"""Admin panel (/admin): single admin, password + TOTP, CSRF on every form, audit log."""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from . import auth, db, export, importer, services
from .ratelimit import parse_rules, validate_rules
from .text import moderation_norm, slugify, title_norm
from .web import get_conn, security_event

router = APIRouter(prefix="/admin")
PER_PAGE = 50
STATUSES = ("active", "hidden", "pending", "flagged", "rejected", "merged")


# ------------------------------------------------------------------ plumbing

def _session(request: Request, conn):
    return auth.get_session(conn, request.cookies.get(auth.SESSION_COOKIE))


def require_admin(request: Request, conn=Depends(get_conn)):
    session = _session(request, conn)
    if session is None:
        raise HTTPException(303, headers={"Location": "/admin/login"})
    request.state.admin = session
    return conn


async def admin_form(request: Request, conn=Depends(require_admin)):
    form = await request.form()
    if not auth.csrf_ok(request.state.admin, form.get("csrf")):
        security_event(request, "admin_csrf_failed")
        raise HTTPException(403)
    return conn, form


def page(request: Request, name: str, ctx: dict | None = None, status: int = 200):
    ctx = dict(ctx or {})
    ctx.setdefault("csrf", request.state.admin["csrf"] if getattr(request.state, "admin", None) else "")
    ctx.setdefault("flash", request.query_params.get("ok", ""))
    ctx.setdefault("error", request.query_params.get("err", ""))
    return request.app.state.templates.TemplateResponse(request, f"admin/{name}", ctx, status_code=status)


def back(url: str, ok: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("ok", ok), ("err", err)) if v}
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url + (sep + urlencode(params) if params else ""), status_code=303)


def audit(request: Request, conn, action: str, detail="") -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    db.audit(conn, action, detail, request.state.ip_hash)


def _ids(form, key: str = "ids") -> list[int]:
    return [int(v) for v in form.getlist(key) if str(v).isdigit()]


def _resolve_dish(conn, ref: str):
    ref = (ref or "").strip()
    if ref.isdigit():
        return conn.execute("SELECT * FROM dishes WHERE id = ?", (int(ref),)).fetchone()
    ref = ref.rstrip("/").rsplit("/", 1)[-1]
    return conn.execute("SELECT * FROM dishes WHERE slug = ?", (ref,)).fetchone()


# ------------------------------------------------------------------ login / logout

@router.get("/login")
def login_form(request: Request, conn=Depends(get_conn)):
    if _session(request, conn):
        return RedirectResponse("/admin", status_code=303)
    token = secrets.token_urlsafe(24)
    resp = page(request, "login.html", {"login_csrf": token,
                                        "configured": request.app.state.settings.admin_configured})
    resp.set_cookie(auth.LOGIN_CSRF_COOKIE, token, max_age=1800, httponly=True,
                    secure=request.app.state.settings.cookie_secure, samesite="strict", path="/admin")
    return resp


@router.post("/login")
async def login(request: Request, conn=Depends(get_conn)):
    settings = request.app.state.settings
    form = await request.form()
    cookie_token = request.cookies.get(auth.LOGIN_CSRF_COOKIE, "")
    if not cookie_token or not secrets.compare_digest(cookie_token, str(form.get("login_csrf", ""))):
        security_event(request, "admin_csrf_failed", stage="login")
        raise HTTPException(403)
    ip_hash = request.state.ip_hash
    rules = parse_rules(db.get_setting(conn, "rl_login", "5/15m")) or [(5, 900)]
    max_failures, window = rules[0]

    def fail(message: str, status: int = 401):
        token = secrets.token_urlsafe(24)
        resp = page(request, "login.html", {"login_csrf": token, "configured": settings.admin_configured,
                                            "error": message}, status=status)
        resp.set_cookie(auth.LOGIN_CSRF_COOKIE, token, max_age=1800, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/admin")
        return resp

    if not settings.admin_configured:
        return fail("L'administration n'est pas configurée (lancez scripts/set-admin.sh).", 503)
    remaining = auth.lockout_remaining(conn, ip_hash, max_failures, window)
    if remaining:
        security_event(request, "admin_login_locked", seconds=remaining)
        return fail(f"Trop d'échecs. Réessayez dans {remaining // 60 + 1} min.", 429)
    password_ok = auth.verify_password(settings.admin_password_hash, str(form.get("password", "")))
    totp_ok = password_ok and auth.verify_totp(conn, settings.admin_totp_secret, str(form.get("totp", "")))
    if not (password_ok and totp_ok):
        auth.record_attempt(conn, ip_hash, False)
        db.audit(conn, "login_failed", "", ip_hash)
        security_event(request, "admin_login_failed")
        return fail("Identifiants invalides.")
    auth.record_attempt(conn, ip_hash, True)
    token, _csrf = auth.create_session(conn)
    db.audit(conn, "login", "", ip_hash)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_TTL, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/admin")
    resp.delete_cookie(auth.LOGIN_CSRF_COOKIE, path="/admin")
    return resp


@router.post("/logout")
async def logout(request: Request, deps=Depends(admin_form)):
    conn, _form = deps
    auth.delete_session(conn, request.cookies.get(auth.SESSION_COOKIE))
    audit(request, conn, "logout")
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE, path="/admin")
    return resp


