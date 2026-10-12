"""Business logic shared by the public site, the JSON API and the admin panel."""
from __future__ import annotations

import math
import random
import sqlite3
import time
from collections import defaultdict

from . import db, security
from .rewards import reward
from .text import basic_norm, find_banned, find_similar, slugify, title_norm

OWN_SUBMISSION_QUARANTINE = 3600  # a submitter doesn't meet their own dish within the hour
MIN_VOTES_FOR_RANKING = 3


class VoteError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


# ------------------------------------------------------------------ categories

def categories(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM categories"
    if active_only:
        sql += " WHERE active = 1"
    return conn.execute(sql + " ORDER BY position, id").fetchall()


# ------------------------------------------------------------------ question selection

def selection_weight(votes: int) -> float:
    """Dishes with fewer votes are much more likely to be asked: coverage grows evenly."""
    return 1.0 / (1 + votes) ** 2


def eligible_dishes(conn: sqlite3.Connection, visitor_id: str) -> list[tuple[int, int]]:
    now = int(time.time())
    rows = conn.execute(
        """
        SELECT d.id AS id, COALESCE(c.n, 0) AS n
        FROM dishes d
        LEFT JOIN (SELECT dish_id, COUNT(*) AS n FROM votes
                   WHERE category_id IS NOT NULL GROUP BY dish_id) c ON c.dish_id = d.id
        WHERE d.status = 'active'
          AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.visitor_id = ? AND v.dish_id = d.id)
          AND NOT (COALESCE(d.submitter_visitor, '') = ? AND d.created_at > ?)
        """,
        (visitor_id, visitor_id, now - OWN_SUBMISSION_QUARANTINE),
    ).fetchall()
    return [(r["id"], r["n"]) for r in rows]


def pick_dish_id(candidates: list[tuple[int, int]], rng: random.Random | None = None) -> int | None:
    if not candidates:
        return None
    rng = rng or random.SystemRandom()
    ids = [c[0] for c in candidates]
    weights = [selection_weight(c[1]) for c in candidates]
    return rng.choices(ids, weights=weights, k=1)[0]


def next_question(conn, settings, visitor_id: str, rng=None) -> dict | None:
    dish_id = pick_dish_id(eligible_dishes(conn, visitor_id), rng)
    if dish_id is None:
        return None
    dish = conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    return {
        "dish": dish_public(dish),
        "token": security.issue_vote_token(settings.secret_key, dish_id, visitor_id),
    }


def dish_public(dish: sqlite3.Row) -> dict:
    return {
        "id": dish["id"],
        "slug": dish["slug"],
        "title": dish["title"],
        "description": dish["description"],
        "region": dish["region"],
        "image": f"/media/{dish['image_path']}" if dish["image_path"] else "",
        "image_author": dish["image_author"],
        "image_license": dish["image_license"],
        "image_license_url": dish["image_license_url"],
        "image_source_url": dish["image_source_url"],
        "wiki_url": dish["wiki_url"],
        "source": dish["source"],
    }


# ------------------------------------------------------------------ votes

def answer_count(conn, visitor_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM votes WHERE visitor_id = ? AND category_id IS NOT NULL", (visitor_id,)
    ).fetchone()[0]


def record_vote(conn, settings, visitor_id: str, token: str, choice: str, ip_hash: str,
                now_ms: int | None = None) -> dict:
    """choice = category slug, or 'skip'. Returns the post-vote payload (reward + distribution)."""
    min_think = int(db.get_setting(conn, "min_think_ms", "600") or 600)
    try:
        dish_id, sig = security.check_vote_token(settings.secret_key, token, visitor_id, min_think, now_ms)
    except security.TokenError as exc:
        raise VoteError(exc.code) from None

    category_id = None
    if choice != "skip":
        cat = conn.execute("SELECT id FROM categories WHERE slug = ? AND active = 1", (choice,)).fetchone()
        if cat is None:
            raise VoteError("invalid_category")
        category_id = cat["id"]

    with db.Tx(conn):
        dish = conn.execute("SELECT id, status FROM dishes WHERE id = ?", (dish_id,)).fetchone()
        if dish is None or dish["status"] != "active":
            raise VoteError("dish_unavailable", 410)
        if not security.consume_token(conn, sig):
            raise VoteError("token_used", 409)
        try:
            conn.execute(
                "INSERT INTO votes(dish_id, visitor_id, category_id, ip_hash, created_at) VALUES (?,?,?,?,?)",
                (dish_id, visitor_id, category_id, ip_hash, int(time.time())),
            )
        except sqlite3.IntegrityError:
            raise VoteError("already_answered", 409) from None

    dist = distribution(conn, dish_id)
    same_pct = None
    if category_id is not None:
        for row in dist["rows"]:
            if row["id"] == category_id:
                same_pct = row["pct"]
    count = answer_count(conn, visitor_id)
    dish_row = conn.execute("SELECT slug, title FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    return {
        "ok": True,
        "skipped": category_id is None,
        "dish": {"slug": dish_row["slug"], "title": dish_row["title"]},
        "choice": choice,
        "same_pct": same_pct,
        "distribution": dist,
        "reward": None if category_id is None else reward(count),
        "count": count,
    }


def distribution(conn, dish_id: int) -> dict:
    counts = {
        r["category_id"]: r["n"]
        for r in conn.execute(
            "SELECT category_id, COUNT(*) AS n FROM votes WHERE dish_id = ? GROUP BY category_id", (dish_id,)
        )
    }
    skips = counts.pop(None, 0)
    total = sum(counts.values())
    rows = []
    for cat in categories(conn, active_only=False):
        n = counts.get(cat["id"], 0)
        if not cat["active"] and n == 0:
            continue
        rows.append({
            "id": cat["id"], "slug": cat["slug"], "name": cat["name"], "emoji": cat["emoji"],
            "color": cat["color"], "count": n, "pct": round(100 * n / total) if total else 0,
        })
    top_share, ent = consensus(list(counts.values()), n_categories=len(categories(conn)))
    top = max(rows, key=lambda r: r["count"]) if total else None
    return {
        "total": total, "skips": skips, "rows": rows,
        "top_share": top_share, "entropy": ent,
        "top": top["slug"] if top else None,
        "label": consensus_label(total, top_share, ent),
    }


def consensus(counts: list[int], n_categories: int) -> tuple[float, float]:
    """(share of the leading category, Shannon entropy normalised to 0..1)."""
    total = sum(counts)
    if total == 0:
        return 0.0, 0.0
    shares = [c / total for c in counts if c]
    h = -sum(p * math.log(p) for p in shares)
    norm = h / math.log(n_categories) if n_categories > 1 else 0.0
    return round(max(shares), 4), round(min(norm, 1.0), 4)


def consensus_label(total: int, top_share: float, entropy: float) -> str:
    if total < MIN_VOTES_FOR_RANKING:
        return "Pas encore assez de réponses"
    if top_share >= 0.75:
        return "Large consensus"
    if top_share >= 0.5:
        return "Plutôt d'accord"
    if entropy >= 0.6:
        return "Très disputé"
    return "Partagé"


def all_dish_stats(conn) -> dict[int, dict]:
    """Per-dish vote counts per category + consensus metrics (one pass over the votes)."""
    n_cats = len(categories(conn))
    per = defaultdict(dict)
    for r in conn.execute(
        "SELECT dish_id, category_id, COUNT(*) AS n FROM votes WHERE category_id IS NOT NULL"
        " GROUP BY dish_id, category_id"
    ):
        per[r["dish_id"]][r["category_id"]] = r["n"]
    result = {}
    for dish_id, counts in per.items():
        total = sum(counts.values())
        top_share, ent = consensus(list(counts.values()), n_cats)
        top_cat = max(counts, key=counts.get)
        result[dish_id] = {"total": total, "counts": counts, "top_share": top_share,
                           "entropy": ent, "top_category": top_cat}
    return result


# ------------------------------------------------------------------ search / browse

SORTS = {
    "az": "A → Z",
    "votes": "Les plus votés",
    "consensus": "Les plus consensuels",
    "dispute": "Les plus disputés",
    "recent": "Les plus récents",
}


def fts_query(q: str) -> str:
    terms = [t for t in basic_norm(q).split() if t][:8]
    return " AND ".join(f'"{t}"*' for t in terms)


def browse(conn, q: str = "", sort: str = "az", page: int = 1, per_page: int = 30) -> dict:
    params: list = []
    sql = "SELECT d.* FROM dishes d"
    match = fts_query(q) if q else ""
    if match:
        sql += " JOIN dishes_fts f ON f.rowid = d.id AND dishes_fts MATCH ?"
        params.append(match)
    sql += " WHERE d.status = 'active'"
    if q and not match:
        return {"items": [], "total": 0, "page": 1, "pages": 1}
    rows = conn.execute(sql, params).fetchall()
    stats = all_dish_stats(conn)
    blank = {"total": 0, "top_share": 0.0, "entropy": 0.0, "top_category": None}

    def st(r):
        return stats.get(r["id"], blank)

    if sort == "votes":
        rows.sort(key=lambda r: (-st(r)["total"], r["title_norm"]))
    elif sort == "consensus":
        rows = [r for r in rows if st(r)["total"] >= MIN_VOTES_FOR_RANKING]
        rows.sort(key=lambda r: (-st(r)["top_share"], st(r)["entropy"], -st(r)["total"]))
    elif sort == "dispute":
        rows = [r for r in rows if st(r)["total"] >= MIN_VOTES_FOR_RANKING]
        rows.sort(key=lambda r: (-st(r)["entropy"], st(r)["top_share"], -st(r)["total"]))
    elif sort == "recent":
        rows.sort(key=lambda r: (-r["created_at"], -r["id"]))
    else:
        rows.sort(key=lambda r: basic_norm(r["title"]))
    total = len(rows)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(1, page), pages)
    cats = {c["id"]: c for c in categories(conn, active_only=False)}
    items = []
    for r in rows[(page - 1) * per_page: page * per_page]:
        s = st(r)
        top = cats.get(s["top_category"]) if s["top_category"] else None
        items.append({"dish": r, "total": s["total"], "top_share": s["top_share"],
                      "entropy": s["entropy"], "top": top})
    return {"items": items, "total": total, "page": page, "pages": pages}


