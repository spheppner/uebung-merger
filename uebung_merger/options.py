"""Einstellungen für Layout und Scan-Filter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ScanFilter(Enum):
    """Bildfilter für Lösungsausschnitte aus Handyfotos."""

    OFF = "Aus"
    COLOR = "Scan (Farbe)"
    BW = "Scan (Schwarzweiß)"


@dataclass
class LayoutOptions:
    """Einstellungen für die Ausgabeseiten."""

    margin_mm: float = 5.0
    gutter_pt: float = 8.0  # Abstand jeder Spalte zur Mittellinie
    row_gap_pt: float = 14.0  # Abstand zwischen zwei Aufgaben
    piece_gap_pt: float = 6.0  # Abstand zwischen Ausschnitten derselben Spalte
    mid_line: bool = True
    separator_line: bool = True
    line_color: tuple[float, float, float] = (0.6, 0.6, 0.6)
    line_width: float = 0.5
    scan_filter: ScanFilter = ScanFilter.OFF  # nur auf die Lösung angewandt
    scan_threshold: float = 0.28  # ab dieser "Dunkelheit" zählt ein Pixel als Tinte
    scan_dpi: int = 250
