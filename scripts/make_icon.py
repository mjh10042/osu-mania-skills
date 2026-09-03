"""Draw the app icon and write assets/skull.ico.

Kept as a script rather than a checked-in binary alone, because a 130 KB .ico is the kind
of file nobody can review in a diff. The shape is a handful of primitives, so the source
of truth is the code and the .ico is its build output - regenerate with:

    python scripts/make_icon.py

Everything is drawn at SUPERSAMPLE times the final size and reduced with Lanczos; the
alternative is drawing 16x16 directly, which turns every curve into a staircase.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "skull.ico"

# The two flat colours the reference uses, and nothing else.
BONE = (204, 214, 221, 255)
DARK = (41, 47, 51, 255)

SIZES = [16, 32, 48, 64, 128, 256]
SUPERSAMPLE = 8
CANVAS = 512          # the coordinate space every number below is written in


def draw(d: ImageDraw.ImageDraw, s: float) -> None:
    """Paint the skull into a canvas of `CANVAS * s` pixels."""
    def box(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    # --- jaw: three teeth hanging below the cranium, rounded at the bottom only ---
    for x0, x1 in ((112, 202), (211, 301), (310, 400)):
        d.rounded_rectangle(box(x0, 330, x1, 500), radius=45 * s, fill=BONE)
        d.rectangle(box(x0, 330, x1, 430), fill=BONE)          # square off the top again

    # --- cranium: a wide dome whose sides tuck back in over the jaw ---
    d.ellipse(box(26, 0, 486, 424), fill=BONE)
    d.rounded_rectangle(box(26, 150, 486, 440), radius=150 * s, fill=BONE)

    # --- eye sockets ---
    for cx in (158, 354):
        r = 72
        d.ellipse(box(cx - r, 197 - r, cx + r, 197 + r), fill=DARK)

    # --- nose: a rounded blob with a small peak on top and a notch cut up from below,
    #     which is what reads as an inverted heart rather than a plain dot ---
    d.rounded_rectangle(box(220, 316, 292, 362), radius=21 * s, fill=DARK)
    d.polygon([(256 * s, 300 * s), (233 * s, 330 * s), (279 * s, 330 * s)], fill=DARK)
    d.polygon([(256 * s, 330 * s), (241 * s, 366 * s), (271 * s, 366 * s)], fill=BONE)

    # --- the gaps between the teeth are cut back out, not painted around ---
    for x0, x1 in ((202, 211), (301, 310)):
        d.rectangle(box(x0, 424, x1, 512), fill=(0, 0, 0, 0))


def render(px: int) -> Image.Image:
    big = CANVAS * SUPERSAMPLE
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw(ImageDraw.Draw(im), SUPERSAMPLE)
    return im.resize((px, px), Image.LANCZOS)


def main() -> int:
    master = render(1024)
    OUT.parent.mkdir(exist_ok=True)
    master.save(OUT, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes, sizes {SIZES})")
    if "--preview" in sys.argv:
        p = OUT.with_name("_preview.png")
        render(512).save(p)
        print(f"preview -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
