# 배포 (DEPLOY) - STEP 9/10 학생 배포판

- 대상: PPT STEP 9 "학생용 배포 및 원클릭 실행"
- 짝 문서: [ARCHITECTURE.md](./ARCHITECTURE.md)(구조·폴백), [SETUP_CHECK.md](./SETUP_CHECK.md)(개발용 compose.yaml 빌드 검증), [BATCH_ENCODING.md](./BATCH_ENCODING.md)(.bat 인코딩 관리)
- 범위: STEP 1~8의 앱 코드와 기능은 전혀 바꾸지 않았습니다. 이 문서와 `dist/` 폴더, `.github/workflows/publish-ghcr.yml` 은
  전부 배포 방식을 새로 추가한 것이며, `app/`, `Dockerfile`, `compose.yaml`, `compose.gpu.yaml`(원본), `docker/`, `tests/` 는
  손대지 않았습니다.

## 1. 왜 개발용 compose.yaml과 다른 파일을 따로 두었는가

기존 `compose.yaml`/`compose.gpu.yaml`은 **이 저장소를 그대로 갖고 있는 사람**(로컬 빌드)을 위한 구성입니다.
학생은 소스 코드도, Python도, Dockerfile도 필요 없이 **미리 빌드된 이미지**만 받아서 실행해야 하므로,
`dist/compose.dist.yaml` + `dist/compose.gpu.yaml`(배포판 전용) 이라는 별도의 pull 전용 구성을 만들었습니다.

- 개발용: `docker compose up -d --build` (이미지를 이 PC에서 빌드)
- 배포판: `docker compose -f compose.dist.yaml pull` 후 `up -d` (GHCR에서 이미지를 받기만 함)
- 배포판의 Docker Compose 프로젝트 이름은 `csv-dl-app-dist`로, 개발용(`csv-dl-app`)과 다르게 두어
  같은 PC에서 두 구성을 실행해도 볼륨과 컨테이너가 섞이지 않습니다(단, 호스트 포트 8080은 하나뿐이므로
  두 구성을 동시에 띄울 수는 없습니다 - 검증 시 순서대로 내려야 합니다).
- 네트워크 노출(호스트 `127.0.0.1:8080`에만 게시), 볼륨 구성(`appdata` 명명된 볼륨 + `./exports` 바인드),
  헬스체크는 ARCHITECTURE.md 4절의 원칙을 그대로 유지했습니다.

## 2. dist 폴더 구조

```
dist/
├─ 시작.bat                 실행 (CPU, 기본)
├─ 시작_GPU.bat             실행 (NVIDIA GPU)
├─ 종료.bat                 종료 (docker compose down)
├─ 초기화.bat               데이터 초기화, 실행 전 확인 필요 (docker compose down -v)
├─ 진단.bat                 실행 환경 11개 항목 점검
├─ compose.dist.yaml       실행 구성 (CPU 기본, 이미지를 pull만 함, 로컬 빌드 없음)
├─ compose.gpu.yaml        GPU 오버라이드 (NVIDIA 디바이스 예약)
├─ .env.example            이미지 경로 설정 예시. .env 로 복사해서 사용
├─ README.md               학생용 실행 안내 (한국어)
├─ exports/.gitkeep        학습 결과, 제출 ZIP 저장 폴더
└─ offline/
   ├─ 오프라인_이미지_저장.bat       인터넷 되는 PC에서 이미지를 .tar로 저장
   ├─ 오프라인_이미지_불러오기.bat   오프라인 PC에서 .tar를 불러오기
   └─ README.md                     오프라인 배포 절차
```

(STEP 10에서 `scripts/get_lan_ip.ps1`, `방화벽_허용.bat`이 추가되고 `시작.bat`/`시작_GPU.bat`/`진단.bat`/`compose.dist.yaml`이
일부 수정되었습니다. 최신 구조와 변경 내용은 8절을 보세요.)

## 3. GHCR 배포 workflow

`.github/workflows/publish-ghcr.yml` (저장소 루트, GitHub Actions):

- 트리거: `main` 브랜치 push, `v*` 태그 push, 수동 실행(workflow_dispatch)
- CPU(`cpu-runtime`)와 GPU(`gpu-runtime`) 타깃을 행렬(matrix)로 각각 빌드해
  `ghcr.io/<저장소 소유자>/<저장소 이름>:cpu` 및 `:gpu` 태그로 푸시합니다.
  커밋 SHA 태그(`:cpu-<sha>`, `:gpu-<sha>`)도 함께 남겨 추적할 수 있게 했습니다.
