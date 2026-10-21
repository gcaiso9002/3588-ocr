"""Generate the application icon (3588-ocr.ico) and the tray PNG."""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))


def draw(size=256, rounded=True):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64.0
    if rounded:
        d.rounded_rectangle([4 * s, 4 * s, 60 * s, 60 * s], radius=12 * s,
                            fill=(28, 116, 214, 255))
    else:
        d.rectangle([4 * s, 4 * s, 60 * s, 60 * s], fill=(28, 116, 214, 255))
    # a sheet of paper
    d.rounded_rectangle([14 * s, 12 * s, 38 * s, 48 * s], radius=2 * s,
                        fill=(255, 255, 255, 255))
    for y in (20, 27, 34, 41):
        d.line([18 * s, y * s, 34 * s, y * s], fill=(28, 116, 214, 255),
               width=max(1, int(2 * s)))
    # a magnifier over it
    d.ellipse([30 * s, 28 * s, 52 * s, 50 * s], outline=(255, 255, 255, 255),
              width=max(2, int(4 * s)))
    d.line([48 * s, 46 * s, 59 * s, 57 * s], fill=(255, 255, 255, 255),
           width=max(2, int(5 * s)))
    return img


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "3588-ocr.ico")
    base = draw(256)
    base.save(out, format="ICO",
              sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32),
                     (24, 24), (16, 16)])
    print("wrote", out)
    png = os.path.splitext(out)[0] + ".png"
    base.resize((256, 256)).save(png)
    print("wrote", png)
