"""
DropFileList – a QListWidget subclass that:
  • Accepts drag-and-drop of files / folders from the OS
  • Supports Delete key and right-click → Remove Selected / Select All / Open Containing Folder
  • Emits paths_dropped(list[str]) so the parent can do dedup/counting
  • Emits count_changed(int) whenever the item count changes
  • Shows 56×56 thumbnails lazily (only for visible rows) via a background
    loader queue – capable of handling 50,000+ files without lag or crashes
"""
import os
import threading
from collections import OrderedDict
from typing import Dict, Optional

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize, QRunnable, QThreadPool,
    QObject, pyqtSlot,
)
from PyQt6.QtGui import QAction, QIcon, QPixmap, QImage, QPainter, QColor, QFont
from PyQt6.QtWidgets import QListWidget, QMenu, QApplication


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_THUMB_SIZE    = 56          # px – width & height of in-list thumbnail
_CACHE_MAX     = 600         # max thumbnails kept in memory
_BATCH_PER_TICK = 15         # max thumbs to enqueue per timer tick
_SCROLL_DEBOUNCE_MS = 220    # ms quiet time after scroll before loading thumbs
_LOAD_DEBOUNCE_MS   = 60     # ms between consecutive load ticks

# Thumbnail mode is disabled when the list has more items than this limit, to
# avoid excessive memory use.  The user can still toggle it off manually.
_THUMB_AUTO_DISABLE = 3000


# ---------------------------------------------------------------------------
# Thumbnail worker (runs one image per Runnable in a shared QThreadPool)
# ---------------------------------------------------------------------------

class _ThumbSignals(QObject):
    loaded = pyqtSignal(str, QIcon)  # path, icon


