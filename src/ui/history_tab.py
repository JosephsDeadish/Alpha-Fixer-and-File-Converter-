"""
History tab – shows recent converter and alpha-fixer runs with timestamps.
"""
import csv
import datetime
import fnmatch
import html
import io
import os
import re
import shlex
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QSize, QRect, pyqtSlot, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMessageBox,
    QTabWidget, QFileDialog, QLineEdit, QStyledItemDelegate, QStyleOptionViewItem,
)

_THUMB_SIZE = 32  # thumbnail icon size (pixels, square)


def _history_capability_summary() -> str:
    return (
        "Ready: browse, filter, preview, and export converter, alpha, GIF Builder, Video Builder, and Alpha Painter history."
    )


def _history_capability_has_limits() -> bool:
    return False


def _history_capability_details() -> str:
    return "\n".join(
        [
            _history_capability_summary(),
            "",
            "History tools available here:",
            "• Filter across visible columns, status, notes, file names, and field-specific queries.",
            "• Export the current sub-tab to CSV, JSON, HTML, or plain text.",
            "• Preview thumbnails appear when stored source/output media is still available locally.",
        ]
    )


class _HistoryItem(QTreeWidgetItem):
    """Tree item that sorts the time column using stored raw timestamp data."""

    _SORT_ROLE = Qt.ItemDataRole.UserRole + 2
    _FILTER_ROLE = Qt.ItemDataRole.UserRole + 3
    _FILTER_FIELDS_ROLE = Qt.ItemDataRole.UserRole + 4

    def __lt__(self, other) -> bool:
        tree = self.treeWidget()
        if tree is not None and tree.sortColumn() == 0:
            left = self.data(0, self._SORT_ROLE) or self.text(0)
            right = other.data(0, self._SORT_ROLE) or other.text(0)
            return str(left) < str(right)
        return super().__lt__(other)


