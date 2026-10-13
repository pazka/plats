"""Recipe-site / Wikipédia sources: parsing on fixtures shaped like the live pages, import rules.
No live network (httpx.MockTransport)."""
from pathlib import Path

import httpx

from app import importer, services, sources

importer._sleep = lambda s: None

FIX = Path(__file__).parent / "fixtures"


def cf_handler(calls):
    listing = (FIX / "cuisine_facile_list.html").read_text()
    tartiflette = (FIX / "cuisine_facile_tartiflette.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert "plats.hosh.it" in request.headers["user-agent"]
        if request.url.host == "cuisine-facile.com":
            if request.url.path == "/liste-tradition.php":
                return httpx.Response(200, text=listing, headers={"content-type": "text/html"})
            if request.url.path == "/divers/recette-tartiflette.php":
                return httpx.Response(200, text=tartiflette, headers={"content-type": "text/html"})
            return httpx.Response(404, text="")
        if request.url.host == "fr.wikipedia.org" and request.url.path == "/w/api.php":
            cat = request.url.params["cmtitle"]
            if cat == "Catégorie:Cuisine française":
                if "cmcontinue" not in request.url.params:
                    return httpx.Response(200, json={"continue": {"cmcontinue": "page|2"}, "query": {"categorymembers": [
                        {"pageid": 11, "ns": 0, "title": "Blanquette de veau"},
                        {"pageid": 12, "ns": 0, "title": "Cuisine bourgeoise"},
                        {"pageid": 13, "ns": 0, "title": "Quiche lorraine"}]}})
                return httpx.Response(200, json={"query": {"categorymembers": [
                    {"pageid": 14, "ns": 0, "title": "Castor et Pollux (éléphants)"}]}})
            if cat == "Catégorie:Spécialité régionale française":
                return httpx.Response(200, json={"query": {"categorymembers": [
                    {"pageid": 11, "ns": 0, "title": "Blanquette de veau"},
                    {"pageid": 15, "ns": 0, "title": "Pan bagnat"}]}})
            return httpx.Response(200, json={"batchcomplete": "", "query": {"categorymembers": []}})
        if request.url.host == "fr.wikipedia.org" and "/page/summary/" in request.url.path:
            title = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"extract": f"{title} : un article de Wikipédia assez long pour servir."})
        return httpx.Response(500)

    return handler


def client(calls):
    return importer.make_client(httpx.MockTransport(cf_handler(calls)))


def test_parse_cuisine_facile_list_sections_and_skips():
    items = sources.parse_cuisine_facile_list((FIX / "cuisine_facile_list.html").read_text())
    titles = [i["title"] for i in items]
    # every <h5> recipe, not only the 20 of the JSON-LD; drinks section and non-recipes skipped
    assert titles == ["Céleris rémoulade", "Soupe à l'oignon gratinée", "Boeuf bourguignon", "Tartiflette",
                      "Mayonnaise"]
    tart = items[3]
    assert tart["key"] == "cuisine-facile:/divers/recette-tartiflette.php"
    assert tart["recipe_url"] == "https://cuisine-facile.com/divers/recette-tartiflette.php"
    assert tart["section"] == "viandes" and tart["text"] == "Un plat savoyard & généreux, au reblochon."


def test_import_cuisine_facile_creates_review_links_existing_and_is_idempotent(conn):
    existing = services.create_dish(conn, title="Bœuf bourguignon", description="Un ragoût de bœuf au vin rouge.",
                                    source="wikidata", qid="Q1")
    calls = []
    with client(calls) as http:
        s = sources.run_sources(conn, client=http, sources=["cuisine-facile"])["cuisine-facile"]
    assert s == {"fetched": 5, "created": 4, "linked": 1, "known": 0}
    linked = conn.execute("SELECT * FROM dishes WHERE id = ?", (existing,)).fetchone()
    assert linked["recipe_url"].endswith("/viandes/recette-boeuf-bourguignon.php") and linked["status"] == "active"
    tart = conn.execute("SELECT * FROM dishes WHERE source_key LIKE '%tartiflette%'").fetchone()
    # waits for the reviewers, its scraped text is kept private (description stays empty)
    assert tart["status"] == "review" and tart["description"] == "" and tart["source"] == "cuisine-facile"
    assert "gratin savoyard" in tart["source_text"] and "Ingrédients : 1 kg de pommes de terre ; 200 g" in tart["source_text"]
    assert not services.eligible_dishes(conn, "v") or all(
        d != tart["id"] for d, _ in services.eligible_dishes(conn, "v"))
    with client(calls) as http:
        again = sources.run_sources(conn, client=http, sources=["cuisine-facile"])["cuisine-facile"]
    assert again == {"fetched": 5, "created": 0, "linked": 0, "known": 5}


