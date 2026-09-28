"""백그라운드 학습: ProcessPoolExecutor(max_workers=1) 워커 함수와 디스패처 (ARCHITECTURE 5장).

- 잡 1건이 곧 실행(run) 1건이다(재사용 없음).
- 진행 상황은 멀티프로세싱 큐로 워커(자식 프로세스) -> 디스패처(웹 프로세스)로 전달되고,
  디스패처가 그 내용을 SQLite 에 기록한다. 화면(폴링/SSE)은 SQLite 만 본다.
- 취소·시간초과는 협조적이다: 디스패처가 공유 플래그(ctypes bool)를 켜면 워커가 에포크 경계에서
  확인해 멈춘다. 왜 멈췄는지(취소 요청 vs 시간초과)는 디스패처만 알고 있다가 최종 상태에 반영한다.
- MemoryError·CUDA OOM 은 워커 함수 안에서 잡아 실패 코드로 돌려준다. 워커 프로세스 자체가
  죽는(OS OOM-kill 등) 경우는 concurrent.futures.process.BrokenProcessPool 로 나타나므로
  디스패처가 그 잡을 failed 로 표시하고 풀을 다시 만든다(풀이 깨진 채로 남으면 이후 모든 잡이
  실패하기 때문, ARCHITECTURE 5-3).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import multiprocessing as mp
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import csv_ingest, db
from .config import DISPATCHER_POLL_SECONDS, Settings, max_train_seconds
from .errors import AppError
from .ml.baselines import run_baselines
from .ml.common import MLError
from .ml.preprocess import prepare_dataset
from .ml.tabular import STOP_CANCELLED, TabularConfig, TabularML

log = logging.getLogger("app")

MP_CONTEXT = mp.get_context("spawn")  # CUDA 재초기화 문제를 피하려면 spawn 이어야 한다(ARCHITECTURE 5-3).

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TIMEOUT = "timeout"
INTERRUPTED = "interrupted"
TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED, TIMEOUT, INTERRUPTED})

_STATUS_LABELS = {
    COMPLETED: "완료",
    FAILED: "실패",
    CANCELLED: "취소",
    TIMEOUT: "시간초과",
    INTERRUPTED: "중단됨",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 워커 함수 (자식 프로세스에서 실행). spawn 으로 피클할 수 있도록 최상위 함수여야 한다.
# ---------------------------------------------------------------------------
def run_training_job(
    job_id: str,
    dataset_path: str,
    encoding: str,
    task: str,
    target: str,
    numeric_cols: list[str],
    categorical_cols: list[str],
    tabular_config: dict[str, Any],
    device_request: str | None,
    model_path: str,
    progress_queue,
    stop_flag,
) -> dict[str, Any]:
    """CSV 재적재 -> 전처리 -> 학습 -> 베이스라인 -> 평가 -> 모델 저장.

    예외 대부분은 여기서 잡아 결과 dict(status/error_code)로 돌려준다. 정말 예상하지 못한
    예외만 그대로 올려 보내되, 상세는 로그용 문자열로만 담는다(화면에는 코드/한 문장만 나간다).
    """
    try:
        path = Path(dataset_path)
        header, total_rows = csv_ingest.scan_structure(path, encoding)
        sample_idx = csv_ingest.pick_sample_indices(total_rows)
        _, df = csv_ingest.load_frames(path, encoding, header, sample_idx)

        config = TabularConfig(**tabular_config)
        prepared = prepare_dataset(
            df,
            target=target,
            task=task,
            numeric_cols=numeric_cols,
            categorical_cols=categorical_cols,
            seed=config.seed,
        )

        def on_epoch(progress) -> None:
            try:
                progress_queue.put(asdict(progress))
            except Exception:  # noqa: BLE001 - 진행률 전달 실패가 학습 자체를 막지 않는다
                pass

        def should_cancel() -> bool:
            return bool(stop_flag.value)

        model = TabularML(config, device=device_request)
        result = model.fit(prepared, on_epoch=on_epoch, should_cancel=should_cancel)

        if result.stop_reason == STOP_CANCELLED:
            # 취소 요청 때문인지 시간초과 때문인지는 디스패처만 알고 있다.
            return {"status": "stopped", "epochs_run": result.epochs_run}

        scores = model.evaluate_all(prepared)
        try:
            baselines = [
                {"kind": b.kind, "label": b.label, "validation": b.validation, "test": b.test}
                for b in run_baselines(prepared, seed=config.seed)
            ]
        except Exception:  # noqa: BLE001 - 베이스라인 비교 실패가 본 학습 성공까지 막지는 않는다
            log.exception("베이스라인 실패 job=%s", job_id)
            baselines = []

        model_file = Path(model_path)
        model_file.parent.mkdir(parents=True, exist_ok=True)
        import torch  # noqa: PLC0415 - 워커(자식) 프로세스 안에서만 가져온다

        torch.save(model.model.state_dict(), model_file)
        prepared.preprocessor.save(model_file.with_suffix(".prep.joblib"))

        return {
            "status": "completed",
            "device": result.device.device if result.device else None,
            "seconds": result.seconds,
            "epochs_run": result.epochs_run,
            "best_epoch": result.best_epoch,
            "stop_reason": result.stop_reason,
            "metrics": {
                "validation": scores["validation"],
                "test": scores["test"],
                "baselines": baselines,
                "n_features": prepared.n_features,
                "split_sizes": prepared.split_sizes,
                # 학습곡선(SPEC 6-6 report.pdf/metrics.json): fit() 이 이미 에포크마다 만들어 두는
                # 값을 그대로 담는다. 학습 루프 자체는 바뀌지 않는다 - 그동안 버려지던 결과를 담을 뿐이다.
                "history": [asdict(p) for p in result.history],
                # stop_reason/epochs_run/best_epoch 도 이미 위 바깥쪽 반환값에 있었지만 DB 에는
                # metrics 컬럼만 저장되므로(_apply_outcome), 리포트에 쓰려면 여기에도 넣어야 한다.
                "stop_reason": result.stop_reason,
                "epochs_run": result.epochs_run,
                "best_epoch": result.best_epoch,
                "seconds": result.seconds,
            },
            "model_path": str(model_file),
        }
    except MemoryError:
        return {"status": "failed", "error_code": "E-JB-004", "detail": "MemoryError"}
    except (MLError, AppError) as exc:
        return {"status": "failed", "error_code": exc.code, "detail": getattr(exc, "detail", "")}
    except Exception as exc:  # noqa: BLE001 - 마지막 방어선. 상세는 로그용 문자열에만 담는다.
        if _looks_like_cuda_oom(exc):
            return {"status": "failed", "error_code": "E-JB-005", "detail": str(exc)}
        return {
            "status": "failed",
            "error_code": "E-SY-002",
            "detail": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        }


def _looks_like_cuda_oom(exc: Exception) -> bool:
    """torch.cuda.OutOfMemoryError(가능하면) 또는 메시지로 CUDA OOM 을 알아본다."""
    try:
        import torch  # noqa: PLC0415

        oom_cls = getattr(getattr(torch, "cuda", None), "OutOfMemoryError", None)
        if oom_cls is not None and isinstance(exc, oom_cls):
            return True
    except Exception:  # noqa: BLE001 - torch 가 없거나 형태가 달라도 문자열 검사로 대체한다
        pass
    text = str(exc).lower()
    return "cuda" in text and "out of memory" in text


# ---------------------------------------------------------------------------
# 디스패처 (웹 프로세스 안에서 asyncio 백그라운드 태스크로 동작)
# ---------------------------------------------------------------------------
@dataclass
class _RunningJob:
    job_id: str
    future: Any
    queue: Any
    stop_flag: Any
    started_monotonic: float
    timeout_requested: bool = False


class Dispatcher:
    """대기열에서 잡을 하나씩 꺼내 ProcessPoolExecutor(max_workers=1)에 맡긴다."""

    def __init__(self, settings: Settings, poll_interval: float = DISPATCHER_POLL_SECONDS) -> None:
        self.settings = settings
        self.poll_interval = poll_interval
        self._executor: ProcessPoolExecutor | None = None
        self._manager: Any = None  # SyncManager: 잡마다 Queue/Value 를 이 매니저에서 만든다(아래 설명)
        self._running: _RunningJob | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    def _new_executor(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=1, mp_context=MP_CONTEXT)

    async def start(self) -> None:
        self._executor = self._new_executor()
        # 진행률 Queue·취소 플래그는 반드시 SyncManager(별도 관리자 프로세스)로 만든다: 이미 떠 있는
        # 워커 풀에 submit() 으로 보내는 인자는 매번 파이프로 피클되는데, MP_CONTEXT.Queue()/Value() 를
        # 그대로 넘기면 "Queue objects should only be shared between processes through inheritance"
        # RuntimeError 로 즉시 실패한다(프로세스 생성 시 인자로 줄 때만 허용되는 방식이라서다).
        # Manager 가 만드는 프록시 객체는 이 경로로도 안전하게 피클된다.
        self._manager = MP_CONTEXT.Manager()
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        if self._manager is not None:
            with contextlib.suppress(Exception):
                self._manager.shutdown()
            self._manager = None

    def request_cancel(self, job_id: str) -> bool:
        """실행 중인 잡이면 즉시 중단 플래그를 켠다(빠른 경로). 반환값은 '지금 실행 중이었는가'.

        API 요청 처리(FastAPI 동기 라우트)는 별도 스레드 풀에서 돈다. 그 스레드가 이 시점의
        self._running 을 읽는 사이 디스패처(이벤트 루프 스레드)가 다음 잡으로 넘어가면 이 호출은
        아무것도 하지 못한 채 끝날 수 있다. 그런 드문 경우를 대비해 cancel_requested 는 항상
        DB 에도 남기고(app/jobs.py), _check_cancel_requested 가 매 틱마다 DB 를 확인해 놓치지
        않는다 - 여기서의 즉시 반영은 지연을 줄이기 위한 최적화일 뿐, 정확성은 DB 확인이 보장한다.
        """
        run = self._running
        if run is not None and run.job_id == job_id:
            run.stop_flag.value = True
            return True
        return False

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - 디스패처 루프 자체는 절대 죽으면 안 된다
                log.exception("디스패처 루프 오류")
            await asyncio.sleep(self.poll_interval)

    def _tick(self) -> None:
        if self._running is None:
            self._dispatch_next()
            return
        self._drain_progress()
        self._check_timeout()
        self._check_cancel_requested()
        if self._running.future.done():
            self._finish_current()

    def _dispatch_next(self) -> None:
        with db.session(self.settings.db_path) as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at, rowid LIMIT 1", (QUEUED,)
            ).fetchone()
            if job is None:
                return
            dataset = conn.execute(
                "SELECT stored_name, encoding FROM datasets WHERE id=?", (job["dataset_id"],)
            ).fetchone()
            if dataset is None:
                conn.execute(
                    "UPDATE jobs SET status=?, error_code=?, finished_at=? WHERE id=?",
                    (FAILED, "E-DS-001", _now(), job["id"]),
                )
                return
            conn.execute(
                "UPDATE jobs SET status=?, started_at=? WHERE id=?", (RUNNING, _now(), job["id"])
            )

        config = json.loads(job["config_json"])
        dataset_path = str(self.settings.uploads_dir / dataset["stored_name"])
        model_path = str(self.settings.models_dir / f"{job['id']}.pt")
        # SyncManager 프록시로 만들어야 한다(start() 의 주석 참고) - 실행 중인 풀에 넘길 수 있는 유일한 방식.
        queue = self._manager.Queue()
        stop_flag = self._manager.Value("b", False)
        future = self._executor.submit(
            run_training_job,
            job["id"],
            dataset_path,
            dataset["encoding"],
            job["task"],
            job["target"],
            config["numeric_cols"],
            config["categorical_cols"],
            config["tabular_config"],
            config.get("device"),
            model_path,
            queue,
            stop_flag,
        )
        self._running = _RunningJob(
            job_id=job["id"], future=future, queue=queue, stop_flag=stop_flag, started_monotonic=time.monotonic()
        )
        self._log_event(job["id"], "학습을 시작합니다.")

    def _drain_progress(self) -> None:
        run = self._running
        assert run is not None
        drained = 0
        while drained < 200:  # 한 번의 틱이 너무 오래 걸리지 않도록 상한을 둔다
            try:
                payload = run.queue.get_nowait()
            except Exception:  # noqa: BLE001 - queue.Empty 및 파이프 관련 예외 모두 포함
                break
            drained += 1
            with db.session(self.settings.db_path) as conn:
                conn.execute(
                    "UPDATE jobs SET progress_json=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), run.job_id),
                )
                conn.execute(
                    "INSERT INTO job_events (job_id, ts, level, message, payload_json) VALUES (?,?,?,?,?)",
                    (
                        run.job_id,
                        _now(),
                        "info",
                        f"에포크 {payload['epoch']}/{payload['max_epochs']} (val_loss={payload['val_loss']:.4f})",
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )

    def _check_timeout(self) -> None:
        run = self._running
        assert run is not None
        if run.timeout_requested:
            return
        limit = max_train_seconds()
        if time.monotonic() - run.started_monotonic > limit:
            run.timeout_requested = True
            run.stop_flag.value = True
            self._log_event(run.job_id, f"최대 학습 시간({limit // 60}분)을 초과해 학습을 중단합니다.")

    def _check_cancel_requested(self) -> None:
        """DB 의 cancel_requested 를 확인한다(request_cancel 의 즉시 반영을 놓쳤을 때의 안전망)."""
        run = self._running
        assert run is not None
        if run.stop_flag.value:
            return
        with db.session(self.settings.db_path) as conn:
            row = conn.execute("SELECT cancel_requested FROM jobs WHERE id=?", (run.job_id,)).fetchone()
        if row is not None and row["cancel_requested"]:
            run.stop_flag.value = True

    def _finish_current(self) -> None:
        run = self._running
        self._running = None
        assert run is not None
        try:
            outcome = run.future.result()
        except BrokenProcessPool as exc:
            log.error("워커 풀 손상 job=%s: %s", run.job_id, exc)
            self._set_status(run.job_id, FAILED, error_code="E-JB-004", detail="워커 프로세스가 예기치 않게 종료됨(풀 손상)")
            with contextlib.suppress(Exception):
                self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = self._new_executor()
            return
        except Exception as exc:  # noqa: BLE001 - 워커가 던진, 위에서 잡지 못한 예외
            log.exception("잡 처리 중 예외 job=%s", run.job_id)
            self._set_status(run.job_id, FAILED, error_code="E-SY-002", detail=str(exc))
            return
        self._apply_outcome(run, outcome)

    def _apply_outcome(self, run: _RunningJob, outcome: dict[str, Any]) -> None:
        status = outcome.get("status")
        if status == "completed":
            self._set_status(
                run.job_id,
                COMPLETED,
                metrics=outcome.get("metrics"),
                model_path=outcome.get("model_path"),
                device=outcome.get("device"),
            )
        elif status == "stopped":
            final_status = TIMEOUT if run.timeout_requested else CANCELLED
            self._set_status(run.job_id, final_status)
        else:
            self._set_status(
                run.job_id, FAILED, error_code=outcome.get("error_code", "E-SY-002"), detail=outcome.get("detail", "")
            )

    def _set_status(
        self,
        job_id: str,
        status: str,
        *,
        error_code: str | None = None,
        detail: str = "",
        metrics: dict | None = None,
        model_path: str | None = None,
        device: str | None = None,
    ) -> None:
        with db.session(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET status=?, error_code=?, metrics_json=?, model_path=?, device=?, finished_at=?"
                " WHERE id=?",
                (
                    status,
                    error_code,
                    json.dumps(metrics, ensure_ascii=False) if metrics is not None else None,
                    model_path,
                    device,
                    _now(),
                    job_id,
                ),
            )
        label = _STATUS_LABELS.get(status, status)
        message = f"학습이 {label} 상태가 되었습니다."
        self._log_event(job_id, message, level="warning" if status == FAILED else "info")
        if detail:
            log.info("job=%s status=%s detail=%s", job_id, status, detail)

    def _log_event(self, job_id: str, message: str, level: str = "info") -> None:
        with db.session(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO job_events (job_id, ts, level, message) VALUES (?,?,?,?)",
                (job_id, _now(), level, message),
            )
