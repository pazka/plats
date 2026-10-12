"""FastAPI application factory."""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, security
from .config import Settings, load_settings
from .ratelimit import RateLimiter
from .web import VISITOR_COOKIE, client_ip, log, make_templates, setup_logging

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")
VISITOR_MAX_AGE = 2 * 365 * 86400


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
        log.info(json.dumps({"event": "startup", "data_dir": str(settings.data_dir),
                             "admin_configured": settings.admin_configured}))
        yield

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
        ip = client_ip(request, settings)
        request.state.client_ip = ip
        request.state.ip_hash = security.hash_ip(ip, settings.ip_salt)
        signer = app.state.visitor_signer
        visitor = signer.loads(request.cookies.get(VISITOR_COOKIE))
        new_visitor = visitor is None
        request.state.visitor_id = visitor or signer.new_id()
        response = await call_next(request)
        path = request.url.path
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
        if path != "/healthz":
            record = {"event": "http", "method": request.method, "path": path, "status": response.status_code,
                      "ms": round((time.perf_counter() - started) * 1000, 1), "ip_hash": request.state.ip_hash}
            if settings.log_client_ip:
                record["client_ip"] = ip
            logging.getLogger("plats.access").info(json.dumps(record))
        return response

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


def run() -> FastAPI:  # uvicorn factory entrypoint: `uvicorn --factory app.main:run`
    setup_logging()
    return create_app()

