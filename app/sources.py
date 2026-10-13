"""More dish sources next to Wikidata: recipe sites and Wikipédia categories.

Network calls happen here and in importer.py only (server-side), with the importer's descriptive
User-Agent, serially and paced. A scraped dish that is new to the catalogue enters with status
'review': it is not played until the reviewers (scripts/review_dishes.py, one `claude -p` per dish)
have kept it — see reviews.py. A scraped recipe whose title is already in the catalogue is not
duplicated: its URL becomes the recipe link of the existing dish.
"""
from __future__ import annotations

import html
import json
import logging
import re
from urllib.parse import quote, unquote, urljoin

import httpx

from . import db, importer, services
from .text import title_norm

log = logging.getLogger("plats.sources")

CUISINE_FACILE_LIST = "https://cuisine-facile.com/liste-tradition.php"
CUISINE_FACILE_SKIP_SECTIONS = {"boissons"}
CUISINE_FACILE_PAUSE = 1.0  # a one-person site: one recipe page per second at most

FRWIKI_API = "https://fr.wikipedia.org/w/api.php"
FRWIKI_ARTICLE = "https://fr.wikipedia.org/wiki/"
# Members only, no recursion: sub-categories such as « Fromage français » are what we want less of.
# A category that does not exist simply yields nothing (counts are in the summary).
WIKIPEDIA_CATEGORIES = (
    "Catégorie:Cuisine française",
    "Catégorie:Spécialité régionale française",
    "Catégorie:Cuisine alsacienne",
    "Catégorie:Cuisine bretonne",
    "Catégorie:Cuisine provençale",
    "Catégorie:Cuisine lyonnaise",
    "Catégorie:Cuisine corse",
    "Catégorie:Cuisine basque",
    "Catégorie:Cuisine normande",
    "Catégorie:Cuisine auvergnate",
    "Catégorie:Cuisine savoyarde",
    "Catégorie:Cuisine occitane",
    "Catégorie:Cuisine lorraine",
    "Catégorie:Cuisine bourguignonne",
    "Catégorie:Cuisine niçoise",
    "Catégorie:Pâtisserie française",
)

ELLE_GALLERY = "https://www.elle.fr/Elle-a-Table/Idees-de-menus/Gastronomie-francaise"
ELLE_RECIPE_RE = re.compile(r"/Elle-a-Table/Recettes-de-cuisine/[^\"'#?\s]*?-(\d{5,})(?=[\"'#?/\s]|$)")
ELLE_ANCHOR_ID_RE = re.compile(r"^[A-Za-z0-9%][A-Za-z0-9%_-]*-(\d{5,})$")

SOURCES = ("cuisine-facile", "wikipedia", "elle")
MAX_TITLE = 120
MAX_SOURCE_TEXT = 1500


# ------------------------------------------------------------------ small HTML helpers

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _json_ld(page: str) -> list[dict]:
    """Every JSON-LD object of a page, @graph flattened; broken blocks are ignored."""
    out: list[dict] = []
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            data = json.loads(block, strict=False)
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if isinstance(obj, dict):
                out.append(obj)
                stack.extend(o for o in obj.get("@graph", []) if isinstance(o, dict))
    return out


def _is_type(obj: dict, name: str) -> bool:
    t = obj.get("@type")
    return t == name or (isinstance(t, list) and name in t)


def _recipe_details(page: str) -> dict:
    for obj in _json_ld(page):
        if _is_type(obj, "Recipe"):
            ingredients = [i for i in obj.get("recipeIngredient") or [] if isinstance(i, str)]
            return {"description": _text(str(obj.get("description") or "")),
                    "ingredients": [_text(i) for i in ingredients][:30]}
    return {}


def _source_text(description: str, ingredients: list[str] | None = None) -> str:
    text = description.strip()
    if ingredients:
        text += ("\n" if text else "") + "Ingrédients : " + " ; ".join(ingredients)
    return text[:MAX_SOURCE_TEXT]


def _clean_title(title: str) -> str:
    title = _text(title)
    return (title[0].upper() + title[1:])[:MAX_TITLE] if title else ""


# ------------------------------------------------------------------ cuisine-facile.com

_CF_TOKEN = re.compile(
    r"<h4[^>]*\bid=\"(?P<section>[^\"]+)\""
    r"|<h5[^>]*>\s*<a[^>]*href=\"(?P<href>/[^\"]+?\.php)\"[^>]*>(?P<title>.*?)</a>\s*</h5>"
    r"(?P<after>[\s\S]{0,4000}?)(?=<div|<h4|<h5|$)",
    re.I,
)


