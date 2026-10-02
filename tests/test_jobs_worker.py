"""run_training_job 의 예외 -> 결과코드 매핑을 직접 호출해서 검증한다(항목 21·22).

TabularML.fit 까지 가기 전에 prepare_dataset 을 가짜로 바꿔치기하므로 torch 가 없어도 돈다.
큐·플래그는 진짜 멀티프로세싱 대신 가벼운 흉내로 충분하다(worker 는 .put()/.value 만 쓴다).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import jobs_worker
from app.ml.common import MLError
from tests.helpers import make_csv


class _FakeQueue:
    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)


def _stop_flag(value: bool = False):
    return SimpleNamespace(value=value)


@pytest.fixture()
def dataset_file(tmp_path):
    path = tmp_path / "ds.csv"
    path.write_bytes(make_csv(80))
    return path


def _run(dataset_file, monkeypatch, raiser):
    monkeypatch.setattr(jobs_worker, "prepare_dataset", raiser)
    return jobs_worker.run_training_job(
        "job-1", str(dataset_file), "utf-8", "classification", "등급", ["나이"], [],
        {"task": "classification", "max_epochs": 1}, None, str(dataset_file.with_suffix(".pt")),
        _FakeQueue(), _stop_flag(),
    )


def test_memory_error_maps_to_e_jb_004(dataset_file, monkeypatch):
    def raiser(*a, **k):
        raise MemoryError()

    out = _run(dataset_file, monkeypatch, raiser)
    assert out == {"status": "failed", "error_code": "E-JB-004", "detail": "MemoryError"}


def test_ml_error_from_preprocessing_is_passed_through(dataset_file, monkeypatch):
    def raiser(*a, **k):
        raise MLError("E-CF-002", detail="클래스 불균형")

    out = _run(dataset_file, monkeypatch, raiser)
    assert out["status"] == "failed" and out["error_code"] == "E-CF-002"


def test_cuda_out_of_memory_message_maps_to_e_jb_005(dataset_file, monkeypatch):
    def raiser(*a, **k):
        raise RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB")

    out = _run(dataset_file, monkeypatch, raiser)
    assert out["status"] == "failed" and out["error_code"] == "E-JB-005"


def test_real_cuda_out_of_memory_error_class_maps_to_e_jb_005(dataset_file, monkeypatch):
    class FakeOOM(RuntimeError):
        pass

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=FakeOOM))
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)

    def raiser(*a, **k):
        raise FakeOOM("out of memory")

    out = _run(dataset_file, monkeypatch, raiser)
    assert out["status"] == "failed" and out["error_code"] == "E-JB-005"


def test_unexpected_exception_maps_to_e_sy_002_with_detail_for_log_only(dataset_file, monkeypatch):
    def raiser(*a, **k):
        raise RuntimeError("무언가 예상 못한 문제 /data/db/app.sqlite3")

    out = _run(dataset_file, monkeypatch, raiser)
    assert out["status"] == "failed" and out["error_code"] == "E-SY-002"
    assert "예상 못한 문제" in out["detail"]  # 상세는 로그로만 가고, API 계층에서 화면에는 노출하지 않는다


def test_file_not_found_maps_to_e_ds_002(dataset_file, monkeypatch):
    """디스패처의 사전 확인(_dispatch_next)과 이 워커 실행 사이에 데이터셋이 지워지는 아주 좁은
    틈을 흉내낸다 - 분류되지 않은 예외(E-SY-002) 대신 명확한 E-DS-002 로 돌려줘야 한다."""

    def raiser(*a, **k):
        raise FileNotFoundError("데이터셋 CSV 없음")

    out = _run(dataset_file, monkeypatch, raiser)
    assert out == {"status": "failed", "error_code": "E-DS-002", "detail": "데이터셋 CSV 없음"}
