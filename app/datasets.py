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
    settings = get_settings(request)
    dataset_id = _valid_id(dataset_id)
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT stored_name FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise AppError("E-DS-001", detail="없는 id")
        # 파일을 먼저 지우고 행을 지운다. 중간에 실패해도 목록에 남아 다시 삭제할 수 있다.
        (settings.uploads_dir / row["stored_name"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    log.info("데이터셋 삭제 id=%s", dataset_id)
    return {"deleted": dataset_id}