# ------------------------------------------------------------------ public stats

def public_stats(conn) -> dict:
    total_answers = conn.execute("SELECT COUNT(*) FROM votes WHERE category_id IS NOT NULL").fetchone()[0]
    total_skips = conn.execute("SELECT COUNT(*) FROM votes WHERE category_id IS NULL").fetchone()[0]
    participants = conn.execute("SELECT COUNT(DISTINCT visitor_id) FROM votes").fetchone()[0]
    n_dishes = conn.execute("SELECT COUNT(*) FROM dishes WHERE status = 'active'").fetchone()[0]
    stats = all_dish_stats(conn)
    active_ids = {r[0] for r in conn.execute("SELECT id FROM dishes WHERE status = 'active'")}
    totals = [stats[i]["total"] for i in active_ids if i in stats]
    coverage = []
    for threshold in (1, 5, 20):
        n = sum(1 for t in totals if t >= threshold)
        coverage.append({"threshold": threshold, "count": n,
                         "pct": round(100 * n / n_dishes, 1) if n_dishes else 0.0})
    cats = categories(conn, active_only=False)
    per_cat_votes = {r["category_id"]: r["n"] for r in conn.execute(
        "SELECT category_id, COUNT(*) AS n FROM votes WHERE category_id IS NOT NULL GROUP BY category_id")}
    per_cat_top = defaultdict(int)
    for i in active_ids:
        if i in stats:
            per_cat_top[stats[i]["top_category"]] += 1
    leaderboard = sorted(
        ({"cat": c, "votes": per_cat_votes.get(c["id"], 0), "top": per_cat_top.get(c["id"], 0),
          "pct": round(100 * per_cat_votes.get(c["id"], 0) / total_answers) if total_answers else 0}
         for c in cats if c["active"] or per_cat_votes.get(c["id"])),
        key=lambda x: -x["votes"],
    )
    ranked = [i for i in active_ids if i in stats and stats[i]["total"] >= MIN_VOTES_FOR_RANKING]
    titles = {r["id"]: r for r in conn.execute("SELECT id, slug, title FROM dishes WHERE status='active'")}
    cat_by_id = {c["id"]: c for c in cats}

    def entry(i):
        s = stats[i]
        return {"dish": titles[i], "total": s["total"], "top_share": s["top_share"],
                "entropy": s["entropy"], "top": cat_by_id.get(s["top_category"])}

    consensual = [entry(i) for i in sorted(ranked, key=lambda i: (-stats[i]["top_share"], -stats[i]["total"]))[:10]]
    contested = [entry(i) for i in sorted(ranked, key=lambda i: (-stats[i]["entropy"], -stats[i]["total"]))[:10]]
    per_day = [dict(r) for r in conn.execute(
        "SELECT date(created_at, 'unixepoch') AS day, COUNT(*) AS n FROM votes"
        " WHERE category_id IS NOT NULL AND created_at >= ? GROUP BY day ORDER BY day",
        (int(time.time()) - 60 * 86400,))]
    peak = max((d["n"] for d in per_day), default=0)
    for d in per_day:
        d["pct"] = round(100 * d["n"] / peak) if peak else 0
    return {
        "answers": total_answers, "skips": total_skips, "participants": participants,
        "dishes": n_dishes, "coverage": coverage, "leaderboard": leaderboard,
        "consensual": consensual, "contested": contested, "per_day": per_day,
    }


