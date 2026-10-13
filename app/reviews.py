"""Catalogue review by small, single-purpose reviewers (scripts/review_dishes.py runs one
`claude -p` per dish wherever the `claude` CLI is installed — not in this container).

    candidates (export_candidates, JSONL)  →  reviewers  →  verdicts (apply_reviews, JSONL)

The reviewer only says what the item *is* (`kind`) and, for plain products, whether it is worth a
question. What happens next is decided here, deterministically:
- a plain cheese is hidden (each cheese being its own "dish" made the game too easy and too
  cheesy); a cheese with an unusual consistency or a cheese preparation stays;
- drinks and off-topic items (brands, breeds, books, people…) are hidden;
- a scraped dish waiting in 'review' becomes active or hidden.
An admin-edited dish only gets the verdict recorded; the admin's choices always win. Re-applying
the same verdict changes nothing, so a dish the admin re-activated stays active. A rules verdict
(the fallback reviewer, review_rules.py) never replaces a Claude verdict.

Reviewer output is untrusted (its input was scraped from the web): beyond the schema, a title or
description that carries a link, markup, control characters or a banned word is dropped — only
the classification is kept.
"""
from __future__ import annotations

import json
import re
import time

from . import db, services
from .review_rules import KINDS, RULES_MODEL, rules_review
from .text import similarity, title_norm

# kind → verdict; None = the reviewer's `keep` decides.
POLICY = {
    "plat": "keep",
    "fromage_simple": "hide",
    "fromage_original": "keep",
    "produit": None,
    "sauce": "keep",
    "boisson": "hide",
    "hors_sujet": "hide",
}
SCRAPED_OWN_WORDS = {"cuisine-facile", "elle"}  # their text is theirs: show the reviewer's words
MIN_DESCRIPTION = 40
MAX_DESCRIPTION = 400
TITLE_MIN_SIMILARITY = 60.0


def dish_key(row) -> str:
    return row["source_key"] or (f"wikidata:{row['qid']}" if row["qid"] else f"dish:{row['id']}")


def export_candidates(conn, *, include_reviewed: bool = False, limit: int = 0):
    """Dishes the reviewers should look at: imported ones (never user submissions), waiting or
    playable, not yet reviewed. Yields JSON-serialisable dicts."""
    sql = ("SELECT * FROM dishes WHERE source != 'user' AND status IN ('review', 'active')"
           + ("" if include_reviewed else f" AND (review_verdict = '' OR review_model = '{RULES_MODEL}')")
           + " ORDER BY status = 'review' DESC, id")
    if limit > 0:
        sql += f" LIMIT {int(limit)}"
    for r in conn.execute(sql):
        yield {
            "id": r["id"],
            "key": dish_key(r),
            "source": r["source"],
            "title": r["title"],
            "text": r["source_text"] or r["description"],
            "url": r["recipe_url"] or r["wiki_url"],
        }


def verdict_for(kind: str, keep: bool) -> str:
    fixed = POLICY[kind]
    return fixed if fixed else ("keep" if keep else "hide")


def _validate(rec: dict) -> str | None:
    if not isinstance(rec.get("id"), int) or not isinstance(rec.get("key"), str):
        return "id/key"
    if rec.get("kind") not in KINDS:
        return "kind"
    if not isinstance(rec.get("keep"), bool):
        return "keep"
    for field in ("title", "description", "reason", "model"):
        if rec.get(field) is not None and not isinstance(rec[field], str):
            return field
    return None


def apply_reviews(conn, records, *, actor: str = "cli") -> dict:
    summary = {"read": 0, "applied": 0, "unchanged": 0, "activated": 0, "hidden": 0,
               "described": 0, "retitled": 0, "text_rejected": 0, "invalid": 0, "unknown": 0, "by_kind": {}}
    now = int(time.time())
    for rec in records:
        summary["read"] += 1
        if not isinstance(rec, dict) or _validate(rec):
            summary["invalid"] += 1
            continue
        row = conn.execute("SELECT * FROM dishes WHERE id = ?", (rec["id"],)).fetchone()
        if row is None or dish_key(row) != rec["key"]:
            summary["unknown"] += 1  # another database, or the dish was deleted/merged since the export
            continue
        kind = rec["kind"]
        verdict = verdict_for(kind, rec["keep"])
        summary["by_kind"][kind] = summary["by_kind"].get(kind, 0) + 1
        model = (rec.get("model") or "")[:60]
        if (row["review_kind"] == kind and row["review_verdict"] == verdict) or (
                model == RULES_MODEL and row["review_model"] not in ("", RULES_MODEL)):
            summary["unchanged"] += 1
            continue
        reason = _clean(rec.get("reason"), 300)
        description = _clean(rec.get("description"), MAX_DESCRIPTION)
        title = _clean(rec.get("title"), 120)
        if not _publishable(conn, title, description):
            summary["text_rejected"] += 1
            title = description = ""
        if not _publishable(conn, reason):
            reason = ""
        sets = {"review_kind": kind, "review_verdict": verdict, "review_reason": reason,
                "review_model": model, "reviewed_at": now}
        if not row["admin_edited"]:
            if row["status"] == "review":
                sets["status"] = "active" if verdict == "keep" else "hidden"
                if (title and title != row["title"]
                        and similarity(title_norm(title), row["title_norm"]) >= TITLE_MIN_SIMILARITY):
                    sets["title"], sets["title_norm"] = title, title_norm(title)
                    summary["retitled"] += 1
            elif row["status"] == "active" and verdict == "hide":
                sets["status"] = "hidden"
            if row["status"] in ("review", "active"):
                new_desc = _description(row, description)
                if new_desc != row["description"]:
                    sets["description"] = new_desc
                    summary["described"] += 1
        if sets.get("status") == "active":
            summary["activated"] += 1
        elif sets.get("status") == "hidden":
            summary["hidden"] += 1
        sets["updated_at"] = now
        with db.Tx(conn):
            conn.execute(f"UPDATE dishes SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                         (*sets.values(), row["id"]))
        summary["applied"] += 1
    db.audit(conn, "reviews_apply", json.dumps({"actor": actor, **summary}, ensure_ascii=False))
    return summary


_CONTROL = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_UNSAFE = re.compile(r"https?:|www\.|\.(com|fr|net|org|io)\b|@|<|>|\{|\}|`", re.I)


def _clean(value, limit: int) -> str:
    return " ".join(_CONTROL.sub(" ", value or "").split())[:limit]


def _publishable(conn, *texts: str) -> bool:
    texts = [t for t in texts if t]
    if any(_UNSAFE.search(t) for t in texts):
        return False
    return not (texts and services.moderation_hits(conn, *texts))


def rules_candidates(conn, *, limit: int = 0) -> list[dict]:
    """The deterministic fallback, run inside the app (no Claude at all): verdicts for the
    obvious candidates only."""
    out = []
    for item in export_candidates(conn, limit=limit):
        rec = rules_review(item)
        if rec:
            out.append(rec)
    return out


def _description(row, reviewed: str) -> str:
    """Scraped recipe sites: the reviewer's own words. Wikipédia/Wikidata: keep the (free) text,
    use the reviewer's only to fill a missing or too short description."""
    current = row["description"]
    if row["source"] in SCRAPED_OWN_WORDS:
        return reviewed or current
    if len(current) >= MIN_DESCRIPTION:
        return current
    if row["status"] == "review" and len(row["source_text"]) >= MIN_DESCRIPTION:
        return row["source_text"].split("\nIngrédients :")[0][:600]
    return reviewed if len(reviewed) > len(current) else current


def parse_jsonl(text: str) -> list:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append(None)  # counted as invalid
    return out
