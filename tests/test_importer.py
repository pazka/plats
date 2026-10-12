"""Importer tests run against fixtures through httpx.MockTransport: no live network."""
import json
from pathlib import Path

import httpx
import pytest

from app import importer

importer._sleep = lambda s: None  # no real pauses in tests

FIX = Path(__file__).parent / "fixtures"
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def handler_factory(calls, dishes_payload=None):
    dishes = dishes_payload or json.loads((FIX / "wikidata_dishes.json").read_text())
    drinks = json.loads((FIX / "wikidata_drinks.json").read_text())
    quiche = json.loads((FIX / "frwiki_quiche.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert "plats.hosh.it" in request.headers["user-agent"]
        url = request.url
        if url.host == "query.wikidata.org":
            q = url.params["query"]
            return httpx.Response(200, json=drinks if "Q40050" in q else dishes)
        if url.host == "fr.wikipedia.org":
            if url.path.endswith("/Quiche_lorraine"):
                return httpx.Response(200, json=quiche)
            return httpx.Response(404, json={})
        if url.host == "commons.wikimedia.org":
            title = url.params["titles"]
            meta = {"Artist": {"value": "<a href='//x'>Jane Doe</a>"}, "LicenseUrl": {"value": "https://cc/by-sa"}}
            if "Brie" not in title:  # only Brie lacks licence info
                meta["LicenseShortName"] = {"value": "CC BY-SA 4.0"}
            return httpx.Response(200, json={"query": {"pages": {"1": {"imageinfo": [{
                "thumburl": f"https://upload.wikimedia.org/thumb/{title}.png", "descriptionurl": "https://commons/x",
                "extmetadata": meta}]}}}})
        if url.host == "upload.wikimedia.org":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return httpx.Response(500)

    return handler


def run(conn, tmp_path, calls, **kw):
    client = importer.make_client(httpx.MockTransport(handler_factory(calls, kw.pop("payload", None))))
    with client:
        return importer.run_import(conn, client=client, images_dir=tmp_path / "images", **kw)


def dish(conn, qid):
    return conn.execute("SELECT * FROM dishes WHERE qid = ?", (qid,)).fetchone()


def test_first_import(conn, tmp_path):
    calls = []
    s = run(conn, tmp_path, calls, enrich=True, images=False)
    assert s["fetched"] == 6 and s["created"] == 4 and s["drinks_skipped"] == 2 and s["enriched"] == 1
    assert dish(conn, "Q3") is None and dish(conn, "Q4") is None  # wine, liqueur
    q = dish(conn, "Q2")
    assert q["title"] == "Quiche lorraine" and q["description"].startswith("La quiche lorraine est une tarte")
    assert q["source"] == "wikidata" and q["wiki_url"] == "https://fr.wikipedia.org/wiki/Quiche_lorraine"
    assert dish(conn, "Q1")["description"].startswith("Plat traditionnel")
    assert dish(conn, "Q5")["description"] == ""


def test_import_is_idempotent(conn, tmp_path):
    run(conn, tmp_path, [], enrich=True, images=False)
    count = conn.execute("SELECT COUNT(*) FROM dishes").fetchone()[0]
    s = run(conn, tmp_path, [], enrich=True, images=False)
    assert s["created"] == 0 and s["updated"] == 0 and s["unchanged"] == 4
    assert conn.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == count
    slugs = [r[0] for r in conn.execute("SELECT slug FROM dishes")]
    assert len(slugs) == len(set(slugs))


def test_reimport_updates_but_never_resurrects_hidden(conn, tmp_path):
    run(conn, tmp_path, [], enrich=False, images=False)
    conn.execute("UPDATE dishes SET status = 'hidden' WHERE qid = 'Q1'")
    conn.execute("UPDATE dishes SET title = 'Ratatouille niçoise', admin_edited = 1 WHERE qid = 'Q5'")
    payload = json.loads((FIX / "wikidata_dishes.json").read_text())
    for b in payload["results"]["bindings"]:
        if b["item"]["value"].endswith("/Q1"):
            b["label"]["value"] = "cassoulet de Castelnaudary"
        if b["item"]["value"].endswith("/Q5"):
            b["label"]["value"] = "ratatouille (changed upstream)"
    s = run(conn, tmp_path, [], enrich=False, images=False, payload=payload)
    assert s["created"] == 0 and s["updated"] == 1
    q1 = dish(conn, "Q1")
    assert q1["title"] == "Cassoulet de Castelnaudary" and q1["status"] == "hidden"
    assert dish(conn, "Q5")["title"] == "Ratatouille niçoise"  # admin edit preserved


def test_images_downloaded_locally_with_attribution(conn, tmp_path):
    s = run(conn, tmp_path, [], enrich=False, images=True)
    assert s["images"] == 2  # cassoulet + quiche; brie has no licence → skipped
    q1 = dish(conn, "Q1")
    assert q1["image_path"] == "Q1.png" and (tmp_path / "images" / "Q1.png").read_bytes() == PNG
    assert q1["image_author"] == "Jane Doe" and q1["image_license"] == "CC BY-SA 4.0"
    assert dish(conn, "Q6")["image_path"] == ""


def test_drink_heuristic():
    assert importer.looks_like_drink("vin français d'appellation")
    assert importer.looks_like_drink("Une liqueur aux plantes")
    assert not importer.looks_like_drink("plat à base de vin rouge")
    assert not importer.looks_like_drink("")


def test_run_logged_records_status_and_audit(app, conn, tmp_path):
    settings = app.state.settings
    client = importer.make_client(httpx.MockTransport(handler_factory([])))
    importer.run_logged(settings.db_path, settings.images_dir, enrich=False, images=False, client=client)
    st = importer.import_status(conn)
    assert st["state"] == "done" and st["summary"]["created"] == 4
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='import_wikidata'").fetchone()[0] == 1


def test_import_failure_is_reported(app, conn):
    settings = app.state.settings
    client = importer.make_client(httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        importer.run_logged(settings.db_path, settings.images_dir, enrich=False, images=False, client=client)
    assert importer.import_status(conn)["state"] == "error"


def test_titles_are_capitalised_and_disambiguation_stripped():
    assert importer.clean_title("coq au vin") == "Coq au vin"
    assert importer.clean_title("œuf poché") == "Œuf poché"
    assert importer.clean_title("liaison (cuisine)") == "Liaison"
    assert importer.clean_title("Bleu des Causses") == "Bleu des Causses"


def test_non_dishes_are_skipped(conn, tmp_path):
    payload = json.loads((FIX / "wikidata_dishes.json").read_text())
    def add(qid, label, desc):
        payload["results"]["bindings"].append({
            "item": {"value": f"http://www.wikidata.org/entity/{qid}"}, "label": {"value": label},
            "desc": {"value": desc}})
    add("Q90", "Clon", "Marque commerciale française de fromages")
    add("Q91", "Verdale des Pyrénées-Orientales", "Cultivar d'olivier originaire de la France")
    add("Q92", "Montbéliarde", "Race bovine française")
    s = run(conn, tmp_path, [], enrich=False, images=False, payload=payload)
    assert s["non_dishes_skipped"] == 3
    assert dish(conn, "Q90") is None and dish(conn, "Q92") is None
    assert importer.looks_like_non_dish("Marque de fromage") and not importer.looks_like_non_dish("Fromage français")


def test_rerun_without_enrichment_keeps_enriched_description(conn, tmp_path):
    run(conn, tmp_path, [], enrich=True, images=False)
    enriched = dish(conn, "Q2")["description"]
    assert enriched.startswith("La quiche lorraine est une tarte")
    s = run(conn, tmp_path, [], enrich=False, images=False)
    assert s["updated"] == 0
    assert dish(conn, "Q2")["description"] == enriched


def test_enrichment_backs_off_on_429_and_reports_statuses(conn, tmp_path):
    calls = []
    base = handler_factory(calls)
    state = {"n": 0}

    def flaky(request):
        if request.url.host == "fr.wikipedia.org" and state["n"] == 0:
            state["n"] += 1
            return httpx.Response(429, headers={"retry-after": "1"}, json={})
        return base(request)

    client = importer.make_client(httpx.MockTransport(flaky))
    with client:
        s = importer.run_import(conn, client=client, images_dir=tmp_path / "img", enrich=True, images=False)
    assert s["enriched"] == 1 and s["enrich_http"].get("200") == 1
    assert sum(1 for r in calls if r.url.host == "fr.wikipedia.org") >= 1
