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
    python angabe_loesung_editor.py [angabe.pdf] [loesung.pdf|bild.jpg ...]

Abhängigkeiten:
    pip install "pymupdf>=1.24.3" PySide6
    pip install numpy opencv-python-headless   # optional, für den Scan-Filter
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

import pymupdf
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

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


class Side(Enum):
    """Welches Quelldokument gemeint ist."""

    ANGABE = "angabe"
    LOESUNG = "loesung"

    @property
    def label(self) -> str:
        """Anzeigename."""
        return "Angabe" if self is Side.ANGABE else "Lösung"

    @property
    def short(self) -> str:
        """Kürzel für Markierungs-Labels."""
        return "A" if self is Side.ANGABE else "L"


# ---------------------------------------------------------------------------
# Layout-Eingabe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Clip:
    """Ein markierter Ausschnitt einer Quellseite (PDF-Koordinaten)."""

    page: int
    rect: pymupdf.Rect
    rotate: bool = False  # nur auf ausdrücklichen Wunsch
    no_scan: bool = False  # Scan-Filter für diesen Ausschnitt aus


@dataclass
class Pair:
    """Eine Aufgabe für das Layout: Angabe-Ausschnitte links, Lösung rechts."""

    angabe: list[Clip] = field(default_factory=list)
    loesung: list[Clip] = field(default_factory=list)
    centered: bool = False  # keine Lösung: Angabe mittig über die Seite
    stacked: bool = False  # Angabe oben, Lösung darunter, über die ganze Breite

    def clips(self, side: Side) -> list[Clip]:
        """Gibt die Ausschnittliste der gewünschten Seite zurück."""
        return self.angabe if side is Side.ANGABE else self.loesung


# ---------------------------------------------------------------------------
# Aufgabenmodell mit Zuordnungslogik (Qt-frei, testbar)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mark:
    """Eine Markierung in einem Quelldokument."""

    id: int
    side: Side
    page: int
    rect: pymupdf.Rect
    rotate: bool = False  # im Ergebnis um 90° drehen (nur auf Wunsch)
    no_scan: bool = False  # Scan-Filter für diese Markierung ausnehmen


@dataclass
class Task:
    """Eine Aufgabe: geordnete Liste von Markierungen beider Seiten."""

    id: int
    mark_ids: list[int] = field(default_factory=list)
    solo: bool = False  # keine Lösung vorgesehen: Angabe zentriert
    stacked: bool = False  # Angabe oben, Lösung darunter


# Struktur, wie sie die Aufgabenliste nach Drag & Drop liefert:
# (Aufgaben-ID oder None für neue Aufgabe, Markierungs-IDs in Reihenfolge)
Structure = list[tuple[int | None, list[int]]]


