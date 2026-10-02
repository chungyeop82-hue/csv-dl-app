"""데이터셋 API: 업로드, 목록, 상세, 삭제."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile

from . import db
from .config import (
    CHUNK_BYTES,
    MAX_FILENAME_CHARS,
    MAX_UPLOAD_BYTES,
    SAMPLE_ROWS,
    Settings,
)
from .csv_ingest import ingest
from .errors import AppError

log = logging.getLogger("app")
router = APIRouter()

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def sanitize_filename(raw: str | None) -> str:
    """원본 파일명을 표시용으로만 정리한다. 저장 경로에는 절대 쓰지 않는다."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name).strip()
    return name[:MAX_FILENAME_CHARS]


def _banner(total_rows: int, used_rows: int, sampled: bool) -> str | None:
    if not sampled:
        return None
    return f"전체 {total_rows:,}행 중 {used_rows:,}행을 무작위로 사용합니다."


def _summary(row) -> dict:
    return {
        "id": row["id"],
        "original_name": row["original_name"],
        "encoding": row["encoding"],
        "encoding_label": json.loads(row["profile_json"])["encoding_label"],
        "size_bytes": row["size_bytes"],
        "total_rows": row["total_rows"],
        "used_rows": row["used_rows"],
        "sampled": bool(row["sampled"]),
        "n_columns": row["n_columns"],
        "created_at": row["created_at"],
    }


def _detail(row) -> dict:
    profile = json.loads(row["profile_json"])
    out = _summary(row)
    out["banner"] = _banner(row["total_rows"], row["used_rows"], bool(row["sampled"]))
    out["warnings"] = profile["warnings"]
    out["preview"] = profile["preview"]
    out["columns"] = profile["columns"]
    return out


