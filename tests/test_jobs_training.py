"""잡 -> ProcessPoolExecutor -> 실제 torch 학습까지 끝까지 도는 통합 테스트.

실제 torch 와 서브프로세스가 필요해 시간이 걸린다(각 테스트 최대 수십 초). torch 가 없는
환경에서는 건너뛴다 - 컨테이너(test 타깃)에는 torch 가 설치되어 있으므로 거기서는 실행된다.
"""

from __future__ import annotations

import time

import pytest

from tests.helpers import upload
from tests.ml_data import make_frame

pytest.importorskip("torch")

TERMINAL = {"completed", "failed", "cancelled", "timeout", "interrupted"}


def _upload_ml_dataset(client, n=300, seed=0):
    df = make_frame(n, seed=seed, missing=False)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    return upload(client, csv_bytes, "ml.csv").json()


def _wait_for_terminal(client, job_id, timeout=60.0, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in TERMINAL:
            return job
        time.sleep(interval)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout}s")


def test_classification_job_completes_with_metrics_and_saved_model(client, settings):
    d = _upload_ml_dataset(client)
    job = client.post(
        "/jobs",
        json={
            "dataset_id": d["id"], "task": "classification", "target": "구간",
            "preset": "small", "max_epochs": 8, "early_stopping": False, "batch_size": 32,
            "learning_rate": 0.01, "seed": 1,
        },
    ).json()
    assert job["status"] == "queued"

    final = _wait_for_terminal(client, job["id"])
    assert final["status"] == "completed", final
    assert final["device"] == "cpu"
    metrics = final["metrics"]
    assert set(metrics["validation"]) >= {"accuracy", "macro_f1"}
    assert set(metrics["test"]) >= {"accuracy", "macro_f1"}
    assert len(metrics["baselines"]) == 2  # 로지스틱 회귀 + 랜덤 포레스트(항목 9·10)

    model_files = list(settings.models_dir.glob(f"{job['id']}.*"))
    assert any(p.suffix == ".pt" for p in model_files)
    assert any(p.name.endswith(".prep.joblib") for p in model_files)


def test_regression_job_completes(client):
    d = _upload_ml_dataset(client)
    job = client.post(
        "/jobs",
        json={
            "dataset_id": d["id"], "task": "regression", "target": "금액",
            "preset": "small", "max_epochs": 10, "early_stopping": False, "batch_size": 32,
            "learning_rate": 0.01, "seed": 1,
        },
    ).json()
    final = _wait_for_terminal(client, job["id"])
    assert final["status"] == "completed", final
    assert set(final["metrics"]["test"]) >= {"mae", "rmse", "r2"}


def test_cancel_while_active_eventually_settles_to_cancelled(client):
    d = _upload_ml_dataset(client, n=2000)
    job = client.post(
        "/jobs",
        json={
            "dataset_id": d["id"], "task": "classification", "target": "구간",
            "preset": "small", "max_epochs": 100000, "early_stopping": False, "batch_size": 16,
        },
    ).json()
    client.post(f"/jobs/{job['id']}/cancel")
    final = _wait_for_terminal(client, job["id"], timeout=60.0)
    assert final["status"] == "cancelled", final


def test_timeout_forces_status_timeout(client, monkeypatch):
    monkeypatch.setenv("APP_MAX_TRAIN_SECONDS", "1")
    d = _upload_ml_dataset(client, n=2000)
    job = client.post(
        "/jobs",
        json={
            "dataset_id": d["id"], "task": "classification", "target": "구간",
            "preset": "small", "max_epochs": 100000, "early_stopping": False, "batch_size": 16,
        },
    ).json()
    final = _wait_for_terminal(client, job["id"], timeout=60.0)
    assert final["status"] == "timeout", final


def test_app_restart_recovers_running_job_as_interrupted(settings):
    """디스패처를 거치지 않고 직접 running 행을 심어, 재시작 시 interrupted 로 바뀌는지만 확인한다."""
    import json
    import uuid

    from app import db
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings)) as c1:
        d = _upload_ml_dataset(c1)
        job_id = str(uuid.uuid4())
        with db.session(settings.db_path) as conn:
            conn.execute(
                "INSERT INTO jobs (id, dataset_id, task, target, config_json, status, cancel_requested, created_at,"
                " started_at) VALUES (?,?,?,?,?, 'running', 0, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')",
                (job_id, d["id"], "classification", "구간", json.dumps({"numeric_cols": [], "categorical_cols": [],
                 "tabular_config": {"task": "classification"}})),
            )

    with TestClient(create_app(settings)) as c2:
        job = c2.get(f"/jobs/{job_id}").json()
        assert job["status"] == "interrupted"