class Project:
    """Alle Markierungen und Aufgaben samt automatischer Zuordnung."""

    def __init__(self) -> None:
        self.marks: dict[int, Mark] = {}
        self.tasks: list[Task] = []
        self.last_task: dict[Side, int | None] = {s: None for s in Side}
        self._next_id = 1

    # -- Hilfen -------------------------------------------------------------

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    def task(self, task_id: int) -> Task | None:
        """Aufgabe nach ID oder None."""
        return next((t for t in self.tasks if t.id == task_id), None)

    def task_of(self, mark_id: int) -> Task | None:
        """Aufgabe, die eine Markierung enthält."""
        return next((t for t in self.tasks if mark_id in t.mark_ids), None)

    def task_number(self, task_id: int) -> int:
        """1-basierte Position einer Aufgabe."""
        return next(i for i, t in enumerate(self.tasks, 1) if t.id == task_id)

    def marks_of(self, task: Task, side: Side) -> list[Mark]:
        """Markierungen einer Seite innerhalb einer Aufgabe, in Reihenfolge."""
        return [self.marks[i] for i in task.mark_ids if self.marks[i].side is side]

    def new_task(self, index: int | None = None) -> Task:
        """Legt eine leere Aufgabe an (standardmäßig am Ende)."""
        task = Task(self._new_id())
        self.tasks.insert(len(self.tasks) if index is None else index, task)
        return task

    # -- Markieren ----------------------------------------------------------

    def add_mark(
        self,
        side: Side,
        page: int,
        rect: pymupdf.Rect,
        *,
        continue_previous: bool = False,
        target_task: int | None = None,
    ) -> Mark:
        """Legt eine Markierung an und ordnet sie einer Aufgabe zu.

        Regeln, in dieser Reihenfolge:
            1. target_task gesetzt -> dorthin.
            2. continue_previous -> zur Aufgabe der letzten Markierung dieser Seite.
            3. Die erste Aufgabe, der auf dieser Seite noch nichts zugeordnet ist.
               So passt die n-te Angabe zur n-ten Lösung, unabhängig davon,
               in welcher Reihenfolge markiert wird.
            4. Lösung, aber keine offene Aufgabe, und die letzte Aufgabe hat eine
               Angabe -> an die letzte Aufgabe anhängen (Lösung über mehrere
               Zettel).
            5. Sonst eine neue Aufgabe.
        """
        mark = Mark(self._new_id(), side, page, pymupdf.Rect(rect))
        self.marks[mark.id] = mark

        task: Task | None = None
        if target_task is not None:
            task = self.task(target_task)
        if task is None and continue_previous and self.last_task[side] is not None:
            task = self.task(self.last_task[side])  # type: ignore[arg-type]
        if task is None:
            task = next(
                (
                    t
                    for t in self.tasks
                    if not self.marks_of(t, side)
                    and not (side is Side.LOESUNG and t.solo)
                ),
                None,
            )
        if (
            task is None
            and side is Side.LOESUNG
            and self.tasks
            and self.marks_of(self.tasks[-1], Side.ANGABE)
            and not self.tasks[-1].solo
        ):
            task = self.tasks[-1]
        if task is None:
            task = self.new_task()

        task.mark_ids.append(mark.id)
        self.last_task[side] = task.id
        self._normalize()
        return mark

    # -- Bearbeiten ---------------------------------------------------------

    def delete_mark(self, mark_id: int) -> None:
        """Entfernt eine Markierung; leer gewordene Aufgaben verschwinden."""
        task = self.task_of(mark_id)
        self.marks.pop(mark_id, None)
        if task is not None:
            task.mark_ids.remove(mark_id)
            if not task.mark_ids:
                self.tasks.remove(task)
        self._fix_last_task()

    def delete_task(self, task_id: int) -> None:
        """Entfernt eine Aufgabe samt ihren Markierungen."""
        task = self.task(task_id)
        if task is None:
            return
        for mid in task.mark_ids:
            self.marks.pop(mid, None)
        self.tasks.remove(task)
        self._fix_last_task()

    def move_mark(self, mark_id: int, task_id: int | None) -> None:
        """Verschiebt eine Markierung in eine Aufgabe (None = neue Aufgabe dahinter)."""
        source = self.task_of(mark_id)
        if source is None:
            return
        if task_id is None:
            target = self.new_task(self.tasks.index(source) + 1)
        else:
            found = self.task(task_id)
            if found is None or found is source:
                return
            target = found
        source.mark_ids.remove(mark_id)
        target.mark_ids.append(mark_id)
        if not source.mark_ids:
            self.tasks.remove(source)
        self._fix_last_task()

    def set_no_scan(self, mark_id: int, value: bool) -> None:
        """Nimmt eine Markierung vom globalen Scan-Filter aus (oder wieder auf)."""
        if mark_id in self.marks:
            self.marks[mark_id] = replace(self.marks[mark_id], no_scan=value)

    def set_solo(self, task_id: int, value: bool) -> bool:
        """Markiert eine Aufgabe als "keine Lösung, Angabe zentriert".

        Returns:
            False, wenn das nicht geht (Aufgabe hat bereits eine Lösung).
        """
        task = self.task(task_id)
        if task is None or (value and self.marks_of(task, Side.LOESUNG)):
            return False
        task.solo = value
        return True

    def set_stacked(self, task_id: int, value: bool) -> None:
        """Angabe und Lösung untereinander (True) oder nebeneinander setzen."""
        task = self.task(task_id)
        if task is not None:
            task.stacked = value

    def set_rotation(self, mark_id: int, value: bool) -> None:
        """Ob die Markierung im Ergebnis um 90° gedreht eingesetzt wird."""
        if mark_id in self.marks:
            self.marks[mark_id] = replace(self.marks[mark_id], rotate=value)

    def move_mark_to(
        self, mark_id: int, task_id: int | None, index: int | None = None
    ) -> None:
        """Markierung in Aufgabe task_id, oder als neue Aufgabe an Position index."""
        source = self.task_of(mark_id)
        if source is None:
            return
        if task_id is not None:
            self.move_mark(mark_id, task_id)
            return
        if source.mark_ids == [mark_id]:  # war allein -> nur umsortieren
            self.reorder_task(source.id, len(self.tasks) if index is None else index)
            return
        target = self.new_task(len(self.tasks) if index is None else index)
        source.mark_ids.remove(mark_id)
        target.mark_ids.append(mark_id)
        self._fix_last_task()

    def merge_task(self, src_id: int, dst_id: int) -> None:
        """Hängt alle Markierungen von src an dst an und entfernt src."""
        src, dst = self.task(src_id), self.task(dst_id)
        if src is None or dst is None or src is dst:
            return
        dst.mark_ids += src.mark_ids
        self.tasks.remove(src)
        self._fix_last_task()

    def reorder_task(self, task_id: int, index: int) -> None:
        """Setzt eine Aufgabe vor die Aufgabe, die bisher an Position index stand."""
        task = self.task(task_id)
        if task is None:
            return
        old = self.tasks.index(task)
        self.tasks.pop(old)
        if index > old:
            index -= 1
        self.tasks.insert(max(0, min(len(self.tasks), index)), task)

    def move_task(self, task_id: int, delta: int) -> None:
        """Verschiebt eine Aufgabe in der Reihenfolge."""
        task = self.task(task_id)
        if task is None:
            return
        i = self.tasks.index(task)
        j = max(0, min(len(self.tasks) - 1, i + delta))
        self.tasks.insert(j, self.tasks.pop(i))

    def set_structure(self, structure: Structure) -> None:
        """Übernimmt Reihenfolge und Zuordnung aus der Aufgabenliste."""
        new_tasks: list[Task] = []
        used_task_ids: set[int] = set()
        seen_marks: set[int] = set()
        for task_id, mark_ids in structure:
            ids = [m for m in mark_ids if m in self.marks and m not in seen_marks]
            seen_marks.update(ids)
            if task_id is not None and task_id not in used_task_ids:
                used_task_ids.add(task_id)
                old = self.task(task_id)
                new_tasks.append(
                    Task(
                        task_id,
                        ids,
                        old.solo if old else False,
                        old.stacked if old else False,
                    )
                )
            elif ids:
                new_tasks.append(Task(self._new_id(), ids))
        # Markierungen, die beim Umbau verloren gingen, nicht stillschweigend löschen
        for mid in self.marks:
            if mid not in seen_marks:
                new_tasks.append(Task(self._new_id(), [mid]))
        self.tasks = new_tasks
        self._fix_last_task()

    def _normalize(self) -> None:
        """Bekommt eine Solo-Aufgabe doch eine Lösung, ist sie keine mehr."""
        for t in self.tasks:
            if t.solo and self.marks_of(t, Side.LOESUNG):
                t.solo = False

    def _fix_last_task(self) -> None:
        self._normalize()
        for side in Side:
            if self.last_task[side] is not None and self.task(self.last_task[side]) is None:  # type: ignore[arg-type]
                self.last_task[side] = None

    # -- Seitenänderungen ---------------------------------------------------

    def transform_page(self, side: Side, page: int, matrix: pymupdf.Matrix) -> None:
        """Rechnet Markierungen nach einer Seitendrehung um."""
        for mid, m in list(self.marks.items()):
            if m.side is side and m.page == page:
                self.marks[mid] = replace(m, rect=(m.rect * matrix).normalize())

    def remove_page(self, side: Side, page: int) -> None:
        """Löscht Markierungen einer entfernten Seite und verschiebt spätere."""
        for mid, m in list(self.marks.items()):
            if m.side is not side:
                continue
            if m.page == page:
                self.delete_mark(mid)
            elif m.page > page:
                self.marks[mid] = replace(m, page=m.page - 1)

    # -- Ausgabe / Undo -----------------------------------------------------

    def pairs(self) -> list[Pair]:
        """Aufgaben als Layout-Eingabe."""
        return [
            Pair(
                [
                    Clip(m.page, m.rect, m.rotate, m.no_scan)
                    for m in self.marks_of(t, Side.ANGABE)
                ],
                [
                    Clip(m.page, m.rect, m.rotate, m.no_scan)
                    for m in self.marks_of(t, Side.LOESUNG)
                ],
                centered=t.solo,
                stacked=t.stacked,
            )
            for t in self.tasks
        ]

    def snapshot(self) -> Project:
        """Tiefe Kopie für Rückgängig/Wiederholen."""
        return copy.deepcopy(self)


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


# ---------------------------------------------------------------------------
# Scan-Filter (benötigt numpy + opencv-python-headless)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


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


def pixmap_to_qpixmap(pix: pymupdf.Pixmap) -> QPixmap:
    """Wandelt eine PyMuPDF-Pixmap (RGB, ohne Alpha) in eine QPixmap um."""
    img = QImage(
        pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
    )
    return QPixmap.fromImage(img.copy())


# ---------------------------------------------------------------------------
# Ansichten
# ---------------------------------------------------------------------------


class Tool(Enum):
    """Auswahlwerkzeug."""

    RECT = "rect"
    FOUR_CLICK = "four_click"


@dataclass(frozen=True)
class Overlay:
    """Eine anzuzeigende Markierung."""

    mark_id: int
    page: int
    rect: pymupdf.Rect
    label: str
    highlighted: bool


