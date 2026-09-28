"""장치 선택과 CPU 스레드 제한. torch 대역(fake)으로 호출 규칙만 확인한다(실제 torch 는 test_ml_tabular_torch.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ml.device import CPU_THREADS, requested_device, select_device


class FakeTorch:
    def __init__(self, cuda: bool, name: str = "Fake GPU") -> None:
        self.threads_calls: list[int] = []
        self.cuda = SimpleNamespace(is_available=lambda: cuda, get_device_name=lambda i: name)

    def set_num_threads(self, n: int) -> None:
        self.threads_calls.append(n)


def test_cpu_request_uses_two_threads():
    t = FakeTorch(cuda=True)
    c = select_device("cpu", t)
    assert (c.device, c.threads, c.requested) == ("cpu", 2, "cpu")
    assert t.threads_calls == [CPU_THREADS] == [2]


def test_auto_without_gpu_falls_to_cpu_with_two_threads():
    t = FakeTorch(cuda=False)
    c = select_device("auto", t)
    assert c.device == "cpu" and t.threads_calls == [2] and "GPU가 없어" in c.reason


def test_auto_with_gpu_selects_cuda_and_leaves_threads_alone():
    t = FakeTorch(cuda=True, name="RTX Test")
    c = select_device("auto", t)
    assert c.device == "cuda" and c.cuda_name == "RTX Test" and c.threads is None
    assert t.threads_calls == []  # GPU 모드에서는 스레드를 제한하지 않는다


def test_cuda_request_without_gpu_falls_back_to_cpu():
    t = FakeTorch(cuda=False)
    c = select_device("cuda", t)
    assert c.device == "cpu" and c.requested == "cuda" and "쓸 수 없어" in c.reason
    assert t.threads_calls == [2]


def test_cuda_request_with_gpu():
    c = select_device("cuda", FakeTorch(cuda=True))
    assert c.device == "cuda"


def test_gpu_name_failure_does_not_block_cuda():
    t = FakeTorch(cuda=True)

    def boom(i):
        raise RuntimeError("no name")

    t.cuda.get_device_name = boom
    c = select_device("cuda", t)
    assert c.device == "cuda" and c.cuda_name is None


@pytest.mark.parametrize("value,expected", [
    (None, "auto"), ("", "auto"), ("cpu", "cpu"), ("CUDA", "cuda"), (" Auto ", "auto"), ("tpu", "auto"),
])
def test_requested_device_reads_app_device(value, expected):
    env = {} if value is None else {"APP_DEVICE": value}
    assert requested_device(env) == expected


def test_default_request_comes_from_environment(monkeypatch):
    monkeypatch.setenv("APP_DEVICE", "cpu")
    t = FakeTorch(cuda=True)
    assert select_device(None, t).device == "cpu"
    monkeypatch.setenv("APP_DEVICE", "cuda")
    assert select_device(None, FakeTorch(cuda=True)).device == "cuda"
    monkeypatch.delenv("APP_DEVICE")
    assert select_device(None, FakeTorch(cuda=False)).device == "cpu"
