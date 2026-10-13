"""Konstanten."""

from __future__ import annotations

from PySide6.QtGui import (
    QColor,
)

MM = 72 / 25.4  # PDF-Punkte pro Millimeter
EDIT_PAGE_WIDTH_PX = 1100  # jede Seite wird im Editor auf diese Breite gerendert
PREVIEW_PAGE_WIDTH_PX = 900
PAGE_GAP_PX = 16  # Abstand zwischen Seiten in den Ansichten
MIN_SELECTION_PX = 8  # kleinere Rechtecke gelten als Klick

COLOR_ANGABE = QColor("#3b82f6")
COLOR_LOESUNG = QColor("#10b981")

FILE_FILTER = (
    "PDF oder Bild (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.gif);;"
    "Alle Dateien (*)"
)
