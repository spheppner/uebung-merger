"""Aufgabenliste mit Drag & Drop."""

from __future__ import annotations

from PySide6.QtCore import (
    QPoint,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QKeyEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from .constants import COLOR_ANGABE, COLOR_LOESUNG
from .model import Project, Side

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
    delete_requested = Signal()

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

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit()
        else:
            super().keyPressEvent(event)

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
                title = "◉ " + title
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