# ------------------------------------------------------------------ dishes: creation, look-alikes

def unique_slug(conn, title: str, exclude_id: int | None = None) -> str:
    base = slugify(title)
    slug, n = base, 1
    while True:
        row = conn.execute("SELECT id FROM dishes WHERE slug = ?", (slug,)).fetchone()
        if row is None or row["id"] == exclude_id:
            return slug
        n += 1
        slug = f"{base}-{n}"


def similar_dishes(conn, name: str, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT id, slug, title, title_norm, description, status FROM dishes"
        " WHERE status IN ('active', 'pending')"
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    matches = find_similar(name, ((r["id"], r["title_norm"]) for r in rows), limit=limit)
    return [
        {"id": i, "slug": by_id[i]["slug"], "title": by_id[i]["title"],
         "description": by_id[i]["description"], "pending": by_id[i]["status"] == "pending",
         "score": round(score)}
        for i, score in matches
    ]


def banned_list(conn) -> list[tuple[str, str, str]]:
    return [(r["word"], r["word_norm"], r["mode"]) for r in conn.execute(
        "SELECT word, word_norm, mode FROM banned_words")]


def moderation_hits(conn, *texts: str) -> list[str]:
    banned = banned_list(conn)
    hits: list[str] = []
    for t in texts:
        for w in find_banned(t, banned):
            if w not in hits:
                hits.append(w)
    return hits


def create_dish(conn, *, title: str, description: str, region: str = "", source: str,
                status: str = "active", qid: str | None = None, wiki_url: str = "",
                submitter_visitor: str | None = None, submitter_ip_hash: str | None = None,
                flag_reason: str = "") -> int:
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO dishes(slug, title, title_norm, description, region, source, qid, wiki_url, status,"
        " flag_reason, submitter_visitor, submitter_ip_hash, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (unique_slug(conn, title), title, title_norm(title), description, region, source, qid, wiki_url,
         status, flag_reason, submitter_visitor, submitter_ip_hash, now, now),
    )
    return cur.lastrowid


