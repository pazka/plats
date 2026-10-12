from app.ratelimit import RateLimiter, parse_rules, validate_rules


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_parse_rules():
    assert parse_rules("3/3s,300/1h") == [(3, 3), (300, 3600)]
    assert parse_rules("5/h, 20/1d") == [(5, 3600), (20, 86400)]
    assert parse_rules("garbage") == []
    assert validate_rules("30/1m") and not validate_rules("30/1m,nope") and not validate_rules("")


def test_sliding_window():
    clock = Clock()
    rl = RateLimiter(clock)
    rules = [(2, 10)]
    assert rl.hit(["k"], rules) and rl.hit(["k"], rules)
    assert not rl.hit(["k"], rules)
    clock.t += 5
    assert not rl.hit(["k"], rules)
    clock.t += 5.1  # first hit is now out of the window
    assert rl.hit(["k"], rules)


def test_multiple_rules_burst_and_long_window():
    clock = Clock()
    rl = RateLimiter(clock)
    rules = parse_rules("3/3s,5/1h")
    for _ in range(3):
        assert rl.hit(["v"], rules)
    assert not rl.hit(["v"], rules)  # burst
    clock.t += 4
    assert rl.hit(["v"], rules) and rl.hit(["v"], rules)
    clock.t += 4
    assert not rl.hit(["v"], rules)  # hourly cap of 5 reached


def test_limit_applies_per_visitor_and_per_ip():
    clock = Clock()
    rl = RateLimiter(clock)
    rules = [(2, 60)]
    assert rl.hit(["ip:A", "v:1"], rules)
    assert rl.hit(["ip:A", "v:2"], rules)
    # visitor 3 is fresh, but IP A is exhausted
    assert not rl.hit(["ip:A", "v:3"], rules)
    # visitor 1 from another IP: visitor key has 1 hit, still allowed once
    assert rl.hit(["ip:B", "v:1"], rules)
    assert not rl.hit(["ip:C", "v:1"], rules)


def test_rejected_hits_are_not_recorded():
    clock = Clock()
    rl = RateLimiter(clock)
    rules = [(1, 10)]
    assert rl.hit(["k"], rules)
    for _ in range(5):
        assert not rl.hit(["k"], rules)
    clock.t += 10.5
    assert rl.hit(["k"], rules)


def test_similar_check_rate_limited(client, conn, dishes):
    from app import db
    db.set_setting(conn, "rl_similar", "2/1m")
    codes = [client.get("/api/similaires?nom=quiche").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_per_ip_limit_is_separate_and_looser(app, conn, dishes):
    """Visitors sharing one IP (NAT) don't block each other at the per-visitor rate,
    but the per-IP ceiling still applies across them."""
    from app import db
    from tests.conftest import make_client
    db.set_setting(conn, "rl_similar", "2/1m")
    db.set_setting(conn, "rl_similar_ip", "5/1m")
    a, b, c = make_client(app), make_client(app), make_client(app)
    assert [a.get("/api/similaires?nom=x").status_code for _ in range(3)] == [200, 200, 429]
    assert [b.get("/api/similaires?nom=x").status_code for _ in range(2)] == [200, 200]
    assert c.get("/api/similaires?nom=x").status_code == 200   # 5th hit from this IP
    assert c.get("/api/similaires?nom=x").status_code == 429   # IP ceiling reached


def test_hit_many_rules_per_key():
    clock = Clock()
    rl = RateLimiter(clock)
    assert rl.hit_many([("ip", [(10, 60)]), ("v", [(1, 60)])])
    assert not rl.hit_many([("ip", [(10, 60)]), ("v", [(1, 60)])])
    assert rl.hit_many([("ip", [(10, 60)]), ("v2", [(1, 60)])])