class PagesView(QGraphicsView):
    """Zeigt alle Seiten eines Dokuments untereinander, jede gleich breit."""

    files_dropped = Signal(list)  # list[Path]

    def __init__(self, page_width_px: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.page_width_px = page_width_px
        self.page_items: list[QGraphicsPixmapItem] = []
        self.page_zoom: list[float] = []
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#2b2b2b"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self._auto_fit = True
        self._placeholder = ""

    def set_placeholder(self, text: str) -> None:
        """Hinweistext, solange kein Dokument geladen ist."""
        self._placeholder = text
        if not self.page_items:
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.scene().clear()
        if self._placeholder:
            item = self.scene().addSimpleText(self._placeholder)
            item.setBrush(QBrush(QColor("#bbbbbb")))
            font = QFont()
            font.setPointSize(11)
            item.setFont(font)
            self.resetTransform()
            self.scene().setSceneRect(
                item.sceneBoundingRect().adjusted(-20, -20, 20, 20)
            )

    def show_document(
        self, doc: pymupdf.Document | None, keep_scroll: bool = True
    ) -> None:
        """Rendert alle Seiten des Dokuments neu."""
        old_scroll = self.verticalScrollBar().value()
        self.scene().clear()
        self.page_items.clear()
        self.page_zoom.clear()
        if doc is None or len(doc) == 0:
            self._show_placeholder()
            return
        y = 0.0
        for page in doc:
            zoom = self.page_width_px / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            item = self.scene().addPixmap(pixmap_to_qpixmap(pix))
            item.setPos(0, y)
            self.page_items.append(item)
            self.page_zoom.append(zoom)
            y += pix.height + PAGE_GAP_PX
        self.scene().setSceneRect(QRectF(-20, -20, self.page_width_px + 40, y + 20))
        if self._auto_fit:
            self.fit_width()
        if keep_scroll:
            self.verticalScrollBar().setValue(old_scroll)

    def fit_width(self) -> None:
        """Skaliert die Ansicht auf Seitenbreite."""
        if not self.page_items:
            return
        factor = max(0.05, (self.viewport().width() - 4) / (self.page_width_px + 40))
        self.resetTransform()
        self.scale(factor, factor)

    def scroll_to_page(self, idx: int) -> None:
        """Scrollt so, dass der Seitenanfang oben steht."""
        if 0 <= idx < len(self.page_items):
            top = self.mapFromScene(self.page_items[idx].pos()).y()
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + top - 10)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit_width()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._auto_fit = False
            factor = 1.15 ** (event.angleDelta().y() / 120)
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    # -- Dateien hineinziehen ---------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = [
            Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()
        ]
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)

    # -- Koordinaten --------------------------------------------------------

    def page_at(self, scene_pt: QPointF) -> int | None:
        """Index der Seite unter dem Szenenpunkt oder None."""
        for i, item in enumerate(self.page_items):
            if item.sceneBoundingRect().contains(scene_pt):
                return i
        return None

    def clamp_to_page(self, idx: int, pt: QPointF) -> QPointF:
        """Begrenzt einen Szenenpunkt auf die Fläche der Seite idx."""
        r = self.page_items[idx].sceneBoundingRect()
        return QPointF(
            min(max(pt.x(), r.left()), r.right()),
            min(max(pt.y(), r.top()), r.bottom()),
        )

    def scene_rect_to_pdf(self, idx: int, rect: QRectF) -> pymupdf.Rect:
        """Rechnet ein Szenenrechteck in PDF-Koordinaten der Seite idx um."""
        origin = self.page_items[idx].pos()
        z = self.page_zoom[idx]
        return pymupdf.Rect(
            (rect.left() - origin.x()) / z,
            (rect.top() - origin.y()) / z,
            (rect.right() - origin.x()) / z,
            (rect.bottom() - origin.y()) / z,
        )

    def pdf_rect_to_scene(self, idx: int, rect: pymupdf.Rect) -> QRectF:
        """Rechnet ein PDF-Rechteck der Seite idx in Szenenkoordinaten um."""
        origin = self.page_items[idx].pos()
        z = self.page_zoom[idx]
        return QRectF(
            origin.x() + rect.x0 * z,
            origin.y() + rect.y0 * z,
            rect.width * z,
            rect.height * z,
        )


class SelectView(PagesView):
    """Dokumentansicht mit Rechteck- und 4-Klick-Auswahl und Markierungs-Overlays."""

    selection_made = Signal(int, object)  # Seitenindex, pymupdf.Rect
    mark_clicked = Signal(int)  # Markierungs-ID
    mark_menu_requested = Signal(int, QPoint)  # Markierungs-ID, globale Position
    page_action = Signal(
        int, str
    )  # Seitenindex, "rot90" | "rot270" | "rot180" | "delete"

    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(EDIT_PAGE_WIDTH_PX, parent)
        self.color = color
        self.tool = Tool.RECT
        self._drag_page: int | None = None
        self._drag_start: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None
        self._clicks: list[QPointF] = []
        self._click_page: int | None = None
        self._click_items: list[QGraphicsItem] = []
        self._overlay_items: list[QGraphicsItem] = []
        self._overlays: list[Overlay] = []
        self._pen = QPen(color, 2)
        self._pen.setCosmetic(True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # -- Overlays ------------------------------------------------------------

    def set_overlays(self, overlays: list[Overlay]) -> None:
        """Zeichnet die bestehenden Markierungen neu."""
        for it in self._overlay_items:
            if it.scene() is not None:
                self.scene().removeItem(it)
        self._overlay_items.clear()
        self._overlays = [o for o in overlays if o.page < len(self.page_items)]
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        for o in self._overlays:
            fill = QColor(self.color)
            fill.setAlpha(70 if o.highlighted else 35)
            pen = QPen(self.color, 4 if o.highlighted else 2)
            pen.setCosmetic(True)
            if not o.highlighted:
                pen.setStyle(Qt.PenStyle.SolidLine)
            srect = self.pdf_rect_to_scene(o.page, o.rect)
            box = self.scene().addRect(srect, pen, QBrush(fill))
            # Label als Fähnchen in der linken oberen Ecke, immer gleich groß
            tag = QGraphicsRectItem()
            tag.setBrush(
                QBrush(self.color.darker(130) if o.highlighted else self.color)
            )
            tag.setPen(QPen(Qt.PenStyle.NoPen))
            tag.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            text = QGraphicsSimpleTextItem(o.label, tag)
            text.setFont(font)
            text.setBrush(QBrush(QColor("white")))
            tb = text.boundingRect()
            tag.setRect(0, 0, tb.width() + 10, tb.height() + 2)
            text.setPos(5, 1)
            tag.setPos(srect.topLeft())
            self.scene().addItem(tag)
            self._overlay_items += [box, tag]

    def mark_at(self, scene_pt: QPointF) -> int | None:
        """Kleinste Markierung unter dem Punkt (bei Überlappung die innerste)."""
        hits = [
            (abs(o.rect), o.mark_id)
            for o in self._overlays
            if self.pdf_rect_to_scene(o.page, o.rect).contains(scene_pt)
        ]
        return min(hits)[1] if hits else None

    def ensure_mark_visible(self, page: int, rect: pymupdf.Rect) -> None:
        """Scrollt zu einer Markierung."""
        if page < len(self.page_items):
            self.ensureVisible(self.pdf_rect_to_scene(page, rect), 20, 40)

    def show_document(
        self, doc: pymupdf.Document | None, keep_scroll: bool = True
    ) -> None:
        """Rendert neu und verwirft laufende Auswahlen."""
        self.cancel_pending()
        self._overlay_items.clear()
        self._overlays.clear()
        super().show_document(doc, keep_scroll)

    # -- Auswahl -------------------------------------------------------------

    def set_tool(self, tool: Tool) -> None:
        """Wechselt das Auswahlwerkzeug."""
        self.cancel_pending()
        self.tool = tool

    def cancel_pending(self) -> None:
        """Bricht eine begonnene Auswahl ab."""
        for it in self._click_items:
            if it.scene() is not None:
                self.scene().removeItem(it)
        self._click_items.clear()
        self._clicks.clear()
        self._click_page = None
        if self._rubber is not None and self._rubber.scene() is not None:
            self.scene().removeItem(self._rubber)
        self._rubber = None
        self._drag_start = None
        self._drag_page = None

    def _is_big_enough(self, srect: QRectF) -> bool:
        on_screen = self.mapFromScene(srect.normalized()).boundingRect()
        return (
            on_screen.width() >= MIN_SELECTION_PX
            and on_screen.height() >= MIN_SELECTION_PX
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.page_items:
            super().mousePressEvent(event)
            return
        pt = self.mapToScene(event.position().toPoint())

        if self.tool is Tool.RECT:
            idx = self.page_at(pt)
            if idx is None:
                return
            self._drag_page = idx
            self._drag_start = pt
            self._rubber = self.scene().addRect(QRectF(pt, pt), self._pen)
            return

        # 4-Klick-Modus
        if self._click_page is None:
            idx = self.page_at(pt)
            if idx is None:
                return
            self._click_page = idx
        pt = self.clamp_to_page(self._click_page, pt)
        self._clicks.append(pt)
        dot = QGraphicsEllipseItem(-5, -5, 10, 10)
        dot.setPos(pt)
        dot.setBrush(QBrush(self.color))
        dot.setPen(QPen(Qt.PenStyle.NoPen))
        dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.scene().addItem(dot)
        self._click_items.append(dot)
        if len(self._clicks) >= 2:
            a, b = self._clicks[-2], self._clicks[-1]
            self._click_items.append(
                self.scene().addLine(a.x(), a.y(), b.x(), b.y(), self._pen)
            )
        if len(self._clicks) == 4:
            xs = [p.x() for p in self._clicks]
            ys = [p.y() for p in self._clicks]
            idx = self._click_page
            rect = QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys)))
            self.cancel_pending()
            if self._is_big_enough(rect):
                self.selection_made.emit(idx, self.scene_rect_to_pdf(idx, rect))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._rubber is not None
            and self._drag_start is not None
            and self._drag_page is not None
        ):
            pt = self.clamp_to_page(
                self._drag_page, self.mapToScene(event.position().toPoint())
            )
            self._rubber.setRect(QRectF(self._drag_start, pt).normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._rubber is not None
            and self._drag_page is not None
            and self._drag_start is not None
        ):
            rect = self._rubber.rect()
            idx = self._drag_page
            start = self._drag_start
            self.cancel_pending()
            if self._is_big_enough(rect):
                self.selection_made.emit(idx, self.scene_rect_to_pdf(idx, rect))
            else:
                hit = self.mark_at(start)
                if hit is not None:
                    self.mark_clicked.emit(hit)
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        pt = self.mapToScene(event.pos())
        hit = self.mark_at(pt)
        if hit is not None:
            self.mark_menu_requested.emit(hit, event.globalPos())
            return
        idx = self.page_at(pt)
        if idx is None:
            return
        menu = QMenu(self)
        actions = {
            menu.addAction(f"Seite {idx + 1} um 90° im Uhrzeigersinn drehen"): "rot90",
            menu.addAction(
                f"Seite {idx + 1} um 90° gegen den Uhrzeigersinn drehen"
            ): "rot270",
            menu.addAction(f"Seite {idx + 1} um 180° drehen"): "rot180",
        }
        menu.addSeparator()
        actions[menu.addAction(f"Seite {idx + 1} entfernen")] = "delete"
        chosen = menu.exec(event.globalPos())
        if chosen in actions:
            self.cancel_pending()
            self.page_action.emit(idx, actions[chosen])

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_pending()
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Aufgabenliste
# ---------------------------------------------------------------------------

ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_ID = Qt.ItemDataRole.UserRole + 1


