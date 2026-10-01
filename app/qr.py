"""스마트폰 LAN 접속용 QR 코드 보조 페이지 (STEP 10).

/qr.svg : QR 코드 SVG 이미지만 반환한다.
/qr     : QR 이미지 + 안내문을 담은 PC용 보조 페이지(HTML)를 반환한다.

주의: 이 페이지는 PC 화면에서 "스마트폰이 접속할 주소"를 보여주기 위한 보조 페이지일 뿐,
STEP 8에서 만든 모바일 UI(/)를 대체하지 않는다. QR이 가리키는 주소는 항상 기존 "/" 이다.
스마트폰은 QR을 찍은 뒤 이 /qr 페이지가 아니라 바로 기존 모바일 UI로 접속한다.
"""

from __future__ import annotations

import html as html_lib
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, Response

from .errors import AppError
from .qrcode_gen import encode, to_svg

router = APIRouter()

_MAX_TEXT_LEN = 42  # QR 버전 1~3·레벨 M 바이트 모드 기준 최대 데이터 바이트 수


def _validate_text(text: str) -> str:
    if not text or not (text.startswith("http://") or text.startswith("https://")):
        raise AppError("E-QR-001", detail=f"허용되지 않은 형식: {text!r}")
    try:
        encoded = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise AppError("E-QR-001", detail="ASCII 문자가 아님") from exc
    if len(encoded) > _MAX_TEXT_LEN:
        raise AppError("E-QR-001", detail=f"너무 길음({len(encoded)}바이트)")
    return text


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_lib.escape(title)}</title>
<link rel="stylesheet" href="/static/qr.css">
</head>
<body>
{body}
</body>
</html>"""


@router.get("/qr.svg")
def qr_svg(text: str = Query(..., min_length=1, max_length=200)) -> Response:
    safe_text = _validate_text(text)
    matrix = encode(safe_text)
    svg = to_svg(matrix, scale=8, border=4)
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/qr", response_class=HTMLResponse)
def qr_page(text: str | None = Query(default=None, max_length=200)) -> HTMLResponse:
    if not text:
        body = """
<main class="qr-wrap">
  <h1>스마트폰 접속 QR</h1>
  <p>이 페이지는 <strong>시작.bat</strong> 또는 <strong>시작_GPU.bat</strong> 실행 시
  스마트폰 접속 주소와 함께 자동으로 열립니다.</p>
  <p>지금은 주소가 전달되지 않아 QR을 만들 수 없습니다. 시작.bat 을 다시 실행해 주세요.</p>
</main>
"""
        return HTMLResponse(_page("스마트폰 접속 QR - CSV 딥러닝 웹앱", body))

    safe_text = _validate_text(text)
    escaped_text = html_lib.escape(safe_text)
    svg_src = "/qr.svg?text=" + quote(safe_text, safe="")
    body = f"""
<main class="qr-wrap">
  <h1>스마트폰으로 접속하기</h1>
  <p>PC와 <strong>같은 Wi-Fi(LAN)</strong>에 연결된 스마트폰 카메라로 아래 QR 코드를 찍으면
  바로 접속됩니다.</p>
  <img class="qr-img" src="{svg_src}" width="320" height="320"
       alt="스마트폰 접속 QR 코드: {escaped_text}">
  <p class="qr-url">주소: <code>{escaped_text}</code></p>
  <p class="qr-note">
    QR을 찍을 수 없다면 스마트폰 브라우저 주소창에 위 주소를 직접 입력하세요.<br>
    PC와 스마트폰이 같은 Wi-Fi(LAN)에 연결되어 있어야 하며, 인터넷(외부망)에서는 접속되지 않습니다.<br>
    접속이 안 되면 <strong>방화벽_허용.bat</strong> 을 실행했는지, <strong>진단.bat</strong> 결과를 확인하세요.
  </p>
</main>
"""
    return HTMLResponse(_page("스마트폰 접속 QR - CSV 딥러닝 웹앱", body))
