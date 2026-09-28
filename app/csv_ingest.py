"""CSV 읽기: 인코딩 감지 → 형식 검증 → (필요 시) 표본 추출 → 열 요약.

여기서 발생하는 모든 실패는 AppError(오류 코드)로 바뀐다. 원인 상세는 detail 로만 전달되어 로그에 남는다.
"""

from __future__ import annotations

import codecs
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import (
    CATEGORICAL_MAX_UNIQUE,
    CHUNK_BYTES,
    MAX_COLUMNS,
    MIN_ROWS,
    NUMERIC_PARSE_RATIO,
    PREVIEW_CELL_MAX_CHARS,
    PREVIEW_ROWS,
    SAMPLE_ROWS,
    SAMPLE_SEED,
    WARN_ROWS,
)
from .errors import AppError

# 감지 순서 (SPEC 6-1). CP949 는 EUC-KR 의 상위 집합이라 실제로 EUC-KR 까지 내려오는 경우는 드물다.
ENCODING_ORDER = ("utf-8-sig", "cp949", "euc-kr")
ENCODING_LABELS = {
    "utf-8": "UTF-8",
    "utf-8-sig": "UTF-8 (BOM)",
    "cp949": "CP949",
    "euc-kr": "EUC-KR",
}
TYPE_LABELS = {
    "numeric": "숫자",
    "categorical": "범주",
    "datetime": "날짜",
    "text": "텍스트",
    "empty": "비어 있음",
}

_CONTROL_BYTES = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f]")
_DATE_PATTERN = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")
_LOAD_CHUNK_ROWS = 50_000

csv.field_size_limit(1_000_000)


@dataclass
class ColumnProfile:
    name: str
    type: str
    type_label: str
    missing: int
    missing_pct: float
    unique: int


@dataclass
class IngestResult:
    encoding: str
    encoding_label: str
    total_rows: int
    used_rows: int
    sampled: bool
    n_columns: int
    preview_columns: list[str]
    preview_rows: list[list[str | None]]
    columns: list[ColumnProfile]
    warnings: list[str] = field(default_factory=list)


def detect_encoding(path: Path) -> str:
    """UTF-8(BOM 포함) → CP949 → EUC-KR 순서로 엄격 디코딩해 처음 성공한 인코딩을 돌려준다."""
    with path.open("rb") as fh:
        head = fh.read(3)
    has_bom = head == codecs.BOM_UTF8

    for enc in ENCODING_ORDER:
        decoder = codecs.getincrementaldecoder(enc)(errors="strict")
        try:
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(CHUNK_BYTES)
                    if not chunk:
                        decoder.decode(b"", final=True)
                        break
                    if _CONTROL_BYTES.search(chunk):
                        raise AppError("E-UP-003", detail="제어 문자(바이너리 추정)")
                    decoder.decode(chunk)
        except UnicodeDecodeError:
            continue
        if enc == "utf-8-sig":
            return "utf-8-sig" if has_bom else "utf-8"
        return enc
    raise AppError("E-UP-003", detail="지원하는 인코딩으로 해석 실패")


def _read_encoding(encoding: str) -> str:
    # BOM 유무와 상관없이 utf-8-sig 로 읽으면 BOM 이 자동 제거된다.
    return "utf-8-sig" if encoding == "utf-8" else encoding


