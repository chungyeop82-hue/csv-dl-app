# 배포 (DEPLOY) - STEP 9 학생 배포판

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