class TaskTree(QTreeWidget):
    """Aufgaben als Baum; Markierungen lassen sich per Drag & Drop umhängen.

    Markierung auf Aufgabe (oder deren Markierung) ziehen -> neu zuordnen
    Markierung in eine Lücke zwischen Aufgaben           -> eigene neue Aufgabe
    Aufgabe auf Aufgabe ziehen                           -> zusammenführen
    Aufgabe in eine Lücke zwischen Aufgaben              -> Reihenfolge ändern
    """

    # (gezogene Art "task"/"mark", ID, Ziel-Aufgabe oder None, Einfügeposition oder None)
    drop_requested = Signal(str, int, object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setIndentation(16)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._collapsed: set[int] = set()
        self.itemCollapsed.connect(lambda it: self._remember(it, True))
        self.itemExpanded.connect(lambda it: self._remember(it, False))

    _MIME = "application/x-qabstractitemmodeldatalist"

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasFormat(self._MIME):
            super().dragEnterEvent(event)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.mimeData().hasFormat(self._MIME):
            super().dragMoveEvent(event)  # zeichnet den Einfügeindikator
            # CopyAction, damit Qt nach dem Drop nichts selbst entfernt;
            # das Modell wird über drop_requested geändert und neu aufgebaut.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def drop_target(self, pos: QPoint) -> tuple[int | None, int | None]:
        """(Ziel-Aufgabe, Einfügeposition) für eine Drop-Position."""
        item = self.itemAt(pos)
        if item is None:
            return None, self.topLevelItemCount()
        top = item if item.parent() is None else item.parent()
        top_index = self.indexOfTopLevelItem(top)
        if item.parent() is not None:
            return int(top.data(0, ROLE_ID)), None
        rect = self.visualItemRect(item)
        rel = (pos.y() - rect.top()) / max(1, rect.height())
        if rel < 0.25:
            return None, top_index
        if rel > 0.75 and (not item.isExpanded() or item.childCount() == 0):
            return None, top_index + 1
        return int(item.data(0, ROLE_ID)), None

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        dragged = self.currentItem()
        if dragged is None or not event.mimeData().hasFormat(self._MIME):
            event.ignore()
            return
        task_id, index = self.drop_target(event.position().toPoint())
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        kind = str(dragged.data(0, ROLE_KIND))
        ident = int(dragged.data(0, ROLE_ID))
        # Erst nach Abschluss des Drags umbauen, Qt hält noch Referenzen
        QTimer.singleShot(
            0, lambda: self.drop_requested.emit(kind, ident, task_id, index)
        )

    def _remember(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        if item.data(0, ROLE_KIND) == "task":
            tid = int(item.data(0, ROLE_ID))
            (self._collapsed.add if collapsed else self._collapsed.discard)(tid)

    def populate(
        self,
        project: Project,
        describe: dict[int, str],
        selected_task: int | None,
        selected_mark: int | None,
        pinned_task: int | None,
    ) -> None:
        """Baut den Baum aus dem Projekt neu auf."""
        self.blockSignals(True)
        self.clear()
        bold = QFont()
        bold.setBold(True)
        current: QTreeWidgetItem | None = None
        for n, task in enumerate(project.tasks, 1):
            has_a = bool(project.marks_of(task, Side.ANGABE))
            has_l = bool(project.marks_of(task, Side.LOESUNG))
            title = f"Aufgabe {n}"
            if task.id == pinned_task:
                title = "📌 " + title
            if task.stacked and not task.solo:
                title += "   ⇵ untereinander"
            if task.solo:
                title += "   ◎ ohne Lösung, zentriert"
            elif not task.mark_ids:
                title += "   (leer)"
            elif not has_l:
                title += "   ⚠ Lösung fehlt"
            elif not has_a:
                title += "   ⚠ Angabe fehlt"
            item = QTreeWidgetItem([title])
            item.setFont(0, bold)
            if has_a and not has_l and not task.solo:
                item.setToolTip(
                    0,
                    "Kommt hier keine Lösung mehr? Rechtsklick → "
                    "„Keine Lösung – Angabe zentriert“",
                )
            item.setData(0, ROLE_KIND, "task")
            item.setData(0, ROLE_ID, task.id)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
            )
            self.addTopLevelItem(item)
            if task.id == selected_task and selected_mark is None:
                current = item
            for mid in task.mark_ids:
                mark = project.marks[mid]
                child = QTreeWidgetItem([describe.get(mid, "?")])
                child.setForeground(
                    0,
                    QBrush(COLOR_ANGABE if mark.side is Side.ANGABE else COLOR_LOESUNG),
                )
                child.setData(0, ROLE_KIND, "mark")
                child.setData(0, ROLE_ID, mid)
                child.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsDragEnabled
                )
                item.addChild(child)
                if mid == selected_mark:
                    current = child
            item.setExpanded(task.id not in self._collapsed)
        if current is not None:
            self.setCurrentItem(current)
            self.scrollToItem(current)
        self.blockSignals(False)

    def current_ids(self) -> tuple[int | None, int | None]:
        """(Aufgaben-ID, Markierungs-ID) des ausgewählten Eintrags."""
        item = self.currentItem()
        if item is None:
            return None, None
        if item.data(0, ROLE_KIND) == "task":
            return int(item.data(0, ROLE_ID)), None
        parent = item.parent()
        task_id = int(parent.data(0, ROLE_ID)) if parent is not None else None
        return task_id, int(item.data(0, ROLE_ID))