def scan_structure(path: Path, encoding: str) -> tuple[list[str], int]:
    """csv 모듈의 strict 모드로 전체를 훑어 형식을 검증한다. (헤더, 데이터 행 수)를 돌려준다."""
    header: list[str] | None = None
    leading_blank = False
    n_rows = 0
    try:
        with path.open("r", encoding=_read_encoding(encoding), newline="") as fh:
            reader = csv.reader(fh, strict=True)
            for row in reader:
                if not row:  # 빈 줄은 건너뛴다
                    continue
                if header is None and len(row) == 1 and not row[0].strip():
                    # 헤더 앞의 공백뿐인 줄. 뒤에 내용이 없으면 빈 파일로, 있으면 pandas 와 행 위치가
                    # 어긋날 수 있으므로 손상으로 안내한다.
                    leading_blank = True
                    continue
                if header is None:
                    if leading_blank:
                        raise AppError("E-UP-009", detail="헤더 앞에 공백만 있는 줄")
                    header = [c.strip() for c in row]
                    if any(c == "" for c in header) or len(set(header)) != len(header):
                        raise AppError("E-UP-004", detail="헤더 비어 있음 또는 중복")
                    if len(header) > MAX_COLUMNS:
                        raise AppError("E-UP-005", detail=f"열 {len(header)}개")
                    continue
                if len(row) != len(header):
                    raise AppError("E-UP-009", detail=f"{reader.line_num}번째 줄 열 수 불일치")
                n_rows += 1
    except csv.Error as exc:
        raise AppError("E-UP-009", detail=f"csv 오류: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise AppError("E-UP-003", detail="디코딩 실패") from exc

    if header is None or n_rows == 0:
        raise AppError("E-UP-007", detail="헤더 또는 데이터 없음")
    if len(header) <= 1:
        raise AppError("E-UP-008", detail="열 1개 이하")
    if n_rows < MIN_ROWS:
        raise AppError("E-UP-006", detail=f"행 {n_rows}개")
    return header, n_rows


def pick_sample_indices(total_rows: int) -> np.ndarray | None:
    """행이 SAMPLE_ROWS 를 넘으면 시드 고정 무작위 표본의 행 위치(오름차순)를 돌려준다."""
    if total_rows <= SAMPLE_ROWS:
        return None
    rng = np.random.default_rng(SAMPLE_SEED)
    return np.sort(rng.choice(total_rows, size=SAMPLE_ROWS, replace=False))


def _clean_cell(value: object) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None
    text = str(value)
    if len(text) > PREVIEW_CELL_MAX_CHARS:
        text = text[:PREVIEW_CELL_MAX_CHARS] + "…"
    return text


def load_frames(path: Path, encoding: str, header: list[str], sample_idx: np.ndarray | None):
    """청크로 읽어 미리보기(원본 앞 20행)와 사용 행 DataFrame 을 만든다. 메모리는 사용 행 수로 제한된다."""
    try:
        reader = pd.read_csv(
            path,
            encoding=_read_encoding(encoding),
            dtype=str,
            header=0,
            names=header,
            chunksize=_LOAD_CHUNK_ROWS,
        )
        preview: pd.DataFrame | None = None
        parts: list[pd.DataFrame] = []
        offset = 0
        for chunk in reader:
            if preview is None:
                preview = chunk.head(PREVIEW_ROWS).copy()
            if sample_idx is None:
                parts.append(chunk)
            else:
                lo = np.searchsorted(sample_idx, offset, side="left")
                hi = np.searchsorted(sample_idx, offset + len(chunk), side="left")
                picked = sample_idx[lo:hi] - offset
                if len(picked):
                    parts.append(chunk.iloc[picked])
            offset += len(chunk)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError, ValueError) as exc:
        raise AppError("E-UP-009", detail=f"pandas 오류: {type(exc).__name__}: {exc}") from exc

    if preview is None or not parts:
        raise AppError("E-UP-007", detail="읽은 데이터 없음")
    df = pd.concat(parts, ignore_index=True)
    return preview, df


def infer_type(series: pd.Series) -> str:
    """SPEC 6-2 타입 감지 규칙. series 는 문자열이고 결측은 NA 로 정리된 상태여야 한다."""
    values = series.dropna()
    if values.empty:
        return "empty"
    n = len(values)

    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() / n >= NUMERIC_PARSE_RATIO:
        return "numeric"

    if values.str.match(_DATE_PATTERN).sum() / n >= NUMERIC_PARSE_RATIO:
        parsed = pd.to_datetime(values, errors="coerce", format="mixed")
        if parsed.notna().sum() / n >= NUMERIC_PARSE_RATIO:
            return "datetime"

    if values.nunique() <= CATEGORICAL_MAX_UNIQUE:
        return "categorical"
    return "text"


def profile_columns(df: pd.DataFrame) -> list[ColumnProfile]:
    profiles: list[ColumnProfile] = []
    total = len(df)
    for name in df.columns:
        col = df[name].astype("string").str.strip()
        col = col.mask(col == "")  # 공백만 있는 칸도 결측으로 본다
        missing = int(col.isna().sum())
        kind = infer_type(col)
        profiles.append(
            ColumnProfile(
                name=str(name),
                type=kind,
                type_label=TYPE_LABELS[kind],
                missing=missing,
                missing_pct=round(missing * 100 / total, 1) if total else 0.0,
                unique=int(col.nunique()),
            )
        )
    return profiles


def ingest(path: Path) -> IngestResult:
    """저장 전 임시 파일을 검증·요약한다. 실패하면 AppError 를 던진다."""
    if path.stat().st_size == 0:
        raise AppError("E-UP-007", detail="0바이트 파일")

    encoding = detect_encoding(path)
    header, total_rows = scan_structure(path, encoding)
    sample_idx = pick_sample_indices(total_rows)
    preview_df, df = load_frames(path, encoding, header, sample_idx)

    warnings: list[str] = []
    if total_rows < WARN_ROWS:
        warnings.append(f"데이터 행이 {total_rows}개로 적어 학습 결과가 불안정할 수 있습니다.")

    preview_rows = [[_clean_cell(v) for v in row] for row in preview_df.itertuples(index=False, name=None)]
    return IngestResult(
        encoding=encoding,
        encoding_label=ENCODING_LABELS[encoding],
        total_rows=total_rows,
        used_rows=len(df),
        sampled=sample_idx is not None,
        n_columns=len(header),
        preview_columns=list(header),
        preview_rows=preview_rows,
        columns=profile_columns(df),
        warnings=warnings,
    )
