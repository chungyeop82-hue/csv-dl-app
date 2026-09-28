"""리포트 생성과 제출 ZIP 테스트 (SPEC 6-6, FR-38~40·FR-52, PPT STEP 7).

- build_korean_summary/sanitize_export_name 은 torch 가 필요 없는 순수 로직이라 먼저 검사한다.
- 나머지는 실제 학습 job 이 완료돼야 하므로 tests/test_jobs_training.py 의 도우미를 그대로 쓰고,
  torch 가 없는 환경에서는 건너뛴다(그 파일과 같은 이유).
"""

from __future__ import annotations

import json
import re
import zipfile

import pytest

from app.reports import build_korean_summary, sanitize_export_name
from tests.test_jobs_training import _upload_ml_dataset, _wait_for_terminal

# ---------------------------------------------------------------------------
# 순수 로직 단위 테스트 (torch 불필요)
# ---------------------------------------------------------------------------
def test_sanitize_export_name_strips_windows_forbidden_chars():
    assert sanitize_export_name('2024고급:분석*팀?<보고서>|"1"') == "2024고급분석팀보고서1"


def test_sanitize_export_name_strips_trailing_dot_and_space():
    assert sanitize_export_name("학번1 . ") == "학번1"


def test_sanitize_export_name_empty_falls_back_to_placeholder():
    assert sanitize_export_name("") == "무제"
    assert sanitize_export_name("   ") == "무제"
    assert sanitize_export_name(None) == "무제"


def test_sanitize_export_name_truncates_to_max_chars():
    assert sanitize_export_name("가" * 100, max_chars=10) == "가" * 10


def test_build_korean_summary_returns_exactly_three_sentences_classification():
    summary = build_korean_summary(
        "classification",
        {"accuracy": 0.9123, "macro_f1": 0.876},
        [{"kind": "logistic_regression", "label": "로지스틱 회귀", "test": {"accuracy": 0.80}}],
        "max_epochs",
        20,
        20,
    )
    assert len(summary) == 3
    assert "정확도" in summary[0] and "매크로 F1" in summary[0]
    assert "로지스틱 회귀" in summary[1] and "높습니다" in summary[1]
    assert "20/20" in summary[2]


def test_build_korean_summary_returns_exactly_three_sentences_regression():
    summary = build_korean_summary(
        "regression",
        {"r2": 0.5, "rmse": 12.3},
        [{"kind": "linear_regression", "label": "선형 회귀", "test": {"r2": 0.7}}],
        "early_stopping",
        15,
        100,
    )
    assert len(summary) == 3
    assert "R²" in summary[0]
    assert "선형 회귀" in summary[1] and "낮아" in summary[1]
    assert "15/100" in summary[2]


def test_build_korean_summary_handles_no_baselines():
    summary = build_korean_summary("classification", {"accuracy": 0.5, "macro_f1": 0.5}, [], "cancelled", 3, 100)
    assert len(summary) == 3
    assert "베이스라인 비교 결과가 없습니다." == summary[1]


# ---------------------------------------------------------------------------
# HTML 리포트 (실제 학습 job 필요 - torch)
# ---------------------------------------------------------------------------
pytest.importorskip("torch")

TERMINAL = {"completed", "failed", "cancelled", "timeout", "interrupted"}


def _completed_classification_job(client):
    d = _upload_ml_dataset(client)
    job = client.post(
        "/jobs",
        json={
            "dataset_id": d["id"], "task": "classification", "target": "구간",
            "preset": "small", "max_epochs": 6, "early_stopping": False, "batch_size": 32,
            "learning_rate": 0.01, "seed": 1,
        },
    ).json()
    final = _wait_for_terminal(client, job["id"])
    assert final["status"] == "completed", final
    return final, d


def _completed_regression_job(client):
    d = _upload_ml_dataset(client)
    job = client.post(
        "/jobs",
        json={
            "dataset_id": d["id"], "task": "regression", "target": "금액",
            "preset": "small", "max_epochs": 6, "early_stopping": False, "batch_size": 32,
            "learning_rate": 0.01, "seed": 1,
        },
    ).json()
    final = _wait_for_terminal(client, job["id"])
    assert final["status"] == "completed", final
    return final, d


