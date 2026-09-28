"""ML 테스트용 합성 데이터. CSV 를 거쳐 dtype=str 로 읽어, 업로드 후 실제로 받게 되는 형태를 흉내 낸다."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd

NUMERIC = ["나이", "소득"]
CATEGORICAL = ["도시", "등급"]


def make_frame(n: int = 300, seed: int = 0, missing: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = rng.uniform(20, 60, n).round(1)
    income = rng.normal(5000, 1500, n).round(0)
    city = rng.choice(["서울", "부산", "대구"], n, p=[0.5, 0.3, 0.2])
    grade = rng.choice(["상", "중", "하"], n)
    # 분류 타깃: 나이·소득으로 거의 결정되는 3개 클래스(선형 분리 가능)
    score = (age - 40) / 10 + (income - 5000) / 1500
    label = np.where(score < -0.7, "낮음", np.where(score < 0.7, "보통", "높음"))
    # 회귀 타깃: 선형 관계 + 작은 잡음, 원래 단위가 크도록 오프셋을 둔다
    target = 3000 + 40 * age + 0.5 * income + rng.normal(0, 30, n)
    df = pd.DataFrame(
        {"나이": age, "소득": income, "도시": city, "등급": grade, "구간": label, "금액": target.round(2)}
    )
    if missing:
        for col in ("나이", "소득", "도시"):
            df.loc[rng.random(n) < 0.1, col] = np.nan
    return read_like_upload(df)


def read_like_upload(df: pd.DataFrame) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(df.to_csv(index=False)), dtype=str)
