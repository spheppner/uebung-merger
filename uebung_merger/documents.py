"""Quelldokumente laden und bearbeiten."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .model import Clip


def load_document(
    path: Path | None = None, data: bytes | None = None, filetype: str = "png"
) -> pymupdf.Document:
    """Öffnet PDF oder Bild als bearbeitbares In-Memory-PDF ohne Seitendrehung.

    Bilder werden in PDF-Seiten umgewandelt (die EXIF-Orientierung von
    Handyfotos wird dabei berücksichtigt). Gedrehte Seiten (/Rotate) werden
    normalisiert, damit Bildschirm- und PDF-Koordinaten identisch sind.

    Args:
        path: Pfad zu PDF, JPG, PNG, TIFF, ...
        data: Alternativ Dateiinhalt im Speicher (z. B. aus der Zwischenablage).
        filetype: Dateityp für data.

    Returns:
        Das geöffnete Dokument.
    """
    raw = pymupdf.open(path) if path is not None else pymupdf.open(filetype, data)
    with raw:
        data = raw.tobytes() if raw.is_pdf else raw.convert_to_pdf()
    doc = pymupdf.open("pdf", data)
    bake_annotations(doc)
    for page in doc:
        if page.rotation:
            page.remove_rotation()
    return doc


def bake_annotations(doc: pymupdf.Document) -> None:
    """Brennt Annotationen und Formularfelder fest in den Seiteninhalt ein.

    Mit Mal-/Markier-Tools gezeichnete Striche, Textmarker, Stempel und
    angehakte Formular-Kästchen liegen nicht im Seiteninhalt, sondern als
    eigene Ebene darüber. Die Vorschau zeigt sie, beim Einbetten einer Seite
    ins Ergebnis würden sie aber fehlen. Nach dem Einbrennen sind sie normaler
    Inhalt und werden überall übernommen.
    """
    if not any(page.first_annot or page.first_widget for page in doc):
        return
    if hasattr(doc, "bake"):
        doc.bake(annots=True, widgets=True)
        return
    # Sehr alte PyMuPDF-Versionen: betroffene Seiten als Bild übernehmen
    for idx in range(len(doc)):
        page = doc[idx]
        if not (page.first_annot or page.first_widget):
            continue
        rect = page.rect
        pix = page.get_pixmap(dpi=300, annots=True)
        doc.delete_page(idx)
        new_page = doc.new_page(idx, width=rect.width, height=rect.height)
        new_page.insert_image(new_page.rect, pixmap=pix)


def rotate_page(doc: pymupdf.Document, idx: int, angle: int) -> pymupdf.Matrix:
    """Dreht eine Seite dauerhaft um ein Vielfaches von 90° im Uhrzeigersinn.

    Args:
        doc: Das (In-Memory-)Dokument.
        idx: Seitenindex.
        angle: 90, 180 oder 270.

    Returns:
        Matrix, die alte Seitenkoordinaten in die neuen umrechnet
        (für bereits gesetzte Markierungen).
    """
    page = doc[idx]
    page.set_rotation(angle % 360)
    matrix = pymupdf.Matrix(page.rotation_matrix)
    page.remove_rotation()
    return matrix


def is_raster_clip(doc: pymupdf.Document, clip: Clip) -> bool:
    """True, wenn der Ausschnitt überwiegend aus einem Bild (Foto/Scan) besteht.

    Vektorinhalte (Tablet-Notizen, digitale PDFs) werden so nicht unnötig
    in Bilder umgewandelt. Scans mit OCR-Textebene zählen als Bild.
    """
    covered = 0.0
    for info in doc[clip.page].get_image_info():
        covered += abs(pymupdf.Rect(info["bbox"]) & clip.rect)
    return covered >= 0.5 * abs(clip.rect)


def is_landscape(doc: pymupdf.Document, idx: int) -> bool:
    """True, wenn die Seite breiter als hoch ist."""
    r = doc[idx].rect
    return r.width > r.height
