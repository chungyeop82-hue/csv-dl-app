"""데이터셋 목록·상세·삭제."""

from __future__ import annotations

import sqlite3
import uuid

from tests.helpers import assert_one_sentence_error, make_csv, stored_files, upload


def test_list_is_empty_at_first(client):
    assert client.get("/datasets").json()["datasets"] == []


def test_list_shows_newest_first_with_summary_fields(client):
    a = upload(client, make_csv(60), "첫번째.csv").json()
    b = upload(client, make_csv(70, encoding="cp949"), "두번째.csv").json()
    items = client.get("/datasets").json()["datasets"]
    assert [i["id"] for i in items] == [b["id"], a["id"]]
    assert items[0]["original_name"] == "두번째.csv"
    assert items[0]["encoding_label"] == "CP949"
    assert items[0]["total_rows"] == 70 and items[0]["n_columns"] == 6
    assert "preview" not in items[0]  # 목록은 요약만


def test_detail_returns_same_content_as_upload_response(client):
    uploaded = upload(client, make_csv(80), "x.csv").json()
    detail = client.get(f"/datasets/{uploaded['id']}").json()
    assert detail == uploaded


def test_delete_removes_file_and_db_row(client, settings):
    d = upload(client, make_csv(60), "삭제대상.csv").json()
    other = upload(client, make_csv(60), "유지.csv").json()
    assert len(stored_files(settings)) == 2

    r = client.delete(f"/datasets/{d['id']}")
    assert r.status_code == 200 and r.json() == {"deleted": d["id"]}
    assert [f.name for f in stored_files(settings)] == [f"{other['id']}.csv"]
    assert [i["id"] for i in client.get("/datasets").json()["datasets"]] == [other["id"]]

    conn = sqlite3.connect(settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM datasets WHERE id=?", (d["id"],)).fetchone()[0] == 0
    finally:
        conn.close()
    assert_one_sentence_error(client.get(f"/datasets/{d['id']}"), "E-DS-001")


def test_delete_unknown_id_is_e_ds_001(client):
    assert_one_sentence_error(client.delete(f"/datasets/{uuid.uuid4()}"), "E-DS-001")
    assert_one_sentence_error(client.delete("/datasets/not-a-uuid"), "E-DS-001")


def test_delete_twice_second_is_not_found(client):
    d = upload(client, make_csv(60)).json()
    assert client.delete(f"/datasets/{d['id']}").status_code == 200
    assert_one_sentence_error(client.delete(f"/datasets/{d['id']}"), "E-DS-001")


def test_delete_works_even_if_file_already_missing(client, settings):
    d = upload(client, make_csv(60)).json()
    stored_files(settings)[0].unlink()
    assert client.delete(f"/datasets/{d['id']}").status_code == 200
    assert client.get("/datasets").json()["datasets"] == []


def test_data_survives_app_restart(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings)) as c1:
        d = upload(c1, make_csv(60), "남아야함.csv").json()
    with TestClient(create_app(settings)) as c2:
        items = c2.get("/datasets").json()["datasets"]
        assert [i["id"] for i in items] == [d["id"]]
        assert c2.get(f"/datasets/{d['id']}").json()["preview"] == d["preview"]
