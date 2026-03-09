"""
DropFileList – a QListWidget subclass that:
  • Accepts drag-and-drop of files / folders from the OS
  • Supports Delete key and right-click → Remove Selected
  • Emits paths_dropped(list[str]) so the parent can do dedup/counting
  • Emits count_changed(int) whenever the item count changes
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QListWidget, QMenu


class DropFileList(QListWidget):
    """QListWidget with built-in file drag-drop and remove support."""

    paths_dropped = pyqtSignal(list)   # list[str] – new paths dragged in
    count_changed = pyqtSignal(int)    # emitted after any add/remove

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    # ------------------------------------------------------------------
    # Drag-and-drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
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

        act_clear = QAction("Clear All", self)
        act_clear.setEnabled(self.count() > 0)
        act_clear.triggered.connect(self._clear_all)
        menu.addAction(act_clear)

        menu.exec(self.mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Remove helpers (can also be called externally)
    # ------------------------------------------------------------------

    def _remove_selected(self):
        items = self.selectedItems()
        if not items:
            return
        for item in items:
            self.takeItem(self.row(item))
        self.count_changed.emit(self.count())

    def _clear_all(self):
        if self.count() == 0:
            return
        super().clear()
        self.count_changed.emit(0)

    # Override clear() so external callers also get count_changed
    def clear(self):
        if self.count() == 0:
            super().clear()
            return
        super().clear()
        self.count_changed.emit(0)