def test_report_html_classification_has_expected_sections_and_no_object_object(client):
    job, _dataset = _completed_classification_job(client)
    resp = client.get(f"/jobs/{job['id']}/report.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text

    assert "[object Object]" not in body
    for needle in (
        "학습 결과 리포트", "요약", "핵심 지표", "학습 곡선", "혼동행렬",
        "딥러닝 모델 vs 베이스라인", "Permutation Importance",
        '<link rel="stylesheet" href="/static/report.css">',
    ):
        assert needle in body, needle
    assert "data:image/png;base64," in body  # 차트가 실제로 그려져 임베드됐는지


def test_report_html_regression_has_actual_vs_predicted_and_no_confusion_matrix(client):
    job, _dataset = _completed_regression_job(client)
    resp = client.get(f"/jobs/{job['id']}/report.html")
    assert resp.status_code == 200
    body = resp.text

    assert "[object Object]" not in body
    assert "예측-실제 비교" in body
    assert "혼동행렬" not in body
    assert "data:image/png;base64," in body


def test_report_for_nonexistent_job_returns_not_found(client):
    import uuid

    resp = client.get(f"/jobs/{uuid.uuid4()}/report.html")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "E-JB-002"


def test_submit_without_student_id_returns_error(client):
    job, _dataset = _completed_classification_job(client)
    resp = client.post(f"/jobs/{job['id']}/submit", json={})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E-RP-002"


def test_submit_for_incomplete_job_returns_error(client):
    # epochs=200·n=2000 이면 요청 직후에는 아직 running/queued 이다(취소 테스트와 같은 방식).
    d = _upload_ml_dataset(client, n=2000)
    job = client.post(
        "/jobs",
        json={
            "dataset_id": d["id"], "task": "classification", "target": "구간",
            "preset": "small", "max_epochs": 200, "early_stopping": False, "batch_size": 16,
        },
    ).json()
    resp = client.post(f"/jobs/{job['id']}/submit", json={"student_id": "20240001"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E-RP-001"
    client.post(f"/jobs/{job['id']}/cancel")  # 뒷정리: 다음 테스트로 프로세스를 끌고 가지 않는다
    _wait_for_terminal(client, job["id"], timeout=60.0)


def test_submit_creates_zip_with_four_files_and_environment_txt(client, settings):
    job, dataset = _completed_classification_job(client)
    resp = client.post(f"/jobs/{job['id']}/submit", json={"student_id": "20240001"})
    assert resp.status_code == 200, resp.text
    filename = resp.json()["filename"]
    assert re.fullmatch(r"20240001_ml_\d{8}\.zip", filename), filename

    zip_path = settings.export_dir / filename
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert names == {"report.pdf", "metrics.json", "config.json", "environment.txt"}

        pdf_bytes = zf.read("report.pdf")
        assert pdf_bytes.startswith(b"%PDF")

        metrics = json.loads(zf.read("metrics.json"))
        assert metrics["task"] == "classification"
        assert set(metrics["test"]) >= {"accuracy", "macro_f1", "precision_macro", "recall_macro"}
        assert metrics["confusion_matrix"]["matrix"]
        assert len(metrics["permutation_importance"]) <= 10
        assert isinstance(metrics["learning_curve"], list) and len(metrics["learning_curve"]) > 0

        config = json.loads(zf.read("config.json"))
        assert config["dataset"]["original_name"] == dataset["original_name"]
        assert config["target"] == "구간"

        env_text = zf.read("environment.txt").decode("utf-8")
        for needle in ("Python 버전:", "PyTorch 버전:", "CUDA 사용 가능 여부:", "GPU 장치명:", "실제 학습 실행 장치:"):
            assert needle in env_text, needle


def test_submit_reuses_previously_saved_student_id(client, settings):
    job1, _d1 = _completed_classification_job(client)
    resp1 = client.post(f"/jobs/{job1['id']}/submit", json={"student_id": "20240002"})
    assert resp1.status_code == 200, resp1.text

    assert client.get("/system/student-id").json()["student_id"] == "20240002"

    job2, _d2 = _completed_classification_job(client)
    resp2 = client.post(f"/jobs/{job2['id']}/submit", json={})  # 학번 생략 -> 저장된 값 재사용
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["filename"].startswith("20240002_")
