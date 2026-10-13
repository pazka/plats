"""Excel export. Formula-injection safe: every text value is written as a string cell."""
from __future__ import annotations

import io
from datetime import datetime, timezone

import xlsxwriter

from . import services

WORKBOOK_OPTIONS = {
    "in_memory": True,
    "strings_to_formulas": False,
    "strings_to_urls": False,
    "strings_to_numbers": False,
}


def _iso(ts) -> str:
    if ts is None or ts == "":
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _write_sheet(wb, name: str, headers: list[str], rows) -> None:
    ws = wb.add_worksheet(name)
    bold = wb.add_format({"bold": True})
    for col, h in enumerate(headers):
        ws.write_string(0, col, h, bold)
    for r, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            if value is None:
                continue
            if isinstance(value, bool):
                ws.write_number(r, col, int(value))
            elif isinstance(value, (int, float)):
                ws.write_number(r, col, value)
            else:
                ws.write_string(r, col, str(value))
    ws.freeze_panes(1, 0)


def build_export(conn) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, WORKBOOK_OPTIONS)
    cats = {c["id"]: c for c in services.categories(conn, active_only=False)}

    dish_cols = ["id", "slug", "title", "description", "region", "source", "qid", "source_key", "wiki_url",
                 "recipe_url", "status", "flag_reason", "merged_into", "image_path", "image_author",
                 "image_license", "review_kind", "review_verdict", "review_reason", "created_at", "updated_at"]
    dishes = conn.execute("SELECT * FROM dishes ORDER BY id").fetchall()

    def dish_row(d):
        return [d["id"], d["slug"], d["title"], d["description"], d["region"], d["source"], d["qid"],
                d["source_key"], d["wiki_url"], d["recipe_url"], d["status"], d["flag_reason"], d["merged_into"],
                d["image_path"], d["image_author"], d["image_license"], d["review_kind"], d["review_verdict"],
                d["review_reason"], _iso(d["created_at"]), _iso(d["updated_at"])]

    _write_sheet(wb, "dishes", dish_cols, (dish_row(d) for d in dishes))

    _write_sheet(
        wb, "votes",
        ["id", "dish_id", "dish_slug", "category", "visitor_id", "ip_hash", "created_at"],
        ([v["id"], v["dish_id"], v["slug"],
          cats[v["category_id"]]["slug"] if v["category_id"] in cats else "(je ne connais pas)",
          v["visitor_id"], v["ip_hash"], _iso(v["created_at"])]
         for v in conn.execute(
             "SELECT v.*, d.slug FROM votes v JOIN dishes d ON d.id = v.dish_id ORDER BY v.id")),
    )

    _write_sheet(
        wb, "categories", ["id", "slug", "name", "color", "emoji", "position", "active"],
        ([c["id"], c["slug"], c["name"], c["color"], c["emoji"], c["position"], c["active"]]
         for c in cats.values()),
    )

    stats = services.all_dish_stats(conn)
    cat_list = list(cats.values())
    skips = {r["dish_id"]: r["n"] for r in conn.execute(
        "SELECT dish_id, COUNT(*) AS n FROM votes WHERE category_id IS NULL GROUP BY dish_id")}
    _write_sheet(
        wb, "dish_stats",
        ["dish_id", "slug", "title", "total_votes", "skips", "top_category", "top_share", "entropy"]
        + [f"n_{c['slug']}" for c in cat_list],
        ([d["id"], d["slug"], d["title"], stats.get(d["id"], {}).get("total", 0), skips.get(d["id"], 0),
          cats[stats[d["id"]]["top_category"]]["slug"] if d["id"] in stats else "",
          stats.get(d["id"], {}).get("top_share", 0), stats.get(d["id"], {}).get("entropy", 0)]
         + [stats.get(d["id"], {}).get("counts", {}).get(c["id"], 0) for c in cat_list]
         for d in dishes if d["status"] == "active"),
    )

    _write_sheet(wb, "submissions", dish_cols + ["submitter_visitor", "submitter_ip_hash"],
                 (dish_row(d) + [d["submitter_visitor"], d["submitter_ip_hash"]]
                  for d in dishes if d["source"] == "user"))

    _write_sheet(wb, "banned_words", ["id", "word", "normalised", "mode", "created_at"],
                 ([b["id"], b["word"], b["word_norm"], b["mode"], _iso(b["created_at"])]
                  for b in conn.execute("SELECT * FROM banned_words ORDER BY word_norm")))
    wb.close()
    return buf.getvalue()
