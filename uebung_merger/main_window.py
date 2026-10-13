"""Hauptfenster des Editors."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pymupdf
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QPoint,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QFontMetrics,
    QKeySequence,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .constants import (
    COLOR_ANGABE,
    COLOR_LOESUNG,
    FILE_FILTER,
    PREVIEW_PAGE_WIDTH_PX,
)
from .documents import load_document, rotate_page
from .layout import build_output, stacked_gain
from .model import Pair, Project, Side, Task
from .options import LayoutOptions, ScanFilter
from .scan import HAVE_CV, FilterCache
from .task_tree import TaskTree
from .views import EmptyPane, Overlay, PagesView, PreviewView, SelectView, Tool

# Breiten der Spalten (Aufgaben, Angabe, Lösung, Vorschau) beim Start bzw. wenn
# die Vorschau vergrößert ist; verteilt wird nur auf sichtbare Spalten.
_WEIGHTS = (0.17, 0.28, 0.28, 0.27)
_WEIGHTS_BIG = (0.2, 0.0, 0.0, 0.8)

HELP_TEXT = """
<h3>So entsteht dein Übungsblatt</h3>
<ol>
<li><b>Hinzufügen:</b> Angabe und Lösung (PDF oder Foto) in die jeweilige Spalte
ziehen, über „Datei wählen“ laden oder mit Strg+V aus der Zwischenablage einfügen.</li>
<li><b>Markieren:</b> Bereiche mit der Maus aufziehen. Die n-te Angabe wird
automatisch der n-ten Lösung zugeordnet – egal in welcher Reihenfolge.</li>
<li><b>Prüfen:</b> Rechts siehst du das Ergebnis. Ein Klick darauf wählt die
Aufgabe aus.</li>
<li><b>Exportieren:</b> Strg+S.</li>
</ol>
<h3>Zuordnung ändern</h3>
<ul>
<li><b>Shift</b> beim Markieren: an dieselbe Aufgabe wie die vorige Markierung
dieser Seite anhängen.</li>
<li><b>Aktive Aufgabe:</b> Solange sie festgelegt ist (orange Rahmen), landen alle
neuen Markierungen in dieser Aufgabe.</li>
<li><b>Ziehen in der Liste:</b> Markierung auf eine Aufgabe = neu zuordnen,
zwischen zwei Aufgaben = eigene Aufgabe. Aufgabe auf Aufgabe = zusammenführen,
Aufgabe zwischen Aufgaben = Reihenfolge ändern.</li>
</ul>
<h3>Tastenkürzel</h3>
<table cellspacing="6">
<tr><td>R / P</td><td>Rechteck ziehen / 4 Klicks</td></tr>
<tr><td>Esc</td><td>laufende Auswahl abbrechen</td></tr>
<tr><td>Entf</td><td>ausgewählte Markierung (oder Aufgabe in der Liste) löschen</td></tr>
<tr><td>Strg+Z / Strg+Y</td><td>rückgängig / wiederholen (auch Strg+Shift+Z)</td></tr>
<tr><td>Strg+Mausrad</td><td>zoomen</td></tr>
<tr><td>Rechtsklick</td><td>Menü zu Markierung bzw. Seite</td></tr>
</table>
"""


def _gain_text(gain: float) -> str:
    """Menschlich lesbarer Größenvergleich für die Anordnung."""
    pct = round((gain - 1) * 100)
    if gain >= 1.15:
        return f"Lösung ca. {gain:.1f}".replace(".", ",").replace("ca,", "ca.") + "× so groß"
    if gain > 0.95:
        return "etwa gleich groß"
    return f"wäre {abs(pct)} % kleiner"


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _muted(label: QLabel) -> QLabel:
    """Zurückgenommene Textfarbe aus der Systempalette (Hell und Dunkel)."""
    pal = label.palette()
    pal.setColor(QPalette.ColorRole.WindowText, pal.color(QPalette.ColorRole.PlaceholderText))
    label.setPalette(pal)
    return label


def _vline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def _pane(heading: QLabel, body: QWidget, *header_widgets: QWidget) -> QWidget:
    """Spalte mit Titelzeile (Titel links, weitere Elemente rechts)."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    head = QHBoxLayout()
    head.addWidget(heading, 1)
    for w in header_widgets:
        head.addWidget(w)
    lay.addLayout(head)
    lay.addWidget(body, 1)
    return box