def parse_cuisine_facile_list(page: str, base: str = CUISINE_FACILE_LIST) -> list[dict]:
    """The list page groups recipes under <h4 id="section"> headings, one <h5><a> per recipe
    followed by its blurb (the page's JSON-LD only lists the first 20)."""
    items, seen, section = [], set(), ""
    for m in _CF_TOKEN.finditer(page):
        if m.group("section"):
            section = m.group("section").strip().lower()
            continue
        href = m.group("href")
        url = urljoin(base, href)
        title = _clean_title(m.group("title"))
        if not title or url in seen or section in CUISINE_FACILE_SKIP_SECTIONS:
            continue
        if "/recette-" not in href:
            continue
        seen.add(url)
        items.append({"key": "cuisine-facile:" + href, "source": "cuisine-facile", "title": title,
                      "recipe_url": url, "wiki_url": "", "section": section,
                      "text": _text(m.group("after"))[:600]})
    return items


def fetch_cuisine_facile(client: httpx.Client, list_url: str = CUISINE_FACILE_LIST) -> list[dict]:
    resp = client.get(list_url, headers={"Accept": "text/html"})
    resp.raise_for_status()
    return parse_cuisine_facile_list(resp.text, list_url)


def describe_cuisine_facile(client: httpx.Client, item: dict) -> None:
    """Ingredients from the recipe page's schema.org Recipe (tells a cheese plate from a dish)."""
    importer._sleep(CUISINE_FACILE_PAUSE)
    try:
        resp = client.get(item["recipe_url"], headers={"Accept": "text/html"})
    except httpx.HTTPError:
        return
    if resp.status_code != 200:
        return
    details = _recipe_details(resp.text)
    if details.get("description") and len(details["description"]) > len(item.get("text", "")):
        item["text"] = details["description"]
    item["ingredients"] = details.get("ingredients", [])


# ------------------------------------------------------------------ fr.wikipedia.org categories

def wiki_url(title: str) -> str:
    return FRWIKI_ARTICLE + quote(title.replace(" ", "_"), safe="()',-_.!~*")


def fetch_wikipedia(client: httpx.Client, categories=WIKIPEDIA_CATEGORIES, counts: dict | None = None) -> list[dict]:
    items, seen = [], set()
    for category in categories:
        params = {"action": "query", "list": "categorymembers", "cmtitle": category, "cmtype": "page",
                  "cmnamespace": "0", "cmlimit": "500", "format": "json"}
        n = 0
        while True:
            importer._sleep(importer.ENRICH_PAUSE)
            resp = client.get(FRWIKI_API, params=params)
            resp.raise_for_status()
            data = resp.json()
            for member in data.get("query", {}).get("categorymembers", []):
                title = str(member.get("title", "")).strip()
                pageid = member.get("pageid")
                if not title or not pageid or pageid in seen or title.startswith(("Liste ", "Cuisine ")):
                    continue
                seen.add(pageid)
                n += 1
                items.append({"key": f"wikipedia:{pageid}", "source": "wikipedia",
                              "title": importer.clean_title(title), "recipe_url": "",
                              "wiki_url": wiki_url(title), "section": category.split(":", 1)[-1], "text": ""})
            cont = data.get("continue", {}).get("cmcontinue")
            if not cont:
                break
            params["cmcontinue"] = cont
        if counts is not None:
            counts[category] = n
    return items


def describe_wikipedia(client: httpx.Client, item: dict) -> None:
    extract, _ = importer._wiki_summary(client, item["wiki_url"])
    item["text"] = extract


# ------------------------------------------------------------------ elle.fr (saved page only)

def _title_from_slug(slug: str) -> str:
    words = unquote(slug).replace("_", " ").replace("-", " ").split()
    return _clean_title(" ".join(words))


