"""대용량: 50MB 상한, 10만 행 샘플링."""

from __future__ import annotations

import numpy as np

from app import csv_ingest
from app.config import MAX_UPLOAD_BYTES, SAMPLE_ROWS
from tests.helpers import assert_one_sentence_error, stored_files, tmp_files, upload


def _big_rows(n: int) -> bytes:
    lines = ["id,group,value"] + [f"{i},g{i % 5},{i * 0.5}" for i in range(n)]
    return ("\n".join(lines) + "\n").encode()


def _padded(size: int) -> bytes:
    head = b"a,b\n"
    row = b"1,2\n"
    return head + row * ((size - len(head)) // len(row))


def test_file_just_over_50mb_is_rejected_after_upload(client, settings):
    data = _padded(MAX_UPLOAD_BYTES + 1024)
    assert len(data) > MAX_UPLOAD_BYTES
    r = upload(client, data, "big.csv")
    assert_one_sentence_error(r, "E-UP-002")
    assert stored_files(settings) == [] and tmp_files(settings) == []


def test_file_far_over_50mb_is_rejected_before_body_is_read(client, settings):
    data = _padded(MAX_UPLOAD_BYTES + 6 * 1024 * 1024)
    r = upload(client, data, "huge.csv")
    assert_one_sentence_error(r, "E-UP-002")
    assert stored_files(settings) == [] and tmp_files(settings) == []


def test_file_exactly_at_50mb_is_accepted(client, settings):
    data = _padded(MAX_UPLOAD_BYTES)
    data = data[:MAX_UPLOAD_BYTES]
    data = data[: data.rfind(b"\n") + 1]  # 마지막 줄을 온전하게 유지
    r = upload(client, data, "limit.csv")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["sampled"] is True and d["used_rows"] == SAMPLE_ROWS
    assert d["total_rows"] == data.count(b"\n") - 1


def test_over_100k_rows_are_sampled_and_banner_shown(client, settings):
    data = _big_rows(150_000)
    r = upload(client, data, "many.csv")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["total_rows"] == 150_000
    assert d["used_rows"] == SAMPLE_ROWS == 100_000
    assert d["sampled"] is True
    assert d["banner"] == "전체 150,000행 중 100,000행을 무작위로 사용합니다."
    # 미리보기는 원본의 앞 20행이고, 열 요약은 사용 행 기준이다.
    assert [row[0] for row in d["preview"]["rows"]] == [str(i) for i in range(20)]
    cols = {c["name"]: c for c in d["columns"]}
    assert cols["id"]["unique"] == SAMPLE_ROWS
    # 원본은 전체 행 그대로 저장된다.
    assert stored_files(settings)[0].read_bytes() == data
    # 목록·상세에서도 같은 정보가 보인다.
    detail = client.get(f"/datasets/{d['id']}").json()
    assert detail["banner"] == d["banner"] and detail["used_rows"] == SAMPLE_ROWS
    item = client.get("/datasets").json()["datasets"][0]
    assert item["sampled"] is True and item["total_rows"] == 150_000


def test_exactly_100k_rows_are_not_sampled(client):
    d = upload(client, _big_rows(100_000), "exact.csv").json()
    assert d["sampled"] is False and d["banner"] is None
    assert d["total_rows"] == d["used_rows"] == 100_000


def test_100001_rows_are_sampled(client):
    d = upload(client, _big_rows(100_001), "plus1.csv").json()
    assert d["sampled"] is True and d["used_rows"] == SAMPLE_ROWS
    assert d["banner"].startswith("전체 100,001행 중 100,000행")


def test_sampling_is_reproducible_and_keeps_row_order(tmp_path):
    p = tmp_path / "many.csv"
    p.write_bytes(_big_rows(150_000))
    enc = csv_ingest.detect_encoding(p)
    header, total = csv_ingest.scan_structure(p, enc)
    idx1 = csv_ingest.pick_sample_indices(total)
    idx2 = csv_ingest.pick_sample_indices(total)
    assert np.array_equal(idx1, idx2)
    assert len(idx1) == SAMPLE_ROWS and len(set(idx1.tolist())) == SAMPLE_ROWS
    assert bool(np.all(np.diff(idx1) > 0))  # 원래 순서 유지(오름차순)

    _, df_a = csv_ingest.load_frames(p, enc, header, idx1)
    _, df_b = csv_ingest.load_frames(p, enc, header, idx2)
    assert df_a.equals(df_b)
    # id 열이 행 위치와 같으므로 뽑힌 행이 정확히 표본 위치인지 확인할 수 있다.
    assert df_a["id"].astype(int).tolist() == idx1.tolist()


def test_same_file_uploaded_twice_gives_same_profile(client):
    data = _big_rows(120_000)
    a = upload(client, data, "a.csv").json()
    b = upload(client, data, "b.csv").json()
    assert a["columns"] == b["columns"] and a["used_rows"] == b["used_rows"]
    assert a["id"] != b["id"]
