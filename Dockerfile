# CSV 딥러닝 웹앱 - Docker 실행 골격 (멀티스테이지)
#
# 빌드 타깃
#   cpu-runtime : 기본. PyTorch CPU 인덱스 사용
#   gpu-runtime : ARG TORCH_INDEX_URL 로 CUDA wheel 인덱스 선택 (기본 cu128)
#   test        : cpu-runtime + pytest. 컨테이너 안에서 테스트를 실행할 때만 사용
#
# 직접 빌드 예시 (일반적으로는 compose 로 실행)
#   docker build --target cpu-runtime -t csv-dl-app:cpu .
#   docker build --target gpu-runtime -t csv-dl-app:gpu \
#       --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126 .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# PyTorch 3종은 서로 호환되는 하나의 릴리스 조합이며, 반드시 세 값을 함께 변경한다.
ARG TORCH_VERSION=2.9.1
ARG TORCHVISION_VERSION=0.24.1
ARG TORCHAUDIO_VERSION=2.9.1


# ---------------------------------------------------------------------------
# 1) 공통 빌더: 가상환경 + 공통 의존성 (torch 제외)
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS base-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN python -m venv /opt/venv

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt


# ---------------------------------------------------------------------------
# 2) CPU 빌더: PyTorch CPU 인덱스에서 3종을 함께 설치
# ---------------------------------------------------------------------------
FROM base-builder AS builder-cpu

ARG TORCH_VERSION
ARG TORCHVISION_VERSION
ARG TORCHAUDIO_VERSION

RUN pip install --index-url https://download.pytorch.org/whl/cpu \
        "torch==${TORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}"

# 버전 일치, 의존성 정합성, CUDA 빌드가 섞이지 않았는지 빌드 중에 검증
COPY docker/check_torch.py /tmp/check_torch.py
RUN pip check && python /tmp/check_torch.py cpu && rm /tmp/check_torch.py


# ---------------------------------------------------------------------------
# 3) GPU 빌더: TORCH_INDEX_URL 의 CUDA wheel 로 3종을 함께 설치
#    인덱스 예: cu126 / cu128 / cu130 (GPU 드라이버·GPU 세대에 맞게 선택)
# ---------------------------------------------------------------------------
FROM base-builder AS builder-gpu

ARG TORCH_VERSION
ARG TORCHVISION_VERSION
ARG TORCHAUDIO_VERSION
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128

RUN test -n "${TORCH_INDEX_URL}" \
 && pip install --index-url "${TORCH_INDEX_URL}" \
        "torch==${TORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}"

COPY docker/check_torch.py /tmp/check_torch.py
RUN pip check && python /tmp/check_torch.py gpu && rm /tmp/check_torch.py


# ---------------------------------------------------------------------------
# 4) 런타임 공통 베이스: 나눔고딕, 비루트 사용자, 환경 변수, 데이터 디렉터리
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime-base

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_DATA_DIR=/data \
    APP_EXPORT_DIR=/exports \
    MPLCONFIGDIR=/opt/mpl

RUN apt-get update \
 && apt-get install -y --no-install-recommends fontconfig fonts-nanum \
 && fc-cache -f \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
 && mkdir -p /data /exports /opt/mpl \
 && chown -R app:app /data /exports /opt/mpl

WORKDIR /app
EXPOSE 8000


# ---------------------------------------------------------------------------
# 5) cpu-runtime (기본 타깃)
# ---------------------------------------------------------------------------
FROM runtime-base AS cpu-runtime

COPY --from=builder-cpu /opt/venv /opt/venv
COPY docker/matplotlibrc /opt/mpl/matplotlibrc

# 나눔고딕 인식과 matplotlib 설정을 빌드 중에 검증하고, 글꼴 캐시를 미리 만들어 둔다.
COPY docker/check_runtime.py /tmp/check_runtime.py
RUN python /tmp/check_runtime.py \
 && rm /tmp/check_runtime.py \
 && chown -R app:app /opt/mpl

COPY --chown=app:app app/ /app/app/

USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]


# ---------------------------------------------------------------------------
# 6) test: cpu-runtime 위에 pytest 를 얹은 테스트 전용 타깃 (컨테이너 안에서 실행)
#    docker build --target test -t csv-dl-app:test .
#    docker run --rm csv-dl-app:test
# ---------------------------------------------------------------------------
FROM cpu-runtime AS test

USER root
COPY requirements.txt requirements-dev.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt \
 && pip check \
 && rm /tmp/requirements.txt /tmp/requirements-dev.txt

COPY --chown=app:app pytest.ini /app/pytest.ini
COPY --chown=app:app tests/ /app/tests/

USER app
CMD ["pytest", "-p", "no:cacheprovider"]


# ---------------------------------------------------------------------------
# 7) gpu-runtime
# ---------------------------------------------------------------------------
FROM runtime-base AS gpu-runtime

ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

COPY --from=builder-gpu /opt/venv /opt/venv
COPY docker/matplotlibrc /opt/mpl/matplotlibrc

COPY docker/check_runtime.py /tmp/check_runtime.py
RUN python /tmp/check_runtime.py \
 && rm /tmp/check_runtime.py \
 && chown -R app:app /opt/mpl

COPY --chown=app:app app/ /app/app/

USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
