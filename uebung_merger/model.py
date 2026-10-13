"""Datenmodell: Seiten, Ausschnitte, Markierungen und Aufgaben (Qt-frei)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from enum import Enum

import pymupdf


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
