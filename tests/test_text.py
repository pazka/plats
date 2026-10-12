import pytest

from app import services
from app.text import find_banned, find_similar, moderation_norm, slugify, title_norm


@pytest.mark.parametrize("a,b", [
    ("Les Crêpes", "crepe"),
    ("La quiche lorraine", "QUICHE LORRAINES"),
    ("Pot-au-feu", "pot au feu"),
    ("Bœuf bourguignon", "boeuf Bourguignon"),
    ("L'aligot", "aligot"),
    ("Des choux à la crème", "chou a la creme"),
])
def test_title_norm_equivalences(a, b):
    assert title_norm(a) == title_norm(b)


def test_slugify():
    assert slugify("Soupe à l'oignon gratinée") == "soupe-a-l-oignon-gratinee"
    assert slugify("!!!") == "plat"


def test_find_similar_fuzzy_and_ordering():
    cands = [(1, title_norm("Quiche lorraine")), (2, title_norm("Tarte Tatin")), (3, title_norm("Quiche aux poireaux")),
             (4, title_norm("Cassoulet"))]
    res = find_similar("quiches lorraines", cands)
    assert res[0][0] == 1
    assert 4 not in [k for k, _ in res] and 2 not in [k for k, _ in res]
    # typo tolerance
    assert find_similar("cassoulé", cands)[0][0] == 4
    assert find_similar("blanquette de veau", cands) == []


def test_similar_includes_pending_submissions(conn, dishes):
    services.create_dish(conn, title="Tartiflette", description="x" * 30, source="user", status="pending")
    services.create_dish(conn, title="Kouign-amann", description="x" * 30, source="user", status="flagged")
    titles = [s["title"] for s in services.similar_dishes(conn, "la tartiflette")]
    assert titles == ["Tartiflette"]
    assert services.similar_dishes(conn, "kouign amann") == []  # flagged ones are not leaked


@pytest.mark.parametrize("text,expected", [
    ("CONNÀÀÀRD", "conard"),
    ("s4l0p3", "salope"),
    ("@$$hole", "ashole"),
    ("Pâté   en  croûte!!", "pate en croute"),
])
def test_moderation_norm(text, expected):
    assert moderation_norm(text) == expected


BANNED = [(w, moderation_norm(w), m) for w, m in [("connard", "substring"), ("pute", "word"), ("ta gueule", "word")]]


@pytest.mark.parametrize("text", [
    "Quel CONNARD", "c0nnnnard", "espèce de c-o-n-n-a-r-d", "sale pute", "P.U.T.E", "Ta GUEULE !", "superconnard",
])
def test_banned_hits(text):
    assert find_banned(text, BANNED)


@pytest.mark.parametrize("text", [
    "Le député a mangé", "Une computation", "Chili con carne", "Pain bâtard", "Ratatouille niçoise",
])
def test_banned_no_false_positive(text):
    assert not find_banned(text, BANNED)


def test_seed_list_has_no_false_positive_on_common_dishes(conn):
    for text in ["Chili con carne", "Pain bâtard", "Coq au vin", "Bouchée à la reine", "Cassoulet de Castelnaudary",
                 "Crêpe Suzette", "Tête de veau sauce gribiche", "Pets-de-nonne", "Raclette", "Cuisses de grenouille"]:
        assert services.moderation_hits(conn, text) == [], text


def test_seed_list_catches_obvious_insults(conn):
    assert services.moderation_hits(conn, "plat de m3rde pour les encul3s")
    assert services.moderation_hits(conn, "fuuuuck this")
