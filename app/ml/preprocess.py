"""전처리와 분할 (SPEC 6-3). torch 를 쓰지 않는다.

- 숫자: 결측·해석 불가 값은 학습 세트 중앙값으로 채운 뒤 학습 세트 평균·표준편차로 표준화
- 범주: 결측은 학습 세트 최빈값으로 채운 뒤 원-핫 인코딩(학습 세트에 없던 값은 모두 0)
- 타깃: 분류는 클래스 번호로, 회귀는 학습 세트 평균·표준편차로 표준화
- 모든 통계는 학습 세트에서만 계산하고 검증·테스트에는 적용만 한다(누수 방지)
- 전처리 객체는 joblib 파일 하나로 저장·복원한다
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .common import CLASSIFICATION, REGRESSION, MLError, check_task

MAX_FEATURES = 1000
MIN_TARGET_ROWS = 20
MIN_CLASSES = 2
MAX_CLASSES = 20
MIN_PER_CLASS = 5
NUMERIC_TARGET_RATIO = 0.95
SPLIT_RATIOS = (0.70, 0.15, 0.15)
MISSING_LABEL = "결측"
PREPROCESSOR_FORMAT = 1


# ---------------------------------------------------------------------------
# 값 정리
# ---------------------------------------------------------------------------
def _numeric_values(series: pd.Series) -> np.ndarray:
    """숫자로 읽을 수 없는 값과 ±무한대는 NaN 으로 바꾼 float64 배열."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    values = np.array(values, dtype="float64", copy=True)
    values[~np.isfinite(values)] = np.nan
    return values


def _clean_strings(series: pd.Series) -> pd.Series:
    """앞뒤 공백을 지우고 빈 문자열은 결측(NA)으로 바꾼 문자열 Series."""
    s = series.astype("string").str.strip()
    return s.mask(s.fillna("") == "")


def _class_sort_key(label: str):
    try:
        return (0, float(label), label)
    except ValueError:
        return (1, 0.0, label)


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


