"""scripts/review_dishes.py against a fake `claude` (no network, no Docker): hardening flags,
scrubbed environment, throwaway HOME, retries, canary, circuit breaker, fallbacks, resume."""
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("review_dishes", ROOT / "scripts" / "review_dishes.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)

FAKE = r'''#!PYTHON
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode")).read().strip()
prompt = sys.stdin.read()
n_path = os.path.join(here, "calls")
n = int(open(n_path).read()) + 1 if os.path.exists(n_path) else 1
open(n_path, "w").write(str(n))
with open(os.path.join(here, "log.jsonl"), "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(),
                         "home_files": os.listdir(os.environ.get("HOME", "/nonexistent")),
                         "prompt": prompt}) + "\n")
title = json.loads(prompt.split("\n", 1)[1])["title"]
def ok():
    kind = "fromage_simple" if "Brie" in title else "plat"
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.001,
                      "structured_output": {"kind": kind, "keep": True, "title": title,
                                            "description": "Une description.", "reason": "test"}}))
if mode == "ok":
    ok()
elif mode == "auth":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 401,
                      "result": "Invalid API key · Fix external API key"}))
elif mode == "flaky" and n % 2 == 1:
    print(json.dumps({"type": "result", "is_error": True, "api_error_status": 529, "result": "Overloaded"}))
elif mode == "flaky":
    ok()
elif mode == "dies_after_canary" and n > 1:
    print(json.dumps({"type": "result", "is_error": True, "api_error_status": 529, "result": "Overloaded"}))
elif mode == "dies_after_canary":
    ok()
elif mode == "garbage":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "structured_output": {"kind": "dessert", "keep": True, "title": "", "description": "",
                                            "reason": ""}}))
'''

ITEMS = [
    {"id": 1, "key": "wikidata:Q1", "source": "wikidata", "title": "Brie de Meaux", "text": "fromage à pâte molle", "url": ""},
    {"id": 2, "key": "cuisine-facile:/t", "source": "cuisine-facile", "title": "Tartiflette",
     "text": "Gratin.\nIngrédients : reblochon", "url": "https://cuisine-facile.com/t"},
    {"id": 3, "key": "wikipedia:3", "source": "wikipedia", "title": "Machin", "text": "un truc", "url": ""},
]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE.replace("PYTHON", sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    (bindir / "mode").write_text("ok")
    cand = tmp_path / "candidates.jsonl"
    cand.write_text("".join(json.dumps(i) + "\n" for i in ITEMS))
    monkeypatch.setattr(rd.time, "sleep", lambda s: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("PLATS_PLANTED_SECRET", "must-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak-either")

    class Fake:
        dir = bindir
        candidates = str(cand)
        output = str(tmp_path / "reviews.jsonl")

        def mode(self, m):
            (bindir / "mode").write_text(m)

        def calls(self):
            p = bindir / "log.jsonl"
            return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

        def out(self):
            p = Path(self.output)
            return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

        def run(self, *extra):
            return rd.main([self.candidates, "-o", self.output, "--sandbox", "process", "--claude",
                            str(exe), "-j", "2", *extra])
    return Fake()


def test_success_with_hardened_flags_scrubbed_env_and_throwaway_home(fake):
    assert fake.run() == 0
    out = sorted(fake.out(), key=lambda r: r["id"])
    assert [(r["id"], r["kind"], r["model"]) for r in out] == [
        (1, "fromage_simple", "haiku"), (2, "plat", "haiku"), (3, "plat", "haiku")]
    calls = fake.calls()
    assert len(calls) == 3
    for call in calls:
        argv = call["argv"]
        for flag in ("-p", "--bare", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
                     "--json-schema"):
            assert flag in argv
        assert argv[argv.index("--tools") + 1] == ""
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
        assert argv[argv.index("--max-budget-usd") + 1] == "0.05"
        assert argv[argv.index("--fallback-model") + 1] == "sonnet"
        # the dish goes through stdin, never argv
        assert "Fiche à relire" in call["prompt"] and not any("Fiche à relire" in a for a in argv)
        env = call["env"]
        assert "PLATS_PLANTED_SECRET" not in env and "AWS_SECRET_ACCESS_KEY" not in env
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert set(env) - {"PYTHONIOENCODING"} <= {"PATH", "HOME", "TMPDIR", "LANG", "ANTHROPIC_API_KEY",
                                                   "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy",
                                                   *rd.NOISE_ENV, "LC_CTYPE", "PWD", "SHLVL", "_"}
        assert env["HOME"] != os.path.expanduser("~") and call["cwd"] == env["HOME"]
        assert call["home_files"] == []  # nothing of the user's: no settings, hooks, CLAUDE.md
        assert not os.path.exists(env["HOME"])  # removed afterwards
    assert len({c["env"]["HOME"] for c in calls}) == 3  # a fresh one per reviewer


def test_resume_skips_dishes_already_reviewed_by_claude(fake):
    fake.run("--limit", "1")
    assert len(fake.out()) == 1
    fake.run()
    assert sorted(r["id"] for r in fake.out()) == [1, 2, 3] and len(fake.calls()) == 3


def test_auth_failure_falls_back_to_rules_after_the_canary(fake):
    fake.mode("auth")
    assert fake.run() == 0
    assert len(fake.calls()) == 1  # the canary only: no fleet launched on a dead credential
    out = {r["id"]: r for r in fake.out()}
    assert set(out) == {1, 2} and all(r["model"] == "rules" for r in out.values())  # 3 stays for Claude
    assert out[1]["kind"] == "fromage_simple" and out[2]["kind"] == "plat"


def test_no_fallback_means_exit_2(fake):
    fake.mode("auth")
    assert fake.run("--fallback", "none") == 2 and fake.out() == []


def test_missing_credentials_never_use_the_personal_login(fake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert fake.run() == 0
    assert fake.calls() == [] and all(r["model"] == "rules" for r in fake.out())


def test_oauth_token_runs_without_bare(fake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat-test")
    assert fake.run() == 0
    call = fake.calls()[0]
    assert "--bare" not in call["argv"] and call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "oat-test"
    assert "ANTHROPIC_API_KEY" not in call["env"]


def test_transient_errors_are_retried(fake):
    fake.mode("flaky")
    assert fake.run("-j", "1") == 0
    assert len(fake.out()) == 3 and all(r["model"] == "haiku" for r in fake.out())
    assert len(fake.calls()) == 6  # each dish: overloaded once, then fine


def test_circuit_breaker_stops_the_fleet_and_falls_back(fake):
    fake.mode("dies_after_canary")
    assert fake.run("-j", "1", "--breaker", "1", "--attempts", "2") == 0
    out = {r["id"]: r for r in fake.out()}
    assert out[1]["model"] == "haiku"  # the canary
    assert len(fake.calls()) == 3  # canary + one dish (2 attempts), then the breaker opens
    assert out[2]["model"] == "rules" and 3 not in out
    errors = Path(fake.output + ".errors.jsonl").read_text()
    assert "Overloaded" in errors


def test_invalid_answers_are_rejected(fake):
    fake.mode("garbage")
    assert fake.run() == 0
    assert all(r["model"] == "rules" for r in fake.out())  # the canary's answer was refused


def test_docker_sandbox_fails_closed(fake, monkeypatch):
    monkeypatch.setenv("PATH", str(fake.dir))  # no docker here
    code = rd.main([fake.candidates, "-o", fake.output, "--fallback", "none"])
    assert code == 2 and fake.calls() == []  # never silently downgraded to --sandbox process


def test_rules_engine_needs_nothing(fake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert rd.main([fake.candidates, "-o", fake.output, "--engine", "rules"]) == 0
    assert {r["id"] for r in fake.out()} == {1, 2}


def test_schema_and_policy_kinds_match_the_app():
    from app import reviews
    assert rd.SCHEMA["properties"]["kind"]["enum"] == list(reviews.KINDS) == list(reviews.POLICY)
