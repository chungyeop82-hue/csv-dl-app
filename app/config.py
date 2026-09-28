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

# 학습 잡 (SPEC FR-51). 환경 변수로 덮어쓸 수 있어 테스트에서 짧게 줄일 수 있다.
DEFAULT_MAX_TRAIN_SECONDS = 600
DISPATCHER_POLL_SECONDS = 0.3

# 리포트·제출 ZIP (SPEC 6-6, FR-38~40, FR-52)
PERMUTATION_REPEATS = 3  # 특성별 반복 셔플 횟수(평균해 변동을 줄인다)
PERMUTATION_TOP_N = 10
STUDENT_ID_SETTING_KEY = "student_id"
STUDENT_ID_MAX_CHARS = 50
EXPORT_NAME_MAX_CHARS = 80  # 데이터셋명 등 ZIP 파일명 각 조각의 최대 길이


def max_train_seconds(environ=None) -> int:
    """최대 학습 시간(초). APP_MAX_TRAIN_SECONDS 로 덮어쓸 수 있다(기본 10분)."""
    env = os.environ if environ is None else environ
    raw = env.get("APP_MAX_TRAIN_SECONDS")
    if raw is None:
        return DEFAULT_MAX_TRAIN_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_TRAIN_SECONDS
    return value if value > 0 else DEFAULT_MAX_TRAIN_SECONDS


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

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_dirs(self) -> None:
        for d in (self.uploads_dir, self.db_dir, self.tmp_dir, self.logs_dir, self.models_dir, self.export_dir):
            d.mkdir(parents=True, exist_ok=True)
