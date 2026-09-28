"""SQLite(WAL). 요청마다 연결을 열고 닫으며 연결을 공유하지 않는다."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

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
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
