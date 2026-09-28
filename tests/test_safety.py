"""스택트레이스·내부 경로·SQL·예외 원문 비노출(고정 조건 10), 화면 자원, 시작 정리."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import datasets as datasets_module
from app import db
from app.errors import CATALOG
from app.main import STATIC_DIR, create_app
from tests.helpers import assert_one_sentence_error, make_csv, stored_files, tmp_files, upload

LEAK_PATTERNS = ["Traceback", "File \"", "RuntimeError", "OperationalError", "sqlite", "SELECT", "INSERT",
                 "/data", "/app/", "\\Users", ".py", "secret-detail", "loc", "detail", "msg"]


def _assert_no_leak(text: str):
    for needle in LEAK_PATTERNS:
        assert needle not in text, f"응답에 '{needle}' 가 노출됨: {text}"


def test_unexpected_exception_returns_e_sy_002_and_logs_traceback(client, settings, monkeypatch):
    def boom(_path):
        raise RuntimeError("secret-detail /data/uploads/x.csv SELECT * FROM datasets")

    monkeypatch.setattr(datasets_module, "ingest", boom)
    r = upload(client, make_csv(60))
    assert_one_sentence_error(r, "E-SY-002")
    _assert_no_leak(r.text)
    assert stored_files(settings) == [] and tmp_files(settings) == []

    log_text = settings.log_path.read_text(encoding="utf-8")
    assert "Traceback" in log_text and "secret-detail" in log_text  # 상세는 로그에만 남는다


def test_db_failure_returns_e_sy_002_and_cleans_up(client, settings, monkeypatch):
    def broken_session(_path):
        raise sqlite3.OperationalError("no such table: datasets /data/db/app.sqlite3")

    monkeypatch.setattr(db, "session", broken_session)
    r = upload(client, make_csv(60))
    assert_one_sentence_error(r, "E-SY-002")
    _assert_no_leak(r.text)
    assert stored_files(settings) == [] and tmp_files(settings) == []
    assert "no such table" in settings.log_path.read_text(encoding="utf-8")


def test_error_on_get_and_delete_also_hidden(client, monkeypatch):
    def broken_session(_path):
        raise sqlite3.OperationalError("database is locked /data/db")

    monkeypatch.setattr(db, "session", broken_session)
    for r in (client.get("/datasets"), client.delete("/datasets/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12)):
        assert_one_sentence_error(r, "E-SY-002")
        _assert_no_leak(r.text)


def test_validation_error_does_not_expose_field_details(client):
    r = client.post("/upload", data={"file": "abc"})
    assert_one_sentence_error(r, "E-UP-010")
    _assert_no_leak(r.text)


def test_unknown_path_and_wrong_method_use_korean_catalog(client):
    r = client.get("/no-such-page")
    assert_one_sentence_error(r, "E-SY-003")
    r = client.put("/health")
    assert_one_sentence_error(r, "E-SY-004", 405)
    r = client.get("/static/nope.js")
    assert_one_sentence_error(r, "E-SY-003")


def test_api_docs_are_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_health_still_works(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_response_headers_protect_the_page(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_stale_tmp_files_are_removed_at_startup(settings):
    settings.ensure_dirs()
    (settings.tmp_dir / "leftover.part").write_bytes(b"x")
    with TestClient(create_app(settings)):
        assert tmp_files(settings) == []


def test_index_is_korean_mobile_first_and_has_no_external_resources(client):
    html = client.get("/").text
    assert '<html lang="ko">' in html
    assert 'name="viewport"' in html and "width=device-width" in html
    assert not re.search(r"(?:src|href)=[\"']https?://", html)
    for name in ("style.css", "app.js"):
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), name


def test_static_files_are_utf8_and_use_no_inner_html():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    for path in STATIC_DIR.glob("*"):
        path.read_bytes().decode("utf-8")  # UTF-8 로 저장되었는지 확인


def test_client_side_messages_match_server_catalog():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    pairs = re.findall(r'"(E-[A-Z]{2}-\d{3})":\s*"([^"]+)"', js)
    assert pairs, "CLIENT_ERRORS 를 찾지 못함"
    for code, message in pairs:
        assert CATALOG[code][1] == message, code


def test_no_hardcoded_data_paths_in_app_code():
    """고정 조건 4: 문자열 경로 이어붙이기·os.path.join·하드코딩된 /data, C:\\ 금지."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    for py in app_dir.glob("*.py"):
        code = py.read_text(encoding="utf-8")
        code_no_comments = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
        assert "os.path.join" not in code_no_comments, py.name
        assert not re.search(r"[\"']/data[/\"']", code_no_comments), py.name
        assert "C:\\\\" not in code_no_comments, py.name


def test_web_process_does_not_import_torch():
    """웹 프로세스에서는 torch 를 가져오지 않는다(ARCHITECTURE). 컨테이너에는 torch 가 설치되어 있어 의미가 있다."""
    import subprocess
    import sys

    code = "import sys, app.main; print('torch' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
