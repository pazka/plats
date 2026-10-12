import os
import stat

from app import rewards
from app.cli import merge_env
from app.main import CSP


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["content-security-policy"] == CSP
    assert "script-src 'self'" in CSP and "unsafe-inline" not in CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_no_external_resources_in_pages(client, dishes):
    import re
    for path in ("/", "/proposer", "/plats", "/stats", "/a-propos", "/admin/login"):
        html = client.get(path).text
        for attr in re.findall(r'(?:src|href)="([^"]+)"', html):
            assert attr.startswith(("/", "#")) or "wikidata.org" in attr or "github.com" in attr \
                or "wikipedia.org" in attr, (path, attr)
        assert "<script>" not in html  # no inline executable script


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok" and body["categories"] == 9


def test_xff_trusted_only_from_proxy(app, conn, dishes, no_think):
    from fastapi.testclient import TestClient
    # TestClient peer is "testclient" (not an IP) → untrusted: XFF ignored.
    c = TestClient(app, base_url="https://testserver")
    q = c.get("/api/question").json()["next"]
    c.post("/api/vote", json={"token": q["token"], "choice": "pain"}, headers={"X-Forwarded-For": "198.51.100.9"})
    from app.security import hash_ip
    row = conn.execute("SELECT ip_hash FROM votes").fetchone()
    assert row["ip_hash"] == hash_ip("testclient", "test-ip-salt")


def test_client_ip_resolution(settings):
    from types import SimpleNamespace

    from app.web import client_ip

    def req(peer, xff=""):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff} if xff else {})

    assert client_ip(req("203.0.113.5", "1.2.3.4"), settings) == "203.0.113.5"  # untrusted peer
    assert client_ip(req("172.18.0.2", "1.2.3.4"), settings) == "1.2.3.4"  # Traefik on docker net
    assert client_ip(req("172.18.0.2", "6.6.6.6, 1.2.3.4"), settings) == "1.2.3.4"  # spoofed left part ignored
    assert client_ip(req("172.18.0.2", "1.2.3.4, 10.0.0.3"), settings) == "1.2.3.4"


def test_rewards_escalate_forever():
    assert rewards.milestone(4) is None
    assert [rewards.milestone(n)["tier"] for n in (5, 10, 15, 20)] == ["small"] * 4
    assert rewards.milestone(25)["title"] == "SUPER !" and rewards.milestone(25)["tier"] == "super"
    assert rewards.milestone(30)["tier"] == "genial" and rewards.milestone(30)["title"].startswith("GÉNIAL")
    assert rewards.milestone(31) is None
    levels = [rewards.milestone(n)["level"] for n in range(30, 1001, 5)]
    assert levels == sorted(levels) and len(set(levels)) == len(levels)
    intensities = [rewards.intensity(n) for n in (1, 25, 100, 1000, 10000)]
    assert intensities == sorted(intensities) and len(set(intensities)) == 5
    badges = [rewards.badge_for(n) for n in range(30, 400, 10)]
    assert len(set(badges)) == len(badges)  # a new badge every 10, forever
    titles = {rewards.milestone(n)["title"] for n in range(30, 2000, 10)}
    assert len(titles) > 50
    assert rewards.palette(10) != rewards.palette(30) != rewards.palette(60) != rewards.palette(150)


def test_reward_progress():
    r = rewards.reward(12)
    assert r["progress"] == {"from": 10, "to": 15, "value": 12}


def test_merge_env_preserves_other_lines(tmp_path):
    env = tmp_path / "plats.env"
    env.write_text("# comment\nPLATS_CONTACT=me@example.org\nPLATS_ADMIN_TOTP_SECRET='OLD'\n")
    merge_env(env, {"PLATS_ADMIN_PASSWORD_HASH": "$argon2id$v=19$m=65536,t=3,p=4$abc$def",
                    "PLATS_ADMIN_TOTP_SECRET": "NEWSECRET"})
    text = env.read_text()
    assert "# comment" in text and "PLATS_CONTACT=me@example.org" in text
    assert "PLATS_ADMIN_TOTP_SECRET='NEWSECRET'" in text and "OLD" not in text
    assert "PLATS_ADMIN_PASSWORD_HASH='$argon2id$" in text
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600


def test_config_strips_quotes(monkeypatch, tmp_path):
    from app.config import load_settings
    monkeypatch.setenv("PLATS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PLATS_ADMIN_PASSWORD_HASH", "'$argon2id$v=19$x'")
    monkeypatch.delenv("PLATS_SECRET_KEY", raising=False)
    s = load_settings()
    assert s.admin_password_hash == "$argon2id$v=19$x"
    # generated secrets are persisted in the data volume and stable across restarts
    assert s.secret_key == load_settings().secret_key
    assert stat.S_IMODE(os.stat(tmp_path / ".secrets" / "secret_key").st_mode) == 0o600


def test_public_pages_and_search(client, conn, dishes):
    assert "Quiche lorraine" in client.get("/plats?q=QUICHE").text
    assert "Soupe à l&#39;oignon" in client.get("/plats?q=soupe a l oignon").text
    assert client.get("/plats?q=%22%27*").status_code == 200  # FTS syntax can't be injected
    for sort in ("az", "votes", "consensus", "dispute", "recent", "bogus"):
        assert client.get(f"/plats?tri={sort}").status_code == 200
    assert client.get("/plat/cassoulet").status_code == 200
    assert client.get("/plat/nope").status_code == 404
    assert client.get("/media/../../etc/passwd").status_code == 404


def test_stats_page(client, conn, dishes):
    for i in range(5):
        conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, created_at) VALUES (?,?,?,strftime('%s','now'))",
                     (dishes[0], f"visitor-{i}-xxxxxx", 6))
    html = client.get("/stats").text
    assert "Quiche lorraine" in html and "Les plus consensuels" in html
