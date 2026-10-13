import io
import re
import time

import openpyxl
import pyotp
import pytest

from app import auth, db
from tests.conftest import ADMIN_PASSWORD, TOTP_SECRET, totp_now


def login_page(client):
    html = client.get("/admin/login").text
    return re.search(r'name="login_csrf" value="([^"]+)"', html).group(1)


def do_login(client, password=ADMIN_PASSWORD, code=None):
    token = login_page(client)
    return client.post("/admin/login", data={"login_csrf": token, "password": password,
                                             "totp": code or totp_now()}, follow_redirects=False)


@pytest.fixture
def admin(client):
    r = do_login(client)
    assert r.status_code == 303 and r.headers["location"] == "/admin", r.text
    return client


def csrf(client, path="/admin"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def test_admin_requires_login(client):
    for path in ("/admin", "/admin/plats", "/admin/votes", "/admin/export.xlsx", "/admin/journal"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login", path


def test_login_success_sets_strict_cookie_and_audits(client, conn):
    r = do_login(client)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert auth.SESSION_COOKIE in cookie and "HttpOnly" in cookie and "Secure" in cookie
    assert "samesite=strict" in cookie.lower() and "Path=/admin" in cookie
    assert client.get("/admin").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'login'").fetchone()[0] == 1


def test_wrong_password_or_totp_rejected(client):
    assert do_login(client, password="nope").status_code == 401
    assert do_login(client, code="000000").status_code == 401
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_totp_code_cannot_be_replayed(client, app):
    code = totp_now()
    assert do_login(client, code=code).status_code == 303
    from tests.conftest import make_client
    other = make_client(app)
    assert do_login(other, code=code).status_code == 401


def test_login_requires_login_csrf(client):
    login_page(client)
    r = client.post("/admin/login", data={"login_csrf": "forged", "password": ADMIN_PASSWORD, "totp": totp_now()})
    assert r.status_code == 403


def test_lockout_after_repeated_failures(client, conn):
    db.set_setting(conn, "rl_login", "3/15m")
    for _ in range(3):
        assert do_login(client, password="wrong").status_code == 401
    r = do_login(client)  # even the right credentials are refused while locked
    assert r.status_code == 429 and "Trop d&#39;échecs" in r.text
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'login_failed'").fetchone()[0] == 3


def test_lockout_expires(client, conn):
    db.set_setting(conn, "rl_login", "3/15m")
    for _ in range(3):
        do_login(client, password="wrong")
    conn.execute("UPDATE login_attempts SET ts = ts - ?", (auth.LOCKOUT_SECONDS + 1,))
    assert do_login(client).status_code == 303


def test_admin_not_configured(app, client):
    app.state.settings.admin_password_hash = ""
    r = do_login(client)
    assert r.status_code == 503


def test_post_without_csrf_is_forbidden(admin, conn):
    for path, data in [("/admin/reglages", {"require_approval": "1"}),
                       ("/admin/mots", {"action": "add", "word": "zut", "mode": "word"}),
                       ("/admin/votes/supprimer", {"mode": "visitor", "value": "x"}),
                       ("/admin/logout", {})]:
        r = admin.post(path, data=data, follow_redirects=False)
        assert r.status_code == 403, path
        r = admin.post(path, data={**data, "csrf": "wrong"}, follow_redirects=False)
        assert r.status_code == 403, path
    assert db.get_setting(conn, "require_approval") == "0"


def test_session_expiry(admin, conn):
    conn.execute("UPDATE admin_sessions SET expires_at = ?", (int(time.time()) - 1,))
    assert admin.get("/admin", follow_redirects=False).status_code == 303


def test_logout(admin):
    token = csrf(admin)
    assert admin.post("/admin/logout", data={"csrf": token}, follow_redirects=False).status_code == 303
    assert admin.get("/admin", follow_redirects=False).status_code == 303


def test_settings_update_and_audit(admin, conn):
    token = csrf(admin, "/admin/reglages")
    data = {"csrf": token, "require_approval": "1", "rl_vote": "3/3s,300/1h", "rl_question": "120/1m",
            "rl_submit": "5/1h,20/1d", "rl_similar": "30/1m", "rl_vote_ip": "30/3s,3000/1h",
            "rl_question_ip": "1200/1m", "rl_submit_ip": "20/1h,60/1d", "rl_similar_ip": "300/1m", "rl_login": "5/15m", "min_think_ms": "600",
            "min_fill_seconds": "3", "pow_difficulty": "", "anomaly_votes_per_hour": "200",
            "anomaly_visitors_per_ip": "15"}
    r = admin.post("/admin/reglages", data=data, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert db.get_setting(conn, "require_approval") == "1"
    bad = admin.post("/admin/reglages", data={**data, "rl_vote": "lots"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'settings_update'").fetchone()[0] == 1


def test_all_admin_pages_render(admin, dishes):
    for path in ("/admin", "/admin/plats", f"/admin/plats/{dishes[0]}", "/admin/propositions",
                 "/admin/propositions?statut=all", "/admin/votes", "/admin/votes?categorie=skip&du=2020-01-01",
                 "/admin/categories", "/admin/mots", "/admin/reglages", "/admin/journal"):
        assert admin.get(path).status_code == 200, path


def test_export_download(admin, dishes):
    r = admin.get("/admin/export.xlsx")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb["dishes"].max_row == len(dishes) + 1


def test_merge_moves_votes_keeping_one_per_visitor(admin, conn, dishes):
    src, dst = dishes[0], dishes[1]
    rows = [(src, "alice-xxxxxxxx", 1), (src, "bob-xxxxxxxxxx", 2), (dst, "alice-xxxxxxxx", 3)]
    for d, v, c in rows:
        conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, created_at) VALUES (?,?,?,0)", (d, v, c))
    r = admin.post("/admin/plats/fusion", data={"csrf": csrf(admin), "source": str(src), "target": str(dst)},
                   follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    votes = conn.execute("SELECT visitor_id, category_id FROM votes WHERE dish_id = ? ORDER BY visitor_id",
                         (dst,)).fetchall()
    assert [(v["visitor_id"], v["category_id"]) for v in votes] == [("alice-xxxxxxxx", 3), ("bob-xxxxxxxxxx", 2)]
    s = conn.execute("SELECT status, merged_into, slug FROM dishes WHERE id = ?", (src,)).fetchone()
    assert s["status"] == "merged" and s["merged_into"] == dst
    # old URL redirects to the merged dish
    r = admin.get(f"/plat/{s['slug']}", follow_redirects=False)
    assert r.status_code == 301


def test_bulk_delete_votes_by_ip(admin, conn, dishes):
    for i, d in enumerate(dishes[:3]):
        conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, ip_hash, created_at) VALUES (?,?,1,?,0)",
                     (d, f"spammer-{i}-xxxxxx", "badhash"))
    conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, ip_hash, created_at) VALUES (?,?,1,?,0)",
                 (dishes[0], "honest-xxxxxxxx", "goodhash"))
    r = admin.post("/admin/votes/supprimer", data={"csrf": csrf(admin), "mode": "ip", "value": "badhash"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='votes_delete'").fetchone()[0] == 1


def test_category_with_votes_cannot_be_deleted(admin, conn, dishes):
    conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, created_at) VALUES (?,?,1,0)",
                 (dishes[0], "v-xxxxxxxxxxxx"))
    r = admin.post("/admin/categories", data={"csrf": csrf(admin), "action": "delete", "id": "1"},
                   follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert conn.execute("SELECT COUNT(*) FROM categories WHERE id = 1").fetchone()[0] == 1


def test_category_color_update_every_row(admin, conn):
    """Every category's colour is editable, typed in any common hex form; stored as #RRGGBB."""
    rows = conn.execute("SELECT id, name, emoji, position FROM categories ORDER BY id").fetchall()
    for i, c in enumerate(rows):
        typed = ["#1a2b3c", "1A2B3C", "#abc", "abc"][i % 4]
        r = admin.post("/admin/categories", follow_redirects=False, data={
            "csrf": csrf(admin), "action": "update", "id": str(c["id"]), "name": c["name"],
            "color": f"  {typed} ", "emoji": c["emoji"], "position": str(c["position"]), "active": "1"})
        assert "ok=" in r.headers["location"], c["id"]
        want = "#1A2B3C" if i % 4 < 2 else "#AABBCC"
        assert conn.execute("SELECT color FROM categories WHERE id = ?", (c["id"],)).fetchone()[0] == want


def test_category_color_rejects_garbage(admin, conn):
    before = conn.execute("SELECT color FROM categories WHERE id = 1").fetchone()[0]
    for bad in ("", "red", "#12345", "#ggg000", "url(x)"):
        r = admin.post("/admin/categories", follow_redirects=False, data={
            "csrf": csrf(admin), "action": "update", "id": "1", "name": "X", "color": bad, "position": "1"})
        assert "err=" in r.headers["location"], bad
    assert conn.execute("SELECT color FROM categories WHERE id = 1").fetchone()[0] == before


def test_category_page_has_typed_color_field(admin):
    html = admin.get("/admin/categories").text
    assert 'name="color" id="color-1" value="#' in html
    assert 'data-color-for="color-1"' in html
    # the native picker is not submitted: only the typed field is
    assert html.count('type="color" value=') == html.count("data-color-for=")
    assert 'type="color" name="color"' not in html


def test_category_pastel_button_recolours_all(admin, conn):
    from app import colors
    conn.execute("UPDATE categories SET color = '#FFFFFF'")
    r = admin.post("/admin/categories", data={"csrf": csrf(admin), "action": "pastel"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    got = [r[0] for r in conn.execute("SELECT color FROM categories ORDER BY position, id")]
    assert got == colors.palette(len(got))
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='category_pastel'").fetchone()[0] == 1


def test_submission_queue_approve(admin, conn):
    from app import services
    did = services.create_dish(conn, title="Plat en attente", description="x" * 30, source="user", status="pending")
    r = admin.post(f"/admin/propositions/{did}", data={"csrf": csrf(admin), "action": "approve"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT status FROM dishes WHERE id = ?", (did,)).fetchone()["status"] == "active"


def test_banned_words_add_test_and_rescan(admin, conn):
    from app import services
    did = services.create_dish(conn, title="Ragoût zorglub", description="x" * 30, source="user")
    token = csrf(admin, "/admin/mots")
    admin.post("/admin/mots", data={"csrf": token, "action": "add", "word": "Zorglub", "mode": "word"})
    r = admin.post("/admin/mots", data={"csrf": token, "action": "test", "text": "un z0rglub ici"})
    assert "Bloqué" in r.text
    admin.post("/admin/mots", data={"csrf": token, "action": "rescan"})
    assert conn.execute("SELECT status FROM dishes WHERE id = ?", (did,)).fetchone()["status"] == "flagged"


def test_totp_window():
    """verify_totp accepts the adjacent step (clock skew) but not codes from far away."""
    import sqlite3
    from app import db as dbmod
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
    totp = pyotp.TOTP(TOTP_SECRET)
    now = time.time()
    assert auth.verify_totp(c, TOTP_SECRET, totp.at(now - 30), now=now)
    assert not auth.verify_totp(c, TOTP_SECRET, totp.at(now - 300), now=now)
    assert dbmod.get_setting(c, "admin_totp_last_step")
