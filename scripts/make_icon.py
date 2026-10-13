"""Erzeugt das App-Icon (PNG + ICO). Aufruf: python scripts/make_icon.py"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "uebung_merger" / "assets"
BLUE, GREEN = (59, 130, 246), (16, 185, 129)


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, r = size * 0.06, size * 0.2
    box = (pad, pad, size - pad, size - pad)
    # linke Hälfte blau, rechte grün, auf abgerundetem Quadrat
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, r, fill=255)
    half = Image.new("RGBA", (size, size), BLUE)
    ImageDraw.Draw(half).rectangle((size / 2, 0, size, size), fill=GREEN)
    img.paste(half, mask=mask)
    # zwei weiße "Seiten" mit Textzeilen
    for i, x0 in enumerate((0.17, 0.53)):
        px0, py0 = size * x0, size * 0.22
        px1, py1 = px0 + size * 0.30, size * 0.78
        d.rounded_rectangle((px0, py0, px1, py1), size * 0.03, fill="white")
        col = BLUE if i == 0 else GREEN
        for k in range(5):
            y = py0 + size * (0.07 + 0.095 * k)
            end = px1 - size * (0.04 if k % 2 == 0 else 0.10)
            d.rounded_rectangle(
                (px0 + size * 0.04, y, end, y + size * 0.03), size * 0.015, fill=col
            )
    return img


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    icon = draw()
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    icon.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("Icon geschrieben nach", OUT)
