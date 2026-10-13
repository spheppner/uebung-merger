"""Layout der Aufgaben auf A4-Querformat-Seiten."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pymupdf

from .constants import MM
from .documents import is_raster_clip
from .model import Clip, Pair, Side
from .options import LayoutOptions, ScanFilter
from .scan import HAVE_CV, FilterCache, filtered_clip_png, raster_clip_jpeg


@dataclass
class _Piece:
    clip: Clip
    height: float  # Höhe bei der Referenzbreite, mit der _stack gerufen wurde
    rotated: bool  # um 90° gegen den Uhrzeigersinn gedreht platzieren


def _stack(
    clips: list[Clip], width: float, opt: LayoutOptions
) -> tuple[list[_Piece], float]:
    """Ausschnitte einer Seite untereinander, jeweils auf die gegebene Breite."""
    pieces: list[_Piece] = []
    for c in clips:
        cw, ch = (
            (c.rect.height, c.rect.width) if c.rotate else (c.rect.width, c.rect.height)
        )
        pieces.append(_Piece(c, ch * width / cw, c.rotate))
    total = sum(p.height for p in pieces) + opt.piece_gap_pt * max(0, len(pieces) - 1)
    return pieces, total


@dataclass(frozen=True)
class _Geometry:
    w: float
    h: float
    m: float
    col_w: float  # Breite einer Spalte (nebeneinander)
    full_w: float  # nutzbare Seitenbreite (untereinander)
    usable_h: float

    @staticmethod
    def of(opt: LayoutOptions) -> _Geometry:
        page = pymupdf.paper_rect("a4-l")
        m = opt.margin_mm * MM
        return _Geometry(
            page.width,
            page.height,
            m,
            page.width / 2 - m - opt.gutter_pt,
            page.width - 2 * m,
            page.height - 2 * m,
        )


@dataclass
class _Placed:
    side: Side
    piece: _Piece
    rect: pymupdf.Rect  # relativ zur Oberkante der Zeile


@dataclass
class _Row:
    placed: list[_Placed]
    height: float
    two_columns: bool  # nur dann läuft die Mittellinie durch
    divider: tuple[float, float, float] | None = None  # (x0, x1, y) Angabe/Lösung


def _place_column(
    side: Side,
    pieces: list[_Piece],
    x: float,
    y: float,
    width: float,
    scale: float,
    opt: LayoutOptions,
) -> tuple[list[_Placed], float]:
    placed: list[_Placed] = []
    for piece in pieces:
        ph = piece.height * scale
        placed.append(_Placed(side, piece, pymupdf.Rect(x, y, x + width, y + ph)))
        y += ph + opt.piece_gap_pt * scale
    return placed, y


def _row_layout(pair: Pair, opt: LayoutOptions, geo: _Geometry) -> _Row | None:
    """Berechnet, wo die Ausschnitte einer Aufgabe innerhalb ihrer Zeile liegen.

    Nebeneinander: Angabe links, Lösung rechts, je Spaltenbreite.
    Untereinander: Angabe oben, Lösung darunter, beide gleich breit über die
        ganze Seitenbreite; ist das zu hoch, wird beides gemeinsam so weit
        verkleinert, dass die Aufgabe auf eine Seite passt (zentriert).
    Zentriert (keine Lösung): nur die Angabe, mittig in Spaltenbreite.
    """
    if pair.centered:
        pieces, total = _stack(pair.angabe, geo.col_w, opt)
        if total <= 0:
            return None
        s = min(1.0, geo.usable_h / total)
        x = (geo.w - geo.col_w * s) / 2
        placed, _ = _place_column(Side.ANGABE, pieces, x, 0, geo.col_w * s, s, opt)
        return _Row(placed, total * s, two_columns=False)

    if pair.stacked:
        a_pieces, a_total = _stack(pair.angabe, geo.full_w, opt)
        l_pieces, l_total = _stack(pair.loesung, geo.full_w, opt)
        inner = opt.row_gap_pt if a_pieces and l_pieces else 0.0
        total = a_total + inner + l_total
        if total <= 0:
            return None
        s = min(1.0, geo.usable_h / total)
        width = geo.full_w * s
        x = (geo.w - width) / 2
        placed, y = _place_column(Side.ANGABE, a_pieces, x, 0, width, s, opt)
        divider = None
        if inner:
            y = a_total * s + inner * s
            divider = (x, x + width, a_total * s + inner * s / 2)
        more, _ = _place_column(Side.LOESUNG, l_pieces, x, y, width, s, opt)
        return _Row(placed + more, total * s, two_columns=False, divider=divider)

    stacks = {side: _stack(pair.clips(side), geo.col_w, opt) for side in Side}
    row_h = max(total for _, total in stacks.values())
    if row_h <= 0:
        return None
    s = min(1.0, geo.usable_h / row_h)
    xs = {Side.ANGABE: geo.m, Side.LOESUNG: geo.w / 2 + opt.gutter_pt}
    placed = []
    for side, (pieces, _) in stacks.items():
        p, _ = _place_column(side, pieces, xs[side], 0, geo.col_w * s, s, opt)
        placed += p
    return _Row(placed, row_h * s, two_columns=True)


def stacked_gain(pair: Pair, opt: LayoutOptions) -> float | None:
    """Wie viel größer die Aufgabe untereinander statt nebeneinander erscheint.

    Verglichen wird die Breite, in der die Lösung (bzw. ohne Lösung die
    Angabe) gesetzt wird. 1.6 heißt: untereinander 1,6-mal so groß.
    None, wenn die Aufgabe leer ist.
    """
    geo = _Geometry.of(opt)
    side = _row_layout(replace(pair, stacked=False, centered=False), opt, geo)
    stacked = _row_layout(replace(pair, stacked=True, centered=False), opt, geo)
    if side is None or stacked is None:
        return None
    ref = Side.LOESUNG if pair.loesung else Side.ANGABE

    def width(row: _Row) -> float:
        return next(p.rect.width for p in row.placed if p.side is ref)

    return width(stacked) / width(side)


def _draw_piece(
    page: pymupdf.Page,
    target: pymupdf.Rect,
    doc: pymupdf.Document,
    piece: _Piece,
    side: Side,
    opt: LayoutOptions,
    cache: FilterCache | None,
) -> None:
    rot = 90 if piece.rotated else 0
    src_w = piece.clip.rect.height if piece.rotated else piece.clip.rect.width
    out_scale = target.width / src_w
    use_filter = (
        HAVE_CV
        and opt.scan_filter is not ScanFilter.OFF
        and side is Side.LOESUNG
        and not piece.clip.no_scan
    )
    try:
        if is_raster_clip(doc, piece.clip):
            if use_filter:
                img = filtered_clip_png(doc, piece.clip, opt, out_scale, cache)
            else:
                img = raster_clip_jpeg(doc, piece.clip, opt, out_scale, cache)
            page.insert_image(target, stream=img, rotate=rot)
        else:
            page.show_pdf_page(
                target, doc, piece.clip.page, clip=piece.clip.rect, rotate=rot
            )
    except ValueError:
        pass  # z. B. völlig leere Quellseite: nichts zu zeigen


def build_output(
    doc_angabe: pymupdf.Document | None,
    doc_loesung: pymupdf.Document | None,
    pairs: list[Pair],
    opt: LayoutOptions,
    cache: FilterCache | None = None,
) -> pymupdf.Document:
    """Setzt alle Aufgaben in ein A4-Querformat-PDF.

    Jede Aufgabe ist eine Zeile (Anordnung siehe _row_layout). Die nächste
    Aufgabe beginnt darunter. Passt eine Aufgabe nicht mehr auf die Seite,
    beginnt sie auf einer neuen Seite. Ist sie höher als eine ganze Seite,
    wird sie verkleinert. Die Mittellinie läuft nur durch zweispaltige Zeilen.

    Args:
        doc_angabe: Quelldokument der Angabe.
        doc_loesung: Quelldokument der Lösung.
        pairs: Die Aufgaben in Reihenfolge.
        opt: Layout-Einstellungen.
        cache: Optionaler Cache für gefilterte Bilder (beschleunigt die Vorschau).

    Returns:
        Das neue Dokument (leer, wenn es keine Ausschnitte gibt).
    """
    out = pymupdf.open()
    geo = _Geometry.of(opt)
    w, h, m = geo.w, geo.h, geo.m
    docs = {Side.ANGABE: doc_angabe, Side.LOESUNG: doc_loesung}

    page: pymupdf.Page | None = None
    y = m
    seg_start: float | None = None  # offener Abschnitt der Mittellinie

    def close_mid_line(end_y: float) -> None:
        nonlocal seg_start
        if (
            opt.mid_line
            and page is not None
            and seg_start is not None
            and end_y > seg_start
        ):
            page.draw_line(
                (w / 2, seg_start),
                (w / 2, end_y),
                color=opt.line_color,
                width=opt.line_width,
            )
        seg_start = None

    for pair in pairs:
        row = _row_layout(pair, opt, geo)
        if row is None:
            continue

        if page is None or (y > m and y + row.height > h - m):
            close_mid_line(h - m)
            page = out.new_page(width=w, height=h)
            y = m
            band_top = m
        else:
            band_top = y - opt.row_gap_pt / 2
            if opt.separator_line:
                page.draw_line(
                    (m, band_top),
                    (w - m, band_top),
                    color=opt.line_color,
                    width=opt.line_width * 0.8,
                )
        if not row.two_columns:
            close_mid_line(band_top)
        elif seg_start is None:
            seg_start = band_top

        for pl in row.placed:
            doc = docs[pl.side]
            if doc is not None:
                target = pymupdf.Rect(
                    pl.rect.x0, y + pl.rect.y0, pl.rect.x1, y + pl.rect.y1
                )
                _draw_piece(page, target, doc, pl.piece, pl.side, opt, cache)
        if row.divider is not None and opt.separator_line:
            x0, x1, dy = row.divider
            page.draw_line(
                (x0, y + dy),
                (x1, y + dy),
                color=opt.line_color,
                width=opt.line_width * 0.6,
                dashes="[3 3] 0",
            )

        y += row.height + opt.row_gap_pt

    close_mid_line(h - m)
    return out