# ---------------------------------------------------------------------------
# 전처리 객체
# ---------------------------------------------------------------------------
class Preprocessor:
    """학습 세트에서 fit 하고 어떤 표에도 transform 할 수 있는 전처리 객체."""

    def __init__(self, task: str, target: str, numeric_cols: list[str], categorical_cols: list[str]) -> None:
        check_task(task)
        numeric_cols, categorical_cols = list(numeric_cols), list(categorical_cols)
        if not numeric_cols and not categorical_cols:
            raise ValueError("특성 열이 하나도 없다")
        all_cols = numeric_cols + categorical_cols
        if len(set(all_cols)) != len(all_cols):
            raise ValueError("숫자 열과 범주 열에 겹치는 열이 있다")
        if target in all_cols:
            raise ValueError("타깃 열은 특성에 포함할 수 없다")
        self.task = task
        self.target = target
        self.numeric_cols = numeric_cols
        self.categorical_cols = categorical_cols
        self.fitted = False
        self.format_version = PREPROCESSOR_FORMAT
        # fit 결과
        self.numeric_median: dict[str, float] = {}
        self.numeric_mean: dict[str, float] = {}
        self.numeric_std: dict[str, float] = {}
        self.categorical_mode: dict[str, str] = {}
        self.categories: dict[str, list[str]] = {}
        self.classes: list[str] = []
        self.target_mean = 0.0
        self.target_std = 1.0
        self.feature_names: list[str] = []
        self.meta: dict[str, str] = {}

    # ---- fit ----
    def fit(self, df_train: pd.DataFrame) -> "Preprocessor":
        self._require_columns(df_train, include_target=True)
        if len(df_train) == 0:
            raise ValueError("학습 세트가 비어 있다")

        for col in self.numeric_cols:
            x = _numeric_values(df_train[col])
            observed = x[~np.isnan(x)]
            median = float(np.median(observed)) if observed.size else 0.0
            filled = np.where(np.isnan(x), median, x)
            mean = float(filled.mean())
            std = float(filled.std())  # 모집단 표준편차(ddof=0)
            self.numeric_median[col] = median
            self.numeric_mean[col] = mean
            self.numeric_std[col] = std if std > 0 else 1.0

        for col in self.categorical_cols:
            s = _clean_strings(df_train[col])
            counts = s.dropna().value_counts()
            if counts.empty:
                mode = MISSING_LABEL
                cats = [MISSING_LABEL]
            else:
                # 빈도가 같으면 사전순으로 앞의 값(재현 가능)
                top = counts.max()
                mode = sorted(str(v) for v in counts.index[counts == top])[0]
                cats = sorted({str(v) for v in counts.index} | {mode})
            self.categorical_mode[col] = mode
            self.categories[col] = cats

        self.feature_names = list(self.numeric_cols)
        for col in self.categorical_cols:
            self.feature_names += [f"{col}={c}" for c in self.categories[col]]
        if len(self.feature_names) > MAX_FEATURES:
            widest = sorted(self.categorical_cols, key=lambda c: len(self.categories[c]), reverse=True)[:3]
            raise MLError("E-CF-003", detail=f"특성 {len(self.feature_names)}개, 범주가 많은 열 {widest}")

        if self.task == CLASSIFICATION:
            labels = _clean_strings(df_train[self.target]).dropna()
            self.classes = sorted({str(v) for v in labels}, key=_class_sort_key)
        else:
            y = _numeric_values(df_train[self.target])
            y = y[~np.isnan(y)]
            if y.size == 0:
                raise MLError("E-CF-001", detail="타깃에 숫자 값이 없음")
            self.target_mean = float(y.mean())
            std = float(y.std())
            self.target_std = std if std > 0 else 1.0

        self.meta = {"numpy": np.__version__, "pandas": pd.__version__, "joblib": joblib.__version__}
        self.fitted = True
        return self

    # ---- transform ----
    @property
    def n_features(self) -> int:
        return len(self.feature_names)

    @property
    def n_classes(self) -> int:
        return len(self.classes)

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        """특성 행렬(float32). 숫자 열 뒤에 범주 열의 원-핫 칸이 열 순서대로 붙는다."""
        self._require_fitted()
        self._require_columns(df, include_target=False)
        n = len(df)
        out = np.zeros((n, self.n_features), dtype=np.float32)
        col_pos = 0
        for col in self.numeric_cols:
            x = _numeric_values(df[col])
            x = np.where(np.isnan(x), self.numeric_median[col], x)
            out[:, col_pos] = (x - self.numeric_mean[col]) / self.numeric_std[col]
            col_pos += 1
        for col in self.categorical_cols:
            cats = self.categories[col]
            s = _clean_strings(df[col]).fillna(self.categorical_mode[col])
            idx = pd.Index(cats).get_indexer(s.astype(object).to_numpy())
            known = np.flatnonzero(idx >= 0)  # 학습 세트에 없던 값(-1)은 모두 0
            out[known, col_pos + idx[known]] = 1.0
            col_pos += len(cats)
        return out

    def transform_target(self, df: pd.DataFrame) -> np.ndarray:
        """분류: 클래스 번호(int64), 회귀: 표준화된 값(float32)."""
        self._require_fitted()
        if self.target not in df.columns:
            raise ValueError("타깃 열이 없다")
        if self.task == CLASSIFICATION:
            labels = _clean_strings(df[self.target])
            if labels.isna().any():
                raise ValueError("타깃에 결측이 있다(분할 전에 제외해야 한다)")
            idx = pd.Index(self.classes).get_indexer(labels.astype(object).to_numpy())
            if (idx < 0).any():
                raise ValueError("학습 세트에 없던 클래스가 있다")
            return idx.astype(np.int64)
        y = _numeric_values(df[self.target])
        if np.isnan(y).any():
            raise ValueError("타깃에 결측이 있다(분할 전에 제외해야 한다)")
        return ((y - self.target_mean) / self.target_std).astype(np.float32)

    def inverse_target(self, y_scaled) -> np.ndarray:
        """회귀 예측값을 원래 단위로 되돌린다."""
        if self.task != REGRESSION:
            raise ValueError("회귀에서만 쓸 수 있다")
        return np.asarray(y_scaled, dtype="float64") * self.target_std + self.target_mean

    def decode_classes(self, class_idx) -> list[str]:
        if self.task != CLASSIFICATION:
            raise ValueError("분류에서만 쓸 수 있다")
        return [self.classes[int(i)] for i in class_idx]

    # ---- 요약(FR-20) ----
    def describe(self) -> list[str]:
        self._require_fitted()
        lines = [
            f"숫자 열 {len(self.numeric_cols)}개: 결측은 학습 세트 중앙값으로 채우고 평균·표준편차로 표준화합니다.",
            f"범주 열 {len(self.categorical_cols)}개: 결측은 학습 세트 최빈값으로 채우고 원-핫 인코딩합니다.",
            f"학습에 쓰는 특성은 모두 {self.n_features}개입니다.",
        ]
        if self.task == CLASSIFICATION:
            lines.append(f"타깃(분류): 클래스 {self.n_classes}개를 번호로 바꿉니다.")
        else:
            lines.append("타깃(회귀): 표준화해 학습하고 지표는 원래 단위로 계산합니다.")
        return lines

    # ---- 저장·복원 ----
    def save(self, path: Path) -> Path:
        """joblib 파일 하나로 저장한다(임시 파일에 쓴 뒤 교체하므로 중간에 끊겨도 기존 파일이 깨지지 않는다)."""
        self._require_fitted()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            joblib.dump(self, tmp, compress=3)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    @classmethod
    def load(cls, path: Path) -> "Preprocessor":
        """앱이 만든 파일만 불러온다(joblib 은 pickle 기반이라 출처를 알 수 없는 파일을 열면 안 된다)."""
        obj = joblib.load(Path(path))
        if not isinstance(obj, cls) or getattr(obj, "format_version", None) != PREPROCESSOR_FORMAT:
            raise ValueError("전처리 파일 형식이 맞지 않는다")
        return obj

    # ---- 내부 ----
    def _require_fitted(self) -> None:
        if not self.fitted:
            raise RuntimeError("fit 을 먼저 호출해야 한다")

    def _require_columns(self, df: pd.DataFrame, include_target: bool) -> None:
        need = self.numeric_cols + self.categorical_cols + ([self.target] if include_target else [])
        missing = [c for c in need if c not in df.columns]
        if missing:
            raise ValueError(f"표에 없는 열: {missing}")


