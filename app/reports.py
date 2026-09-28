"""리포트 생성과 제출 ZIP (SPEC 6-6, FR-38~40·FR-52).

- report.pdf/HTML 은 학습이 끝난 뒤 언제든 다시 만들 수 있어야 하므로, 진행 중 예측을 따로 저장해
  두지 않고 완료된 잡의 정보(모델 가중치 + config_json + 저장된 CSV)만으로 매번 처음부터 다시
  만든다. 데이터 재적재(csv_ingest)·분할(prepare_dataset)·베이스라인(run_baselines) 모두 고정된
  시드로만 무작위성을 쓰므로, 같은 입력이면 학습 때와 똑같은 학습/검증/테스트 분할이 재현된다.
- 모델을 다시 불러와 예측하려면 torch 가 필요하다. requirements.txt 맨 위 주석의 원칙("웹 프로세스
  에서는 torch 를 가져오지 않는다")을 지키기 위해, torch 가 필요한 부분(모델 재구성·추론·
  permutation importance)과 matplotlib/PDF 렌더링까지 전부 자식 프로세스(jobs_worker.py 의
  디스패처와 같은 spawn 컨텍스트)에서 만든다. 웹 프로세스는 그 결과(순수 텍스트·숫자·PNG bytes)를
  받아 HTML 조립과 zip 파일 쓰기만 한다(ARCHITECTURE 5장과 같은 원칙).
- HTML 과 PDF 는 따로 조립하지만(HTML 은 표준 라이브러리 f-string+html.escape 만 쓴다 - 새 의존성
  없음, weasyprint 류의 무거운 OS 의존성도 추가하지 않는다) 같은 matplotlib Figure 객체를 그대로
  재사용해 PNG(HTML용)와 PDF 페이지(그대로 삽입)를 함께 만든다 - 두 형식의 그래프가 어긋날 일이 없다.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import multiprocessing as mp
import platform
import re
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import csv_ingest, db
from .config import (
    EXPORT_NAME_MAX_CHARS,
    PERMUTATION_REPEATS,
    PERMUTATION_TOP_N,
    STUDENT_ID_MAX_CHARS,
    STUDENT_ID_SETTING_KEY,
    Settings,
)
from .errors import AppError
from .ml.common import CLASSIFICATION
from .ml.metrics import regression_metrics
from .ml.preprocess import prepare_dataset
from .ml.tabular import STOP_REASON_LABELS, TabularConfig, TabularML

log = logging.getLogger("app")
router = APIRouter()

MP_CONTEXT = mp.get_context("spawn")  # jobs_worker.py 와 같은 이유(CUDA 재초기화 문제 회피)로 spawn.

# 색각이상 친화적 팔레트(범주형, CVD-safe 순서 검증됨). 원본 값·검증 방법은 dataviz 스킬
# references/palette.md 참고. 혼동행렬처럼 "크기"를 나타낼 때는 matplotlib 내장 단일 색상
# 순차 컬러맵("Blues")을 쓴다 - 역시 색각이상에 안전하고 무지개(jet류) 컬러맵을 피한다.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE, ORANGE, AQUA = PALETTE[0], PALETTE[1], PALETTE[2]

_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_export_name(raw: str, max_chars: int = EXPORT_NAME_MAX_CHARS) -> str:
    """ZIP 파일명 조각 하나를 안전하게 만든다: Windows 금지 문자만 지우고 형식은 검증하지 않는다(FR-52)."""
    name = _FILENAME_UNSAFE.sub("", (raw or "").strip())
    name = name.strip(" .")  # Windows 는 파일명 끝의 점·공백도 허용하지 않는다
    return name[:max_chars] or "무제"


def kst_today_str() -> str:
    """한국 표준시 기준 YYYYMMDD. KST 는 서머타임이 없는 고정 UTC+9 라 zoneinfo/tzdata 없이도 정확하다."""
    return (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 규칙 기반 한국어 요약 (LLM 을 쓰지 않는다 - 항상 같은 입력에 같은 문장)
# ---------------------------------------------------------------------------
def build_korean_summary(
    task: str,
    test_metrics: dict[str, Any],
    baselines: list[dict[str, Any]],
    stop_reason: str | None,
    epochs_run: int,
    max_epochs: int,
) -> list[str]:
    """정확히 3문장을 돌려준다: (1) 핵심 성능 (2) 베이스라인 대비 (3) 종료 사유."""
    if task == CLASSIFICATION:
        metric_key, metric_label, higher_better = "accuracy", "정확도", True
        acc, f1 = test_metrics.get("accuracy"), test_metrics.get("macro_f1")
        if acc is not None and f1 is not None:
            s1 = f"이 분류 모델은 테스트 세트에서 정확도 {acc:.1%}, 매크로 F1 {f1:.3f}을 기록했습니다."
        else:
            s1 = "이 분류 모델의 테스트 지표를 계산했습니다."
    else:
        metric_key, metric_label, higher_better = "r2", "R²", True
        r2, rmse = test_metrics.get("r2"), test_metrics.get("rmse")
        if r2 is not None and rmse is not None:
            s1 = f"이 회귀 모델은 테스트 세트에서 R² {r2:.3f}, RMSE {rmse:.3g}를 기록했습니다."
        else:
            s1 = "이 회귀 모델의 테스트 지표를 계산했습니다."

    own = test_metrics.get(metric_key)
    comparisons = []
    for b in baselines:
        bval = (b.get("test") or {}).get(metric_key)
        if own is None or bval is None:
            continue
        better = (own > bval) if higher_better else (own < bval)
        comparisons.append((b.get("label", "베이스라인"), better))
    if not baselines:
        s2 = "베이스라인 비교 결과가 없습니다."
    elif not comparisons:
        s2 = "베이스라인과 비교할 지표가 충분하지 않습니다."
    else:
        better_than = [name for name, better in comparisons if better]
        worse_than = [name for name, better in comparisons if not better]
        if better_than and not worse_than:
            s2 = f"이는 베이스라인({', '.join(better_than)})보다 {metric_label}가 높습니다."
        elif worse_than and not better_than:
            s2 = f"다만 베이스라인({', '.join(worse_than)})보다 {metric_label}가 낮아 개선 여지가 있습니다."
        else:
            s2 = f"베이스라인 중 {', '.join(better_than)}보다는 {metric_label}가 높고, {', '.join(worse_than)}보다는 낮습니다."

    label = STOP_REASON_LABELS.get(stop_reason, "학습이 종료되었습니다.")
    s3 = f"학습은 총 {epochs_run}/{max_epochs} 에포크를 진행했고, {label}"
    return [s1, s2, s3]


# ---------------------------------------------------------------------------
# 차트 (matplotlib). Figure 객체를 그대로 돌려줘 PNG(HTML)와 PDF 페이지 양쪽에 재사용한다.
# ---------------------------------------------------------------------------
def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    buf.seek(0)
    return buf.read()


def _fig_learning_curve(history: list[dict]):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 3.5))
    if history:
        epochs = [h["epoch"] for h in history]
        ax.plot(epochs, [h["train_loss"] for h in history], color=BLUE, linewidth=2, label="학습 손실")
        ax.plot(epochs, [h["val_loss"] for h in history], color=ORANGE, linewidth=2, label="검증 손실")
        best_epochs = [h["epoch"] for h in history if h.get("is_best")]
        if best_epochs:
            ax.axvline(best_epochs[-1], color=AQUA, linestyle="--", linewidth=1, label="최적 에포크")
    else:
        ax.text(0.5, 0.5, "학습 곡선 기록이 없습니다.", ha="center", va="center", transform=ax.transAxes)
    ax.set_xlabel("에포크")
    ax.set_ylabel("손실")
    ax.set_title("학습 곡선")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def _fig_confusion_matrix(cm: np.ndarray, labels: list[str]):
    import matplotlib.pyplot as plt

    n = max(len(labels), 1)
    fig, ax = plt.subplots(figsize=(4.5 + 0.35 * n, 4 + 0.35 * n))
    im = ax.imshow(cm, cmap="Blues")  # 단일 색상 순차 컬러맵 - 색각이상에 안전(무지개 컬러맵 회피)
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_yticklabels(labels)
    ax.set_xlabel("예측")
    ax.set_ylabel("실제")
    ax.set_title("혼동행렬 (테스트)")
    vmax = cm.max() if cm.size else 1
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            color = "white" if cm[i, j] > vmax / 2 else "black"
            ax.text(j, i, str(int(cm[i, j])), ha="center", va="center", color=color, fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    return fig


def _fig_actual_vs_predicted(y_true: np.ndarray, y_pred: np.ndarray):
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 4))
    ax1.scatter(y_true, y_pred, s=14, alpha=0.6, color=BLUE, edgecolors="none")
    lo = float(min(np.min(y_true), np.min(y_pred)))
    hi = float(max(np.max(y_true), np.max(y_pred)))
    ax1.plot([lo, hi], [lo, hi], color=ORANGE, linewidth=1.5, linestyle="--", label="y = x")
    ax1.set_xlabel("실제값")
    ax1.set_ylabel("예측값")
    ax1.set_title("예측-실제 비교 (테스트)")
    ax1.legend(frameon=False)

    residual = np.asarray(y_true) - np.asarray(y_pred)
    ax2.scatter(y_pred, residual, s=14, alpha=0.6, color=BLUE, edgecolors="none")
    ax2.axhline(0, color=ORANGE, linewidth=1.5, linestyle="--")
    ax2.set_xlabel("예측값")
    ax2.set_ylabel("잔차 (실제-예측)")
    ax2.set_title("잔차 (테스트)")
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def _fig_baseline_comparison(task: str, scores: dict, baselines: list[dict]):
    import matplotlib.pyplot as plt

    metric_key, metric_label = ("accuracy", "정확도") if task == CLASSIFICATION else ("r2", "R²")
    names = ["신경망(MLP)"] + [b.get("label", b.get("kind", "베이스라인")) for b in baselines]
    values = [scores.get("test", {}).get(metric_key, 0.0) or 0.0]
    values += [(b.get("test") or {}).get(metric_key, 0.0) or 0.0 for b in baselines]
    colors = PALETTE[: len(names)]

    fig, ax = plt.subplots(figsize=(5.5, 3.5))
    bars = ax.bar(names, values, color=colors)
    ax.set_ylabel(f"테스트 {metric_label}")
    ax.set_title("딥러닝 모델 vs 베이스라인 (테스트)")
    ax.spines[["top", "right"]].set_visible(False)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.3f}", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    return fig


def _fig_permutation_importance(importance: list[dict]):
    import matplotlib.pyplot as plt

    n = max(len(importance), 1)
    fig, ax = plt.subplots(figsize=(6, 0.4 * n + 1.2))
    names = [d["feature"] for d in importance][::-1]
    values = [d["importance"] for d in importance][::-1]
    if names:
        ax.barh(names, values, color=BLUE)
    ax.set_xlabel(f"중요도 (특성을 섞었을 때 성능 하락, {PERMUTATION_REPEATS}회 평균)")
    ax.set_title(f"Permutation Importance 상위 {len(importance)}개")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# permutation importance (직접 구현): sklearn.inspection.permutation_importance 는
# scikit-learn 추정기(.fit/.score)를 기대하므로, torch 모델에는 같은 원리를 직접 적용한다.
# ---------------------------------------------------------------------------
def _permutation_importance(
    score_fn, X_test: np.ndarray, feature_names: list[str], baseline_score: float, seed: int
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    results = []
    for j, name in enumerate(feature_names):
        drops = []
        for _ in range(PERMUTATION_REPEATS):
            shuffled = X_test.copy()
            rng.shuffle(shuffled[:, j])
            drops.append(baseline_score - score_fn(shuffled))
        results.append({"feature": name, "importance": float(np.mean(drops))})
    results.sort(key=lambda d: d["importance"], reverse=True)
    return results[:PERMUTATION_TOP_N]


def _sample_pairs(y_true, y_pred, max_points: int = 1000, seed: int = 0) -> dict[str, list[float]]:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    n = len(y_true)
    if n > max_points:
        idx = np.sort(np.random.default_rng(seed).choice(n, size=max_points, replace=False))
    else:
        idx = np.arange(n)
    return {"actual": [float(v) for v in y_true[idx]], "predicted": [float(v) for v in y_pred[idx]]}


def _environment_text(device_used: str | None) -> str:
    """torch 를 가져올 수 있는 자식 프로세스 안에서만 호출한다."""
    import torch  # noqa: PLC0415

    lines = [
        f"Python 버전: {platform.python_version()}",
        f"PyTorch 버전: {torch.__version__}",
        f"CUDA 사용 가능 여부: {'예' if torch.cuda.is_available() else '아니오'}",
    ]
    gpu_name = "없음"
    if torch.cuda.is_available():
        try:
            gpu_name = torch.cuda.get_device_name(0)
        except Exception:  # noqa: BLE001 - 이름을 못 읽어도 나머지 정보는 남긴다
            gpu_name = "확인할 수 없음"
    lines.append(f"GPU 장치명: {gpu_name}")
    lines.append(f"실제 학습 실행 장치: {'GPU' if device_used == 'cuda' else 'CPU'}")
    try:
        import matplotlib
        import numpy
        import pandas
        import sklearn

        lines.append(
            "주요 라이브러리: "
            f"numpy={numpy.__version__}, pandas={pandas.__version__}, "
            f"scikit-learn={sklearn.__version__}, matplotlib={matplotlib.__version__}, torch={torch.__version__}"
        )
    except Exception:  # noqa: BLE001 - 버전 표시는 부가 정보일 뿐이다
        pass
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 무거운 부분(torch 필요): 자식 프로세스에서만 실행한다.
# ---------------------------------------------------------------------------
def build_report_payload(job: dict, dataset: dict, uploads_dir: str, models_dir: str) -> dict[str, Any]:
    """job(완료 상태)·dataset 정보만으로 리포트에 필요한 모든 것을 처음부터 다시 만든다.

    반환값은 전부 그림(PNG bytes)과 JSON 가능한 값뿐이라 부모 프로세스로 안전하게 피클된다.
    실패하면 {"ok": False, "error": ..., "traceback": ...} 를 돌려준다(예외를 그대로 올리지 않음 -
    ProcessPoolExecutor 를 넘어가는 예외는 원래 클래스가 보존되지 않을 수 있어 문자열로 남긴다).
    """
    try:
        return _build_report_payload(job, dataset, uploads_dir, models_dir)
    except Exception as exc:  # noqa: BLE001 - 자식 프로세스 예외는 문자열로만 부모에 전달한다
        import traceback

        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}


def _build_report_payload(job: dict, dataset: dict, uploads_dir: str, models_dir: str) -> dict[str, Any]:
    del models_dir  # 모델 경로는 job["model_path"](학습 시 기록된 그대로)를 직접 쓴다
    task = job["task"]
    config = job["config_json"]
    tabular_config = TabularConfig(**config["tabular_config"])

    dataset_path = Path(uploads_dir) / dataset["stored_name"]
    header, total_rows = csv_ingest.scan_structure(dataset_path, dataset["encoding"])
    sample_idx = csv_ingest.pick_sample_indices(total_rows)
    _, df = csv_ingest.load_frames(dataset_path, dataset["encoding"], header, sample_idx)

    prepared = prepare_dataset(
        df,
        target=job["target"],
        task=task,
        numeric_cols=config["numeric_cols"],
        categorical_cols=config["categorical_cols"],
        seed=tabular_config.seed,
    )
    pre = prepared.preprocessor

    model_path = job.get("model_path")
    if not model_path or not Path(model_path).exists():
        raise FileNotFoundError("저장된 모델 파일을 찾을 수 없습니다")
    model = TabularML.load_for_inference(tabular_config, Path(model_path), pre)

    scores = model.evaluate_all(prepared)  # {"validation": {...}, "test": {...}} - 학습 때와 같은 계산
    is_cls = task == CLASSIFICATION
    extra: dict[str, Any] = {}
    figs = {}

    if is_cls:
        from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

        proba_test = model.predict_proba(prepared.X_test)
        pred_test = proba_test.argmax(axis=1)
        y_test = prepared.y_test
        labels = pre.classes
        cm = confusion_matrix(y_test, pred_test, labels=list(range(len(labels))))
        # SPEC 6-5 는 정확도/매크로 F1/혼동행렬/(이진)ROC-AUC 만 정의하지만, PPT STEP 7 요구사항이
        # Precision/Recall 도 명시하므로 여기(리포트 전용)에서만 추가로 계산한다 - 학습 파이프라인의
        # app/ml/metrics.py 계약(및 그 테스트)은 그대로 둔다.
        for split_name, X_split, y_split in (
            ("validation", prepared.X_val, prepared.y_val),
            ("test", prepared.X_test, prepared.y_test),
        ):
            pred_split = model.predict_proba(X_split).argmax(axis=1) if split_name == "validation" else pred_test
            precision, recall, _f1, _support = precision_recall_fscore_support(
                y_split, pred_split, average="macro", zero_division=0
            )
            scores[split_name]["precision_macro"] = float(precision)
            scores[split_name]["recall_macro"] = float(recall)
        extra["confusion_matrix"] = {"labels": labels, "matrix": cm.tolist()}
        figs["primary"] = _fig_confusion_matrix(cm, labels)

        def score_fn(X):
            return float((model.predict_proba(X).argmax(axis=1) == y_test).mean())

        baseline_score = float((pred_test == y_test).mean())
    else:
        pred_test = model.predict(prepared.X_test)  # 원래 단위
        y_test_orig = pre.inverse_target(prepared.y_test)
        extra["actual_vs_predicted_sample"] = _sample_pairs(y_test_orig, pred_test)
        figs["primary"] = _fig_actual_vs_predicted(y_test_orig, pred_test)

        def score_fn(X):
            return regression_metrics(y_test_orig, model.predict(X))["r2"]

        baseline_score = regression_metrics(y_test_orig, pred_test)["r2"]

    importance = _permutation_importance(
        score_fn, prepared.X_test, pre.feature_names, baseline_score, tabular_config.seed
    )

    history = job["metrics"].get("history", []) if job.get("metrics") else []
    baselines = job["metrics"].get("baselines", []) if job.get("metrics") else []
    figs["learning_curve"] = _fig_learning_curve(history)
    figs["baselines"] = _fig_baseline_comparison(task, scores, baselines)
    figs["permutation_importance"] = _fig_permutation_importance(importance)

    charts_png = {name: _fig_to_png(fig) for name, fig in figs.items()}
    korean_summary = build_korean_summary(
        task,
        scores.get("test", {}),
        baselines,
        job["metrics"].get("stop_reason") if job.get("metrics") else None,
        job["metrics"].get("epochs_run", 0) if job.get("metrics") else 0,
        tabular_config.max_epochs,
    )

    import matplotlib.backends.backend_pdf as backend_pdf  # noqa: PLC0415 - 자식 프로세스에서만 필요

    pdf_bytes = _build_pdf_bytes(job, dataset, scores, korean_summary, pre, figs, backend_pdf)

    for fig in figs.values():
        import matplotlib.pyplot as plt  # noqa: PLC0415

        plt.close(fig)

    return {
        "ok": True,
        "scores": scores,
        "extra": extra,
        "permutation_importance": importance,
        "history": history,
        "baselines": baselines,
        "n_features": pre.n_features,
        "n_classes": pre.n_classes if is_cls else None,
        "split_sizes": prepared.split_sizes,
        "preprocessing_summary": pre.describe(),
        "korean_summary": korean_summary,
        "charts": charts_png,
        "environment_text": _environment_text(job.get("device")),
        "pdf_bytes": pdf_bytes,
    }


def _build_pdf_bytes(job, dataset, scores, korean_summary, pre, figs, backend_pdf) -> bytes:
    import matplotlib.pyplot as plt

    metrics_meta = job.get("metrics") or {}
    tabular_config = job["config_json"]["tabular_config"]
    summary_lines = [
        f"데이터셋: {dataset['original_name']}",
        f"문제 유형: {'분류' if job['task'] == CLASSIFICATION else '회귀'} / 타깃: {job['target']}",
        f"실행 장치: {'GPU' if job.get('device') == 'cuda' else 'CPU'} / 시드: {tabular_config.get('seed')}",
        f"학습 종료 사유: {STOP_REASON_LABELS.get(metrics_meta.get('stop_reason'), '-')}"
        f" ({metrics_meta.get('epochs_run', '-')}/{tabular_config.get('max_epochs', '-')} 에포크)",
        "",
        "핵심 지표 (검증 / 테스트)",
    ]
    for key in sorted(set(scores.get("validation", {})) | set(scores.get("test", {}))):
        v = scores.get("validation", {}).get(key)
        t = scores.get("test", {}).get(key)
        v_s = f"{v:.4f}" if isinstance(v, (int, float)) else "-"
        t_s = f"{t:.4f}" if isinstance(t, (int, float)) else "-"
        summary_lines.append(f"  {key}: 검증 {v_s} / 테스트 {t_s}")
    summary_lines.append("")
    summary_lines.append("규칙 기반 요약")
    summary_lines.extend(f"  {s}" for s in korean_summary)

    config_lines = [
        f"숫자 특성 {len(pre.numeric_cols)}개, 범주 특성 {len(pre.categorical_cols)}개 (합계 {pre.n_features}개)",
        f"모델: 은닉층 {tabular_config.get('hidden_layers')}, 드롭아웃 {tabular_config.get('dropout')}",
        f"학습률 {tabular_config.get('learning_rate')}, 배치 크기 {tabular_config.get('batch_size')},"
        f" 최대 에포크 {tabular_config.get('max_epochs')}",
        f"조기 종료: {'사용' if tabular_config.get('early_stopping') else '미사용'}"
        f" (patience {tabular_config.get('patience')})",
        "분할 비율: 학습 70% / 검증 15% / 테스트 15%",
    ]

    buf = io.BytesIO()
    with backend_pdf.PdfPages(buf) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.08, 0.96, "CSV 딥러닝 웹앱 - 학습 결과 리포트", fontsize=16, fontweight="bold")
        y = 0.90
        for line in summary_lines:
            fig.text(0.08, y, line, fontsize=10, wrap=True)
            y -= 0.026
        y -= 0.015
        fig.text(0.08, y, "설정", fontsize=12, fontweight="bold")
        y -= 0.03
        for line in config_lines:
            fig.text(0.08, y, line, fontsize=9)
            y -= 0.022
        pdf.savefig(fig)
        plt.close(fig)

        for name in ("primary", "learning_curve", "baselines", "permutation_importance"):
            if name in figs:
                pdf.savefig(figs[name])
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# HTML 리포트 (웹 프로세스에서 조립, torch 불필요)
# ---------------------------------------------------------------------------
def render_html_report(job: dict, dataset: dict, payload: dict) -> str:
    import base64
    import html as html_lib

    def esc(s: Any) -> str:
        return html_lib.escape(str(s))

    def img_tag(name: str, alt: str) -> str:
        png = payload["charts"].get(name)
        if not png:
            return ""
        b64 = base64.b64encode(png).decode("ascii")
        return f'<img class="chart" src="data:image/png;base64,{b64}" alt="{esc(alt)}">'

    scores = payload["scores"]
    tabular_config = job["config_json"]["tabular_config"]
    metrics_meta = job.get("metrics") or {}
    is_cls = job["task"] == CLASSIFICATION

    metric_rows = []
    for key in sorted(set(scores.get("validation", {})) | set(scores.get("test", {}))):
        v = scores.get("validation", {}).get(key)
        t = scores.get("test", {}).get(key)
        v_s = f"{v:.4f}" if isinstance(v, (int, float)) else "-"
        t_s = f"{t:.4f}" if isinstance(t, (int, float)) else "-"
        metric_rows.append(f"<tr><td>{esc(key)}</td><td class='num'>{v_s}</td><td class='num'>{t_s}</td></tr>")

    baseline_rows = []
    for b in payload.get("baselines", []):
        row = [f"<tr><td>{esc(b.get('label', b.get('kind')))}</td>"]
        for split in ("validation", "test"):
            parts = ", ".join(
                f"{k}={v:.4f}" if isinstance(v, (int, float)) else f"{k}=-"
                for k, v in (b.get(split) or {}).items()
            )
            row.append(f"<td>{esc(parts)}</td>")
        row.append("</tr>")
        baseline_rows.append("".join(row))

    importance_rows = "".join(
        f"<tr><td>{esc(d['feature'])}</td><td class='num'>{d['importance']:.4f}</td></tr>"
        for d in payload.get("permutation_importance", [])
    )

    confusion_html = ""
    if is_cls and payload.get("extra", {}).get("confusion_matrix"):
        confusion_html = f"<h2>혼동행렬 (테스트)</h2>{img_tag('primary', '혼동행렬')}"
    elif not is_cls:
        confusion_html = f"<h2>예측-실제 비교·잔차 (테스트)</h2>{img_tag('primary', '예측-실제 비교')}"

    summary_html = "".join(f"<p>{esc(s)}</p>" for s in payload.get("korean_summary", []))

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>학습 결과 리포트 - {esc(dataset['original_name'])}</title>
<link rel="stylesheet" href="/static/report.css">
</head>
<body>
<h1>학습 결과 리포트</h1>
<p class="meta">
  데이터셋: {esc(dataset['original_name'])} ·
  문제 유형: {esc('분류' if is_cls else '회귀')} · 타깃: {esc(job['target'])} ·
  실행 장치: {esc('GPU' if job.get('device') == 'cuda' else 'CPU')} ·
  시드: {esc(tabular_config.get('seed'))}
</p>
<p class="meta">
  학습 종료 사유: {esc(STOP_REASON_LABELS.get(metrics_meta.get('stop_reason'), '-'))}
  ({esc(metrics_meta.get('epochs_run', '-'))}/{esc(tabular_config.get('max_epochs', '-'))} 에포크,
  최적 에포크 {esc(metrics_meta.get('best_epoch', '-'))})
</p>

<h2>요약</h2>
<div class="summary">{summary_html}</div>

<h2>데이터 분할</h2>
<p>학습 {esc(payload['split_sizes'].get('train'))}행 · 검증 {esc(payload['split_sizes'].get('validation'))}행 ·
   테스트 {esc(payload['split_sizes'].get('test'))}행 · 특성 {esc(payload['n_features'])}개</p>

<h2>핵심 지표 (검증 / 테스트)</h2>
<table><thead><tr><th>지표</th><th>검증</th><th>테스트</th></tr></thead>
<tbody>{''.join(metric_rows)}</tbody></table>

<h2>학습 곡선</h2>
{img_tag('learning_curve', '학습 곡선')}

{confusion_html}

<h2>딥러닝 모델 vs 베이스라인</h2>
{img_tag('baselines', '베이스라인 비교')}
<table><thead><tr><th>모델</th><th>검증</th><th>테스트</th></tr></thead>
<tbody>{''.join(baseline_rows)}</tbody></table>

<h2>Permutation Importance (상위 {len(payload.get('permutation_importance', []))}개)</h2>
{img_tag('permutation_importance', 'Permutation Importance')}
<table><thead><tr><th>특성</th><th>중요도</th></tr></thead>
<tbody>{importance_rows}</tbody></table>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# metrics.json / config.json (제출 ZIP)
# ---------------------------------------------------------------------------
def build_metrics_json(job: dict, payload: dict) -> dict[str, Any]:
    metrics_meta = job.get("metrics") or {}
    return {
        "task": job["task"],
        "target": job["target"],
        "device": job.get("device"),
        "seed": job["config_json"]["tabular_config"].get("seed"),
        "stop_reason": metrics_meta.get("stop_reason"),
        "epochs_run": metrics_meta.get("epochs_run"),
        "best_epoch": metrics_meta.get("best_epoch"),
        "seconds": metrics_meta.get("seconds"),
        "validation": payload["scores"].get("validation"),
        "test": payload["scores"].get("test"),
        "confusion_matrix": payload.get("extra", {}).get("confusion_matrix"),
        "actual_vs_predicted_sample": payload.get("extra", {}).get("actual_vs_predicted_sample"),
        "baselines": payload.get("baselines"),
        "n_features": payload.get("n_features"),
        "split_sizes": payload.get("split_sizes"),
        "learning_curve": payload.get("history"),
        "permutation_importance": payload.get("permutation_importance"),
    }


def build_config_json(job: dict, dataset: dict) -> dict[str, Any]:
    config = job["config_json"]
    return {
        "dataset": {
            "original_name": dataset["original_name"],
            "encoding": dataset["encoding"],
            "total_rows": dataset["total_rows"],
            "used_rows": dataset["used_rows"],
            "sampled": dataset["sampled"],
        },
        "task": job["task"],
        "target": job["target"],
        "numeric_cols": config.get("numeric_cols"),
        "categorical_cols": config.get("categorical_cols"),
        "model": config.get("tabular_config"),
        "split_ratios": {"train": 0.70, "validation": 0.15, "test": 0.15},
    }


# ---------------------------------------------------------------------------
# 리포트 생성 전용 프로세스 풀 (학습용 Dispatcher 와 별도, 훨씬 단순함 - 폴링 루프 없음)
# ---------------------------------------------------------------------------
class ReportExecutor:
    def __init__(self) -> None:
        self._executor: ProcessPoolExecutor | None = None

    def start(self) -> None:
        self._executor = ProcessPoolExecutor(max_workers=1, mp_context=MP_CONTEXT)

    def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    async def run(self, job: dict, dataset: dict, uploads_dir: str, models_dir: str) -> dict[str, Any]:
        assert self._executor is not None, "ReportExecutor.start() 를 먼저 호출해야 한다"
        loop = asyncio.get_running_loop()
        future = self._executor.submit(build_report_payload, job, dataset, uploads_dir, models_dir)
        return await asyncio.wrap_future(future, loop=loop)


# ---------------------------------------------------------------------------
# API 라우트
# ---------------------------------------------------------------------------
def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _load_job_and_dataset(job_id: str, settings: Settings) -> tuple[dict, dict]:
    import uuid

    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise AppError("E-JB-002", detail="잘못된 id 형식") from None

    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise AppError("E-JB-002", detail="없는 잡")
        if row["status"] != "completed":
            raise AppError("E-RP-001", detail=f"status={row['status']}")
        dataset_row = conn.execute("SELECT * FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone()
        if dataset_row is None:
            raise AppError("E-DS-001", detail="없는 데이터셋")

    job = {
        "id": row["id"],
        "task": row["task"],
        "target": row["target"],
        "config_json": json.loads(row["config_json"]),
        "device": row["device"],
        "metrics": json.loads(row["metrics_json"]) if row["metrics_json"] else {},
        "model_path": row["model_path"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }
    dataset = {
        "id": dataset_row["id"],
        "original_name": dataset_row["original_name"],
        "stored_name": dataset_row["stored_name"],
        "encoding": dataset_row["encoding"],
        "total_rows": dataset_row["total_rows"],
        "used_rows": dataset_row["used_rows"],
        "sampled": bool(dataset_row["sampled"]),
    }
    return job, dataset


async def _generate_payload(request: Request, job: dict, dataset: dict) -> dict[str, Any]:
    settings = get_settings(request)
    payload = await request.app.state.report_executor.run(
        job, dataset, str(settings.uploads_dir), str(settings.models_dir)
    )
    if not payload.get("ok"):
        log.error("리포트 생성 실패 job=%s: %s", job["id"], payload.get("traceback") or payload.get("error"))
        raise AppError("E-SY-002", detail=payload.get("error", ""))
    return payload


@router.get("/jobs/{job_id}/report.html", response_class=HTMLResponse)
async def get_report_html(job_id: str, request: Request):
    settings = get_settings(request)
    job, dataset = _load_job_and_dataset(job_id, settings)
    payload = await _generate_payload(request, job, dataset)
    return HTMLResponse(render_html_report(job, dataset, payload))


class SubmitBody(BaseModel):
    student_id: str | None = None


def build_submission_zip(job: dict, dataset: dict, payload: dict, settings: Settings, student_id: str) -> Path:
    metrics_json = build_metrics_json(job, payload)
    config_json = build_config_json(job, dataset)

    dataset_name = sanitize_export_name(Path(dataset["original_name"]).stem)
    student = sanitize_export_name(student_id, max_chars=STUDENT_ID_MAX_CHARS)
    zip_name = f"{student}_{dataset_name}_{kst_today_str()}.zip"
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    zip_path = settings.export_dir / zip_name

    tmp_path = zip_path.with_name(zip_path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("report.pdf", payload["pdf_bytes"])
            zf.writestr("metrics.json", json.dumps(metrics_json, ensure_ascii=False, indent=2))
            zf.writestr("config.json", json.dumps(config_json, ensure_ascii=False, indent=2))
            zf.writestr("environment.txt", payload["environment_text"])
        tmp_path.replace(zip_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return zip_path


@router.get("/system/student-id")
def get_student_id(request: Request):
    settings = get_settings(request)
    with db.session(settings.db_path) as conn:
        student_id = db.get_app_setting(conn, STUDENT_ID_SETTING_KEY)
    return {"student_id": student_id}


@router.post("/jobs/{job_id}/submit")
async def submit_job(job_id: str, body: SubmitBody, request: Request):
    settings = get_settings(request)
    job, dataset = _load_job_and_dataset(job_id, settings)

    with db.session(settings.db_path) as conn:
        given = (body.student_id or "").strip()
        student_id = given or db.get_app_setting(conn, STUDENT_ID_SETTING_KEY)
        if not student_id:
            raise AppError("E-RP-002")
        student_id = student_id[:STUDENT_ID_MAX_CHARS]
        if given:
            db.set_app_setting(conn, STUDENT_ID_SETTING_KEY, student_id)

    payload = await _generate_payload(request, job, dataset)
    zip_path = build_submission_zip(job, dataset, payload, settings, student_id)
    log.info("제출 ZIP 생성 job=%s -> %s", job_id, zip_path.name)
    return {"filename": zip_path.name}
