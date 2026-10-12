"""Signed identifiers and tokens: visitor cookie, single-use vote tokens, IP hashing,
proposal form tokens and the self-hosted proof-of-work."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time

from itsdangerous import BadSignature, URLSafeSerializer

VOTE_TOKEN_TTL = 600  # seconds
FORM_TOKEN_TTL = 6 * 3600


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hmac(key: str, msg: str) -> str:
    return _b64(hmac.new(key.encode(), msg.encode(), hashlib.sha256).digest())


def hash_ip(ip: str, salt: str) -> str:
    """IPs are never stored raw: HMAC(ip, salt), truncated (still unique enough for abuse hints)."""
    return hmac.new(salt.encode(), (ip or "").encode(), hashlib.sha256).hexdigest()[:20]


# ------------------------------------------------------------------ visitor cookie

class VisitorSigner:
    def __init__(self, secret: str):
        self._s = URLSafeSerializer(secret, salt="plats-visitor")

    def new_id(self) -> str:
        return secrets.token_urlsafe(16)

    def dumps(self, visitor_id: str) -> str:
        return self._s.dumps(visitor_id)

    def loads(self, cookie: str | None) -> str | None:
        if not cookie:
            return None
        try:
            value = self._s.loads(cookie)
        except BadSignature:
            return None
        return value if isinstance(value, str) and 10 <= len(value) <= 64 else None


# ------------------------------------------------------------------ vote tokens

class TokenError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def issue_vote_token(secret: str, dish_id: int, visitor_id: str, now_ms: int | None = None) -> str:
    issued = int(time.time() * 1000) if now_ms is None else now_ms
    sig = _hmac(secret, f"vote|{dish_id}|{visitor_id}|{issued}")
    return f"{dish_id}.{issued}.{sig}"


def check_vote_token(secret: str, token: str, visitor_id: str, min_think_ms: int,
                     now_ms: int | None = None) -> tuple[int, str]:
    """Validate signature, TTL and think time. Returns (dish_id, sig). Does NOT consume it."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        dish_part, issued_part, sig = (token or "").split(".")
        dish_id, issued = int(dish_part), int(issued_part)
    except ValueError:
        raise TokenError("invalid_token") from None
    expected = _hmac(secret, f"vote|{dish_id}|{visitor_id}|{issued}")
    if not hmac.compare_digest(expected, sig):
        raise TokenError("invalid_token")
    age = now - issued
    if age > VOTE_TOKEN_TTL * 1000 or age < -5000:
        raise TokenError("expired_token")
    if age < min_think_ms:
        raise TokenError("too_fast")
    return dish_id, sig


def consume_token(conn: sqlite3.Connection, sig: str, ttl_seconds: int = VOTE_TOKEN_TTL) -> bool:
    """Mark a token signature as used. False if it was already used. Call inside a transaction."""
    now = int(time.time())
    try:
        conn.execute("INSERT INTO used_tokens(sig, expires_at) VALUES (?, ?)", (sig, now + ttl_seconds + 60))
    except sqlite3.IntegrityError:
        return False
    return True


def purge_used_tokens(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM used_tokens WHERE expires_at < ?", (int(time.time()),))


# ------------------------------------------------------------------ proposal form + proof-of-work

def issue_form_token(secret: str, visitor_id: str, now: float | None = None) -> str:
    """Token embedded in the proposal form: binds visitor + issue time, doubles as CSRF token
    and PoW challenge (the client must find a nonce for it)."""
    issued = int((time.time() if now is None else now) * 1000)
    rand = secrets.token_urlsafe(9)
    sig = _hmac(secret, f"form|{visitor_id}|{issued}|{rand}")
    return f"{issued}.{rand}.{sig}"


def check_form_token(secret: str, token: str, visitor_id: str, min_fill_seconds: float,
                     now: float | None = None) -> str:
    """Returns the token signature (for single-use bookkeeping). Raises TokenError."""
    now_ms = int((time.time() if now is None else now) * 1000)
    try:
        issued_part, rand, sig = (token or "").split(".")
        issued = int(issued_part)
    except ValueError:
        raise TokenError("invalid_form") from None
    expected = _hmac(secret, f"form|{visitor_id}|{issued}|{rand}")
    if not hmac.compare_digest(expected, sig):
        raise TokenError("invalid_form")
    age = now_ms - issued
    if age > FORM_TOKEN_TTL * 1000:
        raise TokenError("expired_form")
    if age < min_fill_seconds * 1000:
        raise TokenError("too_fast")
    return sig


def leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def check_pow(challenge: str, nonce: str, difficulty: int) -> bool:
    if not nonce or len(nonce) > 32 or not nonce.isdigit():
        return False
    digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()
    return leading_zero_bits(digest) >= difficulty


def solve_pow(challenge: str, difficulty: int) -> str:
    """Reference solver (used by tests; the browser has its own JS implementation)."""
    nonce = 0
    while True:
        if check_pow(challenge, str(nonce), difficulty):
            return str(nonce)
        nonce += 1


def new_secret_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