- 기존 `Dockerfile`을 그대로 사용합니다(빌드 타깃 이름도 기존과 동일: `cpu-runtime`, `gpu-runtime`).
- **주의 1**: GHCR에 처음 올라간 패키지는 기본적으로 비공개(private)일 수 있습니다. 학생이 로그인 없이
  `docker pull` 할 수 있도록 GitHub 저장소의 Packages 설정에서 이미지를 **Public**으로 바꿔야 합니다.
  이 워크플로는 이 설정을 대신 해주지 않습니다(최초 1회 수동 작업 필요).
- **주의 2**: GPU 이미지는 PyTorch CUDA wheel을 받아 설치하므로 빌드 시간이 길고(수 GB, 10분 이상 가능)
  GitHub Actions 무료 실행 시간에 영향을 줄 수 있습니다.
- 배포 담당자는 워크플로 실행 후 `dist/.env` 의 `APP_IMAGE` 값을
  `ghcr.io/<저장소 소유자 소문자>/<저장소 이름 소문자>` 로 채워야 합니다.

## 4. 오프라인(인터넷 없는 환경) 지원

`dist/offline/` 의 두 스크립트로 `docker save`/`docker load` 기반 배포를 지원합니다. 절차는
`dist/offline/README.md`에 정리했습니다. 요약: 인터넷 되는 PC에서 이미지를 `.tar`로 저장 -> USB로
옮김 -> 오프라인 PC에서 `.tar`를 불러온 뒤 `시작.bat`/`시작_GPU.bat` 실행(이때는 pull이 실패해도
로컬에 이미 있는 이미지로 자동 진행하도록 `시작.bat`/`시작_GPU.bat`에 폴백 로직을 넣었습니다).

## 5. 배치 파일(.bat) 인코딩

[BATCH_ENCODING.md](./BATCH_ENCODING.md) 참고. 모든 `.bat`는 CP949(ANSI 한국어), BOM 없음,
CRLF로 저장했고, 방어적으로 `chcp 949`를 각 스크립트 앞부분에 추가했습니다.

## 6. 진단.bat 점검 항목과 근거

| # | 항목 | 확인 방법 | 근거 |
|---|------|-----------|------|
| 1 | Windows 가상화 | PowerShell `HypervisorPresent` | ENV_CHECK.md 2-3절과 동일한 목적 |
| 2 | WSL2 | `wsl --status` 원본 출력 표시 | ENV_CHECK.md 2-6절. 자동 판정 대신 원문을 보여줌(아래 5절 참고) |
| 3 | RAM | PowerShell `TotalPhysicalMemory` (8GB 기준) | ENV_CHECK.md 2-4절과 동일 기준 |
| 4 | 디스크 여유 | PowerShell `Get-PSDrive` (15GB 기준, 스크립트가 있는 드라이브) | ENV_CHECK.md 2-5절과 동일 기준 |
| 5 | 8080 포트 | `netstat -ano \| findstr :8080` | ENV_CHECK.md 2-9절 |
| 6 | Docker | `docker --version` + `docker info` | SETUP_CHECK.md 1번 |
| 7 | Docker Compose | `docker compose version` | SETUP_CHECK.md 2번 |
| 8 | nvidia-smi | `where nvidia-smi` + 실행 | ENV_CHECK.md 2-8절 |
| 9 | Docker GPU 사용 가능 여부 | `docker run --rm --gpus all ubuntu nvidia-smi` | ENV_CHECK.md 2-8절과 동일 명령 |
| 10 | PyTorch CUDA 사용 가능 여부 | 실행 중인 앱 컨테이너에서 `torch.cuda.is_available()` | 앱이 실행 중일 때만 확인(무거운 이미지를 진단만을 위해 새로 받지 않음) |
| 11 | /health 상태 | `curl http://127.0.0.1:8080/health` | 앱이 실행 중일 때만 확인 |

## 7. 검증 상태 (이 문서 작성 시점)

