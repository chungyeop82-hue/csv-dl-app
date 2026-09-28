# 초기화 빠른 점검 (SETUP_CHECK)

프로젝트를 Windows 11 + Docker Desktop(WSL 2)에서 시작하기 전에 **명령어 4개**로 실행 가능 여부를 확인하는 문서입니다. 전체 8항목 점검은 [ENV_CHECK.md](./ENV_CHECK.md)를 봅니다.

## 사용 규칙

- 모든 명령은 **PowerShell**에서 실행합니다. Docker Desktop을 설치·업데이트한 직후에는 **새 PowerShell 창**을 엽니다.
- `docker` 명령은 **Docker Desktop 앱이 실행 중**(왼쪽 아래 "Engine running")이어야 동작합니다.
- 1~3번이 통과하기 전에는 앱 코드를 만들지 않습니다. 실패하면 각 항목의 "해결 순서"를 위에서부터 따릅니다.
- 4번(GPU)은 **선택 검사**입니다. 실패해도 CPU 모드(기본)로 진행합니다.

## 점검 요약

| # | 명령 | 필수 여부 | 통과 기준 |
|---|------|-----------|-----------|
| 1 | `docker --version` | 필수 | 버전 문자열 출력 |
| 2 | `docker compose version` | 필수 | 버전 문자열 출력 (Compose v2) |
| 3 | `wsl --status` | 필수 | 기본 버전이 2 |
| 4 | `nvidia-smi` | **선택** | GPU 이름·드라이버 표 출력 → GPU 구성(`compose.gpu.yaml`) 사용 가능 |

## 1. docker --version

```powershell
docker --version
```

- 목적: Docker 클라이언트가 설치되어 PATH에 잡혀 있는지 확인.
- 기대 출력 예: `Docker version 29.8.0, build 88096ef`
- 실패 시 해결 순서:
  1. `docker` 명령을 찾을 수 없음 → PowerShell을 **닫고 새로 열기**.
  2. 그래도 없음 → Docker Desktop 설치 여부 확인, 없으면 관리자 PowerShell에서 `winget install --id Docker.DockerDesktop -e` 후 재부팅.
  3. 설치가 불가능(권한·정책) → ARCHITECTURE.md 8절의 **Docker 설치 불가 폴백(M2/M3)**.
- 참고: 이 명령은 클라이언트만 확인합니다. 엔진이 켜져 있는지는 `docker info` 또는 `docker run --rm hello-world`로 확인합니다(엔진이 꺼져 있으면 `failed to connect to the docker API` 오류).

## 2. docker compose version

```powershell
docker compose version
```

- 목적: app 컨테이너·볼륨·바인드 마운트·GPU 오버라이드를 한 명령으로 관리할 Compose가 있는지 확인. 하이픈 없는 `docker compose` 형태(v2 플러그인)를 기준으로 합니다.
- 기대 출력 예: `Docker Compose version v2.x.x`
- 실패 시 해결 순서:
  1. Docker Desktop을 최신 버전으로 업데이트 (Compose가 함께 포함됨).
  2. 업데이트 후 Docker Desktop 재시작, 새 PowerShell 창에서 재확인.
  3. 계속 실패하면 재설치.

## 3. wsl --status

```powershell
wsl --status
```

- 목적: WSL이 설치되어 있고 기본 버전이 2인지 확인 (Docker Desktop의 WSL 2 백엔드 전제).
- 기대 출력: "기본 버전: 2" 문구. 버전 상세는 `wsl --version`(예: WSL 2.7.14.0)으로 확인합니다.
- 실패 시 해결 순서:
  1. 명령이 동작하지 않음 → 관리자 PowerShell에서 `wsl --install`, **재부팅**.
  2. 기본 버전이 1로 나옴 → `wsl --set-default-version 2`.
  3. "가상화가 꺼져 있음" 오류 → BIOS 가상화 활성화 (ENV_CHECK 2-3절). 불가하면 **가상화 불가 폴백(M3)**.
  4. 커널 관련 오류 → `wsl --update` 후 `wsl --shutdown`, Docker Desktop 재시작.

## 4. nvidia-smi (선택)

```powershell
nvidia-smi
```

- 목적: NVIDIA GPU와 Windows 드라이버가 있는지 확인. 통과하면 `compose.gpu.yaml`을 쓸 수 있는지 다음 단계로 확인합니다.
- 기대 출력: GPU 이름, 드라이버 버전, 메모리 사용량 표.
- 컨테이너에서 GPU가 보이는지 추가 확인 (Docker Desktop 실행 중일 때):

```powershell
docker run --rm --gpus all ubuntu nvidia-smi
```

- 결과 해석:

