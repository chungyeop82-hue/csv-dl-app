"""학습 잡 API: 생성, 목록·상세, 취소, 진행률(SSE)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import db
from .catalog import CATALOG
from .config import Settings
from .errors import AppError
from .jobs_worker import TERMINAL_STATUSES
from .ml.tabular import BATCH_SIZES, PRESETS, TabularConfig

log = logging.getLogger("app")
router = APIRouter()

DEFAULT_PRESET = "small"


class JobCreate(BaseModel):
    dataset_id: str
    task: Literal["classification", "regression"]
    target: str
    feature_columns: list[str] | None = None
    preset: Literal[tuple(PRESETS)] = DEFAULT_PRESET  # type: ignore[valid-type]
    device: Literal["auto", "cpu", "cuda"] | None = None
    learning_rate: float | None = None
    batch_size: Literal[tuple(BATCH_SIZES)] | None = None  # type: ignore[valid-type]
    max_epochs: int | None = None
    early_stopping: bool | None = None
    patience: int | None = None
    seed: int = 42


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_job_id(job_id: str) -> str:
    try:
        return str(uuid.UUID(job_id))
    except ValueError:
        raise AppError("E-JB-002", detail="잘못된 id 형식") from None


def _job_summary(row) -> dict:
    out = {
        "id": row["id"],
        "dataset_id": row["dataset_id"],
        "task": row["task"],
        "target": row["target"],
        "status": row["status"],
        "device": row["device"],
        "error_code": row["error_code"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }
    if row["error_code"]:
        out["error_message"] = CATALOG[row["error_code"]][1]
    if row["progress_json"]:
        out["progress"] = json.loads(row["progress_json"])
    if row["metrics_json"]:
        out["metrics"] = json.loads(row["metrics_json"])
    return out


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, request: Request):
    settings = get_settings(request)
    with db.session(settings.db_path) as conn:
        # 확인 후 삽입(check-then-act) 사이에 동시 요청이 끼어들지 못하도록 쓰기 잠금을 먼저 잡는다
        # (항목 17: 동시 학습 거절). 이 연결의 첫 문장이라 안전하게 트랜잭션을 직접 시작할 수 있다.
        conn.execute("BEGIN IMMEDIATE")
        dataset = conn.execute("SELECT * FROM datasets WHERE id=?", (body.dataset_id,)).fetchone()
        if dataset is None:
            raise AppError("E-DS-001", detail="없는 데이터셋")

        active = conn.execute(
            "SELECT id FROM jobs WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone()
        if active is not None:
            raise AppError("E-JB-001", detail=f"이미 활성 작업 {active['id']}")

        profile = json.loads(dataset["profile_json"])
        columns = {c["name"]: c for c in profile["columns"]}
        if body.target not in columns:
            raise AppError("E-CF-001", detail=f"없는 타깃 열: {body.target}")

        if body.feature_columns is not None:
            chosen = [c for c in body.feature_columns if c != body.target and c in columns]
        else:
            chosen = [name for name in columns if name != body.target]

        numeric_cols = [c for c in chosen if columns[c]["type"] == "numeric"]
        categorical_cols = [c for c in chosen if columns[c]["type"] == "categorical"]
        if not numeric_cols and not categorical_cols:
            raise AppError("E-CF-005", detail="사용 가능한 특성 열 없음")

        overrides = {
            k: v
            for k, v in {
                "learning_rate": body.learning_rate,
                "batch_size": body.batch_size,
                "max_epochs": body.max_epochs,
                "early_stopping": body.early_stopping,
                "patience": body.patience,
            }.items()
            if v is not None
        }
        try:
            tabular_config = TabularConfig.from_preset(body.task, body.preset, seed=body.seed, **overrides)
        except ValueError as exc:
            raise AppError("E-CF-001", detail=str(exc)) from exc

        job_id = str(uuid.uuid4())
        config_json = json.dumps(
            {
                "numeric_cols": numeric_cols,
                "categorical_cols": categorical_cols,
                "device": body.device,
                "tabular_config": {
                    "task": tabular_config.task,
                    "hidden_layers": list(tabular_config.hidden_layers),
                    "dropout": tabular_config.dropout,
                    "learning_rate": tabular_config.learning_rate,
                    "batch_size": tabular_config.batch_size,
                    "max_epochs": tabular_config.max_epochs,
                    "early_stopping": tabular_config.early_stopping,
                    "patience": tabular_config.patience,
                    "seed": tabular_config.seed,
                },
            },
            ensure_ascii=False,
        )
        created_at = _now()
        conn.execute(
            "INSERT INTO jobs (id, dataset_id, task, target, config_json, status, cancel_requested, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'queued', 0, ?)",
            (job_id, body.dataset_id, body.task, body.target, config_json, created_at),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    log.info("잡 생성 id=%s dataset=%s task=%s target=%s", job_id, body.dataset_id, body.task, body.target)
    return _job_summary(row)


@router.get("/jobs")
def list_jobs(request: Request):
    settings = get_settings(request)
    with db.session(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC").fetchall()
    return {"jobs": [_job_summary(r) for r in rows]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    settings = get_settings(request)
    job_id = _valid_job_id(job_id)
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise AppError("E-JB-002", detail="없는 잡")
    return _job_summary(row)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    settings = get_settings(request)
    job_id = _valid_job_id(job_id)
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise AppError("E-JB-002", detail="없는 잡")
        if row["status"] in TERMINAL_STATUSES:
            return _job_summary(row)
        conn.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        if row["status"] == "queued":
            # 아직 워커에 배정되지 않았으면 곧바로 취소를 확정한다.
            conn.execute(
                "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (_now(), job_id)
            )
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    request.app.state.dispatcher.request_cancel(job_id)
    log.info("잡 취소 요청 id=%s", job_id)
    return _job_summary(row)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    """진행률 SSE. 서버가 SQLite 를 짧은 주기로 확인해 바뀐 내용만 내려보낸다."""
    settings = get_settings(request)
    job_id = _valid_job_id(job_id)

    async def stream():
        last_payload: str | None = None
        while True:
            if await request.is_disconnected():
                return
            with db.session(settings.db_path) as conn:
                row = conn.execute(
                    "SELECT status, progress_json, error_code FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
            if row is None:
                yield 'event: error\ndata: {"code": "E-JB-002"}\n\n'
                return
            payload = json.dumps(
                {
                    "status": row["status"],
                    "progress": json.loads(row["progress_json"]) if row["progress_json"] else None,
                    "error_code": row["error_code"],
                },
                ensure_ascii=False,
            )
            if payload != last_payload:
                event = "done" if row["status"] in TERMINAL_STATUSES else "progress"
                yield f"event: {event}\ndata: {payload}\n\n"
                last_payload = payload
                if row["status"] in TERMINAL_STATUSES:
                    return
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream")