class _AnimatedGifDelegate(QStyledItemDelegate):
    """Item delegate that shows animated .gif thumbnails in column 0 (item 80).

    For each GIF builder history entry, a ``QMovie`` is created and started.
    A shared timer repaints the viewport at ~12 fps so all animations run
    smoothly without per-movie signal wiring.
    """

    _GIF_PATH_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, tree: QTreeWidget, parent=None):
        super().__init__(parent)
        self._tree = tree
        self._movies: dict[str, "QMovie"] = {}
        # Repaint viewport at ~12 fps while any GIF is loaded
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(80)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def set_gif_path(self, item: QTreeWidgetItem, path: str) -> None:
        """Store *path* on *item* and start a QMovie for .gif files."""
        item.setData(0, self._GIF_PATH_ROLE, path)
        if path and path.lower().endswith(".gif") and os.path.isfile(path):
            if path not in self._movies:
                try:
                    from PyQt6.QtGui import QMovie
                    m = QMovie(path, parent=self)
                    m.setScaledSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
                    m.start()
                    self._movies[path] = m
                except Exception:
                    pass

    def clear_movies(self) -> None:
        """Stop and discard all loaded movies (call before rebuilding the tree)."""
        for m in self._movies.values():
            try:
                m.stop()
            except Exception:
                pass
        self._movies.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        if self._movies:
            try:
                self._tree.viewport().update()
            except Exception:
                pass

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:
        if index.column() != 0:
            super().paint(painter, option, index)
            return
        item = self._tree.itemFromIndex(index)
        gif_path = item.data(0, self._GIF_PATH_ROLE) if item is not None else None
        movie = self._movies.get(gif_path) if gif_path else None
        if movie is None:
            super().paint(painter, option, index)
            return
        # Draw background + text as normal, but skip the static icon.
        opt = QStyleOptionViewItem(option)
        opt.icon = QIcon()
        super().paint(painter, opt, index)
        # Overlay the animated frame in the icon rect
        frame = movie.currentPixmap()
        if not frame.isNull():
            icon_size = self._tree.iconSize()
            r = option.rect
            y_off = max(0, (r.height() - icon_size.height()) // 2)
            dst = QRect(r.left() + 2, r.top() + y_off, icon_size.width(), icon_size.height())
            scaled = frame.scaled(
                icon_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = dst.left() + max(0, (dst.width() - scaled.width()) // 2)
            y = dst.top() + max(0, (dst.height() - scaled.height()) // 2)
            painter.drawPixmap(x, y, scaled)


def _fmt_ts(ts: str) -> str:
    """Format an ISO timestamp for display, returning it unchanged on failure."""
    try:
        dt = datetime.datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d  %H:%M:%S")
    except (ValueError, TypeError):
        return ts


def _set_filter_text(item: QTreeWidgetItem, *parts) -> None:
    tokens: list[str] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            tokens.extend(str(value) for value in part if str(value or "").strip())
        else:
            text = str(part or "").strip()
            if text:
                tokens.append(text)
    item.setData(0, _HistoryItem._FILTER_ROLE, " ".join(tokens).lower())


def _set_filter_fields(item: QTreeWidgetItem, **fields) -> None:
    normalized: dict[str, str] = {}
    for key, value in fields.items():
        if isinstance(value, (list, tuple, set)):
            text = " ".join(str(part).strip() for part in value if str(part or "").strip())
        else:
            text = str(value or "").strip()
        if text:
            normalized[str(key).strip().lower()] = text.lower()
    item.setData(0, _HistoryItem._FILTER_FIELDS_ROLE, normalized)


_FILTER_FIELD_ALIASES = {
    "out": "output",
    "path": "output",
    "note": "notes",
    "files": "file",
    "name": "file",
    "kind": "type",
    "sources": "source",
    "src": "source",
    "err": "errors",
    "error": "errors",
    "ok": "success",
    "successes": "success",
    "fmt": "format",
    "frame": "frames",
    "clip": "clips",
    "largestframe": "largest",
    "alphas": "alpha",
    "resolution": "canvas",
    "res": "canvas",
    "dim": "canvas",
    "ms": "delay",
    "stream": "streams",
}
_FILTER_COMPARATORS = (">=", "<=", ">", "<", "=")
_FILTER_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _filter_tokens(text: str) -> list[str]:
    try:
        return [token.casefold() for token in shlex.split(text) if token.strip()]
    except ValueError:
        return [token.casefold() for token in text.split() if token.strip()]


def _field_filter_groups(text: str) -> tuple[list[str], dict[str, list[str]]]:
    free_text: list[str] = []
    grouped: dict[str, list[str]] = {}
    for token in _filter_tokens(text):
        if ":" not in token:
            free_text.append(token)
            continue
        raw_key, raw_value = token.split(":", 1)
        key = _FILTER_FIELD_ALIASES.get(raw_key, raw_key)
        value = raw_value.strip()
        if not key or not value:
            continue
        grouped.setdefault(key, []).append(value)
    return free_text, grouped


def _numeric_filter_values(text: str) -> list[float]:
    values: list[float] = []
    for match in _FILTER_NUMBER_RE.findall(str(text or "")):
        try:
            values.append(float(match))
        except ValueError:
            continue
    return values


def _matches_numeric_filter(haystack: str, option: str) -> bool:
    candidate_values = _numeric_filter_values(haystack)
    if not candidate_values:
        return False
    operator = "="
    operand = option.strip()
    for prefix in _FILTER_COMPARATORS:
        if operand.startswith(prefix):
            operator = prefix
            operand = operand[len(prefix):].strip()
            break
    query_values = _numeric_filter_values(operand)
    if not query_values:
        return False
    query = query_values[0]
    if operator == ">=":
        return any(value >= query for value in candidate_values)
    if operator == "<=":
        return any(value <= query for value in candidate_values)
    if operator == ">":
        return any(value > query for value in candidate_values)
    if operator == "<":
        return any(value < query for value in candidate_values)
    return any(abs(value - query) < 1e-9 for value in candidate_values)


def _matches_field_filter(haystack: str, value: str) -> bool:
    text = str(haystack or "")
    for option in (part.strip() for part in value.split("|")):
        if not option:
            continue
        if "*" in option or "?" in option:
            if fnmatch.fnmatch(text, option):
                return True
            continue
        if (
            option[0] in "><="
            or (_numeric_filter_values(option) and _numeric_filter_values(text))
        ) and _matches_numeric_filter(text, option):
            return True
        if option in text:
            return True
    return False


def _load_thumb(path: str) -> QIcon:
    """Return a small QIcon thumbnail for *path*, or an empty QIcon on failure (item 9)."""
    try:
        if not path or not os.path.isfile(path):
            return QIcon()
        px = QPixmap(path)
        if px.isNull():
            # Try Pillow for formats Qt cannot decode directly (e.g. DDS, TGA).
            try:
                from PIL import Image as _PILImage
                from PIL.ImageQt import ImageQt
                img = _PILImage.open(path)
                rgba = img.convert("RGBA")
                try:
                    rgba.thumbnail((_THUMB_SIZE * 2, _THUMB_SIZE * 2))
                    px = QPixmap.fromImage(ImageQt(rgba))
                finally:
                    rgba.close()
                    img.close()
            except Exception:
                return QIcon()
        if px.isNull():
            return QIcon()
        scaled = px.scaled(
            _THUMB_SIZE, _THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return QIcon(scaled)
    except Exception:
        return QIcon()


def _make_tree(columns: list[str], col_tips: list[str] | None = None) -> QTreeWidget:
    """Build a standard history QTreeWidget with the given column headers.

    If *col_tips* is provided it must have the same length as *columns*; each
    non-empty string is set as the tooltip for that column header section.
    """
    from PyQt6.QtCore import QSize
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
    tree.setSortingEnabled(False)
    tree.header().setSectionsClickable(True)
    tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
    # Allow thumbnail icons to show at full size (item 9)
    tree.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
    return tree


def _apply_default_sort(tree: QTreeWidget) -> None:
    """Apply the default newest-first sort and keep the header state in sync."""
    tree.sortItems(0, Qt.SortOrder.DescendingOrder)
    tree.header().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
    tree.setSortingEnabled(True)


def _set_history_tooltip(item: QTreeWidgetItem, columns: int, base_text: str, file_list: list[str]) -> None:
    """Apply a tooltip to every visible history cell, appending file names when present."""
    tooltip = base_text
    if file_list:
        tooltip += "\n\nFiles processed:\n  " + "\n  ".join(file_list)
    for col in range(columns):
        item.setToolTip(col, tooltip)


def _set_builder_tooltip(item: QTreeWidgetItem, columns: int, base_text: str, file_list: list[str]) -> None:
    """Apply a tooltip to every builder-history cell, appending input files when present."""
    tooltip = base_text
    if file_list:
        tooltip += "\n\nInput files:\n  " + "\n  ".join(file_list)
    for col in range(columns):
        item.setToolTip(col, tooltip)


def _history_next_step_text(total: int, visible: int, filter_text: str) -> str:
    if total <= 0:
        return "Next step: run a tool or export from a builder to populate history here."
    if filter_text and visible <= 0:
        return "Next step: clear or relax the current filter to bring matching history entries back into view."
    if filter_text and visible < total:
        return "Next step: review the filtered status/notes results, then clear the filter or export the current view."
    return "Next step: review status and notes details, export the current view, or switch history sub-tabs."


def _video_history_status(errors: object, notes: str) -> str:
    try:
        err_count = int(errors or 0)
    except Exception:
        err_count = 0
    lower_notes = notes.lower()
    has_recovery = "fallback" in lower_notes or "recovery" in lower_notes
    if err_count > 0 and has_recovery:
        return "Partial / recovery"
    if err_count > 0:
        return "Issues"
    if has_recovery:
        return "Recovery"
    return "OK"


def _gif_history_status(errors: object) -> str:
    try:
        return "Issues" if int(errors or 0) > 0 else "OK"
    except Exception:
        return "OK"


def _history_status_mix(statuses: list[str]) -> str:
    if not statuses:
        return ""
    order = ("OK", "Recovery", "Partial / recovery", "Issues")
    counts: dict[str, int] = {}
    for status in statuses:
        key = str(status or "").strip() or "Unknown"
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{label} {counts[label]}" for label in order if counts.get(label)]
    parts.extend(
        f"{label} {count}"
        for label, count in counts.items()
        if label not in order
    )
    return "  •  ".join(parts)


class HistoryTab(QWidget):
    """History view for Converter, Alpha, Selective Alpha, GIF Builder, and Video Builder runs."""
    queue_status_changed = pyqtSignal(str)

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._setup_ui()
        self.refresh()
        self._refresh_session_status()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hdr = QLabel("📋  Processing History")
        hdr.setObjectName("header")
        self._hdr = hdr
        layout.addWidget(hdr)

        # Hint pointing users to where settings live (item 8)
        hint = QLabel("⚙  History settings are in  Settings → General → History")
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(hint)

        self._capability_lbl = QLabel(_history_capability_summary())
        self._capability_lbl.setWordWrap(True)
        self._capability_lbl.setStyleSheet(
            "color: #b26a00; font-size: 11px;"
            if _history_capability_has_limits()
            else "color: #2e7d32; font-size: 11px;"
        )
        self._capability_lbl.setToolTip(_history_capability_details())
        layout.addWidget(self._capability_lbl)

        self._session_status_lbl = QLabel("")
        self._session_status_lbl.setWordWrap(True)
        self._session_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._session_status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._session_status_lbl)
        self._next_step_lbl = QLabel("Next step: run a tool or export from a builder to populate history here.")
        self._next_step_lbl.setWordWrap(True)
        self._next_step_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next_step_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._next_step_lbl)

        btn_row = QHBoxLayout()
        self._btn_export = QPushButton("📤  Export History…")
        self._btn_clear = QPushButton("🗑  Clear All History")
        btn_row.addWidget(self._btn_export)
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_clear)
        layout.addLayout(btn_row)

        # Sub-tabs: Converter | Alpha & RGBA Adjuster | Selective Alpha
        self._sub_tabs = QTabWidget()
        self._sub_tabs.setUsesScrollButtons(True)
        self._sub_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self._sub_tabs.tabBar().setExpanding(False)

        # --- Converter sub-tab ---
        conv_widget = QWidget()
        conv_layout = QVBoxLayout(conv_widget)
        conv_layout.setContentsMargins(0, 6, 0, 0)
        self._conv_search = self._make_search_field("converter")
        conv_layout.addWidget(self._conv_search)
        self._conv_tree = _make_tree(
            ["Time", "Format", "Files", "✔ OK", "✘ Err", "File names"],
            col_tips=[
                "When the conversion batch was started.",
                "Output format chosen for this batch (e.g. PNG, WEBP, DDS).",
                "Total number of files submitted to the converter.",
                "Files that converted successfully.",
                "Files that failed — check the format/path if this is non-zero.",
                "Input filenames in this batch.",
            ],
        )
        conv_layout.addWidget(self._conv_tree)
        self._conv_summary = QLabel("")
        self._conv_summary.setObjectName("subheader")
        conv_layout.addWidget(self._conv_summary)
        self._sub_tabs.addTab(conv_widget, "🔄 Converter")
        self._sub_tabs.tabBar().setTabToolTip(self._sub_tabs.indexOf(conv_widget), "Converter history")

        # --- Alpha & RGBA Adjuster sub-tab ---
        alpha_widget = QWidget()
        alpha_layout = QVBoxLayout(alpha_widget)
        alpha_layout.setContentsMargins(0, 6, 0, 0)
        self._alpha_search = self._make_search_field("alpha")
        alpha_layout.addWidget(self._alpha_search)
        self._alpha_tree = _make_tree(
            ["Time", "Mode", "Files", "✔ OK", "✘ Err", "File names"],
            col_tips=[
                "When the alpha-fix batch was started.",
                "Preset / mode used for this batch.",
                "Total number of files processed.",
                "Files processed successfully.",
                "Files that encountered errors — may be unsupported format or locked file.",
                "Input filenames in this batch.",
            ],
        )
        alpha_layout.addWidget(self._alpha_tree)
        self._alpha_summary = QLabel("")
        self._alpha_summary.setObjectName("subheader")
        alpha_layout.addWidget(self._alpha_summary)
        self._sub_tabs.addTab(alpha_widget, "🖼  Alpha & RGBA")
        self._sub_tabs.tabBar().setTabToolTip(self._sub_tabs.indexOf(alpha_widget), "Alpha & RGBA Adjuster history")

        # --- Selective Alpha sub-tab ---
        sel_widget = QWidget()
        sel_layout = QVBoxLayout(sel_widget)
        sel_layout.setContentsMargins(0, 6, 0, 0)
        self._sel_search = self._make_search_field("selective")
        sel_layout.addWidget(self._sel_search)
        self._sel_tree = _make_tree(
            ["Time", "Mode", "Files", "✔ OK", "✘ Err", "File names"],
            col_tips=[
                "When the selective-alpha batch was started.",
                "Zone / mode used for this batch.",
                "Total number of files processed.",
                "Files processed successfully.",
                "Files that encountered errors.",
                "Input filenames in this batch.",
            ],
        )
        sel_layout.addWidget(self._sel_tree)
        self._sel_summary = QLabel("")
        self._sel_summary.setObjectName("subheader")
        sel_layout.addWidget(self._sel_summary)
        self._sub_tabs.addTab(sel_widget, "🎭  Alpha Painter")
        self._sub_tabs.tabBar().setTabToolTip(self._sub_tabs.indexOf(sel_widget), "Alpha Painter history")

        # --- GIF Builder sub-tab (item 74) ---
        gif_widget = QWidget()
        gif_layout = QVBoxLayout(gif_widget)
        gif_layout.setContentsMargins(0, 6, 0, 0)
        self._gif_search = self._make_search_field("gif")
        gif_layout.addWidget(self._gif_search)
        self._gif_tree = _make_tree(
            ["Time", "Output", "Frames", "Delay", "FPS", "Optimize", "Loop", "Resize", "Sources", "Largest", "Alpha", "✔ OK", "✘ Err", "Status", "Notes", "File names"],
            col_tips=[
                "When the GIF was built.",
                "Output file path.",
                "Total number of frames included.",
                "Base export delay used for frames without per-frame overrides.",
                "Approximate playback FPS implied by the recorded delay.",
                "Loop count recorded for the export.",
                "Whether palette optimization was enabled for this build.",
                "Resize cap applied during export, if any.",
                "Summary of imported source types used in this build.",
                "Largest imported source frame size recorded for this build.",
                "How many imported frames/sources carried alpha before quantization.",
                "Frames processed successfully.",
                "Frames that had errors.",
                "Build status summary for filtering at a glance.",
                "Export settings / diagnostics notes recorded for this build.",
                "Input filenames used in this build.",
            ],
        )
        gif_layout.addWidget(self._gif_tree)
        # Animated GIF thumbnails delegate (item 80)
        self._gif_anim_delegate = _AnimatedGifDelegate(self._gif_tree, self._gif_tree)
        self._gif_tree.setItemDelegate(self._gif_anim_delegate)
        self._gif_summary = QLabel("")
        self._gif_summary.setObjectName("subheader")
        gif_layout.addWidget(self._gif_summary)
        self._sub_tabs.addTab(gif_widget, "🎞  GIF Builder")
        self._sub_tabs.tabBar().setTabToolTip(self._sub_tabs.indexOf(gif_widget), "GIF Builder history")

        # --- Video Builder sub-tab (item 74) ---
        vid_widget = QWidget()
        vid_layout = QVBoxLayout(vid_widget)
        vid_layout.setContentsMargins(0, 6, 0, 0)
        self._vid_search = self._make_search_field("video")
        vid_layout.addWidget(self._vid_search)
        self._vid_tree = _make_tree(
            ["Time", "Output", "Format", "Clips", "FPS", "Canvas", "Filter", "Audio", "Recovery", "Streams", "Sources", "✔ OK", "✘ Err", "Status", "Clip details", "Notes", "File names"],
            col_tips=[
                "When the video was built.",
                "Output file path.",
                "Export container/format used for this build.",
                "Total number of clips included.",
                "Export frame rate used for this build.",
                "Final export canvas/output size used for this build.",
                "Visual filter used during export.",
                "Whether export audio was kept or muted/off.",
                "Recovery summary for clips that needed fallback loading.",
                "Manual video-stream selections recorded for clips in this build.",
                "Summary of clip source types used in this build.",
                "Clips processed successfully.",
                "Clips that had errors.",
                "Build status summary for filtering at a glance.",
                "Per-clip recovery and stream-selection details recorded for this build.",
                "Recovery / export notes recorded for this build.",
                "Input filenames used in this build.",
            ],
        )
        vid_layout.addWidget(self._vid_tree)
        self._vid_summary = QLabel("")
        self._vid_summary.setObjectName("subheader")
        vid_layout.addWidget(self._vid_summary)
        self._sub_tabs.addTab(vid_widget, "🎬  Video Builder")
        self._sub_tabs.tabBar().setTabToolTip(self._sub_tabs.indexOf(vid_widget), "Video Builder history")

        layout.addWidget(self._sub_tabs, 1)

        # Connections
        self._btn_export.clicked.connect(self._export_history)
        self._btn_clear.clicked.connect(self._clear_history)

        self._conv_search.textChanged.connect(
            lambda text: self._apply_filter(self._conv_tree, text)
        )
        self._conv_search.textChanged.connect(self._refresh_session_status)
        self._alpha_search.textChanged.connect(
            lambda text: self._apply_filter(self._alpha_tree, text)
        )
        self._alpha_search.textChanged.connect(self._refresh_session_status)
        self._sel_search.textChanged.connect(
            lambda text: self._apply_filter(self._sel_tree, text)
        )
        self._sel_search.textChanged.connect(self._refresh_session_status)
        self._gif_search.textChanged.connect(
            lambda text: self._apply_filter(self._gif_tree, text)
        )
        self._gif_search.textChanged.connect(self._refresh_session_status)
        self._vid_search.textChanged.connect(
            lambda text: self._apply_filter(self._vid_tree, text)
        )
        self._vid_search.textChanged.connect(self._refresh_session_status)
        self._sub_tabs.currentChanged.connect(self._refresh_session_status)

    # ------------------------------------------------------------------
    # Search / filter helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_search_field(name: str) -> QLineEdit:
        """Return a styled search QLineEdit for a history sub-tab."""
        field = QLineEdit()
        field.setObjectName(f"history_search_{name}")
        field.setPlaceholderText("🔍  Filter by time/output/status/notes/file/source/format/recovery/streams/clip/audio/filter/largest/alpha/canvas/delay or use status:, output:, notes:, file:, source:, format:, recovery:, streams:, clip:, audio:, filter:, largest:, alpha:, canvas:, size:, delay:, ok:, errors:, frames:, clips:, loop:, optimize:, resize:, fps:, wildcards (*.gif), ranges (>24, <=100), or field ORs (audio:off|kept) …")
        field.setClearButtonEnabled(True)
        return field

    @staticmethod
    def _apply_filter(tree: QTreeWidget, text: str) -> None:
        """Show only rows whose text in any column contains *text* (case-insensitive)."""
        needle = text.strip().lower()
        free_text, grouped_fields = _field_filter_groups(needle)
        root = tree.invisibleRootItem()
        for row in range(root.childCount()):
            item = root.child(row)
            if not needle:
                item.setHidden(False)
                continue
            row_text = item.data(0, _HistoryItem._FILTER_ROLE)
            if not row_text:
                row_text = " ".join(
                    item.text(col) for col in range(tree.columnCount())
                ).lower()
            fields = item.data(0, _HistoryItem._FILTER_FIELDS_ROLE) or {}
            visible = True
            for token in free_text:
                if token not in row_text:
                    visible = False
                    break
            if visible:
                for key, values in grouped_fields.items():
                    haystack = str(fields.get(key, ""))
                    if not haystack or not any(_matches_field_filter(haystack, value) for value in values):
                        visible = False
                        break
            item.setHidden(not visible)

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr: "TooltipManager") -> None:
        """Register History tab widgets with the TooltipManager."""
        mgr.register(self._btn_clear, "history_clear_btn")
        mgr.register(self._btn_export, "history_export_btn")
        mgr.register(self._sub_tabs.widget(0), "history_conv_sub")
        mgr.register(self._sub_tabs.widget(1), "history_alpha_sub")
        mgr.register(self._sub_tabs.widget(2), "history_sel_sub")
        mgr.register(self._conv_tree, "history_conv_tree")
        mgr.register(self._alpha_tree, "history_alpha_tree")
        mgr.register(self._sel_tree, "history_sel_tree")
        mgr.register(self._gif_tree, "history_gif_tree")
        mgr.register(self._vid_tree, "history_vid_tree")
        mgr.register(self._conv_summary, "history_conv_summary")
        mgr.register(self._alpha_summary, "history_alpha_summary")
        mgr.register(self._sel_summary, "history_sel_summary")
        mgr.register(self._gif_summary, "history_gif_summary")
        mgr.register(self._vid_summary, "history_vid_summary")
        mgr.register(self._conv_search, "history_search")
        mgr.register(self._alpha_search, "history_search")
        mgr.register(self._sel_search, "history_search")
        mgr.register(self._gif_search, "history_search")
        mgr.register(self._vid_search, "history_search")

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
        # Decorate the sub-tab labels with the theme icon.
        icon = get_theme_icon(theme_name)
        self._sub_tabs.setTabText(0, f"{icon}🔄 Converter")
        self._sub_tabs.setTabText(1, f"{icon}🖼 Alpha & RGBA Adjuster")
        self._sub_tabs.setTabText(2, f"{icon}🎭 Alpha Painter")
        self._sub_tabs.setTabText(3, f"{icon}🎞 GIF Builder")
        self._sub_tabs.setTabText(4, f"{icon}🎬 Video Builder")

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refresh(self):
        """Reload all history lists from settings and reapply any active filters."""
        self._refresh_converter()
        self._refresh_alpha()
        self._refresh_selective_alpha()
        self._refresh_gif_builder()
        self._refresh_video_builder()
        # Re-apply search filters so existing text still works after refresh.
        self._apply_filter(self._conv_tree, self._conv_search.text())
        self._apply_filter(self._alpha_tree, self._alpha_search.text())
        self._apply_filter(self._sel_tree, self._sel_search.text())
        self._apply_filter(self._gif_tree, self._gif_search.text())
        self._apply_filter(self._vid_tree, self._vid_search.text())
        self._refresh_session_status()

    def _current_history_status(self) -> str:
        mapping = {
            0: ("Converter", self._conv_tree, self._conv_search, self._conv_summary),
            1: ("Alpha & RGBA", self._alpha_tree, self._alpha_search, self._alpha_summary),
            2: ("Alpha Painter", self._sel_tree, self._sel_search, self._sel_summary),
            3: ("GIF Builder", self._gif_tree, self._gif_search, self._gif_summary),
            4: ("Video Builder", self._vid_tree, self._vid_search, self._vid_summary),
        }
        label, tree, search, summary_lbl = mapping.get(
            self._sub_tabs.currentIndex(),
            ("History", self._conv_tree, self._conv_search, self._conv_summary),
        )
        total = tree.topLevelItemCount()
        visible = sum(1 for row in range(total) if not tree.topLevelItem(row).isHidden())
        parts = [label]
        parts.append(
            f"{visible}/{total} shown"
            if total and visible != total
            else (f"{total} item{'s' if total != 1 else ''}" if total else "no entries yet")
        )
        filter_text = search.text().strip()
        if filter_text:
            parts.append(f"filter {filter_text}")
        summary_text = summary_lbl.text().strip()
        if summary_text:
            parts.append(summary_text)
        return "📋 History: " + "  •  ".join(parts)

    def get_status_bar_text(self) -> str:
        return self._current_history_status()

    def _refresh_session_status(self, *_args) -> None:
        status = self.get_status_bar_text().strip()
        mapping = {
            0: (self._conv_tree, self._conv_search),
            1: (self._alpha_tree, self._alpha_search),
            2: (self._sel_tree, self._sel_search),
            3: (self._gif_tree, self._gif_search),
            4: (self._vid_tree, self._vid_search),
        }
        tree, search = mapping.get(self._sub_tabs.currentIndex(), (self._conv_tree, self._conv_search))
        total = tree.topLevelItemCount()
        visible = sum(1 for row in range(total) if not tree.topLevelItem(row).isHidden())
        filter_text = search.text().strip()
        next_text = _history_next_step_text(total, visible, filter_text)
        self._next_step_lbl.setText(next_text)
        self._next_step_lbl.setToolTip(next_text)
        text = f"Tool status: {status}" if status else "Tool status: ready"
        if next_text:
            text += f"\nNext: {next_text.removeprefix('Next step:').strip()}"
        self._session_status_lbl.setText(text)
        self._session_status_lbl.setToolTip((status + "\n\n" + next_text).strip() or text)
        self.queue_status_changed.emit(status)

    def _refresh_converter(self):
        history = self._settings.get_converter_history()
        self._conv_tree.setSortingEnabled(False)
        self._conv_tree.clear()
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            fmt = entry.get("format", "?")
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            file_list = entry.get("files", [])
            files = ", ".join(file_list)
            item = _HistoryItem([ts, fmt, n_files, n_ok, n_err, files])
            item.setData(0, _HistoryItem._SORT_ROLE, entry.get("timestamp", ""))
            _set_filter_text(item, ts, fmt, n_files, n_ok, n_err, file_list)
            _set_filter_fields(
                item,
                time=ts,
                format=fmt,
                file=file_list,
                status="issues" if str(n_err) not in {"0", "?"} else "ok",
                errors=n_err,
            )
            # Thumbnail icon from first processed file (item 9)
            thumb = _load_thumb(entry.get("first_file", ""))
            preview_text = "Preview: first file thumbnail shown." if not thumb.isNull() else "Preview: no thumbnail available."
            if not thumb.isNull():
                item.setIcon(0, thumb)
            _set_history_tooltip(
                item,
                6,
                f"Batch: {ts}\nFormat: {fmt}\n"
                f"Total: {n_files}  OK: {n_ok}  Errors: {n_err}\n"
                f"{preview_text}",
                file_list,
            )
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._conv_tree.addTopLevelItem(item)
        _apply_default_sort(self._conv_tree)
        total = len(history)
        self._conv_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else
               " — run the Converter to see history here.")
        )

    def _refresh_alpha(self):
        history = self._settings.get_alpha_history()
        self._alpha_tree.setSortingEnabled(False)
        self._alpha_tree.clear()
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            mode = entry.get("mode", entry.get("preset", "?"))
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            file_list = entry.get("files", [])
            files = ", ".join(file_list)
            item = _HistoryItem([ts, mode, n_files, n_ok, n_err, files])
            item.setData(0, _HistoryItem._SORT_ROLE, entry.get("timestamp", ""))
            _set_filter_text(item, ts, mode, n_files, n_ok, n_err, file_list)
            _set_filter_fields(
                item,
                time=ts,
                mode=mode,
                file=file_list,
                status="issues" if str(n_err) not in {"0", "?"} else "ok",
                errors=n_err,
            )
            # Thumbnail icon from first processed file (item 9)
            thumb = _load_thumb(entry.get("first_file", ""))
            preview_text = "Preview: first file thumbnail shown." if not thumb.isNull() else "Preview: no thumbnail available."
            if not thumb.isNull():
                item.setIcon(0, thumb)
            _set_history_tooltip(
                item,
                6,
                f"Batch: {ts}\n"
                f"Mode: {mode}\n"
                f"Total: {n_files}  OK: {n_ok}  Errors: {n_err}\n"
                f"{preview_text}",
                file_list,
            )
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._alpha_tree.addTopLevelItem(item)
        _apply_default_sort(self._alpha_tree)
        total = len(history)
        self._alpha_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else
               " — run the Alpha & RGBA Adjuster to see history here.")
        )

    def _refresh_selective_alpha(self):
        history = self._settings.get_selective_alpha_history()
        self._sel_tree.setSortingEnabled(False)
        self._sel_tree.clear()
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            mode = entry.get("mode", entry.get("preset", "?"))
            n_files = str(entry.get("file_count", "?"))
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            file_list = entry.get("files", [])
            # Older entries stored source/output but not files list — derive it
            if not file_list and entry.get("output"):
                import os as _os
                file_list = [_os.path.basename(entry["output"])]
            files = ", ".join(file_list)
            item = _HistoryItem([ts, mode, n_files, n_ok, n_err, files])
            item.setData(0, _HistoryItem._SORT_ROLE, entry.get("timestamp", ""))
            _set_filter_text(
                item,
                ts,
                mode,
                n_files,
                n_ok,
                n_err,
                entry.get("source", ""),
                entry.get("output", ""),
                file_list,
            )
            _set_filter_fields(
                item,
                time=ts,
                mode=mode,
                source=entry.get("source", ""),
                output=entry.get("output", ""),
                file=file_list,
                status="issues" if str(n_err) not in {"0", "?"} else "ok",
                errors=n_err,
            )
            # Thumbnail icon from source image (item 9)
            thumb_path = entry.get("first_file", entry.get("source", ""))
            thumb = _load_thumb(thumb_path)
            preview_text = "Preview: source thumbnail shown." if not thumb.isNull() else "Preview: no thumbnail available."
            if not thumb.isNull():
                item.setIcon(0, thumb)
            _set_history_tooltip(
                item,
                6,
                f"Batch: {ts}\nMode: {mode}\n"
                f"Total: {n_files}  OK: {n_ok}  Errors: {n_err}\n"
                f"{preview_text}",
                file_list,
            )
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(6):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._sel_tree.addTopLevelItem(item)
        _apply_default_sort(self._sel_tree)
        total = len(history)
        self._sel_summary.setText(
            f"{total} session{'s' if total != 1 else ''} recorded"
            + ("  (most recent first)" if total > 0 else
               " — run Alpha Painter to see history here.")
        )

    def _refresh_gif_builder(self):
        """Populate the GIF Builder history tree (item 74)."""
        history = self._settings.get_gif_builder_history()
        # Clear old movies before rebuilding to avoid stale references (item 80).
        self._gif_anim_delegate.clear_movies()
        self._gif_tree.setSortingEnabled(False)
        self._gif_tree.clear()
        statuses: list[str] = []
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            output_path = entry.get("output", "")
            output = os.path.basename(output_path) if output_path else "?"
            n_frames = str(entry.get("frame_count", "?"))
            delay = str(entry.get("delay", "") or "").strip()
            fps = str(entry.get("fps", "") or "").strip()
            loop = str(entry.get("loop", "") or "").strip()
            optimize = str(entry.get("optimize", "") or "").strip()
            resize = str(entry.get("resize", "") or "").strip()
            sources = str(entry.get("sources", "") or "").strip()
            largest = str(entry.get("largest_frame", "") or "").strip()
            alpha = str(entry.get("alpha_summary", "") or "").strip()
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            status = _gif_history_status(entry.get("errors", 0))
            statuses.append(status)
            notes = str(entry.get("notes", "") or "").strip()
            file_list = entry.get("files", [])
            files = ", ".join(file_list)
            item = _HistoryItem([ts, output, n_frames, delay, fps, optimize, loop, resize, sources, largest, alpha, n_ok, n_err, status, notes, files])
            item.setData(0, _HistoryItem._SORT_ROLE, entry.get("timestamp", ""))
            _set_filter_text(
                item,
                ts,
                output,
                output_path,
                n_frames,
                delay,
                fps,
                loop,
                optimize,
                resize,
                sources,
                largest,
                alpha,
                n_ok,
                n_err,
                status,
                notes,
                file_list,
            )
            _set_filter_fields(
                item,
                type="gif",
                time=ts,
                output=[output, output_path],
                source=sources,
                largest=largest,
                size=[largest, resize],
                alpha=alpha,
                status=status,
                notes=notes,
                file=file_list,
                frames=n_frames,
                success=n_ok,
                errors=n_err,
                delay=delay,
                fps=fps,
                loop=loop,
                optimize=optimize,
                resize=resize,
            )
            # Use the output GIF for animated thumbnail (item 80); fall back to
            # the first input file for non-GIF outputs or missing files.
            gif_output = output_path if (output_path and output_path.lower().endswith(".gif")
                                         and os.path.isfile(output_path)) else ""
            if gif_output:
                # Let the animated delegate handle thumbnail rendering
                self._gif_anim_delegate.set_gif_path(item, gif_output)
                preview_text = "Preview: animated GIF thumbnail shown from the output file."
            else:
                thumb = _load_thumb(entry.get("first_file", ""))
                preview_text = "Preview: first input thumbnail shown." if not thumb.isNull() else "Preview: no thumbnail available."
                if not thumb.isNull():
                    item.setIcon(0, thumb)
            _set_builder_tooltip(
                item,
                16,
                f"Built: {ts}\nOutput: {output}\n"
                f"Frames: {n_frames}  Delay: {delay or 'n/a'}  FPS: {fps or 'n/a'}  Optimize: {optimize or 'n/a'}  Loop: {loop or 'n/a'}  Resize: {resize or 'original'}  Sources: {sources or 'n/a'}  Largest: {largest or 'n/a'}  Alpha: {alpha or 'n/a'}  OK: {n_ok}  Errors: {n_err}  Status: {status}\n"
                f"{preview_text}"
                + (f"\nNotes: {notes}" if notes else ""),
                file_list,
            )
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(16):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._gif_tree.addTopLevelItem(item)
        _apply_default_sort(self._gif_tree)
        total = len(history)
        status_mix = _history_status_mix(statuses)
        self._gif_summary.setText(
            f"{total} build{'s' if total != 1 else ''} recorded"
            + (f"  •  {status_mix}  (most recent first)" if total > 0 and status_mix else
               "  (most recent first)" if total > 0 else
               " — use the GIF Builder to see history here.")
        )

    def _refresh_video_builder(self):
        """Populate the Video Builder history tree (item 74)."""
        history = self._settings.get_video_builder_history()
        self._vid_tree.setSortingEnabled(False)
        self._vid_tree.clear()
        statuses: list[str] = []
        for entry in history:
            ts = _fmt_ts(entry.get("timestamp", ""))
            raw_output = entry.get("output")
            output = os.path.basename(raw_output) if raw_output else "?"
            fmt = str(entry.get("format", "") or "").strip()
            n_clips = str(entry.get("clip_count", "?"))
            fps = str(entry.get("fps", "") or "").strip()
            canvas = str(entry.get("canvas", "") or "").strip()
            filter_name = str(entry.get("filter", "") or "").strip()
            audio_mode = str(entry.get("audio", "") or "").strip()
            n_ok = str(entry.get("success", "?"))
            n_err = str(entry.get("errors", "?"))
            recovery = str(entry.get("recovery", "") or "").strip()
            streams = str(entry.get("streams", "") or "").strip()
            clip_details = str(entry.get("clips", "") or "").strip()
            sources = str(entry.get("sources", "") or "").strip()
            notes = str(entry.get("notes", "") or "").strip()
            status = _video_history_status(entry.get("errors", 0), notes)
            statuses.append(status)
            file_list = entry.get("files", [])
            files = ", ".join(file_list)
            item = _HistoryItem([ts, output, fmt, n_clips, fps, canvas, filter_name, audio_mode, recovery, streams, sources, n_ok, n_err, status, clip_details, notes, files])
            item.setData(0, _HistoryItem._SORT_ROLE, entry.get("timestamp", ""))
            _set_filter_text(
                item,
                ts,
                output,
                raw_output,
                fmt,
                n_clips,
                fps,
                canvas,
                filter_name,
                audio_mode,
                n_ok,
                n_err,
                recovery,
                streams,
                clip_details,
                sources,
                status,
                notes,
                file_list,
            )
            _set_filter_fields(
                item,
                type="video",
                time=ts,
                output=[output, raw_output],
                format=fmt,
                canvas=canvas,
                size=canvas,
                filter=filter_name,
                audio=audio_mode,
                recovery=recovery,
                streams=streams,
                clip=clip_details,
                source=sources,
                status=status,
                notes=notes,
                file=file_list,
                clips=n_clips,
                success=n_ok,
                errors=n_err,
                fps=fps,
            )
            thumb = _load_thumb(entry.get("first_file", ""))
            preview_text = "Preview: first clip thumbnail shown." if not thumb.isNull() else "Preview: no thumbnail available."
            if not thumb.isNull():
                item.setIcon(0, thumb)
            note_text = f"\nNotes: {notes}" if notes else ""
            clip_text = f"\nClip details: {clip_details}" if clip_details else ""
            _set_builder_tooltip(
                item,
                17,
                f"Built: {ts}\nOutput: {output}\nFormat: {fmt or '?'}\n"
                f"Clips: {n_clips}  FPS: {fps or 'n/a'}  Canvas: {canvas or 'auto'}  Filter: {filter_name or 'none'}  Audio: {audio_mode or 'n/a'}  Recovery: {recovery or 'direct only'}  Streams: {streams or 'auto/default'}  Sources: {sources or 'n/a'}  OK: {n_ok}  Errors: {n_err}  Status: {status}\n"
                f"{preview_text}{note_text}{clip_text}",
                file_list,
            )
            if isinstance(entry.get("errors", 0), int) and entry.get("errors", 0) > 0:
                for col in range(17):
                    item.setForeground(col, Qt.GlobalColor.yellow)
            self._vid_tree.addTopLevelItem(item)
        _apply_default_sort(self._vid_tree)
        total = len(history)
        status_mix = _history_status_mix(statuses)
        self._vid_summary.setText(
            f"{total} build{'s' if total != 1 else ''} recorded"
            + (f"  •  {status_mix}  (most recent first)" if total > 0 and status_mix else
               "  (most recent first)" if total > 0 else
               " — use the Video Builder to see history here.")
        )

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear_history(self):
        reply = QMessageBox.question(
            self, "Clear History",
            "Delete all history for all tools?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._settings.clear_converter_history()
            self._settings.clear_alpha_history()
            self._settings.clear_selective_alpha_history()
            self._settings.clear_gif_builder_history()
            self._settings.clear_video_builder_history()
            self.refresh()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_history(self) -> None:
        """Export the currently visible history sub-tab to a file.

        Supported formats (TXT default): Plain Text, CSV, JSON, HTML.
        """
        import json as _json

        def _filter_default_ext(file_filter: str) -> str:
            if file_filter.startswith("CSV Files"):
                return ".csv"
            if file_filter.startswith("JSON Files"):
                return ".json"
            if file_filter.startswith("HTML Files"):
                return ".html"
            if file_filter.startswith("Text Files"):
                return ".txt"
            return ""

        # Determine which sub-tab is active
        tab_idx = self._sub_tabs.currentIndex()
        if tab_idx == 0:
            tree = self._conv_tree
            tab_name = "converter"
            headers = ["Time", "Format", "Files", "OK", "Errors", "File names"]
        elif tab_idx == 1:
            tree = self._alpha_tree
            tab_name = "alpha_fixer"
            headers = ["Time", "Mode", "Files", "OK", "Errors", "File names"]
        elif tab_idx == 2:
            tree = self._sel_tree
            tab_name = "selective_alpha"
            headers = ["Time", "Mode", "Files", "OK", "Errors", "File names"]
        elif tab_idx == 3:
            tree = self._gif_tree
            tab_name = "gif_builder"
            headers = ["Time", "Output", "Frames", "Delay", "FPS", "Optimize", "Loop", "Resize", "Sources", "Largest", "Alpha", "OK", "Errors", "Status", "Notes", "File names"]
        else:
            tree = self._vid_tree
            tab_name = "video_builder"
            headers = ["Time", "Output", "Format", "Clips", "FPS", "Canvas", "Filter", "Audio", "Recovery", "Streams", "Sources", "OK", "Errors", "Status", "Clip details", "Notes", "File names"]

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

        final_ext = _filter_default_ext(selected_filter)
        current_ext = Path(path).suffix.lower()
        if final_ext and current_ext != final_ext and not (
            final_ext == ".html" and current_ext == ".htm"
        ):
            path = str(Path(path).with_suffix(final_ext))
        elif not current_ext:
            path = str(Path(path).with_suffix(".txt"))

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        # Collect rows in the same visible order shown to the user.
        rows = []
        for r in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(r)
            if item is None or item.isHidden():
                continue
            rows.append([item.text(c) for c in range(tree.columnCount())])

        ext = Path(path).suffix.lower().lstrip(".") or "txt"

        try:
            if ext == "csv":
                self._export_csv(path, headers, rows)

            elif ext == "json":
                data = [dict(zip(headers, row)) for row in rows]
                with open(path, "w", encoding="utf-8") as f:
                    _json.dump(data, f, indent=2, ensure_ascii=False)

            elif ext in ("html", "htm"):
                th_cells = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
                tr_rows = "".join(
                    "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
                    for row in rows
                )
                title = html.escape(tab_name.replace('_', ' ').title())
                content = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    f"<title>{title} History</title>"
                    "<style>body{background:#ffffff;color:#111111}table{border-collapse:collapse}th,td{border:1px solid #888;"
                    "padding:4px 8px;text-align:left;word-break:break-word;overflow-wrap:anywhere}th{background:#333;color:#eee}"
                    "tr:nth-child(even){background:#f5f5f5}</style></head><body>"
                    f"<h2>{title} History</h2>"
                    f"<table><caption>{title} History</caption><thead><tr>{th_cells}</tr></thead><tbody>{tr_rows}</tbody></table>"
                    "</body></html>"
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            else:
                # Plain text (default)
                def _txt_cell(value: str, *, is_last: bool = False) -> str:
                    text = str(value)
                    if is_last and len(text) > 80:
                        return text[:77] + "..."
                    return text

                txt_rows = [
                    [_txt_cell(cell, is_last=(i == len(headers) - 1)) for i, cell in enumerate(row)]
                    for row in rows
                ]
                col_widths = [max(len(h), *(len(r[i]) for r in txt_rows), 4)
                              for i, h in enumerate(headers)] if txt_rows else [len(h) for h in headers]
                def _fmt_row(cells):
                    return "  ".join(c.ljust(w) for c, w in zip(cells, col_widths))
                header_line = _fmt_row(headers)
                sep = "-" * len(header_line)
                lines = [header_line, sep] + [_fmt_row(r) for r in txt_rows]
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
