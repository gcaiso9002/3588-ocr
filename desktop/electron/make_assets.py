"""Generate the Electron app icons.

    python make_assets.py

Writes assets/icon.ico, assets/icon.png and assets/tray.png.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

BLUE = (47, 111, 237, 255)
BLUE_LIGHT = (74, 139, 255, 255)
WHITE = (255, 255, 255, 255)


def app_icon(size=256):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64.0
    # rounded square
    d.rounded_rectangle([4 * s, 4 * s, 60 * s, 60 * s], radius=13 * s, fill=BLUE)
    # a sheet of paper
    d.rounded_rectangle([14 * s, 12 * s, 38 * s, 48 * s], radius=2 * s, fill=WHITE)
    for y in (20, 27, 34, 41):
        d.line([18 * s, y * s, 34 * s, y * s], fill=BLUE, width=max(1, int(2 * s)))
    # a magnifier
    d.ellipse([30 * s, 28 * s, 52 * s, 50 * s], outline=WHITE,
              width=max(2, int(4 * s)))
    d.line([48 * s, 46 * s, 59 * s, 57 * s], fill=WHITE,
           width=max(2, int(5 * s)))
    return img


def tray_icon(size=32):
    """A flat monochrome mark -- Windows renders tray icons better that way."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 32.0
    d.rounded_rectangle([2 * s, 2 * s, 30 * s, 30 * s], radius=7 * s, fill=WHITE)
    d.rounded_rectangle([9 * s, 7 * s, 21 * s, 25 * s], radius=1.5 * s,
                        fill=(0, 0, 0, 0))
    for y in (11, 15, 19, 22):
        d.line([11 * s, y * s, 19 * s, y * s], fill=WHITE,
               width=max(1, int(1.6 * s)))
    d.ellipse([15 * s, 14 * s, 26 * s, 25 * s], outline=WHITE,
              width=max(1, int(2.4 * s)))
    d.line([22 * s, 22 * s, 28 * s, 28 * s], fill=WHITE,
           width=max(1, int(2.8 * s)))
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
    base.resize((256, 256)).save(png)
    print("wrote", png)
    tray = os.path.join(ASSETS, "tray.png")
    tray_icon(32).save(tray)
    print("wrote", tray)


if __name__ == "__main__":
    main()
