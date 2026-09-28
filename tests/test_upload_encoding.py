"""인코딩 감지: UTF-8(BOM) → CP949 → EUC-KR 순서."""

from __future__ import annotations

import pytest

from app import csv_ingest
from tests.helpers import make_csv, stored_files, upload


def test_cp949_file_is_detected_and_korean_text_is_intact(client, settings):
    data = make_csv(120, encoding="cp949")
    r = upload(client, data, "엑셀저장.csv")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["encoding"] == "cp949" and d["encoding_label"] == "CP949"
    assert d["preview"]["columns"] == ["번호", "나이", "등급", "가입일", "메모", "비고"]
    assert d["preview"]["rows"][0][2] == "상"
    assert d["preview"]["rows"][1][2] == "중"
    # 원본은 변환하지 않고 그대로 저장한다.
    assert stored_files(settings)[0].read_bytes() == data


def test_euckr_file_is_read_correctly(client):
    # 흔한 한글 음절만 쓰면 EUC-KR 과 CP949 바이트가 같다. CP949 가 상위 집합이라 CP949 로 판정될 수 있다.
    data = make_csv(120, encoding="euc-kr")
    d = upload(client, data).json()
    assert d["encoding"] in ("cp949", "euc-kr")
    assert d["preview"]["rows"][0][2] == "상"


def test_cp949_only_syllable_is_read(client):
    # '똠' 은 CP949 확장 영역 글자라 EUC-KR 로는 표현할 수 없다.
    lines = ["이름,값"] + [f"똠{i},{i}" for i in range(60)]
    data = ("\n".join(lines) + "\n").encode("cp949")
    d = upload(client, data).json()
    assert d["encoding"] == "cp949"
    assert d["preview"]["rows"][0][0] == "똠0"


def test_utf8_is_tried_before_cp949(tmp_path):
    p = tmp_path / "k.csv"
    p.write_bytes(make_csv(60))  # UTF-8 한글
    assert csv_ingest.detect_encoding(p) == "utf-8"


def test_euckr_is_used_when_cp949_is_skipped(tmp_path, monkeypatch):
    p = tmp_path / "k.csv"
    p.write_bytes(make_csv(60, encoding="euc-kr"))
    monkeypatch.setattr(csv_ingest, "ENCODING_ORDER", ("utf-8-sig", "euc-kr"))
    assert csv_ingest.detect_encoding(p) == "euc-kr"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_cp949_with_crlf(client, newline):
    d = upload(client, make_csv(60, encoding="cp949", newline=newline)).json()
    assert d["total_rows"] == 60 and d["encoding"] == "cp949"


def test_multibyte_char_split_across_read_chunks(tmp_path, monkeypatch):
    # 청크 경계에서 한글 한 글자가 잘려도 오판하지 않아야 한다.
    monkeypatch.setattr(csv_ingest, "CHUNK_BYTES", 7)
    p = tmp_path / "k.csv"
    p.write_bytes(make_csv(60))
    assert csv_ingest.detect_encoding(p) == "utf-8"
    p.write_bytes(make_csv(60, encoding="cp949"))
    assert csv_ingest.detect_encoding(p) == "cp949"
