# 환경 점검 (ENV_CHECK)

- 대상: 학생용 Windows 11 PC에서 단독 실행하는 CSV 딥러닝 웹앱
- 작성일: 2026-09-19 / 상태: 착수 단계 초안
- 짝 문서: [ARCHITECTURE.md](./ARCHITECTURE.md) (판정 결과에 따른 실행 모드가 그쪽 "폴백" 절과 연결됩니다)

## 0. 사용법

1. 아래 8개 필수 항목을 위에서부터 순서대로 확인하고 [4. 결과 기록표](#4-결과-기록표)에 적습니다.
2. 실패한 항목은 해당 절의 "실패 시" 안내를 따릅니다. 해결이 안 되면 그대로 두고 다음 항목으로 넘어갑니다.
3. 모든 항목을 마친 뒤 [3. 실행 모드 판정](#3-실행-모드-판정)으로 **이 PC에서 쓸 실행 모드(M0~M3)** 를 정합니다.
4. 이 문서의 명령어는 점검용 한 줄 명령이며, 앱 코드가 아닙니다. 명령어는 모두 PowerShell 기준입니다.

## 1. 한눈에 보는 점검표

| # | 항목 | 필수 기준 | 미충족 시 |
|---|------|-----------|-----------|
| 1 | Windows 버전 | Windows 11 64비트 (22H2 이상 권장) | 업데이트, 불가하면 M3 |
| 2 | 관리자 권한 | 관리자로 PowerShell 실행 가능 | Docker/WSL 설치 불가 → M3 |
| 3 | BIOS 가상화 | 작업 관리자에서 "가상화: 사용" | BIOS에서 켜기, 잠겨 있으면 M3 |
| 4 | RAM | 8GB 이상 | 8GB 미만이면 M3 + 데이터 제한, 그래도 어려우면 M4 |
| 5 | 디스크 | C: 여유 15GB 이상 (GPU 이미지는 20GB 권장) | 정리 또는 다른 드라이브로 이동 |
| 6 | WSL 2 | `wsl --version` 정상, 배포판 VERSION 2 | 설치/업데이트, 불가하면 M3 |
| 7 | Docker Desktop | `docker run hello-world` 성공 | 설치 불가 → M3 |
| 8 | NVIDIA GPU | (선택) `nvidia-smi` 정상 + 컨테이너에서 GPU 인식 | 없거나 실패 → CPU 모드 (M1) |

8번(GPU)만 **선택 항목**이고 나머지 7개는 표준 구성(M0/M1)의 필수 조건입니다. CSV 딥러닝(표 형태 데이터, 소형 신경망)은 CPU만으로도 동작하는 것을 기본 전제로 설계합니다.

## 2. 항목별 점검

### 2-1. Windows 버전

- 왜: Docker Desktop의 WSL 2 백엔드와 GPU 지원이 최신 Windows 11 빌드를 기준으로 합니다.
- 확인: `winver` 실행 또는 아래 명령.

```powershell
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsBuildNumber
```

- 합격: Windows 11, 64비트, 22H2 이상. Home 에디션도 WSL 2 백엔드로 사용 가능합니다.
- 실패 시: Windows Update로 올립니다. 올릴 수 없으면 M3(Windows 네이티브 실행)으로 갑니다.

### 2-2. 관리자 권한

- 왜: WSL 기능 켜기와 Docker Desktop 설치는 관리자 권한이 필요합니다. 설치 이후 일상 실행은 일반 권한으로 충분합니다.
- 확인: 시작 메뉴에서 PowerShell을 "관리자 권한으로 실행"하고 제목 표시줄에 **"관리자:"** 가 붙는지 봅니다. 또는 아래 명령이 `True`를 출력해야 합니다.

```powershell
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
```

- 합격: 관리자 실행이 되고 UAC 승인 창에서 승인할 수 있음 (본인 소유 PC이거나 관리자 계정 보유).
- 실패 시: 학교·기관 관리 PC라 관리자 권한이 없으면 Docker/WSL 설치가 불가능합니다. 이미 설치되어 있다면 그대로 쓰고, 아니면 **M3**로 갑니다. 설치를 위해 정책을 우회하지 않습니다.

### 2-3. BIOS(UEFI) 가상화

- 왜: WSL 2와 Docker Desktop은 가상 머신 위에서 동작하므로 CPU 가상화(Intel VT-x / AMD-V, 메인보드 메뉴 이름은 SVM Mode 등)가 켜져 있어야 합니다.
- 확인: 작업 관리자(Ctrl+Shift+Esc) → 성능 → CPU → 오른쪽 아래 **"가상화: 사용"** 여부. 이미 Docker/WSL이 동작 중이면 켜져 있는 것으로 봅니다. 보조 확인은 아래 명령입니다.

```powershell
systeminfo | findstr /i "Hyper-V 하이퍼바이저"
```

- 합격: "가상화: 사용", 또는 "하이퍼바이저가 검색되었습니다" 문구.
- 실패 시:
  1. 재부팅 후 BIOS 진입 키(제조사마다 F2 / Del / F10 등)로 들어가 가상화 옵션을 Enabled로 바꿉니다.
  2. BIOS가 잠겨 있거나 옵션이 없거나, PC 자체가 가상 머신이면 WSL 2/Docker는 불가능합니다. **M3**로 갑니다.
- 주의: BIOS 설정을 바꾸기 전에 현재 값을 사진으로 남겨 두세요. 가상화 외의 항목은 건드리지 않습니다.

### 2-4. RAM 8GB

- 왜: Windows 자체와 Docker Desktop이 상시 메모리를 쓰고, WSL 2는 기본적으로 전체 RAM의 최대 50%까지 가져갑니다. 8GB PC에서는 학습 중 메모리 부족(OOM)이 가장 흔한 실패 원인입니다.
- 확인:

```powershell
[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
```

- 합격: 8GB 이상. 다만 8GB는 **최소선**이므로 아래 "8GB 튜닝 권고"(5절)를 함께 적용합니다.
- 실패 시(8GB 미만): Docker Desktop은 무리입니다. **M3**로 가고 CSV 크기와 모델 크기를 더 낮춰 잡습니다. 그래도 실행이 어렵다면 M4(학습만 클라우드)를 고려합니다.
- 참고: 점검 시점에 브라우저·메신저 등을 닫은 상태에서 사용 가능 메모리가 4GB 이상이면 여유가 있는 편입니다. 작업 관리자 → 성능 → 메모리에서 봅니다.

### 2-5. 디스크 15GB

- 왜: Docker Desktop 설치, WSL/Docker 가상 디스크, 앱 이미지, 업로드 CSV, 모델, 내보내기 결과가 모두 C: 드라이브를 씁니다. Docker 가상 디스크는 기본적으로 사용자 폴더의 `AppData\Local\Docker\wsl` 아래에 만들어집니다.
- 확인:

```powershell
[math]::Round((Get-PSDrive C).Free / 1GB, 1)
```

- 합격: 여유 15GB 이상. 아래는 **대략의 추정치**이며 실제 크기는 버전에 따라 달라집니다.

| 구성 요소 | CPU 전용 구성 | GPU(CUDA) 구성 |
|-----------|---------------|----------------|
| Docker Desktop + WSL 구성 요소 | 약 3~4GB | 약 3~4GB |
| Docker 기본 이미지·캐시 | 약 1~2GB | 약 1~2GB |
| 앱 이미지 (Python + PyTorch 등) | 약 2~3GB | 약 6~9GB |
| 데이터·모델·내보내기 | 약 1~2GB | 약 1~2GB |
| **합계(추정)** | **약 7~11GB** | **약 11~17GB** |

  GPU 구성은 15GB 기준선에 빠듯할 수 있어 **20GB 이상 여유를 권장**합니다. 여유가 부족하면 CPU 이미지로 시작합니다.
- 실패 시: 임시 파일 정리(디스크 정리), 큰 파일 이동, 사용하지 않는 프로그램 삭제. 또한 Windows 업데이트가 임시 공간을 쓰므로 합계보다 20% 정도 더 남겨 두면 안전합니다.

### 2-6. WSL 2

- 왜: Docker Desktop이 WSL 2 위에서 컨테이너를 실행하고, GPU 연동도 WSL 2 경로로 이루어집니다.
- 확인:

```powershell
wsl --version
wsl -l -v
```

- 합격: `wsl --version`이 정상 출력. `wsl -l -v`에 배포판이 보이면 VERSION이 2여야 합니다. Docker Desktop은 자체 내부 배포판을 쓰므로 Ubuntu가 없어도 동작할 수 있습니다.
- 실패 시: `wsl --update`를 실행하고, 관리자 PowerShell에서 `wsl --install`로 설치합니다. 기능 설치 후에는 재부팅이 필요합니다. 그래도 안 되면 **M3**.

### 2-7. Docker Desktop

- 왜: 본 프로젝트의 표준 실행 방식(M0/M1)이 Docker 컨테이너입니다.
- 확인: Docker Desktop 앱을 먼저 실행해 "Engine running"이 된 뒤, **새** PowerShell 창에서 실행합니다.

```powershell
docker --version
docker compose version
docker run --rm hello-world
```

- 합격: `hello-world`가 "Hello from Docker!"를 출력하고 종료. (`docker --version`만 되고 `docker API 연결 실패` 오류가 나면 앱이 꺼져 있는 것이므로 앱부터 실행)
- 실패 시: 설치/실행이 정책·용량·가상화 문제로 안 되면 **M3**. Docker Desktop은 개인·교육 용도는 무료로 알려져 있으나 조직 규모에 따라 유료일 수 있으므로 소속 기관 약관을 확인합니다.

### 2-8. NVIDIA GPU (선택)

- 왜: 있으면 학습이 빨라지지만 필수는 아닙니다. CSV(표 형태) 모델은 CPU로도 충분히 학습 가능하도록 설계합니다.
- 확인 1 — Windows에 GPU와 드라이버가 있는가:

```powershell
nvidia-smi
Get-CimInstance Win32_VideoController | Select-Object Name
```

- 확인 2 — 컨테이너 안에서 GPU가 보이는가 (Docker Desktop이 실행 중이어야 함):

```powershell
docker run --rm --gpus all ubuntu nvidia-smi
```

- 합격: 두 확인 모두 GPU 이름과 드라이버 버전을 출력. GPU 드라이버는 **Windows 쪽에만** 설치하며 WSL 안에는 별도 설치하지 않습니다. VRAM은 4GB 이상이면 소형 표 모델에는 충분합니다.
- 실패 시:
  - `nvidia-smi` 자체가 없음 → NVIDIA GPU가 없는 PC(Intel/AMD 내장 등). **M1(CPU 모드)** 로 진행.
  - 확인 1은 성공, 확인 2만 실패 → NVIDIA 드라이버 업데이트(WSL 지원 버전), Docker Desktop이 WSL 2 백엔드인지 확인 후 재시도. 계속 실패하면 M1.
  - 구형 GPU는 최신 PyTorch CUDA 빌드가 지원하지 않을 수 있습니다. 이 경우도 M1로 진행합니다.
  - GPU를 못 쓰는 것은 실패가 아닙니다. 기능 차이 없이 속도만 느려집니다.

### 2-9. 추가 점검 (권장)

| 항목 | 확인 | 기준·조치 |
|------|------|-----------|
| 포트 충돌 | `netstat -ano \| findstr :8080` | 아무것도 안 나오면 사용 가능. 사용 중이면 `compose.yaml`의 호스트 포트를 다른 값(예: 8081)으로 변경 |
| 프로젝트 경로 | 탐색기에서 프로젝트 폴더 경로 확인 | 한글·공백이 없는 ASCII 경로(예: `C:\work\csv-dl-app`) 권장. 사용자 폴더 이름이 한글이면 특히 그 밑에 두지 않기 |
| 인터넷 | 브라우저로 pypi.org, hub.docker.com 접속 | 이미지 빌드·의존성 설치 때만 필요. 실행 단계는 오프라인 가능하게 설계 |
| 백신/보안 SW | 작업 관리자에서 CPU 상시 점유 여부 | 대용량 CSV·모델 파일 쓰기가 느리면 프로젝트 폴더를 검사 예외로 (본인 PC에서만) |

## 3. 실행 모드 판정

점검 결과를 아래 표에 대입해 **위에서부터 처음 맞는 행**을 선택합니다. 각 모드의 상세는 [ARCHITECTURE.md의 폴백 절](./ARCHITECTURE.md#8-폴백-전략)을 봅니다.

| 조건 | 실행 모드 | 요약 |
|------|-----------|------|
| 1~7 통과 + GPU(8번) 통과 | **M0 표준** | Docker + GPU |
| 1~7 통과 + GPU 없음/실패 | **M1 CPU 모드** | Docker + CPU 이미지 (GPU 불가 폴백) |
| 관리자 권한 없음 또는 Docker 설치/실행 불가 | **M3 네이티브** | Windows 네이티브 Python 실행 (Docker 설치 불가 폴백). GPU가 있으면 네이티브에서도 사용 가능 |
| BIOS 가상화 불가 (WSL 2·Docker 모두 불가) | **M3 네이티브** | 위와 동일 (가상화 불가 폴백) |
| RAM 8GB 미만 또는 로컬 실행 자체가 불가 | **M4 최후 수단** | 학습은 클라우드 노트북에서, 웹앱은 결과 조회 위주 |

> 표의 모드 번호는 ARCHITECTURE.md와 동일합니다. M2(WSL 2 내부 venv 실행)는 "WSL 2는 되는데 Docker Desktop만 안 되는" 예외 경우를 위한 중간 단계로 ARCHITECTURE.md에서 다룹니다.

## 4. 결과 기록표

### 4-1. 이 PC의 기록 (2026-09-19, 세션 중 확인한 항목만)

| # | 항목 | 결과 | 근거 |
|---|------|------|------|
| 1 | Windows 버전 | 통과 | Windows 11, 빌드 10.0.26200.9457 (WSL 출력에 표기) |
| 2 | 관리자 권한 | 통과 | 관리자 PowerShell로 WSL/Git/Docker 설치 성공 |
| 3 | BIOS 가상화 | 통과 | WSL 2 및 Docker 컨테이너 정상 실행 |
| 4 | RAM 8GB | **미확인** | 2-4절 명령으로 확인 필요 |
| 5 | 디스크 15GB | **미확인** | 2-5절 명령으로 확인 필요 (Docker 설치 후 여유 공간 기준) |
| 6 | WSL 2 | 통과 | WSL 2.7.14.0, 커널 6.18.33.2-2 |
| 7 | Docker Desktop | 통과 | Docker 29.8.0, `hello-world` 성공 |
| 8 | NVIDIA GPU | **미확인** | 2-8절 명령으로 확인 필요 |
| - | Git | 통과 | 2.55.0.windows.3 (참고 항목) |

현재 판정: 4·5번 확인 전까지는 **M0 또는 M1 후보**. GPU 확인 결과로 M0/M1이 갈립니다.

### 4-2. 빈 기록표 (다른 PC용 복사본)

| # | 항목 | 결과 (통과/실패) | 측정값·비고 |
|---|------|------------------|-------------|
| 1 | Windows 버전 | | |
| 2 | 관리자 권한 | | |
| 3 | BIOS 가상화 | | |
| 4 | RAM | | GB |
| 5 | 디스크 여유 | | GB |
| 6 | WSL 2 | | 버전 |
| 7 | Docker Desktop | | 버전 |
| 8 | NVIDIA GPU | | 모델명 / 드라이버 |
| 판정 | 실행 모드 | | M0 / M1 / M3 / M4 |

## 5. 8GB RAM 튜닝 권고

WSL 2가 메모리를 과하게 가져가지 않도록 사용자 폴더의 `.wslconfig` 파일(`%UserProfile%\.wslconfig`)에 상한을 둡니다. 아래는 **초기값 제안**이며 실제 학습을 돌려 보고 조정합니다. 변경 후에는 WSL을 완전히 종료(`wsl --shutdown`)하고 Docker Desktop을 다시 시작해야 반영됩니다.

| 설정 | 8GB PC 초기값 | 이유 |
|------|---------------|------|
| `memory` | 4GB | Windows와 브라우저가 쓸 몫(약 4GB)을 남김 |
| `processors` | 물리 코어의 절반 수준 | 학습 중에도 브라우저 UI가 끊기지 않도록 |
| `swap` | 2~4GB | OOM 완충용. 단, 디스크 여유를 그만큼 씀 |

앱 쪽 대응(자세한 내용은 ARCHITECTURE.md): 워커 1개, CSV 업로드 크기 상한, 학습 배치 크기 자동 축소, 컨테이너 메모리 한도 설정.

## 6. 자주 나는 문제 빠른 참조

| 증상 | 원인 | 조치 |
|------|------|------|
| `failed to connect to the docker API ...` | Docker Desktop 앱이 꺼져 있음 | 앱 실행 후 "Engine running" 대기 |
| Docker 시작 시 "Virtualization is disabled" | BIOS 가상화 꺼짐 | 2-3절 |
| "WSL 2 installation is incomplete" | WSL 커널 구버전 | `wsl --update` 후 재시작 |
| 컨테이너가 갑자기 종료(Exit 137) | 메모리 부족(OOM) | 5절 튜닝, CSV·모델 크기 축소 |
| `--gpus all` 오류 | 드라이버 미지원 또는 WSL 2 백엔드 아님 | 2-8절 |
| 디스크가 갑자기 참 | Docker 이미지·빌드 캐시 누적 | Docker Desktop의 Clean up 또는 미사용 이미지 정리 |
