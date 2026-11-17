"""Logging standard (hosh-management AGENTS.md section 11): one JSON object per line on stdout with
ts, level, service, version, event, msg; requests logged only when they matter; req_id echoed."""
import io
import json
import logging
import re
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.web import setup_logging

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
REQUIRED = {"ts", "level", "service", "version", "event", "msg"}


@pytest.fixture
def logs(monkeypatch):
    """Installs the app's real logging (checked to write to stdout), then reads its JSON lines from a buffer."""
    monkeypatch.setenv("PLATS_VERSION", "v9.9.9")
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    buf = io.StringIO()

    def install():
        setup_logging()
        (handler,) = root.handlers
        assert handler.stream is sys.stdout, "log lines go to stdout"
        handler.setStream(buf)

    install()

    def read():
        lines = buf.getvalue().splitlines()
        buf.seek(0)
        buf.truncate()
        return [json.loads(line) for line in lines if line.strip()]

    read.install = install
    yield read
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


def _check_format(rec):
    assert REQUIRED <= rec.keys(), rec
    assert TS.match(rec["ts"]), rec["ts"]
    assert rec["level"] in ("debug", "info", "warn", "error")
    assert rec["service"] == "plats" and rec["version"] == "v9.9.9"
    assert re.match(r"^[a-z][a-z0-9_]*$", rec["event"]), rec["event"]


def test_startup_and_shutdown_lines(settings, logs, monkeypatch):
    monkeypatch.setenv("PLATS_SECRET_KEY", "must-not-appear")
    with TestClient(create_app(settings), base_url="https://testserver"):
        pass
    recs = logs()
    for r in recs:
        _check_format(r)
    start = next(r for r in recs if r["event"] == "startup")
    assert start["level"] == "info" and start["port"] == 8000
    assert start["data_dir_writable"] is True and start["admin_configured"] is True
    assert "PLATS_SECRET_KEY" in start["config"]
    assert "must-not-appear" not in json.dumps(recs)
    assert any(r["event"] == "shutdown" for r in recs)


def test_unhandled_error_logs_one_error_line_with_err(settings, logs):
    app = create_app(settings)

    @app.get("/boom/{n}")
    def boom(n: int):
        raise RuntimeError("kaboom")

    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        logs()  # drop the startup line
        r = c.get("/boom/7?secret=x", headers={"X-Request-Id": "abc-123"})
    assert r.status_code == 500
    errors = [x for x in logs() if x["level"] == "error"]
    assert len(errors) == 1, errors
    e = errors[0]
    _check_format(e)
    assert e["event"] == "http" and e["status"] == 500 and e["method"] == "GET"
    assert e["route"] == "/boom/{n}", "the route template, never the raw path"
    assert "secret" not in json.dumps(e) and "/boom/7" not in json.dumps(e)
    assert e["req_id"] == "abc-123" and isinstance(e["dur_ms"], float)
    assert e["err"]["type"] == "RuntimeError" and e["err"]["message"] == "kaboom"
    assert "in boom" in e["err"]["stack"] and e["err"]["stack"].rstrip().endswith("RuntimeError: kaboom")


def test_requests_logged_only_when_they_matter(client, logs):
    logs()
    ok = client.get("/")
    health = client.get("/healthz")
    missing = client.get("/nope")
    assert (ok.status_code, health.status_code, missing.status_code) == (200, 200, 404)
    assert [r for r in logs() if r["event"] == "http"] == [], "2xx, /healthz and public 4xx stay at debug"

    client.get("/admin")  # not logged in: auth route answers 4xx or a redirect
    bad = client.post("/admin/login", data={"password": "wrong", "totp": "000000"})
    assert bad.status_code >= 400
    recs = [r for r in logs() if r["event"] == "http"]
    assert recs and all(r["level"] == "warn" and r["route"].startswith("/admin") for r in recs), recs
    for r in recs:
        _check_format(r)
        assert "path" not in r and "client_ip" not in r


