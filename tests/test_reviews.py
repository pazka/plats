"""Reviewer verdicts: the cheese policy, idempotency, admin wins, untrusted text, rules fallback,
admin upload/download, and the recipe link on the question card."""
import io
import json

from app import review_rules, reviews, services
from tests.test_admin import csrf, do_login


def dish(conn, **kw):
    kw.setdefault("description", "")
    return services.create_dish(conn, **kw)


def verdict(conn, dish_id, kind, keep=True, **kw):
    row = conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()
    return {"id": dish_id, "key": reviews.dish_key(row), "kind": kind, "keep": keep,
            "title": kw.get("title", ""), "description": kw.get("description", ""),
            "reason": kw.get("reason", "parce que"), "model": kw.get("model", "haiku")}


def row(conn, dish_id):
    return conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()


def test_policy_hides_plain_cheese_keeps_original_cheese_and_cheese_dishes(conn):
    brie = dish(conn, title="Brie de Meaux", description="Fromage à pâte molle et croûte fleurie.",
                source="wikidata", qid="Q1")
    cancoillotte = dish(conn, title="Cancoillotte", description="Fromage fondu et coulant de Franche-Comté.",
                        source="wikidata", qid="Q2")
    fondue = dish(conn, title="Fondue savoyarde", description="Fromages fondus au vin blanc, pain trempé.",
                  source="wikidata", qid="Q3")
    vin = dish(conn, title="Château Truc", description="Vin de Bordeaux, rouge, tannique et puissant.",
               source="wikidata", qid="Q4")
    pomme = dish(conn, title="Pomme", description="Fruit du pommier, très courant dans les vergers.",
                 source="wikidata", qid="Q5")
    s = reviews.apply_reviews(conn, [
        verdict(conn, brie, "fromage_simple", keep=True),   # the reviewer's keep does not matter here
        verdict(conn, cancoillotte, "fromage_original", keep=False),
        verdict(conn, fondue, "plat"),
        verdict(conn, vin, "boisson"),
        verdict(conn, pomme, "produit", keep=False),        # produit: the reviewer decides
    ])
    assert s["applied"] == 5 and s["hidden"] == 3
    status = {i: row(conn, i)["status"] for i in (brie, cancoillotte, fondue, vin, pomme)}
    assert status == {brie: "hidden", cancoillotte: "active", fondue: "active", vin: "hidden", pomme: "hidden"}
    assert row(conn, brie)["review_kind"] == "fromage_simple" and row(conn, brie)["review_verdict"] == "hide"


def test_review_status_dishes_get_activated_retitled_and_described(conn):
    cf = dish(conn, title="tartiflette", source="cuisine-facile", status="review", source_key="cuisine-facile:/t.php",
              recipe_url="https://cuisine-facile.com/t.php", source_text="Le texte du site, à ne pas republier.")
    wp = dish(conn, title="Pan bagnat", source="wikipedia", status="review", source_key="wikipedia:15",
              source_text="Le pan bagnat est une spécialité niçoise : un pain rond garni de crudités et de thon.")
    other = dish(conn, title="Machin", source="wikipedia", status="review", source_key="wikipedia:16")
    s = reviews.apply_reviews(conn, [
        verdict(conn, cf, "plat", title="Tartiflette", description="Pommes de terre, lardons, oignons et reblochon."),
        verdict(conn, wp, "plat", title="Pan bagnat", description="Un mot du relecteur."),
        verdict(conn, other, "hors_sujet", title="Un tout autre nom sans rapport"),
    ])
    assert s["activated"] == 2 and s["hidden"] == 1 and s["retitled"] == 1
    r = row(conn, cf)
    # recipe sites: the reviewer's own words, never the scraped text
    assert (r["status"], r["title"], r["description"]) == ("active", "Tartiflette",
                                                           "Pommes de terre, lardons, oignons et reblochon.")
    # Wikipédia: its free text is kept when long enough
    assert row(conn, wp)["description"].startswith("Le pan bagnat est une spécialité niçoise")
    assert row(conn, other)["title"] == "Machin" and row(conn, other)["status"] == "hidden"  # no renaming


