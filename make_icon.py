#!/usr/bin/env python3
"""Draw the app icon: a viewfinder frame around a lens.

Deliberately not a copy of Google's Lens mark — this is our launcher, and it
should not pass itself off as Google's app.
"""
import os
import sys

from PIL import Image, ImageDraw

SIZES = (256, 128, 64, 48, 32)
BG = (32, 33, 36, 255)       # the grey the panel sits on
FRAME = (232, 234, 237, 255)
RING = (138, 180, 248, 255)  # a calm blue, distinct from Google's four colours
CORE = (232, 234, 237, 255)


def draw(size, variant="search"):
    """The same viewfinder frame for all three, with a different mark inside so
    they are told apart at a glance in the dock."""
    s = 512  # draw big, downsample once
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = s // 2

    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=BG)

    # Viewfinder corners.
    m, arm, w = int(s * 0.18), int(s * 0.13), int(s * 0.045)
    for cx, cy, dx, dy in (
        (m, m, 1, 1), (s - m, m, -1, 1), (m, s - m, 1, -1), (s - m, s - m, -1, -1)
    ):
        d.line((cx, cy, cx + arm * dx, cy), fill=FRAME, width=w)
        d.line((cx, cy, cx, cy + arm * dy), fill=FRAME, width=w)

    if variant == "search":
        r = int(s * 0.19)
        d.ellipse((c - r, c - r, c + r, c + r), outline=RING, width=int(s * 0.055))
        cr = int(s * 0.062)
        d.ellipse((c - cr, c - cr, c + cr, c + cr), fill=CORE)

    elif variant == "text":
        # Lines of text, the last one short, the way a paragraph ends.
        lw, gap = int(s * 0.05), int(s * 0.095)
        half = int(s * 0.16)
        for i, frac in enumerate((1.0, 1.0, 0.55)):
            y = c - gap + i * gap
            d.line((c - half, y, c - half + int(2 * half * frac), y),
                   fill=RING if i < 2 else CORE, width=lw)

    elif variant == "translate":
        # A double-headed arrow: this becomes that.
        half, y = int(s * 0.17), c
        lw, head = int(s * 0.05), int(s * 0.07)
        d.line((c - half, y, c + half, y), fill=RING, width=lw)
        for sign in (1, -1):
            tip = c + sign * half
            d.polygon([(tip + sign * int(s * 0.02), y),
                       (tip - sign * head, y - head),
                       (tip - sign * head, y + head)], fill=RING)
        d.line((c - half, y - int(s * 0.115), c + half, y - int(s * 0.115)),
               fill=CORE, width=int(s * 0.035))

    elif variant == "ask":
        # A four-point spark: "ask about it" without borrowing anyone's mark.
        def spark(cx, cy, rad, thick):
            d.polygon(
                [(cx, cy - rad), (cx + thick, cy - thick), (cx + rad, cy),
                 (cx + thick, cy + thick), (cx, cy + rad), (cx - thick, cy + thick),
                 (cx - rad, cy), (cx - thick, cy - thick)],
                fill=RING,
            )
        spark(c, c, int(s * 0.20), int(s * 0.055))
        spark(int(c + s * 0.155), int(c - s * 0.145), int(s * 0.075), int(s * 0.021))

    return im.resize((size, size), Image.LANCZOS)


VARIANTS = {
    "lens-desktop": "search",
    "lens-desktop-text": "text",
    "lens-desktop-ask": "ask",
    "lens-desktop-translate": "translate",
}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/.local/share/icons/hicolor"
    )
    for name, variant in VARIANTS.items():
        for size in SIZES:
            out = f"{root}/{size}x{size}/apps"
            os.makedirs(out, exist_ok=True)
            draw(size, variant).save(f"{out}/{name}.png")
        print("wrote", name)


if __name__ == "__main__":
    main()