# ------------------------------------------------------------------ dashboard

@router.get("")
def dashboard(request: Request, conn=Depends(require_admin)):
    since = int(time.time()) - 86400
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    totals = {
        "answers": one("SELECT COUNT(*) FROM votes WHERE category_id IS NOT NULL"),
        "skips": one("SELECT COUNT(*) FROM votes WHERE category_id IS NULL"),
        "participants": one("SELECT COUNT(DISTINCT visitor_id) FROM votes"),
        "active": one("SELECT COUNT(*) FROM dishes WHERE status='active'"),
        "hidden": one("SELECT COUNT(*) FROM dishes WHERE status='hidden'"),
        "pending": one("SELECT COUNT(*) FROM dishes WHERE status='pending'"),
        "flagged": one("SELECT COUNT(*) FROM dishes WHERE status='flagged'"),
        "answers_24h": one("SELECT COUNT(*) FROM votes WHERE category_id IS NOT NULL AND created_at >= ?", since),
        "participants_24h": one("SELECT COUNT(DISTINCT visitor_id) FROM votes WHERE created_at >= ?", since),
        "submissions_24h": one("SELECT COUNT(*) FROM dishes WHERE source='user' AND created_at >= ?", since),
    }
    return page(request, "dashboard.html", {"t": totals, "anomalies": services.anomalies(conn),
                                            "import_status": importer.import_status(conn)})


# ------------------------------------------------------------------ dishes