# ---------------------------------------------------------------------------
# 분할
# ---------------------------------------------------------------------------
def _split_sizes(n: int, ratios=SPLIT_RATIOS) -> tuple[int, int, int]:
    n_val = max(1, _round_half_up(ratios[1] * n))
    n_test = max(1, _round_half_up(ratios[2] * n))
    return n - n_val - n_test, n_val, n_test


def split_indices(labels: np.ndarray | None, n: int, seed: int, ratios=SPLIT_RATIOS):
    """(학습, 검증, 테스트) 행 위치. labels 가 있으면 클래스별로 같은 비율을 유지(층화)한다.

    클래스당 5행이어도 검증·테스트에 각 1행 이상 배정한다. 각 집합 안에서는 원래 순서로 정렬한다.
    """
    rng = np.random.default_rng(seed)
    if labels is None:
        order = rng.permutation(n)
        n_train, n_val, n_test = _split_sizes(n, ratios)
        if n_train < 1:
            raise MLError("E-CF-004", detail=f"분할 불가 n={n}")
        parts = [order[:n_train], order[n_train:n_train + n_val], order[n_train + n_val:]]
    else:
        labels = np.asarray(labels)
        tr, va, te = [], [], []
        for cls in np.unique(labels):
            idx = np.flatnonzero(labels == cls)
            rng.shuffle(idx)
            n_train, n_val, n_test = _split_sizes(len(idx), ratios)
            if n_train < 1:
                raise MLError("E-CF-002", detail=f"클래스 {cls!r} 가 {len(idx)}행")
            tr.append(idx[:n_train])
            va.append(idx[n_train:n_train + n_val])
            te.append(idx[n_train + n_val:])
        parts = [np.concatenate(p) for p in (tr, va, te)]
    return tuple(np.sort(p) for p in parts)