class _ThumbRunnable(QRunnable):
    """QRunnable that loads one thumbnail and emits a signal when done.

    *cancel* is a :class:`threading.Event` shared with the owning
    :class:`DropFileList`.  If the list is cleared while this runnable is
    queued or running, the event is set and the runnable bails out early
    without emitting, saving CPU and avoiding work for items that no longer
    exist.

    *thumb_px* is the physical pixel size of the thumbnail cell (logical
    size × device-pixel-ratio).  The resulting pixmap has its
    ``devicePixelRatio`` set so Qt scales it correctly on HiDPI displays.
    """

    def __init__(self, path: str, signals: _ThumbSignals,
                 cancel: threading.Event, thumb_px: int = _THUMB_SIZE,
                 dpr: float = 1.0):
        super().__init__()
        self._path = path
        self._signals = signals
        self._cancel = cancel
        self._thumb_px = thumb_px
        self._dpr = dpr
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        # Bail out immediately if the list was cleared before we started.
        if self._cancel.is_set():
            return
        img = None
        try:
            from PIL import Image
            img = Image.open(self._path)
            img.thumbnail((self._thumb_px, self._thumb_px), Image.LANCZOS)
            if img.mode == "RGBA":
                data = img.tobytes("raw", "RGBA")
                qimg = QImage(data, img.width, img.height,
                              QImage.Format.Format_RGBA8888)
            else:
                # Convert to RGB and track the new image so the finally block
                # closes it; the original opened image (img) is reassigned.
                converted = img.convert("RGB")
                img.close()
                img = converted
                data = img.tobytes("raw", "RGB")
                qimg = QImage(data, img.width, img.height,
                              QImage.Format.Format_RGB888)
            # Scale the image to fit within the physical thumbnail cell while
            # preserving its aspect ratio, then letterbox it into an exact
            # _thumb_px square transparent pixmap so Qt never stretches it.
            phys = self._thumb_px
            scaled = QPixmap.fromImage(qimg).scaled(
                phys, phys,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            out = QPixmap(phys, phys)
            out.fill(Qt.GlobalColor.transparent)
            painter = QPainter(out)
            if painter.isActive():
                painter.drawPixmap(
                    (phys - scaled.width()) // 2,
                    (phys - scaled.height()) // 2,
                    scaled,
                )
                painter.end()
            # Tag the pixmap with the device-pixel ratio so Qt renders it at
            # the correct logical size on HiDPI / Retina displays.
            out.setDevicePixelRatio(self._dpr)
            icon = QIcon(out)
            # Final cancel check before emitting so we don't deliver the icon
            # to a list that was cleared while the thumbnail was being built.
            if not self._cancel.is_set():
                self._signals.loaded.emit(self._path, icon)
        except Exception:
            pass  # silently skip unreadable / non-image files
        finally:
            if img is not None:
                img.close()


# ---------------------------------------------------------------------------
# DropFileList
# ---------------------------------------------------------------------------

class DropFileList(QListWidget):
    """QListWidget with built-in file drag-drop, remove support, and lazy
    thumbnails.  Tested up to 50,000 entries without UI lag."""

    paths_dropped = pyqtSignal(list)   # list[str] – new paths dragged in
    count_changed = pyqtSignal(int)    # emitted after any add/remove
    file_removed  = pyqtSignal()       # emitted when items are explicitly removed by the user
    drag_entered  = pyqtSignal()       # emitted when files are first dragged over the list

    # Icon shown in the centre of the list when no files have been added yet
    _EMPTY_STATE_ICON = "📂"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setUniformItemSizes(True)   # critical for scroll performance
        self.setMinimumHeight(160)

        # Compute DPI-aware thumbnail dimensions once at construction time.
        # Qt uses logical pixels for setIconSize, but the pixmap backing each
        # icon must be created at physical pixels (logical × DPR) so thumbnails
        # stay sharp on HiDPI / Retina displays.
        self._dpr: float = self._current_dpr()
        self._thumb_px: int = max(1, round(_THUMB_SIZE * self._dpr))

        # Thumbnail state
        self._thumb_enabled: bool = True
        self._thumb_cache: OrderedDict[str, QIcon] = OrderedDict()
        self._pending: set[str] = set()   # paths currently being loaded
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(max(2, self._pool.maxThreadCount() // 2))

        # Shared signals object (must live on the main thread)
        self._signals = _ThumbSignals()
        self._signals.loaded.connect(self._on_thumb_loaded)

        # Cancellation event shared with all _ThumbRunnable tasks.  When the
        # list is cleared, we retire the current event (set it so running
        # runnables bail out) and create a fresh one for future requests.
        self._cancel_event: threading.Event = threading.Event()

        # Scroll-debounce timer: wait for user to stop scrolling
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(_SCROLL_DEBOUNCE_MS)
        self._scroll_timer.timeout.connect(self._load_visible_thumbs)

        # Periodic tick to drip-feed items that need thumbnails
        self._load_tick = QTimer(self)
        self._load_tick.setInterval(_LOAD_DEBOUNCE_MS)
        self._load_tick.timeout.connect(self._load_visible_thumbs)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Set icon size (logical pixels – Qt scales to physical automatically)
        self.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))

    @staticmethod
    def _current_dpr() -> float:
        """Return the device-pixel ratio of the primary screen (≥ 1.0)."""
        screen = QApplication.primaryScreen()
        return screen.devicePixelRatio() if screen is not None else 1.0

    def changeEvent(self, event) -> None:  # noqa: N802
        """Refresh DPI-dependent thumbnail dimensions when the screen changes.

        Qt fires ``QEvent.Type.ScreenChangeInternal`` when the widget moves
        to a monitor with a different device-pixel ratio or when the user
        changes the system display-scale setting.  We invalidate the thumbnail
        cache and recalculate the physical pixel size so newly-loaded thumbs
        are sharp on the new display.
        """
        super().changeEvent(event)
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.ScreenChangeInternal:
            new_dpr = self._current_dpr()
            if new_dpr != self._dpr:
                self._dpr = new_dpr
                self._thumb_px = max(1, round(_THUMB_SIZE * self._dpr))
                # Flush cached icons – they were built for the old DPR.
                self._thumb_cache.clear()
                # Re-queue visible rows so fresh sharp icons are loaded.
                QTimer.singleShot(200, self._load_visible_thumbs)

    # ------------------------------------------------------------------
    # Empty-state hint overlay
    # ------------------------------------------------------------------

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.viewport().rect()

            # Big icon
            icon_font = QFont(painter.font())
            icon_font.setPointSize(28)
            painter.setFont(icon_font)
            painter.setPen(QColor(128, 128, 128, 80))
            painter.drawText(
                rect.adjusted(0, 0, 0, -30),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                self._EMPTY_STATE_ICON,
            )

            # Hint text
            hint_font = QFont(painter.font())
            hint_font.setPointSize(9)
            painter.setFont(hint_font)
            painter.setPen(QColor(128, 128, 128, 120))
            painter.drawText(
                rect.adjusted(0, 40, 0, 0),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                "Drop files or folders here\nor use the Add Files button",
            )
            painter.end()

    # ------------------------------------------------------------------
    # Thumbnail helpers
    # ------------------------------------------------------------------

    def set_thumbnails_enabled(self, enabled: bool) -> None:
        """Enable or disable thumbnail loading."""
        self._thumb_enabled = enabled
        if enabled:
            self._scroll_timer.start()
        else:
            self._load_tick.stop()

    def _on_scroll(self, _value: int) -> None:
        if self._thumb_enabled:
            self._scroll_timer.start()  # restart debounce

    def _load_visible_thumbs(self) -> None:
        """Enqueue thumbnail loads for currently visible rows."""
        if not self._thumb_enabled:
            return
        # Auto-disable thumbnails when list is very large to prevent RAM spikes
        if self.count() > _THUMB_AUTO_DISABLE:
            self._load_tick.stop()
            return

        rect = self.viewport().rect()
        loaded_any = False
        batch = 0
        for row in range(self.count()):
            if batch >= _BATCH_PER_TICK:
                break
            item = self.item(row)
            if item is None:
                continue
            vis_rect = self.visualItemRect(item)
            if not rect.intersects(vis_rect):
                continue
            path = item.text()
            if not path or path in self._pending:
                continue
            if path in self._thumb_cache:
                if item.icon().isNull():
                    item.setIcon(self._thumb_cache[path])
                continue
            # Kick off background load
            self._pending.add(path)
            runnable = _ThumbRunnable(path, self._signals, self._cancel_event,
                                       thumb_px=self._thumb_px, dpr=self._dpr)
            self._pool.start(runnable)
            batch += 1
            loaded_any = True

        if loaded_any:
            self._load_tick.start()
        else:
            self._load_tick.stop()

    @pyqtSlot(str, QIcon)
    def _on_thumb_loaded(self, path: str, icon: QIcon) -> None:
        """Called from _ThumbSignals (main thread) when a thumbnail is ready."""
        self._pending.discard(path)

        # Evict oldest entry if cache is full
        if len(self._thumb_cache) >= _CACHE_MAX:
            evicted_path, _ = self._thumb_cache.popitem(last=False)
            # Clear the icon from any existing list item to free memory
            for row in range(self.count()):
                item = self.item(row)
                if item and item.text() == evicted_path:
                    item.setIcon(QIcon())
                    break

        self._thumb_cache[path] = icon
        self._thumb_cache.move_to_end(path)  # mark as recently used

        # Apply to matching list item(s)
        for row in range(self.count()):
            item = self.item(row)
            if item and item.text() == path:
                item.setIcon(icon)
                break  # paths are unique

    # ------------------------------------------------------------------
    # Public batch-add helper (avoids UI freeze for large imports)
    # ------------------------------------------------------------------

    def add_paths_batch(self, paths: list[str]) -> int:
        """Add paths in batches, processing events periodically so the UI
        stays responsive when adding tens of thousands of files."""
        existing = {self.item(i).text() for i in range(self.count())}
        added = 0
        CHUNK = 500
        for start in range(0, len(paths), CHUNK):
            chunk = paths[start:start + CHUNK]
            for p in chunk:
                if p not in existing:
                    self.addItem(p)
                    existing.add(p)
                    added += 1
            if added and start > 0 and start % 5000 == 0:
                QApplication.processEvents()
        if added:
            self.count_changed.emit(self.count())
            if self._thumb_enabled and self.count() <= _THUMB_AUTO_DISABLE:
                self._scroll_timer.start()
        return added

    # ------------------------------------------------------------------
    # Drag-and-drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.setProperty("drag_active", True)
            self.style().unpolish(self)
            self.style().polish(self)
            self.drag_entered.emit()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setProperty("drag_active", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.setProperty("drag_active", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            if paths:
                self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self._remove_selected()
        elif (event.key() == Qt.Key.Key_A and
              event.modifiers() == Qt.KeyboardModifier.ControlModifier):
            self.selectAll()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        act_remove = QAction("🗑  Remove Selected", self)
        act_remove.setEnabled(bool(self.selectedItems()))
        act_remove.triggered.connect(self._remove_selected)
        menu.addAction(act_remove)

        menu.addSeparator()

        # Select-all / Deselect-all
        act_select_all = QAction("Select All", self)
        act_select_all.setShortcut("Ctrl+A")
        act_select_all.setEnabled(self.count() > 0)
        act_select_all.triggered.connect(self.selectAll)
        menu.addAction(act_select_all)

        act_deselect = QAction("Deselect All", self)
        act_deselect.setEnabled(bool(self.selectedItems()))
        act_deselect.triggered.connect(self.clearSelection)
        menu.addAction(act_deselect)

        menu.addSeparator()

        act_clear = QAction("Clear All", self)
        act_clear.setEnabled(self.count() > 0)
        act_clear.triggered.connect(self._clear_all)
        menu.addAction(act_clear)

        # Open containing folder for single selection
        selected = self.selectedItems()
        if len(selected) == 1:
            menu.addSeparator()
            act_open = QAction("📂  Open Containing Folder", self)
            act_open.triggered.connect(lambda: self._open_containing_folder(selected[0].text()))
            menu.addAction(act_open)

        menu.addSeparator()

        act_thumbs = QAction(
            "✓ Thumbnails" if self._thumb_enabled else "  Thumbnails",
            self,
        )
        act_thumbs.setCheckable(True)
        act_thumbs.setChecked(self._thumb_enabled)
        act_thumbs.triggered.connect(self._toggle_thumbs)
        menu.addAction(act_thumbs)

        menu.exec(self.mapToGlobal(pos))

    def _toggle_thumbs(self) -> None:
        self.set_thumbnails_enabled(not self._thumb_enabled)
        if not self._thumb_enabled:
            # Clear all icons to free memory
            for row in range(self.count()):
                item = self.item(row)
                if item:
                    item.setIcon(QIcon())
            self._thumb_cache.clear()
            self._pending.clear()

    # ------------------------------------------------------------------
    # Remove helpers (can also be called externally)
    # ------------------------------------------------------------------

    def _open_containing_folder(self, path: str) -> None:
        """Open the folder containing *path* in the OS file manager."""
        import subprocess
        import sys
        abs_path = os.path.abspath(path)
        folder = os.path.dirname(abs_path)
        try:
            if sys.platform == "win32":
                # Highlight the specific file in Explorer
                subprocess.run(
                    ["explorer", "/select,", abs_path],
                    check=False, timeout=5,
                )
            elif sys.platform == "darwin":
                subprocess.run(
                    ["open", "-R", abs_path],
                    check=False, timeout=5,
                )
            else:
                subprocess.run(
                    ["xdg-open", folder],
                    check=False, timeout=5,
                )
        except Exception:
            pass

    def _remove_selected(self):
        items = self.selectedItems()
        if not items:
            return
        for item in items:
            path = item.text()
            self._thumb_cache.pop(path, None)
            self._pending.discard(path)
            self.takeItem(self.row(item))
        self.count_changed.emit(self.count())
        self.file_removed.emit()

    def _clear_all(self):
        if self.count() == 0:
            return
        self._thumb_cache.clear()
        self._pending.clear()
        self._load_tick.stop()
        # Cancel any runnables that are still queued or running so they don't
        # waste CPU decoding thumbnails for items that no longer exist.
        # Create a fresh event for the next batch of thumbnail requests.
        self._cancel_event.set()
        self._cancel_event = threading.Event()
        super().clear()
        self.count_changed.emit(0)

    # Override clear() so external callers also get count_changed
    def clear(self):
        if self.count() == 0:
            return
        self._thumb_cache.clear()
        self._pending.clear()
        self._load_tick.stop()
        # Same cancellation as _clear_all for consistency.
        self._cancel_event.set()
        self._cancel_event = threading.Event()
        super().clear()
        self.count_changed.emit(0)
