"""Upgrade safety net: pins what must survive a Python, FastAPI/Starlette, argon2-cffi, pyotp or SQLite bump.

Characterization only (what the app does today). See TESTING.md. Regenerate the schema snapshot only together
with a new migration: UPDATE_SCHEMA_SNAPSHOT=1 python -m pytest tests/test_upgrade.py
"""
import json
import os
import sys
from pathlib import Path

import pyotp
import pytest

from app import auth, db

SCHEMA_SNAPSHOT = Path(__file__).parent / "fixtures" / "schema.json"

# Every admin route: without a session each one redirects to the login page (GET and POST alike).
ADMIN_ROUTES = [
    ("GET", ""), ("POST", "/logout"), ("GET", "/plats"), ("POST", "/plats/action"), ("POST", "/plats/fusion"),
    ("POST", "/plats/nouveau"), ("POST", "/import"), ("GET", "/plats/1"), ("POST", "/plats/1"),
    ("GET", "/propositions"), ("POST", "/propositions/1"), ("GET", "/votes"), ("POST", "/votes/supprimer"),
    ("GET", "/categories"), ("POST", "/categories"), ("GET", "/mots"), ("POST", "/mots"), ("GET", "/reglages"),
    ("POST", "/reglages"), ("GET", "/export.xlsx"), ("GET", "/journal"),
]

# Every public route with its status and content type for a first, cookie-less, form-less request.
PUBLIC_ROUTES = [
    ("GET", "/", 200, "text/html"),
    ("POST", "/api/vote", 400, "application/json"),
    ("GET", "/api/question", 200, "application/json"),
    ("POST", "/voter", 303, None),
    ("GET", "/proposer", 200, "text/html"),
    ("POST", "/proposer", 400, "text/html"),
    ("GET", "/api/similaires", 200, "application/json"),
    ("GET", "/plats", 200, "text/html"),
    ("GET", "/plat/inconnu", 404, "text/html"),
    ("GET", "/stats", 200, "text/html"),
    ("GET", "/a-propos", 200, "text/html"),
    ("GET", "/categories.css", 200, "text/css"),
    ("GET", "/media/inconnu", 404, "text/html"),
    ("GET", "/healthz", 200, "application/json"),
    ("GET", "/robots.txt", 200, "text/plain"),
    ("GET", "/admin/login", 200, "text/html"),
    ("POST", "/admin/login", 403, "text/html"),
    ("GET", "/nope", 404, "text/html"),
    ("GET", "/.env", 404, "text/html"),
    ("GET", "/admin/nope", 404, "text/html"),
]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_every_admin_route_requires_login(client, method, path):
    r = client.request(method, "/admin" + path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/login"


@pytest.mark.parametrize("method,path,status,ctype", PUBLIC_ROUTES)
def test_public_route_status_and_type(client, method, path, status, ctype):
    r = client.request(method, path, follow_redirects=False)
    assert r.status_code == status
    if ctype:
        assert r.headers["content-type"].split(";")[0] == ctype


def test_api_question_shape(client, dishes):
    nxt = client.get("/api/question").json()["next"]
    assert set(nxt) == {"dish", "token"}
    assert set(nxt["dish"]) == {"id", "slug", "title", "description", "region", "image", "image_author",
                                "image_license", "image_license_url", "image_source_url", "wiki_url", "source"}


def _schema(conn):
    objects = conn.execute(
        "SELECT type, name, tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    columns = {}
    for o in objects:
        if o["type"] == "table":
            columns[o["name"]] = [
                f"{c['name']} {c['type']}{' NOT NULL' if c['notnull'] else ''}{' PK' if c['pk'] else ''}"
                for c in conn.execute(f'PRAGMA table_info("{o["name"]}")')
            ]
    return {"objects": [f"{o['type']} {o['name']} on {o['tbl_name']}" for o in objects], "columns": columns}


def test_fresh_database_schema_matches_snapshot(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    assert db.migrate(path) == 2
    conn = db.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        current = _schema(conn)
    finally:
        conn.close()
    if os.environ.get("UPDATE_SCHEMA_SNAPSHOT") == "1":
        SCHEMA_SNAPSHOT.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert SCHEMA_SNAPSHOT.exists(), "schema snapshot missing: run with UPDATE_SCHEMA_SNAPSHOT=1"
    assert current == json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "x.sqlite3"
    assert db.migrate(path) == 2
    assert db.migrate(path) == 2


# Produced by app.auth.hash_password (argon2-cffi 25.1.0 defaults). The owner's real admin hash was made the
# same way by set-admin.sh: it must keep verifying after any upgrade or the admin is locked out.
KNOWN_HASH = "$argon2id$v=19$m=65536,t=3,p=4$gMvyWFLcZklDXOpmpk4cZA$Gefs1ZJ3+R/bFgG3LgFhcNE2QBtQDYBskVNIIBOO9Nw"


def test_admin_hash_made_before_the_upgrade_still_verifies():
    assert auth.verify_password(KNOWN_HASH, "known-password-v1")
    assert not auth.verify_password(KNOWN_HASH, "wrong")
    assert not auth.verify_password("not-a-hash", "known-password-v1")


def test_new_hashes_are_argon2id():
    h = auth.hash_password("x")
    assert h.startswith("$argon2id$v=19$") and auth.verify_password(h, "x")


def test_totp_is_rfc6238_with_a_fixed_secret(conn):
    # Fixed secret + fixed time: pyotp must still produce the same code, and the app must accept it once.
    secret, at = "JBSWY3DPEHPK3PXP", 1_700_000_000
    assert pyotp.TOTP(secret).at(at) == "324550"
    assert auth.verify_totp(conn, secret, "324550", now=at)
    assert not auth.verify_totp(conn, secret, "324550", now=at)  # replay
    assert not auth.verify_totp(conn, secret, "000000", now=at + 600)


def test_python_is_a_supported_version():
    assert sys.version_info >= (3, 12)