| 결과 | 판정 |
|------|------|
| 두 명령 모두 GPU 표 출력 | GPU 구성 사용 가능 (M0) |
| `nvidia-smi` 명령을 찾을 수 없음 | NVIDIA GPU 없음 또는 드라이버 미설치 → **CPU 기본으로 진행 (M1)** |
| 1번은 되고 컨테이너에서 실패 | NVIDIA 드라이버를 최신(WSL 지원)으로 업데이트, Docker Desktop이 WSL 2 백엔드인지 확인 후 재시도. 계속 실패하면 CPU 기본으로 진행 |

- GPU를 쓰지 못하는 것은 문제가 아닙니다. CPU가 기본이며 기능 차이는 없습니다. 속도만 느려집니다.

## 5. 이 PC의 기록

| 검사 | 결과 | 근거 |
|------|------|------|
| `docker --version` | 통과 | Docker 29.8.0 (2026-09-19 사용자 출력) |
| Docker 엔진 동작 | 통과 | `hello-world` 정상 실행 |
| `docker compose version` | **미기록** | 아직 출력 없음. 위 2번 실행 필요 |
| `wsl --version` | 통과 | WSL 2.7.14.0, 커널 6.18.33.2-2 (참고: `wsl --status`는 아직 미기록) |
| `wsl --status` | **미기록** | 위 3번 실행 필요 |
| `nvidia-smi` (선택) | **미기록** | 위 4번 실행 필요 |
| 호스트 디스크 여유 | 통과 | 사용자 폴더가 있는 드라이브 여유 약 57GB (세션 중 측정) |
| 호스트 RAM | **미기록** | ENV_CHECK 2-4절 명령으로 확인 필요 |

미기록 항목이 채워지면 아래 판정표로 실행 모드를 정합니다.

| 결과 | 실행 모드 |
|------|-----------|
| 1~3 통과 + 4 통과 | 표준 (CPU 기본 + `compose.gpu.yaml` 사용 가능) |
| 1~3 통과 + 4 실패/생략 | CPU 기본 |
| 1~3 중 하나라도 해결 불가 | 폴백 (ARCHITECTURE.md 8절) — 이 경우 Docker 기반 초기화는 보류 |

## 6. Docker 실행 골격 검증

`Dockerfile`, `compose.yaml`, `compose.gpu.yaml`이 실제로 빌드되고 동작하는지 **사용자 PC의 PowerShell**에서 확인하는 절차입니다. 프로젝트 폴더(`F:\CLAUDE_PROJ1`)에서 실행하고, Docker Desktop은 "Engine running" 상태여야 합니다. 첫 빌드는 PyTorch를 내려받으므로 수 분이 걸립니다(디스크 사용량 추정은 ENV_CHECK 2-5절).

### 6-1. CPU 모드 (기본, 필수)

```powershell
cd F:\CLAUDE_PROJ1
docker compose up -d --build
docker compose ps
curl.exe -i http://127.0.0.1:8080/health
docker compose exec app python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
docker compose exec app python -c "from matplotlib import font_manager as f; print(f.findfont('NanumGothic', fallback_to_default=False))"
docker compose down
```

| 단계 | 기대 결과 |
|------|-----------|
| `up -d --build` | 빌드 성공. 빌드 중 `check_torch.py`, `check_runtime.py`가 통과해야 함(실패하면 원인 메시지와 함께 중단) |
| `ps` | `app` 서비스 1개, 포트 `127.0.0.1:8080->8000`, 잠시 후(약 20초 이내) 상태 `healthy` |
| `curl.exe -i` | `HTTP/1.1 200 OK`, 본문 `{"status":"ok"}` |
| torch 확인 | `2.9.1+cpu None False` (`torch.cuda.is_available()`가 **False**) |
| 글꼴 확인 | 경로에 `NanumGothic`이 포함됨 |

`docker compose down`은 컨테이너만 지우고 볼륨 `appdata`는 유지합니다. 볼륨까지 지우려면 `-v`를 붙이되 데이터가 삭제되므로 주의합니다.

### 6-2. GPU 모드 (선택)

