"""Erzeugt docs/demo.gif: steuert die echte App per simulierter Maus und
verziert die Bilder (Cursor, Klick-Wellen, Datei-Chips, Untertitel, Fenster mit
Schatten, Titel- und Abspannkarte).

Aufruf:  python scripts/make_demo_gif.py
Benötigt: Pillow (pip install pillow), die App-Abhängigkeiten inkl. Scan-Filter.
"""

from __future__ import annotations

import math
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

from uebung_merger import __version__  # noqa: E402
from uebung_merger.main_window import MainWindow  # noqa: E402
from uebung_merger.model import Side  # noqa: E402

OUT = ROOT / "docs" / "demo.gif"
REPO = "github.com/spheppner/uebung-merger"
CW, CH = 1000, 620  # Größe des GIFs
WIN_W, WIN_H = 1280, 720  # logische Fenstergröße
VIEW_W = 920  # Breite des Fensters im GIF
TB = 26  # Höhe der gezeichneten Titelleiste
S = VIEW_W / WIN_W
WIN_X, WIN_Y = (CW - VIEW_W) // 2, 18
ACCENT, GREEN, ORANGE = (59, 130, 246), (16, 185, 129), (245, 158, 11)
FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default()


# --------------------------------------------------------------------------
# Demo-Material
# --------------------------------------------------------------------------

def make_angabe(path: Path) -> list[pymupdf.Rect]:
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text((50, 60), "Übung 4 – Ableitungen", fontsize=18, fontname="hebo")
    items = [
        ("Aufgabe 1", "Bestimme die Ableitung von f(x) = 3x^4 - 2x^2 + 7x - 5\nund berechne f'(2).", 200),
        ("Aufgabe 2", "Leite g(x) = x * sin(x) mit der Produktregel ab.\nWelche Steigung hat g bei x = pi?", 215),
        ("Aufgabe 3", "Bestimme alle Extremstellen von h(x) = x^3 - 6x^2 + 9x\nund klassifiziere sie.", 225),
    ]
    y, rects = 100, []
    for head, body, h in items:
        pg.insert_text((55, y + 26), head, fontsize=17, fontname="hebo")
        pg.insert_textbox(pymupdf.Rect(55, y + 40, 545, y + h), body, fontsize=15)
        rects.append(pymupdf.Rect(45, y, 550, y + h))
        y += h + 22
    doc.save(path)
    return rects


