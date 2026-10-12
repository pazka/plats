#!/usr/bin/env python3
"""Summarise / watch plats JSON logs (stdlib only).

One-shot report of the last hour:
    docker compose logs --no-log-prefix --since 1h plats | python3 scripts/monitor_logs.py
Live watch (alerts printed immediately, summary every 5 min):
    docker compose logs -f --no-log-prefix --since 1m plats | python3 scripts/monitor_logs.py --follow
CI / scripted check (exit 1 on any error line or HTTP 5xx):
    docker logs plats 2>&1 | python3 scripts/monitor_logs.py --fail-on-error

Alerts: level error/critical, HTTP 5xx, tracebacks, admin login lockouts, CSRF failures,
bursts of rate limiting. Raw IPs never appear in the logs (only ip_hash), unless
PLATS_LOG_CLIENT_IP=1.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict

ALERT_EVENTS = {"admin_login_locked", "admin_csrf_failed", "import_failed"}


class Stats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.lines = 0
        self.non_json = 0
        self.status = Counter()
        self.paths_err = Counter()
        self.latencies: list[float] = []
        self.events = Counter()
        self.reasons = defaultdict(Counter)
        self.errors: list[str] = []
        self.ip_hashes = Counter()
        self.started = time.time()

    def feed(self, raw: str) -> list[str]:
        """Account one line; return alert messages for it (possibly empty)."""
        line = raw.strip()
        if not line:
            return []
        self.lines += 1
        brace = line.find("{")
        if brace < 0:
            self.non_json += 1
            if "Traceback" in line or "Error" in line:
                self.errors.append(line[:300])
                return [f"non-JSON error line: {line[:200]}"]
            return []
        try:
            rec = json.loads(line[brace:])
        except ValueError:
            self.non_json += 1
            return []
        alerts = []
        level = str(rec.get("level", "")).lower()
        event = rec.get("event", "")
        if event == "http":
            status = int(rec.get("status", 0) or 0)
            self.status[f"{status // 100}xx"] += 1
            if status >= 400:
                self.paths_err[f"{status} {rec.get('method', '')} {rec.get('path', '')}"] += 1
            if status >= 500:
                alerts.append(f"HTTP {status} on {rec.get('method')} {rec.get('path')}")
            try:
                self.latencies.append(float(rec.get("ms", 0)))
            except (TypeError, ValueError):
                pass
            if rec.get("ip_hash"):
                self.ip_hashes[rec["ip_hash"]] += 1
        elif event:
            self.events[event] += 1
            for key in ("reason", "action", "stage"):
                if rec.get(key):
                    self.reasons[event][str(rec[key])] += 1
            if event in ALERT_EVENTS:
                alerts.append(f"{event}: {json.dumps({k: v for k, v in rec.items() if k not in ('ts', 'logger')})}")
        if level in ("error", "critical") or rec.get("exc"):
            msg = rec.get("message") or event or "error"
            self.errors.append(f"{rec.get('ts', '')} {msg} {str(rec.get('exc', ''))[-300:]}".strip())
            alerts.append(f"{level.upper() or 'ERROR'}: {msg}")
        if event == "rate_limited" and self.events["rate_limited"] in (20, 100, 500):
            alerts.append(f"rate limiting is firing a lot ({self.events['rate_limited']} so far): {dict(self.reasons['rate_limited'])}")
        return alerts

    @property
    def failed(self) -> bool:
        return bool(self.errors) or self.status.get("5xx", 0) > 0

    def report(self) -> str:
        out = [f"== plats log summary: {self.lines} lines ({self.non_json} non-JSON) =="]
        total = sum(self.status.values())
        out.append(f"requests: {total}  " + "  ".join(f"{k}: {v}" for k, v in sorted(self.status.items())))
        if self.latencies:
            lat = sorted(self.latencies)
            p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]  # noqa: E731
            out.append(f"latency ms: p50 {p(0.5):.1f}  p95 {p(0.95):.1f}  max {lat[-1]:.1f}")
        out.append(f"distinct ip hashes: {len(self.ip_hashes)}"
                   + (f"  (busiest: {self.ip_hashes.most_common(1)[0][1]} req)" if self.ip_hashes else ""))
        if self.paths_err:
            out.append("4xx/5xx by route: " + ", ".join(f"{k} ×{v}" for k, v in self.paths_err.most_common(8)))
        if self.events:
            parts = []
            for event, n in self.events.most_common():
                detail = dict(self.reasons[event]) if self.reasons[event] else ""
                parts.append(f"{event} ×{n}{' ' + json.dumps(detail) if detail else ''}")
            out.append("events: " + "; ".join(parts))
        if self.errors:
            out.append(f"ERRORS ({len(self.errors)}):")
            out.extend("  " + e for e in self.errors[-10:])
        out.append("status: " + ("PROBLEMS FOUND" if self.failed else "OK — no errors, no 5xx"))
        return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--follow", action="store_true", help="print alerts live + a summary every --interval s")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--fail-on-error", action="store_true", help="exit 1 if any error line or 5xx was seen")
    ap.add_argument("--ignore", action="append", default=[], metavar="REGEX",
                    help="skip lines matching REGEX (e.g. an error you caused on purpose)")
    args = ap.parse_args()
    ignore = [re.compile(r) for r in args.ignore]
    stats = Stats()
    last = time.time()
    for raw in sys.stdin:
        if any(r.search(raw) for r in ignore):
            continue
        for alert in stats.feed(raw):
            if args.follow:
                print(f"[ALERT {time.strftime('%H:%M:%S')}] {alert}", flush=True)
        if args.follow and time.time() - last >= args.interval:
            print(stats.report(), flush=True)
            last = time.time()
    print(stats.report())
    return 1 if (args.fail_on_error and stats.failed) else 0


if __name__ == "__main__":
    sys.exit(main())
