"""정상 CSV: 업로드 결과, 저장 위치, 원본명 기록, 열 요약."""

from __future__ import annotations

import sqlite3
import uuid

from tests.helpers import make_csv, stored_files, tmp_files, upload


def test_utf8_upload_returns_preview_types_missing_unique(client, settings):
    data = make_csv(120)
    r = upload(client, data, "학생 성적.csv")
    assert r.status_code == 201, r.text
    d = r.json()

    assert d["original_name"] == "학생 성적.csv"
    assert d["encoding"] == "utf-8" and d["encoding_label"] == "UTF-8"
    assert d["total_rows"] == d["used_rows"] == 120
    assert d["sampled"] is False and d["banner"] is None
    assert d["n_columns"] == 6
    assert d["size_bytes"] == len(data)
    assert d["warnings"] == []

    # 상위 20행 미리보기(원본 그대로)
    assert d["preview"]["columns"] == ["번호", "나이", "등급", "가입일", "메모", "비고"]
    assert len(d["preview"]["rows"]) == 20
    assert d["preview"]["rows"][0][:3] == ["0", "20", "상"]
    assert d["preview"]["rows"][0][5] is None  # 빈 칸은 결측(null)

    cols = {c["name"]: c for c in d["columns"]}
    assert cols["번호"]["type"] == "numeric" and cols["번호"]["type_label"] == "숫자"
    assert cols["나이"]["type"] == "numeric"
    assert cols["등급"]["type"] == "categorical" and cols["등급"]["unique"] == 3
    assert cols["가입일"]["type"] == "datetime"
    assert cols["메모"]["type"] == "text" and cols["메모"]["unique"] == 120
    assert cols["비고"]["type"] == "empty"
    assert cols["비고"]["missing"] == 120 and cols["비고"]["missing_pct"] == 100.0
    assert cols["번호"]["missing"] == 0 and cols["번호"]["unique"] == 120


def test_original_saved_as_uuid_csv_and_name_recorded_in_sqlite(client, settings):
    data = make_csv(80)
    d = upload(client, data, "내 데이터.csv").json()

    files = stored_files(settings)
    assert len(files) == 1
    assert files[0].name == f"{d['id']}.csv"
    assert uuid.UUID(d["id"])  # 유효한 UUID
    assert files[0].read_bytes() == data  # 원본 그대로
    assert tmp_files(settings) == []  # 임시 파일 남지 않음

    conn = sqlite3.connect(settings.db_path)
    try:
        row = conn.execute("SELECT original_name, stored_name, encoding FROM datasets WHERE id=?", (d["id"],)).fetchone()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert row == ("내 데이터.csv", f"{d['id']}.csv", "utf-8")
    assert mode == "wal"


def test_utf8_bom_is_detected_and_stripped_from_header(client):
    data = b"\xef\xbb\xbf" + make_csv(60)
    d = upload(client, data).json()
    assert d["encoding"] == "utf-8-sig" and d["encoding_label"] == "UTF-8 (BOM)"
    assert d["preview"]["columns"][0] == "번호"  # BOM 문자가 열 이름에 붙지 않음


def test_crlf_quoted_fields_and_embedded_newlines(client):
    lines = ['이름,설명,점수']
    for i in range(60):
        lines.append(f'"김,{i}","줄1\n줄2 ""인용""",{i}')
    data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    r = upload(client, data)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["total_rows"] == 60
    assert d["preview"]["rows"][0][0] == "김,0"
    assert d["preview"]["rows"][0][1] == '줄1\n줄2 "인용"'


def test_trailing_blank_lines_are_ignored(client):
    data = make_csv(60) + b"\n\n\n"
    d = upload(client, data).json()
    assert d["total_rows"] == 60


def test_fewer_than_100_rows_gives_warning_but_succeeds(client):
    d = upload(client, make_csv(99)).json()
    assert d["total_rows"] == 99
    assert len(d["warnings"]) == 1 and "99" in d["warnings"][0]


def test_exactly_50_rows_is_accepted(client):
    assert upload(client, make_csv(50)).status_code == 201


def test_long_cells_are_truncated_in_preview_only(client, settings):
    lines = ["a,b"] + [f"{'x' * 500},{i}" for i in range(60)]
    data = ("\n".join(lines) + "\n").encode()
    d = upload(client, data).json()
    cell = d["preview"]["rows"][0][0]
    assert len(cell) == 201 and cell.endswith("…")
    assert stored_files(settings)[0].read_bytes() == data


def test_whitespace_only_cells_count_as_missing(client):
    lines = ["a,b"] + [f"{i},   " for i in range(60)]
    d = upload(client, ("\n".join(lines) + "\n").encode()).json()
    b = {c["name"]: c for c in d["columns"]}["b"]
    assert b["missing"] == 60 and b["type"] == "empty"


def test_filename_is_sanitized_and_never_used_as_path(client, settings, tmp_path):
    d = upload(client, make_csv(60), "../../etc/passwd.csv").json()
    assert d["original_name"] == "passwd.csv"
    d2 = upload(client, make_csv(60), "C:\\Users\\me\\b.csv").json()
    assert d2["original_name"] == "b.csv"
    assert len(stored_files(settings)) == 2
    assert all(f.parent == settings.uploads_dir for f in stored_files(settings))
    assert not (tmp_path / "etc").exists()


def test_uppercase_extension_is_accepted(client):
    assert upload(client, make_csv(60), "DATA.CSV").status_code == 201


def test_sanitize_filename_strips_control_chars_and_limits_length():
    from app.datasets import sanitize_filename

    assert sanitize_filename("a\x07b\n.csv") == "ab.csv"
    assert sanitize_filename("../x/y.csv") == "y.csv"
    assert len(sanitize_filename("가" * 500 + ".csv")) == 200
    assert sanitize_filename(None) == ""


def test_leading_whitespace_line_before_header_is_reported_as_corrupt(client):
    from tests.helpers import assert_one_sentence_error

    data = b"   \n" + make_csv(60)
    assert_one_sentence_error(upload(client, data), "E-UP-009")
