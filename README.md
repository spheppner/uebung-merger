# Übung Merger

Interaktiver Editor, der Angabe (links) und Lösung (rechts) einer Übung als A4-Querformat-PDF zusammenfügt.
Gedacht für Erstellung von Übungsabgabe, die optimal während der Übung gebeamed werden kann.

## Installation

```
pip install -r requirements.txt
```

`numpy` und `opencv-python-headless` werden nur für den optionalen Scan-Filter gebraucht.

## Start

```
python -m uebung_merger [angabe.pdf] [loesung.pdf|bild.jpg ...]
```

## Benutzung

1. PDFs oder Fotos per Drag & Drop oder Strg+V hinzufügen.
2. Bereiche markieren – die n-te Angabe wird der n-ten Lösung zugeordnet.
3. Aufgaben in der Liste umordnen, bei Bedarf Anordnung ändern.
4. Mit Strg+S exportieren.

Alle Tastenkürzel und Details stehen am Anfang von [`uebung_merger/app.py`](uebung_merger/app.py).

## Struktur

```
uebung_merger/
├── app.py          Einstiegspunkt (main)
├── main_window.py  Hauptfenster
├── views.py        Seitenansichten, Markierwerkzeuge
├── task_tree.py    Aufgabenliste mit Drag & Drop
├── model.py        Markierungen, Aufgaben, Zuordnung (ohne Qt)
├── layout.py       Aufbau der Ausgabe-PDF
├── scan.py         Scan-Filter
├── documents.py    PDFs/Bilder laden, drehen
├── options.py      Layout- und Filter-Einstellungen
└── constants.py    Konstanten
```
