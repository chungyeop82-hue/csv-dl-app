"""설정과 상수. 경로는 환경 변수에서 읽고 pathlib.Path 로만 다룬다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 업로드 제한 (SPEC 6-1)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# 본문 외 멀티파트 경계 등 여유분. Content-Length 가 이보다 크면 본문을 읽기 전에 거절한다.
UPLOAD_OVERHEAD_BYTES = 5 * 1024 * 1024
MAX_COLUMNS = 500
MIN_ROWS = 50
WARN_ROWS = 100
SAMPLE_ROWS = 100_000
SAMPLE_SEED = 42
PREVIEW_ROWS = 20
PREVIEW_CELL_MAX_CHARS = 200
CATEGORICAL_MAX_UNIQUE = 50
NUMERIC_PARSE_RATIO = 0.95
MAX_FILENAME_CHARS = 200
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    export_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("APP_DATA_DIR", "data"))
        export_dir = Path(os.environ.get("APP_EXPORT_DIR", "exports"))
        return cls(data_dir=data_dir, export_dir=export_dir)

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "db"

    @property
    def db_path(self) -> Path:
        return self.db_dir / "app.sqlite3"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def log_path(self) -> Path:
        return self.logs_dir / "app.log"

    def ensure_dirs(self) -> None:
        for d in (self.uploads_dir, self.db_dir, self.tmp_dir, self.logs_dir, self.export_dir):
            d.mkdir(parents=True, exist_ok=True)
