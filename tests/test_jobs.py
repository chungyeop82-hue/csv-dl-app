"""학습 잡 API: 생성 검증, 동시 실행 거절, 취소, 조회. (실제 학습이 끝까지 도는 시나리오는
test_jobs_training.py 에 있다 - 실제 torch 가 필요해 없으면 건너뛴다.)"""

from __future__ import annotations

import uuid

from tests.helpers import assert_one_sentence_error, make_csv, upload


def _dataset(client):
    return upload(client, make_csv(120)).json()


def test_create_job_requires_existing_dataset(client):
    body = {"dataset_id": str(uuid.uuid4()), "task": "classification", "target": "등급"}
    assert_one_sentence_error(client.post("/jobs", json=body), "E-DS-001")


def test_create_job_rejects_unknown_target_column(client):
    d = _dataset(client)
    body = {"dataset_id": d["id"], "task": "classification", "target": "없는열"}
    assert_one_sentence_error(client.post("/jobs", json=body), "E-CF-001")


def test_create_job_rejects_unknown_task(client):
    d = _dataset(client)
    body = {"dataset_id": d["id"], "task": "time_series", "target": "등급"}
    r = client.post("/jobs", json=body)
    # pydantic Literal 검증 실패 -> 전역 검증 오류 처리기가 한국어 한 문장으로 변환(app/errors.py)
    assert_one_sentence_error(r, "E-SY-004")


def test_create_job_succeeds_with_queued_status(client):
    d = _dataset(client)
    body = {"dataset_id": d["id"], "task": "classification", "target": "등급", "max_epochs": 1}
    r = client.post("/jobs", json=body)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert job["task"] == "classification" and job["target"] == "등급"
    assert job["error_code"] is None and "progress" not in job


def test_second_job_is_rejected_while_one_is_active(client):
    d = _dataset(client)
    body = {"dataset_id": d["id"], "task": "classification", "target": "등급", "max_epochs": 1}
    first = client.post("/jobs", json=body)
    assert first.status_code == 201, first.text
    second = client.post("/jobs", json=body)
    assert_one_sentence_error(second, "E-JB-001", 409)


def test_get_unknown_job_is_e_jb_002(client):
    assert_one_sentence_error(client.get(f"/jobs/{uuid.uuid4()}"), "E-JB-002")
    assert_one_sentence_error(client.get("/jobs/not-a-uuid"), "E-JB-002")


def test_cancel_unknown_job_is_e_jb_002(client):
    assert_one_sentence_error(client.post(f"/jobs/{uuid.uuid4()}/cancel"), "E-JB-002")


def test_cancel_queued_job_is_settled_immediately(client):
    d = _dataset(client)
    job = client.post(
        "/jobs", json={"dataset_id": d["id"], "task": "classification", "target": "등급", "max_epochs": 1}
    ).json()
    r = client.post(f"/jobs/{job['id']}/cancel")
    assert r.status_code == 200
    # 디스패처가 먼저 집어갔을 수도 있으므로(타이밍), queued 였다면 즉시 cancelled 로 확정된다.
    assert r.json()["status"] in ("cancelled", "running", "completed")


def test_cancel_already_finished_job_is_a_no_op(client):
    d = _dataset(client)
    job = client.post(
        "/jobs", json={"dataset_id": d["id"], "task": "classification", "target": "등급", "max_epochs": 1}
    ).json()
    client.post(f"/jobs/{job['id']}/cancel")  # queued 상태이므로 곧바로 cancelled
    again = client.post(f"/jobs/{job['id']}/cancel")
    assert again.status_code == 200
    assert again.json()["id"] == job["id"]


def test_list_jobs_shows_newest_first(client):
    d = _dataset(client)
    a = client.post(
        "/jobs", json={"dataset_id": d["id"], "task": "classification", "target": "등급", "max_epochs": 1}
    ).json()
    client.post(f"/jobs/{a['id']}/cancel")
    b = client.post(
        "/jobs", json={"dataset_id": d["id"], "task": "classification", "target": "등급", "max_epochs": 1}
    ).json()
    items = client.get("/jobs").json()["jobs"]
    assert [i["id"] for i in items[:2]] == [b["id"], a["id"]]


def test_events_endpoint_streams_json_lines(client):
    d = _dataset(client)
    job = client.post(
        "/jobs", json={"dataset_id": d["id"], "task": "classification", "target": "등급", "max_epochs": 1}
    ).json()
    with client.stream("GET", f"/jobs/{job['id']}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data:"):
                import json as _json

                payload = _json.loads(line[len("data:"):].strip())
                assert "status" in payload
                break
