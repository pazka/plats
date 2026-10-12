"""Single-admin authentication: argon2id password + TOTP, DB-backed sessions, CSRF, lockout."""
from __future__ import annotations

import hmac
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import db
from .security import new_secret_token, sha256_hex

SESSION_TTL = 8 * 3600
LOCKOUT_SECONDS = 15 * 60
GLOBAL_FAILURES_PER_HOUR = 50
SESSION_COOKIE = "plats_admin"
LOGIN_CSRF_COOKIE = "plats_login"

_ph = PasswordHasher()  # argon2id with library defaults


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    if not stored_hash or not password:
        return False
    try:
        return _ph.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_totp(conn, secret: str, code: str, now: float | None = None) -> bool:
    """Accepts the current code ±1 step, and never the same time-step twice (replay)."""
    code = (code or "").replace(" ", "")
    if not secret or not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(secret)
    now = time.time() if now is None else now
    last_step = int(db.get_setting(conn, "admin_totp_last_step", "0") or 0)
    for offset in (-1, 0, 1):
        at = now + offset * totp.interval
        step = int(at // totp.interval)
        if hmac.compare_digest(totp.at(at), code):
            if step <= last_step:
                return False
            db.set_setting(conn, "admin_totp_last_step", str(step))
            return True
    return False


def lockout_remaining(conn, ip_hash: str, max_failures: int, window: int) -> int:
    """Seconds until login is allowed again (0 = allowed)."""
    now = int(time.time())
    count, last = conn.execute(
        "SELECT COUNT(*), MAX(ts) FROM login_attempts WHERE ip_hash = ? AND success = 0 AND ts >= ?",
        (ip_hash, now - window),
    ).fetchone()
    if count >= max_failures and last + LOCKOUT_SECONDS > now:
        return last + LOCKOUT_SECONDS - now
    # Distributed guessing: a global brake, independent of the source address.
    count, last = conn.execute(
        "SELECT COUNT(*), MAX(ts) FROM login_attempts WHERE success = 0 AND ts >= ?", (now - 3600,)
    ).fetchone()
    if count >= GLOBAL_FAILURES_PER_HOUR and last + LOCKOUT_SECONDS > now:
        return last + LOCKOUT_SECONDS - now
    return 0


def record_attempt(conn, ip_hash: str, success: bool) -> None:
    now = int(time.time())
    conn.execute("INSERT INTO login_attempts(ts, ip_hash, success) VALUES (?,?,?)", (now, ip_hash, int(success)))
    if success:
        conn.execute("DELETE FROM login_attempts WHERE ip_hash = ? AND success = 0", (ip_hash,))
    conn.execute("DELETE FROM login_attempts WHERE ts < ?", (now - 30 * 86400,))


def create_session(conn) -> tuple[str, str]:
    token, csrf = new_secret_token(), new_secret_token()
    now = int(time.time())
    conn.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (now,))
    conn.execute(
        "INSERT INTO admin_sessions(id_hash, csrf, created_at, expires_at) VALUES (?,?,?,?)",
        (sha256_hex(token), csrf, now, now + SESSION_TTL),
    )
    return token, csrf


def get_session(conn, token: str | None):
    if not token:
        return None
    return conn.execute(
        "SELECT * FROM admin_sessions WHERE id_hash = ? AND expires_at > ?",
        (sha256_hex(token), int(time.time())),
    ).fetchone()


def delete_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM admin_sessions WHERE id_hash = ?", (sha256_hex(token),))


def csrf_ok(session, submitted: str | None) -> bool:
    return bool(session and submitted and hmac.compare_digest(session["csrf"], submitted))