def submit_dish(conn, *, title: str, description: str, region: str, visitor_id: str, ip_hash: str) -> dict:
    hits = moderation_hits(conn, title, description, region)
    if hits:
        status, reason = "flagged", "Mots interdits : " + ", ".join(hits)
    elif db.get_setting(conn, "require_approval", "0") == "1":
        status, reason = "pending", ""
    else:
        status, reason = "active", ""
    with db.Tx(conn):
        dish_id = create_dish(conn, title=title, description=description, region=region, source="user",
                              status=status, submitter_visitor=visitor_id, submitter_ip_hash=ip_hash,
                              flag_reason=reason)
    return {"id": dish_id, "status": status}


# ------------------------------------------------------------------ admin operations

def merge_dishes(conn, source_id: int, target_id: int) -> dict:
    """Move votes from source to target (a visitor who answered both keeps the target vote),
    then mark the source as merged."""
    if source_id == target_id:
        raise ValueError("Impossible de fusionner un plat avec lui-même.")
    with db.Tx(conn):
        src = conn.execute("SELECT id FROM dishes WHERE id = ?", (source_id,)).fetchone()
        dst = conn.execute("SELECT id FROM dishes WHERE id = ?", (target_id,)).fetchone()
        if not src or not dst:
            raise ValueError("Plat introuvable.")
        dropped = conn.execute(
            "DELETE FROM votes WHERE dish_id = ? AND visitor_id IN"
            " (SELECT visitor_id FROM votes WHERE dish_id = ?)", (source_id, target_id)
        ).rowcount
        moved = conn.execute("UPDATE votes SET dish_id = ? WHERE dish_id = ?", (target_id, source_id)).rowcount
        conn.execute(
            "UPDATE dishes SET status = 'merged', merged_into = ?, updated_at = ? WHERE id = ?",
            (target_id, int(time.time()), source_id),
        )
    return {"moved": moved, "dropped": dropped}