def test_admin_edits_and_status_never_touched_by_a_rerun(conn):
    calls = []
    with client(calls) as http:
        sources.run_sources(conn, client=http, sources=["cuisine-facile"])
    conn.execute("UPDATE dishes SET status = 'hidden', title = 'Mayo' WHERE source_key LIKE '%mayonnaise%'")
    with client(calls) as http:
        sources.run_sources(conn, client=http, sources=["cuisine-facile"])
    row = conn.execute("SELECT status, title FROM dishes WHERE source_key LIKE '%mayonnaise%'").fetchone()
    assert tuple(row) == ("hidden", "Mayo")


def test_import_wikipedia_categories_dedup_and_pagination(conn):
    services.create_dish(conn, title="Quiche lorraine", description="Tarte salée aux lardons et à la crème.",
                         source="wikidata", qid="Q2", wiki_url="https://fr.wikipedia.org/wiki/Quiche_lorraine")
    calls = []
    with client(calls) as http:
        out = sources.run_sources(conn, client=http, sources=["wikipedia"])["wikipedia"]
    # Blanquette (twice in two categories), Castor et Pollux, Pan bagnat; "Cuisine …" pages skipped;
    # Quiche already known through its Wikipédia URL
    assert out["created"] == 3 and out["known"] == 1
    assert out["categories"]["Catégorie:Cuisine française"] == 3
    row = conn.execute("SELECT * FROM dishes WHERE source_key = 'wikipedia:15'").fetchone()
    assert row["title"] == "Pan bagnat" and row["wiki_url"] == "https://fr.wikipedia.org/wiki/Pan_bagnat"
    assert row["status"] == "review" and "un article de Wikipédia" in row["source_text"]
    castor = conn.execute("SELECT title FROM dishes WHERE source_key = 'wikipedia:14'").fetchone()
    assert castor["title"] == "Castor et Pollux"  # disambiguation dropped like the Wikidata titles


def test_parse_elle_saved_page():
    items = {i["key"]: i for i in sources.parse_elle_page((FIX / "elle_saved_page.html").read_text())}
    assert set(items) == {"elle:1234567", "elle:7654321", "elle:2976349", "elle:3000001", "elle:1111111"}
    assert items["elle:1234567"]["title"] == "Bœuf bourguignon"
    assert items["elle:7654321"]["recipe_url"] == "https://www.elle.fr/Elle-a-Table/Recettes-de-cuisine/Pot-au-feu-7654321"
    # the gallery anchor is upgraded to the real recipe link found later on the page
    assert items["elle:2976349"]["title"] == "Flan parisien"
    assert items["elle:2976349"]["recipe_url"].endswith("/Recettes-de-cuisine/Flan-parisien-2976349")
    # a heading anchor keeps its accented text and links into the gallery
    assert items["elle:3000001"]["title"] == "Pâtes à la carbonara"
    assert items["elle:3000001"]["recipe_url"].endswith("/Gastronomie-francaise#Pates-carbonara-3000001")
    assert items["elle:1111111"]["recipe_url"].endswith("/Croque-madame-1111111")


def test_elle_needs_the_saved_page(conn):
    import pytest
    with client([]) as http, pytest.raises(ValueError, match="navigateur"):
        sources.run_sources(conn, client=http, sources=["elle"])
    with client([]) as http:
        out = sources.run_sources(conn, client=http, sources=["elle"],
                                  elle_html=(FIX / "elle_saved_page.html").read_text())
    assert out["elle"]["created"] == 5


def test_run_sources_logged_records_status_and_audit(app, conn):
    settings = app.state.settings
    summary = importer.run_sources_logged(settings.db_path, sources=["cuisine-facile"], client=client([]))
    assert summary["cuisine-facile"]["created"] == 5
    st = importer.import_status(conn)
    assert st["state"] == "done" and st["kind"] == "sources"
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'import_sources'").fetchone()[0] == 1
