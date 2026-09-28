# CLAUDE.md — CSV 딥러닝 웹앱 프로젝트 규칙

Windows 11 + Docker Desktop(WSL 2)에서 단독 실행하는 CSV 딥러닝 웹앱입니다. 작업 전에 이 파일과 `docs/`를 먼저 읽습니다.

- 환경 점검: [docs/ENV_CHECK.md](docs/ENV_CHECK.md), 빠른 점검: [docs/SETUP_CHECK.md](docs/SETUP_CHECK.md)
- 제품 명세(기능·화면·오류 문구·수용 기준): [docs/SPEC.md](docs/SPEC.md)
- 구조와 폴백: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## 고정 조건 (변경하려면 먼저 사용자에게 확인)

| # | 조건 | 의미 |
|---|------|------|
| 1 | 사용자 1명 | 다중 사용자·권한 구분·동시 접속 대응을 만들지 않음 |
| 2 | 로그인 없음 | 인증·세션·계정 기능 금지. 대신 포트는 `127.0.0.1`에만 게시 |
| 3 | app 컨테이너 1개 | 웹 + 학습 워커를 한 컨테이너에서 실행. 별도 워커·DB·큐 컨테이너를 추가하지 않음 |
| 4 | pathlib 경로 | 파일 경로는 `pathlib.Path`로만 다룸. 문자열 이어붙이기·`os.path.join`·하드코딩된 `/data`, `C:\` 금지. 기준 경로는 환경 변수(`APP_DATA_DIR`, `APP_EXPORT_DIR`)에서 읽음 |
| 5 | 셸 스크립트 LF | `*.sh`는 항상 LF 줄바꿈 (`.gitattributes`로 강제). CRLF가 섞이면 컨테이너에서 실행 실패 |
| 6 | 한국어 UTF-8 | 화면 문구·오류 메시지·문서는 한국어. 모든 텍스트 파일은 UTF-8. CSV 입력은 UTF-8/UTF-8 BOM/CP949를 처리 |
| 7 | 360px 모바일 우선 | UI는 360px 너비를 기준으로 먼저 설계하고 넓은 화면으로 확장. 가로 스크롤 없이 동작 |
| 8 | CPU 기본 | 기본 실행은 CPU 이미지. GPU 없이도 모든 기능이 동작해야 함 |
| 9 | GPU는 선택 프로필 | GPU는 `compose.gpu.yaml`을 겹쳐 실행할 때만 켬(`docker compose -f compose.yaml -f compose.gpu.yaml up`). 기본 `docker compose up`에는 포함되지 않음. 같은 `app` 서비스를 **대체**하므로 컨테이너는 항상 1개 |
| 10 | 스택트레이스 노출 금지 | 화면·API 응답에 스택트레이스, 내부 경로, SQL, 예외 원문을 절대 내보내지 않음. 사용자에게는 한국어 안내 문구와 오류 코드만 보이고, 상세는 서버 로그(`/data/logs`)에만 기록 |

## 아키텍처 요약

- 구성: app 컨테이너 1개, `appdata` 명명된 볼륨(컨테이너 `/data`: SQLite·업로드·모델·로그), `./exports` 바인드 마운트(컨테이너 `/exports`: 사용자용 결과물)
- 실행 골격: `Dockerfile`(멀티스테이지, 타깃 `cpu-runtime`·`gpu-runtime`·`test`), `compose.yaml`(CPU 기본), `compose.gpu.yaml`(GPU 오버라이드). 호스트 `127.0.0.1:8080` → 컨테이너 `8000`, 헬스체크 `/health`
- 저장소: SQLite(WAL). 웹과 워커는 각자 연결을 열고 공유하지 않음. DB는 바인드 마운트 위에 두지 않음
- 학습: `ProcessPoolExecutor(max_workers=1)`, 시작 방식은 `spawn` 명시, 웹 프로세스에서 torch를 가져오지 않음
- 학습 라이브러리: `app/ml/`. torch는 `device.py`·`tabular.py` 안에서만 지연 import, CPU는 `torch.set_num_threads(2)`, 장치는 `APP_DEVICE` + `torch.cuda.is_available()`
- 폴백: GPU 불가 → CPU 모드, Docker 불가·가상화 불가 → Windows 네이티브 실행 (상세는 ARCHITECTURE.md 8절)
- 자원 기준: RAM 8GB, 여유 디스크 15GB. 동시 학습 금지, 업로드 크기 상한 적용

## 작업 규칙

- 환경 점검(`docs/SETUP_CHECK.md`)이 통과하지 않았으면 앱 코드를 만들지 않고, 해결 순서를 한국어로 안내합니다.
- 프로젝트 경로는 한글·공백이 없는 ASCII 경로를 권장합니다 (예: `C:\work\csv-dl-app`).
- 명령 예시는 PowerShell 기준으로 작성합니다.
- 구조·조건을 바꾸는 작업은 코드보다 먼저 `docs/`를 수정합니다.
- 테스트는 컨테이너 안에서 실행합니다: `docker build --target test -t csv-dl-app:test .` 후 `docker run --rm csv-dl-app:test` (SETUP_CHECK 7절).
- 코드 작성 후에는 스크립트 줄바꿈(LF), UTF-8 저장, 360px 화면, 오류 화면의 스택트레이스 미노출 여부를 확인합니다.
