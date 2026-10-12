"""Wikidata (CC0) importer: idempotent by QID, never resurrects a dish the admin hid.

Network calls happen here only (server-side), with a descriptive User-Agent.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from . import __version__, db, services
from .text import basic_norm, title_norm

log = logging.getLogger("plats.importer")

SPARQL_URL = "https://query.wikidata.org/sparql"
FRWIKI_SUMMARY = "https://fr.wikipedia.org/api/rest_v1/page/summary/"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (f"plats.hosh.it-importer/{__version__} (https://plats.hosh.it; "
              "social experiment classifying French dishes; https://github.com/pazka/plats)")

DISHES_QUERY = """
SELECT ?item ?label ?desc ?img ?article WHERE {
  { ?item wdt:P495 wd:Q142 } UNION { ?item wdt:P2012 wd:Q6661 } UNION { ?item wdt:P361 wd:Q6661 }
  { ?item wdt:P31/wdt:P279* wd:Q2095 } UNION { ?item wdt:P279* wd:Q2095 }
  ?item rdfs:label ?label FILTER(lang(?label)="fr")
  OPTIONAL { ?item schema:description ?desc FILTER(lang(?desc)="fr") }
  OPTIONAL { ?item wdt:P18 ?img }
  OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://fr.wikipedia.org/> }
}
"""

# Same candidate set, restricted to beverages (Q40050 "drink" covers wines, spirits, ciders…).
DRINKS_QUERY = """
SELECT DISTINCT ?item WHERE {
  { ?item wdt:P495 wd:Q142 } UNION { ?item wdt:P2012 wd:Q6661 } UNION { ?item wdt:P361 wd:Q6661 }
  { ?item wdt:P31/wdt:P279* wd:Q40050 } UNION { ?item wdt:P279* wd:Q40050 }
}
"""

# Fallback when the class hierarchy is incomplete: the description's head noun is a drink.
DRINK_WORDS = {
    "vin", "vins", "boisson", "boissons", "biere", "bieres", "liqueur", "liqueurs", "spiritueux",
    "eau", "cidre", "aperitif", "cocktail", "champagne", "whisky", "cognac", "armagnac",
    "calvados", "pastis", "cepage", "appellation", "vignoble", "domaine", "cuvee", "marc", "rhum",
    "brandy", "digestif", "sirop", "soda", "limonade", "the", "tisane", "infusion", "cafe",
}
LEADING_FILLERS = {"un", "une", "le", "la", "les", "l", "des", "du", "de", "d", "type", "sorte",
                   "variete", "francais", "francaise", "aoc", "aop", "igp"}

QID_RE = re.compile(r"Q\d+$")
MIN_DESCRIPTION = 40
MAX_DESCRIPTION = 600
THUMB_WIDTH = 480
MAX_IMAGE_BYTES = 3 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

_lock = threading.Lock()


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                        timeout=httpx.Timeout(120.0, connect=15.0), transport=transport,
                        follow_redirects=True)


def _qid(uri: str) -> str | None:
    tail = (uri or "").rsplit("/", 1)[-1]
    return tail if QID_RE.match(tail) else None


def _sparql(client: httpx.Client, query: str) -> list[dict]:
    resp = client.get(SPARQL_URL, params={"query": query, "format": "json"},
                      headers={"Accept": "application/sparql-results+json"})
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def looks_like_drink(description: str) -> bool:
    words = [w for w in basic_norm(description).split() if w not in LEADING_FILLERS]
    return bool(words) and words[0] in DRINK_WORDS


def group_items(bindings: list[dict]) -> dict[str, dict]:
    items: dict[str, dict] = {}
    for b in bindings:
        qid = _qid(b.get("item", {}).get("value", ""))
        label = b.get("label", {}).get("value", "").strip()
        if not qid or not label:
            continue
        it = items.setdefault(qid, {"qid": qid, "label": label, "desc": "", "img": "", "article": ""})
        for key in ("desc", "img", "article"):
            if not it[key] and b.get(key, {}).get("value"):
                it[key] = b[key]["value"].strip()
    return items


def _first_sentences(text: str, limit: int = MAX_DESCRIPTION) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"


def _wiki_summary(client: httpx.Client, article_url: str) -> str:
    title = unquote(article_url.rsplit("/wiki/", 1)[-1]) if "/wiki/" in article_url else ""
    if not title:
        return ""
    resp = client.get(FRWIKI_SUMMARY + quote(title.replace(" ", "_"), safe=""))
    if resp.status_code != 200:
        return ""
    return _first_sentences(resp.json().get("extract", ""))


def _strip_html(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _fetch_image(client: httpx.Client, images_dir: Path, qid: str, commons_url: str) -> dict | None:
    filename = unquote(commons_url.rsplit("/", 1)[-1])
    if not filename:
        return None
    resp = client.get(COMMONS_API, params={
        "action": "query", "format": "json", "prop": "imageinfo", "titles": f"File:{filename}",
        "iiprop": "url|extmetadata|mime", "iiurlwidth": str(THUMB_WIDTH),
    })
    if resp.status_code != 200:
        return None
    pages = resp.json().get("query", {}).get("pages", {})
    info = next(iter(pages.values()), {}).get("imageinfo", [{}])[0] if pages else {}
    meta = info.get("extmetadata", {})
    licence = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
    thumb = info.get("thumburl", "")
    if not licence or not thumb:
        return None  # no licence info → no image
    img = client.get(thumb)
    ctype = img.headers.get("content-type", "").split(";")[0].strip()
    if img.status_code != 200 or ctype not in IMAGE_TYPES or len(img.content) > MAX_IMAGE_BYTES:
        return None
    images_dir.mkdir(parents=True, exist_ok=True)
    name = f"{qid}.{IMAGE_TYPES[ctype]}"
    tmp = images_dir / (name + ".tmp")
    tmp.write_bytes(img.content)
    tmp.replace(images_dir / name)
    return {
        "image_path": name,
        "image_author": _strip_html(meta.get("Artist", {}).get("value", ""))[:200] or "Auteur inconnu",
        "image_license": licence[:100],
        "image_license_url": meta.get("LicenseUrl", {}).get("value", "")[:300],
        "image_source_url": info.get("descriptionurl", "")[:300],
    }


def run_import(conn, *, client: httpx.Client, images_dir: Path, enrich: bool = True,
               images: bool = False, progress=None) -> dict:
    summary = {"fetched": 0, "created": 0, "updated": 0, "unchanged": 0, "drinks_skipped": 0,
               "enriched": 0, "images": 0}
    items = group_items(_sparql(client, DISHES_QUERY))
    drinks = {q for q in (_qid(b.get("item", {}).get("value", "")) for b in _sparql(client, DRINKS_QUERY)) if q}
    summary["fetched"] = len(items)
    now = int(time.time())
    for n, it in enumerate(sorted(items.values(), key=lambda i: i["qid"]), start=1):
        if progress and n % 50 == 0:
            progress(n, len(items))
        if it["qid"] in drinks or looks_like_drink(it["desc"]):
            summary["drinks_skipped"] += 1
            continue
        existing = conn.execute("SELECT * FROM dishes WHERE qid = ?", (it["qid"],)).fetchone()
        desc = it["desc"][:MAX_DESCRIPTION]
        if desc:
            desc = desc[0].upper() + desc[1:]
        if enrich and it["article"] and len(desc) < MIN_DESCRIPTION and (
                existing is None or not existing["admin_edited"]):
            try:
                extract = _wiki_summary(client, it["article"])
            except httpx.HTTPError:
                extract = ""
            if len(extract) > len(desc):
                desc = extract
                summary["enriched"] += 1
        image = None
        if images and it["img"] and (existing is None or not existing["image_path"]):
            try:
                image = _fetch_image(client, images_dir, it["qid"], it["img"])
            except (httpx.HTTPError, ValueError, OSError):
                image = None
            if image:
                summary["images"] += 1
        with db.Tx(conn):
            if existing is None:
                dish_id = services.create_dish(conn, title=it["label"][:120], description=desc,
                                               source="wikidata", qid=it["qid"], wiki_url=it["article"])
                if image:
                    _set_image(conn, dish_id, image)
                summary["created"] += 1
                continue
            changed = False
            if not existing["admin_edited"] and (
                    existing["title"] != it["label"][:120] or existing["description"] != desc
                    or existing["wiki_url"] != it["article"]):
                # Status is never touched: a dish the admin hid stays hidden.
                conn.execute(
                    "UPDATE dishes SET title = ?, title_norm = ?, description = ?, wiki_url = ?, updated_at = ?"
                    " WHERE id = ?",
                    (it["label"][:120], title_norm(it["label"]), desc, it["article"], now, existing["id"]),
                )
                changed = True
            if image:
                _set_image(conn, existing["id"], image)
                changed = True
            summary["updated" if changed else "unchanged"] += 1
    return summary


def _set_image(conn, dish_id: int, image: dict) -> None:
    conn.execute(
        "UPDATE dishes SET image_path = ?, image_author = ?, image_license = ?, image_license_url = ?,"
        " image_source_url = ? WHERE id = ?",
        (image["image_path"], image["image_author"], image["image_license"], image["image_license_url"],
         image["image_source_url"], dish_id),
    )


def import_status(conn) -> dict:
    try:
        return json.loads(db.get_setting(conn, "import_status", "{}") or "{}")
    except ValueError:
        return {}


def run_logged(db_path: Path, images_dir: Path, *, enrich: bool, images: bool,
               client: httpx.Client | None = None, actor: str = "cli") -> dict:
    """Run an import with a lock, recording progress in settings + the audit log."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Un import est déjà en cours.")
    conn = db.connect(db_path)
    try:
        def status(**kw):
            db.set_setting(conn, "import_status", json.dumps(kw, ensure_ascii=False))

        started = int(time.time())
        status(state="running", started=started, actor=actor)
        try:
            with (client or make_client()) as http:
                summary = run_import(
                    conn, client=http, images_dir=images_dir, enrich=enrich, images=images,
                    progress=lambda n, total: status(state="running", started=started, actor=actor,
                                                     progress=f"{n}/{total}"))
        except Exception as exc:  # noqa: BLE001 - reported to the admin
            log.exception("wikidata import failed")
            status(state="error", started=started, finished=int(time.time()), error=str(exc)[:500])
            db.audit(conn, "import_wikidata_failed", str(exc)[:500])
            raise
        status(state="done", started=started, finished=int(time.time()), summary=summary, actor=actor)
        db.audit(conn, "import_wikidata", json.dumps(summary))
        log.info(json.dumps({"event": "import_done", **summary}))
        return summary
    finally:
        conn.close()
        _lock.release()


def start_background(db_path: Path, images_dir: Path, *, enrich: bool, images: bool) -> bool:
    if _lock.locked():
        return False

    def target():
        try:
            run_logged(db_path, images_dir, enrich=enrich, images=images, actor="admin")
        except Exception:  # noqa: BLE001 - already logged and recorded
            pass

    threading.Thread(target=target, name="wikidata-import", daemon=True).start()
    return True
