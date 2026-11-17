"""scripts/monitor_logs.py is the e2e log gate (--fail-on-error): it must read the logging standard's
lines (route, dur_ms, msg, err as a dict) and still read the older ones (path, ms, message, exc)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "monitor_logs.py"

NEW_5XX = json.dumps({"ts": "2026-11-17T10:00:00.000Z", "level": "error", "service": "plats", "version": "v1",
                      "event": "http", "msg": "GET /boom/{n} 500", "req_id": "r1", "method": "GET",
                      "route": "/boom/{n}", "status": 500, "dur_ms": 12.5,
                      "err": {"type": "RuntimeError", "message": "kaboom", "stack": "Traceback...\nRuntimeError: kaboom"}})
OLD_5XX = json.dumps({"ts": "2026-11-16T10:00:00", "level": "error", "event": "http", "message": "old",
                      "method": "POST", "path": "/admin/import", "status": 502, "ms": 40,
                      "exc": "Traceback...\nValueError: old-boom"})
NEW_OK = json.dumps({"ts": "2026-11-17T10:00:01.000Z", "level": "warn", "service": "plats", "version": "v1",
                     "event": "http", "msg": "GET / 200", "method": "GET", "route": "/", "status": 200, "dur_ms": 1500.0})
IMPORT_FAILED = json.dumps({"ts": "2026-11-17T10:00:02.000Z", "level": "error", "service": "plats", "version": "v1",
                            "event": "import_failed", "msg": "wikidata import failed", "dur_ms": 3,
                            "error": "HTTPStatusError: 503"})


@pytest.fixture(scope="module")
def mon():
    import importlib.util
    spec = importlib.util.spec_from_file_location("monitor_logs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(lines, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input="\n".join(lines) + "\n",
                          capture_output=True, text=True, timeout=30)


def test_new_format_5xx_with_err_dict(mon):
    st = mon.Stats()
    alerts = st.feed(NEW_5XX)
    assert "HTTP 500 on GET /boom/{n}" in alerts and "ERROR: GET /boom/{n} 500" in alerts
    assert st.status["5xx"] == 1 and st.paths_err["500 GET /boom/{n}"] == 1 and st.latencies == [12.5]
    (err,) = st.errors
    assert "GET /boom/{n} 500" in err and "RuntimeError: kaboom" in err
    assert st.failed


def test_old_format_5xx_with_exc_still_parses(mon):
    st = mon.Stats()
    alerts = st.feed(OLD_5XX)
    assert "HTTP 502 on POST /admin/import" in alerts and "ERROR: old" in alerts
    assert st.paths_err["502 POST /admin/import"] == 1 and st.latencies == [40.0]
    (err,) = st.errors
    assert "ValueError: old-boom" in err


def test_slow_request_is_not_an_error(mon):
    st = mon.Stats()
    assert st.feed(NEW_OK) == []
    assert st.latencies == [1500.0] and not st.errors and not st.failed


def test_fail_on_error_exit_codes():
    bad = run([NEW_OK, NEW_5XX], "--fail-on-error")
    assert bad.returncode == 1 and "PROBLEMS FOUND" in bad.stdout and "RuntimeError: kaboom" in bad.stdout
    old = run([OLD_5XX], "--fail-on-error")
    assert old.returncode == 1 and "ValueError: old-boom" in old.stdout
    ok = run([NEW_OK], "--fail-on-error")
    assert ok.returncode == 0 and "status: OK" in ok.stdout
    # the e2e causes an import failure on purpose and skips that one line (e2e/run.sh)
    ignored = run([NEW_OK, IMPORT_FAILED], "--fail-on-error", "--ignore", "wikidata import failed")
    assert ignored.returncode == 0 and "import_failed" not in ignored.stdout
    seen = run([NEW_OK, IMPORT_FAILED], "--fail-on-error")
    assert seen.returncode == 1 and "wikidata import failed" in seen.stdout
