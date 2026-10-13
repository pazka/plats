"""What a catalogue reviewer may answer, and the deterministic fallback reviewer.

Standard library only: imported by the app (reviews.py, importer.py) and by
scripts/review_dishes.py outside the container.

The rules reviewer is the fallback when Claude is unavailable (no `claude` CLI, no credentials,
API down, circuit breaker open). It is conservative: it only decides obvious cases (a plain cheese,
a drink, a brand/breed, a scraped recipe with an ingredient list) and leaves everything else
unreviewed for a later Claude run. Its verdicts carry model="rules" and are re-offered to Claude
by the next export.
"""
from __future__ import annotations

import re
import unicodedata

RULES_MODEL = "rules"

KINDS = {
    "plat": "un plat cuisiné, une recette, une préparation (y compris à base de fromage : fondue, "
            "tartiflette, aligot, soufflé, gougère…)",
    "fromage_simple": "un fromage tel quel (pâte molle, pressée, persillée, croûte fleurie…), "
                      "qui n'est qu'un fromage de plus",
    "fromage_original": "un fromage à la consistance inhabituelle (coulant, à tartiner, battu, fondu, "
                        "en saumure…) ou une préparation fromagère (cancoillotte, fromage fort, "
                        "cervelle de canut…)",
    "produit": "un produit ou ingrédient peu transformé (fruit, légume, viande, charcuterie, "
               "condiment, pain de base…)",
    "sauce": "une sauce, un fond, un beurre composé",
    "boisson": "une boisson (vin, alcool, sirop, eau…)",
    "hors_sujet": "pas un aliment qu'on mange : marque, entreprise, race, cultivar, ustensile, "
                  "technique, livre, personne, concept",
}

# Head word of a description/title (after fillers) → kind. Same spirit as the importer's filters.
DRINK_WORDS = {
    "vin", "vins", "boisson", "boissons", "biere", "bieres", "liqueur", "liqueurs", "spiritueux",
    "eau", "cidre", "aperitif", "cocktail", "champagne", "whisky", "cognac", "armagnac",
    "calvados", "pastis", "cepage", "appellation", "vignoble", "domaine", "cuvee", "marc", "rhum",
    "brandy", "digestif", "sirop", "soda", "limonade", "the", "tisane", "infusion", "cafe",
}
NON_DISH_WORDS = {
    "marque", "marques", "entreprise", "societe", "restaurant", "enseigne", "chaine", "fabricant",
    "cultivar", "race", "ustensile", "appareil", "cooperative", "label", "livre", "ouvrage",
    "personnalite", "cuisinier", "chef", "elephant", "elephants",
}
FILLERS = {"un", "une", "le", "la", "les", "l", "des", "du", "de", "d", "type", "sorte",
           "variete", "francais", "francaise", "aoc", "aop", "igp", "petit", "petite", "grand", "grande"}
CHEESE_HEADS = {"fromage", "fromages"}
# A cheese that is more than "one more cheese": unusual consistency or a preparation.
CHEESE_ORIGINAL = re.compile(
    r"\b(fondu|fondue|coulant|coulante|a tartiner|tartinable|battu|fort|forte|saumure|en pot|"
    r"cancoillotte|cervelle|prepare|preparation|melange|fromage frais|fromage blanc)\b")
INGREDIENTS_MARK = "Ingrédients :"
SCRAPED_RECIPE_SOURCES = {"cuisine-facile", "elle"}


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in decomposed if not unicodedata.combining(c)).replace("œ", "oe").replace("æ", "ae")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def head_word(text: str) -> str:
    words = [w for w in norm(text).split() if w not in FILLERS]
    return words[0] if words else ""


def rules_review(item: dict) -> dict | None:
    """item: a candidate (title, text, source). Returns a verdict record or None (undecided)."""
    text = item.get("text") or ""
    description = text.split(INGREDIENTS_MARK)[0]
    head = head_word(description)
    if item.get("source") in SCRAPED_RECIPE_SOURCES and INGREDIENTS_MARK in text:
        kind, reason = "plat", "Recette d'un site de cuisine, avec une liste d'ingrédients."
    elif head in DRINK_WORDS:
        kind, reason = "boisson", f"La description commence par « {head} »."
    elif head in NON_DISH_WORDS:
        kind, reason = "hors_sujet", f"La description commence par « {head} »."
    elif head in CHEESE_HEADS:
        if CHEESE_ORIGINAL.search(norm(description)):
            kind, reason = "fromage_original", "Fromage à la consistance ou à la préparation particulière."
        else:
            kind, reason = "fromage_simple", "Décrit comme un fromage, sans particularité de consistance."
    else:
        return None
    return {"id": item["id"], "key": item["key"], "kind": kind, "keep": kind in ("plat", "fromage_original"),
            "title": "", "description": "", "reason": reason, "model": RULES_MODEL}
