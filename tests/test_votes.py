import time

from app import db, security
from app.security import issue_vote_token
from app.web import VISITOR_COOKIE


def question(client):
    r = client.get("/api/question")
    assert r.status_code == 200
    return r.json()["next"]


def vote(client, token, choice):
    return client.post("/api/vote", json={"token": token, "choice": choice})


def test_first_visit_sets_secure_httponly_cookie(client, dishes):
    r = client.get("/")
    cookie = r.headers["set-cookie"]
    assert VISITOR_COOKIE in cookie and "HttpOnly" in cookie and "Secure" in cookie
    assert "samesite=lax" in cookie.lower()


def test_vote_flow_returns_reward_distribution_and_next(client, dishes, no_think):
    q = question(client)
    r = vote(client, q["token"], "tarte")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and not body["skipped"]
    assert body["same_pct"] == 100
    assert body["distribution"]["total"] == 1
    assert body["reward"]["count"] == 1
    assert body["next"]["dish"]["id"] != q["dish"]["id"]


def test_token_is_single_use(client, dishes, no_think):
    q = question(client)
    assert vote(client, q["token"], "tarte").status_code == 200
    r = vote(client, q["token"], "soupe")
    assert r.status_code == 409
    assert r.json()["error"] == "token_used"


def test_one_vote_per_visitor_and_dish(client, conn, dishes, no_think):
    q = question(client)
    assert vote(client, q["token"], "tarte").status_code == 200
    # A second, validly signed token for the same dish must not create a second vote.
    vid = security.VisitorSigner("test-secret-key").loads(client.cookies.get(VISITOR_COOKIE))
    tok2 = issue_vote_token("test-secret-key", q["dish"]["id"], vid)
    r = vote(client, tok2, "soupe")
    assert r.status_code == 409 and r.json()["error"] == "already_answered"
    n = conn.execute("SELECT COUNT(*) FROM votes WHERE dish_id = ?", (q["dish"]["id"],)).fetchone()[0]
    assert n == 1


def test_token_of_another_visitor_rejected(app, client, dishes, no_think):
    q = question(client)
    from tests.conftest import make_client
    other = make_client(app)
    r = other.post("/api/vote", json={"token": q["token"], "choice": "tarte"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_token"


def test_vote_requires_think_time(client, dishes):
    q = question(client)
    r = vote(client, q["token"], "tarte")
    assert r.status_code == 400 and r.json()["error"] == "too_fast"


def test_expired_token(client, dishes, no_think):
    vid = None
    client.get("/")
    vid = security.VisitorSigner("test-secret-key").loads(client.cookies.get(VISITOR_COOKIE))
    old = issue_vote_token("test-secret-key", dishes[0], vid, now_ms=int((time.time() - 601) * 1000))
    r = vote(client, old, "tarte")
    assert r.status_code == 400 and r.json()["error"] == "expired_token"


def test_skip_is_recorded_but_not_a_vote(client, conn, dishes, no_think):
    q = question(client)
    r = vote(client, q["token"], "skip")
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] and body["reward"] is None and body["count"] == 0
    row = conn.execute("SELECT category_id FROM votes WHERE dish_id = ?", (q["dish"]["id"],)).fetchone()
    assert row["category_id"] is None
    # skipped dish is not asked again
    for _ in range(20):
        assert question(client)["dish"]["id"] != q["dish"]["id"]


def test_invalid_category_rejected(client, dishes, no_think):
    q = question(client)
    assert vote(client, q["token"], "pizza").status_code == 400


def test_end_screen_when_everything_answered(client, dishes, no_think):
    for _ in range(len(dishes)):
        q = question(client)
        assert vote(client, q["token"], "soupe").status_code == 200
    assert question(client) is None
    assert "Vous avez fait le tour" in client.get("/").text


def test_count_persists_across_requests(client, dishes, no_think):
    for _ in range(3):
        q = question(client)
        vote(client, q["token"], "pain")
    html = client.get("/").text
    assert '<span id="count">3</span>' in html


def test_vote_rate_limit(client, conn, dishes, no_think):
    db.set_setting(conn, "rl_vote", "2/1h")
    codes = []
    for _ in range(3):
        q = question(client)
        codes.append(vote(client, q["token"], "pain").status_code)
    assert codes == [200, 200, 429]


def test_no_js_form_fallback(client, dishes, no_think):
    html = client.get("/").text
    token = html.split('name="token" id="token" value="')[1].split('"')[0]
    r = client.post("/voter", data={"token": token, "choice": "gratin"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?apres=")
    page = client.get(r.headers["location"]).text
    assert "ont répondu comme toi" in page


def test_hidden_dish_cannot_be_voted(client, conn, dishes, no_think):
    q = question(client)
    conn.execute("UPDATE dishes SET status = 'hidden' WHERE id = ?", (q["dish"]["id"],))
    r = vote(client, q["token"], "pain")
    assert r.status_code == 410
