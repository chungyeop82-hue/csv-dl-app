"""전처리·분할: 중앙값/최빈값 대체, 원-핫, 표준화, 층화 분할, 누수 방지, joblib 저장."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.errors import CATALOG
from app.ml.common import MLError
from app.ml.preprocess import (
    MAX_FEATURES,
    MISSING_LABEL,
    Preprocessor,
    prepare_dataset,
    split_indices,
)
from tests.ml_data import CATEGORICAL, NUMERIC, make_frame, read_like_upload

ROOT = Path(__file__).resolve().parent.parent


def prep(df, task="classification", target="구간", seed=42, **kw):
    return prepare_dataset(df, target=target, task=task, numeric_cols=NUMERIC,
                           categorical_cols=CATEGORICAL, seed=seed, **kw)


# ---------------------------------------------------------------- 대체·표준화·원-핫
def small_frame():
    return read_like_upload(pd.DataFrame({
        "x": [1, 2, 3, 4, None, 100],
        "c": ["a", "a", "b", None, "b", "b"],
        "y": [0, 1, 0, 1, 0, 1],
    }))


def test_numeric_median_and_categorical_mode_fill_missing():
    df = small_frame()
    pre = Preprocessor("regression", "y", ["x"], ["c"]).fit(df)
    assert pre.numeric_median["x"] == 3.0            # 결측 제외 [1,2,3,4,100] 의 중앙값
    assert pre.categorical_mode["c"] == "b"          # a:2, b:3
    X = pre.transform(df)
    # 결측 행(x=None)은 중앙값 3 으로 채워진 뒤 표준화된 값과 같다
    filled = np.array([1, 2, 3, 4, 3, 100], dtype=float)
    expected = (filled - filled.mean()) / filled.std()
    assert np.allclose(X[:, 0], expected, atol=1e-6)
    # 결측 범주(행 3)는 최빈값 b 의 원-핫이 된다
    assert pre.feature_names == ["x", "c=a", "c=b"]
    assert X[3, 1:].tolist() == [0.0, 1.0]
    assert X.dtype == np.float32 and X.shape == (6, 3)


def test_standardized_train_features_have_zero_mean_unit_std():
    p = prep(make_frame(400))
    numeric = p.X_train[:, :2]
    assert np.allclose(numeric.mean(axis=0), 0, atol=1e-5)
    assert np.allclose(numeric.std(axis=0), 1, atol=1e-4)


def test_mode_tie_breaks_alphabetically():
    df = read_like_upload(pd.DataFrame({"c": ["나", "가", "나", "가", "다"], "y": [1, 2, 3, 4, 5]}))
    pre = Preprocessor("regression", "y", [], ["c"]).fit(df)
    assert pre.categorical_mode["c"] == "가"


def test_unknown_category_becomes_all_zeros_and_missing_uses_mode():
    train = read_like_upload(pd.DataFrame({"c": ["a", "b", "b", "c"], "y": [1, 2, 3, 4]}))
    pre = Preprocessor("regression", "y", [], ["c"]).fit(train)
    new = read_like_upload(pd.DataFrame({"c": ["zzz", None, "a", "  b  "], "y": [1, 2, 3, 4]}))
    X = pre.transform(new)
    assert pre.feature_names == ["c=a", "c=b", "c=c"]
    assert X[0].tolist() == [0, 0, 0]        # 학습 세트에 없던 값 = 알 수 없음
    assert X[1].tolist() == [0, 1, 0]        # 결측 = 최빈값 b
    assert X[2].tolist() == [1, 0, 0]
    assert X[3].tolist() == [0, 1, 0]        # 공백은 정리됨


def test_degenerate_columns_are_safe():
    df = read_like_upload(pd.DataFrame({
        "allnan": [None] * 5, "const": [7] * 5, "junk": ["a", "b", "inf", "-inf", "1"],
        "cat_empty": [None] * 5, "y": [1, 2, 3, 4, 5]}))
    pre = Preprocessor("regression", "y", ["allnan", "const", "junk"], ["cat_empty"]).fit(df)
    X = pre.transform(df)
    assert np.isfinite(X).all()
    assert pre.numeric_median["allnan"] == 0.0 and pre.numeric_std["allnan"] == 1.0
    assert np.allclose(X[:, 1], 0)                       # 상수 열은 0
    assert pre.categories["cat_empty"] == [MISSING_LABEL]  # 값이 전혀 없는 범주 열만 예외
    assert pre.numeric_median["junk"] == 1.0             # 해석 가능한 값은 "1" 하나뿐


def test_constructor_validates_columns():
    with pytest.raises(ValueError):
        Preprocessor("regression", "y", [], [])
    with pytest.raises(ValueError):
        Preprocessor("regression", "y", ["a"], ["a"])
    with pytest.raises(ValueError):
        Preprocessor("regression", "a", ["a"], [])
    with pytest.raises(ValueError):
        Preprocessor("clustering", "y", ["a"], [])


# ---------------------------------------------------------------- 분할
def test_split_is_70_15_15_disjoint_and_complete():
    p = prep(make_frame(300, missing=False), task="regression", target="금액")
    assert p.split_sizes == {"train": 210, "validation": 45, "test": 45}  # 회귀: 정확히 70/15/15
    pc = prep(make_frame(300, missing=False))                             # 분류: 클래스별 반올림이라 ±(클래스 수) 이내
    for key, want in {"train": 210, "validation": 45, "test": 45}.items():
        assert abs(pc.split_sizes[key] - want) <= pc.n_classes
    for q in (p, pc):
        all_idx = np.concatenate([q.idx_train, q.idx_val, q.idx_test])
        assert sorted(all_idx.tolist()) == list(range(300))
        for idx in (q.idx_train, q.idx_val, q.idx_test):
            assert (np.diff(idx) > 0).all()  # 원래 순서로 정렬


def test_classification_split_is_stratified():
    df = make_frame(600, missing=False)
    p = prep(df)
    overall = df["구간"].value_counts(normalize=True)
    for idx in (p.idx_train, p.idx_val, p.idx_test):
        share = df["구간"].iloc[idx].value_counts(normalize=True)
        for cls, ratio in overall.items():
            assert abs(share[cls] - ratio) < 0.04


def test_class_with_five_rows_still_reaches_validation_and_test():
    labels = np.array(["a"] * 5 + ["b"] * 40)
    tr, va, te = split_indices(labels, len(labels), seed=1)
    for cls in ("a", "b"):
        for part in (tr, va, te):
            assert (labels[part] == cls).sum() >= 1
    assert (labels[tr] == "a").sum() == 3


def test_split_is_reproducible_and_seed_dependent():
    df = make_frame(200)
    a, b, c = prep(df, seed=42), prep(df, seed=42), prep(df, seed=7)
    assert np.array_equal(a.idx_test, b.idx_test) and np.array_equal(a.X_train, b.X_train)
    assert not np.array_equal(a.idx_test, c.idx_test)


def test_statistics_come_from_train_only_no_leakage():
    df = make_frame(300, missing=False)
    p = prep(df)
    train_age = pd.to_numeric(df["나이"]).iloc[p.idx_train].to_numpy()
    assert p.preprocessor.numeric_median["나이"] == pytest.approx(float(np.median(train_age)))
    assert p.preprocessor.numeric_mean["나이"] == pytest.approx(float(train_age.mean()))
    # 검증·테스트 값을 극단적으로 바꿔도 학습 세트 통계와 학습 행렬은 변하지 않는다
    df2 = df.copy()
    held_out = np.concatenate([p.idx_val, p.idx_test])
    df2.loc[held_out, "나이"] = "9999999"
    df2.loc[held_out, "도시"] = "새도시"
    p2 = prep(df2)
    assert np.array_equal(p.idx_train, p2.idx_train)
    assert p2.preprocessor.numeric_mean["나이"] == p.preprocessor.numeric_mean["나이"]
    assert p2.preprocessor.categories["도시"] == p.preprocessor.categories["도시"]
    assert np.array_equal(p.X_train, p2.X_train)


# ---------------------------------------------------------------- 타깃
def test_missing_targets_are_dropped_and_counted_and_indices_map_to_original_rows():
    df = make_frame(200, missing=False)
    df.loc[[3, 50, 120], "구간"] = None
    p = prep(df)
    assert p.n_dropped_target_missing == 3 and p.n_rows_input == 200
    used = np.concatenate([p.idx_train, p.idx_val, p.idx_test])
    assert len(used) == 197 and not ({3, 50, 120} & set(used.tolist()))
    # 원본 행 번호로 되짚으면 클래스 번호가 원본 라벨과 일치한다
    labels = p.preprocessor.decode_classes(p.y_test)
    assert labels == df["구간"].iloc[p.idx_test].tolist()


def test_classification_classes_are_numbered_and_decodable():
    p = prep(make_frame(300))
    assert sorted(p.preprocessor.classes) == ["낮음", "높음", "보통"]
    assert p.y_train.dtype == np.int64 and set(p.y_train.tolist()) <= {0, 1, 2}
    assert p.n_classes == 3


def test_numeric_class_labels_sort_numerically():
    df = read_like_upload(pd.DataFrame({"a": range(90), "y": [10, 2, 1] * 30}))
    p = prepare_dataset(df, target="y", task="classification", numeric_cols=["a"], categorical_cols=[])
    assert p.preprocessor.classes == ["1", "2", "10"]


def test_regression_target_is_standardized_and_invertible():
    df = make_frame(300, missing=False)
    p = prep(df, task="regression", target="금액")
    assert p.y_train.dtype == np.float32
    assert abs(float(p.y_train.mean())) < 1e-4 and abs(float(p.y_train.std()) - 1) < 1e-3
    restored = p.preprocessor.inverse_target(p.y_test)
    assert np.allclose(restored, pd.to_numeric(df["금액"]).iloc[p.idx_test].to_numpy(), rtol=1e-4)


def test_regression_drops_unreadable_target_values():
    df = make_frame(100, missing=False)
    df.loc[[1, 2], "금액"] = "모름"
    df.loc[5, "금액"] = None
    p = prep(df, task="regression", target="금액")
    assert p.n_dropped_target_missing == 3


@pytest.mark.parametrize("bad", ["text", "empty"])
def test_regression_with_non_numeric_target_is_e_cf_001(bad):
    df = make_frame(100, missing=False)
    df["금액"] = ["가나"] * 100 if bad == "text" else None
    with pytest.raises(MLError) as e:
        prep(df, task="regression", target="금액")
    assert e.value.code == "E-CF-001" and e.value.message == CATALOG["E-CF-001"][1]


def test_class_count_and_size_rules_are_e_cf_002():
    df = make_frame(100, missing=False)
    for labels in (["A"] * 100,                                   # 클래스 1개
                   [f"c{i % 21}" for i in range(100)],            # 클래스 21개
                   ["A"] * 48 + ["B"] * 48 + ["C"] * 4):          # 한 클래스가 4행
        d = df.copy()
        d["구간"] = labels
        with pytest.raises(MLError) as e:
            prep(d)
        assert e.value.code == "E-CF-002"
    d = df.copy()
    d["구간"] = [f"c{i % 20}" for i in range(100)]                 # 클래스 20개, 각 5행 = 허용
    assert prep(d).n_classes == 20


def test_too_few_target_rows_is_e_cf_004():
    df = make_frame(100, missing=False)
    df.loc[10:, "구간"] = None
    with pytest.raises(MLError) as e:
        prep(df)
    assert e.value.code == "E-CF-004"


def test_too_many_features_is_e_cf_003_with_reason_for_log():
    n = MAX_FEATURES * 2  # 학습 세트(70%)에서도 1,000개를 넘도록
    df = read_like_upload(pd.DataFrame({"id": [f"k{i}" for i in range(n)], "x": range(n), "y": [0, 1] * (n // 2)}))
    with pytest.raises(MLError) as e:
        prepare_dataset(df, target="y", task="classification", numeric_cols=["x"], categorical_cols=["id"])
    assert e.value.code == "E-CF-003" and "id" in e.value.detail
    assert "id" not in e.value.message  # 사용자 문구에는 내부 값이 없다


# ---------------------------------------------------------------- joblib
def test_preprocessor_joblib_roundtrip(tmp_path):
    df = make_frame(300)
    p = prep(df)
    path = p.preprocessor.save(tmp_path / "out" / "preprocessor.joblib")
    assert path.is_file() and list(path.parent.glob("*.tmp")) == []
    loaded = Preprocessor.load(path)
    assert loaded is not p.preprocessor and loaded.fitted
    assert loaded.feature_names == p.preprocessor.feature_names and loaded.classes == p.preprocessor.classes
    assert np.array_equal(loaded.transform(df), p.preprocessor.transform(df))
    assert loaded.meta["pandas"] == pd.__version__
    # 새 표에도 그대로 적용된다(예측 시 재사용)
    fresh = make_frame(20, seed=99)
    assert np.array_equal(loaded.transform(fresh), p.preprocessor.transform(fresh))


def test_regression_preprocessor_roundtrip_keeps_target_scale(tmp_path):
    p = prep(make_frame(300), task="regression", target="금액")
    loaded = Preprocessor.load(p.preprocessor.save(tmp_path / "r.joblib"))
    y = np.array([-1.0, 0.0, 2.5])
    assert np.array_equal(loaded.inverse_target(y), p.preprocessor.inverse_target(y))


def test_load_rejects_other_objects_and_unfitted_save(tmp_path):
    import joblib

    joblib.dump({"not": "a preprocessor"}, tmp_path / "x.joblib")
    with pytest.raises(ValueError):
        Preprocessor.load(tmp_path / "x.joblib")
    with pytest.raises(RuntimeError):
        Preprocessor("regression", "y", ["x"], []).save(tmp_path / "y.joblib")
    with pytest.raises(RuntimeError):
        Preprocessor("regression", "y", ["x"], []).transform(small_frame())


def test_transform_requires_columns():
    p = prep(make_frame(100))
    with pytest.raises(ValueError):
        p.preprocessor.transform(pd.DataFrame({"나이": ["1"]}))


def test_describe_is_korean_summary():
    lines = prep(make_frame(100)).preprocessor.describe()
    assert any("중앙값" in l for l in lines) and any("최빈값" in l for l in lines) and any("원-핫" in l for l in lines)


# ---------------------------------------------------------------- torch 비의존
def test_importing_ml_modules_does_not_load_torch():
    code = (
        "import sys\n"
        "import app.ml, app.ml.preprocess, app.ml.baselines, app.ml.metrics, app.ml.device, app.ml.tabular\n"
        "print('torch' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_ml_modules_do_not_import_web_framework():
    """학습 라이브러리는 웹 프레임워크를 몰라야 한다(카탈로그만 가벼운 별도 모듈에서 가져옴)."""
    code = (
        "import sys\n"
        "import app.ml.preprocess, app.ml.baselines, app.ml.metrics, app.ml.device, app.ml.tabular\n"
        "print('fastapi' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
