"""FastAPI application factory."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, security
from .config import Settings, load_settings
from .ratelimit import RateLimiter
from .web import VISITOR_COOKIE, app_version, client_ip, log, make_templates, route_of, setup_logging

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")
VISITOR_MAX_AGE = 2 * 365 * 86400
REQ_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
SLOW_MS = 1000
access = logging.getLogger("plats.access")


def _data_dir_writable(path: Path) -> bool:
    probe = path / f".write-probe-{os.getpid()}"
    try:
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def _access_level(path: str, status: int, ms: float) -> int:
    """AGENTS.md section 11: requests are logged at info+ only when they matter (Traefik has the rest):
    5xx (error), 4xx on the admin (auth) routes and anything slower than 1 s (warn); the rest at debug."""
    if status >= 500:
        return logging.ERROR
    if (400 <= status < 500 and path.startswith("/admin")) or ms > SLOW_MS:
        return logging.WARNING
    return logging.DEBUG


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.migrate(settings.db_path)
        conn = db.connect(settings.db_path)
        try:
            security.purge_used_tokens(conn)
        finally:
            conn.close()
        log.info(json.dumps({"event": "startup", "msg": "plats started", "version": app_version(),
                             "port": int(os.environ.get("PORT", "8000")),
                             "data_dir_writable": _data_dir_writable(settings.data_dir),
                             "admin_configured": settings.admin_configured,
                             "config": sorted(k for k in os.environ if k.startswith("PLATS_") or k == "LOG_LEVEL")}))
        yield
        log.info(json.dumps({"event": "shutdown", "msg": "plats stopping"}))

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.limiter = RateLimiter()
    app.state.templates = make_templates()
    app.state.visitor_signer = security.VisitorSigner(settings.secret_key)
    db.migrate(settings.db_path)  # also at import time so the app is usable without lifespan

    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.middleware("http")
    async def plumbing(request: Request, call_next):
        started = time.perf_counter()
        given = request.headers.get("x-request-id", "")
        request.state.req_id = given if REQ_ID.match(given) else uuid.uuid4().hex
        ip = client_ip(request, settings)
        request.state.client_ip = ip
        request.state.ip_hash = security.hash_ip(ip, settings.ip_salt)
        signer = app.state.visitor_signer
        visitor = signer.loads(request.cookies.get(VISITOR_COOKIE))
        new_visitor = visitor is None
        request.state.visitor_id = visitor or signer.new_id()
        path = request.url.path
        try:
            response = await call_next(request)
        except Exception:
            access.error(json.dumps(_access_record(request, path, 500, started)), exc_info=True)
            raise
        if new_visitor and not path.startswith(("/static", "/admin", "/healthz", "/media")):
            response.set_cookie(VISITOR_COOKIE, signer.dumps(request.state.visitor_id), max_age=VISITOR_MAX_AGE,
                                httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()")
        if not path.startswith(("/static", "/media", "/categories.css")):
            h.setdefault("Cache-Control", "no-store")
        h.setdefault("X-Request-Id", request.state.req_id)
        record = _access_record(request, path, response.status_code, started)
        level = logging.DEBUG if path == "/healthz" else _access_level(path, response.status_code, record["dur_ms"])
        if access.isEnabledFor(level):
            access.log(level, json.dumps(record))
        return response

    def _access_record(request: Request, path: str, status: int, started: float) -> dict:
        record = {"event": "http", "msg": f"{request.method} {route_of(request)} {status}",
                  "req_id": request.state.req_id, "method": request.method, "route": route_of(request),
                  "status": status, "dur_ms": round((time.perf_counter() - started) * 1000, 1),
                  "ip_hash": request.state.ip_hash}
        if settings.log_client_ip:
            record["client_ip"] = request.state.client_ip
        return record

    from . import routes_admin, routes_public  # noqa: E402 - avoid import cycles

    app.include_router(routes_public.router)
    app.include_router(routes_admin.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if request.url.path.startswith("/api/") or exc.status_code not in (403, 404, 429):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        messages = {404: "Cette page n'existe pas (ou plus).", 403: "Accès refusé.",
                    429: "Doucement ! Trop de requêtes, réessayez dans un instant."}
        return app.state.templates.TemplateResponse(
            request, "error.html", {"status": exc.status_code, "message": messages[exc.status_code]},
            status_code=exc.status_code)

    return app


class HeadAsGet:
    """Serve HEAD like GET (uptime monitors, `curl -I`); the server drops the body for HEAD."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "HEAD":
            scope = dict(scope, method="GET")

            async def send_no_body(message):
                if message["type"] == "http.response.body":
                    message = {**message, "body": b""}
                await send(message)

            return await self.app(scope, receive, send_no_body)
        return await self.app(scope, receive, send)


def run():  # uvicorn factory entrypoint: `uvicorn --factory app.main:run`
    setup_logging()
    return HeadAsGet(create_app())

