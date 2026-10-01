"""스마트폰 LAN 접속용 QR 보조 페이지(/qr, /qr.svg) 엔드포인트 (STEP 10)."""

from __future__ import annotations

from tests.helpers import assert_one_sentence_error


def test_qr_svg_returns_image_for_valid_lan_url(client):
    r = client.get("/qr.svg", params={"text": "http://192.168.0.10:8080"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.text.startswith("<svg")


def test_qr_svg_rejects_non_http_text(client):
    r = client.get("/qr.svg", params={"text": "javascript:alert(1)"})
    assert_one_sentence_error(r, "E-QR-001", 400)


def test_qr_svg_rejects_too_long_text(client):
    r = client.get("/qr.svg", params={"text": "http://" + "a" * 60 + ":8080"})
    assert_one_sentence_error(r, "E-QR-001", 400)


def test_qr_svg_requires_text_param(client):
    r = client.get("/qr.svg")
    assert r.status_code in (400, 422)


def test_qr_page_shows_fallback_without_text(client):
    r = client.get("/qr")
    assert r.status_code == 200
    assert "시작.bat" in r.text


def test_qr_page_embeds_image_and_url_when_text_given(client):
    url = "http://192.168.0.10:8080"
    r = client.get("/qr", params={"text": url})
    assert r.status_code == 200
    assert "/qr.svg?text=" in r.text
    assert url in r.text
    assert '<html lang="ko">' in r.text


def test_qr_page_has_no_external_resources_and_uses_own_stylesheet(client):
    r = client.get("/qr", params={"text": "http://192.168.0.10:8080"})
    assert '/static/qr.css' in r.text
    import re

    assert not re.search(r"(?:src|href)=[\"']https?://", r.text)


def test_qr_page_escapes_text_safely(client):
    r = client.get("/qr", params={"text": "http://<script>"})
    # http:// 로 시작하지만 ASCII 이고 '<' '>' 문자가 포함되어, 그대로 삽입되면
    # 꺾쇠가 섞인 비정상 QR 데이터가 되므로 이미 E-QR-001 로 막히지는 않는다.
    # 다만 화면에 그대로 꺾쇠가 노출(이스케이프)되어도 실행되는 태그가 되지 않아야 한다.
    assert "<script>" not in r.text
