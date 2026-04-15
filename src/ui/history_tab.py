"""
History tab – shows recent converter and alpha-fixer runs with timestamps.
"""
import csv
import datetime
import io

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMessageBox,
    QTabWidget, QFileDialog, QLineEdit,
)


def _fmt_ts(ts: str) -> str:
    """Format an ISO timestamp for display, returning it unchanged on failure."""
    try:
        dt = datetime.datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d  %H:%M:%S")
    except (ValueError, TypeError):
        return ts


def _make_tree(columns: list[str], col_tips: list[str] | None = None) -> QTreeWidget:
    """Build a standard history QTreeWidget with the given column headers.

    If *col_tips* is provided it must have the same length as *columns*; each
    non-empty string is set as the tooltip for that column header section.
    """
    tree = QTreeWidget()
    tree.setHeaderLabels(columns)
    for i in range(len(columns) - 1):
        tree.header().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
    tree.header().setSectionResizeMode(len(columns) - 1, QHeaderView.ResizeMode.Stretch)
    if col_tips:
        header_item = tree.headerItem()
        for i, tip in enumerate(col_tips):
            if tip and header_item:
                header_item.setToolTip(i, tip)
    tree.setAlternatingRowColors(True)
    tree.setRootIsDecorated(False)
    tree.setSortingEnabled(True)
    tree.header().setSectionsClickable(True)
    tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
    return tree


