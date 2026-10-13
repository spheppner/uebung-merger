"""Scan-Filter (benötigt numpy + opencv-python-headless) und Ausschnitt-Rendering."""

from __future__ import annotations

import pymupdf

from .model import Clip
from .options import LayoutOptions, ScanFilter

try:
    import cv2
    import numpy as np

    HAVE_CV = True
except ImportError:  # Filter wird dann in der GUI deaktiviert
    HAVE_CV = False


def enhance_scan(rgb: np.ndarray, mode: ScanFilter, threshold: float) -> np.ndarray:
    """Macht aus einem Handyfoto einer handschriftlichen Seite einen sauberen Scan.

    Ausgelegt auf hellgraues Karopapier mit schwarzem oder dunkelblauem
    Kugelschreiber:

    1. Beleuchtung ausgleichen: Der Papierhintergrund wird pro Farbkanal
       geschätzt (morphologisches Schließen entfernt Striche und Karolinien)
       und herausdividiert. Das entfernt Schatten, Verläufe und Farbstich.
    2. Tinte erkennen: Dunkelheit plus ein Bonus für gesättigtes Blau.
       Die grauen Karolinien sind hell und ungesättigt und fallen heraus.
    3. Hysterese: Nur zusammenhängende Bereiche, die irgendwo eindeutig
       Tinte sind, bleiben erhalten; einzelne Störpixel fliegen raus.
    4. Weicher Übergang statt harter Schwelle, damit Schrift nicht pixelig wird.

    Args:
        rgb: Bild als uint8-Array (H, W, 3).
        mode: COLOR behält die Tintenfarbe (abgedunkelt), BW liefert Schwarz.
        threshold: Untere Tintenschwelle (0..1), typ. 0.2–0.4.

    Returns:
        Bearbeitetes Bild als uint8-Array (H, W, 3).
    """
    img = rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]

    # 1. Hintergrund schätzen (verkleinert, damit es schnell geht)
    small_f = 0.25
    small = cv2.resize(
        img,
        (max(1, int(w * small_f)), max(1, int(h * small_f))),
        interpolation=cv2.INTER_AREA,
    )
    k = max(3, int(min(small.shape[:2]) * 0.03) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    bg = cv2.medianBlur((bg * 255).astype(np.uint8), k)
    bg = (
        cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        / 255.0
    )
    norm = np.clip(img / np.maximum(bg, 0.05), 0.0, 1.0)

    # 2. Tintenwahrscheinlichkeit
    r, g, b = norm[..., 0], norm[..., 1], norm[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    darkness = 1.0 - lum
    blue_bonus = np.clip(b - np.maximum(r, g), 0.0, 1.0)  # nur bläuliche Pixel
    score = darkness + 0.8 * blue_bonus

    # Karo-Erkennung: lange waagrechte/senkrechte Linien per Öffnung finden
    # und die Schwelle knapp über deren typische Dunkelheit legen.
    grid = estimate_grid_darkness(score)
    lo = float(np.clip(max(threshold, grid + 0.1), 0.05, 0.9))
    hi = min(0.95, lo + 0.18)

    # 3. Hysterese über Zusammenhangskomponenten
    weak = (score > lo).astype(np.uint8)
    strong = score > hi
    n, labels, stats, _ = cv2.connectedComponentsWithStats(weak, connectivity=8)
    keep_label = np.zeros(n, dtype=bool)
    keep_label[np.unique(labels[strong])] = True
    keep_label[0] = False
    min_area = max(4, int((w * h) * 2e-6))  # i-Punkte und Kommas bleiben
    keep_label &= stats[:, cv2.CC_STAT_AREA] >= min_area
    keep = keep_label[labels]

    # 4. Weiche Maske und Ausgabe
    alpha = np.clip((score - lo) / (hi - lo), 0.0, 1.0)
    alpha = alpha * alpha * (3 - 2 * alpha)  # smoothstep
    alpha = np.where(keep, alpha, 0.0)[..., None]

    if mode is ScanFilter.BW:
        ink = np.zeros_like(norm)
    else:
        ink = np.clip(norm, 0.0, 1.0) ** 2.2  # Farbe kräftiger und dunkler
    out = alpha * ink + (1.0 - alpha) * 1.0
    return (out * 255.0 + 0.5).astype(np.uint8)


def estimate_grid_darkness(score: np.ndarray) -> float:
    """Schätzt, wie "dunkel" die Karolinien im Tintenmaß erscheinen.

    Morphologisches Öffnen mit langen Linien-Kernen lässt nur Strukturen übrig,
    die über eine große Strecke gerade verlaufen, also vor allem das Karo.
    Weil Karopixel die Linienpixel weit überwiegen, beschreibt ein hohes
    Perzentil ihrer Werte die Karo-Dunkelheit robust.

    Args:
        score: Tintenmaß pro Pixel (float, ~0..1).

    Returns:
        Geschätzte Karo-Dunkelheit (0, wenn kein Karo gefunden wurde).
    """
    s8 = (np.clip(score, 0.0, 1.0) * 255).astype(np.uint8)
    length = max(15, int(min(score.shape) * 0.05))
    horiz = cv2.morphologyEx(
        s8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1))
    )
    vert = cv2.morphologyEx(
        s8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, length))
    )
    lines = np.maximum(horiz, vert)
    candidates = lines[lines > 15]  # etwas dunkler als reines Papier
    if candidates.size < score.size * 0.005:
        return 0.0
    return float(np.percentile(candidates, 85)) / 255.0


