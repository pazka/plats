import pytest

from app import security
from app.security import TokenError, check_vote_token, issue_vote_token

SECRET = "s3cret"


def test_valid_token_roundtrip():
    tok = issue_vote_token(SECRET, 42, "visitor-abcdef", now_ms=1_000_000)
    dish_id, sig = check_vote_token(SECRET, tok, "visitor-abcdef", 600, now_ms=1_000_000 + 1000)
    assert dish_id == 42 and sig


def test_token_bound_to_visitor_and_dish():
    tok = issue_vote_token(SECRET, 42, "visitor-abcdef", now_ms=1_000_000)
    with pytest.raises(TokenError) as e:
        check_vote_token(SECRET, tok, "someone-else-xyz", 600, now_ms=1_002_000)
    assert e.value.code == "invalid_token"
    forged = tok.replace("42.", "43.", 1)
    with pytest.raises(TokenError):
        check_vote_token(SECRET, forged, "visitor-abcdef", 600, now_ms=1_002_000)
    with pytest.raises(TokenError):
        check_vote_token("other-secret", tok, "visitor-abcdef", 600, now_ms=1_002_000)


def test_token_ttl_10_minutes():
    tok = issue_vote_token(SECRET, 1, "visitor-abcdef", now_ms=0)
    check_vote_token(SECRET, tok, "visitor-abcdef", 600, now_ms=599_000)
    with pytest.raises(TokenError) as e:
        check_vote_token(SECRET, tok, "visitor-abcdef", 600, now_ms=600_001)
    assert e.value.code == "expired_token"


def test_minimum_think_time():
    tok = issue_vote_token(SECRET, 1, "visitor-abcdef", now_ms=10_000)
    with pytest.raises(TokenError) as e:
        check_vote_token(SECRET, tok, "visitor-abcdef", 600, now_ms=10_300)
    assert e.value.code == "too_fast"
    check_vote_token(SECRET, tok, "visitor-abcdef", 600, now_ms=10_600)


@pytest.mark.parametrize("garbage", ["", "abc", "1.2", "x.y.z", "1.2.3.4"])
def test_garbage_tokens_rejected(garbage):
    with pytest.raises(TokenError):
        check_vote_token(SECRET, garbage, "visitor-abcdef", 0)


def test_consume_is_single_use(conn):
    assert security.consume_token(conn, "sig-1") is True
    assert security.consume_token(conn, "sig-1") is False
    assert security.consume_token(conn, "sig-2") is True


def test_visitor_cookie_signature():
    signer = security.VisitorSigner(SECRET)
    vid = signer.new_id()
    assert signer.loads(signer.dumps(vid)) == vid
    assert signer.loads(signer.dumps(vid) + "x") is None
    assert security.VisitorSigner("other").loads(signer.dumps(vid)) is None
    assert signer.loads(None) is None


def test_ip_hash_is_not_raw_and_stable():
    h = security.hash_ip("203.0.113.7", "salt")
    assert "." not in h and len(h) == 20 and h == security.hash_ip("203.0.113.7", "salt")
    assert h != security.hash_ip("203.0.113.7", "other-salt")


def test_proof_of_work():
    nonce = security.solve_pow("challenge", 10)
    assert security.check_pow("challenge", nonce, 10)
    assert not security.check_pow("challenge", "", 10)
    assert not security.check_pow("challenge", "abc", 10)
    assert security.leading_zero_bits(bytes([0, 0, 0x0F])) == 20


def test_form_token_min_fill_and_binding():
    tok = security.issue_form_token(SECRET, "visitor-abcdef", now=1000.0)
    with pytest.raises(TokenError) as e:
        security.check_form_token(SECRET, tok, "visitor-abcdef", 3, now=1001.0)
    assert e.value.code == "too_fast"
    assert security.check_form_token(SECRET, tok, "visitor-abcdef", 3, now=1004.0)
    with pytest.raises(TokenError):
        security.check_form_token(SECRET, tok, "visitor-other", 3, now=1004.0)
