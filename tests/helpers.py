"""테스트 공용 도우미: CSV 생성, 업로드 호출, 저장 상태 확인."""

from __future__ import annotations

import io
from pathlib import Path

from app.config import Settings
from app.errors import CATALOG


def make_csv(n_rows: int = 120, encoding: str = "utf-8", newline: str = "\n") -> bytes:
    """한글 열 이름·값이 섞인 정상 CSV. 숫자·범주·날짜·텍스트·빈 열이 각각 하나씩 들어 있다."""
    lines = ["번호,나이,등급,가입일,메모,비고"]
    for i in range(n_rows):
        grade = ["상", "중", "하"][i % 3]
        lines.append(f"{i},{20 + i % 40},{grade},2024-01-{i % 28 + 1:02d},고유메모{i},")
    return (newline.join(lines) + newline).encode(encoding)


def upload(client, data: bytes, name: str = "sample.csv", content_type: str = "text/csv"):
    return client.post("/upload", files={"file": (name, io.BytesIO(data), content_type)})


def stored_files(settings: Settings) -> list[Path]:
    return sorted(settings.uploads_dir.glob("*"))


def tmp_files(settings: Settings) -> list[Path]:
    return sorted(settings.tmp_dir.glob("*"))


def assert_one_sentence_error(response, code: str, status: int | None = None) -> dict:
    """오류 응답이 카탈로그와 같은 한국어 한 문장이며 코드·요청 번호를 갖는지 확인한다."""
    expected_status, expected_message = CATALOG[code]
    assert response.status_code == (status or expected_status), response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"] == expected_message
    assert error["request_id"] and error["request_id"] == response.headers["X-Request-ID"]
    message = error["message"]
    assert message.endswith("다.") or message.endswith("요.")
    assert message.count(".") == 1, "한 문장이어야 한다"
    assert "\n" not in message
    assert any("가" <= ch <= "힣" for ch in message)
    # 응답에는 error 키 하나만 있고 그 안에 세 항목만 있다.
    assert set(response.json()) == {"error"}
    assert set(error) == {"code", "message", "request_id"}
    return error