def test_reapplying_is_a_no_op_and_admin_choices_win(conn):
    brie = dish(conn, title="Brie", description="Fromage à pâte molle.", source="wikidata", qid="Q1")
    edited = dish(conn, title="Comté", description="Fromage à pâte pressée cuite.", source="wikidata", qid="Q2")
    conn.execute("UPDATE dishes SET admin_edited = 1 WHERE id = ?", (edited,))
    recs = [verdict(conn, brie, "fromage_simple"), verdict(conn, edited, "fromage_simple")]
    reviews.apply_reviews(conn, recs)
    assert row(conn, brie)["status"] == "hidden"
    assert row(conn, edited)["status"] == "active" and row(conn, edited)["review_kind"] == "fromage_simple"
    conn.execute("UPDATE dishes SET status = 'active' WHERE id = ?", (brie,))  # the admin brings it back
    s = reviews.apply_reviews(conn, recs)
    assert s["unchanged"] == 2 and row(conn, brie)["status"] == "active"


def test_untrusted_reviewer_text_is_never_published(conn):
    ids = [dish(conn, title=f"Plat {n}", source="cuisine-facile", status="review", source_key=f"cuisine-facile:/{n}")
           for n in range(4)]
    bad = ["Délicieux, voir https://spam.example", "Un plat <script>alert(1)</script>",
           "Cuisinée par une grosse pute", "Une écriture \u202eà l'envers"]
    s = reviews.apply_reviews(conn, [verdict(conn, i, "plat", description=d, title=f"Plat {n}")
                                     for n, (i, d) in enumerate(zip(ids, bad))])
    assert s["text_rejected"] == 3
    descriptions = [row(conn, i)["description"] for i in ids]
    assert descriptions[:3] == ["", "", ""] and "\u202e" not in descriptions[3]
    assert all(row(conn, i)["status"] == "active" for i in ids)  # the classification still counts


def test_invalid_and_foreign_records_are_ignored(conn):
    d = dish(conn, title="Quiche", description="Tarte salée.", source="wikidata", qid="Q9")
    s = reviews.apply_reviews(conn, reviews.parse_jsonl("\n".join([
        "not json",
        json.dumps({"id": d, "key": "wikidata:Q9", "kind": "dessert", "keep": True}),
        json.dumps({"id": d, "key": "wikidata:Q9", "kind": "plat", "keep": "yes"}),
        json.dumps({"id": d, "key": "wikidata:Q999", "kind": "plat", "keep": True}),  # another database
        json.dumps({"id": 999, "key": "dish:999", "kind": "plat", "keep": True}),
    ])))
    assert s == {**s, "read": 5, "invalid": 3, "unknown": 2, "applied": 0}


def test_rules_never_override_claude_and_are_offered_to_claude_again(conn):
    brie = dish(conn, title="Brie", description="Fromage à pâte molle et croûte fleurie.", source="wikidata", qid="Q1")
    reblochon = dish(conn, title="Reblochon", description="Fromage à pâte pressée non cuite.",
                     source="wikidata", qid="Q2")
    reviews.apply_reviews(conn, [verdict(conn, brie, "fromage_original", model="haiku")])
    recs = reviews.rules_candidates(conn)
    assert [(r["id"], r["kind"]) for r in recs] == [(reblochon, "fromage_simple")]  # brie: already reviewed
    reviews.apply_reviews(conn, recs + [{**verdict(conn, brie, "fromage_simple"), "model": "rules"}])
    assert row(conn, brie)["review_kind"] == "fromage_original" and row(conn, brie)["status"] == "active"
    assert row(conn, reblochon)["review_model"] == "rules" and row(conn, reblochon)["status"] == "hidden"
    # rules verdicts go back to Claude on the next export (hidden ones are the admin's/review's call)
    exported = {c["id"] for c in reviews.export_candidates(conn)}
    assert brie not in exported


def test_rules_reviewer_is_conservative():
    def r(text, source="wikidata"):
        rec = review_rules.rules_review({"id": 1, "key": "k", "source": source, "title": "x", "text": text})
        return rec and rec["kind"]
    assert r("fromage français à pâte molle") == "fromage_simple"
    assert r("Fromage à pâte fondue, coulant, de Franche-Comté") == "fromage_original"
    assert r("fromage frais battu") == "fromage_original"
    assert r("vin rouge de Bourgogne") == "boisson"
    assert r("marque de biscuits") == "hors_sujet"
    assert r("Un plat\nIngrédients : œufs ; crème", source="cuisine-facile") == "plat"
    assert r("plat traditionnel à base de fromage fondu") is None  # not obvious: left to Claude
    assert r("") is None


