"""베이스라인: 로지스틱(회귀는 선형)·랜덤 포레스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ml.baselines import LINEAR, RANDOM_FOREST, baseline_label, run_baseline, run_baselines
from app.ml.preprocess import prepare_dataset
from tests.ml_data import CATEGORICAL, NUMERIC, make_frame, read_like_upload


def prepared(task, target, df=None, missing=False):
    df = make_frame(400, missing=missing) if df is None else df
    return prepare_dataset(df, target=target, task=task, numeric_cols=NUMERIC, categorical_cols=CATEGORICAL)


@pytest.mark.parametrize("kind", [LINEAR, RANDOM_FOREST])
def test_classification_baselines_beat_chance(kind):
    p = prepared("classification", "구간")
    r = run_baseline(kind, p)
    assert r.kind == kind and r.fit_seconds >= 0
    for split in (r.validation, r.test):
        assert set(split) == {"accuracy", "macro_f1"}  # 3클래스라 ROC-AUC 없음
        assert split["accuracy"] > 0.85 and split["macro_f1"] > 0.8


def test_binary_classification_reports_roc_auc():
    df = make_frame(400, missing=False)
    df["구간"] = np.where(df["구간"] == "높음", "yes", "no")
    r = run_baseline(LINEAR, prepared("classification", "구간", df))
    assert r.test["roc_auc"] > 0.9


@pytest.mark.parametrize("kind", [LINEAR, RANDOM_FOREST])
def test_regression_metrics_are_in_original_units(kind):
    df = make_frame(400, missing=False)
    p = prepared("regression", "금액", df)
    r = run_baseline(kind, p)
    target_std = pd.to_numeric(df["금액"]).std()          # 수백 단위
    assert set(r.test) == {"mae", "rmse", "r2"}
    assert r.test["r2"] > 0.8
    assert r.test["rmse"] < 0.5 * target_std             # 표준화 단위(≈1)가 아니라 원래 단위
    assert r.test["rmse"] > 1.0
    assert r.test["mae"] <= r.test["rmse"] + 1e-9


def test_baselines_work_with_missing_values_in_features():
    r = run_baseline(LINEAR, prepared("classification", "구간", missing=True))
    assert r.test["accuracy"] > 0.6  # 결측이 있어도 대체 후 학습되고 무작위(0.33)보다 좋다


def test_labels_reflect_task():
    assert baseline_label(LINEAR, "classification") == "로지스틱 회귀"
    assert baseline_label(LINEAR, "regression") == "선형 회귀(릿지)"
    assert baseline_label(RANDOM_FOREST, "regression") == "랜덤 포레스트"
    with pytest.raises(ValueError):
        baseline_label("svm", "regression")


def test_run_baselines_returns_both_and_is_reproducible():
    p = prepared("classification", "구간")
    a = run_baselines(p, seed=5)
    b = run_baselines(p, seed=5)
    assert [r.kind for r in a] == [LINEAR, RANDOM_FOREST]
    assert [r.test for r in a] == [r.test for r in b]
    assert a[1].model.n_jobs == 2 and a[1].model.n_estimators == 100  # CPU 를 독점하지 않음