# ---------------------------------------------------------------------------
# Hauptfenster
# ---------------------------------------------------------------------------


def _gain_text(gain: float) -> str:
    """Menschlich lesbarer Größenvergleich für die Anordnung."""
    pct = round((gain - 1) * 100)
    if gain >= 1.15:
        return f"Lösung ca. {gain:.1f}".replace(".", ",").replace("ca,", "ca.") + "× so groß"
    if gain > 0.95:
        return "etwa gleich groß"
    return f"wäre {abs(pct)} % kleiner"


def _titled(title: str, widget: QWidget) -> tuple[QWidget, QLabel]:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    label = QLabel(title)
    label.setStyleSheet("font-weight: bold; padding: 2px;")
    lay.addWidget(label)
    lay.addWidget(widget)
    return box, label


class MainWindow(QMainWindow):
    """Editorfenster: Aufgaben, Angabe, Lösung und Live-Vorschau."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Angabe | Lösung – Merger")
        self.resize(1700, 950)

        self.docs: dict[Side, pymupdf.Document | None] = {s: None for s in Side}
        self.page_names: dict[Side, list[str]] = {s: [] for s in Side}
        self.first_path: dict[Side, Path | None] = {s: None for s in Side}
        self.project = Project()
        self.undo_stack: list[Project] = []
        self.redo_stack: list[Project] = []
        self.selected_task: int | None = None
        self.selected_mark: int | None = None
        self.pinned_task: int | None = None
        self.options = LayoutOptions()
        self.filter_cache: FilterCache = {}

        # Ansichten
        self.views = {
            Side.ANGABE: SelectView(COLOR_ANGABE),
            Side.LOESUNG: SelectView(COLOR_LOESUNG),
        }
        self.views[Side.ANGABE].set_placeholder(
            "Angabe hinzufügen …\n(PDF oder Bild hierher ziehen)"
        )
        self.views[Side.LOESUNG].set_placeholder(
            "Lösung hinzufügen …\n(PDFs/Fotos hierher ziehen oder Strg+V)"
        )
        self.preview = PagesView(PREVIEW_PAGE_WIDTH_PX)
        for side, view in self.views.items():
            view.selection_made.connect(
                lambda idx, rect, s=side: self.on_selection(s, idx, rect)
            )
            view.mark_clicked.connect(self.select_mark)
            view.mark_menu_requested.connect(self.show_mark_menu)
            view.page_action.connect(
                lambda idx, action, s=side: self.on_page_action(s, idx, action)
            )
            view.files_dropped.connect(lambda paths, s=side: self.add_files(s, paths))

        # Aufgabenliste
        self.tree = TaskTree()
        self.tree.drop_requested.connect(self.on_tree_drop)
        self.tree.currentItemChanged.connect(self.on_tree_selection)
        self.tree.customContextMenuRequested.connect(self.show_tree_menu)
        task_panel = QWidget()
        tp = QVBoxLayout(task_panel)
        tp.setContentsMargins(4, 4, 4, 4)
        head = QLabel("Aufgaben")
        head.setStyleSheet("font-weight: bold; padding: 2px;")
        tp.addWidget(head)
        tp.addWidget(self.tree)
        row = QHBoxLayout()
        for text, tip, slot in (
            ("＋", "Neue leere Aufgabe", self.add_empty_task),
            ("▲", "Nach oben", lambda: self.move_selected_task(-1)),
            ("▼", "Nach unten", lambda: self.move_selected_task(1)),
            ("🗑", "Ausgewählte Markierung/Aufgabe löschen", self.delete_selected),
        ):
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        tp.addLayout(row)
        self.btn_pin = QPushButton("📌 Neue Markierungen → ausgewählte Aufgabe")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setToolTip(
            "Solange aktiv, landen alle neuen Markierungen in der angepinnten\n"
            "Aufgabe statt automatisch zugeordnet zu werden."
        )
        self.btn_pin.toggled.connect(self.on_pin_toggled)
        tp.addWidget(self.btn_pin)
        hint = QLabel(
            "<small>Ziehen: Markierung auf Aufgabe = neu zuordnen, zwischen "
            "Aufgaben = eigene Aufgabe, Aufgabe auf Aufgabe = zusammenführen."
            "<br>Beim Markieren <b>Shift</b> halten = an vorige Aufgabe dieser "
            "Seite anhängen.</small>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        tp.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(task_panel)
        self.titles: dict[Side, QLabel] = {}
        for side in Side:
            box, label = _titled(side.label, self.views[side])
            self.titles[side] = label
            splitter.addWidget(box)
        preview_box, _ = _titled("Vorschau (A4 quer)", self.preview)
        splitter.addWidget(preview_box)
        splitter.setSizes([280, 480, 480, 460])
        self.setCentralWidget(splitter)

        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self.update_preview)

        self._build_toolbar()
        self.refresh_ui()

    # -- Toolbar -------------------------------------------------------------

    def _action(
        self, text: str, slot: object, shortcut: QKeySequence | str | None = None
    ) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(lambda _=False: slot())  # type: ignore[operator]
        return act

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Werkzeuge")
        tb.setMovable(False)
        tb.addAction(self._action("Neu", self.new_project))
        tb.addAction(self._action("＋ Angabe…", lambda: self.add_files(Side.ANGABE)))
        tb.addAction(self._action("＋ Lösung…", lambda: self.add_files(Side.LOESUNG)))
        tb.addAction(
            self._action(
                "📋 Einfügen", self.paste_clipboard, QKeySequence.StandardKey.Paste
            )
        )
        tb.addSeparator()

        group = QActionGroup(self)
        for tool, text, key in (
            (Tool.RECT, "▭ Rechteck ziehen", "R"),
            (Tool.FOUR_CLICK, "⁘ 4 Klicks", "P"),
        ):
            act = QAction(text, self, checkable=True)
            act.setShortcut(QKeySequence(key))
            act.setChecked(tool is Tool.RECT)
            act.triggered.connect(lambda _=False, t=tool: self.set_tool(t))
            group.addAction(act)
            tb.addAction(act)
        tb.addSeparator()
        tb.addAction(self._action("↶", self.undo, QKeySequence.StandardKey.Undo))
        tb.addAction(self._action("↷", self.redo, "Ctrl+Y"))
        tb.addSeparator()

        self.cb_mid = QCheckBox("Mittellinie")
        self.cb_mid.setChecked(self.options.mid_line)
        self.cb_mid.toggled.connect(self._options_changed)
        tb.addWidget(self.cb_mid)
        self.cb_sep = QCheckBox("Trennlinie zw. Aufgaben")
        self.cb_sep.setChecked(self.options.separator_line)
        self.cb_sep.toggled.connect(self._options_changed)
        tb.addWidget(self.cb_sep)
        tb.addWidget(QLabel("  Rand:"))
        self.sb_margin = QDoubleSpinBox()
        self.sb_margin.setRange(0, 30)
        self.sb_margin.setSingleStep(1)
        self.sb_margin.setSuffix(" mm")
        self.sb_margin.setValue(self.options.margin_mm)
        self.sb_margin.valueChanged.connect(self._options_changed)
        tb.addWidget(self.sb_margin)
        tb.addSeparator()
        tb.addAction(
            self._action("💾 Exportieren…", self.export, QKeySequence.StandardKey.Save)
        )

        self.addToolBarBreak()
        tb2 = self.addToolBar("Ausgabe")
        tb2.setMovable(False)
        tb2.addWidget(QLabel(" Scan-Filter (Lösung): "))
        self.cmb_filter = QComboBox()
        for f in ScanFilter:
            self.cmb_filter.addItem(f.value, f)
        self.cmb_filter.currentIndexChanged.connect(self._options_changed)
        tb2.addWidget(self.cmb_filter)
        tb2.addWidget(QLabel("  Mindestschwelle:"))
        self.sb_thresh = QDoubleSpinBox()
        self.sb_thresh.setRange(0.10, 0.70)
        self.sb_thresh.setSingleStep(0.02)
        self.sb_thresh.setValue(self.options.scan_threshold)
        self.sb_thresh.setToolTip(
            "Höher = mehr Hintergrund/Karo wird entfernt, aber dünne oder\n"
            "helle Striche können verschwinden. Das Karo wird zusätzlich\n"
            "automatisch erkannt."
        )
        self.sb_thresh.valueChanged.connect(self._options_changed)
        tb2.addWidget(self.sb_thresh)
        if not HAVE_CV:
            self.cmb_filter.setEnabled(False)
            self.cmb_filter.setToolTip(
                "Benötigt: pip install numpy opencv-python-headless"
            )
        self.sb_thresh.setEnabled(False)

    def _options_changed(self) -> None:
        self.options.mid_line = self.cb_mid.isChecked()
        self.options.separator_line = self.cb_sep.isChecked()
        self.options.margin_mm = self.sb_margin.value()
        self.options.scan_filter = self.cmb_filter.currentData()
        self.options.scan_threshold = self.sb_thresh.value()
        self.sb_thresh.setEnabled(self.options.scan_filter is not ScanFilter.OFF)
        self.refresh_ui()

    def set_tool(self, tool: Tool) -> None:
        """Setzt das Auswahlwerkzeug in beiden Ansichten."""
        for view in self.views.values():
            view.set_tool(tool)

    # -- Dateien -------------------------------------------------------------

    def new_project(self) -> None:
        """Alles schließen und von vorne beginnen."""
        if self.project.marks and (
            QMessageBox.question(
                self, "Neu", "Alle Dokumente und Markierungen verwerfen?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        for side in Side:
            if self.docs[side] is not None:
                self.docs[side].close()  # type: ignore[union-attr]
            self.docs[side] = None
            self.page_names[side] = []
            self.first_path[side] = None
            self.views[side].show_document(None)
            self.titles[side].setText(side.label)
        self.project = Project()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selected_task = self.selected_mark = self.pinned_task = None
        self.btn_pin.setChecked(False)
        self.filter_cache.clear()
        self.refresh_ui()

    def add_files(self, side: Side, paths: list[Path] | None = None) -> None:
        """Hängt Dateien an Angabe oder Lösung an; Bisheriges bleibt erhalten."""
        if paths is None:
            names, _ = QFileDialog.getOpenFileNames(
                self, f"{side.label} hinzufügen", str(self._last_dir()), FILE_FILTER
            )
            paths = [Path(n) for n in names]
        for path in paths:
            try:
                doc = load_document(path)
            except Exception as exc:  # noqa: BLE001 – Fehler dem Nutzer zeigen
                QMessageBox.critical(self, "Fehler beim Öffnen", f"{path}\n\n{exc}")
                continue
            if self.first_path[side] is None:
                self.first_path[side] = path
            self._append_doc(side, doc, path.name)

    def paste_clipboard(self) -> None:
        """Fügt ein Bild (oder kopierte Dateien) aus der Zwischenablage ein.

        Ziel ist die Angabe, wenn deren Ansicht den Fokus hat, sonst die Lösung.
        """
        side = Side.ANGABE if self.views[Side.ANGABE].hasFocus() else Side.LOESUNG
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime.hasImage():
            image = clipboard.image()
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            buffer.close()
            doc = load_document(data=bytes(data.data()), filetype="png")
            self._append_doc(side, doc, "Zwischenablage")
        elif mime.hasUrls():
            self.add_files(
                side, [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
            )
        else:
            self.status.setText("⚠ Die Zwischenablage enthält kein Bild.")

    def _append_doc(self, side: Side, new: pymupdf.Document, name: str) -> None:
        first_new = 0
        if self.docs[side] is None:
            self.docs[side] = new
        else:
            target = self.docs[side]
            assert target is not None
            first_new = len(target)
            target.insert_pdf(new)
            new.close()
        doc = self.docs[side]
        assert doc is not None
        added = len(doc) - first_new
        self.page_names[side] += (
            [name] if added == 1 else [f"{name} S.{i}" for i in range(1, added + 1)]
        )
        self.views[side].show_document(doc)
        self.views[side].scroll_to_page(first_new)
        self.refresh_ui()

    def _update_titles(self) -> None:
        for side in Side:
            names = self.page_names[side]
            if not names:
                self.titles[side].setText(side.label)
                continue
            files = len({n.rsplit(" S.", 1)[0] for n in names})
            self.titles[side].setText(
                f"{side.label}: {len(names)} Seite(n) aus {files} Datei(en)"
            )

    def _last_dir(self) -> Path:
        for p in self.first_path.values():
            if p is not None:
                return p.parent
        return Path.cwd()

    # -- Markieren und Zuordnen ----------------------------------------------

    def _push_undo(self) -> None:
        self.undo_stack.append(self.project.snapshot())
        self.redo_stack.clear()
        del self.undo_stack[:-100]

    def on_selection(self, side: Side, page: int, rect: pymupdf.Rect) -> None:
        """Neue Markierung aus einer Ansicht."""
        shift = bool(
            QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        self._push_undo()
        mark = self.project.add_mark(
            side,
            page,
            rect,
            continue_previous=shift,
            target_task=self.pinned_task,
        )
        task = self.project.task_of(mark.id)
        self.selected_task = task.id if task else None
        self.selected_mark = None
        self.refresh_ui()

    def select_mark(self, mark_id: int) -> None:
        """Wählt eine Markierung (und ihre Aufgabe) aus."""
        task = self.project.task_of(mark_id)
        self.selected_task = task.id if task else None
        self.selected_mark = mark_id
        self.refresh_ui(preview=False)

    def on_tree_selection(self, *_: object) -> None:
        """Auswahl in der Liste: Markierungen hervorheben und hinscrollen."""
        task_id, mark_id = self.tree.current_ids()
        self.selected_task, self.selected_mark = task_id, mark_id
        self._update_overlays()
        task = self.project.task(task_id) if task_id is not None else None
        if task is None:
            return
        for side in Side:
            marks = (
                [self.project.marks[mark_id]]
                if mark_id is not None and self.project.marks[mark_id].side is side
                else self.project.marks_of(task, side)
            )
            if marks:
                self.views[side].ensure_mark_visible(marks[0].page, marks[0].rect)

    def on_tree_drop(
        self, kind: str, ident: int, task_id: int | None, index: int | None
    ) -> None:
        """Drag & Drop in der Aufgabenliste übernehmen."""
        self._push_undo()
        if kind == "mark":
            self.project.move_mark_to(ident, task_id, index)
            task = self.project.task_of(ident)
            self.selected_task, self.selected_mark = (task.id if task else None), ident
        else:
            if task_id is not None:
                self.project.merge_task(ident, task_id)
                self.selected_task = task_id
            elif index is not None:
                self.project.reorder_task(ident, index)
                self.selected_task = ident
            self.selected_mark = None
        if self.pinned_task is not None and self.project.task(self.pinned_task) is None:
            self.btn_pin.setChecked(False)
        self.refresh_ui()

    def on_pin_toggled(self, on: bool) -> None:
        """Ausgewählte Aufgabe als Ziel für neue Markierungen anpinnen."""
        if on:
            task_id, _ = self.tree.current_ids()
            if task_id is None:
                self._push_undo()
                task_id = self.project.new_task().id
            self.pinned_task = task_id
            self.selected_task = task_id
        else:
            self.pinned_task = None
        self.refresh_ui(preview=False)

    def _move_menu(self, menu: QMenu, mark_id: int) -> None:
        current = self.project.task_of(mark_id)
        sub = menu.addMenu("Verschieben nach")
        for n, task in enumerate(self.project.tasks, 1):
            act = sub.addAction(f"Aufgabe {n}")
            act.setEnabled(task is not current)
            act.triggered.connect(lambda _=False, t=task.id: self._do_move(mark_id, t))
        sub.addSeparator()
        sub.addAction("Neue Aufgabe").triggered.connect(
            lambda _=False: self._do_move(mark_id, None)
        )

    def _rotation_menu(self, menu: QMenu, mark_id: int) -> None:
        act = menu.addAction("Im Ergebnis um 90° drehen")
        act.setCheckable(True)
        act.setChecked(self.project.marks[mark_id].rotate)
        act.triggered.connect(lambda checked: self._do_rotation(mark_id, checked))

    def _solo_action(self, menu: QMenu, task_id: int) -> None:
        task = self.project.task(task_id)
        if task is None:
            return
        n = self.project.task_number(task_id)
        act = menu.addAction(f"Aufgabe {n}: Keine Lösung – Angabe zentriert")
        act.setCheckable(True)
        act.setChecked(task.solo)
        if not task.solo and self.project.marks_of(task, Side.LOESUNG):
            act.setEnabled(False)
            act.setText(act.text() + "  (hat schon eine Lösung)")
        act.triggered.connect(lambda checked: self._do_solo(task_id, checked))

    def _do_solo(self, task_id: int, value: bool) -> None:
        self._push_undo()
        if self.project.set_solo(task_id, value):
            self.selected_task, self.selected_mark = task_id, None
            self.refresh_ui()
        else:
            self.undo_stack.pop()

    def _task_pair(self, task: Task) -> Pair:
        idx = self.project.tasks.index(task)
        return self.project.pairs()[idx]

    def _layout_menu(self, menu: QMenu, task_id: int) -> None:
        """Untermenü "Anordnung" mit Größenvergleich."""
        task = self.project.task(task_id)
        if task is None:
            return
        n = self.project.task_number(task_id)
        sub = menu.addMenu(f"Aufgabe {n}: Anordnung")
        if task.solo:
            sub.setEnabled(False)
            sub.setTitle(sub.title() + "  (ohne Lösung, zentriert)")
            return
        gain = stacked_gain(replace(self._task_pair(task), stacked=False), self.options)
        side_act = sub.addAction("Nebeneinander (Angabe links, Lösung rechts)")
        side_act.setCheckable(True)
        side_act.setChecked(not task.stacked)
        side_act.triggered.connect(lambda _=False: self._do_stacked(task_id, False))
        label = "Untereinander (Angabe oben, Lösung unten)"
        if gain is not None:
            label += "  –  " + _gain_text(gain)
        st_act = sub.addAction(label)
        st_act.setCheckable(True)
        st_act.setChecked(task.stacked)
        st_act.triggered.connect(lambda _=False: self._do_stacked(task_id, True))

    def _do_stacked(self, task_id: int, value: bool) -> None:
        self._push_undo()
        self.project.set_stacked(task_id, value)
        self.selected_task, self.selected_mark = task_id, None
        self.refresh_ui()

    def _scan_menu(self, menu: QMenu, mark_id: int) -> None:
        mark = self.project.marks[mark_id]
        if mark.side is not Side.LOESUNG:
            return  # der Scan-Filter wirkt nur auf die Lösung
        act = menu.addAction("Scan-Filter für diesen Ausschnitt")
        act.setCheckable(True)
        act.setChecked(not mark.no_scan)
        if self.options.scan_filter is ScanFilter.OFF:
            act.setText("Scan-Filter für diesen Ausschnitt (oben ausgeschaltet)")
        act.triggered.connect(lambda checked: self._do_no_scan(mark_id, not checked))

    def _do_no_scan(self, mark_id: int, value: bool) -> None:
        self._push_undo()
        self.project.set_no_scan(mark_id, value)
        self.refresh_ui()

    def _do_rotation(self, mark_id: int, value: bool) -> None:
        self._push_undo()
        self.project.set_rotation(mark_id, value)
        self.refresh_ui()

    def _do_move(self, mark_id: int, task_id: int | None) -> None:
        self._push_undo()
        self.project.move_mark(mark_id, task_id)
        task = self.project.task_of(mark_id)
        self.selected_task = task.id if task else None
        self.selected_mark = mark_id
        self.refresh_ui()

    def _do_delete_mark(self, mark_id: int) -> None:
        self._push_undo()
        self.project.delete_mark(mark_id)
        self.selected_mark = None
        self.refresh_ui()

    def show_mark_menu(self, mark_id: int, pos: QPoint) -> None:
        """Kontextmenü einer Markierung in der Dokumentansicht."""
        self.select_mark(mark_id)
        menu = QMenu(self)
        self._move_menu(menu, mark_id)
        self._rotation_menu(menu, mark_id)
        self._scan_menu(menu, mark_id)
        task = self.project.task_of(mark_id)
        if task is not None:
            menu.addSeparator()
            self._layout_menu(menu, task.id)
            if self.project.marks[mark_id].side is Side.ANGABE:
                self._solo_action(menu, task.id)
        menu.addSeparator()
        menu.addAction("Markierung löschen").triggered.connect(
            lambda _=False: self._do_delete_mark(mark_id)
        )
        menu.exec(pos)

    def show_tree_menu(self, pos: QPoint) -> None:
        """Kontextmenü der Aufgabenliste."""
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        task_id, mark_id = self.tree.current_ids()
        menu = QMenu(self)
        if mark_id is not None:
            self._move_menu(menu, mark_id)
            self._rotation_menu(menu, mark_id)
            self._scan_menu(menu, mark_id)
            menu.addSeparator()
            menu.addAction("Markierung löschen").triggered.connect(
                lambda _=False: self._do_delete_mark(mark_id)
            )
        elif task_id is not None:
            menu.addAction("Nach oben").triggered.connect(
                lambda _=False: self.move_selected_task(-1)
            )
            menu.addAction("Nach unten").triggered.connect(
                lambda _=False: self.move_selected_task(1)
            )
            menu.addAction("Als Ziel anpinnen 📌").triggered.connect(
                lambda _=False: (
                    self.btn_pin.setChecked(False),
                    self.btn_pin.setChecked(True),
                )
            )
            menu.addSeparator()
            self._layout_menu(menu, task_id)
            self._solo_action(menu, task_id)
            menu.addSeparator()
            menu.addAction("Aufgabe löschen").triggered.connect(self.delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def add_empty_task(self) -> None:
        """Neue leere Aufgabe hinter der ausgewählten anlegen und anpinnen."""
        self._push_undo()
        index = None
        if self.selected_task is not None and self.project.task(self.selected_task):
            index = self.project.tasks.index(self.project.task(self.selected_task)) + 1  # type: ignore[arg-type]
        task = self.project.new_task(index)
        self.selected_task, self.selected_mark = task.id, None
        self.refresh_ui(preview=False)
        self.btn_pin.setChecked(False)
        self.btn_pin.setChecked(True)

    def move_selected_task(self, delta: int) -> None:
        """Ausgewählte Aufgabe nach oben/unten schieben."""
        if self.selected_task is None:
            return
        self._push_undo()
        self.project.move_task(self.selected_task, delta)
        self.selected_mark = None
        self.refresh_ui()

    def delete_selected(self) -> None:
        """Löscht die ausgewählte Markierung bzw. Aufgabe."""
        task_id, mark_id = self.tree.current_ids()
        if mark_id is not None:
            self._do_delete_mark(mark_id)
        elif task_id is not None:
            self._push_undo()
            self.project.delete_task(task_id)
            if self.pinned_task == task_id:
                self.btn_pin.setChecked(False)
            self.selected_task = None
            self.refresh_ui()

    def undo(self) -> None:
        """Letzte Änderung an den Markierungen rückgängig machen."""
        if self.undo_stack:
            self.redo_stack.append(self.project)
            self.project = self.undo_stack.pop()
            self._after_history()

    def redo(self) -> None:
        """Rückgängig gemachte Änderung wiederherstellen."""
        if self.redo_stack:
            self.undo_stack.append(self.project)
            self.project = self.redo_stack.pop()
            self._after_history()

    def _after_history(self) -> None:
        if self.pinned_task is not None and self.project.task(self.pinned_task) is None:
            self.btn_pin.setChecked(False)
        self.selected_mark = None
        self.refresh_ui()

    # -- Seitenaktionen ------------------------------------------------------

    def on_page_action(self, side: Side, idx: int, action: str) -> None:
        """Seite drehen oder entfernen (Markierungen werden angepasst)."""
        doc = self.docs[side]
        if doc is None:
            return
        if action == "delete":
            n_marks = sum(
                1
                for m in self.project.marks.values()
                if m.side is side and m.page == idx
            )
            msg = f"Seite {idx + 1} ({self.page_names[side][idx]}) entfernen?"
            if n_marks:
                msg += f"\n{n_marks} Markierung(en) darauf werden gelöscht."
            if (
                QMessageBox.question(self, "Seite entfernen", msg)
                != QMessageBox.StandardButton.Yes
            ):
                return
            doc.delete_page(idx)
            del self.page_names[side][idx]
            self.project.remove_page(side, idx)
        else:
            angle = {"rot90": 90, "rot270": 270, "rot180": 180}[action]
            self.project.transform_page(side, idx, rotate_page(doc, idx, angle))
        # Dokumentänderungen lassen sich nicht rückgängig machen
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.filter_cache.clear()
        self.views[side].show_document(doc)
        self.refresh_ui()

    # -- Anzeige -------------------------------------------------------------

    def _describe(self) -> dict[int, str]:
        result: dict[int, str] = {}
        for mid, m in self.project.marks.items():
            names = self.page_names[m.side]
            name = names[m.page] if m.page < len(names) else f"S.{m.page + 1}"
            extra = ("  ↻ gedreht" if m.rotate else "") + (
                "  ⊘ kein Scan" if m.no_scan else ""
            )
            result[mid] = f"{m.side.short} · {name}{extra}"
        return result

    def _update_layout_hints(self) -> None:
        """Schlägt in der Liste vor, wo "untereinander" deutlich größer wäre."""
        pairs = self.project.pairs()
        for i, task in enumerate(self.project.tasks):
            item = self.tree.topLevelItem(i)
            if item is None or task.solo or task.stacked:
                continue
            if not (task.mark_ids and self.project.marks_of(task, Side.LOESUNG)):
                continue
            gain = stacked_gain(pairs[i], self.options)
            if gain is not None and gain >= 1.3:
                item.setText(0, item.text(0) + "   💡")
                item.setToolTip(
                    0,
                    "Untereinander wäre diese Aufgabe größer ("
                    + _gain_text(gain)
                    + ").\nRechtsklick → Anordnung → Untereinander",
                )

    def _update_overlays(self) -> None:
        for side, view in self.views.items():
            overlays: list[Overlay] = []
            for n, task in enumerate(self.project.tasks, 1):
                marks = self.project.marks_of(task, side)
                for k, m in enumerate(marks, 1):
                    label = f"{side.short}{n}" + (f"·{k}" if len(marks) > 1 else "")
                    if m.no_scan and self.options.scan_filter is not ScanFilter.OFF:
                        label += " ⊘ kein Scan"
                    highlighted = (
                        m.id == self.selected_mark
                        if self.selected_mark is not None
                        else task.id == self.selected_task
                    )
                    overlays.append(Overlay(m.id, m.page, m.rect, label, highlighted))
            view.set_overlays(overlays)

    def _status_text(self) -> str:
        if self.docs[Side.ANGABE] is None or self.docs[Side.LOESUNG] is None:
            return "Füge Angabe und Lösung hinzu (Toolbar, Drag & Drop oder Strg+V)."
        tasks = self.project.tasks
        no_l = sum(
            1
            for t in tasks
            if t.mark_ids and not t.solo and not self.project.marks_of(t, Side.LOESUNG)
        )
        no_a = sum(
            1 for t in tasks if t.mark_ids and not self.project.marks_of(t, Side.ANGABE)
        )
        parts = [f"{len(tasks)} Aufgabe(n)"]
        if no_l:
            parts.append(f"{no_l} ohne Lösung")
        if no_a:
            parts.append(f"{no_a} ohne Angabe")
        if self.pinned_task is not None and self.project.task(self.pinned_task):
            parts.append(
                f"📌 neue Markierungen → Aufgabe {self.project.task_number(self.pinned_task)}"
            )
        else:
            parts.append(
                "Markieren ordnet automatisch der Reihe nach zu, Shift = an vorige anhängen"
            )
        return " · ".join(parts)

    def refresh_ui(self, preview: bool = True) -> None:
        """Aktualisiert Liste, Overlays, Statuszeile und (verzögert) die Vorschau."""
        if (
            self.selected_task is not None
            and self.project.task(self.selected_task) is None
        ):
            self.selected_task = None
        if (
            self.selected_mark is not None
            and self.selected_mark not in self.project.marks
        ):
            self.selected_mark = None
        self.tree.populate(
            self.project,
            self._describe(),
            self.selected_task,
            self.selected_mark,
            self.pinned_task,
        )
        self._update_layout_hints()
        self._update_overlays()
        self._update_titles()
        self.status.setText(self._status_text())
        if preview:
            self._preview_timer.start()

    def _build(self) -> pymupdf.Document:
        return build_output(
            self.docs[Side.ANGABE],
            self.docs[Side.LOESUNG],
            self.project.pairs(),
            self.options,
            self.filter_cache,
        )

    def update_preview(self) -> None:
        """Baut die Ausgabe neu und zeigt sie in der Vorschau."""
        if not any(t.mark_ids for t in self.project.tasks):
            self.preview.show_document(None)
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            out = self._build()
        finally:
            QApplication.restoreOverrideCursor()
        self.preview.show_document(out)
        out.close()

    def export(self) -> None:
        """Speichert das Ergebnis als PDF."""
        tasks = [t for t in self.project.tasks if t.mark_ids]
        if not tasks:
            QMessageBox.information(
                self, "Nichts zu exportieren", "Noch keine Aufgaben markiert."
            )
            return
        incomplete = [
            str(n)
            for n, t in enumerate(self.project.tasks, 1)
            if t.mark_ids
            and not (
                self.project.marks_of(t, Side.ANGABE)
                and (t.solo or self.project.marks_of(t, Side.LOESUNG))
            )
        ]
        if incomplete and (
            QMessageBox.question(
                self,
                "Unvollständige Aufgaben",
                f"Aufgabe(n) {', '.join(incomplete)} haben keine Angabe oder keine "
                "Lösung. Trotzdem exportieren?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        src = self.first_path[Side.LOESUNG] or self.first_path[Side.ANGABE]
        default = (src.parent / f"{src.stem}_merged.pdf") if src else Path("merged.pdf")
        name, _ = QFileDialog.getSaveFileName(
            self, "Exportieren", str(default), "PDF (*.pdf)"
        )
        if not name:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            out = self._build()
            out.save(name, garbage=3, deflate=True)
            out.close()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Fehler beim Speichern", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.status.setText(f"✔ Gespeichert: {name}")


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