def rescan_submissions(conn) -> dict:
    """Re-apply the banned-word filter to all user submissions that are visible or pending."""
    flagged = 0
    with db.Tx(conn):
        for d in conn.execute(
            "SELECT id, title, description, region FROM dishes"
            " WHERE source = 'user' AND status IN ('active', 'pending')"
        ).fetchall():
            hits = moderation_hits(conn, d["title"], d["description"], d["region"])
            if hits:
                flagged += 1
                conn.execute(
                    "UPDATE dishes SET status = 'flagged', flag_reason = ?, updated_at = ? WHERE id = ?",
                    ("Mots interdits : " + ", ".join(hits), int(time.time()), d["id"]),
                )
    return {"flagged": flagged}


def anomalies(conn) -> dict:
    """Hints for the admin: abnormal answer rates per visitor / IP-hash, cookie churn per IP."""
    per_hour = int(db.get_setting(conn, "anomaly_votes_per_hour", "200") or 200)
    visitors_per_ip = int(db.get_setting(conn, "anomaly_visitors_per_ip", "15") or 15)
    since_h = int(time.time()) - 3600
    since_d = int(time.time()) - 86400
    fast_visitors = conn.execute(
        "SELECT visitor_id, COUNT(*) AS n FROM votes WHERE created_at >= ? GROUP BY visitor_id"
        " HAVING n >= ? ORDER BY n DESC LIMIT 20", (since_h, per_hour)).fetchall()
    fast_ips = conn.execute(
        "SELECT ip_hash, COUNT(*) AS n FROM votes WHERE created_at >= ? GROUP BY ip_hash"
        " HAVING n >= ? ORDER BY n DESC LIMIT 20", (since_h, per_hour)).fetchall()
    churn = conn.execute(
        "SELECT ip_hash, COUNT(DISTINCT visitor_id) AS n, COUNT(*) AS votes FROM votes WHERE created_at >= ?"
        " GROUP BY ip_hash HAVING n >= ? ORDER BY n DESC LIMIT 20", (since_d, visitors_per_ip)).fetchall()
    return {"fast_visitors": fast_visitors, "fast_ips": fast_ips, "churn": churn,
            "per_hour": per_hour, "visitors_per_ip": visitors_per_ip}
