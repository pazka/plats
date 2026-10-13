"""Category colours: OKLCH maths for the pastel "grocery label" palette.

Every category gets the same lightness and chroma (only the hue changes), so no answer button looks
heavier or more inviting than another. From one stored #RRGGBB the site derives, in the same hue:
  ink  = deep text colour on the pastel (contrast >= 7:1, WCAG AAA),
  edge = mid tone for the label border, the "pressed" shadow and the distribution bars.
Stdlib only (no external call, no dependency).
"""
from __future__ import annotations

import math

PASTEL_L = 0.88   # OKLCH lightness of every pastel
PASTEL_C = 0.058  # same chroma for all (the largest every hue reaches in sRGB at this lightness)
INK_L = 0.33
EDGE_L = 0.66


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_oklch(hex_color: str) -> tuple[float, float, float]:
    r, g, b = (_lin(int(hex_color[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _oklch_to_rgb(L: float, C: float, h: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def oklch_to_hex(L: float, C: float, h: float) -> str:
    """Out-of-gamut colours keep their lightness and hue; chroma is reduced until they fit."""
    for _ in range(40):
        rgb = _oklch_to_rgb(L, C, h)
        if all(-1e-4 <= v <= 1 + 1e-4 for v in rgb):
            break
        C *= 0.93
    return "#" + "".join(f"{round(min(1, max(0, _gam(min(1, max(0, v))))) * 255):02X}" for v in rgb)


def luminance(hex_color: str) -> float:
    r, g, b = (_lin(int(hex_color[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def pastel(hue: float) -> str:
    return oklch_to_hex(PASTEL_L, PASTEL_C, hue)


def palette(n: int, start: float = 25.0) -> list[str]:
    """n pastels, hues evenly spread; consecutive entries are far apart on the wheel."""
    if n <= 0:
        return []
    step = 360 / n
    order, k = [], 0
    stride = next(s for s in (3, 7, 3, 1) if math.gcd(s, n) == 1) if n > 2 else 1
    for _ in range(n):
        order.append(k)
        k = (k + stride) % n
    return [pastel(start + i * step) for i in order]


def derived(hex_color: str) -> tuple[str, str]:
    """(ink, edge) in the hue of hex_color; the ink is whichever of dark/light reads best on it."""
    L, C, h = hex_to_oklch(hex_color)
    C = min(C, 0.12)
    candidates = [oklch_to_hex(INK_L, max(C, 0.05), h), oklch_to_hex(0.97, min(C, 0.03), h), "#000000", "#FFFFFF"]
    ink = max(candidates[:2], key=lambda c: contrast(c, hex_color))
    if contrast(ink, hex_color) < 7:
        ink = max(candidates, key=lambda c: contrast(c, hex_color))
    edge = oklch_to_hex(EDGE_L, max(C, 0.06), h) if L >= 0.6 else oklch_to_hex(max(0.0, L - 0.15), C, h)
    return ink, edge
