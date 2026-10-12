"""Text normalisation: slugs, look-alike titles and banned-word matching."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

ARTICLES = {
    "le", "la", "les", "l", "un", "une", "des", "du", "de", "d", "au", "aux",
    "a", "en", "et", "the", "of", "with", "avec", "sur", "facon", "maniere",
}
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "@": "a", "$": "s", "5": "s", "7": "t"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_REPEATS = re.compile(r"(.)\1+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE").replace("ß", "ss")


def basic_norm(text: str) -> str:
    """Lowercase, accents stripped, punctuation collapsed to single spaces."""
    return _NON_ALNUM.sub(" ", strip_accents(text or "").lower()).strip()


def slugify(text: str, max_len: int = 70) -> str:
    slug = basic_norm(text).replace(" ", "-")
    return slug[:max_len].strip("-") or "plat"


def _singular(word: str) -> str:
    if len(word) > 3 and word[-1] in "sx" and not word.endswith("ss"):
        return word[:-1]
    return word


def title_norm(title: str) -> str:
    """Normalisation for look-alike detection: accent/case/article/plural-insensitive."""
    words = [w for w in basic_norm(title).split() if w not in ARTICLES]
    return " ".join(_singular(w) for w in words)


def similarity(a_norm: str, b_norm: str) -> float:
    if not a_norm or not b_norm:
        return 0.0
    return float(fuzz.token_set_ratio(a_norm, b_norm))


def find_similar(name: str, candidates, threshold: float = 85.0, limit: int = 5):
    """candidates: iterable of (key, normalised_title). Returns [(key, score)] best first."""
    target = title_norm(name)
    if len(target) < 2:
        return []
    scored = []
    for key, cand_norm in candidates:
        score = similarity(target, cand_norm)
        if score >= threshold:
            # Tie-break subset matches ("tarte" vs every "tarte …") by plain ratio.
            scored.append((score, float(fuzz.ratio(target, cand_norm)), key))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [(key, score) for score, _, key in scored[:limit]]


# ---------------------------------------------------------------- banned words

def moderation_norm(text: str) -> str:
    """Lowercase, accents stripped, leetspeak mapped, repeated letters collapsed."""
    text = strip_accents(text or "").lower().translate(LEET)
    text = _NON_ALNUM.sub(" ", text)
    return _REPEATS.sub(r"\1", text).strip()


def find_banned(text: str, banned) -> list[str]:
    """banned: iterable of (word, word_norm, mode). Returns the original words that matched."""
    norm = moderation_norm(text)
    if not norm:
        return []
    padded = f" {norm} "
    squashed = norm.replace(" ", "")
    hits = []
    for word, word_norm, mode in banned:
        if not word_norm:
            continue
        if mode == "substring":
            if word_norm in norm or word_norm.replace(" ", "") in squashed:
                hits.append(word)
        elif f" {word_norm} " in padded:
            hits.append(word)
    return hits