def make_photo(path: Path) -> list[tuple[float, float, float, float]]:
    """Handyfoto einer handschriftlichen Lösung auf Karopapier (Anteile der Seite)."""
    w, h = 1000, 1400
    rnd = random.Random(7)
    table = Image.new("RGB", (w, h), (58, 46, 40))
    paper = Image.new("RGB", (w - 70, h - 70), (240, 236, 222))
    d = ImageDraw.Draw(paper)
    for x in range(0, paper.width, 30):
        d.line((x, 0, x, paper.height), fill=(176, 194, 226), width=2)
    for y in range(0, paper.height, 30):
        d.line((0, y, paper.width, y), fill=(176, 194, 226), width=2)
    ink = font("Inkfree.ttf", 50)
    blocks = [
        (190, ["1)  f'(x) = 12x³ - 4x + 7", "f'(2) = 12·8 - 8 + 7 = 95"]),
        (560, ["2)  g'(x) = sin(x) + x·cos(x)", "g'(pi) = 0 + pi·(-1) = -pi"]),
        (930, ["3)  h'(x) = 3x² - 12x + 9 = 0", "x = 1  oder  x = 3", "h''(1) = -6 < 0  ->  Max", "h''(3) = 6 > 0  ->  Min"]),
    ]
    for y0, lines in blocks:
        for i, text in enumerate(lines):
            layer = Image.new("RGBA", paper.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).text(
                (60 + rnd.randint(-6, 6), y0 + i * 62), text, font=ink, fill=(22, 36, 120, 255),
                stroke_width=1, stroke_fill=(22, 36, 120, 255),
            )
            layer = layer.rotate(rnd.uniform(-1.2, 0.8), center=(60, y0 + i * 62))
            paper.paste(layer, (0, 0), layer)
    # Licht: sanfter Verlauf plus weicher Schatten (z. B. von der Hand), dazu Rauschen
    pw, ph = paper.size
    light = Image.linear_gradient("L").resize((pw, ph)).point(lambda v: 255 - v // 5)
    paper = ImageChops.multiply(paper, Image.merge("RGB", (light,) * 3))
    cast = Image.new("L", paper.size, 0)
    ImageDraw.Draw(cast).polygon([(pw * 0.55, 0), (pw, 0), (pw, ph * 0.72), (pw * 0.8, ph * 0.55)], fill=95)
    cast = ImageChops.invert(cast.filter(ImageFilter.GaussianBlur(70)))
    paper = ImageChops.multiply(paper, Image.merge("RGB", (cast,) * 3))
    noise = Image.effect_noise(paper.size, 14).convert("RGB")
    paper = Image.blend(paper, ImageChops.multiply(paper, noise), 0.08)
    shadow = Image.new("L", table.size, 0)
    ImageDraw.Draw(shadow).rectangle((40, 44, w - 22, h - 20), fill=170)
    table.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(14)))
    table.paste(paper, (35, 35))
    table = table.rotate(-1.4, resample=Image.BICUBIC, fillcolor=(58, 46, 40))
    table.save(path, quality=90)
    return [(0.07, 0.10, 0.95, 0.34), (0.07, 0.38, 0.95, 0.60), (0.07, 0.64, 0.95, 0.95)]


# --------------------------------------------------------------------------
# Bildaufbereitung (Pillow)
# --------------------------------------------------------------------------

def make_background() -> Image.Image:
    bg = Image.new("RGB", (CW, CH), (12, 14, 26))
    glow = Image.new("RGB", (CW, CH), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-200, -250, 520, 330), fill=(28, 52, 120))
    gd.ellipse((560, 330, 1240, 900), fill=(10, 80, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return ImageChops.add(bg, glow)


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


BG = make_background()
WIN_SIZE = (VIEW_W, round(WIN_H * S) + TB)
WIN_MASK = rounded_mask(WIN_SIZE, 10)


def make_base() -> Image.Image:
    shadow = Image.new("L", (CW, CH), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (WIN_X, WIN_Y + 12, WIN_X + WIN_SIZE[0], WIN_Y + WIN_SIZE[1] + 12), 12, fill=190
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    base = BG.copy()
    base.paste((0, 0, 0), (0, 0), shadow)
    return base


BASE = make_base()


def decorate_window(shot: Image.Image) -> Image.Image:
    """Fügt eine Titelleiste hinzu und rundet die Ecken."""
    win = Image.new("RGB", WIN_SIZE, (30, 31, 36))
    win.paste(shot.resize((VIEW_W, WIN_SIZE[1] - TB), Image.LANCZOS), (0, TB))
    d = ImageDraw.Draw(win)
    for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse((12 + i * 20, 8, 22 + i * 20, 18), fill=col)
    title = f"Angabe | Lösung – Merger {__version__}"
    f = font("segoeui.ttf", 12)
    d.text(((VIEW_W - d.textlength(title, font=f)) / 2, 6), title, font=f, fill=(200, 202, 210))
    return win


def make_cursor() -> Image.Image:
    big = Image.new("RGBA", (64, 80), (0, 0, 0, 0))
    pts = [(2, 2), (2, 64), (17, 50), (28, 74), (38, 69), (27, 46), (46, 46)]
    d = ImageDraw.Draw(big)
    d.polygon([(x + 3, y + 4) for x, y in pts], fill=(0, 0, 0, 90))
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(10, 10, 10, 255), width=4)
    return big.resize((24, 30), Image.LANCZOS)


CURSOR = make_cursor()


def chip(label: str, color: tuple[int, int, int], name: str) -> Image.Image:
    """Kleines Datei-Symbol, das beim "Hineinziehen" der Dateien mitläuft."""
    f, fb = font("segoeui.ttf", 14), font("segoeuib.ttf", 11)
    w = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(name, font=f)) + 64
    im = Image.new("RGBA", (w + 16, 56), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((8, 12, w + 6, 48), 9, fill=(0, 0, 0, 150))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 8, w + 4, 44), 9, fill=(250, 250, 252, 255))
    d.rounded_rectangle((13, 15, 45, 37), 5, fill=color)
    d.text((29 - d.textlength(label, font=fb) / 2, 20), label, font=fb, fill="white")
    d.text((54, 16), name, font=f, fill=(30, 32, 40))
    return im


