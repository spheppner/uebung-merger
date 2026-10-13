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
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

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
from .views import Overlay, PagesView, SelectView, Tool


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
