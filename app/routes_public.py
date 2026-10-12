"""Public pages and the JSON API used by static/app.js."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import db, security, services
from .rewards import reward
from .web import get_conn, rate_ok, security_event

router = APIRouter()
MEDIA_NAME = re.compile(r"^Q\d+\.(jpg|png|webp)$")
SLUG_RE = re.compile(r"^[a-z0-9-]{1,80}$")


def render(request: Request, name: str, ctx: dict | None = None, status: int = 200):
    return request.app.state.templates.TemplateResponse(request, name, ctx or {}, status_code=status)


def _settings(request: Request):
    return request.app.state.settings


# ------------------------------------------------------------------ question loop

@router.get("/")
def index(request: Request, apres: str = "", conn=Depends(get_conn)):
    visitor = request.state.visitor_id
    question = services.next_question(conn, _settings(request), visitor)
    previous = None
    if apres and SLUG_RE.match(apres):
        row = conn.execute(
            "SELECT d.id, d.slug, d.title, v.category_id FROM dishes d JOIN votes v ON v.dish_id = d.id"
            " WHERE d.slug = ? AND v.visitor_id = ?", (apres, visitor)).fetchone()
        if row:
            dist = services.distribution(conn, row["id"])
            same = next((r for r in dist["rows"] if r["id"] == row["category_id"]), None)
            previous = {"dish": row, "dist": dist, "same": same}
    count = services.answer_count(conn, visitor)
    return render(request, "index.html", {
        "question": question, "categories": services.categories(conn), "previous": previous,
        "reward": reward(count), "count": count,
    })


def _vote(request: Request, conn, token: str, choice: str):
    if not rate_ok(request, conn, "vote"):
        raise services.VoteError("rate_limited", 429)
    try:
        return services.record_vote(conn, _settings(request), request.state.visitor_id, token, choice,
                                    request.state.ip_hash)
    except services.VoteError as exc:
        if exc.code in ("invalid_token", "token_used", "too_fast"):
            security_event(request, "vote_rejected", reason=exc.code)
        raise


@router.post("/api/vote")
async def api_vote(request: Request, conn=Depends(get_conn)):
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return JSONResponse({"error": "bad_request"}, status_code=400)
    try:
        result = _vote(request, conn, str(payload.get("token", "")), str(payload.get("choice", "")))
    except services.VoteError as exc:
        body = {"error": exc.code}
        if exc.code in ("token_used", "already_answered", "dish_unavailable", "expired_token", "invalid_token"):
            body["next"] = services.next_question(conn, _settings(request), request.state.visitor_id)
        return JSONResponse(body, status_code=exc.status)
    result["next"] = services.next_question(conn, _settings(request), request.state.visitor_id)
    return JSONResponse(result)


@router.get("/api/question")
def api_question(request: Request, conn=Depends(get_conn)):
    if not rate_ok(request, conn, "question"):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    return JSONResponse({"next": services.next_question(conn, _settings(request), request.state.visitor_id)})


@router.post("/voter")
async def form_vote(request: Request, conn=Depends(get_conn)):
    """No-JS fallback for the question loop."""
    form = await request.form()
    try:
        result = _vote(request, conn, str(form.get("token", "")), str(form.get("choice", "")))
    except services.VoteError as exc:
        if exc.status == 429:
            raise HTTPException(429) from None
        return RedirectResponse("/", status_code=303)
    target = "/" if result["skipped"] else f"/?apres={result['dish']['slug']}"
    return RedirectResponse(target, status_code=303)


# ------------------------------------------------------------------ proposer

NAME_MIN, NAME_MAX, DESC_MIN, DESC_MAX, REGION_MAX = 2, 80, 20, 600, 80


def _pow_difficulty(conn, request) -> int:
    raw = db.get_setting(conn, "pow_difficulty", "")
    try:
        return max(0, min(24, int(raw)))
    except ValueError:
        return _settings(request).pow_difficulty


def _proposer_page(request, conn, *, values=None, errors=None, similar=None, status=200):
    token = security.issue_form_token(_settings(request).secret_key, request.state.visitor_id)
    return render(request, "proposer.html", {
        "values": values or {}, "errors": errors or [], "similar": similar or [],
        "form_token": token, "difficulty": _pow_difficulty(conn, request),
        "limits": {"name": (NAME_MIN, NAME_MAX), "desc": (DESC_MIN, DESC_MAX), "region": REGION_MAX},
    }, status=status)


@router.get("/proposer")
def proposer(request: Request, conn=Depends(get_conn)):
    return _proposer_page(request, conn)


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@router.post("/proposer")
async def proposer_post(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    values = {"nom": _clean(form.get("nom"))[:200], "description": str(form.get("description") or "").strip()[:2000],
              "region": _clean(form.get("region"))[:200]}
    settings = _settings(request)
    visitor = request.state.visitor_id

    if form.get("website"):  # honeypot: pretend everything went fine
        security_event(request, "honeypot")
        return render(request, "merci.html", {"duplicate": False})

    min_fill = float(db.get_setting(conn, "min_fill_seconds", "3") or 3)
    token = str(form.get("form_token", ""))
    try:
        sig = security.check_form_token(settings.secret_key, token, visitor, min_fill)
    except security.TokenError as exc:
        security_event(request, "proposal_rejected", reason=exc.code)
        msg = {"too_fast": "Un peu trop rapide ! Prenez quelques secondes et renvoyez le formulaire.",
               "expired_form": "Le formulaire a expiré, merci de le renvoyer."}.get(
            exc.code, "Le formulaire n'est pas valide, merci de le renvoyer.")
        return _proposer_page(request, conn, values=values, errors=[msg], status=400)
    if not security.check_pow(token, str(form.get("pow_nonce", "")), _pow_difficulty(conn, request)):
        security_event(request, "proposal_rejected", reason="pow")
        return _proposer_page(request, conn, values=values, status=400, errors=[
            "La vérification anti-robot n'a pas abouti (JavaScript est nécessaire). Merci de renvoyer."])
    with db.Tx(conn):
        fresh = security.consume_token(conn, "form:" + sig, ttl_seconds=security.FORM_TOKEN_TTL)
    if not fresh:
        security_event(request, "proposal_rejected", reason="form_reused")
        return _proposer_page(request, conn, values=values, status=400,
                              errors=["Ce formulaire a déjà été envoyé."])

    decision = str(form.get("decision", ""))
    if decision.startswith("same:"):
        db.audit(conn, "proposal_duplicate_acknowledged", decision[5:20], request.state.ip_hash)
        return render(request, "merci.html", {"duplicate": True})

    errors = []
    if not NAME_MIN <= len(values["nom"]) <= NAME_MAX:
        errors.append(f"Le nom du plat doit faire entre {NAME_MIN} et {NAME_MAX} caractères.")
    if not DESC_MIN <= len(values["description"]) <= DESC_MAX:
        errors.append(f"La description doit faire entre {DESC_MIN} et {DESC_MAX} caractères.")
    if len(values["region"]) > REGION_MAX:
        errors.append(f"La région/origine ne doit pas dépasser {REGION_MAX} caractères.")
    if errors:
        return _proposer_page(request, conn, values=values, errors=errors, status=400)

    if decision != "different":
        similar = services.similar_dishes(conn, values["nom"])
        if similar:
            return _proposer_page(request, conn, values=values, similar=similar)

    if not rate_ok(request, conn, "submit"):
        return _proposer_page(request, conn, values=values, status=429, errors=[
            "Vous avez proposé beaucoup de plats récemment, merci ! Réessayez un peu plus tard."])
    result = services.submit_dish(conn, title=values["nom"], description=values["description"],
                                  region=values["region"], visitor_id=visitor, ip_hash=request.state.ip_hash)
    if result["status"] == "flagged":
        security_event(request, "proposal_flagged", dish_id=result["id"])
    return render(request, "merci.html", {"duplicate": False})


@router.get("/api/similaires")
def api_similar(request: Request, nom: str = "", conn=Depends(get_conn)):
    if not rate_ok(request, conn, "similar"):
        return JSONResponse({"error": "rate_limited", "items": []}, status_code=429)
    nom = _clean(nom)[:NAME_MAX]
    items = services.similar_dishes(conn, nom) if len(nom) >= NAME_MIN else []
    return JSONResponse({"items": [{k: i[k] for k in ("slug", "title", "description", "pending")} for i in items]})


# ------------------------------------------------------------------ browse / dish / stats

@router.get("/plats")
def plats(request: Request, q: str = "", tri: str = "az", page: int = 1, conn=Depends(get_conn)):
    q = _clean(q)[:100]
    tri = tri if tri in services.SORTS else "az"
    result = services.browse(conn, q, tri, page)
    return render(request, "plats.html", {"q": q, "tri": tri, "sorts": services.SORTS, **result})


@router.get("/plat/{slug}")
def plat(request: Request, slug: str, conn=Depends(get_conn)):
    if not SLUG_RE.match(slug):
        raise HTTPException(404)
    dish = conn.execute("SELECT * FROM dishes WHERE slug = ?", (slug,)).fetchone()
    if dish is not None and dish["status"] == "merged" and dish["merged_into"]:
        target = conn.execute("SELECT slug FROM dishes WHERE id = ?", (dish["merged_into"],)).fetchone()
        if target:
            return RedirectResponse(f"/plat/{target['slug']}", status_code=301)
    if dish is None or dish["status"] != "active":
        raise HTTPException(404)
    mine = conn.execute("SELECT category_id FROM votes WHERE dish_id = ? AND visitor_id = ?",
                        (dish["id"], request.state.visitor_id)).fetchone()
    return render(request, "plat.html", {"dish": services.dish_public(dish), "dist": services.distribution(conn, dish["id"]),
                                         "mine": mine})


@router.get("/stats")
def stats(request: Request, conn=Depends(get_conn)):
    return render(request, "stats.html", {"s": services.public_stats(conn)})


@router.get("/a-propos")
def about(request: Request):
    return render(request, "a_propos.html", {"contact": _settings(request).contact})


# ------------------------------------------------------------------ assets & health

@router.get("/categories.css")
def categories_css(conn=Depends(get_conn)):
    lines = []
    for c in services.categories(conn, active_only=False):
        color = c["color"] if re.fullmatch(r"#[0-9A-Fa-f]{6}", c["color"] or "") else "#888888"
        lines.append(f".cat-{c['slug']}{{--cat:{color};--cat-fg:{_text_color(color)}}}")
    return Response("\n".join(lines) + "\n", media_type="text/css",
                    headers={"Cache-Control": "public, max-age=60"})


def _text_color(hex_color: str) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#000000" if lum > 0.179 else "#ffffff"


@router.get("/media/{name}")
def media(request: Request, name: str):
    if not MEDIA_NAME.match(name):
        raise HTTPException(404)
    path = _settings(request).images_dir / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/healthz")
def healthz(conn=Depends(get_conn)):
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        dishes = conn.execute("SELECT COUNT(*) FROM dishes WHERE status='active'").fetchone()[0]
        cats = conn.execute("SELECT COUNT(*) FROM categories WHERE active=1").fetchone()[0]
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "error"}, status_code=503)
    return JSONResponse({"status": "ok", "schema": version, "active_dishes": dishes, "categories": cats})


@router.get("/robots.txt")
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /admin\nDisallow: /api/\n")