CHIPS = {
    Side.ANGABE: chip("PDF", (220, 70, 60), "angabe.pdf"),
    Side.LOESUNG: chip("JPG", (230, 140, 30), "loesung_foto.jpg"),
}


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def draw_caption(ov: Image.Image, cap: tuple[str, str] | None, age_ms: float) -> None:
    if cap is None:
        return
    num, text = cap
    t = ease(min(1.0, age_ms / 280))
    d = ImageDraw.Draw(ov)
    f, fb = font("segoeui.ttf", 19), font("segoeuib.ttf", 17)
    tw = d.textlength(text, font=f)
    w, h = int(tw + 78), 40
    x, y = (CW - w) // 2, CH - 54 + int((1 - t) * 16)
    pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    pd.rounded_rectangle((0, 0, w - 1, h - 1), h // 2, fill=(14, 16, 30, 235), outline=(255, 255, 255, 40))
    pd.ellipse((6, 6, 34, 34), fill=ACCENT)
    pd.text((20 - pd.textlength(num, font=fb) / 2, 7), num, font=fb, fill="white")
    pd.text((46, 6), text, font=f, fill=(240, 242, 250))
    pill.putalpha(pill.getchannel("A").point(lambda v: int(v * t)))
    ov.alpha_composite(pill, (x, y))


def draw_toast(ov: Image.Image, t: float) -> None:
    if t <= 0:
        return
    t = ease(min(1.0, t))
    text = "Gespeichert: Uebung_merged.pdf"
    f = font("segoeuib.ttf", 15)
    d0 = ImageDraw.Draw(ov)
    w, h = int(d0.textlength(text, font=f)) + 70, 44
    x = WIN_X + VIEW_W - w - 22
    y = WIN_Y + WIN_SIZE[1] - 80 + int((1 - t) * 30)
    toast = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(toast)
    td.rounded_rectangle((0, 0, w - 1, h - 1), 12, fill=(16, 150, 105, 255))
    td.ellipse((10, 9, 36, 35), fill=(255, 255, 255, 255))
    td.line([(16, 22), (21, 27), (30, 16)], fill=(16, 150, 105, 255), width=3)
    td.text((46, 10), text, font=f, fill="white")
    toast.putalpha(toast.getchannel("A").point(lambda v: int(v * t)))
    ov.alpha_composite(toast, (x, y))


def card(title: str, lines: list[str], icon: bool = True) -> Image.Image:
    im = BG.copy().convert("RGBA")
    d = ImageDraw.Draw(im)
    y = 150
    if icon:
        ic = Image.open(ROOT / "uebung_merger" / "assets" / "icon.png").convert("RGBA")
        ic = ic.resize((150, 150), Image.LANCZOS)
        im.alpha_composite(ic, ((CW - 150) // 2, y))
        y += 175
    ft, fl = font("segoeuib.ttf", 54), font("segoeui.ttf", 24)
    d.text(((CW - d.textlength(title, font=ft)) / 2, y), title, font=ft, fill="white")
    y += 82
    for i, line in enumerate(lines):
        col = (170, 200, 255) if i == 0 else (150, 156, 175)
        f = fl if i == 0 else font("segoeui.ttf", 21)
        d.text(((CW - d.textlength(line, font=f)) / 2, y), line, font=f, fill=col)
        y += 40
    return im.convert("RGB")


# --------------------------------------------------------------------------
# Aufnahme
# --------------------------------------------------------------------------

class Recorder:
    def __init__(self, app: QApplication, win: MainWindow) -> None:
        self.app, self.w = app, win
        self.shots: list[Image.Image] = []
        self.frames: list[dict] = []
        self.cursor: tuple[float, float] | None = None
        self.caption: tuple[str, str] | None = None
        self.cap_age = 0.0
        self.cur_shot = -1

    # -- Hilfen ---------------------------------------------------------
    def grab(self) -> int:
        self.app.processEvents()
        img = self.w.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        pil = Image.frombuffer(
            "RGBA", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGBA",
            img.bytesPerLine(), 1,
        ).convert("RGB")
        self.shots.append(decorate_window(pil))
        self.cur_shot = len(self.shots) - 1
        return self.cur_shot

    def add(self, dur: int, regrab: bool = False, **extra: object) -> None:
        if regrab or self.cur_shot < 0:
            self.grab()
        self.frames.append(
            dict(shot=self.cur_shot, cur=self.cursor, cap=self.caption, age=self.cap_age,
                 dur=dur, **extra)
        )
        self.cap_age += dur

    def say(self, num: str, text: str) -> None:
        self.caption, self.cap_age = (num, text), 0.0

    def to_canvas(self, widget, pt: QPoint) -> tuple[float, float]:
        p = widget.mapTo(self.w, pt)
        return WIN_X + p.x() * S, WIN_Y + TB + p.y() * S

    def hold(self, ms: int, step: int = 100) -> None:
        for _ in range(max(1, ms // step)):
            self.add(step)

    def move_to(self, target: tuple[float, float], steps: int = 8, dur: int = 40, **extra) -> None:
        start = self.cursor or (target[0] - 120, target[1] + 80)
        for i in range(1, steps + 1):
            t = ease(i / steps)
            self.cursor = (start[0] + (target[0] - start[0]) * t, start[1] + (target[1] - start[1]) * t)
            self.add(dur, **extra)

    def click_fx(self) -> None:
        for i in range(7):
            self.add(45, ripple=(i + 1) / 7)

    # -- Aktionen -------------------------------------------------------
    def drag_mark(self, view, side: Side, page: int, rect: pymupdf.Rect) -> None:
        sr = view.pdf_rect_to_scene(page, rect)
        vp = view.viewport()
        p0 = view.mapFromScene(QPointF(sr.left(), sr.top()))
        p1 = view.mapFromScene(QPointF(sr.right(), sr.bottom()))
        self.move_to(self.to_canvas(vp, p0), steps=6)
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0)
        self.add(60, regrab=True)
        steps = 6
        for i in range(1, steps + 1):
            t = ease(i / steps)
            pt = QPoint(round(p0.x() + (p1.x() - p0.x()) * t), round(p0.y() + (p1.y() - p0.y()) * t))
            QTest.mouseMove(vp, pt)
            self.cursor = self.to_canvas(vp, pt)
            self.add(45, regrab=True)
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p1)
        self.w.update_preview()
        self.add(90, regrab=True)

    def click_widget(self, widget, fn=None, steps: int = 9) -> None:
        self.move_to(self.to_canvas(widget, widget.rect().center()), steps=steps)
        self.click_fx()
        if fn:
            fn()
        self.app.processEvents()
        self.add(60, regrab=True)

    def drop_files(self, side: Side, path: Path) -> None:
        pane = self.w.stacks[side].widget(0)
        target = self.to_canvas(pane, pane.rect().center())
        start = (WIN_X + 60 if side is Side.ANGABE else WIN_X + 160, WIN_Y + TB - 5)
        self.cursor = None
        n = 14
        for i in range(n + 1):
            t = ease(i / n)
            pos = (start[0] + (target[0] - start[0]) * t, start[1] + (target[1] - start[1]) * t)
            self.cursor = pos
            if i == n - 3:
                pane._set_drop(True)
            self.add(45, regrab=(i >= n - 3), chip=(CHIPS[side], pos))
        self.hold(200, 100)
        pane._set_drop(False)
        self.w.add_files(side, [path])
        self.cursor = None
        self.add(400, regrab=True)


def render(rec: Recorder) -> list[tuple[Image.Image, int]]:
    total = sum(f["dur"] for f in rec.frames)
    out: list[tuple[Image.Image, int]] = []
    elapsed = 0
    prog_h = 4
    cache: dict[int, Image.Image] = {}
    for fr in rec.frames:
        sh = rec.shots[fr["shot"]]
        if fr["shot"] not in cache:
            cache.clear()
            im = BASE.copy()
            im.paste(sh, (WIN_X, WIN_Y), WIN_MASK)
            cache[fr["shot"]] = im
        im = cache[fr["shot"]].convert("RGBA")
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        draw_caption(ov, fr["cap"], fr["age"])
        if fr.get("toast"):
            draw_toast(ov, fr["toast"])
        if fr.get("chip"):
            sprite, (x, y) = fr["chip"]
            ov.alpha_composite(sprite, (int(x - 20), int(y - 24)))
        if fr.get("ripple") and fr["cur"]:
            t = fr["ripple"]
            cx, cy = fr["cur"]
            r = 6 + 30 * ease(t)
            rd = ImageDraw.Draw(ov)
            rd.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(*ORANGE, int(255 * (1 - t))), width=3)
            r2 = r * 0.55
            rd.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), fill=(*ORANGE, int(90 * (1 - t))))
        if fr["cur"]:
            ov.alpha_composite(CURSOR, (int(fr["cur"][0] - 2), int(fr["cur"][1] - 2)))
        # Fortschrittsbalken
        frac = (elapsed + fr["dur"]) / total
        pd = ImageDraw.Draw(ov)
        pd.rectangle((0, CH - prog_h, int(CW * frac), CH), fill=(*ACCENT, 255))
        pd.rectangle((int(CW * frac * 0.5), CH - prog_h, int(CW * frac), CH), fill=(*GREEN, 255))
        elapsed += fr["dur"]
        out.append((Image.alpha_composite(im, ov).convert("RGB"), fr["dur"]))
    return out


