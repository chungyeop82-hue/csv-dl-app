"""CSV 딥러닝 웹앱 진입점. 로그인 없음·사용자 1명 전제.

포트 게시(바인딩)는 compose 파일이 정한다 - 개발용 compose.yaml 은 127.0.0.1(이 PC) 전용이고,
학생 배포판 dist/compose.dist.yaml 은 같은 Wi-Fi(LAN)에서도 접속할 수 있도록 0.0.0.0 으로 게시한다
(STEP 10). 이 파일(앱 코드) 자체는 바인딩 방식과 무관하게 동일하게 동작한다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import MAX_UPLOAD_BYTES, UPLOAD_OVERHEAD_BYTES, Settings
from .datasets import router as datasets_router
from .errors import error_response, install_error_handling, setup_logging
from .jobs import router as jobs_router
from .jobs_worker import Dispatcher
from .qr import router as qr_router
from .reports import ReportExecutor
from .reports import router as reports_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("app")

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        setup_logging(settings)
        # 이전 실행에서 중단된 업로드 임시 파일을 정리한다.
        for leftover in settings.tmp_dir.glob("*.part"):
            leftover.unlink(missing_ok=True)
        db.init_db(settings.db_path)
        recovered = db.recover_interrupted_jobs(settings.db_path)
        if recovered:
            log.info("재시작 복구: running 이던 잡 %s건을 interrupted 로 표시", recovered)

        dispatcher = Dispatcher(settings)
        await dispatcher.start()
        app.state.dispatcher = dispatcher
        report_executor = ReportExecutor()
        report_executor.start()
        app.state.report_executor = report_executor
        log.info("앱 시작 - 저장 폴더 준비 완료")
        try:
            yield
        finally:
            await dispatcher.stop()
            report_executor.stop()

    # 내부 API 문서(/docs, /redoc, /openapi.json)는 열지 않는다.
    app = FastAPI(
        title="CSV 딥러닝 웹앱",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def upload_size_guard(request: Request, call_next):
        # 본문을 읽기 전에 Content-Length 로 명백한 초과를 거절한다.
        if request.method == "POST" and request.url.path == "/upload":
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES + UPLOAD_OVERHEAD_BYTES:
                return error_response("E-UP-002", request)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    # 요청 번호·오류 처리 미들웨어는 마지막에 등록해 가장 바깥에서 동작하게 한다.
    install_error_handling(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    app.include_router(datasets_router)
    app.include_router(jobs_router)
    app.include_router(reports_router)
    app.include_router(qr_router)  # STEP 10: 스마트폰 LAN 접속용 QR 보조 페이지
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
