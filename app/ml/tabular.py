"""TabularML: 표 형태 데이터용 소형 MLP 분류·회귀 (SPEC 6-4).

- 활성화 ReLU, 최적화 Adam, 손실은 분류 교차 엔트로피 / 회귀 MSE(표준화된 타깃)
- 장치는 APP_DEVICE 와 torch.cuda.is_available() 로 고르고, CPU 면 torch.set_num_threads(2)
- 에포크가 끝날 때마다 진행률 콜백을 호출하고, 콜백이 요청하면 다음 에포크 경계에서 멈춘다
- 조기 종료(검증 손실 patience 에포크 연속 무개선), 수치 발산(NaN)은 E-JB-003
- torch 는 이 모듈을 가져올 때가 아니라 fit/predict 안에서만 가져온다
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .common import CLASSIFICATION, MLError, check_task
from .device import DeviceChoice, select_device
from .metrics import classification_metrics, regression_metrics
from .preprocess import PreparedData

# 프리셋: (은닉층 너비, 드롭아웃)
PRESETS: dict[str, tuple[tuple[int, ...], float]] = {
    "small": ((64, 32), 0.1),
    "medium": ((128, 64, 32), 0.2),
    "large": ((256, 128, 64), 0.3),
}
BATCH_SIZES = (16, 32, 64, 128)
EVAL_CHUNK = 8192

STOP_MAX_EPOCHS = "max_epochs"
STOP_EARLY = "early_stopping"
STOP_CANCELLED = "cancelled"
STOP_REASON_LABELS = {
    STOP_MAX_EPOCHS: "최대 에포크까지 학습했습니다.",
    STOP_EARLY: "검증 손실이 더 이상 개선되지 않아 조기 종료했습니다.",
    STOP_CANCELLED: "사용자 요청으로 중단했습니다.",
}


@dataclass(frozen=True)
class TabularConfig:
    task: str
    hidden_layers: tuple[int, ...] = (64, 32)
    dropout: float = 0.1
    learning_rate: float = 0.001
    batch_size: int = 64
    max_epochs: int = 100
    early_stopping: bool = True
    patience: int = 10
    seed: int = 42

    def __post_init__(self) -> None:
        check_task(self.task)
        layers = tuple(self.hidden_layers)
        object.__setattr__(self, "hidden_layers", layers)
        if not 1 <= len(layers) <= 4:
            raise ValueError("은닉층은 1~4개여야 한다")
        if any(not isinstance(w, int) or not 16 <= w <= 256 for w in layers):
            raise ValueError("은닉층 너비는 16~256 정수여야 한다")
        if not 0.0 <= self.dropout <= 0.5:
            raise ValueError("드롭아웃은 0~0.5 여야 한다")
        if not 0.0001 <= self.learning_rate <= 0.01:
            raise ValueError("학습률은 0.0001~0.01 여야 한다")
        if self.batch_size not in BATCH_SIZES:
            raise ValueError(f"배치 크기는 {BATCH_SIZES} 중 하나여야 한다")
        if not 1 <= self.max_epochs <= 200:
            raise ValueError("최대 에포크는 1~200 여야 한다")
        if self.patience < 1:
            raise ValueError("patience 는 1 이상이어야 한다")

    @classmethod
    def from_preset(cls, task: str, preset: str = "small", **overrides) -> "TabularConfig":
        if preset not in PRESETS:
            raise ValueError(f"프리셋은 {tuple(PRESETS)} 중 하나여야 한다")
        layers, dropout = PRESETS[preset]
        return cls(task=task, hidden_layers=layers, dropout=dropout, **overrides)


@dataclass(frozen=True)
class EpochProgress:
    """에포크가 끝날 때마다 콜백으로 전달되는 진행 상황."""

    epoch: int
    max_epochs: int
    train_loss: float
    val_loss: float
    val_metrics: dict[str, float]  # 분류: accuracy / 회귀: mae, rmse, r2 (원래 단위)
    elapsed_sec: float
    eta_sec: float  # 최대 에포크까지 갈 때 남은 시간(조기 종료하면 더 짧아짐)
    best_epoch: int
    is_best: bool


@dataclass
class TrainResult:
    history: list[EpochProgress] = field(default_factory=list)
    best_epoch: int = 0
    epochs_run: int = 0
    stop_reason: str = STOP_MAX_EPOCHS
    device: DeviceChoice | None = None
    seconds: float = 0.0

    @property
    def stop_reason_label(self) -> str:
        return STOP_REASON_LABELS[self.stop_reason]


ProgressCallback = Callable[[EpochProgress], None]
CancelCheck = Callable[[], bool]


class TabularML:
    def __init__(self, config: TabularConfig, device: str | None = None, torch_module=None) -> None:
        """device: auto|cpu|cuda (None 이면 환경 변수 APP_DEVICE). torch_module 는 테스트용 주입 지점이다."""
        self.config = config
        self._requested_device = device
        self._torch = torch_module
        self.model = None
        self.result: TrainResult | None = None
        self._device = None
        self._preprocessor = None

    # ---- 내부 도우미 ----
    def _get_torch(self):
        if self._torch is None:
            import torch  # noqa: PLC0415 - 지연 import

            self._torch = torch
        return self._torch

    def _build(self, torch, n_in: int, out_dim: int):
        layers = []
        prev = n_in
        for width in self.config.hidden_layers:
            layers += [torch.nn.Linear(prev, width), torch.nn.ReLU(), torch.nn.Dropout(self.config.dropout)]
            prev = width
        layers.append(torch.nn.Linear(prev, out_dim))
        return torch.nn.Sequential(*layers)

    def _forward_all(self, X: np.ndarray):
        """평가 모드로 전체를 조각내어 통과시킨 원시 출력(텐서, 장치 위)."""
        torch = self._get_torch()
        self.model.eval()
        outs = []
        with torch.no_grad():
            for start in range(0, len(X), EVAL_CHUNK):
                chunk = torch.from_numpy(np.ascontiguousarray(X[start:start + EVAL_CHUNK])).to(self._device)
                outs.append(self.model(chunk))
        return torch.cat(outs, dim=0)

    # ---- 학습 ----
    def fit(
        self,
        prepared: PreparedData,
        on_epoch: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> TrainResult:
        cfg = self.config
        if prepared.task != cfg.task:
            raise ValueError("설정과 데이터의 문제 유형이 다르다")
        is_cls = cfg.task == CLASSIFICATION
        pre = prepared.preprocessor
        torch = self._get_torch()

        choice = select_device(self._requested_device, torch)  # CPU 면 여기서 스레드 2개로 제한
        self._device = torch.device(choice.device)
        self._preprocessor = pre

        torch.manual_seed(cfg.seed)
        shuffle_gen = torch.Generator().manual_seed(cfg.seed)  # CPU 생성기: 장치와 무관하게 같은 섞임

        out_dim = prepared.n_classes if is_cls else 1
        self.model = self._build(torch, prepared.n_features, out_dim).to(self._device)
        loss_fn = torch.nn.CrossEntropyLoss() if is_cls else torch.nn.MSELoss()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=cfg.learning_rate)

        X_tr = torch.from_numpy(prepared.X_train).to(self._device)
        y_tr = torch.from_numpy(prepared.y_train).to(self._device)
        X_va = torch.from_numpy(prepared.X_val).to(self._device)
        y_va = torch.from_numpy(prepared.y_val).to(self._device)
        n = X_tr.shape[0]

        def as_target_shape(out):
            return out if is_cls else out.squeeze(-1)

        result = TrainResult(device=choice)
        best_val = math.inf
        best_state = None
        stall = 0
        started = time.perf_counter()

        for epoch in range(1, cfg.max_epochs + 1):
            self.model.train()
            order = torch.randperm(n, generator=shuffle_gen).to(self._device)
            running = torch.zeros((), device=self._device)
            for start in range(0, n, cfg.batch_size):
                batch = order[start:start + cfg.batch_size]
                optimizer.zero_grad()
                loss = loss_fn(as_target_shape(self.model(X_tr[batch])), y_tr[batch])
                loss.backward()
                optimizer.step()
                running += loss.detach() * len(batch)
            train_loss = float(running) / n

            val_out = self._forward_all(prepared.X_val)
            with torch.no_grad():
                val_loss = float(loss_fn(as_target_shape(val_out), y_va))
            if not (math.isfinite(train_loss) and math.isfinite(val_loss)):
                raise MLError("E-JB-003", detail=f"에포크 {epoch}에서 손실이 유한하지 않음")

            if is_cls:
                pred = val_out.argmax(dim=1).cpu().numpy()
                val_metrics = {"accuracy": float((pred == prepared.y_val).mean())}
            else:
                pred = val_out.squeeze(-1).cpu().numpy()
                val_metrics = regression_metrics(pre.inverse_target(prepared.y_val), pre.inverse_target(pred))

            is_best = val_loss < best_val - 1e-9
            if is_best:
                best_val, stall, result.best_epoch = val_loss, 0, epoch
                best_state = {k: v.detach().clone() for k, v in self.model.state_dict().items()}
            else:
                stall += 1

            elapsed = time.perf_counter() - started
            result.epochs_run = epoch
            progress = EpochProgress(
                epoch=epoch,
                max_epochs=cfg.max_epochs,
                train_loss=train_loss,
                val_loss=val_loss,
                val_metrics=val_metrics,
                elapsed_sec=elapsed,
                eta_sec=elapsed / epoch * (cfg.max_epochs - epoch),
                best_epoch=result.best_epoch,
                is_best=is_best,
            )
            result.history.append(progress)
            if on_epoch is not None:
                on_epoch(progress)

            # 취소와 조기 종료는 에포크 경계에서만 판단한다
            if should_cancel is not None and should_cancel():
                result.stop_reason = STOP_CANCELLED
                break
            if cfg.early_stopping and stall >= cfg.patience:
                result.stop_reason = STOP_EARLY
                break
        else:
            result.stop_reason = STOP_MAX_EPOCHS

        if cfg.early_stopping and best_state is not None and result.stop_reason != STOP_CANCELLED:
            self.model.load_state_dict(best_state)  # 검증 손실이 가장 낮았던 시점으로 되돌림
        result.seconds = time.perf_counter() - started
        self.result = result
        return result

    # ---- 저장된 가중치로 추론 전용 인스턴스 만들기 (리포트 생성, SPEC 6-6 전용) ----
    @classmethod
    def load_for_inference(cls, config: TabularConfig, state_dict_path, preprocessor, torch_module=None) -> "TabularML":
        """저장된 state_dict 를 불러와 예측만 할 수 있는 인스턴스를 만든다. 재학습은 하지 않는다.

        리포트(혼동행렬·예측-실제 비교·permutation importance)를 만들 때만 쓴다. 항상 CPU 에서 돌려서
        학습 당시 장치(GPU 였을 수도 있음)와 무관하게 어떤 환경에서도 리포트를 다시 만들 수 있게 한다.
        """
        instance = cls(config, device="cpu", torch_module=torch_module)
        torch = instance._get_torch()
        out_dim = preprocessor.n_classes if config.task == CLASSIFICATION else 1
        instance.model = instance._build(torch, preprocessor.n_features, out_dim)
        state = torch.load(state_dict_path, map_location="cpu")
        instance.model.load_state_dict(state)
        instance.model.eval()
        instance._device = torch.device("cpu")
        instance._preprocessor = preprocessor
        return instance

    # ---- 예측·평가 ----
    def _require_fitted(self) -> None:
        if self.model is None:
            raise RuntimeError("fit 을 먼저 호출해야 한다")

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """분류: 클래스별 확률 (N, K)."""
        self._require_fitted()
        if self.config.task != CLASSIFICATION:
            raise ValueError("분류에서만 쓸 수 있다")
        torch = self._get_torch()
        return torch.softmax(self._forward_all(X), dim=1).cpu().numpy()

    def predict(self, X: np.ndarray) -> np.ndarray:
        """분류: 클래스 번호, 회귀: 원래 단위의 예측값."""
        self._require_fitted()
        out = self._forward_all(X)
        if self.config.task == CLASSIFICATION:
            return out.argmax(dim=1).cpu().numpy()
        return self._preprocessor.inverse_target(out.squeeze(-1).cpu().numpy())

    def evaluate(self, prepared: PreparedData, split: str) -> dict[str, float | None]:
        """split: "validation" | "test". 회귀 지표는 원래 단위."""
        self._require_fitted()
        X, y = {"validation": (prepared.X_val, prepared.y_val), "test": (prepared.X_test, prepared.y_test)}[split]
        if self.config.task == CLASSIFICATION:
            proba = self.predict_proba(X)
            return classification_metrics(y, proba.argmax(axis=1), proba)
        pre = prepared.preprocessor
        return regression_metrics(pre.inverse_target(y), self.predict(X))

    def evaluate_all(self, prepared: PreparedData) -> dict[str, dict[str, float | None]]:
        return {"validation": self.evaluate(prepared, "validation"), "test": self.evaluate(prepared, "test")}
