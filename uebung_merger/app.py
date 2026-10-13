"""Interaktiver Editor: Angabe links, Lösung rechts, als A4-Querformat-PDF.

Workflow:
    1. Angabe und Lösung hinzufügen: PDFs, Bilder/Handyfotos, per Drag & Drop
       aufs Fenster oder per Strg+V aus der Zwischenablage. Weitere Dateien
       werden einfach hinten angehängt; alles bisher Markierte bleibt.
    2. Bereiche markieren. Die Zuordnung zu Aufgaben passiert automatisch
       der Reihe nach: die n-te Angabe gehört zur n-ten Lösung. Egal ob du
       abwechselnd markierst (A, L, A, L) oder erst alle Angaben und dann alle
       Lösungen.
    3. In der Aufgabenliste links per Drag & Drop umordnen und neu zuordnen.
    4. Exportieren.

Zuordnung beim Markieren:
    normal       nächste Aufgabe, der auf dieser Seite noch etwas fehlt
                 (eine weitere Lösung ohne offene Aufgabe wird an die letzte
                 Aufgabe angehängt, z. B. Lösung über zwei Zettel)
    Shift        an dieselbe Aufgabe wie die vorige Markierung dieser Seite
    📌 Ziel      ist eine Aufgabe angepinnt, landet alles dort

Bedienung:
    R / P          Werkzeug "Rechteck ziehen" / "4 Klicks"
    Esc            laufende 4-Klick-Auswahl abbrechen
    Klick          auf eine Markierung: Aufgabe in der Liste auswählen
    Rechtsklick    auf eine Markierung: verschieben/löschen;
                   auf eine Seite: drehen/entfernen
    Strg+Z / Y     rückgängig / wiederholen
    Strg+V         Bild aus der Zwischenablage als Lösung hinzufügen
    Strg+S         exportieren
    Strg+Mausrad   zoomen

Anordnung pro Aufgabe:
    Rechtsklick auf die Aufgabe (oder eine ihrer Markierungen) -> "Anordnung":
    nebeneinander (Standard) oder untereinander (Angabe oben, Lösung unten,
    über die ganze Seitenbreite). Das Menü zeigt, wie viel größer die Aufgabe
    untereinander würde; deutlich lohnende Fälle markiert die Liste mit 💡.
    Untereinander wird so verkleinert, dass die Aufgabe auf eine Seite passt.

Aufgaben ohne Lösung:
    Rechtsklick auf die Aufgabe (oder ihre Angabe) -> "Keine Lösung – Angabe
    zentriert": Die Angabe steht dann allein mittig auf der Seite, ohne
    Mittellinie; neue Lösungen werden dieser Aufgabe nicht mehr zugeordnet.

Drehen:
    Jeder Ausschnitt erscheint im Ergebnis genau so, wie er markiert wurde.
    Eine quer geschriebene Lösung kann per Rechtsklick auf die Markierung
    -> "Im Ergebnis um 90° drehen" hochkant eingesetzt werden; sie wird dann
    in der Spalte größer. Es wird nie automatisch gedreht.

Scan-Filter (optional, nur Lösung):
    Macht aus Handyfotos auf Karopapier saubere Scans: Schatten und Farbstich
    weg, Karo weg, schwarze/blaue Tinte bleibt. Wird oben für alle Ausschnitte
    eingeschaltet; einzelne Ausschnitte per Rechtsklick -> Haken bei
    "Scan-Filter für diesen Ausschnitt" entfernen, um sie auszunehmen.

Start:
    python -m uebung_merger [angabe.pdf] [loesung.pdf|bild.jpg ...]

Abhängigkeiten:
    pip install "pymupdf>=1.24.3" PySide6
    pip install numpy opencv-python-headless   # optional, für den Scan-Filter
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
)

from .main_window import MainWindow
from .model import Side


def main() -> int:
    """Startet den Editor.

    Argumente (optional): erste Datei = Angabe, alle weiteren = Lösung.
    """
    app = QApplication(sys.argv)
    win = MainWindow()
    args = [Path(a) for a in sys.argv[1:]]
    if args:
        win.add_files(Side.ANGABE, args[:1])
    if len(args) > 1:
        win.add_files(Side.LOESUNG, args[1:])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