| 항목 | 상태 | 비고 |
|------|------|------|
| YAML 문법(compose.dist.yaml, compose.gpu.yaml) | 통과 | `docker compose config` 대신 Python `yaml.safe_load`로 파싱 검증(클라우드 작업 환경에 Docker가 없어 compose 자체 실행은 못 함) |
| GitHub Actions workflow YAML 문법 | 통과 | Python `yaml.safe_load`로 파싱 검증 |
| .bat 스크립트 실제 실행 | **미검증** | 클라우드/Linux 작업 환경이라 cmd.exe를 실행할 수 없음. 아래 "검증 절차" 참고 |
| CP949 인코딩 변환 | 통과(인코딩 자체) | 모든 한글 문구가 CP949로 인코딩 오류 없이 변환됨을 확인. 실제 cmd 창에서 보이는 모습은 Windows에서 확인 필요 |
| GHCR 배포 workflow 실제 실행 | **미검증** | GitHub Actions 실행 환경이 아니므로 실제 빌드/푸시는 못 해 봄 |
| docker save/load 오프라인 스크립트 | **미검증** | 위와 동일한 사유로 실제 이미지 저장/불러오기는 못 해 봄 |

## 8. STEP 10 - 스마트폰 LAN 접속, QR 코드, 방화벽 안내

- 대상: PPT STEP 10 "같은 Wi-Fi에 연결된 스마트폰에서 PC의 웹앱에 접속"
- 범위: STEP 1~9의 앱 기능과 코드는 바꾸지 않았습니다. STEP 8에서 만든 모바일 UI(`/`)도 그대로 재사용하며,
  별도의 모바일 웹 UI를 새로 만들지 않았습니다. 바뀐 것은 ① `dist/compose.dist.yaml`의 포트 바인딩(LAN 노출),
  ② `시작.bat`/`시작_GPU.bat`의 "실행 완료" 안내 부분(스마트폰 주소·QR 표시), ③ `진단.bat`에 점검 항목 4개 추가,
  ④ 새 파일(`app/qrcode_gen.py`, `app/qr.py`, `app/static/qr.css`, `dist/scripts/get_lan_ip.ps1`,
  `dist/방화벽_허용.bat`)뿐입니다.

### 8-1. 포트 바인딩

| 파일 | STEP 9 이전 | STEP 10 이후 |
|------|------------|--------------|
| `compose.yaml`(개발용, 원본) | `127.0.0.1:8080:8000` | **변경 없음** - 그대로 127.0.0.1 전용 |
| `dist/compose.dist.yaml`(배포판) | `127.0.0.1:8080:8000` | `0.0.0.0:8080:8000` (같은 Wi-Fi(LAN)의 다른 기기도 접속 가능) |
| `dist/compose.gpu.yaml`(GPU 오버라이드) | 포트 설정 없음(dist.yaml 그대로 사용) | **변경 없음** |

컨테이너 내부(8000번)와 앱 코드는 전혀 바뀌지 않았습니다. 바인딩만 "이 PC 전용"에서
"이 PC가 속한 LAN 전체"로 넓어진 것이며, 인터넷(외부망) 공개가 아닙니다 - 가정용/학교용 공유기의
기본 설정에서는 LAN 밖(외부 인터넷)에서 이 주소로 바로 접속되지 않습니다.

### 8-2. 스마트폰 접속 주소 자동 안내

`dist/scripts/get_lan_ip.ps1`이 `Get-NetIPAddress`/`Get-NetAdapter`로 다음을 제외하고 LAN IPv4를 고릅니다.

- `127.0.0.1`, `169.254.*`(APIPA)
- Docker/WSL/Hyper-V/VPN류 가상 어댑터(어댑터 이름에 `vEthernet`, `Docker`, `WSL`, `Virtual` 등이 포함된 경우)
- 연결되지 않은(Status ≠ Up) 어댑터

남은 후보 중 `192.168.x.x` > `10.x.x.x` > `172.16~31.x.x` > 그 외 순으로 하나를 고릅니다. 못 찾으면 아무 것도
출력하지 않으며, `시작.bat`/`시작_GPU.bat`은 이 경우 "확인 불가" 안내와 함께 Windows 설정에서 직접 확인하는
방법을 보여줍니다.

`시작.bat`/`시작_GPU.bat` 실행 완료 화면은 이제 두 주소를 모두 보여줍니다.

```
PC:      http://localhost:8080
스마트폰: http://192.168.x.x:8080   (같은 Wi-Fi에서만 접속 가능)
```

### 8-3. QR 코드

`app/qrcode_gen.py`는 외부 라이브러리 없이(표준 라이브러리만 사용) 작성한 QR 코드(Model 2, 바이트 모드,
오류정정 레벨 M, 버전 1~3) 인코더입니다. 이 샌드박스 작업 환경에서는 `pypi.org` 등 패키지 저장소에 접근할 수
없어 `segno`/`qrcode` 같은 QR 생성 라이브러리를 설치할 수 없었기 때문입니다. 대신 OpenCV(`cv2.QRCodeDetector`)를
검증 전용 도구로 써서 다음을 확인했습니다(OpenCV는 앱이나 이 모듈의 의존성이 아닙니다 - 검증에만 사용).