@router.get("/plats")
def dishes(request: Request, q: str = "", source: str = "", status: str = "", votes: str = "",
           page_no: int = 1, conn=Depends(require_admin)):
    where, params = [], []
    if q:
        where.append("(d.title_norm LIKE ? OR d.slug LIKE ? OR d.qid = ?)")
        params += [f"%{title_norm(q)}%", f"%{slugify(q)}%", q.strip()]
    if source in ("wikidata", "user", "manual"):
        where.append("d.source = ?")
        params.append(source)
    if status in STATUSES:
        where.append("d.status = ?")
        params.append(status)
    having = {"0": "n = 0", "1": "n >= 1", "5": "n >= 5", "20": "n >= 20"}.get(votes, "")
    sql = ("SELECT d.*, (SELECT COUNT(*) FROM votes v WHERE v.dish_id = d.id AND v.category_id IS NOT NULL) AS n"
           " FROM dishes d" + (" WHERE " + " AND ".join(where) if where else ""))
    if having:
        sql = f"SELECT * FROM ({sql}) WHERE {having}"
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    pages = max(1, -(-total // PER_PAGE))
    page_no = min(max(1, page_no), pages)
    rows = conn.execute(sql + " ORDER BY title_norm LIMIT ? OFFSET ?",
                        params + [PER_PAGE, (page_no - 1) * PER_PAGE]).fetchall()
    return page(request, "dishes.html", {
        "rows": rows, "total": total, "pages": pages, "page_no": page_no, "statuses": STATUSES,
        "f": {"q": q, "source": source, "status": status, "votes": votes},
        "import_status": importer.import_status(conn),
    })


@router.post("/plats/action")
async def dishes_action(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    ids, action = _ids(form), str(form.get("action", ""))
    target = {"hide": "hidden", "activate": "active", "reject": "rejected"}.get(action)
    if not ids or not target:
        return back("/admin/plats", err="Sélectionnez des plats et une action.")
    with db.Tx(conn):
        for i in ids:
            conn.execute("UPDATE dishes SET status = ?, updated_at = ? WHERE id = ? AND status != 'merged'",
                         (target, int(time.time()), i))
        audit(request, conn, f"dishes_{action}", {"ids": ids})
    return_to = str(form.get("return_to", ""))
    return back(return_to if return_to.startswith("/admin/") else "/admin/plats",
                ok=f"{len(ids)} plat(s) mis à jour.")


@router.post("/plats/fusion")
async def dishes_merge(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    src, dst = _resolve_dish(conn, str(form.get("source", ""))), _resolve_dish(conn, str(form.get("target", "")))
    return_to = str(form.get("return_to", "/admin/plats"))
    if not return_to.startswith("/admin"):
        return_to = "/admin/plats"
    if not src or not dst:
        return back(return_to, err="Plat source ou cible introuvable (id ou slug).")
    try:
        result = services.merge_dishes(conn, src["id"], dst["id"])
    except ValueError as exc:
        return back(return_to, err=str(exc))
    audit(request, conn, "dish_merge", {"source": src["id"], "target": dst["id"], **result})
    return back(return_to, ok=f"« {src['title']} » fusionné dans « {dst['title']} » "
                              f"({result['moved']} vote(s) déplacé(s), {result['dropped']} doublon(s) retiré(s)).")


@router.post("/plats/nouveau")
async def dish_create(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    title = str(form.get("title", "")).strip()[:120]
    if len(title) < 2:
        return back("/admin/plats", err="Titre trop court.")
    with db.Tx(conn):
        dish_id = services.create_dish(conn, title=title, description=str(form.get("description", "")).strip()[:1000],
                                       region=str(form.get("region", "")).strip()[:80], source="manual")
        conn.execute("UPDATE dishes SET admin_edited = 1 WHERE id = ?", (dish_id,))
        audit(request, conn, "dish_create", {"id": dish_id, "title": title})
    return back(f"/admin/plats/{dish_id}", ok="Plat créé.")


@router.post("/import")
async def run_import(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    settings = request.app.state.settings
    started = importer.start_background(settings.db_path, settings.images_dir,
                                        enrich=form.get("enrich") == "1", images=form.get("images") == "1")
    audit(request, conn, "import_wikidata_started" if started else "import_wikidata_busy")
    if not started:
        return back("/admin/plats", err="Un import est déjà en cours.")
    return back("/admin/plats", ok="Import Wikidata lancé en arrière-plan (rafraîchissez pour suivre).")


@router.get("/plats/{dish_id}")
def dish_edit(request: Request, dish_id: int, conn=Depends(require_admin)):
    dish = conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    if dish is None:
        raise HTTPException(404)
    merged_into = None
    if dish["merged_into"]:
        merged_into = conn.execute("SELECT id, slug, title FROM dishes WHERE id = ?", (dish["merged_into"],)).fetchone()
    return page(request, "dish_edit.html", {
        "dish": dish, "dist": services.distribution(conn, dish_id), "statuses": STATUSES,
        "merged_into": merged_into, "similar": [s for s in services.similar_dishes(conn, dish["title"])
                                                if s["id"] != dish_id],
    })


@router.post("/plats/{dish_id}")
async def dish_save(request: Request, dish_id: int, deps=Depends(admin_form)):
    conn, form = deps
    dish = conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    if dish is None:
        raise HTTPException(404)
    title = re.sub(r"\s+", " ", str(form.get("title", ""))).strip()[:120]
    status = str(form.get("status", dish["status"]))
    if len(title) < 2 or status not in STATUSES or (status == "merged" and not dish["merged_into"]):
        return back(f"/admin/plats/{dish_id}", err="Titre ou statut invalide.")
    with db.Tx(conn):
        conn.execute(
            "UPDATE dishes SET title = ?, title_norm = ?, description = ?, region = ?, status = ?,"
            " admin_edited = 1, updated_at = ? WHERE id = ?",
            (title, title_norm(title), str(form.get("description", "")).strip()[:1000],
             str(form.get("region", "")).strip()[:80], status, int(time.time()), dish_id))
        audit(request, conn, "dish_edit", {"id": dish_id, "title": title, "status": status})
    return back(f"/admin/plats/{dish_id}", ok="Enregistré.")


# ------------------------------------------------------------------ submissions

@router.get("/propositions")
def submissions(request: Request, statut: str = "queue", conn=Depends(require_admin)):
    statuses = {"queue": ("pending", "flagged"), "all": STATUSES, "rejected": ("rejected",),
                "active": ("active",)}.get(statut, ("pending", "flagged"))
    rows = conn.execute(
        f"SELECT * FROM dishes WHERE source = 'user' AND status IN ({','.join('?' * len(statuses))})"
        " ORDER BY created_at DESC LIMIT 200", statuses).fetchall()
    items = [{"dish": r, "similar": [s for s in services.similar_dishes(conn, r["title"]) if s["id"] != r["id"]]}
             for r in rows]
    return page(request, "submissions.html", {"items": items, "statut": statut})


@router.post("/propositions/{dish_id}")
async def submission_action(request: Request, dish_id: int, deps=Depends(admin_form)):
    conn, form = deps
    action = str(form.get("action", ""))
    dish = conn.execute("SELECT * FROM dishes WHERE id = ? AND source = 'user'", (dish_id,)).fetchone()
    if dish is None:
        raise HTTPException(404)
    if action in ("approve", "reject"):
        new = "active" if action == "approve" else "rejected"
        with db.Tx(conn):
            conn.execute("UPDATE dishes SET status = ?, updated_at = ? WHERE id = ?", (new, int(time.time()), dish_id))
            audit(request, conn, f"submission_{action}", {"id": dish_id, "title": dish["title"]})
        return back("/admin/propositions", ok=f"« {dish['title']} » : {'approuvé' if new == 'active' else 'rejeté'}.")
    if action == "merge":
        target = _resolve_dish(conn, str(form.get("target", "")))
        if not target:
            return back("/admin/propositions", err="Plat cible introuvable.")
        try:
            result = services.merge_dishes(conn, dish_id, target["id"])
        except ValueError as exc:
            return back("/admin/propositions", err=str(exc))
        audit(request, conn, "submission_merge", {"id": dish_id, "target": target["id"], **result})
        return back("/admin/propositions", ok=f"Fusionné dans « {target['title']} ».")
    return back("/admin/propositions", err="Action inconnue.")


# ------------------------------------------------------------------ votes

def _vote_filters(params) -> tuple[str, list]:
    where, args = [], []
    if params.get("plat"):
        where.append("(d.slug = ? OR CAST(d.id AS TEXT) = ?)")
        args += [params["plat"], params["plat"]]
    if params.get("categorie"):
        if params["categorie"] == "skip":
            where.append("v.category_id IS NULL")
        else:
            where.append("c.slug = ?")
            args.append(params["categorie"])
    if params.get("visiteur"):
        where.append("v.visitor_id = ?")
        args.append(params["visiteur"])
    if params.get("ip"):
        where.append("v.ip_hash = ?")
        args.append(params["ip"])
    for key, op in (("du", ">="), ("au", "<")):
        if params.get(key):
            try:
                day = datetime.strptime(params[key], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            ts = int(day.timestamp()) + (86400 if key == "au" else 0)
            where.append(f"v.created_at {op} ?")
            args.append(ts)
    return (" WHERE " + " AND ".join(where)) if where else "", args


VOTE_FILTER_KEYS = ("plat", "categorie", "visiteur", "ip", "du", "au")


@router.get("/votes")
def votes(request: Request, page_no: int = 1, conn=Depends(require_admin)):
    f = {k: request.query_params.get(k, "").strip() for k in VOTE_FILTER_KEYS}
    where, args = _vote_filters(f)
    base = (" FROM votes v JOIN dishes d ON d.id = v.dish_id LEFT JOIN categories c ON c.id = v.category_id"
            + where)
    total = conn.execute("SELECT COUNT(*)" + base, args).fetchone()[0]
    pages = max(1, -(-total // 100))
    page_no = min(max(1, page_no), pages)
    rows = conn.execute("SELECT v.*, d.slug AS dish_slug, d.title AS dish_title, c.name AS cat_name,"
                        " c.slug AS cat_slug" + base + " ORDER BY v.id DESC LIMIT 100 OFFSET ?",
                        args + [(page_no - 1) * 100]).fetchall()
    return page(request, "votes.html", {
        "rows": rows, "total": total, "pages": pages, "page_no": page_no, "f": f,
        "qs": urlencode({k: v for k, v in f.items() if v}),
        "categories": services.categories(conn, active_only=False),
    })


@router.post("/votes/supprimer")
async def votes_delete(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    mode = str(form.get("mode", "selected"))
    with db.Tx(conn):
        if mode == "selected":
            ids = _ids(form)
            n = sum(conn.execute("DELETE FROM votes WHERE id = ?", (i,)).rowcount for i in ids)
            detail = {"ids": ids}
        elif mode == "visitor" and form.get("value"):
            n = conn.execute("DELETE FROM votes WHERE visitor_id = ?", (str(form["value"]),)).rowcount
            detail = {"visitor": str(form["value"])}
        elif mode == "ip" and form.get("value"):
            n = conn.execute("DELETE FROM votes WHERE ip_hash = ?", (str(form["value"]),)).rowcount
            detail = {"ip_hash": str(form["value"])}
        elif mode == "filter":
            f = {k: str(form.get(k, "")).strip() for k in VOTE_FILTER_KEYS}
            if not any(f.values()):
                raise HTTPException(400, "Refus : aucun filtre (tout supprimer n'est pas permis ici).")
            where, args = _vote_filters(f)
            n = conn.execute(
                "DELETE FROM votes WHERE id IN (SELECT v.id FROM votes v JOIN dishes d ON d.id = v.dish_id"
                " LEFT JOIN categories c ON c.id = v.category_id" + where + ")", args).rowcount
            detail = {"filter": f}
        else:
            return back("/admin/votes", err="Rien à supprimer.")
        audit(request, conn, "votes_delete", {**detail, "deleted": n})
    return back("/admin/votes", ok=f"{n} réponse(s) supprimée(s).")


# ------------------------------------------------------------------ categories

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@router.get("/categories")
def categories_page(request: Request, conn=Depends(require_admin)):
    rows = conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM votes v WHERE v.category_id = c.id) AS n FROM categories c"
        " ORDER BY position, id").fetchall()
    return page(request, "categories.html", {"rows": rows})


@router.post("/categories")
async def categories_save(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    action = str(form.get("action", ""))
    name = str(form.get("name", "")).strip()[:40]
    color = str(form.get("color", "")).strip()
    emoji = str(form.get("emoji", "")).strip()[:8]
    try:
        position = int(form.get("position") or 0)
    except ValueError:
        position = 0
    if action in ("create", "update") and (not name or not COLOR_RE.match(color)):
        return back("/admin/categories", err="Nom requis et couleur au format #RRGGBB.")
    with db.Tx(conn):
        if action == "create":
            slug = slugify(str(form.get("slug") or name), 30)
            if conn.execute("SELECT 1 FROM categories WHERE slug = ?", (slug,)).fetchone():
                return back("/admin/categories", err="Ce slug existe déjà.")
            conn.execute("INSERT INTO categories(slug, name, color, emoji, position, active, created_at)"
                         " VALUES (?,?,?,?,?,1,?)", (slug, name, color, emoji, position, int(time.time())))
            audit(request, conn, "category_create", {"slug": slug, "name": name})
        elif action == "update":
            cid = int(form.get("id", 0))
            active = 1 if form.get("active") == "1" else 0
            conn.execute("UPDATE categories SET name = ?, color = ?, emoji = ?, position = ?, active = ? WHERE id = ?",
                         (name, color, emoji, position, active, cid))
            audit(request, conn, "category_update", {"id": cid, "name": name, "active": active})
        elif action == "delete":
            cid = int(form.get("id", 0))
            if conn.execute("SELECT 1 FROM votes WHERE category_id = ? LIMIT 1", (cid,)).fetchone():
                return back("/admin/categories", err="Cette catégorie a des votes : désactivez-la plutôt.")
            conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
            audit(request, conn, "category_delete", {"id": cid})
        else:
            return back("/admin/categories", err="Action inconnue.")
    return back("/admin/categories", ok="Catégories mises à jour.")


# ------------------------------------------------------------------ banned words

@router.get("/mots")
def words(request: Request, conn=Depends(require_admin)):
    rows = conn.execute("SELECT * FROM banned_words ORDER BY word_norm").fetchall()
    return page(request, "words.html", {"rows": rows, "test": None})


@router.post("/mots")
async def words_action(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    action = str(form.get("action", ""))
    now = int(time.time())
    if action == "test":
        text = str(form.get("text", ""))[:5000]
        rows = conn.execute("SELECT * FROM banned_words ORDER BY word_norm").fetchall()
        test = {"text": text, "norm": moderation_norm(text), "hits": services.moderation_hits(conn, text)}
        return page(request, "words.html", {"rows": rows, "test": test})
    with db.Tx(conn):
        if action == "add":
            word, mode = str(form.get("word", "")).strip()[:80], str(form.get("mode", "word"))
            if not moderation_norm(word) or mode not in ("word", "substring"):
                return back("/admin/mots", err="Mot invalide.")
            conn.execute("INSERT OR IGNORE INTO banned_words(word, word_norm, mode, created_at) VALUES (?,?,?,?)",
                         (word, moderation_norm(word), mode, now))
            audit(request, conn, "banned_word_add", {"word": word, "mode": mode})
            msg = f"« {word} » ajouté."
        elif action == "remove":
            ids = _ids(form)
            for i in ids:
                conn.execute("DELETE FROM banned_words WHERE id = ?", (i,))
            audit(request, conn, "banned_word_remove", {"ids": ids})
            msg = f"{len(ids)} mot(s) retiré(s)."
        elif action == "import":
            added = 0
            for line in str(form.get("bulk", "")).splitlines()[:5000]:
                word, _, mode = line.partition("|")
                word, mode = word.strip()[:80], (mode.strip() or str(form.get("mode", "word")))
                if word and moderation_norm(word) and mode in ("word", "substring"):
                    added += conn.execute(
                        "INSERT OR IGNORE INTO banned_words(word, word_norm, mode, created_at) VALUES (?,?,?,?)",
                        (word, moderation_norm(word), mode, now)).rowcount
            audit(request, conn, "banned_word_import", {"added": added})
            msg = f"{added} mot(s) importé(s)."
        elif action != "rescan":
            return back("/admin/mots", err="Action inconnue.")
    if action == "rescan":
        result = services.rescan_submissions(conn)
        audit(request, conn, "banned_word_rescan", result)
        msg = f"Re-scan terminé : {result['flagged']} proposition(s) signalée(s)."
    return back("/admin/mots", ok=msg)


# ------------------------------------------------------------------ settings

SETTING_FIELDS = [
    ("rl_vote", "Réponses (visiteur et IP)", "rules"),
    ("rl_question", "Questions servies via l'API", "rules"),
    ("rl_submit", "Propositions de plats", "rules"),
    ("rl_similar", "Vérification « plats similaires »", "rules"),
    ("rl_login", "Échecs de connexion admin avant blocage (1re règle)", "rules"),
    ("min_think_ms", "Temps de réflexion minimum (ms)", "int"),
    ("min_fill_seconds", "Temps de remplissage minimum du formulaire (s)", "int"),
    ("pow_difficulty", "Preuve de travail : bits à zéro (vide = valeur d'environnement)", "intblank"),
    ("anomaly_votes_per_hour", "Alerte : réponses par heure (visiteur ou IP)", "int"),
    ("anomaly_visitors_per_ip", "Alerte : visiteurs distincts par IP en 24 h", "int"),
]


@router.get("/reglages")
def settings_page(request: Request, conn=Depends(require_admin)):
    values = {k: db.get_setting(conn, k) for k, _, _ in SETTING_FIELDS}
    return page(request, "settings.html", {"fields": SETTING_FIELDS, "values": values,
                                           "require_approval": db.get_setting(conn, "require_approval") == "1"})


@router.post("/reglages")
async def settings_save(request: Request, deps=Depends(admin_form)):
    conn, form = deps
    changes, errors = {}, []
    for key, label, kind in SETTING_FIELDS:
        value = str(form.get(key, "")).strip()
        if kind == "rules" and not validate_rules(value):
            errors.append(f"{label} : format attendu « 5/1h,20/1d ».")
        elif kind == "int" and not value.isdigit():
            errors.append(f"{label} : nombre entier attendu.")
        elif kind == "intblank" and value and not (value.isdigit() and int(value) <= 24):
            errors.append(f"{label} : entier entre 0 et 24, ou vide.")
        else:
            changes[key] = value
    if errors:
        return back("/admin/reglages", err=" ".join(errors))
    changes["require_approval"] = "1" if form.get("require_approval") == "1" else "0"
    with db.Tx(conn):
        for key, value in changes.items():
            db.set_setting(conn, key, value)
        audit(request, conn, "settings_update", changes)
    request.app.state.limiter.reset()
    return back("/admin/reglages", ok="Réglages enregistrés.")


# ------------------------------------------------------------------ export & audit

@router.get("/export.xlsx")
def export_xlsx(request: Request, conn=Depends(require_admin)):
    data = export.build_export(conn)
    audit(request, conn, "export_xlsx", {"bytes": len(data)})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="plats-export-{stamp}.xlsx"'})


@router.get("/journal")
def audit_page(request: Request, conn=Depends(require_admin)):
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300").fetchall()
    return page(request, "audit.html", {"rows": rows})
