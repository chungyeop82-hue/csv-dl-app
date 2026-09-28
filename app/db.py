"""SQLite(WAL). 요청마다 연결을 열고 닫으며 연결을 공유하지 않는다."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 3

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS datasets (
    id            TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    stored_name   TEXT NOT NULL,
    encoding      TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    total_rows    INTEGER NOT NULL,
    used_rows     INTEGER NOT NULL,
    sampled       INTEGER NOT NULL DEFAULT 0,
    n_columns     INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    profile_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_datasets_created ON datasets (created_at DESC);
"""

# 잡 상태는 SPEC.md 5-4절과 ARCHITECTURE.md 5-2절에 고정되어 있다:
#   queued -> running -> completed | failed | cancelled | timeout | interrupted
# runs(ARCHITECTURE 4-4)는 잡 1건당 실행이 1건뿐이라 별도 테이블 대신 jobs 컬럼에 합쳤다.
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    dataset_id        TEXT NOT NULL,
    task              TEXT NOT NULL,
    target            TEXT NOT NULL,
    config_json       TEXT NOT NULL,
    status            TEXT NOT NULL,
    device            TEXT,
    progress_json     TEXT,
    metrics_json      TEXT,
    model_path        TEXT,
    error_code        TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    FOREIGN KEY (dataset_id) REFERENCES datasets (id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, created_at);

CREATE TABLE IF NOT EXISTS job_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    ts           TEXT NOT NULL,
    level        TEXT NOT NULL,
    message      TEXT NOT NULL,
    payload_json TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs (id)
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events (job_id, id);
"""

# 앱 전역의 작은 키-값 설정. 지금은 제출 ZIP 파일명에 재사용하는 학번(SPEC FR-52) 하나만 쓴다.
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(_SCHEMA_V1)
        if version < 2:
            conn.executescript(_SCHEMA_V2)
        if version < 3:
            conn.executescript(_SCHEMA_V3)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def get_app_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_app_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def recover_interrupted_jobs(db_path: Path) -> int:
    """앱 시작 시 이전 실행에서 running 으로 남아 있던 잡을 interrupted 로 복구한다(ARCHITECTURE 5-2).

    오류가 아니라 정보 상태로 다룬다(SPEC 8-3). 몇 건을 복구했는지 돌려준다.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with session(db_path) as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='interrupted', finished_at=? WHERE status='running'",
            (now,),
        )
        return cur.rowcount