- Reed-Solomon 인코딩 자기 검증(생성다항식의 각 근에서 코드워드 값이 0인지) - `tests/test_qrcode_gen.py`
- 버전 1~3 경계값(최대 글자 수)에서 정확히 인코딩되고, 그 이상은 `ValueError`로 명확히 거절되는지
- 실제 LAN 주소 형태(`http://192.168.x.x:8080` 등) 다수와 임의 ASCII 문자열 약 50개를 왕복 인코딩→
  OpenCV 디코딩해 전부 원문과 일치하는지(49/49 통과)
- 앱이 실제로 내보내는 SVG를 헤드리스 브라우저로 그대로 렌더링한 뒤 스크린샷을 OpenCV로 디코딩해도
  동일하게 성공하는지

새 라우트 2개(`app/qr.py`, `app/main.py`에 1줄 등록):

| 경로 | 설명 |
|------|------|
| `GET /qr.svg?text=<주소>` | QR 코드 SVG 이미지만 반환 |
| `GET /qr?text=<주소>` | QR 이미지 + 주소 텍스트 + 안내문을 담은 PC용 보조 페이지(HTML) |

`text`는 `http://` 또는 `https://`로 시작하는 ASCII 문자열(최대 42바이트)만 허용하며, 그 외에는 새 오류
코드 `E-QR-001`로 거절합니다(기존 오류 처리 미들웨어가 그대로 받아 한국어 한 문장 + 요청 번호로 응답하므로
`app/errors.py`는 바꾸지 않았습니다). `/qr` 페이지는 CSP(`style-src 'self'`)를 지키기 위해 인라인
style을 쓰지 않고 새 파일 `app/static/qr.css`만 사용합니다.

**중요**: `/qr`은 PC 화면에 "스마트폰이 찍을 QR"을 보여주기 위한 보조 페이지일 뿐입니다. QR이 가리키는
주소는 항상 기존 `/` (STEP 8 모바일 UI)이며, 스마트폰은 QR을 찍은 뒤 `/qr`이 아니라 바로 기존 모바일
UI로 접속합니다. 별도의 모바일 웹 UI를 새로 만들지 않았습니다(요구사항 9).

`시작.bat`/`시작_GPU.bat`은 LAN IP를 찾으면 `http://localhost:8080/qr?text=http://<LAN_IP>:8080` 페이지를
브라우저 탭으로 추가로 엽니다.

### 8-4. Windows 방화벽

`dist/방화벽_허용.bat`(신규)은 다음 순서로 동작하며, **사용자 확인(Y) 없이는 방화벽 설정을 바꾸지
않습니다**(요구사항 5).

1. 관리자 권한 확인(`net session`), 없으면 `Start-Process -Verb RunAs`로 자기 자신을 관리자 권한으로 재실행
2. `CSV 딥러닝 웹앱 LAN 8080`이라는 이름의 인바운드 규칙이 이미 있는지 확인(중복 추가 방지)
3. 없으면 추가할지 Y/N으로 물은 뒤, Y일 때만 `New-NetFirewallRule`로 TCP 8080 인바운드 허용 규칙을 추가

`진단.bat`은 이 규칙의 존재/활성화 여부만 "확인"하고, 스스로 바꾸지는 않습니다.

### 8-5. 진단.bat 점검 항목 추가 (12~15번)

| # | 항목 | 확인 방법 |
|---|------|-----------|
| 12 | LAN IPv4 주소 | `get_lan_ip.ps1` 재사용 |
| 13 | 8080 포트 LISTEN 상태(0.0.0.0 바인딩 여부) | `netstat -ano \| findstr LISTENING \| findstr 0.0.0.0:8080` |
| 14 | Windows Firewall 상태(TCP 8080 인바운드) | `Get-NetFirewallRule -DisplayName 'CSV 딥러닝 웹앱 LAN 8080'` |
| 15 | 스마트폰 접속 주소 | 12번 결과로 `http://<IP>:8080`, `/qr?text=...` 조합 |

1~11번(STEP 9)은 그대로입니다.

### 8-6. dist 폴더 구조 (STEP 10 반영)

