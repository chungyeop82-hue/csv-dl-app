"""실제 과제 데이터셋(반도체 웨이퍼 픽앤플레이스) 컬럼으로 TargetBin 분류가 되는지 확인한다.

HEAD, PICK_WAFER_ROW/COL, PLACE_WAFER_ROW/COL, ChipSizeX/Y, DieGapLeft/Right/Top/Bottom, ANGLE,
TargetBin(0=Fail, 1=Good) 컬럼을 그대로 쓴다. torch 가 없어도 도는 순수 전처리 테스트다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ml.common import CLASSIFICATION
from app.ml.preprocess import prepare_dataset
from tests.ml_data import read_like_upload

FEATURE_COLUMNS = [
    "HEAD",
    "PICK_WAFER_ROW",
    "PICK_WAFER_COL",
    "PLACE_WAFER_ROW",
    "PLACE_WAFER_COL",
    "ChipSizeX",
    "ChipSizeY",
    "DieGapLeft",
    "DieGapRight",
    "DieGapTop",
    "DieGapBottom",
    "ANGLE",
]
TARGET = "TargetBin"


def make_wafer_frame(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "HEAD": rng.integers(1, 5, n),
            "PICK_WAFER_ROW": rng.integers(0, 50, n),
            "PICK_WAFER_COL": rng.integers(0, 50, n),
            "PLACE_WAFER_ROW": rng.integers(0, 50, n),
            "PLACE_WAFER_COL": rng.integers(0, 50, n),
            "ChipSizeX": rng.uniform(1.0, 5.0, n).round(3),
            "ChipSizeY": rng.uniform(1.0, 5.0, n).round(3),
            "DieGapLeft": rng.uniform(0.0, 0.5, n).round(3),
            "DieGapRight": rng.uniform(0.0, 0.5, n).round(3),
            "DieGapTop": rng.uniform(0.0, 0.5, n).round(3),
            "DieGapBottom": rng.uniform(0.0, 0.5, n).round(3),
            "ANGLE": rng.uniform(-1.0, 1.0, n).round(3),
        }
    )
    # ANGLE 이 클수록(정렬이 틀어질수록) Fail(0) 이 되기 쉽게 만들어 분리 가능한 타깃을 만든다.
    fail_prob = 1 / (1 + np.exp(-(3.0 * df["ANGLE"].abs() - 1.2)))
    df["TargetBin"] = np.where(rng.random(n) < fail_prob, 0, 1)
    return read_like_upload(df)  # 업로드 후 실제로 받는 형태(dtype=str)를 흉내 낸다


def test_targetbin_is_usable_as_binary_classification_target():
    df = make_wafer_frame(400)
    prepared = prepare_dataset(
        df, target=TARGET, task=CLASSIFICATION, numeric_cols=FEATURE_COLUMNS, categorical_cols=[]
    )
    assert prepared.preprocessor.classes == ["0", "1"]  # 0=Fail, 1=Good 순서 그대로
    assert prepared.n_classes == 2
    assert prepared.n_features == len(FEATURE_COLUMNS)
    # 두 클래스 모두 세 분할에 나타난다(층화 분할).
    for y in (prepared.y_train, prepared.y_val, prepared.y_test):
        assert set(np.unique(y)) == {0, 1}
    assert prepared.n_dropped_target_missing == 0


def test_targetbin_with_string_labels_fail_good_also_works():
    """0/1 대신 'Fail'/'Good' 문자열이어도(실무에서 흔함) 그대로 분류 타깃이 된다."""
    df = make_wafer_frame(300, seed=1)
    df["TargetBin"] = df["TargetBin"].map({"0": "Fail", "1": "Good"})
    prepared = prepare_dataset(
        df, target=TARGET, task=CLASSIFICATION, numeric_cols=FEATURE_COLUMNS, categorical_cols=[]
    )
    assert set(prepared.preprocessor.classes) == {"Fail", "Good"}


def test_targetbin_also_passes_regression_format_checks_but_assignment_uses_classification():
    """TargetBin 은 0/1 숫자라 회귀 입력 형식 검사(숫자 비율 95%)도 통과한다.

    다만 이번 과제 범위에서는 TargetBin(Fail/Good)을 분류 타깃으로만 쓴다 - 이 테스트는
    prepare_dataset 이 과제 문제 유형(task 인자)을 그대로 따른다는 것만 확인한다.
    """
    from app.ml.common import REGRESSION

    df = make_wafer_frame(300, seed=2)
    prepared = prepare_dataset(
        df, target=TARGET, task=REGRESSION, numeric_cols=FEATURE_COLUMNS, categorical_cols=[]
    )
    assert prepared.task == REGRESSION
    assert prepared.y_train.dtype == np.float32