CPU 모드를 먼저 `down` 한 뒤 실행합니다(같은 포트·서비스를 사용).

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
docker compose -f compose.yaml -f compose.gpu.yaml exec app python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
docker compose -f compose.yaml -f compose.gpu.yaml down
```

- 기대 결과: `2.9.1+cu128 12.8 True` 형태(`torch.cuda.is_available()`가 **True**).
- `False`이면 SETUP_CHECK 4번(`nvidia-smi`)과 컨테이너 GPU 확인을 먼저 점검합니다.
- GPU 세대·드라이버에 맞는 CUDA 인덱스로 바꾸려면 빌드 전에 지정합니다.

```powershell
$env:TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu126"
```

### 6-3. 이 골격의 검증 상태 (2026-09-19)

| 항목 | 상태 | 비고 |
|------|------|------|
| Python 문법 (`app/`, `docker/*.py`) | 통과 | 문법 컴파일 |
| `docker/matplotlibrc` | 통과 | 백엔드 Agg, 폰트 순서 NanumGothic → DejaVu Sans, 마이너스 기호 설정 로드 확인 |
| `compose.yaml` / `compose.gpu.yaml` | 통과 | `docker compose config`로 CPU·GPU 병합 결과 확인: 서비스 `app` 1개, 포트 `127.0.0.1:8080:8000`, `appdata:/data`, `./exports:/exports`, 헬스체크, `restart: unless-stopped`, GPU 예약(driver nvidia, count 1, capabilities gpu), `APP_DEVICE` cpu/cuda |
| `Dockerfile` 구조 | 통과 | 단계 6개, `cpu-runtime`·`gpu-runtime` 타깃, `COPY --from` 순서, ARG 재선언, PyTorch 3종 동시 설치, 비루트 사용자, 워커 1개 |
| 실제 이미지 빌드 | **미실행** | 작업 환경에서 PyPI·PyTorch 인덱스·Docker Hub 접속이 조직 정책으로 차단되어 실행하지 못함 |
| `/health` 200 | **미실행** | 위와 같은 사유. 6-1절을 PC에서 실행해 확인 |
| CPU 모드 `torch.cuda.is_available()` False | **미실행** | 위와 같은 사유. 6-1절에서 확인 |
| GPU 모드 | **미실행** | 6-2절에서 확인 (GPU 없으면 생략) |
| 고정 버전의 실재 여부 | **일부 미확인** | torch 2.9.1 / torchvision 0.24.1 / torchaudio 2.9.1과 CUDA 인덱스 cu128은 인덱스를 조회하지 못해 알고 있는 정보에 근거함. 웹 계열(fastapi 0.141.1, starlette 1.0.0, pydantic 2.13.3, uvicorn 0.46.0, pandas 3.0.2, numpy 2.4.4, python-multipart 0.0.26)은 작업 환경에서 같은 버전으로 pytest를 실행해 동작을 확인했으나, PyPI 인덱스 조회는 하지 못함. `pip`가 버전을 찾지 못하면 Dockerfile의 ARG와 `requirements.txt`를 조정 |

빌드나 실행이 실패하면 오류 메시지를 그대로 전달해 주세요.

## 7. 컨테이너 안에서 pytest 실행

CSV 업로드 기능의 테스트(정상·CP949·손상·대용량 등)는 `Dockerfile`의 `test` 타깃(= `cpu-runtime` + pytest)에서 **컨테이너 안에서** 실행합니다. 프로젝트 폴더(`F:\CLAUDE_PROJ1`)의 PowerShell에서 실행하고, Docker Desktop은 "Engine running" 상태여야 합니다.

```powershell
cd F:\CLAUDE_PROJ1
docker build --target test -t csv-dl-app:test .
docker run --rm csv-dl-app:test
```

| 항목 | 기대 결과 |
|------|-----------|
| 빌드 | 성공 (`cpu-runtime` 캐시가 있으면 pytest 설치만 추가됨) |
| 실행 | 마지막 줄에 `162 passed` 형태(개수는 테스트 추가에 따라 변함), **건너뜀(skipped) 0건**, 종료 코드 0. 컨테이너에는 torch가 있으므로 실제 torch로 도는 테스트가 건너뛰어지지 않아야 함 |
| 소요 시간 | 대용량(50MB) 테스트 포함 약 10~60초 |

- 테스트는 임시 폴더를 쓰므로 `appdata` 볼륨과 실제 데이터에 영향을 주지 않습니다.
- 앱을 실제로 띄워 화면을 확인하려면 6-1절의 `docker compose up -d --build` 후 브라우저에서 `http://127.0.0.1:8080`을 엽니다.

### 7-1. 검증 상태 (2026-09-19)

| 항목 | 상태 | 비고 |
|------|------|------|
| pytest 업로드·목록·삭제·안전 (작업 환경, 같은 버전 고정 세트) | 통과 | 73건 |
| pytest 전처리·분할·베이스라인·장치 선택 (작업 환경) | 통과 | 중앙값·최빈값·원-핫·표준화, 누수 방지, 층화 분할, joblib 저장·복원, 로지스틱/선형·랜덤 포레스트, `APP_DEVICE`·스레드 2개 규칙(torch 대역) |
| pytest TabularML 진행률·취소·조기 종료·발산·재현성 (작업 환경) | 통과 | **numpy로 만든 torch 대역**으로 실행. 제어 흐름 검증이며 torch API 자체를 검증한 것은 아님 |
| **실제 torch로 TabularML 학습** | **미실행** | 작업 환경에 torch가 없고 PyPI·PyTorch 인덱스에 접속할 수 없음. 같은 테스트 12건이 컨테이너에서 실제 torch로 실행됨(위 명령) |
| 360px·1024px 브라우저 확인 (Chromium) | 통과 | 업로드 화면 (이번 변경은 화면 없음) |
| **컨테이너 안 pytest** | **미실행** | 작업 환경에서 Docker 엔진과 Docker Hub·PyPI에 접속할 수 없어 실행하지 못함. 위 명령을 PC에서 실행해 확인 |
