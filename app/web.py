"""Request plumbing shared by public and admin routes."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import __version__, db
from .ratelimit import parse_rules

log = logging.getLogger("plats")
SERVICE = "plats"
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "warning": logging.WARNING,
          "error": logging.ERROR}
VISITOR_COOKIE = "plats_v"
TEMPLATES_DIR = Path(__file__).parent / "templates"


def client_ip(request: Request, settings) -> str:
    """The socket peer, unless it is a trusted proxy (Traefik): then the right-most
    X-Forwarded-For address that is not itself a trusted proxy."""
    peer = request.client.host if request.client else ""
    if not settings.is_trusted_proxy(peer):
        return peer
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    for addr in reversed(forwarded):
        if not settings.is_trusted_proxy(addr):
            return addr
    return forwarded[0] if forwarded else peer


def get_conn(request: Request):
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def rate_ok(request: Request, conn, action: str) -> bool:
    """Apply the admin-tunable rules `rl_<action>` per visitor and `rl_<action>_ip` per IP hash."""
    checks = [(f"{action}:ip:{request.state.ip_hash}", parse_rules(db.get_setting(conn, f"rl_{action}_ip")))]
    if getattr(request.state, "visitor_id", None):
        checks.append((f"{action}:v:{request.state.visitor_id}", parse_rules(db.get_setting(conn, f"rl_{action}"))))
    ok = request.app.state.limiter.hit_many(checks)
    if not ok:
        security_event(request, "rate_limited", action=action)
    return ok


def security_event(request: Request, event: str, **fields) -> None:
    """Structured JSON line on stdout (CrowdSec-parsable later)."""
    record = {"event": event, "route": route_of(request), "req_id": getattr(request.state, "req_id", ""),
              "ip_hash": getattr(request.state, "ip_hash", ""), **fields}
    if request.app.state.settings.log_client_ip:
        record["client_ip"] = getattr(request.state, "client_ip", "")
    log.warning(json.dumps(record, ensure_ascii=False))


def route_of(request: Request) -> str:
    """The matched route template (`/admin/plats/{dish_id}`), never the raw path or query string."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def app_version() -> str:
    """The release tag baked into the image (PLATS_VERSION), else the package version."""
    return os.environ.get("PLATS_VERSION") or __version__


class JsonFormatter(logging.Formatter):
    """One JSON object per line (hosh-management AGENTS.md section 11): ts, level, service, version,
    event, msg, then the record's own fields; `err` {type, message, stack} when an exception is attached."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        try:
            payload = json.loads(msg) if msg.startswith("{") else {"msg": msg}
        except ValueError:
            payload = {"msg": msg}
        if not isinstance(payload, dict):
            payload = {"msg": msg}
        if "message" in payload and "msg" not in payload:
            payload["msg"] = payload.pop("message")
        level = {"warning": "warn", "critical": "error"}.get(record.levelname.lower(), record.levelname.lower())
        base = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "level": level, "service": SERVICE, "version": app_version(), "logger": record.name}
        out = {**base, **payload}
        out.setdefault("event", "log")
        out.setdefault("msg", out["event"])
        if record.exc_info and record.exc_info[0] is not None:
            etype, exc, tb = record.exc_info
            out["err"] = {"type": etype.__name__, "message": str(exc)[:500],
                          "stack": "".join(traceback.format_exception(etype, exc, tb))[-4000:]}
        return json.dumps(out, ensure_ascii=False)


def log_level() -> int:
    """LOG_LEVEL env (debug|info|warn|error), default info."""
    return LEVELS.get(os.environ.get("LOG_LEVEL", "info").strip().lower(), logging.INFO)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(log_level())
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(max(logging.WARNING, log_level()))


def fmt_dt(ts, with_time: bool = True) -> str:
    if not ts:
        return ""
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return dt.strftime("%d/%m/%Y %H:%M UTC" if with_time else "%d/%m/%Y")


def fmt_int(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["dt"] = fmt_dt
    templates.env.filters["num"] = fmt_int
    templates.env.globals["version"] = __version__
    templates.env.globals["now"] = time.time
    return templates