def test_request_id_is_echoed_or_generated(client):
    given = client.get("/healthz", headers={"X-Request-Id": "req-42"})
    assert given.headers["X-Request-Id"] == "req-42"
    made = client.get("/healthz", headers={"X-Request-Id": "bad id\nwith newline"})
    assert re.fullmatch(r"[0-9a-f]{32}", made.headers["X-Request-Id"])


def test_log_level_debug_logs_every_request(client, logs, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    logs.install()
    logs()
    client.get("/healthz")
    recs = [r for r in logs() if r["event"] == "http"]
    assert [(r["level"], r["route"], r["status"]) for r in recs] == [("debug", "/healthz", 200)]


def test_security_event_has_route_and_req_id(client, logs):
    logs()
    client.post("/admin/login", data={"password": "wrong", "totp": "000000"}, headers={"X-Request-Id": "sec-1"})
    ev = [r for r in logs() if r["event"].startswith("admin_")]
    assert ev, "a failed admin login is a security event"
    for r in ev:
        _check_format(r)
        assert r["level"] == "warn" and r["route"] == "/admin/login" and r["req_id"] == "sec-1"


def test_slow_request_is_logged_at_warn_and_a_fast_one_stays_at_debug(settings, logs, monkeypatch):
    import time

    import app.main as main
    monkeypatch.setattr(main, "SLOW_MS", 200)
    app = create_app(settings)

    @app.get("/slow/{n}")
    def slow(n: int):
        time.sleep(0.3)
        return {"n": n}

    @app.get("/fast/{n}")
    def fast(n: int):
        return {"n": n}

    with TestClient(app, base_url="https://testserver") as c:
        logs()  # drop the startup line
        assert c.get("/fast/1").status_code == 200
        assert [r for r in logs() if r["event"] == "http"] == [], "a fast 2xx stays at debug"
        assert c.get("/slow/2?q=x").status_code == 200
    recs = [r for r in logs() if r["event"] == "http"]
    assert len(recs) == 1, recs
    r = recs[0]
    _check_format(r)
    assert r["level"] == "warn" and r["status"] == 200 and r["route"] == "/slow/{n}"
    assert r["dur_ms"] >= 300 and "q=x" not in json.dumps(r)


def _import_lines(recs):
    return {r["event"]: r for r in recs if r["event"].startswith("import_")}


def test_import_job_logs_start_and_done_with_dur_ms_and_counts(app, logs):
    import httpx

    from app import importer
    from tests.test_importer import handler_factory
    s = app.state.settings
    client = importer.make_client(httpx.MockTransport(handler_factory([])))
    summary = importer.run_logged(s.db_path, s.images_dir, enrich=False, images=False, client=client, actor="test")
    lines = _import_lines(logs())
    assert set(lines) == {"import_start", "import_done"}, lines
    for r in lines.values():
        _check_format(r)
        assert r["level"] == "info"
    assert lines["import_start"]["actor"] == "test"
    done = lines["import_done"]
    assert isinstance(done["dur_ms"], int) and done["dur_ms"] >= 0
    assert done["created"] == summary["created"] == 4
    assert {k: done[k] for k in summary} == summary, "every count of the summary is on the done line"


def test_import_job_failure_logs_one_error_line_with_dur_ms(app, logs):
    import httpx

    from app import importer
    s = app.state.settings
    client = importer.make_client(httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        importer.run_logged(s.db_path, s.images_dir, enrich=False, images=False, client=client)
    lines = _import_lines(logs())
    assert set(lines) == {"import_start", "import_failed"}, lines
    failed = lines["import_failed"]
    _check_format(failed)
    assert failed["level"] == "error" and failed["msg"] == "wikidata import failed"
    assert isinstance(failed["dur_ms"], int) and failed["dur_ms"] >= 0
    assert failed["error"].startswith("HTTPStatusError")