def crossfade(a: Image.Image, b: Image.Image, n: int = 4, dur: int = 45) -> list[tuple[Image.Image, int]]:
    return [(Image.blend(a, b, ease((i + 1) / (n + 1))), dur) for i in range(n)]


def fade_card(img: Image.Image, n: int = 5) -> list[tuple[Image.Image, int]]:
    return [(Image.blend(BG, img, (i + 1) / n), 50) for i in range(n)]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="um_demo_"))
    angabe, foto = tmp / "angabe.pdf", tmp / "loesung_foto.jpg"
    a_rects = make_angabe(angabe)
    l_fracs = make_photo(foto)

    app = QApplication([])
    w = MainWindow()
    w.resize(WIN_W, WIN_H)
    w.show()
    app.processEvents()
    rec = Recorder(app, w)

    # 1 -- Hinzufügen
    rec.say("1", "Angabe (PDF) und Lösung (Foto) hineinziehen")
    rec.add(600, regrab=True)
    rec.drop_files(Side.ANGABE, angabe)
    rec.drop_files(Side.LOESUNG, foto)
    page = w.docs[Side.LOESUNG][0].rect
    l_rects = [
        pymupdf.Rect(page.width * x0, page.height * y0, page.width * x1, page.height * y1)
        for x0, y0, x1, y1 in l_fracs
    ]

    # 2 -- Markieren
    rec.say("2", "Aufgaben markieren – die Zuordnung passiert automatisch")
    for a, l in zip(a_rects, l_rects):
        rec.drag_mark(w.views[Side.ANGABE], Side.ANGABE, 0, a)
        rec.drag_mark(w.views[Side.LOESUNG], Side.LOESUNG, 0, l)
        rec.hold(100, 100)
    rec.hold(400)

    # 3 -- Vorschau
    rec.say("3", "Live-Vorschau: ein Klick wählt die Aufgabe")
    w.update_preview()
    _, pg, rect = w.preview_regions[1]
    sp = w.preview.mapFromScene(w.preview.pdf_rect_to_scene(pg, rect).center())
    rec.move_to(rec.to_canvas(w.preview.viewport(), sp), steps=12)
    rec.click_fx()
    QTest.mouseClick(w.preview.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, sp)
    rec.add(100, regrab=True)
    rec.hold(700)

    # 4 -- Anordnung
    rec.say("4", "Untereinander, wenn die Aufgabe dadurch größer wird")
    rec.click_widget(w.btn_stack, lambda: (w.btn_stack.click(), w.update_preview()))
    rec.hold(1000)
    rec.click_widget(w.btn_side, lambda: (w.btn_side.click(), w.update_preview()), steps=7)
    rec.hold(500)

    # 5 -- Scan-Filter
    rec.say("5", "Scan-Filter: aus dem Foto wird ein sauberer Scan")
    rec.move_to(rec.to_canvas(w.cmb_filter, w.cmb_filter.rect().center()), steps=10)
    rec.click_fx()
    w.cmb_filter.setCurrentIndex(1)
    w.update_preview()
    rec.add(100, regrab=True)
    rec.hold(1500)

    # 6 -- Groß und Export
    rec.say("6", "Prüfen, exportieren – fertig zum Beamen")
    rec.click_widget(w.btn_big, w.btn_big.click)
    rec.hold(1100)
    export = next(b for b in w.findChildren(QToolButton) if "Exportieren" in b.text())
    rec.move_to(rec.to_canvas(export, export.rect().center()), steps=12)
    rec.click_fx()
    for i in range(1, 9):
        rec.add(45, toast=i / 8)
    rec.hold(1200)

    frames = render(rec)
    intro = card(
        "Übung Merger",
        ["Angabe links. Lösung rechts. Fertig zum Beamen.", f"Version {__version__}"],
    )
    outro = card(
        f"Übung Merger {__version__}",
        ["Kostenlos & Open Source", f"Windows-EXE zum Download:  {REPO}/releases"],
    )
    seq: list[tuple[Image.Image, int]] = []
    seq += fade_card(intro)
    seq.append((intro, 1000))
    seq += crossfade(intro, frames[0][0])
    seq += frames
    seq += crossfade(frames[-1][0], outro)
    seq.append((outro, 3000))

    # gemeinsame Palette für ruhige Farben und kleine Datei
    sample = Image.new("RGB", (CW, CH * 4))
    for i, k in enumerate((len(seq) // 8, len(seq) // 3, len(seq) // 2, len(seq) * 3 // 4)):
        sample.paste(seq[k][0], (0, CH * i))
    palette = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    quant = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im, _ in seq]
    OUT.parent.mkdir(exist_ok=True)
    quant[0].save(
        OUT, save_all=True, append_images=quant[1:], duration=[d for _, d in seq],
        loop=0, optimize=False, disposal=1,
    )
    print(f"{OUT}  {OUT.stat().st_size / 1e6:.1f} MB  {len(seq)} Bilder  "
          f"{sum(d for _, d in seq) / 1000:.1f} s")


if __name__ == "__main__":
    main()
