import re

import pytest

from app import db, security


@pytest.fixture
def quick_form(conn):
    db.set_setting(conn, "min_fill_seconds", "0")


def form_token(client):
    html = client.get("/proposer").text
    return re.search(r'name="form_token" value="([^"]+)"', html).group(1)


def propose(client, nom, description, region="", decision="", pow_ok=True, honeypot="", token=None):
    token = token or form_token(client)
    data = {"form_token": token, "nom": nom, "description": description, "region": region,
            "pow_nonce": security.solve_pow(token, 8) if pow_ok else "1", "website": honeypot}
    if decision:
        data["decision"] = decision
    return client.post("/proposer", data=data)


DESC = "Une galette de sarrasin garnie, pliée en carré et servie chaude sur une assiette plate."


def test_successful_submission_enters_pool(client, conn, quick_form):
    r = propose(client, "Galette complète", DESC, "Bretagne")
    assert r.status_code == 200 and "Merci" in r.text
    row = conn.execute("SELECT * FROM dishes WHERE title = 'Galette complète'").fetchone()
    assert row["status"] == "active" and row["source"] == "user" and row["region"] == "Bretagne"
    assert row["submitter_ip_hash"] and row["submitter_ip_hash"] != "testclient"


def test_submitter_does_not_get_own_dish_right_away(client, conn, quick_form, no_think):
    propose(client, "Galette complète", DESC)
    assert client.get("/api/question").json()["next"] is None


def test_validation_lengths(client, conn, quick_form):
    r = propose(client, "X", "trop court")
    assert r.status_code == 400
    assert "entre 2 et 80" in r.text and "entre 20 et 600" in r.text
    assert conn.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 0


def test_honeypot_silently_drops(client, conn, quick_form):
    r = propose(client, "Galette complète", DESC, honeypot="http://spam")
    assert r.status_code == 200 and "Merci" in r.text
    assert conn.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 0


def test_pow_required(client, conn, quick_form):
    r = propose(client, "Galette complète", DESC, pow_ok=False)
    assert r.status_code == 400 and "anti-robot" in r.text
    assert conn.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 0


def test_min_fill_time(client, conn):
    r = propose(client, "Galette complète", DESC)  # default 3 s minimum
    assert r.status_code == 400 and "trop rapide" in r.text


def test_form_token_single_use(client, conn, quick_form):
    token = form_token(client)
    assert propose(client, "Galette complète", DESC, token=token).status_code == 200
    r = propose(client, "Autre galette", DESC, token=token)
    assert r.status_code == 400 and "déjà été envoyé" in r.text


def test_lookalike_then_same_or_different(client, conn, dishes, quick_form):
    r = propose(client, "La quiche lorraines", DESC)
    assert r.status_code == 200 and "peut-être l'un de ceux-là" in r.text and "Quiche lorraine" in r.text
    assert conn.execute("SELECT COUNT(*) FROM dishes WHERE source='user'").fetchone()[0] == 0
    r = propose(client, "La quiche lorraines", DESC, decision=f"same:{dishes[0]}")
    assert "déjà dans la liste" in r.text
    assert conn.execute("SELECT COUNT(*) FROM dishes WHERE source='user'").fetchone()[0] == 0
    r = propose(client, "La quiche lorraines", DESC, decision="different")
    assert "Merci" in r.text
    assert conn.execute("SELECT COUNT(*) FROM dishes WHERE source='user'").fetchone()[0] == 1


def test_banned_word_submission_is_flagged_not_shown(client, conn, quick_form):
    r = propose(client, "Plat de c0nnard", DESC)
    assert "Merci" in r.text
    row = conn.execute("SELECT status, flag_reason FROM dishes WHERE source='user'").fetchone()
    assert row["status"] == "flagged" and "connard" in row["flag_reason"]
    assert "Plat de c0nnard" not in client.get("/plats").text


def test_require_approval(client, conn, quick_form):
    db.set_setting(conn, "require_approval", "1")
    propose(client, "Galette complète", DESC)
    assert conn.execute("SELECT status FROM dishes WHERE source='user'").fetchone()["status"] == "pending"


def test_submission_rate_limit(client, conn, quick_form):
    db.set_setting(conn, "rl_submit", "2/1h")
    codes = [propose(client, name, DESC).status_code for name in ("Galette", "Bouillabaisse", "Tartiflette")]
    assert codes == [200, 200, 429]


def test_live_similar_api(client, dishes):
    items = client.get("/api/similaires", params={"nom": "cassoulé"}).json()["items"]
    assert items and items[0]["title"] == "Cassoulet"
