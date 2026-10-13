"""Seitenansichten mit Markierwerkzeugen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf
from PySide6.QtCore import (
    QPoint,
    QPointF,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .constants import EDIT_PAGE_WIDTH_PX, MIN_SELECTION_PX, MM, PAGE_GAP_PX

ACCENT = "#f59e0b"  # Hervorhebung: aktive Aufgabe, Ablegen, Vorschau-Auswahl
TOP_MARGIN_PX = 40  # Platz über der ersten Seite für Markierungs-Fähnchen


def pixmap_to_qpixmap(pix: pymupdf.Pixmap) -> QPixmap:
    """Wandelt eine PyMuPDF-Pixmap (RGB, ohne Alpha) in eine QPixmap um."""
    img = QImage(
        pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
    )
    return QPixmap.fromImage(img.copy())


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
    page_changed = Signal(int, int)  # sichtbare Seite (0-basiert, -1 = keine), Anzahl

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
        self._drop_active = False
        self._frame_color: str | None = None
        self._last_page = -2
        self._apply_frame()
        self._banner = self._make_overlay_label(
            f"background: {ACCENT}; color: black; font-weight: bold;"
            "padding: 3px 12px; border-radius: 9px;"
        )
        self._hint = self._make_overlay_label(
            "background: rgba(20,20,20,215); color: white; padding: 4px 10px;"
            "border-radius: 6px;"
        )
        self.verticalScrollBar().valueChanged.connect(self._emit_page)

    # -- Rahmen und Hinweise über der Ansicht -------------------------------

    def _make_overlay_label(self, style: str) -> QLabel:
        label = QLabel(self.viewport())
        label.setStyleSheet(style)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.hide()
        return label

    def _apply_frame(self) -> None:
        # Der Rahmen ist immer 3 px breit, damit sich die Fläche nie verschiebt.
        if self._drop_active:
            border = f"3px dashed {ACCENT}"
        else:
            border = f"3px solid {self._frame_color or 'transparent'}"
        self.setStyleSheet(f"QGraphicsView {{ border: {border}; }}")

    def set_active(self, text: str | None) -> None:
        """Zeigt (oder entfernt) Rahmen und Banner "neue Markierungen → Aufgabe n"."""
        self._frame_color = ACCENT if text else None
        self._apply_frame()
        self._set_overlay(self._banner, text)

    def set_hint(self, text: str | None) -> None:
        """Kurzer Bedienhinweis unten links in der Ansicht."""
        self._set_overlay(self._hint, text)

    def _set_overlay(self, label: QLabel, text: str | None) -> None:
        if text:
            label.setText(text)
            label.adjustSize()
            label.show()
            label.raise_()
        else:
            label.hide()
        self._place_overlays()

    def _place_overlays(self) -> None:
        vp = self.viewport().rect()
        self._banner.move((vp.width() - self._banner.width()) // 2, 8)
        self._hint.move(10, vp.height() - self._hint.height() - 10)

    # -- Seitenanzeige ------------------------------------------------------

    def current_page(self) -> int | None:
        """Index der Seite, die gerade im oberen Drittel der Ansicht liegt."""
        if not self.page_items:
            return None
        vp = self.viewport()
        y = self.mapToScene(vp.width() // 2, vp.height() // 3).y()
        current = 0
        for i, item in enumerate(self.page_items):
            if item.pos().y() <= y:
                current = i
        return current

    def _emit_page(self, *_: object) -> None:
        page = self.current_page()
        key = -1 if page is None else page
        if key != self._last_page:
            self._last_page = key
            self.page_changed.emit(key, len(self.page_items))

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
            self._last_page = -2
            self._emit_page()
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
        self.scene().setSceneRect(QRectF(
                -20, -TOP_MARGIN_PX, self.page_width_px + 40, y + TOP_MARGIN_PX
            )
        )
        if self._auto_fit:
            self.fit_width()
        if keep_scroll:
            self.verticalScrollBar().setValue(old_scroll)
        self._last_page = -2
        self._emit_page()

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
        self._place_overlays()
        self._emit_page()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._auto_fit = False
            factor = 1.15 ** (event.angleDelta().y() / 120)
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    # -- Dateien hineinziehen ---------------------------------------------

    def _set_drop_active(self, on: bool) -> None:
        if on != self._drop_active:
            self._drop_active = on
            self._apply_frame()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self._set_drop_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_drop_active(False)
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
    delete_pressed = Signal()

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
        font.setPointSize(9)
        for o in self._overlays:
            fill = QColor(self.color)
            fill.setAlpha(80 if o.highlighted else 28)
            outline = self.color.darker(160) if o.highlighted else self.color
            pen = QPen(outline, 4 if o.highlighted else 2)
            pen.setCosmetic(True)
            srect = self.pdf_rect_to_scene(o.page, o.rect)
            box = self.scene().addRect(srect, pen, QBrush(fill))
            self._overlay_items.append(box)
            if o.highlighted:
                # zweifarbige Kontur: sichtbar auf hellem wie dunklem Untergrund
                inner_pen = QPen(QColor("white"), 2, Qt.PenStyle.DashLine)
                inner_pen.setCosmetic(True)
                self._overlay_items.append(self.scene().addRect(srect, inner_pen))
            # Fähnchen außerhalb der linken oberen Ecke: verdeckt den Inhalt nicht
            tag = QGraphicsRectItem()
            tag.setBrush(
                QBrush(self.color.darker(130) if o.highlighted else self.color)
            )
            tag.setPen(QPen(Qt.PenStyle.NoPen))
            tag.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            tag.setOpacity(0.92)
            tag.setZValue(5)
            text = QGraphicsSimpleTextItem(o.label, tag)
            text.setFont(font)
            text.setBrush(QBrush(QColor("white")))
            tb = text.boundingRect()
            w, h = tb.width() + 10, tb.height() + 2
            tag.setRect(0, -h, w, h)
            text.setPos(5, -h + 1)
            tag.setPos(srect.topLeft())
            self.scene().addItem(tag)
            self._overlay_items.append(tag)

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
        self._show_idle_hint()

    def _show_idle_hint(self) -> None:
        if self.tool is Tool.FOUR_CLICK:
            self.set_hint("4-Klick-Modus: Klick 1/4 – setze die vier Ecken des Bereichs")
        else:
            self.set_hint(None)

    def _size_text(self, rect: QRectF, page: int) -> str:
        pdf = self.scene_rect_to_pdf(page, rect.normalized())
        return f"{pdf.width / MM:.0f} × {pdf.height / MM:.0f} mm"

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
        self._show_idle_hint()

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
            rubber_fill = QColor(self.color)
            rubber_fill.setAlpha(40)
            self._rubber = self.scene().addRect(
                QRectF(pt, pt), self._pen, QBrush(rubber_fill)
            )
            self.set_hint("Loslassen = Bereich übernehmen · Esc = abbrechen")
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
        if len(self._clicks) < 4:
            n = len(self._clicks)
            self.set_hint(
                f"Punkt {n}/4 gesetzt – weiter mit Punkt {n + 1} · Esc = abbrechen"
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
            rect = QRectF(self._drag_start, pt).normalized()
            self._rubber.setRect(rect)
            self.set_hint(
                f"{self._size_text(rect, self._drag_page)} · Loslassen = übernehmen · "
                "Esc = abbrechen"
            )
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
        elif event.key() == Qt.Key.Key_Delete:
            self.delete_pressed.emit()
        else:
            super().keyPressEvent(event)


class PreviewView(PagesView):
    """Vorschau der Ausgabe: Klick wählt die Aufgabe, die Auswahl wird umrahmt."""

    cell_clicked = Signal(int, float, float)  # Seite, x, y in PDF-Punkten

    def __init__(self, page_width_px: int, parent: QWidget | None = None) -> None:
        super().__init__(page_width_px, parent)
        self._highlight: QGraphicsRectItem | None = None

    def show_document(
        self, doc: pymupdf.Document | None, keep_scroll: bool = True
    ) -> None:
        """Rendert neu; die Umrahmung muss danach neu gesetzt werden."""
        self._highlight = None
        super().show_document(doc, keep_scroll)

    def set_highlight(self, page: int | None, rect: pymupdf.Rect | None) -> None:
        """Umrahmt einen Bereich der Ausgabe (None entfernt die Umrahmung)."""
        if self._highlight is not None and self._highlight.scene() is not None:
            self.scene().removeItem(self._highlight)
        self._highlight = None
        if page is None or rect is None or page >= len(self.page_items):
            return
        pen = QPen(QColor(ACCENT), 3)
        pen.setCosmetic(True)
        fill = QColor(ACCENT)
        fill.setAlpha(35)
        srect = self.pdf_rect_to_scene(page, rect)
        self._highlight = self.scene().addRect(srect, pen, QBrush(fill))
        self._highlight.setZValue(10)
        self.ensureVisible(srect, 20, 40)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.page_items:
            pt = self.mapToScene(event.position().toPoint())
            idx = self.page_at(pt)
            if idx is not None:
                pdf = self.scene_rect_to_pdf(idx, QRectF(pt, pt))
                self.cell_clicked.emit(idx, pdf.x0, pdf.y0)
        super().mousePressEvent(event)


class EmptyPane(QFrame):
    """Leerer Zustand einer Dokumentspalte: Anleitung, Buttons, Ablegefläche."""

    files_dropped = Signal(list)  # list[Path]
    browse_requested = Signal()
    paste_requested = Signal()

    def __init__(
        self, title: str, subtitle: str, color: QColor, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("emptyPane")
        self.setAcceptDrops(True)
        self._color = color.name()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        lay.addStretch(2)
        head = QLabel(title)
        font = head.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 4)
        head.setFont(font)
        head.setStyleSheet(f"color: {self._color};")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(head)
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(8)
        browse = QPushButton("Datei wählen …")
        browse.clicked.connect(self.browse_requested)
        paste = QPushButton("Aus Zwischenablage einfügen (Strg+V)")
        paste.clicked.connect(self.paste_requested)
        for btn in (browse, paste):
            btn.setMinimumHeight(32)
            lay.addWidget(btn)
        lay.addStretch(3)
        self._set_drop(False)

    def _set_drop(self, on: bool) -> None:
        style = "dashed" if on else "solid"
        color = ACCENT if on else self._color
        width = 3 if on else 2
        self.setStyleSheet(
            f"#emptyPane {{ border: {width}px {style} {color}; border-radius: 10px; }}"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self._set_drop(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._set_drop(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_drop(False)
        paths = [
            Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()
        ]
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
