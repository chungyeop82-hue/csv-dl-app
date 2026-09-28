"""베이스라인 모델 (SPEC 6-7): 로지스틱 회귀(회귀 문제는 선형 회귀)와 랜덤 포레스트. torch 를 쓰지 않는다.

신경망과 같은 PreparedData(같은 전처리·분할)를 쓰고, 지표는 6-5절과 같은 형식(회귀는 원래 단위)으로 낸다.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, Ridge

from .common import CLASSIFICATION
from .metrics import classification_metrics, regression_metrics
from .preprocess import PreparedData

LINEAR = "linear"
RANDOM_FOREST = "random_forest"
BASELINE_KINDS = (LINEAR, RANDOM_FOREST)
RF_TREES = 100
N_JOBS = 2  # CPU 를 독점하지 않는다 (ARCHITECTURE 5-3)


@dataclass
class BaselineResult:
    kind: str
    label: str
    validation: dict[str, float | None]
    test: dict[str, float | None]
    fit_seconds: float
    model: object


def baseline_label(kind: str, task: str) -> str:
    if kind == LINEAR:
        return "로지스틱 회귀" if task == CLASSIFICATION else "선형 회귀(릿지)"
    if kind == RANDOM_FOREST:
        return "랜덤 포레스트"
    raise ValueError(f"알 수 없는 베이스라인: {kind!r}")


def _make_model(kind: str, task: str, seed: int):
    cls = task == CLASSIFICATION
    if kind == LINEAR:
        return LogisticRegression(max_iter=1000, random_state=seed) if cls else Ridge(alpha=1.0, random_state=seed)
    if kind == RANDOM_FOREST:
        rf = RandomForestClassifier if cls else RandomForestRegressor
        return rf(n_estimators=RF_TREES, n_jobs=N_JOBS, random_state=seed)
    raise ValueError(f"알 수 없는 베이스라인: {kind!r}")


def evaluate_model(model, prepared: PreparedData, X: np.ndarray, y: np.ndarray) -> dict[str, float | None]:
    pre = prepared.preprocessor
    if prepared.task == CLASSIFICATION:
        proba = model.predict_proba(X)
        return classification_metrics(y, proba.argmax(axis=1), proba)
    return regression_metrics(pre.inverse_target(y), pre.inverse_target(model.predict(X)))


def run_baseline(kind: str, prepared: PreparedData, seed: int = 42) -> BaselineResult:
    model = _make_model(kind, prepared.task, seed)
    start = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        model.fit(prepared.X_train, prepared.y_train)
    seconds = time.perf_counter() - start
    return BaselineResult(
        kind=kind,
        label=baseline_label(kind, prepared.task),
        validation=evaluate_model(model, prepared, prepared.X_val, prepared.y_val),
        test=evaluate_model(model, prepared, prepared.X_test, prepared.y_test),
        fit_seconds=seconds,
        model=model,
    )


def run_baselines(prepared: PreparedData, seed: int = 42, kinds=BASELINE_KINDS) -> list[BaselineResult]:
    return [run_baseline(k, prepared, seed) for k in kinds]