FilterCache = dict[tuple[int, int, tuple[float, ...], str, float, float], bytes]


def filtered_clip_png(
    doc: pymupdf.Document,
    clip: Clip,
    opt: LayoutOptions,
    out_scale: float,
    cache: FilterCache | None = None,
) -> bytes:
    """Rendert einen Ausschnitt, wendet den Scan-Filter an und liefert PNG-Bytes.

    Die Auflösung richtet sich nach der Größe im Ergebnis (opt.scan_dpi bezogen
    auf die Ausgabeseite), nicht nach der oft riesigen Fotoseite.

    Args:
        doc: Quelldokument.
        clip: Der Ausschnitt.
        opt: Layout-Einstellungen (Filtermodus, Schwelle, dpi).
        out_scale: Ausgabe-Punkte pro Quell-Punkt.
        cache: Optionaler Cache.

    Returns:
        PNG-Daten des gefilterten Ausschnitts.
    """
    zoom = max(0.1, out_scale * opt.scan_dpi / 72)
    key = (
        id(doc),
        clip.page,
        tuple(clip.rect),
        opt.scan_filter.name,
        round(opt.scan_threshold, 3),
        round(zoom, 3),
    )
    if cache is not None and key in cache:
        return cache[key]
    pix = doc[clip.page].get_pixmap(
        matrix=pymupdf.Matrix(zoom, zoom), clip=clip.rect, alpha=False
    )
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
    arr = arr[:, : pix.width * 3].reshape(pix.height, pix.width, 3)
    result = enhance_scan(arr, opt.scan_filter, opt.scan_threshold)
    if opt.scan_filter is ScanFilter.BW:
        gray = np.ascontiguousarray(result[..., 0])
        ok, buf = cv2.imencode(".png", gray)
    else:
        ok, buf = cv2.imencode(".png", cv2.cvtColor(result, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("PNG-Kodierung fehlgeschlagen")
    data = buf.tobytes()
    if cache is not None:
        cache[key] = data
    return data


def raster_clip_jpeg(
    doc: pymupdf.Document,
    clip: Clip,
    opt: LayoutOptions,
    out_scale: float,
    cache: FilterCache | None = None,
) -> bytes:
    """Schneidet einen Foto-/Scan-Ausschnitt in Zielauflösung als JPEG aus.

    Ohne das würde jeder Ausschnitt das komplette Originalfoto in die
    Ausgabe-PDF mitnehmen.
    """
    zoom = max(0.1, out_scale * opt.scan_dpi / 72)
    key = (id(doc), clip.page, tuple(clip.rect), "JPEG", 0.0, round(zoom, 3))
    if cache is not None and key in cache:
        return cache[key]
    pix = doc[clip.page].get_pixmap(
        matrix=pymupdf.Matrix(zoom, zoom), clip=clip.rect, alpha=False
    )
    data: bytes = pix.tobytes("jpg", jpg_quality=88)
    if cache is not None:
        cache[key] = data
    return data
