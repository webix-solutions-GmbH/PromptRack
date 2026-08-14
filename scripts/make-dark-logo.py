#!/usr/bin/env python3
"""Generate dark-mode variants of the PromptRack mark.

The source PNGs draw the rack outline and document strokes in near-black
(RGB ~30, low saturation) on a transparent background, with a green accent
(RGB ~(128,182,33), high saturation) for the highlighted slot and white for
its checkmark. On a dark topbar the near-black strokes disappear; the green
accent and white checkmark are already legible there and must not shift.

Run: cd /Users/phil/Projects/PromptRack && uv run --with pillow python scripts/make-dark-logo.py

Pixel palette (sampled via `Image.getcolors`, see the design-session notes)
splits cleanly into three clusters by max(r,g,b):
  - ~20-90, low saturation: the near-black strokes -> invert toward white.
  - ~130-150, low saturation: the secondary gray bars -> left alone. Their
    absolute value already reads correctly on either background: on white
    they sit at ~140/255 (dimmer than full black), and left unchanged on a
    near-black background they sit at ~140/255 above the background (still
    dimmer than the inverted ~220 main strokes), so the light/dark hierarchy
    between "main stroke" and "secondary bar" survives untouched.
  - ~130-185, high saturation: the green accent -> left alone.
A smooth (not hard-cutoff) weight based on both value and saturation blends
the boundary between the first two clusters, since the source is itself
lightly anti-aliased between shapes at these thresholds.
"""

from pathlib import Path

from PIL import Image

BRAND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "brand"

# Value (max channel) fully inverted below VALUE_LOW, fully untouched above
# VALUE_HIGH, linearly blended between. Same shape for saturation.
VALUE_LOW, VALUE_HIGH = 40, 100
SAT_LOW, SAT_HIGH = 0.15, 0.40


def _falloff(x: float, low: float, high: float) -> float:
    """1.0 at/below `low`, 0.0 at/above `high`, linear ramp between."""
    if x <= low:
        return 1.0
    if x >= high:
        return 0.0
    return (high - x) / (high - low)


def _saturation(r: int, g: int, b: int) -> float:
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == 0 else (mx - mn) / mx


def darken_to_light(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            mx = max(r, g, b)
            weight = _falloff(mx, VALUE_LOW, VALUE_HIGH) * _falloff(
                _saturation(r, g, b), SAT_LOW, SAT_HIGH
            )
            if weight <= 0:
                continue
            # Blend each channel toward its inversion by `weight`, rather
            # than snapping straight to 255 - v, so a transitional
            # anti-aliased pixel lands between the two instead of banding.
            nr = round(r + weight * (255 - r - r))
            ng = round(g + weight * (255 - g - g))
            nb = round(b + weight * (255 - b - b))
            px[x, y] = (
                max(0, min(255, nr)),
                max(0, min(255, ng)),
                max(0, min(255, nb)),
                a,
            )
    return img


def main() -> None:
    for src_name, dst_name in (
        ("promptrack-mark.png", "promptrack-mark-dark.png"),
        ("promptrack-mark-128.png", "promptrack-mark-dark-128.png"),
    ):
        src = BRAND_DIR / src_name
        dst = BRAND_DIR / dst_name
        darken_to_light(Image.open(src)).save(dst)
        print(f"wrote {dst}")


if __name__ == "__main__":
    main()
