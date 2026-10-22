"""Generate the Electron app icons.

    python make_assets.py

Writes assets/icon.ico, assets/icon.png (app icon) and assets/tray.png
(tray icon).  The mark is a magnifying glass over a block of text, drawn as
a white glyph on a blue gradient tile; the lens is punched out so it reads
as one continuous shape instead of a pile of stickers.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

# ---------------------------------------------------------------- palette

TOP = (86, 155, 255)        # gradient top
BOTTOM = (37, 82, 214)      # gradient bottom
WHITE = (255, 255, 255, 255)


def gradient(size):
    """Vertical gradient tile."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        d.line([(0, y), (size, y)],
               fill=tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    return img


def rounded_tile(size, radius_ratio=0.22, inset_ratio=0.03):
    """The blue rounded square, with a soft gloss on the upper half."""
    tile = gradient(size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [size * inset_ratio, size * inset_ratio,
         size * (1 - inset_ratio), size * (1 - inset_ratio)],
        radius=size * radius_ratio, fill=255)
    tile.putalpha(mask)

    gloss = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    gd = ImageDraw.Draw(gloss)
    gd.ellipse([-size * 0.3, -size * 0.75, size * 1.3, size * 0.55],
               fill=(255, 255, 255, 34))
    gloss.putalpha(Image.composite(gloss.getchannel("A"),
                                   Image.new("L", (size, size), 0), mask))
    tile.alpha_composite(gloss)
    return tile


def _magnifier(draw, cx, cy, r, ring, handle, color=WHITE):
    """Ring + handle."""
    ring = max(1, int(round(ring)))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=ring)
    sx = cx + r * 0.72
    sy = cy + r * 0.72
    ex = sx + handle * 0.78
    ey = sy + handle * 0.78
    draw.line([sx, sy, ex, ey], fill=color, width=max(1, int(round(ring * 1.15))))
    # round the handle tip
    tip = ring * 0.575
    draw.ellipse([ex - tip, ey - tip, ex + tip, ey + tip], fill=color)


def _punch_lens(img, cx, cy, r):
    """Erase the lens interior so the glyph reads as one shape.

    The mask must be 0 everywhere EXCEPT inside the lens -- otherwise the
    paste replaces the whole tile and the rounded corners are lost.
    """
    hole = Image.new("L", img.size, 0)
    ImageDraw.Draw(hole).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    img.paste(gradient(img.size[0]), (0, 0), hole)


def app_icon(size=256):
    """Blue tile, white text block, magnifying glass with a cut-out lens."""
    s = size / 256.0
    img = rounded_tile(size)
    ov = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)

    # a block of "text" on the left
    for w, y in ((66, 74), (50, 108), (36, 142)):
        od.rounded_rectangle([54 * s, y * s, (54 + w) * s, (y + 17) * s],
                             radius=8.5 * s, fill=(255, 255, 255, 232))

    # magnifying glass over the lower right
    cx, cy, r = 160 * s, 160 * s, 46 * s
    _magnifier(od, cx, cy, r, ring=18 * s, handle=42 * s)

    img.alpha_composite(ov)
    _punch_lens(img, cx, cy, r - 18 * s / 2)
    return img


def tray_icon(size=32):
    """Same tile, but only the magnifier — legible down to 16 px."""
    s = size / 32.0
    img = rounded_tile(size, radius_ratio=0.24, inset_ratio=0.04)
    ov = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    cx, cy, r = 13.8 * s, 13.2 * s, 8.0 * s
    _magnifier(od, cx, cy, r, ring=3.2 * s, handle=9.0 * s)
    img.alpha_composite(ov)
    _punch_lens(img, cx, cy, r - 3.2 * s / 2)
    return img


def main():
    os.makedirs(ASSETS, exist_ok=True)

    base = app_icon(256)
    ico = os.path.join(ASSETS, "icon.ico")
    base.save(ico, format="ICO",
              sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32),
                     (24, 24), (16, 16)])
    print("wrote", ico)

    png = os.path.join(ASSETS, "icon.png")
    base.save(png)
    print("wrote", png)

    tray = os.path.join(ASSETS, "tray.png")
    tray_icon(32).save(tray)
    print("wrote", tray)

    tray2 = os.path.join(ASSETS, "tray@2x.png")
    tray_icon(64).save(tray2)
    print("wrote", tray2)


if __name__ == "__main__":
    main()
