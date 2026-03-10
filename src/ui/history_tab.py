"""
History tab – shows recent converter and alpha-fixer runs with timestamps.
"""
import datetime

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMessageBox,
    QTabWidget,
)


def _fmt_ts(ts: str) -> str:
    """Format an ISO timestamp for display, returning it unchanged on failure."""
    try:
        dt = datetime.datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d  %H:%M:%S")
    except (ValueError, TypeError):
        return ts


def _make_tree(columns: list[str]) -> QTreeWidget:
    """Build a standard history QTreeWidget with the given column headers."""
    tree = QTreeWidget()
    tree.setHeaderLabels(columns)
    for i in range(len(columns) - 1):
        tree.header().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
    tree.header().setSectionResizeMode(len(columns) - 1, QHeaderView.ResizeMode.Stretch)
    tree.setAlternatingRowColors(True)
    tree.setRootIsDecorated(False)
    return tree


class HistoryTab(QWidget):
    """View of the last 50 sessions for both the Converter and the Alpha Fixer."""

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hdr = QLabel("📋  Processing History")
        hdr.setObjectName("header")
        layout.addWidget(hdr)

        btn_row = QHBoxLayout()
        self._btn_refresh = QPushButton("🔄  Refresh")
        self._btn_clear = QPushButton("🗑  Clear All History")
        btn_row.addWidget(self._btn_refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_clear)
        layout.addLayout(btn_row)

        # Sub-tabs: Converter | Alpha Fixer
        self._sub_tabs = QTabWidget()

        # --- Converter sub-tab ---
        conv_widget = QWidget()
        conv_layout = QVBoxLayout(conv_widget)
        conv_layout.setContentsMargins(0, 6, 0, 0)
        self._conv_tree = _make_tree(
            ["Time", "Format", "Files", "✔ OK", "✘ Err", "File names (first 10)"]
        )
        conv_layout.addWidget(self._conv_tree)
        self._conv_summary = QLabel("")
        self._conv_summary.setObjectName("subheader")
        conv_layout.addWidget(self._conv_summary)
        self._sub_tabs.addTab(conv_widget, "🔄  Converter")

        # --- Alpha Fixer sub-tab ---
        alpha_widget = QWidget()
        alpha_layout = QVBoxLayout(alpha_widget)
        alpha_layout.setContentsMargins(0, 6, 0, 0)
        self._alpha_tree = _make_tree(
            ["Time", "Preset / Mode", "Files", "✔ OK", "✘ Err", "File names (first 10)"]
        )
        alpha_layout.addWidget(self._alpha_tree)
        self._alpha_summary = QLabel("")
        self._alpha_summary.setObjectName("subheader")
        alpha_layout.addWidget(self._alpha_summary)
        self._sub_tabs.addTab(alpha_widget, "🖼  Alpha Fixer")

        layout.addWidget(self._sub_tabs, 1)

        # Connections
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_clear.clicked.connect(self._clear_history)

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr) -> None:
        """Register History tab widgets with the TooltipManager."""
        mgr.register(self._btn_refresh, "history_refresh_btn")
        mgr.register(self._btn_clear, "history_clear_btn")

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refresh(self):
        """Reload both history lists from settings."""
        self._refresh_converter()
        self._refresh_alpha()

    def _refresh_converter(self):
        history = self._settings.get_converter_history()
        self._conv_tree.clear()
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            fmt = entry.get("format", "?")
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            files = ", ".join(entry.get("files", []))
            item = QTreeWidgetItem([ts, fmt, n_files, n_ok, n_err, files])
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._conv_tree.addTopLevelItem(item)
        total = len(history)
        self._conv_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else "")
        )

    def _refresh_alpha(self):
        history = self._settings.get_alpha_history()
        self._alpha_tree.clear()
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            preset = entry.get("preset", "?")
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            files = ", ".join(entry.get("files", []))
            item = QTreeWidgetItem([ts, preset, n_files, n_ok, n_err, files])
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._alpha_tree.addTopLevelItem(item)
        total = len(history)
        self._alpha_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else "")
        )

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear_history(self):
        reply = QMessageBox.question(
            self, "Clear History",
            "Delete all conversion and alpha-fixer history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._settings.clear_converter_history()
            self._settings.clear_alpha_history()
            self.refresh()
