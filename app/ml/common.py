"""학습 라이브러리 공통: 문제 유형, 오류."""

from __future__ import annotations

from ..catalog import CATALOG

CLASSIFICATION = "classification"
REGRESSION = "regression"
TASKS = (CLASSIFICATION, REGRESSION)


class MLError(Exception):
    """오류 카탈로그(app/errors.py)의 코드로 표현되는 학습 관련 오류. detail 은 로그용이며 화면에 내보내지 않는다."""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in CATALOG:
            raise KeyError(code)
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def message(self) -> str:
        return CATALOG[self.code][1]


def check_task(task: str) -> str:
    if task not in TASKS:
        raise ValueError(f"task 는 {TASKS} 중 하나여야 한다: {task!r}")
    return task