def parse_elle_page(page: str, page_url: str = ELLE_GALLERY) -> list[dict]:
    """elle.fr answers scripts with a captcha (AWS WAF), so it is never fetched: the owner saves the
    gallery page from a browser and uploads it. Best effort on three shapes, deduplicated by the
    numeric id: schema.org ItemList/Recipe, links to /Elle-a-Table/Recettes-de-cuisine/<Nom>-<id>,
    and gallery anchors id="<Nom>-<id>" (e.g. #Flan-parisien-2976349)."""
    found: dict[str, dict] = {}

    def add(num: str, title: str, url: str, text: str = "") -> None:
        title = _clean_title(title)
        if not title or len(title) < 2:
            return
        item = found.get(num)
        if item is None:
            found[num] = {"key": f"elle:{num}", "source": "elle", "title": title, "recipe_url": url,
                          "wiki_url": "", "section": "", "text": text[:600]}
            return
        if "/Recettes-de-cuisine/" in url and "/Recettes-de-cuisine/" not in item["recipe_url"]:
            item["recipe_url"] = url  # a real recipe page beats a gallery anchor
        if text and not item["text"]:
            item["text"] = text[:600]

    for obj in _json_ld(page):
        entries = obj.get("itemListElement") if _is_type(obj, "ItemList") else [obj] if _is_type(obj, "Recipe") else []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("item") if isinstance(entry.get("item"), dict) else entry
            url = str(inner.get("url") or entry.get("url") or "")
            m = ELLE_RECIPE_RE.search(url)
            if m and inner.get("name"):
                add(m.group(1), str(inner["name"]), urljoin(page_url, url.split("#")[0].split("?")[0]), _text(str(inner.get("description") or "")))

    for m in re.finditer(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", page, re.S | re.I):
        href, label = html.unescape(m.group(1)), _text(m.group(2))
        rm = ELLE_RECIPE_RE.search(href)
        if not rm:
            continue
        slug = href.split("/Recettes-de-cuisine/", 1)[1].split("?")[0].split("#")[0]
        title = label if 2 <= len(label) <= MAX_TITLE and not label.lower().startswith(("voir", "lire", "découvr")) \
            else _title_from_slug(slug.rsplit("-", 1)[0])
        add(rm.group(1), title, urljoin(page_url, href.split("#")[0].split("?")[0]))

    base = page_url.split("#")[0]
    for m in re.finditer(r"<(h\d|div|section|article|li|figure)\b[^>]*\bid=\"([^\"]+)\"[^>]*>", page, re.I):
        anchor = m.group(2)
        am = ELLE_ANCHOR_ID_RE.match(anchor)
        if not am:
            continue
        heading = ""
        if m.group(1).lower().startswith("h"):
            end = page.find(f"</{m.group(1)}", m.end())
            heading = _text(page[m.end():end]) if 0 < end - m.end() < 400 else ""
        add(am.group(1), heading or _title_from_slug(anchor.rsplit("-", 1)[0]), f"{base}#{anchor}")
    return list(found.values())


# ------------------------------------------------------------------ import into the catalogue

def import_items(conn, items: list[dict], *, describe=None, progress=None) -> dict:
    """Idempotent by source key. Never changes a dish's status or text once it exists: a re-run only
    fills a missing recipe link. New dishes enter with status 'review'."""
    summary = {"fetched": len(items), "created": 0, "linked": 0, "known": 0}
    by_norm: dict[str, dict] = {}
    known_wiki: set[str] = set()
    for r in conn.execute("SELECT id, title_norm, wiki_url, recipe_url, status FROM dishes"
                          " WHERE status NOT IN ('merged', 'rejected', 'flagged') ORDER BY status = 'active' DESC, id"):
        by_norm.setdefault(r["title_norm"], dict(r))
        if r["wiki_url"]:
            known_wiki.add(unquote(r["wiki_url"]))
    for n, it in enumerate(items, start=1):
        if progress and n % 25 == 0:
            progress(n, len(items))
        if conn.execute("SELECT 1 FROM dishes WHERE source_key = ?", (it["key"],)).fetchone():
            summary["known"] += 1
            continue
        if it.get("wiki_url") and unquote(it["wiki_url"]) in known_wiki:
            summary["known"] += 1
            continue
        norm = title_norm(it["title"])
        match = by_norm.get(norm) if norm else None
        if match is not None:
            if it.get("recipe_url") and not match["recipe_url"]:
                with db.Tx(conn):
                    conn.execute("UPDATE dishes SET recipe_url = ? WHERE id = ? AND recipe_url = ''",
                                 (it["recipe_url"], match["id"]))
                match["recipe_url"] = it["recipe_url"]
                summary["linked"] += 1
            else:
                summary["known"] += 1
            continue
        if describe is not None:
            describe(it)
        with db.Tx(conn):
            dish_id = services.create_dish(
                conn, title=it["title"], description="", source=it["source"], status="review",
                source_key=it["key"], recipe_url=it.get("recipe_url", ""), wiki_url=it.get("wiki_url", ""),
                source_text=_source_text(it.get("text", ""), it.get("ingredients")))
        by_norm[norm] = {"id": dish_id, "title_norm": norm, "recipe_url": it.get("recipe_url", ""),
                         "wiki_url": it.get("wiki_url", ""), "status": "review"}
        if it.get("wiki_url"):
            known_wiki.add(unquote(it["wiki_url"]))
        summary["created"] += 1
    return summary


def run_sources(conn, *, client: httpx.Client, sources=("cuisine-facile", "wikipedia"),
                elle_html: str | None = None, progress=None) -> dict:
    out: dict = {}
    for name in sources:
        if name == "cuisine-facile":
            items = fetch_cuisine_facile(client)
            out[name] = import_items(conn, items, describe=lambda it: describe_cuisine_facile(client, it),
                                     progress=progress)
        elif name == "wikipedia":
            counts: dict = {}
            items = fetch_wikipedia(client, counts=counts)
            out[name] = import_items(conn, items, describe=lambda it: describe_wikipedia(client, it),
                                     progress=progress)
            out[name]["categories"] = counts
        elif name == "elle":
            if not elle_html:
                raise ValueError("Elle : fournir la page enregistrée depuis un navigateur (le site bloque les scripts).")
            out[name] = import_items(conn, parse_elle_page(elle_html), progress=progress)
        else:
            raise ValueError(f"source inconnue : {name}")
    return out

