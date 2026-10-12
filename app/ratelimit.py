"""In-memory sliding-window rate limiter (the app runs as a single process)."""
from __future__ import annotations

import re
import threading
import time
from collections import deque

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_RULE = re.compile(r"^\s*(\d+)\s*/\s*(\d*)\s*([smhd])\s*$")


def parse_rules(spec: str) -> list[tuple[int, int]]:
    """'3/3s,300/1h' -> [(3, 3), (300, 3600)]. Invalid parts are ignored."""
    rules = []
    for part in (spec or "").split(","):
        m = _RULE.match(part)
        if not m:
            continue
        count, mult, unit = int(m.group(1)), int(m.group(2) or 1), m.group(3)
        if count > 0 and mult > 0:
            rules.append((count, mult * _UNITS[unit]))
    return rules


def validate_rules(spec: str) -> bool:
    parts = [p for p in (spec or "").split(",") if p.strip()]
    return bool(parts) and len(parse_rules(spec)) == len(parts)


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._last_prune = clock()

    def hit(self, keys: list[str], rules: list[tuple[int, int]]) -> bool:
        """Record one event for every key if all keys are under every rule.
        Returns False (and records nothing) when any limit would be exceeded."""
        if not rules:
            return True
        now = self._clock()
        longest = max(window for _, window in rules)
        with self._lock:
            self._maybe_prune(now)
            for key in keys:
                q = self._hits.get(key)
                if q is None:
                    continue
                while q and now - q[0] > longest:
                    q.popleft()
                for count, window in rules:
                    recent = sum(1 for t in q if now - t < window) if len(q) >= count else 0
                    if recent >= count:
                        return False
            for key in keys:
                self._hits.setdefault(key, deque()).append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < 300:
            return
        self._last_prune = now
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] > 86400]:
            del self._hits[key]
