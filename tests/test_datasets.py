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


# --- STEP 10 검증 중 발견한 버그 수정: 데이터셋 삭제 원자성 ---------------------------------
#
# 예전 코드는 "CSV 파일 unlink -> DELETE FROM datasets" 순서였다. jobs.dataset_id 가
# datasets.id 를 FK 로 참조하는데 ON DELETE CASCADE 가 없어서, 그 데이터셋으로 학습한 Job이
# 하나라도 있으면 CSV는 이미 지워졌는데 datasets 행 삭제는 FK 위반으로 실패해 500(E-SY-002)이
# 났고, DB에는 남지만 파일은 없는 고아(orphan) 데이터셋이 생겼다. 아래 테스트들은 그 가설을
# 실제 코드로 재현하고, 수정 후(job_events -> jobs -> datasets 를 한 트랜잭션에서 먼저 지우고
# 커밋이 성공한 뒤에만 파일을 지우는 방식) 더 이상 그 상태가 생기지 않는지 확인한다.


def _insert_job(conn, dataset_id: str, status: str, model_path: str | None = None) -> str:
    """디스패처/워커를 거치지 않고 jobs 테이블에 바로 행을 심는다.

    (tests/test_jobs_training.py 의 test_app_restart_recovers_running_job_as_interrupted 와
    같은 방식 - 실제 학습 없이도 "학습 이력이 있는 데이터셋" 상태를 만들 수 있다.)
    """
    job_id = str(uuid.uuid4())
    now = "2024-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO jobs (id, dataset_id, task, target, config_json, status, model_path,"
        " error_code, cancel_requested, created_at, started_at, finished_at)"
        " VALUES (?, ?, 'classification', '등급', '{}', ?, ?, ?, 0, ?, ?, ?)",
        (job_id, dataset_id, status, model_path, "E-JB-004" if status == "failed" else None, now, now, now),
    )
    return job_id


def _insert_job_event(conn, job_id: str, message: str) -> None:
    conn.execute(
        "INSERT INTO job_events (job_id, ts, level, message) VALUES (?, ?, 'info', ?)",
        (job_id, "2024-01-01T00:00:00Z", message),
    )


def test_delete_with_completed_and_failed_jobs_cleans_up_db_and_model_files(client, settings):
    """B. completed/failed Job이 있는 데이터셋 삭제: FK 위반(500) 없이 끝나야 하고, 그 Job이
    남긴 모델 산출물(.pt, .prep.joblib)도 함께 정리되어야 한다."""
    from app import db

    d = upload(client, make_csv(60)).json()
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    model_path = settings.models_dir / "completed-job.pt"
    model_path.write_bytes(b"fake-model")
    model_path.with_suffix(".prep.joblib").write_bytes(b"fake-prep")

    with db.session(settings.db_path) as conn:
        completed_id = _insert_job(conn, d["id"], "completed", model_path=str(model_path))
        failed_id = _insert_job(conn, d["id"], "failed")

    r = client.delete(f"/datasets/{d['id']}")
    assert r.status_code == 200 and r.json() == {"deleted": d["id"]}, r.text

    assert stored_files(settings) == []
    assert not model_path.exists()
    assert not model_path.with_suffix(".prep.joblib").exists()

    with db.session(settings.db_path) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE id IN (?, ?)", (completed_id, failed_id)
        ).fetchone()[0]
        assert remaining == 0
        assert conn.execute("SELECT COUNT(*) FROM datasets WHERE id=?", (d["id"],)).fetchone()[0] == 0


def test_delete_with_job_events_cleans_up_events_too(client, settings):
    """C. job_events가 있는 데이터셋 삭제: job_events부터 먼저 지워져야 jobs/datasets 삭제가
    FK 위반 없이 끝난다."""
    from app import db

    d = upload(client, make_csv(60)).json()
    with db.session(settings.db_path) as conn:
        job_id = _insert_job(conn, d["id"], "failed")
        _insert_job_event(conn, job_id, "학습을 시작합니다.")
        _insert_job_event(conn, job_id, "학습이 실패 상태가 되었습니다.")

    r = client.delete(f"/datasets/{d['id']}")
    assert r.status_code == 200

    with db.session(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_events WHERE job_id=?", (job_id,)).fetchone()[0] == 0


def test_delete_orphan_dataset_with_job_present_now_succeeds(client, settings):
    """D. CSV 파일이 이미 없는 orphan dataset 삭제: 이 버그가 실제로 만들어내던 상태(Job이 있는
    데이터셋의 CSV만 먼저 사라진 상태)를 그대로 재현해, 삭제 버튼을 다시 눌렀을 때 이번에는
    정상적으로 끝나는지 확인한다."""
    from app import db

    d = upload(client, make_csv(60)).json()
    with db.session(settings.db_path) as conn:
        job_id = _insert_job(conn, d["id"], "completed")
    stored_files(settings)[0].unlink()  # 예전 버그로 이미 지워졌던 CSV 파일 상태를 흉내낸다

    r = client.delete(f"/datasets/{d['id']}")
    assert r.status_code == 200 and r.json() == {"deleted": d["id"]}, r.text

    with db.session(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == 0


def test_delete_leaves_no_related_rows_in_any_table(client, settings):
    """E. 삭제 후 datasets/jobs/job_events 어디에도 관련 행이 남지 않아야 한다."""
    from app import db

    d = upload(client, make_csv(60)).json()
    with db.session(settings.db_path) as conn:
        job_id = _insert_job(conn, d["id"], "completed")
        _insert_job_event(conn, job_id, "학습을 시작합니다.")

    assert client.delete(f"/datasets/{d['id']}").status_code == 200

    with db.session(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM datasets WHERE id=?", (d["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE dataset_id=?", (d["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_events WHERE job_id=?", (job_id,)).fetchone()[0] == 0