@dataclass
class PreparedData:
    """학습에 바로 쓸 수 있는 분할·전처리 결과. idx_* 는 prepare_dataset 에 넘긴 표의 행 위치(0부터)다."""

    task: str
    target: str
    preprocessor: Preprocessor
    X_train: np.ndarray
    y_train: np.ndarray
    X_val: np.ndarray
    y_val: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray
    idx_train: np.ndarray
    idx_val: np.ndarray
    idx_test: np.ndarray
    n_rows_input: int
    n_dropped_target_missing: int

    @property
    def n_features(self) -> int:
        return self.X_train.shape[1]

    @property
    def n_classes(self) -> int:
        return self.preprocessor.n_classes

    @property
    def split_sizes(self) -> dict[str, int]:
        return {"train": len(self.X_train), "validation": len(self.X_val), "test": len(self.X_test)}


def prepare_dataset(
    df: pd.DataFrame,
    *,
    target: str,
    task: str,
    numeric_cols: list[str],
    categorical_cols: list[str],
    seed: int = 42,
) -> PreparedData:
    """타깃 결측 제외 → 타깃 검증 → 70/15/15 분할 → 학습 세트로만 fit → 세 집합 변환."""
    check_task(task)
    if target not in df.columns:
        raise ValueError("타깃 열이 표에 없다")

    raw = _clean_strings(df[target])
    present = raw.notna().to_numpy()
    if task == REGRESSION:
        if not present.any():
            raise MLError("E-CF-001", detail="타깃이 모두 비어 있음")
        parsed = ~np.isnan(_numeric_values(df[target]))
        if parsed[present].mean() < NUMERIC_TARGET_RATIO:
            raise MLError("E-CF-001", detail="타깃의 숫자 비율이 95% 미만")
        valid = present & parsed  # 숫자로 읽을 수 없는 값은 결측처럼 제외
    else:
        valid = present

    n_dropped = int((~valid).sum())
    if valid.sum() < MIN_TARGET_ROWS:
        raise MLError("E-CF-004", detail=f"타깃이 있는 행 {int(valid.sum())}개")

    pos = np.flatnonzero(valid)
    work = df.iloc[pos].reset_index(drop=True)

    labels = None
    if task == CLASSIFICATION:
        labels = raw.iloc[pos].astype(object).to_numpy()
        uniq, counts = np.unique(labels, return_counts=True)
        if not (MIN_CLASSES <= len(uniq) <= MAX_CLASSES) or counts.min() < MIN_PER_CLASS:
            raise MLError("E-CF-002", detail=f"클래스 {len(uniq)}개, 최소 {int(counts.min())}행")

    tr, va, te = split_indices(labels, len(work), seed)
    pre = Preprocessor(task, target, numeric_cols, categorical_cols).fit(work.iloc[tr])

    def part(idx):
        sub = work.iloc[idx]
        return pre.transform(sub), pre.transform_target(sub)

    X_tr, y_tr = part(tr)
    X_va, y_va = part(va)
    X_te, y_te = part(te)
    return PreparedData(
        task=task, target=target, preprocessor=pre,
        X_train=X_tr, y_train=y_tr, X_val=X_va, y_val=y_va, X_test=X_te, y_test=y_te,
        idx_train=pos[tr], idx_val=pos[va], idx_test=pos[te],
        n_rows_input=len(df), n_dropped_target_missing=n_dropped,
    )