class HistoryTab(QWidget):
    """View of the last 100 sessions for the Converter, Alpha & RGBA Adjuster, and Selective Alpha."""

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
        self._hdr = hdr
        layout.addWidget(hdr)

        btn_row = QHBoxLayout()
        self._btn_refresh = QPushButton("🔄  Refresh")
        self._btn_export = QPushButton("📤  Export History…")
        self._btn_clear = QPushButton("🗑  Clear All History")
        btn_row.addWidget(self._btn_refresh)
        btn_row.addWidget(self._btn_export)
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_clear)
        layout.addLayout(btn_row)

        # Sub-tabs: Converter | Alpha & RGBA Adjuster
        self._sub_tabs = QTabWidget()

        # --- Converter sub-tab ---
        conv_widget = QWidget()
        conv_layout = QVBoxLayout(conv_widget)
        conv_layout.setContentsMargins(0, 6, 0, 0)
        self._conv_search = self._make_search_field("converter")
        conv_layout.addWidget(self._conv_search)
        self._conv_tree = _make_tree(
            ["Time", "Format", "Files", "✔ OK", "✘ Err", "File names (first 10)"],
            col_tips=[
                "When the conversion batch was started.",
                "Output format chosen for this batch (e.g. PNG, WEBP, DDS).",
                "Total number of files submitted to the converter.",
                "Files that converted successfully.",
                "Files that failed — check the format/path if this is non-zero.",
                "First 10 input filenames in this batch.",
            ],
        )
        conv_layout.addWidget(self._conv_tree)
        self._conv_summary = QLabel("")
        self._conv_summary.setObjectName("subheader")
        conv_layout.addWidget(self._conv_summary)
        self._sub_tabs.addTab(conv_widget, "🔄  Converter")

        # --- Alpha & RGBA Adjuster sub-tab ---
        alpha_widget = QWidget()
        alpha_layout = QVBoxLayout(alpha_widget)
        alpha_layout.setContentsMargins(0, 6, 0, 0)
        self._alpha_search = self._make_search_field("alpha")
        alpha_layout.addWidget(self._alpha_search)
        self._alpha_tree = _make_tree(
            ["Time", "Preset / Mode", "Files", "✔ OK", "✘ Err", "File names (first 10)"],
            col_tips=[
                "When the alpha-fix batch was started.",
                "Preset or manual mode used for this batch.",
                "Total number of files processed.",
                "Files processed successfully.",
                "Files that encountered errors — may be unsupported format or locked file.",
                "First 10 input filenames in this batch.",
            ],
        )
        alpha_layout.addWidget(self._alpha_tree)
        self._alpha_summary = QLabel("")
        self._alpha_summary.setObjectName("subheader")
        alpha_layout.addWidget(self._alpha_summary)
        self._sub_tabs.addTab(alpha_widget, "🖼  Alpha & RGBA Adjuster")

        # --- Selective Alpha sub-tab ---
        sel_widget = QWidget()
        sel_layout = QVBoxLayout(sel_widget)
        sel_layout.setContentsMargins(0, 6, 0, 0)
        self._sel_search = self._make_search_field("selective")
        sel_layout.addWidget(self._sel_search)
        self._sel_tree = _make_tree(
            ["Time", "Mode", "Files", "✔ OK", "✘ Err", "File names (first 10)"],
            col_tips=[
                "When the selective-alpha batch was started.",
                "Zone / mode used for this batch.",
                "Total number of files processed.",
                "Files processed successfully.",
                "Files that encountered errors.",
                "First 10 input filenames in this batch.",
            ],
        )
        sel_layout.addWidget(self._sel_tree)
        self._sel_summary = QLabel("")
        self._sel_summary.setObjectName("subheader")
        sel_layout.addWidget(self._sel_summary)
        self._sub_tabs.addTab(sel_widget, "🎭  Selective Alpha")

        layout.addWidget(self._sub_tabs, 1)

        # Connections
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_export.clicked.connect(self._export_history)
        self._btn_clear.clicked.connect(self._clear_history)

        self._conv_search.textChanged.connect(
            lambda text: self._apply_filter(self._conv_tree, text)
        )
        self._alpha_search.textChanged.connect(
            lambda text: self._apply_filter(self._alpha_tree, text)
        )
        self._sel_search.textChanged.connect(
            lambda text: self._apply_filter(self._sel_tree, text)
        )

    # ------------------------------------------------------------------
    # Search / filter helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_search_field(name: str) -> QLineEdit:
        """Return a styled search QLineEdit for a history sub-tab."""
        field = QLineEdit()
        field.setObjectName(f"history_search_{name}")
        field.setPlaceholderText("🔍  Filter by time, format, file name…")
        field.setClearButtonEnabled(True)
        return field

    @staticmethod
    def _apply_filter(tree: QTreeWidget, text: str) -> None:
        """Show only rows whose text in any column contains *text* (case-insensitive)."""
        needle = text.strip().lower()
        root = tree.invisibleRootItem()
        for row in range(root.childCount()):
            item = root.child(row)
            if not needle:
                item.setHidden(False)
                continue
            row_text = " ".join(
                item.text(col) for col in range(tree.columnCount())
            ).lower()
            item.setHidden(needle not in row_text)

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr) -> None:
        """Register History tab widgets with the TooltipManager."""
        mgr.register(self._btn_refresh, "history_refresh_btn")
        mgr.register(self._btn_clear, "history_clear_btn")
        mgr.register(self._btn_export, "history_export_btn")
        mgr.register(self._sub_tabs.widget(0), "history_conv_sub")
        mgr.register(self._sub_tabs.widget(1), "history_alpha_sub")
        mgr.register(self._sub_tabs.widget(2), "history_sel_sub")
        mgr.register(self._conv_tree, "history_conv_tree")
        mgr.register(self._alpha_tree, "history_alpha_tree")
        mgr.register(self._sel_tree, "history_sel_tree")
        mgr.register(self._conv_summary, "history_conv_summary")
        mgr.register(self._alpha_summary, "history_alpha_summary")
        mgr.register(self._sel_summary, "history_sel_summary")
        mgr.register(self._conv_search, "history_search")
        mgr.register(self._alpha_search, "history_search")
        mgr.register(self._sel_search, "history_search")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def update_theme(self, theme_name: str) -> None:
        """Update the inner header and sub-tab labels to match the active theme."""
        from .theme_engine import get_theme_tab_labels, get_theme_icon
        labels = get_theme_tab_labels(theme_name)
        # labels[2] is e.g. "📋🐼  History" — extract the emoji prefix and
        # rebuild the descriptive inner header title.
        history_label = labels[2]
        prefix = history_label.split("  ", 1)[0] if "  " in history_label else "📋"
        self._hdr.setText(f"{prefix}  Processing History")
        # Decorate the converter/alpha-fixer sub-tab labels with the theme icon.
        icon = get_theme_icon(theme_name)
        self._sub_tabs.setTabText(0, f"{icon}🔄  Converter")
        self._sub_tabs.setTabText(1, f"{icon}🖼  Alpha & RGBA Adjuster")
        self._sub_tabs.setTabText(2, f"{icon}🎭  Selective Alpha")

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refresh(self):
        """Reload all three history lists from settings and reapply any active filters."""
        self._refresh_converter()
        self._refresh_alpha()
        self._refresh_selective_alpha()
        # Re-apply search filters so existing text still works after refresh.
        self._apply_filter(self._conv_tree, self._conv_search.text())
        self._apply_filter(self._alpha_tree, self._alpha_search.text())
        self._apply_filter(self._sel_tree, self._sel_search.text())

    def _refresh_converter(self):
        history = self._settings.get_converter_history()
        self._conv_tree.clear()
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            fmt = entry.get("format", "?")
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            file_list = entry.get("files", [])
            files = ", ".join(file_list)
            item = QTreeWidgetItem([ts, fmt, n_files, n_ok, n_err, files])
            if file_list:
                tooltip = (
                    f"Batch: {ts}\nFormat: {fmt}\n"
                    f"Total: {n_files}  OK: {n_ok}  Errors: {n_err}\n\n"
                    "Files processed (first 10):\n  " + "\n  ".join(file_list)
                )
                for col in range(6):
                    item.setToolTip(col, tooltip)
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._conv_tree.addTopLevelItem(item)
        total = len(history)
        self._conv_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else
               " — run the Converter to see history here.")
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
            file_list = entry.get("files", [])
            files = ", ".join(file_list)
            item = QTreeWidgetItem([ts, preset, n_files, n_ok, n_err, files])
            if file_list:
                tooltip = (
                    f"Batch: {ts}\nPreset / Mode: {preset}\n"
                    f"Total: {n_files}  OK: {n_ok}  Errors: {n_err}\n\n"
                    "Files processed (first 10):\n  " + "\n  ".join(file_list)
                )
                for col in range(6):
                    item.setToolTip(col, tooltip)
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._alpha_tree.addTopLevelItem(item)
        total = len(history)
        self._alpha_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else
               " — run the Alpha & RGBA Adjuster to see history here.")
        )

    def _refresh_selective_alpha(self):
        history = self._settings.get_selective_alpha_history()
        self._sel_tree.clear()
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            mode = entry.get("mode", entry.get("preset", "?"))
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            file_list = entry.get("files", [])
            files = ", ".join(file_list)
            item = QTreeWidgetItem([ts, mode, n_files, n_ok, n_err, files])
            if file_list:
                tooltip = (
                    f"Batch: {ts}\nMode: {mode}\n"
                    f"Total: {n_files}  OK: {n_ok}  Errors: {n_err}\n\n"
                    "Files processed (first 10):\n  " + "\n  ".join(file_list)
                )
                for col in range(6):
                    item.setToolTip(col, tooltip)
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._sel_tree.addTopLevelItem(item)
        total = len(history)
        self._sel_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else
               " — run the Selective Alpha tool to see history here.")
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
            self._settings.clear_selective_alpha_history()
            self.refresh()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_history(self) -> None:
        """Export the currently visible history sub-tab to a file.

        Supported formats (TXT default): Plain Text, CSV, JSON, HTML.
        """
        import json as _json

        # Determine which sub-tab is active
        tab_idx = self._sub_tabs.currentIndex()
        if tab_idx == 0:
            tree = self._conv_tree
            tab_name = "converter"
            headers = ["Time", "Format", "Files", "OK", "Errors", "File names (first 10)"]
        elif tab_idx == 1:
            tree = self._alpha_tree
            tab_name = "alpha_fixer"
            headers = ["Time", "Preset / Mode", "Files", "OK", "Errors", "File names (first 10)"]
        else:
            tree = self._sel_tree
            tab_name = "selective_alpha"
            headers = ["Time", "Mode", "Files", "OK", "Errors", "File names (first 10)"]

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export History",
            f"{tab_name}_history.txt",
            "Text Files (*.txt);;"
            "CSV Files (*.csv);;"
            "JSON Files (*.json);;"
            "HTML Files (*.html *.htm);;"
            "All Files (*)",
        )
        if not path:
            return

        # Collect rows from the tree
        root = tree.invisibleRootItem()
        rows = [
            [root.child(r).text(c) for c in range(tree.columnCount())]
            for r in range(root.childCount())
        ]

        ext = path.rsplit(".", 1)[-1].lower() if "." in path else "txt"

        try:
            if ext == "csv":
                self._export_csv(path, headers, rows)

            elif ext == "json":
                data = [dict(zip(headers, row)) for row in rows]
                with open(path, "w", encoding="utf-8") as f:
                    _json.dump(data, f, indent=2, ensure_ascii=False)

            elif ext in ("html", "htm"):
                th_cells = "".join(f"<th>{h}</th>" for h in headers)
                tr_rows = "".join(
                    "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                    for row in rows
                )
                content = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<style>table{border-collapse:collapse}th,td{border:1px solid #888;"
                    "padding:4px 8px;text-align:left}th{background:#333;color:#eee}"
                    "tr:nth-child(even){background:#f5f5f5}</style></head><body>"
                    f"<h2>{tab_name.replace('_', ' ').title()} History</h2>"
                    f"<table><thead><tr>{th_cells}</tr></thead><tbody>{tr_rows}</tbody></table>"
                    "</body></html>"
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            else:
                # Plain text (default)
                col_widths = [max(len(h), *(len(r[i]) for r in rows), 4)
                              for i, h in enumerate(headers)] if rows else [len(h) for h in headers]
                def _fmt_row(cells):
                    return "  ".join(c.ljust(w) for c, w in zip(cells, col_widths))
                sep = "-" * (sum(col_widths) + 2 * len(col_widths))
                lines = [_fmt_row(headers), sep] + [_fmt_row(r) for r in rows]
                content = "\n".join(lines) + "\n"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            QMessageBox.information(
                self, "Export Complete",
                f"History exported to:\n{path}",
            )
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not write file:\n{exc}")

    def _export_csv(self, path: str, headers: list, rows: list) -> None:
        """Write *rows* with *headers* to *path* as a CSV file.

        Uses ``io.StringIO`` as a context manager to guarantee the in-memory
        buffer is released even if the csv.writer raises.
        """
        with io.StringIO(newline="") as buf:
            writer = csv.writer(buf)
            writer.writerow(headers)
            writer.writerows(rows)
            content = buf.getvalue()
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write(content)
