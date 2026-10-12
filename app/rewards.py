"""The escalating "thank you": computed server-side, rendered by static/app.js."""
from __future__ import annotations

import math

SMALL = {5: "Bien joué !", 10: "Bravo !", 15: "Excellent !", 20: "Plus que 5 avant le SUPER…"}
BADGES = [
    "🥄 Commis de cuisine",
    "🍳 Chef de partie",
    "🔪 Sous-chef",
    "👩‍🍳 Cheffe de cuisine",
    "⭐ Une étoile",
    "⭐⭐ Deux étoiles",
    "⭐⭐⭐ Trois étoiles",
    "🏅 Meilleur Ouvrier de France",
    "👑 Grand Gastronome",
    "🌌 Gastronome cosmique",
]
GENIAL_WORDS = ["GÉNIAL", "FORMIDABLE", "PRODIGIEUX", "SPECTACULAIRE", "LÉGENDAIRE",
                "MYTHIQUE", "ÉPIQUE", "COSMIQUE", "INTERGALACTIQUE", "ABSOLU"]


def palette(count: int) -> str:
    if count >= 200:
        return "cosmique"
    if count >= 100:
        return "holographique"
    if count >= 50:
        return "or"
    if count >= 25:
        return "neon"
    return "classique"


def intensity(count: int) -> float:
    """Burst strength: keeps growing (logarithmically) forever, never plateaus."""
    return round(1 + math.log2(1 + count / 5), 3)


def badge_for(count: int) -> str | None:
    if count < 30:
        return None
    idx = (count - 30) // 10
    if idx < len(BADGES):
        return BADGES[idx]
    return f"{BADGES[-1]} ×{idx - len(BADGES) + 2}"


def milestone(count: int) -> dict | None:
    if count in SMALL:
        return {"tier": "small", "level": count // 5, "title": SMALL[count]}
    if count == 25:
        return {"tier": "super", "level": 5, "title": "SUPER !",
                "subtitle": "25 réponses. Merci, vraiment."}
    if count > 25 and count % 5 == 0:
        step = (count - 25) // 5  # 1, 2, 3…
        word = GENIAL_WORDS[min((step - 1) // 2, len(GENIAL_WORDS) - 1)]
        extra = (step - 1) // 2 - (len(GENIAL_WORDS) - 1)
        title = f"{word} !" if extra <= 0 else f"{word} ×{extra + 1} !"
        result = {"tier": "genial", "level": 5 + step, "title": title}
        if count % 10 == 0:
            result["badge"] = badge_for(count)
        return result
    return None


def reward(count: int) -> dict:
    prev_stop = (count // 5) * 5
    next_stop = prev_stop + 5
    return {
        "count": count,
        "intensity": intensity(count),
        "palette": palette(count),
        "milestone": milestone(count),
        "badge": badge_for(count),
        "progress": {"from": prev_stop, "to": next_stop, "value": count},
    }