class MainWindow(QMainWindow):
    """Editorfenster: Aufgaben, Angabe, Lösung und Live-Vorschau."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Angabe | Lösung – Merger {__version__}")
        screen = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(1000, 600)
        self.resize(
            min(1700, int(screen.width() * 0.96)),
            min(950, int(screen.height() * 0.94)),
        )
        self.move(screen.center() - self.rect().center())

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
        self.preview_regions: list[tuple[int, int, pymupdf.Rect]] = []
        self._show_tasks = True
        self._show_preview = True
        self._preview_big = False

        self._build_views()
        self._build_task_panel()
        self._build_settings()
        self._build_preview_panel()
        self._build_strip()

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.task_box)
        for side in Side:
            self.splitter.addWidget(self.side_boxes[side])
        self.splitter.addWidget(self.preview_box)
        for i, minimum in enumerate((230, 260, 260, 340)):
            self.splitter.widget(i).setMinimumWidth(minimum)
        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.splitter, 1)
        col.addWidget(self.strip)
        self.setCentralWidget(central)

        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self.update_preview)

        self._build_toolbar()
        self._apply_panels()
        self.refresh_ui()

    # -- Aufbau --------------------------------------------------------------

    def _build_views(self) -> None:
        self.views = {
            Side.ANGABE: SelectView(COLOR_ANGABE),
            Side.LOESUNG: SelectView(COLOR_LOESUNG),
        }
        self.preview = PreviewView(PREVIEW_PAGE_WIDTH_PX)
        self.preview.set_placeholder(
            "Die Vorschau erscheint,\nsobald du Bereiche markiert hast."
        )
        self.preview.cell_clicked.connect(self.on_preview_click)

        self.stacks: dict[Side, QStackedWidget] = {}
        self.titles: dict[Side, QLabel] = {}
        self.page_labels: dict[Side, QLabel] = {}
        self.page_menus: dict[Side, QMenu] = {}
        self.side_boxes: dict[Side, QWidget] = {}
        subtitles = {
            Side.ANGABE: "PDF oder Foto der Angabe hierher ziehen",
            Side.LOESUNG: "PDFs oder Fotos der Lösung hierher ziehen\n(auch mehrere)",
        }
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
            view.delete_pressed.connect(self.delete_selected_mark)

            empty = EmptyPane(f"{side.label} hinzufügen", subtitles[side], view.color)
            empty.files_dropped.connect(lambda paths, s=side: self.add_files(s, paths))
            empty.browse_requested.connect(lambda s=side: self.add_files(s))
            empty.paste_requested.connect(lambda s=side: self.paste_clipboard(s))
            stack = QStackedWidget()
            stack.addWidget(empty)
            stack.addWidget(view)
            self.stacks[side] = stack

            title = _heading(side.label)
            self.titles[side] = title
            page_label = _muted(QLabel(""))
            self.page_labels[side] = page_label
            view.page_changed.connect(
                lambda i, n, s=side: self._set_page_label(self.page_labels[s], i, n)
            )
            page_btn = QToolButton()
            page_btn.setText("Seite ▾")
            page_btn.setToolTip("Aktuelle Seite drehen oder entfernen")
            page_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(page_btn)
            menu.aboutToShow.connect(lambda s=side: self._fill_page_menu(s))
            page_btn.setMenu(menu)
            self.page_menus[side] = menu
            self.side_boxes[side] = _pane(title, stack, page_label, page_btn)

    @staticmethod
    def _set_page_label(label: QLabel, index: int, count: int) -> None:
        label.setText(f"Seite {index + 1} / {count}" if index >= 0 else "")

    def _build_task_panel(self) -> None:
        self.tree = TaskTree()
        self.tree.drop_requested.connect(self.on_tree_drop)
        self.tree.currentItemChanged.connect(self.on_tree_selection)
        self.tree.customContextMenuRequested.connect(self.show_tree_menu)
        self.tree.delete_requested.connect(self.delete_selected)

        self.lbl_steps = QLabel(
            "<b>So geht's</b><ol style='margin-left:-20px'>"
            "<li>Angabe und Lösung hinzufügen</li>"
            "<li>Aufgaben in beiden Dokumenten markieren – die n-te Angabe passt "
            "zur n-ten Lösung</li>"
            "<li>Vorschau prüfen und exportieren (Strg+S)</li></ol>"
        )
        self.lbl_steps.setWordWrap(True)
        self.lbl_steps.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.lbl_steps.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_steps.setContentsMargins(4, 8, 4, 4)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.tree, 1)
        bl.addWidget(self.lbl_steps, 1)

        panel = QWidget()
        tp = QVBoxLayout(panel)
        tp.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        head.addWidget(_heading("Aufgaben"), 1)
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip("Hilfe: Zuordnen, Ziehen und Tastenkürzel (F1)")
        help_btn.clicked.connect(self.show_help)
        head.addWidget(help_btn)
        tp.addLayout(head)
        tp.addWidget(body, 1)

        row = QHBoxLayout()
        for text, tip, slot in (
            ("＋", "Neue leere Aufgabe", self.add_empty_task),
            ("▲", "Aufgabe nach oben", lambda: self.move_selected_task(-1)),
            ("▼", "Aufgabe nach unten", lambda: self.move_selected_task(1)),
            ("✕", "Ausgewählte Markierung/Aufgabe löschen (Entf)", self.delete_selected),
        ):
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        tp.addLayout(row)

        self.btn_pin = QPushButton()
        self.btn_pin.setCheckable(True)
        self.btn_pin.setToolTip(
            "Solange eine Aufgabe aktiv ist (orange Rahmen um die Dokumente),\n"
            "landen alle neuen Markierungen in dieser Aufgabe. Sonst werden sie\n"
            "automatisch der Reihe nach zugeordnet."
        )
        self.btn_pin.setStyleSheet(
            "QPushButton:checked { background: #f59e0b; color: black; font-weight: bold; }"
        )
        self.btn_pin.toggled.connect(self.on_pin_toggled)
        tp.addWidget(self.btn_pin)
        tip = _muted(
            QLabel(
                "Tipp: Einträge ziehen, um sie neu zuzuordnen. "
                "Mehr unter „?“."
            )
        )
        tip.setWordWrap(True)
        tp.addWidget(tip)
        self.task_box = panel

    def _build_settings(self) -> None:
        """Ausgabe-Einstellungen: sie wirken auf die Vorschau und stehen darüber."""
        self.settings = QFrame()
        self.settings.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(self.settings)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self.cb_mid = QCheckBox("Mittellinie")
        self.cb_mid.setChecked(self.options.mid_line)
        self.cb_sep = QCheckBox("Trennlinien")
        self.cb_sep.setToolTip("Linie zwischen den Aufgaben")
        self.cb_sep.setChecked(self.options.separator_line)
        self.sb_margin = QDoubleSpinBox()
        self.sb_margin.setRange(0, 30)
        self.sb_margin.setSingleStep(1)
        self.sb_margin.setSuffix(" mm")
        self.sb_margin.setValue(self.options.margin_mm)
        self.sb_margin.setToolTip("Seitenrand")
        self.cmb_filter = QComboBox()
        for f in ScanFilter:
            self.cmb_filter.addItem(f.value, f)
        self.cmb_filter.setToolTip(
            "Macht aus Handyfotos der Lösung saubere Scans (Schatten und Karo weg)."
        )

        self.row_thresh = QWidget()
        tl = QHBoxLayout(self.row_thresh)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("Hintergrund entfernen:"))
        tl.addWidget(_muted(QLabel("schwächer")))
        self.sl_thresh = QSlider(Qt.Orientation.Horizontal)
        self.sl_thresh.setRange(10, 70)
        self.sl_thresh.setSingleStep(2)
        self.sl_thresh.setPageStep(6)
        self.sl_thresh.setValue(round(self.options.scan_threshold * 100))
        self.sl_thresh.setToolTip(
            "Stärker: sauberer Hintergrund, aber dünne oder helle Striche können\n"
            "verschwinden. Schwächer: Striche bleiben, Karo bleibt teilweise.\n"
            "Das Ergebnis siehst du sofort in der Vorschau."
        )
        tl.addWidget(self.sl_thresh, 1)
        tl.addWidget(_muted(QLabel("stärker")))

        self.lbl_no_cv = QLabel(
            "⚠ Der Scan-Filter ist nicht verfügbar. Installieren mit:<br>"
            "<code>pip install numpy opencv-python-headless</code>"
        )
        self.lbl_no_cv.setWordWrap(True)
        self.lbl_no_cv.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        row0 = QHBoxLayout()
        row0.addWidget(self.cb_mid)
        row0.addWidget(self.cb_sep)
        row0.addStretch(1)
        row0.addWidget(QLabel("Rand:"))
        row0.addWidget(self.sb_margin)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Scan-Filter (Lösung):"))
        row1.addWidget(self.cmb_filter, 1)
        grid.addLayout(row0, 0, 0)
        grid.addLayout(row1, 1, 0)
        grid.addWidget(self.row_thresh, 2, 0)
        grid.addWidget(self.lbl_no_cv, 3, 0)

        if not HAVE_CV:
            self.cmb_filter.setEnabled(False)
        self.lbl_no_cv.setVisible(not HAVE_CV)
        self.row_thresh.setVisible(False)

        for sig in (
            self.cb_mid.toggled,
            self.cb_sep.toggled,
            self.sb_margin.valueChanged,
            self.cmb_filter.currentIndexChanged,
            self.sl_thresh.valueChanged,
        ):
            sig.connect(self._options_changed)

    def _build_preview_panel(self) -> None:
        self.lbl_preview_page = _muted(QLabel(""))
        self.preview.page_changed.connect(
            lambda i, n: self._set_page_label(self.lbl_preview_page, i, n)
        )
        self.btn_big = QToolButton()
        self.btn_big.setText("⤢ Groß")
        self.btn_big.setCheckable(True)
        self.btn_big.setToolTip(
            "Vorschau vergrößern: Angabe und Lösung werden ausgeblendet"
        )
        self.btn_big.toggled.connect(self._on_big_toggled)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.settings)
        bl.addWidget(self.preview, 1)
        self.preview_box = _pane(
            _heading("Vorschau (A4 quer)"), body, self.lbl_preview_page, self.btn_big
        )

    def _build_strip(self) -> None:
        """Leiste für die Auswahl: macht versteckte Menüfunktionen sichtbar."""
        self.strip = QFrame()
        self.strip.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(self.strip)
        grid.setContentsMargins(10, 4, 10, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)

        self.lbl_task = _heading("")
        self.lbl_mark = _heading("")
        for label in (self.lbl_task, self.lbl_mark):
            label.setFixedWidth(250)
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        self.btn_side = QToolButton()
        self.btn_side.setText("▯ ▯  Nebeneinander")
        self.btn_side.setToolTip("Angabe links, Lösung rechts")
        self.btn_stack = QToolButton()
        self.btn_stack.setText("▭ ▭  Untereinander")
        self.btn_stack.setToolTip(
            "Angabe oben, Lösung unten, über die ganze Seitenbreite"
        )
        group = QButtonGroup(self)
        group.setExclusive(True)
        for btn in (self.btn_side, self.btn_stack):
            btn.setCheckable(True)
            group.addButton(btn)
        self.btn_side.clicked.connect(lambda: self._strip_stacked(False))
        self.btn_stack.clicked.connect(lambda: self._strip_stacked(True))
        self.lbl_gain = _muted(QLabel(""))
        self.chk_solo = QCheckBox("Ohne Lösung (Angabe zentriert)")
        self.chk_solo.clicked.connect(self._strip_solo)

        self.chk_rotate = QCheckBox("Um 90° drehen")
        self.chk_rotate.setToolTip("Den Ausschnitt im Ergebnis hochkant einsetzen")
        self.chk_rotate.clicked.connect(self._strip_rotate)
        self.chk_scan = QCheckBox("Scan-Filter anwenden")
        self.chk_scan.clicked.connect(self._strip_scan)

        grid.addWidget(self.lbl_task, 0, 0)
        grid.addWidget(QLabel("Anordnung:"), 0, 1)
        grid.addWidget(self.btn_side, 0, 2)
        grid.addWidget(self.btn_stack, 0, 3)
        grid.addWidget(self.lbl_gain, 0, 4)
        grid.addWidget(self.chk_solo, 0, 5)
        grid.addWidget(self.lbl_mark, 1, 0)
        grid.addWidget(self.chk_rotate, 1, 2, 1, 2)
        grid.addWidget(self.chk_scan, 1, 4, 1, 2)
        grid.setColumnStretch(4, 1)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Werkzeuge")
        tb.setMovable(False)

        def add(text: str, slot: object, tip: str, shortcut: object = None) -> QAction:
            act = QAction(text, self)
            if shortcut is not None:
                if isinstance(shortcut, list):
                    act.setShortcuts(shortcut)
                else:
                    act.setShortcut(QKeySequence(shortcut))
                keys = ", ".join(
                    k.toString(QKeySequence.SequenceFormat.NativeText)
                    for k in act.shortcuts()
                )
                tip += f" ({keys})"
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False: slot())  # type: ignore[operator]
            tb.addAction(act)
            return act

        add("Neu", self.new_project, "Alles schließen und neu beginnen")
        add(
            "＋ Angabe",
            lambda: self.add_files(Side.ANGABE),
            "Angabe-Dateien hinzufügen",
        )
        add(
            "＋ Lösung",
            lambda: self.add_files(Side.LOESUNG),
            "Lösungs-Dateien hinzufügen",
        )
        add(
            "Einfügen",
            self.paste_clipboard,
            "Bild oder Dateien aus der Zwischenablage einfügen",
            QKeySequence.StandardKey.Paste,
        )
        tb.addSeparator()

        group = QActionGroup(self)
        for tool, text, tip, key in (
            (Tool.RECT, "▭ Rechteck", "Bereich mit der Maus aufziehen", "R"),
            (Tool.FOUR_CLICK, "⁘ 4 Klicks", "Bereich über vier Eckpunkte wählen", "P"),
        ):
            act = QAction(text, self, checkable=True)
            act.setShortcut(QKeySequence(key))
            act.setToolTip(f"{tip} ({key})")
            act.setChecked(tool is Tool.RECT)
            act.triggered.connect(lambda _=False, t=tool: self.set_tool(t))
            group.addAction(act)
            tb.addAction(act)
        tb.addSeparator()

        add("↶ Rückgängig", self.undo, "Letzte Änderung rückgängig", QKeySequence.StandardKey.Undo)
        add(
            "↷ Wiederholen",
            self.redo,
            "Rückgängiges wiederherstellen",
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")],
        )
        tb.addSeparator()

        self.act_tasks = QAction("☰ Aufgaben", self, checkable=True)
        self.act_tasks.setChecked(True)
        self.act_tasks.setToolTip("Aufgabenliste ein-/ausblenden")
        self.act_tasks.toggled.connect(self._on_tasks_toggled)
        tb.addAction(self.act_tasks)
        self.act_preview = QAction("▣ Vorschau", self, checkable=True)
        self.act_preview.setChecked(True)
        self.act_preview.setToolTip("Vorschau ein-/ausblenden")
        self.act_preview.toggled.connect(self._on_preview_toggled)
        tb.addAction(self.act_preview)
        add("?  Hilfe", self.show_help, "Hilfe und Tastenkürzel", "F1")

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        export = QAction("💾 Exportieren …", self)
        export.setShortcut(QKeySequence.StandardKey.Save)
        export.setToolTip(
            "Als PDF speichern (" + QKeySequence("Ctrl+S").toString(
                QKeySequence.SequenceFormat.NativeText
            ) + ")"
        )
        export.triggered.connect(lambda _=False: self.export())
        tb.addAction(export)
        btn = tb.widgetForAction(export)
        if btn is not None:
            btn.setStyleSheet(
                "QToolButton { background: #2563eb; color: white; font-weight: bold;"
                " padding: 4px 14px; border-radius: 4px; }"
                "QToolButton:hover { background: #1d4ed8; }"
            )

    # -- Spalten ein-/ausblenden ---------------------------------------------

    def _on_tasks_toggled(self, on: bool) -> None:
        self._show_tasks = on
        self._apply_panels()

    def _on_preview_toggled(self, on: bool) -> None:
        self._show_preview = on
        self._apply_panels()

    def _on_big_toggled(self, on: bool) -> None:
        self._preview_big = on
        self.btn_big.setText("⤡ Normal" if on else "⤢ Groß")
        self._apply_panels()

    def _apply_panels(self) -> None:
        big = self._preview_big and self._show_preview
        visible = [
            self._show_tasks,
            not big,
            not big,
            self._show_preview,
        ]
        for i, show in enumerate(visible):
            self.splitter.widget(i).setVisible(show)
        weights = _WEIGHTS_BIG if big else _WEIGHTS
        active = [w if v else 0.0 for w, v in zip(weights, visible)]
        total = sum(active) or 1.0
        width = max(self.splitter.width(), self.width())
        self.splitter.setSizes([int(width * w / total) for w in active])

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        if not getattr(self, "_sized", False):
            self._sized = True
            self._apply_panels()

    def show_help(self) -> None:
        """Erklärt Workflow, Zuordnung und Tastenkürzel."""
        box = QMessageBox(self)
        box.setWindowTitle("Hilfe")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(HELP_TEXT)
        box.addButton("Schließen", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    def _confirm(self, title: str, text: str, yes: str, no: str = "Abbrechen") -> bool:
        """Rückfrage mit deutschen, beschrifteten Buttons."""
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self)
        yes_btn = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(no, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is yes_btn

    def _options_changed(self) -> None:
        self.options.mid_line = self.cb_mid.isChecked()
        self.options.separator_line = self.cb_sep.isChecked()
        self.options.margin_mm = self.sb_margin.value()
        self.options.scan_filter = self.cmb_filter.currentData()
        self.options.scan_threshold = self.sl_thresh.value() / 100
        self.row_thresh.setVisible(self.options.scan_filter is not ScanFilter.OFF)
        self.refresh_ui()

    def set_tool(self, tool: Tool) -> None:
        """Setzt das Auswahlwerkzeug in beiden Ansichten."""
        for view in self.views.values():
            view.set_tool(tool)

    # -- Dateien -------------------------------------------------------------

    def new_project(self) -> None:
        """Alles schließen und von vorne beginnen."""
        if self.project.marks and not self._confirm(
            "Neu beginnen",
            "Alle Dokumente und Markierungen verwerfen?",
            "Verwerfen",
        ):
            return
        for side in Side:
            if self.docs[side] is not None:
                self.docs[side].close()  # type: ignore[union-attr]
            self.docs[side] = None
            self.page_names[side] = []
            self.first_path[side] = None
            self.views[side].show_document(None)
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

    def _paste_side(self) -> Side:
        """Angabe, wenn deren Spalte den Fokus hat, sonst die Lösung."""
        focus = QApplication.focusWidget()
        stack = self.stacks[Side.ANGABE]
        if focus is not None and (focus is stack or stack.isAncestorOf(focus)):
            return Side.ANGABE
        return Side.LOESUNG

    def paste_clipboard(self, side: Side | None = None) -> None:
        """Fügt ein Bild (oder kopierte Dateien) aus der Zwischenablage ein.

        Ohne Angabe ist das Ziel die Angabe, wenn deren Spalte den Fokus hat,
        sonst die Lösung.
        """
        side = side or self._paste_side()
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
            self.stacks[side].setCurrentIndex(1 if self.docs[side] is not None else 0)
            self.page_menus[side].parentWidget().setEnabled(bool(names))
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

    def _fill_page_menu(self, side: Side) -> None:
        """Seitenmenü für die gerade sichtbare Seite der Spalte."""
        menu = self.page_menus[side]
        menu.clear()
        idx = self.views[side].current_page()
        if idx is None:
            return
        n = idx + 1
        for text, action in (
            (f"Seite {n} um 90° im Uhrzeigersinn drehen", "rot90"),
            (f"Seite {n} um 90° gegen den Uhrzeigersinn drehen", "rot270"),
            (f"Seite {n} um 180° drehen", "rot180"),
        ):
            menu.addAction(text).triggered.connect(
                lambda _=False, a=action: self.on_page_action(side, idx, a)
            )
        menu.addSeparator()
        menu.addAction(f"Seite {n} entfernen").triggered.connect(
            lambda _=False: self.on_page_action(side, idx, "delete")
        )

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
        if task is not None:
            self._reveal(task, mark_id)

    def _reveal(self, task: Task, mark_id: int | None) -> None:
        """Scrollt Angabe und Lösung zu den Markierungen einer Aufgabe."""
        for side in Side:
            marks = (
                [self.project.marks[mark_id]]
                if mark_id is not None and self.project.marks[mark_id].side is side
                else self.project.marks_of(task, side)
            )
            if marks:
                self.views[side].ensure_mark_visible(marks[0].page, marks[0].rect)

    def on_preview_click(self, page: int, x: float, y: float) -> None:
        """Klick in die Vorschau wählt die darunterliegende Aufgabe."""
        for index, region_page, rect in self.preview_regions:
            if region_page == page and rect.contains(pymupdf.Point(x, y)):
                if index >= len(self.project.tasks):
                    return
                task = self.project.tasks[index]
                self.selected_task, self.selected_mark = task.id, None
                self.refresh_ui(preview=False)
                self._reveal(task, None)
                return

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
        """Ausgewählte Aufgabe als aktive Aufgabe für neue Markierungen festlegen."""
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
            self.selected_task = task_id
            self.refresh_ui()
        else:
            self.undo_stack.pop()
            self._update_strip()

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
        self.selected_task = task_id
        self.refresh_ui()

    def _scan_menu(self, menu: QMenu, mark_id: int) -> None:
        mark = self.project.marks[mark_id]
        if mark.side is not Side.LOESUNG:
            return  # der Scan-Filter wirkt nur auf die Lösung
        act = menu.addAction("Scan-Filter für diesen Ausschnitt")
        act.setCheckable(True)
        act.setChecked(not mark.no_scan)
        if self.options.scan_filter is ScanFilter.OFF:
            act.setText("Scan-Filter für diesen Ausschnitt (global ausgeschaltet)")
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

    # -- Auswahl-Leiste ------------------------------------------------------

    def _strip_stacked(self, value: bool) -> None:
        if self.selected_task is not None:
            self._do_stacked(self.selected_task, value)

    def _strip_solo(self, checked: bool) -> None:
        if self.selected_task is not None:
            self._do_solo(self.selected_task, checked)

    def _strip_rotate(self, checked: bool) -> None:
        if self.selected_mark is not None:
            self._do_rotation(self.selected_mark, checked)

    def _strip_scan(self, checked: bool) -> None:
        if self.selected_mark is not None:
            self._do_no_scan(self.selected_mark, not checked)

    def _update_strip(self) -> None:
        """Zeigt die Einstellungen der ausgewählten Aufgabe bzw. Markierung."""
        task = (
            self.project.task(self.selected_task)
            if self.selected_task is not None
            else None
        )
        mark = (
            self.project.marks.get(self.selected_mark)
            if self.selected_mark is not None
            else None
        )
        widgets = (
            self.btn_side,
            self.btn_stack,
            self.chk_solo,
            self.chk_rotate,
            self.chk_scan,
        )
        for w in widgets:
            w.blockSignals(True)
        metrics = QFontMetrics(self.lbl_mark.font())

        # Aufgabe
        if task is None:
            self.lbl_task.setText("Keine Aufgabe gewählt")
            self.lbl_task.setToolTip("")
            self.lbl_gain.setText(
                "Wähle eine Aufgabe oder Markierung (Liste, Dokument oder Vorschau)."
            )
            for w in (self.btn_side, self.btn_stack, self.chk_solo):
                w.setEnabled(False)
            self.btn_side.setChecked(False)
            self.btn_stack.setChecked(False)
            self.chk_solo.setChecked(False)
        else:
            n = self.project.task_number(task.id)
            self.lbl_task.setText(f"Aufgabe {n}")
            self.btn_side.setEnabled(not task.solo)
            self.btn_stack.setEnabled(not task.solo)
            self.btn_side.setChecked(not task.stacked)
            self.btn_stack.setChecked(task.stacked)
            has_loesung = bool(self.project.marks_of(task, Side.LOESUNG))
            self.chk_solo.setChecked(task.solo)
            self.chk_solo.setEnabled(task.solo or not has_loesung)
            self.chk_solo.setToolTip(
                "Diese Aufgabe hat schon eine Lösung." if has_loesung and not task.solo
                else "Keine Lösung vorgesehen: die Angabe steht allein mittig auf der Seite."
            )
            gain_text = ""
            if task.solo:
                gain_text = "Ohne Lösung entfällt die Anordnung."
            elif task.mark_ids:
                gain = stacked_gain(
                    replace(self._task_pair(task), stacked=False), self.options
                )
                if gain is not None:
                    gain_text = "Untereinander: " + _gain_text(gain)
            self.lbl_gain.setText(gain_text)

        # Markierung
        if mark is None:
            self.lbl_mark.setText("Keine Markierung gewählt")
            self.lbl_mark.setToolTip("")
            self.chk_rotate.setEnabled(False)
            self.chk_scan.setEnabled(False)
            self.chk_rotate.setChecked(False)
            self.chk_scan.setChecked(False)
            self.chk_scan.setToolTip("Markierung in Liste oder Dokument anklicken.")
        else:
            names = self.page_names[mark.side]
            name = names[mark.page] if mark.page < len(names) else f"S.{mark.page + 1}"
            full = f"Markierung: {mark.side.short} · {name}"
            self.lbl_mark.setText(
                metrics.elidedText(full, Qt.TextElideMode.ElideMiddle, 240)
            )
            self.lbl_mark.setToolTip(full)
            self.chk_rotate.setEnabled(True)
            self.chk_rotate.setChecked(mark.rotate)
            is_loesung = mark.side is Side.LOESUNG
            self.chk_scan.setEnabled(is_loesung)
            self.chk_scan.setChecked(not mark.no_scan)
            if not is_loesung:
                self.chk_scan.setToolTip("Der Scan-Filter wirkt nur auf Lösungen.")
            elif self.options.scan_filter is ScanFilter.OFF:
                self.chk_scan.setToolTip(
                    "Der Scan-Filter ist in den Ausgabe-Einstellungen ausgeschaltet."
                )
            else:
                self.chk_scan.setToolTip("")
        for w in widgets:
            w.blockSignals(False)

    # -- Kontextmenüs --------------------------------------------------------

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
            menu.addAction("Als aktive Aufgabe festlegen").triggered.connect(
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
        """Neue leere Aufgabe hinter der ausgewählten anlegen und aktiv setzen."""
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

    def delete_selected_mark(self) -> None:
        """Entf in einer Dokumentansicht: nur die ausgewählte Markierung löschen."""
        if self.selected_mark is not None and self.selected_mark in self.project.marks:
            self._do_delete_mark(self.selected_mark)

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
            msg += "\nDabei wird der Rückgängig-Verlauf gelöscht."
            if not self._confirm("Seite entfernen", msg, "Entfernen"):
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
                    + ").\nUnten in der Leiste: Anordnung → Untereinander",
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
        self._update_strip()
        self._update_preview_highlight()

    def _update_preview_highlight(self) -> None:
        """Umrahmt die ausgewählte Aufgabe in der Vorschau."""
        task = (
            self.project.task(self.selected_task)
            if self.selected_task is not None
            else None
        )
        if task is not None:
            index = self.project.tasks.index(task)
            for i, page, rect in self.preview_regions:
                if i == index:
                    self.preview.set_highlight(page, rect)
                    return
        self.preview.set_highlight(None, None)

    def _status_text(self) -> str:
        if self.docs[Side.ANGABE] is None or self.docs[Side.LOESUNG] is None:
            return "Füge Angabe und Lösung hinzu (Buttons, Drag & Drop oder Strg+V)."
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
                "neue Markierungen → Aufgabe "
                f"{self.project.task_number(self.pinned_task)} (aktive Aufgabe)"
            )
        else:
            parts.append(
                "Markieren ordnet automatisch der Reihe nach zu, Shift = an vorige anhängen"
            )
        return " · ".join(parts)

    def _update_active_task(self) -> None:
        """Zeigt die aktive Aufgabe am Button und als Rahmen um die Dokumente."""
        active = (
            self.pinned_task is not None and self.project.task(self.pinned_task)
        )
        if active:
            n = self.project.task_number(self.pinned_task)  # type: ignore[arg-type]
            self.btn_pin.setText(f"Aktive Aufgabe: {n} – beenden")
            banner = f"Neue Markierungen → Aufgabe {n}"
        else:
            self.btn_pin.setText("Aktive Aufgabe festlegen …")
            banner = None
        for view in self.views.values():
            view.set_active(banner)

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
        if self.pinned_task is not None and self.project.task(self.pinned_task) is None:
            self.pinned_task = None
        self.tree.populate(
            self.project,
            self._describe(),
            self.selected_task,
            self.selected_mark,
            self.pinned_task,
        )
        empty = not self.project.tasks
        self.tree.setVisible(not empty)
        self.lbl_steps.setVisible(empty)
        self._update_layout_hints()
        self._update_active_task()
        self._update_overlays()
        self._update_titles()
        self.status.setText(self._status_text())
        if preview:
            self._preview_timer.start()

    def _build(
        self, regions: list[tuple[int, int, pymupdf.Rect]] | None = None
    ) -> pymupdf.Document:
        return build_output(
            self.docs[Side.ANGABE],
            self.docs[Side.LOESUNG],
            self.project.pairs(),
            self.options,
            self.filter_cache,
            regions,
        )

    def update_preview(self) -> None:
        """Baut die Ausgabe neu und zeigt sie in der Vorschau."""
        if not any(t.mark_ids for t in self.project.tasks):
            self.preview_regions = []
            self.preview.show_document(None)
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        regions: list[tuple[int, int, pymupdf.Rect]] = []
        try:
            out = self._build(regions)
        finally:
            QApplication.restoreOverrideCursor()
        self.preview_regions = regions
        self.preview.show_document(out)
        out.close()
        self._update_preview_highlight()

    def export(self) -> None:
        """Speichert das Ergebnis als PDF."""
        tasks = [t for t in self.project.tasks if t.mark_ids]
        if not tasks:
            QMessageBox.information(
                self, "Nichts zu exportieren", "Noch keine Aufgaben markiert."
            )
            return
        incomplete = []
        for n, t in enumerate(self.project.tasks, 1):
            if not t.mark_ids:
                continue
            if not self.project.marks_of(t, Side.ANGABE):
                incomplete.append(f"Aufgabe {n}: Angabe fehlt")
            elif not (t.solo or self.project.marks_of(t, Side.LOESUNG)):
                incomplete.append(f"Aufgabe {n}: Lösung fehlt")
        if incomplete and not self._confirm(
            "Unvollständige Aufgaben",
            "Diese Aufgaben sind unvollständig:\n\n  "
            + "\n  ".join(incomplete)
            + "\n\nTrotzdem exportieren?",
            "Trotzdem exportieren",
            "Zurück zum Bearbeiten",
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