```
dist/
├─ 시작.bat                 실행 (CPU, 기본) - 끝에 스마트폰 주소·QR 안내 추가(STEP 10)
├─ 시작_GPU.bat             실행 (NVIDIA GPU) - 동일하게 추가(STEP 10)
├─ 종료.bat                 종료 (변경 없음)
├─ 초기화.bat               데이터 초기화 (변경 없음)
├─ 진단.bat                 실행 환경 점검 - 12~15번 항목 추가(STEP 10)
├─ 방화벽_허용.bat          (신규, STEP 10) TCP 8080 인바운드 허용, 사용자 확인 필요
├─ scripts/
│  └─ get_lan_ip.ps1        (신규, STEP 10) LAN IPv4 자동 탐지
├─ compose.dist.yaml       실행 구성 - 포트 바인딩만 0.0.0.0으로 변경(STEP 10)
├─ compose.gpu.yaml        GPU 오버라이드 (변경 없음)
├─ .env.example            (변경 없음)
├─ README.md               학생용 실행 안내
├─ exports/                (변경 없음)
└─ offline/                (변경 없음)
```

### 8-7. Android APK가 선택 사항인 이유

요구사항 10은 "웹브라우저 LAN 접속을 먼저 완성하고, APK는 구현하지 말고 마지막에 선택 옵션으로만 정리"를
명시했습니다. 이 앱은 STEP 8부터 반응형 모바일 웹 UI(`/`)로 이미 동작하므로, 스마트폰에서 **네이티브 앱
없이** Chrome 등 기본 브라우저로 모든 기능(파일 선택·업로드·학습·진행률·결과·리포트·제출 ZIP)을 쓸 수
있습니다. APK(네이티브 앱 또는 WebView 래퍼)를 추가로 만들면 ① 빌드 체인(Android Studio/Gradle)과
서명 키 관리, ② 스토어 배포 또는 APK 사이드로드(스마트폰 보안 설정 변경) 같은 별도 복잡도가 생기지만,
기능 면에서 브라우저 접속과 차이가 없습니다. 따라서 과제 범위에서는 "불필요한 복잡도"이며, 구현하지
않고 선택 사항으로 남겨 둡니다. 나중에 필요하다면 가장 가벼운 방법은 기존 `/`를 그대로 보여주는
WebView 한 화면짜리 래퍼 앱(주소를 `http://<LAN_IP>:8080`으로 고정)이고, 완전한 네이티브 재구현은
권장하지 않습니다(UI를 중복 유지해야 함).

### 8-8. 검증 상태 (STEP 10)

| 항목 | 상태 | 비고 |
|------|------|------|
| QR 인코더 RS/BCH 자기 검증 | 통과 | `tests/test_qrcode_gen.py` (의존성 없는 대수적 검증) |
| QR 인코더 ↔ OpenCV 왕복 디코딩 | 통과 | 실제 LAN 주소 형태 + 임의 ASCII 약 50개, 버전 1~3 경계값 전부 포함, 49/49 성공 |
| QR SVG → 헤드리스 브라우저 렌더링 → OpenCV 디코딩 | 통과 | 앱이 실제로 내보내는 `to_svg()` 결과 그대로 사용 |
| `app/qr.py` 엔드포인트 단위 테스트(`tests/test_qr.py`) | 작성됨, **미실행** | 이 작업 환경에 `fastapi` 등 패키지 저장소 접근이 막혀 있어 설치하지 못함(STEP 9와 동일한 사유). `docker compose up --build` 가능한 PC에서 `pytest` 실행 필요 |
| `compose.dist.yaml` YAML 문법 | 통과 | `yaml.safe_load` 파싱 검증 |
| `get_lan_ip.ps1`, `시작.bat`/`시작_GPU.bat`/`방화벽_허용.bat`/`진단.bat` 실제 실행 | **미검증** | 클라우드/Linux 작업 환경이라 PowerShell·cmd.exe를 실행할 수 없음(STEP 9와 동일한 한계). 문법은 기존에 검증된 STEP 9 스크립트의 관용구를 그대로 재사용해 손으로 신중히 검토함 |
| 실제 스마트폰 실기기 접속(업로드→학습→결과→리포트→제출 ZIP) | **미검증** | Windows PC·스마트폰·실제 Wi-Fi가 필요하며 이 작업 환경에는 없음 |
| Windows Firewall 규칙 추가/점검 실제 동작 | **미검증** | 위와 동일한 사유 |
| CP949 인코딩 변환(신규/수정 .bat) | 통과 | 모든 한글 문구가 CP949로 인코딩 오류 없이 변환되고 왕복 디코딩이 원문과 일치함을 확인 |
