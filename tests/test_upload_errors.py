"""손상·비정상 CSV: 원인과 해결법을 한국어 한 문장으로 안내하고, 아무것도 저장하지 않는다."""

from __future__ import annotations

import pytest

from tests.helpers import assert_one_sentence_error, make_csv, stored_files, tmp_files, upload


def _rows(header: str, n: int = 60, row="{i},{i}") -> bytes:
    return (header + "\n" + "\n".join(row.format(i=i) for i in range(n)) + "\n").encode("utf-8")


def _nothing_left(client, settings):
    assert stored_files(settings) == []
    assert tmp_files(settings) == []
    assert client.get("/datasets").json()["datasets"] == []


CASES = {
    # 이름: (파일 바이트, 파일명, 기대 코드)
    "빈 파일": (b"", "empty.csv", "E-UP-007"),
    "공백·줄바꿈만": (b"\n\n  \n" * 3, "blank.csv", "E-UP-007"),
    "BOM만": (b"\xef\xbb\xbf", "bom.csv", "E-UP-007"),
    "헤더만": (b"a,b,c\n", "header.csv", "E-UP-007"),
    "열 1개": (_rows("a", row="{i}"), "one.csv", "E-UP-008"),
    "세미콜론 구분": (_rows("a;b;c", row="{i};{i};{i}"), "semi.csv", "E-UP-008"),
    "탭 구분": (_rows("a\tb", row="{i}\t{i}"), "tab.csv", "E-UP-008"),
    "바이너리(NUL)": (b"PK\x03\x04\x00\x00\x00\x00binary\x00\x00" * 50, "zip.csv", "E-UP-003"),
    "UTF-16": ("a,b\n1,2\n".encode("utf-16"), "u16.csv", "E-UP-003"),
    "어떤 인코딩도 아님": (b"a,b\n" + b"1,\xff\xfe\xfd\n" * 60, "bad.csv", "E-UP-003"),
    "따옴표 안 닫힘": (b"a,b\n" + b"1,2\n" * 60 + '3,"미완성\n4,5\n'.encode(), "quote.csv", "E-UP-009"),
    "닫는 따옴표 뒤 잡문자": (b"a,b\n" + b"1,2\n" * 60 + b'"abc"def,1\n', "junk.csv", "E-UP-009"),
    "행마다 열 수 다름(적음)": (b"a,b,c\n" + b"1,2,3\n" * 60 + b"4,5\n", "short.csv", "E-UP-009"),
    "행마다 열 수 다름(많음)": (b"a,b,c\n" + b"1,2,3\n" * 60 + b"4,5,6,7\n", "long.csv", "E-UP-009"),
    "열 이름 중복": (_rows("a,a"), "dup.csv", "E-UP-004"),
    "열 이름 비어 있음": (_rows("a,,c", row="{i},{i},{i}"), "noname.csv", "E-UP-004"),
    "행 49개": (_rows("a,b", n=49), "few.csv", "E-UP-006"),
    "열 501개": (
        (",".join(f"c{i}" for i in range(501)) + "\n" + "\n".join(",".join("1" for _ in range(501)) for _ in range(60)) + "\n").encode(),
        "wide.csv",
        "E-UP-005",
    ),
}


@pytest.mark.parametrize("label", list(CASES))
def test_invalid_csv_is_rejected_with_one_korean_sentence(client, settings, label):
    data, name, code = CASES[label]
    r = upload(client, data, name)
    assert_one_sentence_error(r, code)
    _nothing_left(client, settings)


def test_500_columns_is_accepted(client):
    header = ",".join(f"c{i}" for i in range(500))
    body = "\n".join(",".join(str(i) for _ in range(500)) for i in range(60))
    r = upload(client, (header + "\n" + body + "\n").encode())
    assert r.status_code == 201 and r.json()["n_columns"] == 500


def test_non_csv_extension_is_rejected(client, settings):
    for name in ("data.xlsx", "data.txt", "data", "data.csv.exe"):
        assert_one_sentence_error(upload(client, make_csv(60), name), "E-UP-001")
    _nothing_left(client, settings)


def test_no_file_is_rejected(client):
    assert_one_sentence_error(client.post("/upload"), "E-UP-010")
    # 파일 칸에 문자열만 온 경우
    assert_one_sentence_error(client.post("/upload", data={"file": "abc"}), "E-UP-010")
    # 파일 이름이 빈 경우(브라우저에서 파일을 고르지 않고 보낸 경우)
    assert_one_sentence_error(client.post("/upload", files={"file": ("", b"", "application/octet-stream")}), "E-UP-010")


def test_error_after_valid_upload_does_not_touch_existing_dataset(client, settings):
    ok = upload(client, make_csv(60), "ok.csv").json()
    upload(client, b"a,b\n1,2\n" + b'"x', "bad.csv")
    listed = client.get("/datasets").json()["datasets"]
    assert [d["id"] for d in listed] == [ok["id"]]
    assert len(stored_files(settings)) == 1
