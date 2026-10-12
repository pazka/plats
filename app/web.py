"""Request plumbing shared by public and admin routes."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import __version__, db
from .ratelimit import parse_rules

log = logging.getLogger("plats")
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


def rate_ok(request: Request, conn, action: str, extra_keys: tuple[str, ...] = ()) -> bool:
    """Apply the admin-tunable rule set `rl_<action>` per visitor AND per IP hash."""
    rules = parse_rules(db.get_setting(conn, f"rl_{action}"))
    keys = [f"{action}:ip:{request.state.ip_hash}"]
    if getattr(request.state, "visitor_id", None):
        keys.append(f"{action}:v:{request.state.visitor_id}")
    keys.extend(f"{action}:{k}" for k in extra_keys)
    ok = request.app.state.limiter.hit(keys, rules)
    if not ok:
        security_event(request, "rate_limited", action=action)
    return ok


def security_event(request: Request, event: str, **fields) -> None:
    """Structured JSON line on stdout (CrowdSec-parsable later)."""
    record = {"event": event, "path": request.url.path, "ip_hash": getattr(request.state, "ip_hash", ""),
              **fields}
    if request.app.state.settings.log_client_ip:
        record["client_ip"] = getattr(request.state, "client_ip", "")
    log.warning(json.dumps(record, ensure_ascii=False))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        try:
            payload = json.loads(msg) if msg.startswith("{") else {"message": msg}
        except ValueError:
            payload = {"message": msg}
        base = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "level": record.levelname.lower(), "logger": record.name}
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps({**base, **payload}, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
