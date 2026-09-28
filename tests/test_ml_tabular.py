"""TabularML 학습 테스트. 같은 테스트를 (1) numpy 로 만든 torch 대역과 (2) 실제 torch 로 돌린다.

실제 torch 가 없는 환경에서는 (2)만 건너뛴다. 컨테이너(test 타깃)에서는 둘 다 실행된다.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.common import MLError
from app.ml.preprocess import prepare_dataset
from app.ml.tabular import STOP_CANCELLED, STOP_EARLY, STOP_MAX_EPOCHS, TabularConfig, TabularML
from tests.fake_torch import FakeTorch
from tests.ml_data import CATEGORICAL, NUMERIC, make_frame


@pytest.fixture(params=["fake", "torch"])
def backend(request):
    """(torch 모듈 또는 None, 대역 여부). None 을 주면 TabularML 이 실제 torch 를 가져온다."""
    if request.param == "fake":
        return FakeTorch(), True
    pytest.importorskip("torch")
    return None, False


def prepared(task, target, n=400, missing=False):
    return prepare_dataset(make_frame(n, missing=missing), target=target, task=task,
                           numeric_cols=NUMERIC, categorical_cols=CATEGORICAL)


def cfg(task, **kw):
    kw.setdefault("max_epochs", 40)
    kw.setdefault("batch_size", 32)
    kw.setdefault("learning_rate", 0.01)
    return TabularConfig(task=task, **kw)


def model(backend, config, device="cpu"):
    return TabularML(config, device=device, torch_module=backend[0])


def test_classification_learns_and_reports_progress_every_epoch(backend):
    p = prepared("classification", "구간")
    seen = []
    m = model(backend, cfg("classification", early_stopping=False, max_epochs=30))
    r = m.fit(p, on_epoch=seen.append)

    assert r.stop_reason == STOP_MAX_EPOCHS and r.epochs_run == 30
    assert [e.epoch for e in seen] == list(range(1, 31)) and seen == r.history   # 에포크마다 1회, 순서대로
    assert all(e.max_epochs == 30 for e in seen)
    assert all(np.isfinite(e.train_loss) and np.isfinite(e.val_loss) for e in seen)
    assert all("accuracy" in e.val_metrics for e in seen)
    assert all(b.elapsed_sec >= a.elapsed_sec for a, b in zip(seen, seen[1:]))
    assert seen[-1].eta_sec == 0 and seen[0].eta_sec > 0
    assert seen[-1].train_loss < seen[0].train_loss                                # 손실이 줄어듦
    scores = m.evaluate_all(p)
    assert set(scores) == {"validation", "test"} and {"accuracy", "macro_f1"} <= set(scores["test"])
    assert scores["test"]["accuracy"] > 0.8


def test_regression_learns_and_reports_original_units(backend):
    p = prepared("regression", "금액")
    m = model(backend, cfg("regression", max_epochs=60, early_stopping=False))
    seen = []
    m.fit(p, on_epoch=seen.append)
    assert {"mae", "rmse", "r2"} == set(seen[-1].val_metrics)
    test = m.evaluate(p, "test")
    assert test["r2"] > 0.8
    assert test["rmse"] > 1.0                                                      # 표준화 단위가 아님
    pred = m.predict(p.X_test)
    assert pred.shape == (len(p.X_test),) and pred.mean() > 1000                   # 원래 단위(수천)


def test_cpu_mode_limits_threads_to_two(backend):
    p = prepared("classification", "구간", n=120)
    m = model(backend, cfg("classification", max_epochs=1))
    r = m.fit(p)
    assert r.device.device == "cpu" and r.device.threads == 2
    if backend[1]:
        assert backend[0].threads_calls == [2]
    else:
        import torch

        assert torch.get_num_threads() == 2


def test_app_device_env_selects_device(backend, monkeypatch):
    p = prepared("classification", "구간", n=120)
    monkeypatch.setenv("APP_DEVICE", "cuda")
    r = TabularML(cfg("classification", max_epochs=1), torch_module=backend[0]).fit(p)
    if backend[1]:
        assert r.device.device == "cpu" and "쓸 수 없어" in r.device.reason      # 대역에는 GPU 가 없음 → CPU 로 내려감
    else:
        import torch

        assert r.device.device == ("cuda" if torch.cuda.is_available() else "cpu")
    monkeypatch.setenv("APP_DEVICE", "cpu")
    assert TabularML(cfg("classification", max_epochs=1), torch_module=backend[0]).fit(p).device.device == "cpu"


def test_gpu_is_used_when_available_and_threads_are_left_alone():
    p = prepared("classification", "구간", n=120)
    t = FakeTorch(cuda=True)
    r = TabularML(cfg("classification", max_epochs=1), device="auto", torch_module=t).fit(p)
    assert r.device.device == "cuda" and t.threads_calls == []


def test_cancel_stops_at_epoch_boundary(backend):
    p = prepared("classification", "구간", n=120)
    seen = []
    m = model(backend, cfg("classification", max_epochs=50, early_stopping=False))
    r = m.fit(p, on_epoch=seen.append, should_cancel=lambda: len(seen) >= 3)
    assert r.stop_reason == STOP_CANCELLED and r.epochs_run == 3 and len(seen) == 3
    assert r.stop_reason_label == "사용자 요청으로 중단했습니다."


def test_early_stopping_and_best_weights_restored(backend):
    p = prepared("classification", "구간", n=200)
    rng = np.random.default_rng(0)
    p.y_train = rng.integers(0, 3, size=len(p.y_train)).astype(np.int64)         # 무작위 라벨: 검증 손실이 곧 악화됨
    m = model(backend, cfg("classification", max_epochs=200, patience=3))
    r = m.fit(p)
    assert r.stop_reason == STOP_EARLY and r.epochs_run < 200
    assert r.epochs_run - r.best_epoch >= 3
    best = min(e.val_loss for e in r.history)
    assert r.history[r.best_epoch - 1].val_loss == pytest.approx(best)
    # 되돌린 가중치로 계산한 검증 손실이 기록된 최저값과 같다(최고 성능 복원)
    proba = m.predict_proba(p.X_val)
    nll = -np.log(proba[np.arange(len(p.y_val)), p.y_val] + 1e-12).mean()
    assert nll == pytest.approx(best, rel=1e-3)


def test_early_stopping_off_runs_all_epochs(backend):
    p = prepared("classification", "구간", n=120)
    p.y_train = np.random.default_rng(1).integers(0, 3, size=len(p.y_train)).astype(np.int64)
    r = model(backend, cfg("classification", max_epochs=8, patience=1, early_stopping=False)).fit(p)
    assert r.epochs_run == 8 and r.stop_reason == STOP_MAX_EPOCHS


def test_same_seed_gives_same_history_and_other_seed_differs(backend):
    p = prepared("classification", "구간", n=150)

    def losses(seed):
        m = model(backend, cfg("classification", max_epochs=5, seed=seed, early_stopping=False))
        return [e.val_loss for e in m.fit(p).history]

    assert losses(42) == pytest.approx(losses(42), abs=1e-6)
    assert losses(42) != pytest.approx(losses(7), abs=1e-6)


def test_divergence_is_e_jb_003(backend):
    p = prepared("regression", "금액", n=120)
    p.X_train[0, 0] = np.inf
    with np.errstate(all="ignore"):
        with pytest.raises(MLError) as e:
            model(backend, cfg("regression", max_epochs=3)).fit(p)
    assert e.value.code == "E-JB-003"


def test_predict_proba_rows_sum_to_one_and_shapes(backend):
    p = prepared("classification", "구간", n=150)
    m = model(backend, cfg("classification", max_epochs=3))
    m.fit(p)
    proba = m.predict_proba(p.X_test)
    assert proba.shape == (len(p.X_test), 3) and np.allclose(proba.sum(axis=1), 1, atol=1e-5)
    assert m.predict(p.X_test).shape == (len(p.X_test),)
    with pytest.raises(RuntimeError):
        model(backend, cfg("classification")).predict(p.X_test)


def test_task_mismatch_is_rejected_and_last_batch_of_one_works(backend):
    p = prepared("classification", "구간", n=60)
    with pytest.raises(ValueError):
        model(backend, cfg("regression")).fit(p)
    pr = prepared("regression", "금액", n=47)
    assert len(pr.X_train) % 16 == 1                      # 마지막 배치가 1행
    model(backend, cfg("regression", batch_size=16, max_epochs=1)).fit(pr)


def test_missing_values_in_features_do_not_break_training(backend):
    p = prepared("classification", "구간", missing=True)
    m = model(backend, cfg("classification", max_epochs=30, early_stopping=False))
    m.fit(p)
    assert m.evaluate(p, "test")["accuracy"] > 0.6