def test_export_candidates(conn):
    a = dish(conn, title="Quiche", description="Tarte salée.", source="wikidata", qid="Q1",
             wiki_url="https://fr.wikipedia.org/wiki/Quiche_lorraine")
    b = dish(conn, title="Tartiflette", source="cuisine-facile", status="review", source_key="cuisine-facile:/t",
             recipe_url="https://cuisine-facile.com/t", source_text="texte du site")
    dish(conn, title="Proposé", description="Une proposition d'un visiteur.", source="user")
    dish(conn, title="Caché", description="Masqué.", source="wikidata", qid="Q3", status="hidden")
    got = list(reviews.export_candidates(conn))
    assert [c["id"] for c in got] == [b, a]  # waiting ones first; never user submissions or hidden
    assert got[0] == {"id": b, "key": "cuisine-facile:/t", "source": "cuisine-facile", "title": "Tartiflette",
                      "text": "texte du site", "url": "https://cuisine-facile.com/t"}
    assert got[1]["key"] == "wikidata:Q1" and got[1]["url"].endswith("Quiche_lorraine")


def login(client):
    assert do_login(client).status_code == 303
    return csrf(client, "/admin/plats")


def test_admin_download_candidates_and_upload_verdicts(client, conn):
    brie = dish(conn, title="Brie", description="Fromage à pâte molle.", source="wikidata", qid="Q1")
    token = login(client)
    r = client.get("/admin/relecture/candidats.jsonl")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    lines = [json.loads(line) for line in r.text.splitlines()]
    assert lines[0]["id"] == brie
    body = json.dumps({**lines[0], "kind": "fromage_simple", "keep": False, "title": "Brie",
                       "description": "", "reason": "fromage", "model": "haiku"}) + "\n"
    r = client.post("/admin/relecture", data={"csrf": token},
                    files={"reviews": ("avis.jsonl", io.BytesIO(body.encode()), "application/x-ndjson")},
                    follow_redirects=False)
    assert r.status_code == 303 and "masqu" in r.headers["location"]
    assert row(conn, brie)["status"] == "hidden"
    # without the CSRF token: refused
    r = client.post("/admin/relecture", files={"reviews": ("a.jsonl", io.BytesIO(body.encode()), "text/plain")})
    assert r.status_code == 403


def test_admin_rules_fallback_button(client, conn):
    brie = dish(conn, title="Brie", description="Fromage à pâte molle.", source="wikidata", qid="Q1")
    r = client.post("/admin/relecture/regles", data={"csrf": login(client)}, follow_redirects=False)
    assert r.status_code == 303 and row(conn, brie)["status"] == "hidden"
    page = client.get("/admin/plats?status=hidden&avis=fromage_simple").text
    assert "Brie" in page and "fromage_simple" in page


def test_question_card_links_to_the_recipe_in_a_new_tab(client, conn, no_think):
    dish(conn, title="Tartiflette", description="Pommes de terre et reblochon.", source="cuisine-facile",
         source_key="cuisine-facile:/t", recipe_url="https://cuisine-facile.com/divers/recette-tartiflette.php")
    html = client.get("/").text
    assert 'href="https://cuisine-facile.com/divers/recette-tartiflette.php"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html and "Voir la recette (cuisine-facile.com)" in html
    q = client.get("/api/question").json()["next"]["dish"]
    assert q["link_url"].endswith("recette-tartiflette.php") and q["link_label"].startswith("Voir la recette")


def test_question_card_falls_back_to_wikipedia_then_nothing(conn):
    a = dish(conn, title="Quiche", description="x", source="wikidata", qid="Q1",
             wiki_url="https://fr.wikipedia.org/wiki/Quiche_lorraine")
    b = dish(conn, title="Truc", description="x", source="manual")
    assert services.dish_public(row(conn, a))["link_label"] == "En savoir plus (Wikipédia)"
    assert services.dish_public(row(conn, b))["link_url"] == ""


def test_review_status_dishes_are_never_played(client, conn, no_think):
    dish(conn, title="En attente", source="cuisine-facile", status="review", source_key="cuisine-facile:/x")
    assert client.get("/api/question").json()["next"] is None
