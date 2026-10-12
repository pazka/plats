import random
from collections import Counter

from app import services


def test_weight_decreases_with_votes():
    assert services.selection_weight(0) > services.selection_weight(1) > services.selection_weight(10)


def test_pick_prefers_dishes_with_fewer_votes():
    rng = random.Random(1234)
    candidates = [(1, 0), (2, 5), (3, 50)]
    counts = Counter(services.pick_dish_id(candidates, rng) for _ in range(5000))
    assert counts[1] > counts[2] > counts[3]
    assert counts[1] > 0.8 * 5000
    assert counts[3] >= 0  # still possible, just rare


def test_every_dish_remains_reachable():
    rng = random.Random(99)
    candidates = [(1, 0), (2, 3)]
    picks = {services.pick_dish_id(candidates, rng) for _ in range(2000)}
    assert picks == {1, 2}


def test_pick_empty():
    assert services.pick_dish_id([]) is None


def test_eligible_excludes_answered_hidden_and_own_recent_submission(conn, dishes):
    services.create_dish(conn, title="Hidden thing", description="x" * 30, source="manual", status="hidden")
    own = services.create_dish(conn, title="Mon plat", description="x" * 30, source="user",
                               submitter_visitor="me-visitor-123")
    conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, created_at) VALUES (?,?,1,0)",
                 (dishes[0], "me-visitor-123"))
    ids = {i for i, _ in services.eligible_dishes(conn, "me-visitor-123")}
    assert dishes[0] not in ids and own not in ids
    assert set(dishes[1:]) <= ids
    # someone else does see the user submission
    assert own in {i for i, _ in services.eligible_dishes(conn, "someone-else-456")}


def test_eligible_counts_only_real_votes(conn, dishes):
    conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, created_at) VALUES (?,?,NULL,0)",
                 (dishes[1], "v1-aaaaaaaaaa"))
    conn.execute("INSERT INTO votes(dish_id, visitor_id, category_id, created_at) VALUES (?,?,2,0)",
                 (dishes[1], "v2-aaaaaaaaaa"))
    counts = dict(services.eligible_dishes(conn, "v3-aaaaaaaaaa"))
    assert counts[dishes[1]] == 1 and counts[dishes[0]] == 0
