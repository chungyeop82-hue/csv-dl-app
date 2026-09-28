"""오류 카탈로그와 공통 오류 응답.

사용자에게는 한국어 한 문장(원인 + 해결)과 오류 코드, 요청 번호만 보인다.
스택트레이스·내부 경로·SQL·예외 원문은 서버 로그(/data/logs)에만 기록한다.
"""

from __future__ import annotations

import logging
import uuid
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .catalog import CATALOG
from .config import Settings

log = logging.getLogger("app")



class AppError(Exception):
    """카탈로그에 있는 오류 코드로 발생시키는 예외. 상세 원인(detail)은 로그에만 남는다."""

    def __init__(self, code: str, status: int | None = None, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.status = status if status is not None else CATALOG[code][0]
        self.detail = detail


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def error_response(code: str, request: Request, status: int | None = None) -> JSONResponse:
    st, message = CATALOG[code]
    rid = _request_id(request)
    return JSONResponse(
        status_code=status if status is not None else st,
        content={"error": {"code": code, "message": message, "request_id": rid}},
        headers={"X-Request-ID": rid},
    )


def setup_logging(settings: Settings) -> None:
    """서버 로그를 /data/logs/app.log 에 회전 저장한다. 화면·API 응답에는 나가지 않는다."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("app")
    root.setLevel(logging.INFO)
    root.propagate = False
    target = str(settings.log_path)
    for h in list(root.handlers):
        if isinstance(h, RotatingFileHandler) and h.baseFilename == str(settings.log_path.resolve()):
            return
        root.removeHandler(h)
        h.close()
    handler = RotatingFileHandler(target, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(handler)


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - 어떤 예외도 화면에 내보내지 않는다
            log.exception("처리되지 않은 예외 rid=%s %s %s", request.state.request_id, request.method, request.url.path)
            return error_response("E-SY-002", request)
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        log.warning("오류 %s rid=%s %s %s detail=%s", exc.code, _request_id(request), request.method, request.url.path, exc.detail)
        return error_response(exc.code, request, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        log.warning("요청 검증 실패 rid=%s %s %s", _request_id(request), request.method, request.url.path)
        code = "E-UP-010" if request.url.path == "/upload" else "E-SY-004"
        return error_response(code, request)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        code = "E-SY-003" if exc.status_code == 404 else "E-SY-004"
        return error_response(code, request, exc.status_code)

    @app.exception_handler(Exception)
    async def fallback_handler(request: Request, exc: Exception):
        log.error("처리되지 않은 예외 rid=%s", _request_id(request), exc_info=exc)
        return error_response("E-SY-002", request)
