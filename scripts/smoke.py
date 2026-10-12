#!/usr/bin/env python3
"""End-to-end smoke check of a running plats instance (stdlib only).

    docker compose exec plats python scripts/smoke.py                    # inside the container
    python3 scripts/smoke.py https://plats.hosh.it --min-dishes 1000     # from anywhere

Proves more than "it answers 200": a question is served with a signed token, an answer is
recorded and returns the distribution, the token cannot be replayed, the proposal form carries
its anti-bot challenge, search/stats/dish pages render, security headers are present and no page
references an external origin. It records ONE "je ne connais pas" (skip) answer — no real vote.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookies: dict[str, str] = {}

    def request(self, path: str, data: bytes | None = None, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, data=data, headers={
            "User-Agent": "plats-smoke/1", **(headers or {}),
            **({"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())} if self.cookies else {})})
        try:
            resp = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as err:
            resp = err
        for raw in resp.headers.get_all("Set-Cookie") or []:
            name, _, rest = raw.partition("=")
            self.cookies[name.strip()] = rest.split(";", 1)[0]
        body = resp.read().decode("utf-8", "replace")
        return resp.status, resp.headers, body

    def json(self, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        status, headers, body = self.request(path, data, {"Content-Type": "application/json"} if data else None)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    ap.add_argument("--min-dishes", type=int, default=1, help="expected minimum number of active dishes")
    args = ap.parse_args()
    s = Session(args.base)

    print("health")
    status, health = s.json("/healthz")
    check(status == 200 and health.get("status") == "ok", f"/healthz ok ({health})")
    check(health.get("categories", 0) >= 9, "9 active categories")
    check(health.get("active_dishes", 0) >= args.min_dishes, f"≥ {args.min_dishes} active dishes")

    print("home + headers")
    status, headers, html = s.request("/")
    check(status == 200, "/ returns 200")
    check("script-src 'self'" in headers.get("Content-Security-Policy", ""), "strict CSP header")
    check(headers.get("X-Content-Type-Options") == "nosniff", "nosniff header")
    check("plats_v" in s.cookies, "anonymous visitor cookie issued")
    set_cookie = " ".join(headers.get_all("Set-Cookie") or [])
    check("HttpOnly" in set_cookie, "visitor cookie is HttpOnly")
    check('id="answer-form"' in html and 'name="token"' in html, "question rendered with a signed token")

    print("question loop")
    status, q = s.json("/api/question")
    nxt = q.get("next")
    check(status == 200 and nxt and nxt.get("token"), "/api/question serves a dish + token")
    if nxt:
        status, early = s.json("/api/vote", {"token": nxt["token"], "choice": "skip"})
        check(status == 400 and early.get("error") == "too_fast", "answer before the think time is refused")
        time.sleep(0.8)
        status, res = s.json("/api/vote", {"token": nxt["token"], "choice": "skip"})
        check(status == 200 and res.get("ok") and res.get("skipped"), "answer recorded (skip)")
        check("distribution" in res and "rows" in res["distribution"], "distribution returned")
        status, again = s.json("/api/vote", {"token": nxt["token"], "choice": "skip"})
        check(status == 409, "token replay refused (single-use)")
        slug = res.get("dish", {}).get("slug")
        if slug:
            status, _, page = s.request(f"/plat/{slug}")
            check(status == 200 and "Ce que les gens en pensent" in page, f"/plat/{slug} renders")

    print("pages")
    status, _, page = s.request("/proposer")
    check(status == 200 and 'name="form_token"' in page and "data-difficulty" in page and 'name="website"' in page,
          "/proposer has token, proof-of-work and honeypot")
    status, sim = s.json("/api/similaires?nom=quiche")
    check(status == 200 and "items" in sim, "look-alike API answers")
    status, _, page = s.request("/plats?q=soupe&tri=votes")
    check(status == 200 and "dish-list" in page, "search page renders")
    status, _, page = s.request("/stats")
    check(status == 200 and "Combien de temps pour tout classer" in page, "stats page renders")
    status, _, page = s.request("/a-propos")
    check(status == 200, "/a-propos renders")
    status, _, page = s.request("/admin/login")
    check(status == 200 and "TOTP" in page, "admin login page renders")
    status, headers, page = s.request("/admin/export.xlsx")  # redirected to the login page
    check("attachment" not in headers.get("Content-Disposition", "") and "TOTP" in page, "export is not public")
    for path in ("/static/app.js", "/static/style.css", "/categories.css"):
        status, _, _ = s.request(path)
        check(status == 200, f"{path} served locally")

    external = set()
    for path in ("/", "/proposer", "/plats", "/stats", "/a-propos"):
        _, _, page = s.request(path)
        for url in re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="(https?://[^"]+)"', page):
            external.add(url)
    check(not external, f"no external scripts/styles/images ({sorted(external)[:3]})")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