def _spool_upload(upload: UploadFile, tmp_path: Path) -> tuple[int, str]:
    """업로드를 임시 파일로 옮기며 크기(상한 적용)와 SHA-256 을 계산한다."""
    digest = hashlib.sha256()
    size = 0
    with tmp_path.open("wb") as out:
        while True:
            chunk = upload.file.read(CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise AppError("E-UP-002", detail=f"{size}바이트 초과")
            digest.update(chunk)
            out.write(chunk)
    return size, digest.hexdigest()


@router.post("/upload", status_code=201)
def upload_csv(request: Request, file: UploadFile | None = File(None)):
    settings = get_settings(request)
    if file is None or not (file.filename or "").strip():
        raise AppError("E-UP-010")
    original_name = sanitize_filename(file.filename)
    if not original_name.lower().endswith(".csv"):
        raise AppError("E-UP-001", detail=f"확장자: {original_name[-10:]}")

    dataset_id = str(uuid.uuid4())
    tmp_path = settings.tmp_dir / f"{dataset_id}.part"
    final_path = settings.uploads_dir / f"{dataset_id}.csv"
    committed = False
    try:
        size, sha256 = _spool_upload(file, tmp_path)
        result = ingest(tmp_path)

        profile = {
            "encoding_label": result.encoding_label,
            "warnings": result.warnings,
            "preview": {"columns": result.preview_columns, "rows": result.preview_rows},
            "columns": [c.__dict__ for c in result.columns],
        }
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        tmp_path.replace(final_path)
        committed = True
        with db.session(settings.db_path) as conn:
            conn.execute(
                "INSERT INTO datasets (id, original_name, stored_name, encoding, size_bytes, total_rows,"
                " used_rows, sampled, n_columns, sha256, profile_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dataset_id,
                    original_name,
                    final_path.name,
                    result.encoding,
                    size,
                    result.total_rows,
                    result.used_rows,
                    int(result.sampled),
                    result.n_columns,
                    sha256,
                    json.dumps(profile, ensure_ascii=False),
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        log.info("업로드 완료 id=%s rows=%s used=%s size=%s", dataset_id, result.total_rows, result.used_rows, size)
        return _detail(row)
    except BaseException:
        # 실패하면 어떤 파일도 남기지 않는다.
        tmp_path.unlink(missing_ok=True)
        if committed:
            final_path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@router.get("/datasets")
def list_datasets(request: Request):
    settings = get_settings(request)
    with db.session(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM datasets ORDER BY created_at DESC, rowid DESC").fetchall()
    return {"datasets": [_summary(r) for r in rows], "sample_rows": SAMPLE_ROWS}


def _valid_id(dataset_id: str) -> str:
    try:
        return str(uuid.UUID(dataset_id))
    except ValueError:
        raise AppError("E-DS-001", detail="잘못된 id 형식") from None


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, request: Request):
    settings = get_settings(request)
    dataset_id = _valid_id(dataset_id)
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if row is None:
        raise AppError("E-DS-001", detail="없는 id")
    return _detail(row)


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, request: Request):
    """데이터셋을 지운다.

    정책(STEP 10 검증 중 발견한 버그의 수정, 2026-10): 데이터셋을 지우면 그 데이터셋으로 만든
    학습 이력(jobs, job_events)도 함께 정리한다. 이 앱은 되돌리기가 없는 1인 사용자용 도구라,
    원본 CSV가 사라진 뒤에 남는 학습 이력은 다시 보거나 재현할 수 없어 의미가 없기 때문이다.

    순서가 중요하다: app/db.py의 jobs.dataset_id 는 datasets.id 를 FOREIGN KEY 로 참조하지만
    ON DELETE CASCADE 가 없다(의도적으로 끄지 않는다). 예전 코드처럼 CSV 파일을 먼저 지우고 나서
    자식 행(jobs/job_events) 없이 곧장 datasets 행만 지우려 하면 FK 위반으로 500(E-SY-002)이 나고,
    그 사이 CSV는 이미 사라져 DB에는 남아 있지만 파일은 없는 고아(orphan) 데이터셋이 생긴다.
    그래서 ① job_events -> jobs -> datasets 를 하나의 트랜잭션에서 먼저 지우고, ② 그 트랜잭션이
    예외 없이 끝나(커밋되어) 자식 행이 전부 사라진 뒤에만 ③ 실제 파일(업로드 CSV, 완료된 학습이
    남긴 모델 산출물)을 정리한다. DB가 실패했는데 CSV만 먼저 없어지는 상황은 이 순서로 막는다.
    이미 생겼던 고아 데이터셋(CSV 파일 없음)도 ③에서 missing_ok=True 로 조용히 넘어가 정상
    정리된다.
    """
    settings = get_settings(request)
    dataset_id = _valid_id(dataset_id)

    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT stored_name FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise AppError("E-DS-001", detail="없는 id")
        job_rows = conn.execute("SELECT id, model_path FROM jobs WHERE dataset_id = ?", (dataset_id,)).fetchall()
        job_ids = [j["id"] for j in job_rows]
        if job_ids:
            placeholders = ",".join("?" * len(job_ids))
            conn.execute(f"DELETE FROM job_events WHERE job_id IN ({placeholders})", job_ids)
            conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", job_ids)
        conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        # 여기까지 예외 없이 끝나야 commit 된다(db.session) - 그래야만 아래에서 파일을 지운다.
        stored_name = row["stored_name"]
        model_paths = [j["model_path"] for j in job_rows if j["model_path"]]

    # DB 트랜잭션이 성공한 뒤에만 실제 파일을 정리한다. 이미 없는 파일(고아 데이터셋)이어도
    # missing_ok=True 로 조용히 넘어간다 - 사용자에게는 "삭제됨"으로 보이는 게 맞다.
    (settings.uploads_dir / stored_name).unlink(missing_ok=True)
    for model_path in model_paths:
        model_file = Path(model_path)
        model_file.unlink(missing_ok=True)
        model_file.with_suffix(".prep.joblib").unlink(missing_ok=True)

    log.info("데이터셋 삭제 id=%s jobs=%s", dataset_id, len(job_ids))
    return {"deleted": dataset_id}
