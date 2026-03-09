"""
History tab – shows recent converter / alpha-fixer runs with timestamps.
"""
import datetime

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMessageBox,
)


class HistoryTab(QWidget):
    """Read-only view of the last 50 converter sessions."""

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hdr = QLabel("📋  Conversion History")
        hdr.setObjectName("header")
        layout.addWidget(hdr)

        btn_row = QHBoxLayout()
        self._btn_refresh = QPushButton("🔄  Refresh")
        self._btn_clear = QPushButton("🗑  Clear History")
        btn_row.addWidget(self._btn_refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_clear)
        layout.addLayout(btn_row)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Time", "Format", "Files", "✔ OK", "✘ Err", "File names (first 10)"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(False)
        layout.addWidget(self._tree, 1)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setObjectName("subheader")
        layout.addWidget(self._summary_lbl)

        # Connections
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_clear.clicked.connect(self._clear_history)

    @pyqtSlot()
    def refresh(self):
        """Reload history from settings and repopulate the tree."""
        history = self._settings.get_converter_history()
        self._tree.clear()

        for entry in history:
            ts = entry.get("timestamp", "")
            # Format timestamp for display
            try:
                dt = datetime.datetime.fromisoformat(ts)
                ts_display = dt.strftime("%Y-%m-%d  %H:%M:%S")
            except (ValueError, TypeError):
                ts_display = ts

            fmt = entry.get("format", "?")
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            files = ", ".join(entry.get("files", []))

            item = QTreeWidgetItem([ts_display, fmt, n_files, n_ok, n_err, files])

            # Colour error rows
            err_count = entry.get("errors", 0)
            if isinstance(err_count, int) and err_count > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)

            self._tree.addTopLevelItem(item)

        total = len(history)
        self._summary_lbl.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else "")
        )

    def _clear_history(self):
        reply = QMessageBox.question(
            self, "Clear History",
            "Delete all conversion history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._settings._qs.setValue("converter_history", "[]")
            self._settings._qs.sync()
            self.refresh()
