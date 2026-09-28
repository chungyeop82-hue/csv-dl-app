"""지표 (SPEC 6-5). torch 를 쓰지 않는다. 회귀 지표는 호출하는 쪽이 원래 단위 값을 넘긴다."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, r2_score, roc_auc_score


def classification_metrics(y_true, y_pred, proba=None) -> dict[str, float | None]:
    """정확도, 매크로 F1, (이진 분류이고 확률이 있으면) ROC-AUC."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    out: dict[str, float | None] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
    }
    if proba is not None:
        proba = np.asarray(proba)
        if proba.ndim == 2 and proba.shape[1] == 2:
            try:
                out["roc_auc"] = float(roc_auc_score(y_true, proba[:, 1]))
            except ValueError:  # 평가 집합에 한 클래스뿐이면 정의되지 않음
                out["roc_auc"] = None
    return out


def regression_metrics(y_true, y_pred) -> dict[str, float]:
    """MAE, RMSE, R²."""
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "r2": float(r2_score(y_true, y_pred)),
    }
