"""
Alpha & RGBA Adjuster tab widget.
"""
import datetime
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QFileDialog,
    QProgressBar, QGroupBox, QScrollArea,
    QGridLayout, QLineEdit, QSplitter,
    QMessageBox, QTextEdit, QMenu,
    QAbstractSpinBox,
)

from ..core.presets import PresetManager
from ..core.alpha_processor import collect_files, SUPPORTED_READ
from ..core.worker import AlphaWorker
from .drop_list import DropFileList
from .preview_pane import BeforeAfterWidget


# ---------------------------------------------------------------------------
# Background worker: load + process one image for the comparison pane
# ---------------------------------------------------------------------------

class _AlphaPreviewLoader(QThread):
    """
    Load a single image, apply the current preset/manual settings,
    and emit both the original and processed images as QImages,
    plus numeric alpha statistics (min, max, mean) for each.
    """
    preview_ready = pyqtSignal(QImage, QImage)   # (before, after)
    stats_ready = pyqtSignal(dict, dict)          # (before_stats, after_stats)
    failed = pyqtSignal(str)

    def __init__(self, path: str, preset=None, manual_params: dict | None = None):
        super().__init__()
        self._path = path
        self._preset = preset
        self._manual = manual_params
        self._abort = False

    def stop(self) -> None:
        """Request that the thread abandon work as soon as it can check."""
        self._abort = True

    @staticmethod
    def _alpha_stats(img) -> dict:
        """Return min/max/mean/percent_nonzero alpha for a PIL RGBA image."""
        import numpy as np
        img_rgba = img.convert("RGBA") if img.mode != "RGBA" else img
        try:
            arr = np.array(img_rgba, dtype=np.uint8)
            alpha = arr[:, :, 3]
            total_pixels = alpha.size
            nonzero = int(np.count_nonzero(alpha))
            return {
                "min": int(alpha.min()),
                "max": int(alpha.max()),
                "mean": float(alpha.mean()),
                "percent_nonzero": round(nonzero / max(total_pixels, 1) * 100, 1),
            }
        finally:
            if img_rgba is not img:
                img_rgba.close()

    def run(self):
        try:
            from ..core.alpha_processor import (
                load_image,
                apply_alpha_preset,
                apply_manual_alpha,
                apply_rgba_adjust,
            )
            from .preview_pane import _pil_to_qimage

            orig = load_image(self._path)  # always RGBA PIL image
            processed = None
            try:
                # If the user has already moved to a different file, bail out now
                # before spending CPU on the expensive numpy processing step.
                if self._abort:
                    return

                before_qi = _pil_to_qimage(orig)
                before_stats = self._alpha_stats(orig)

                if self._preset is not None:
                    processed = apply_alpha_preset(orig, self._preset)
                elif self._manual is not None:
                    processed = apply_manual_alpha(
                        orig,
                        threshold=self._manual.get("threshold", 0),
                        invert=self._manual.get("invert", False),
                        clamp_min=self._manual.get("clamp_min", 0),
                        clamp_max=self._manual.get("clamp_max", 255),
                        binary_cut=self._manual.get("binary_cut", False),
                    )
                else:
                    processed = orig

                # Apply optional RGBA channel adjustments
                rgb = self._manual.get("rgb") if self._manual else None
                if rgb and (rgb.get("r") or rgb.get("g") or rgb.get("b") or rgb.get("a")):
                    _tmp = apply_rgba_adjust(
                        processed,
                        red_delta=rgb.get("r", 0),
                        green_delta=rgb.get("g", 0),
                        blue_delta=rgb.get("b", 0),
                        alpha_delta=rgb.get("a", 0),
                    )
                    if processed is not orig:
                        processed.close()
                    processed = _tmp

                after_qi = _pil_to_qimage(processed)
                after_stats = self._alpha_stats(processed)
                try:
                    self.preview_ready.emit(before_qi, after_qi)
                    self.stats_ready.emit(before_stats, after_stats)
                except RuntimeError:
                    return  # receiver destroyed; abort
            finally:
                orig.close()
                if processed is not None and processed is not orig:
                    processed.close()
        except Exception:
            import traceback
            try:
                self.failed.emit(traceback.format_exc())
            except RuntimeError:
                pass  # receiver destroyed; nothing to do



# ---------------------------------------------------------------------------
# Background worker: expand directories to file lists without blocking the UI
# ---------------------------------------------------------------------------

class _FileCollectThread(QThread):
    """Walk directories in a background thread, emitting batches of found paths.

    Use this whenever the incoming paths might include large directories that
    would block the Qt event loop if scanned synchronously.  The caller should
    connect ``files_found`` to process batches of paths as they arrive, and
    ``finished`` (inherited from QThread) to perform final clean-up.
    """

    #: Emitted with a list of newly discovered file paths every CHUNK files.
    files_found = pyqtSignal(list)
    #: Total number of files found (emitted once on completion).
    scan_done = pyqtSignal(int)

    _CHUNK = 500  # emit a signal every N files so the UI can add them progressively

    def __init__(self, paths: list[str], extensions: set, recursive: bool):
        super().__init__()
        self._paths = paths
        self._extensions = extensions
        self._recursive = recursive
        self._stop = False

    def stop(self) -> None:
        """Request early termination (e.g. when the user adds another batch)."""
        self._stop = True

    def run(self) -> None:
        buffer: list[str] = []
        total = 0
        for p in self._paths:
            if self._stop:
                break
            p = os.path.normpath(p)
            if os.path.isfile(p):
                if Path(p).suffix.lower() in self._extensions:
                    buffer.append(p)
                    total += 1
                    if len(buffer) >= self._CHUNK:
                        try:
                            self.files_found.emit(list(buffer))
                        except RuntimeError:
                            return
                        buffer.clear()
            elif os.path.isdir(p):
                if self._recursive:
                    for root, _, files in os.walk(p):
                        if self._stop:
                            break
                        for f in files:
                            if Path(f).suffix.lower() in self._extensions:
                                buffer.append(os.path.join(root, f))
                                total += 1
                                if len(buffer) >= self._CHUNK:
                                    try:
                                        self.files_found.emit(list(buffer))
                                    except RuntimeError:
                                        return
                                    buffer.clear()
                else:
                    for f in os.listdir(p):
                        if self._stop:
                            break
                        fp = os.path.join(p, f)
                        if os.path.isfile(fp) and Path(f).suffix.lower() in self._extensions:
                            buffer.append(fp)
                            total += 1
                            if len(buffer) >= self._CHUNK:
                                try:
                                    self.files_found.emit(list(buffer))
                                except RuntimeError:
                                    return
                                buffer.clear()
        if buffer:
            try:
                self.files_found.emit(buffer)
            except RuntimeError:
                return
        try:
            self.scan_done.emit(total)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------

class AlphaFixerTab(QWidget):
    """Tab widget for batch alpha-channel processing."""

    # Maximum number of paths passed to ROM detection (keeps scan fast for
    # very large file imports — the first few paths usually have enough
    # context to identify the console).
    _ROM_SCAN_LIMIT = 50

    # Emitted after every successful batch: carries the count of files processed.
    # MainWindow connects this to check for processing-based theme unlocks.
    processing_done = pyqtSignal(int)
    # Emitted when a batch finishes with at least one error (carries error count).
    processing_error = pyqtSignal(int)
    # Emitted when a batch starts (before the worker thread is launched).
    processing_started = pyqtSignal()
    # Emitted the very first time a batch completes successfully.
    # MainWindow uses this to trigger the 'first alpha fix' theme unlock.
    first_alpha_fix = pyqtSignal()
    # Emitted whenever at least one file is added to the queue.
    files_added = pyqtSignal()
    # Emitted whenever files are removed from the queue.
    files_removed = pyqtSignal()
    # Emitted when the entire file queue is cleared at once.
    list_cleared = pyqtSignal()
    # Emitted when files are first dragged over the drop zone.
    drag_entered = pyqtSignal()
    preview_refreshed = pyqtSignal()
    # Emitted when the user right-clicks the alpha-vis preview and chooses to
    # share detected zone masks with the Selective Alpha tool.
    # Carries a list of (alpha_value: int, bool_mask: np.ndarray) tuples.
    zone_masks_shared = pyqtSignal(list)
    # Emitted when the output directory is changed (browse or typed).
    # Carries the new path string (empty string = same as source).
    output_dir_changed = pyqtSignal(str)

    def __init__(self, preset_manager: PresetManager, settings_manager, parent=None):
        super().__init__(parent)
        self._presets = preset_manager
        self._settings = settings_manager
        self._worker = None
        # Background file-collection thread (avoids UI freeze on large folders)
        self._collect_thread: _FileCollectThread | None = None
        # ETA tracking for large batch runs
        self._batch_start_time: float = 0.0
        self._batch_total: int = 0
        # Compare preview state
        self._preview_path: str | None = None
        self._preview_loader: _AlphaPreviewLoader | None = None
        # Debounce timer so rapid fine-tune slider changes don't flood with threads
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(150)  # ms -- wait for user to settle
        self._preview_debounce.timeout.connect(self._update_compare)
        self._setup_ui()
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Header – uses the default-theme label; updated to the active theme via update_theme()
        from .theme_engine import get_theme_tab_labels
        _default_labels = get_theme_tab_labels("Panda Dark")
        hdr = QLabel(_default_labels[0])
        hdr.setObjectName("header")
        self._hdr = hdr
        main_layout.addWidget(hdr)

        outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        outer_splitter.setChildrenCollapsible(False)

        # ==============================================================
        # Left panel: file list  +  before/after comparison
        # ==============================================================
        left = QWidget()
        left.setMinimumWidth(320)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(6)

        lbl_files = QLabel("Input Files / Folders  (drag & drop supported)")
        lbl_files.setObjectName("section")
        self._lbl_files = lbl_files
        lv.addWidget(lbl_files)

        btn_row = QHBoxLayout()
        self._btn_add_files = QPushButton("Add Files")
        self._btn_add_folder = QPushButton("Add Folder")
        self._btn_clear = QPushButton("Clear")
        btn_row.addWidget(self._btn_add_files)
        btn_row.addWidget(self._btn_add_folder)
        btn_row.addWidget(self._btn_clear)
        lv.addLayout(btn_row)

        self._recursive_check = QCheckBox("Include subfolders")
        self._recursive_check.setChecked(
            self._settings.get("batch_recursive", True)
        )
        lv.addWidget(self._recursive_check)

        # Output section – placed here (near input) so it's obvious where
        # processed files will land without hunting to the far right panel.
        grp_out = QGroupBox("Output")
        self._grp_out = grp_out
        go_layout = QGridLayout(grp_out)
        go_layout.setContentsMargins(10, 14, 10, 12)
        go_layout.setColumnStretch(0, 0)
        go_layout.setColumnStretch(1, 1)
        go_layout.setColumnMinimumWidth(0, 120)
        go_layout.setHorizontalSpacing(12)
        go_layout.setVerticalSpacing(10)
        # Explicit row minimum heights prevent the nested QHBoxLayout in row 0
        # from causing the two rows to visually overlap on some platforms.
        # 40 px gives comfortable clearance for 28 px widgets plus any
        # platform-default margins the nested QHBoxLayout may add.
        go_layout.setRowMinimumHeight(0, 40)
        go_layout.setRowMinimumHeight(1, 40)

        lbl_out_folder = QLabel("Output folder:")
        lbl_out_folder.setMinimumHeight(24)
        go_layout.addWidget(lbl_out_folder, 0, 0)
        out_row = QHBoxLayout()
        self._out_dir_edit = QLineEdit()
        self._out_dir_edit.setPlaceholderText("Same as source (default)")
        self._out_dir_edit.setMinimumHeight(28)
        self._btn_out_dir = QPushButton("Browse…")
        self._btn_out_dir.setMinimumHeight(28)
        self._btn_out_dir.setMinimumWidth(80)
        out_row.addWidget(self._out_dir_edit, 1)
        out_row.addWidget(self._btn_out_dir)
        go_layout.addLayout(out_row, 0, 1)

        lbl_suffix = QLabel("Filename suffix:")
        lbl_suffix.setMinimumHeight(24)
        go_layout.addWidget(lbl_suffix, 1, 0)
        self._suffix_edit = QLineEdit()
        self._suffix_edit.setPlaceholderText("e.g. _fixed  (blank = overwrite source)")
        self._suffix_edit.setMinimumHeight(28)
        saved_suffix = self._settings.get("output_suffix", "")
        if saved_suffix:
            self._suffix_edit.setText(saved_suffix)
        go_layout.addWidget(self._suffix_edit, 1, 1)

        lv.addWidget(grp_out)

        # ---- File list area (inside the scrollable controls panel) ----
        list_area = QWidget()
        la_layout = QVBoxLayout(list_area)
        la_layout.setContentsMargins(0, 0, 0, 0)
        la_layout.setSpacing(4)

        self._file_list = DropFileList()
        self._file_list.setMinimumHeight(120)
        self._file_list.setToolTip(
            "Files queued for processing.\n"
            "• Drag files/folders here from Explorer/Finder\n"
            "• Delete key or right-click → Remove Selected\n"
            "• Right-click → Thumbnails to toggle image previews"
        )
        la_layout.addWidget(self._file_list, 1)

        self._file_count_lbl = QLabel("0 files  |  F5 to process  |  Esc to stop")
        self._file_count_lbl.setObjectName("subheader")
        la_layout.addWidget(self._file_count_lbl)

        # ROM / game folder detection banner (hidden when no game is detected)
        self._rom_banner = QLabel()
        self._rom_banner.setObjectName("rom_banner")
        self._rom_banner.setWordWrap(True)
        self._rom_banner.hide()
        la_layout.addWidget(self._rom_banner)

        lv.addWidget(list_area, 1)

        # Wrap controls + file-list in a scroll area so they remain accessible
        # on smaller windows.  The compare widget lives OUTSIDE this scroll area
        # so it is never clipped by layout size constraints.
        # Do NOT set an explicit minimum height here: the QVBoxLayout already
        # computes a natural minimum (~370 px) from its children, and the
        # scroll area respects that value.  An explicit override smaller than
        # the natural minimum (e.g. 300 px) would allow the scroll area to
        # squish the widget below its natural minimum, causing rows in the
        # Output group-box and the file list to visually overlap.
        left_scroll = QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        # ---- Before/after compare panel (outside scroll area, always visible) ----
        compare_area = QWidget()
        ca_layout = QVBoxLayout(compare_area)
        ca_layout.setContentsMargins(0, 0, 0, 0)
        ca_layout.setSpacing(2)

        compare_lbl = QLabel("Before / After Comparison  ◀▶ drag to compare")
        compare_lbl.setObjectName("section")
        compare_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._compare_lbl = compare_lbl
        ca_layout.addWidget(compare_lbl)

        # Alpha visualization toggle – off by default, does not affect processing
        self._alpha_vis_check = QCheckBox("🎨  Highlight Alpha Values")
        self._alpha_vis_check.setChecked(False)
        self._alpha_vis_check.setToolTip(
            "Overlay a false-colour heat-map on the alpha channel in both preview images.\n"
            "Red = fully transparent (α 0), Yellow = semi-transparent (α 128),\n"
            "Green = fully opaque (α 255).\n"
            "This is purely a visual aid — it does NOT change how files are processed."
        )
        ca_layout.addWidget(self._alpha_vis_check)

        # Row: [Before stats panel] [BeforeAfterWidget] [After stats panel]
        compare_row = QHBoxLayout()
        compare_row.setContentsMargins(0, 0, 0, 0)
        compare_row.setSpacing(4)

        def _make_stats_panel() -> QLabel:
            """Return a small label used for alpha statistics."""
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            # Use font metrics so the panel stays wide enough regardless of
            # system font size or DPI.  "Min: 255" is the widest typical text.
            from PyQt6.QtGui import QFontMetrics
            from PyQt6.QtWidgets import QApplication
            fm = QFontMetrics(QApplication.font())
            lbl.setMinimumWidth(fm.horizontalAdvance("Min: 255") + 8)
            lbl.setWordWrap(True)
            lbl.setObjectName("stats_panel")
            lbl.setContentsMargins(2, 4, 2, 4)
            return lbl

        self._before_stats_lbl = _make_stats_panel()
        self._after_stats_lbl = _make_stats_panel()

        self._compare = BeforeAfterWidget()
        self._compare.setMinimumHeight(180)
        # Right-click on the compare preview lets users export detected alpha
        # zones directly to the Selective Alpha tool (cross-tool clipboard).
        self._compare.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._compare.customContextMenuRequested.connect(self._on_compare_context_menu)

        compare_row.addWidget(self._before_stats_lbl, 0)
        compare_row.addWidget(self._compare, 1)
        compare_row.addWidget(self._after_stats_lbl, 0)
        ca_layout.addLayout(compare_row, 1)

        # Left column: vertical splitter – controls/file-list panel on top
        # (scrollable), compare panel on the bottom (always fully visible).
        left_vsplit = QSplitter(Qt.Orientation.Vertical)
        left_vsplit.setChildrenCollapsible(False)
        left_vsplit.setMinimumWidth(320)
        left_vsplit.addWidget(left_scroll)
        left_vsplit.addWidget(compare_area)
        left_vsplit.setSizes([420, 380])
        outer_splitter.addWidget(left_vsplit)

        # ==============================================================
        # Right panel: run controls + alpha settings
        # ==============================================================
        right = QWidget()
        right.setMinimumWidth(380)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(8)

        # Run controls – at the very top so the Process button is always
        # immediately visible when the tab is opened.
        run_row = QHBoxLayout()
        self._btn_run = QPushButton("▶  Process  [F5]")
        self._btn_run.setObjectName("accent")
        self._btn_run.setMinimumHeight(40)
        self._btn_stop = QPushButton("■  Stop  [Esc]")
        self._btn_stop.setEnabled(False)
        run_row.addWidget(self._btn_run, 1)
        run_row.addWidget(self._btn_stop)
        rv.addLayout(run_row)

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setRange(0, 100)
        rv.addWidget(self._progress)

        self._status_lbl = QLabel("Ready.")
        self._status_lbl.setObjectName("subheader")
        rv.addWidget(self._status_lbl)

        # Alpha channel settings section
        grp_tune = QGroupBox("Alpha Channel Settings")
        self._grp_tune = grp_tune
        gt_layout = QGridLayout(grp_tune)
        gt_layout.setContentsMargins(10, 14, 10, 12)
        gt_layout.setColumnStretch(0, 0)
        gt_layout.setColumnStretch(1, 1)
        gt_layout.setColumnMinimumWidth(0, 165)
        gt_layout.setHorizontalSpacing(12)
        gt_layout.setVerticalSpacing(8)

        # Brief hint so users immediately understand the workflow.
        hint_lbl = QLabel(
            "ℹ  Normalize the alpha channel to a new range.  "
            "The image's darkest pixel maps to Min, the brightest maps to Max — "
            "all others scale proportionally in between.  "
            "For images where every pixel shares the same alpha value, the value is "
            "clamped: below Min → Min, above Max → Max, within [Min, Max] → unchanged.  "
            "Set Min = Max to force every pixel to exactly that value."
        )
        hint_lbl.setObjectName("subheader")
        hint_lbl.setWordWrap(True)
        hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gt_layout.addWidget(hint_lbl, 0, 0, 1, 2)

        # ── Output range ─────────────────────────────────────────────────────────
        lbl_cmin = QLabel("Min alpha (0–255):")
        lbl_cmin.setMinimumHeight(24)
        gt_layout.addWidget(lbl_cmin, 1, 0)
        self._clamp_min_spin = QSpinBox()
        self._clamp_min_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._clamp_min_spin.setRange(0, 255)
        self._clamp_min_spin.setValue(0)
        self._clamp_min_spin.setMinimumHeight(26)
        self._clamp_min_spin.setToolTip(
            "Minimum alpha in the output.\n"
            "For images with a range of alpha values: the darkest pixel maps to this\n"
            "value and all others are stretched proportionally above it.\n"
            "For uniform-alpha images (every pixel already the same value): the single\n"
            "value is clamped — below-Min → Min, above-Max → Max, in-range → unchanged.\n"
            "0 = fully transparent minimum (most common).\n"
            "Set Min = Max to force every pixel to the same alpha value."
        )
        gt_layout.addWidget(self._clamp_min_spin, 1, 1)

        lbl_cmax = QLabel("Max alpha (0–255):")
        lbl_cmax.setMinimumHeight(24)
        gt_layout.addWidget(lbl_cmax, 2, 0)
        self._clamp_max_spin = QSpinBox()
        self._clamp_max_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._clamp_max_spin.setRange(0, 255)
        self._clamp_max_spin.setValue(255)
        self._clamp_max_spin.setMinimumHeight(26)
        self._clamp_max_spin.setToolTip(
            "Maximum alpha in the output.\n"
            "For images with a range of alpha values: the brightest pixel maps to this\n"
            "value and all others are stretched proportionally below it.\n"
            "For uniform-alpha images (every pixel already the same value): the single\n"
            "value is clamped — below-Min → Min, above-Max → Max, in-range → unchanged.\n"
            "Example: set Max to 128 to cap maximum alpha at 128 (PS2 native full opacity).\n"
            "Set Min = Max to force every pixel to the same alpha value."
        )
        gt_layout.addWidget(self._clamp_max_spin, 2, 1)

        # ── Simple checkboxes ───────────────────────────────────────────────────
        self._invert_check = QCheckBox("Invert alpha (swap transparent ↔ opaque)")
        self._invert_check.setToolTip(
            "Flip every alpha value: 0 becomes 255 and 255 becomes 0.\n"
            "Use this when a texture's transparency is inside-out —\n"
            "e.g. the opaque area should be transparent and vice versa."
        )
        gt_layout.addWidget(self._invert_check, 3, 0, 1, 2)

        self._binary_cut_check = QCheckBox("Binary cut (\u2265 threshold \u2192 255, else \u2192 0)")
        self._binary_cut_check.setToolTip(
            "Hard cutout mask: pixels with alpha ≥ threshold become fully opaque (255),\n"
            "pixels below threshold become fully transparent (0).\n"
            "Used by N64 1-bit alpha textures and similar hard-edge formats."
        )
        gt_layout.addWidget(self._binary_cut_check, 4, 0, 1, 2)

        # ── Advanced separator ──────────────────────────────────────────────────
        adv_sep = QLabel("─── Advanced Options ───")
        adv_sep.setObjectName("subheader")
        adv_sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gt_layout.addWidget(adv_sep, 5, 0, 1, 2)

        # ── Threshold (advanced) ────────────────────────────────────────────────
        lbl_thresh = QLabel("Threshold (0 = all pixels):")
        lbl_thresh.setMinimumHeight(24)
        lbl_thresh.setToolTip(
            "When set above 0 (and Binary cut is OFF): pixels with alpha >= threshold\n"
            "are protected and kept at their original value — only pixels with alpha\n"
            "below this value are inverted/scaled.\n"
            "0 (default) means every pixel is processed regardless of its current alpha.\n"
            "Example: threshold=128 leaves already-opaque areas unchanged.\n"
            "When Binary cut is ON: sets the hard split point (>= threshold → 255, else → 0)."
        )
        gt_layout.addWidget(lbl_thresh, 6, 0)
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._threshold_spin.setRange(0, 255)
        self._threshold_spin.setValue(0)
        self._threshold_spin.setMinimumHeight(26)
        gt_layout.addWidget(self._threshold_spin, 6, 1)

        # ── Live params summary ─────────────────────────────────────────────────
        self._finetune_params_lbl = QLabel("")
        self._finetune_params_lbl.setObjectName("subheader")
        self._finetune_params_lbl.setWordWrap(True)
        self._finetune_params_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gt_layout.addWidget(self._finetune_params_lbl, 7, 0, 1, 2)

        # ── RGBA channel adjustments ────────────────────────────────────────────
        rgb_sep = QLabel("─── RGBA Channel Adjust (delta \u2013255 to +255) ───")
        rgb_sep.setObjectName("subheader")
        rgb_sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gt_layout.addWidget(rgb_sep, 11, 0, 1, 2)

        lbl_red = QLabel("Red adjust:")
        lbl_red.setMinimumHeight(24)
        gt_layout.addWidget(lbl_red, 12, 0)
        self._red_spin = QSpinBox()
        self._red_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._red_spin.setRange(-255, 255)
        self._red_spin.setValue(0)
        self._red_spin.setPrefix("R ")
        self._red_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._red_spin, 12, 1)

        lbl_green = QLabel("Green adjust:")
        lbl_green.setMinimumHeight(24)
        gt_layout.addWidget(lbl_green, 13, 0)
        self._green_spin = QSpinBox()
        self._green_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._green_spin.setRange(-255, 255)
        self._green_spin.setValue(0)
        self._green_spin.setPrefix("G ")
        self._green_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._green_spin, 13, 1)

        lbl_blue = QLabel("Blue adjust:")
        lbl_blue.setMinimumHeight(24)
        gt_layout.addWidget(lbl_blue, 14, 0)
        self._blue_spin = QSpinBox()
        self._blue_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._blue_spin.setRange(-255, 255)
        self._blue_spin.setValue(0)
        self._blue_spin.setPrefix("B ")
        self._blue_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._blue_spin, 14, 1)

        lbl_alpha_adj = QLabel("Alpha adjust:")
        lbl_alpha_adj.setMinimumHeight(24)
        gt_layout.addWidget(lbl_alpha_adj, 15, 0)
        self._alpha_delta_spin = QSpinBox()
        self._alpha_delta_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._alpha_delta_spin.setRange(-255, 255)
        self._alpha_delta_spin.setValue(0)
        self._alpha_delta_spin.setPrefix("A\u25b3 ")
        self._alpha_delta_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._alpha_delta_spin, 15, 1)

        self._apply_rgb_check = QCheckBox("Apply RGBA adjustments")
        self._apply_rgb_check.setChecked(False)
        self._apply_rgb_check.setToolTip(
            "When checked, the Red/Green/Blue/Alpha deltas above are applied on top of\n"
            "the alpha fix. Useful for colour-correcting game textures."
        )
        gt_layout.addWidget(self._apply_rgb_check, 16, 0, 1, 2)

        rv.addWidget(grp_tune)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(90)
        self._log.setPlaceholderText("Processing log…")
        rv.addWidget(self._log, 1)

        # Wrap right-panel content in a scroll area so it never clips on
        # small/tight window heights — user can scroll down to see all controls.
        right_scroll = QScrollArea()
        right_scroll.setWidget(right)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        right_scroll.setMinimumWidth(380)
        outer_splitter.addWidget(right_scroll)
        outer_splitter.setSizes([460, 580])
        main_layout.addWidget(outer_splitter, 1)

        # ==============================================================
        # Connections
        # ==============================================================
        self._btn_add_files.clicked.connect(self._add_files)
        self._btn_add_folder.clicked.connect(self._add_folder)
        self._btn_clear.clicked.connect(self._file_list._clear_all)
        self._btn_run.clicked.connect(self._run)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_out_dir.clicked.connect(self._browse_out_dir)
        # DropFileList signals
        self._file_list.paths_dropped.connect(self._add_to_list)
        self._file_list.count_changed.connect(self._update_file_count)
        self._file_list.file_removed.connect(self.files_removed)
        self._file_list.list_cleared.connect(self.list_cleared)
        self._file_list.drag_entered.connect(self.drag_entered)
        # Selection → compare preview
        self._file_list.currentRowChanged.connect(self._on_selection_changed)
        # Fine-tune controls → refresh compare preview AND live params label
        self._threshold_spin.valueChanged.connect(self._on_finetune_changed)
        self._clamp_min_spin.valueChanged.connect(self._on_finetune_changed)
        self._clamp_max_spin.valueChanged.connect(self._on_finetune_changed)
        self._invert_check.toggled.connect(self._on_finetune_changed)
        self._binary_cut_check.toggled.connect(self._on_finetune_changed)
        self._red_spin.valueChanged.connect(self._on_finetune_changed)
        self._green_spin.valueChanged.connect(self._on_finetune_changed)
        self._blue_spin.valueChanged.connect(self._on_finetune_changed)
        self._alpha_delta_spin.valueChanged.connect(self._on_finetune_changed)
        self._apply_rgb_check.toggled.connect(self._on_finetune_changed)
        # Alpha visualization toggle → re-apply overlay without re-processing
        self._alpha_vis_check.toggled.connect(self._on_alpha_vis_toggled)
        # Persist batch options so they survive app restarts
        self._recursive_check.toggled.connect(
            lambda v: self._settings.set("batch_recursive", v)
        )
        self._suffix_edit.textChanged.connect(
            lambda t: self._settings.set("output_suffix", t)
        )
        # Initialise the live params label
        self._refresh_finetune_label()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("F5"), self).activated.connect(self._run)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._stop)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._add_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self).activated.connect(self._add_folder)

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr) -> None:
        """Register tab widgets with the TooltipManager for cycling tips."""
        mgr.register(self._btn_add_files, "add_files")
        mgr.register(self._btn_add_folder, "add_folder")
        mgr.register(self._btn_clear, "clear_list")
        mgr.register(self._btn_run, "process_btn")
        mgr.register(self._btn_stop, "stop_btn")
        mgr.register(self._threshold_spin, "threshold_spin")
        mgr.register(self._finetune_params_lbl, "finetune_params_lbl")
        mgr.register(self._clamp_min_spin, "clamp_min_spin")
        mgr.register(self._clamp_max_spin, "clamp_max_spin")
        mgr.register(self._invert_check, "invert_check")
        mgr.register(self._binary_cut_check, "binary_cut_check")
        mgr.register(self._out_dir_edit, "out_dir")
        mgr.register(self._btn_out_dir, "out_dir_browse")
        mgr.register(self._suffix_edit, "suffix_edit")
        mgr.register(self._recursive_check, "recursive_check")
        mgr.register(self._file_list, "file_list")
        mgr.register(self._compare, "compare_widget")
        mgr.register(self._red_spin, "red_spin")
        mgr.register(self._green_spin, "green_spin")
        mgr.register(self._blue_spin, "blue_spin")
        mgr.register(self._alpha_delta_spin, "alpha_delta_spin")
        mgr.register(self._apply_rgb_check, "apply_rgb_check")
        mgr.register(self._before_stats_lbl, "before_stats_panel")
        mgr.register(self._after_stats_lbl, "after_stats_panel")
        mgr.register(self._rom_banner, "rom_banner")
        mgr.register(self._file_count_lbl, "alpha_file_count_lbl")
        mgr.register(self._log, "processing_log")
        mgr.register(self._progress, "processing_progress")
        mgr.register(self._status_lbl, "alpha_status_lbl")
        mgr.register(self._alpha_vis_check, "alpha_vis_check")

    def update_theme(self, theme_name: str) -> None:
        """Update inner header, section labels and group-box titles to match the active theme."""
        from .theme_engine import get_theme_tab_labels, get_theme_icon
        labels = get_theme_tab_labels(theme_name)
        # labels[0] is e.g. "🩸🖼  Alpha & RGBA Adjuster" – use it directly as the header
        self._hdr.setText(labels[0])
        # Decorate section labels and group-box titles with the theme's representative icon.
        icon = get_theme_icon(theme_name)
        self._lbl_files.setText(f"{icon}  Input Files / Folders  (drag & drop supported)")
        self._grp_out.setTitle(f"{icon}  Output")
        self._grp_tune.setTitle(f"{icon}  Alpha Channel Settings")
        self._compare_lbl.setText(f"{icon}  Before / After Comparison  ◀▶ drag to compare")

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _add_files(self):
        last_dir = self._settings.get("last_input_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Files", last_dir,
            "Images (*.png *.dds *.jpg *.jpeg *.bmp *.tiff *.tif *.webp *.tga *.ico *.gif *.ppm *.pcx *.avif *.qoi *.svg *.jp2);;All Files (*)",
        )
        if paths:
            self._settings.set("last_input_dir", os.path.dirname(paths[0]))
            self._add_to_list(paths)

    def _add_folder(self):
        last_dir = self._settings.get("last_input_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", last_dir)
        if folder:
            self._settings.set("last_input_dir", folder)
            self._add_to_list([folder])

    def _add_to_list(self, paths: list[str]):
        """Expand directories, filter to supported formats, then add to list.

        Individual file paths are added synchronously (fast path).
        Directory paths are expanded in a background ``_FileCollectThread``
        so the Qt event loop stays responsive even when scanning very large
        folders (100 000+ files).
        """
        # Fast path: individual files only → expand synchronously (no thread overhead)
        individual = [p for p in paths if os.path.isfile(p)]
        dirs = [p for p in paths if os.path.isdir(p)]

        unsupported_count = sum(
            1 for p in individual
            if Path(p).suffix.lower() not in SUPPORTED_READ
        )
        valid_files = [p for p in individual if Path(p).suffix.lower() in SUPPORTED_READ]

        was_empty = self._file_list.count() == 0
        if valid_files:
            self._file_list.add_paths_batch(valid_files)
            if was_empty and self._file_list.count() > 0:
                self._file_list.setCurrentRow(0)
            self.files_added.emit()
        if unsupported_count:
            self._log_msg(
                f"⚠ {unsupported_count} file(s) skipped — format not supported "
                f"(supported: {', '.join(sorted(SUPPORTED_READ))})"
            )

        if dirs:
            # Stop any previous collection thread before starting a new one
            if self._collect_thread is not None and self._collect_thread.isRunning():
                self._collect_thread.stop()
                self._collect_thread.wait(200)

            recursive = self._recursive_check.isChecked()
            thread = _FileCollectThread(dirs, SUPPORTED_READ, recursive)

            def _on_files_found(batch: list[str]) -> None:
                pre = self._file_list.count() == 0
                self._file_list.add_paths_batch(batch)
                if pre and self._file_list.count() > 0:
                    self._file_list.setCurrentRow(0)
                self.files_added.emit()

            def _on_scan_done(total: int) -> None:
                if total:
                    self._log_msg(f"📁 Folder scan complete — {total} image(s) found.")

            thread.files_found.connect(_on_files_found)
            thread.scan_done.connect(_on_scan_done)
            self._collect_thread = thread
            thread.start()

        # Trigger game/ROM folder detection for the added paths
        self._detect_rom(paths)

    @pyqtSlot(int)
    def _update_file_count(self, n: int):
        self._file_count_lbl.setText(
            f"{n} file{'s' if n != 1 else ''}  |  F5 to process  |  Esc to stop"
        )

    @pyqtSlot(int)
    def _on_selection_changed(self, row: int):
        item = self._file_list.item(row)
        if item and os.path.isfile(item.text()):
            self._preview_path = item.text()
            self._preview_debounce.start()
        else:
            self._preview_path = None
            self._compare.clear()
            self._before_stats_lbl.setText("")
            self._after_stats_lbl.setText("")

    # ------------------------------------------------------------------
    # ROM / game folder detection
    # ------------------------------------------------------------------

    def _detect_rom(self, paths: list[str]) -> None:
        """Run ROM detection on *paths* and update the banner (non-blocking)."""
        if not paths:
            return
        try:
            from ..core.rom_detector import detect_from_paths  # noqa: F401 – probe
        except ImportError:
            return

        # Run in the Qt event loop after a short delay so the UI stays
        # responsive during large file imports
        QTimer.singleShot(200, lambda: self._run_rom_detection(paths))

    def _run_rom_detection(self, paths: list[str]) -> None:
        try:
            from ..core.rom_detector import detect_from_paths
            # Limit the scan for performance when users drop thousands of files.
            # _ROM_SCAN_LIMIT paths are enough to fingerprint any single-game
            # folder while keeping the detection latency below ~50 ms.
            if len(paths) > self._ROM_SCAN_LIMIT:
                logger.debug(
                    "ROM detection limited to first %d of %d paths for performance",
                    self._ROM_SCAN_LIMIT, len(paths),
                )
            scan_paths = paths[:self._ROM_SCAN_LIMIT]
            result = detect_from_paths(scan_paths)
            if result.detected:
                text = result.description()
                if result.cover_art_hint:
                    text += f"  🖼 Cover: {result.cover_art_hint}"
                self._rom_banner.setText(text)
                self._rom_banner.show()
            else:
                self._rom_banner.hide()
        except Exception:
            self._rom_banner.hide()

    @staticmethod
    def _clamp_range_label(lo: int, hi: int) -> str:
        """Return a compact clamp range string."""
        return f"{lo}–{hi}"

    def _refresh_finetune_label(self) -> None:
        """Update the live fine-tune params summary label."""
        cmin   = self._clamp_min_spin.value()
        cmax   = self._clamp_max_spin.value()
        thresh = self._threshold_spin.value()
        lo, hi = min(cmin, cmax), max(cmin, cmax)
        parts  = [f"scale → [{self._clamp_range_label(lo, hi)}]"]
        if thresh:
            parts.append(f"thresh={thresh}")
        if self._invert_check.isChecked():
            parts.append("invert=yes")
        if self._binary_cut_check.isChecked():
            parts.append("binary_cut=yes")
        self._finetune_params_lbl.setText("  ·  ".join(parts))

    @pyqtSlot()
    def _on_finetune_changed(self, *args):
        """Refresh live params label and debounce the compare preview update."""
        self._refresh_finetune_label()
        self._preview_debounce.start()

    # ------------------------------------------------------------------
    # Compare preview
    # ------------------------------------------------------------------

    def _build_manual_params(self) -> dict:
        """Return a manual-params dict from the current fine-tune UI controls."""
        return {
            "threshold": self._threshold_spin.value(),
            "invert": self._invert_check.isChecked(),
            "clamp_min": self._clamp_min_spin.value(),
            "clamp_max": self._clamp_max_spin.value(),
            "binary_cut": self._binary_cut_check.isChecked(),
        }


    def _update_compare(self, *args):
        """Start a background load+process to update the before/after comparison."""
        if not self._preview_path:
            return

        # Disconnect the previous loader's signals before replacing it so that
        # a stale thread finishing late cannot overwrite the current result.
        # Also ask the thread to abandon its work so CPU is freed quickly.
        if self._preview_loader is not None:
            self._preview_loader.stop()
            try:
                self._preview_loader.preview_ready.disconnect()
                self._preview_loader.stats_ready.disconnect()
                self._preview_loader.failed.disconnect()
            except RuntimeError:
                pass  # already disconnected

        manual = self._build_manual_params()

        # Attach RGBA adjustments when enabled
        if self._apply_rgb_check.isChecked():
            manual["rgb"] = {
                "r": self._red_spin.value(),
                "g": self._green_spin.value(),
                "b": self._blue_spin.value(),
                "a": self._alpha_delta_spin.value(),
            }

        self._compare.set_loading()
        self._preview_loader = _AlphaPreviewLoader(
            self._preview_path, preset=None, manual_params=manual
        )
        self._preview_loader.preview_ready.connect(self._on_compare_ready)
        self._preview_loader.stats_ready.connect(self._on_stats_ready)
        self._preview_loader.failed.connect(self._on_compare_failed)
        self._preview_loader.start()

    # ------------------------------------------------------------------
    # Alpha visualization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _alpha_vis_overlay(qi: QImage) -> QImage:
        """
        Return a copy of *qi* (Format_ARGB32) with a false-colour heat-map
        blended over the alpha channel, plus tiny text labels showing the
        numeric alpha value sampled across the image on a sparse grid.

        Colour key:
          α = 0   → vivid red    (fully transparent)
          α = 128 → yellow       (semi-transparent)
          α = 255 → vivid green  (fully opaque)

        The heat-map is drawn at 70 % opacity so the underlying colours remain
        visible while the alpha structure is clearly legible.  Text labels are
        omitted for images smaller than 32 × 32 pixels.
        """
        import numpy as np
        from PyQt6.QtGui import QImage as _QI, QPainter, QColor, QFont, QPen

        # Work in Format_ARGB32 so we have direct byte access
        src = qi.convertToFormat(_QI.Format.Format_ARGB32)
        w, h = src.width(), src.height()
        if w == 0 or h == 0:
            return qi

        # Extract raw bytes → ARGB array (Qt stores BGRA in little-endian)
        ptr = src.bits()
        ptr.setsize(h * w * 4)
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()

        # Save the raw alpha channel before we modify the colour channels so
        # the text labels can read the original (unmodified) alpha values.
        alpha_raw = arr[:, :, 3].copy()

        # Qt ARGB32: channels are [B, G, R, A] per pixel
        alpha = alpha_raw.astype(np.float32) / 255.0  # 0.0 … 1.0

        # Heat-map colours (R, G, B):
        #   0.0 → red   (255, 0, 0)
        #   0.5 → yellow (255, 255, 0)
        #   1.0 → green  (0, 255, 0)
        heat_r = np.where(alpha < 0.5,
                          255,
                          np.clip((1.0 - alpha) * 2 * 255, 0, 255)).astype(np.uint8)
        heat_g = np.where(alpha < 0.5,
                          np.clip(alpha * 2 * 255, 0, 255),
                          255).astype(np.uint8)
        heat_b = np.zeros((h, w), dtype=np.uint8)

        # Blend: out = heat * 0.70 + original * 0.30
        blend = 0.70
        arr[:, :, 2] = np.clip(heat_r * blend + arr[:, :, 2] * (1 - blend), 0, 255).astype(np.uint8)
        arr[:, :, 1] = np.clip(heat_g * blend + arr[:, :, 1] * (1 - blend), 0, 255).astype(np.uint8)
        arr[:, :, 0] = np.clip(heat_b * blend + arr[:, :, 0] * (1 - blend), 0, 255).astype(np.uint8)
        # Keep alpha channel unchanged so transparency is still rendered
        # (the overlay colour already encodes the alpha information visually)

        out = _QI(arr.tobytes(), w, h, w * 4, _QI.Format.Format_ARGB32)
        out = out.copy()  # detach from numpy buffer

        # --- Draw tiny alpha-value text labels on a sparse grid -------------
        # Only add labels when the image is large enough to be legible.
        _MIN_LABEL_DIM = 32
        if w >= _MIN_LABEL_DIM and h >= _MIN_LABEL_DIM:
            # Grid spacing: aim for roughly 8 × 8 labels maximum, adaptive to
            # image size.  Minimum step of 20 px to avoid label clutter.
            step = max(20, min(w, h) // 8)
            font = QFont()
            font.setPixelSize(max(7, step // 4))
            font.setBold(True)
            painter = QPainter(out)
            painter.setFont(font)
            fm = painter.fontMetrics()
            half_step = step // 2
            for gy in range(half_step, h, step):
                for gx in range(half_step, w, step):
                    a_val = int(alpha_raw[gy, gx])
                    text = str(a_val)
                    tw = fm.horizontalAdvance(text)
                    th = fm.ascent()
                    tx = gx - tw // 2
                    ty = gy + th // 2
                    # Black shadow/outline for contrast
                    painter.setPen(QPen(QColor(0, 0, 0, 200)))
                    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        painter.drawText(tx + ox, ty + oy, text)
                    # White foreground text
                    painter.setPen(QPen(QColor(255, 255, 255, 230)))
                    painter.drawText(tx, ty, text)
            painter.end()

        return out

    @pyqtSlot(bool)
    def _on_alpha_vis_toggled(self, _checked: bool) -> None:
        """Re-apply (or remove) the alpha visualization when the toggle changes."""
        if self._compare.has_images():
            self._apply_alpha_vis_to_compare()

    def _apply_alpha_vis_to_compare(self) -> None:
        """Apply the alpha heat-map overlay to the current compare images if enabled."""
        before_raw = self._compare.before_image()
        after_raw = self._compare.after_image()
        if before_raw is None or after_raw is None:
            return
        if self._alpha_vis_check.isChecked():
            self._compare.set_before(self._alpha_vis_overlay(before_raw))
            self._compare.set_after(self._alpha_vis_overlay(after_raw))
        else:
            self._compare.set_before(before_raw)
            self._compare.set_after(after_raw)

    def _on_compare_context_menu(self, pos) -> None:
        """Right-click on the compare preview: offer to copy detected alpha zones
        to the Selective Alpha tool via the ``zone_masks_shared`` signal.

        The context menu is only shown when the alpha visualization overlay is
        active *and* a preview image is available, so users see the zones they
        are about to copy.
        """
        import numpy as np
        from ..core.selective_alpha_processor import detect_alpha_zones

        raw = self._compare.before_image()
        if raw is None:
            return

        # Convert the stored raw QImage to a numpy RGBA array for zone analysis.
        src = raw.convertToFormat(QImage.Format.Format_ARGB32)
        w, h = src.width(), src.height()
        if w == 0 or h == 0:
            return
        ptr = src.bits()
        ptr.setsize(h * w * 4)
        # Qt ARGB32 LE stores bytes as [B, G, R, A]; alpha is always index 3.
        bgra = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()

        zones = detect_alpha_zones(bgra)

        menu = QMenu(self)

        if not zones:
            no_act = menu.addAction("No distinct alpha zones detected in this image")
            no_act.setEnabled(False)
            menu.addSeparator()
            menu.addAction(
                "Hint: enable 'Highlight Alpha Values' to see the zone structure"
            ).setEnabled(False)
        else:
            total_px = w * h
            copy_all = menu.addAction(
                f"📋 Copy all {len(zones)} detected zone(s) → Selective Alpha tool"
            )
            copy_all.setToolTip(
                "Sends all detected alpha-value zones to the Selective Alpha tool.\n"
                "Switch to the Selective Alpha tab and click 'Import from Alpha Tool'\n"
                "to load the zones into the painting canvas."
            )
            copy_all.triggered.connect(lambda: self.zone_masks_shared.emit(zones))

            menu.addSeparator()
            menu.addAction("Copy individual zone to Selective Alpha:").setEnabled(False)
            for idx, (alpha_val, bool_mask) in enumerate(zones):
                pct = round(bool_mask.sum() / max(total_px, 1) * 100, 1)
                act = menu.addAction(
                    f"   📋 Zone {idx + 1}:  α = {alpha_val}  ({pct}% of pixels)"
                )
                act.setToolTip(
                    f"Send only Zone {idx + 1} (α={alpha_val}) to the Selective Alpha tool.\n"
                    "Switch to the Selective Alpha tab and click 'Import from Alpha Tool' to load it."
                )
                # Capture loop variables explicitly to avoid late-binding closure issues.
                def _make_single_zone_handler(av, bm):
                    def _handler():
                        self.zone_masks_shared.emit([(av, bm)])
                    return _handler
                act.triggered.connect(_make_single_zone_handler(alpha_val, bool_mask))

        menu.exec(self._compare.mapToGlobal(pos))

    @pyqtSlot(QImage, QImage)
    def _on_compare_ready(self, before_qi: QImage, after_qi: QImage):
        # Store the raw (unmodified) images so the vis toggle can toggle on/off
        # without needing to re-run the background worker.
        self._compare.store_raw_images(before_qi, after_qi)
        if self._alpha_vis_check.isChecked():
            self._compare.set_before(self._alpha_vis_overlay(before_qi))
            self._compare.set_after(self._alpha_vis_overlay(after_qi))
        else:
            self._compare.set_before(before_qi)
            self._compare.set_after(after_qi)
        # Notify main window so it can play the preview sound (opt-in, off by default)
        self.preview_refreshed.emit()

    @pyqtSlot(dict, dict)
    def _on_stats_ready(self, before: dict, after: dict):
        self._compare.set_stats(before, after)
        # Update the side stat panels with formatted channel values
        def _panel_text(label: str, s: dict) -> str:
            if not s:
                return ""
            pct = s.get("percent_nonzero", None)
            pct_line = f"vis%<br><b>{pct}%</b><br>" if pct is not None else ""
            return (
                f"<b>{label}</b><br>"
                f"min<br><b>{s['min']}</b><br>"
                f"max<br><b>{s['max']}</b><br>"
                f"mean<br><b>{s['mean']:.1f}</b><br>"
                f"{pct_line}"
            )
        self._before_stats_lbl.setText(_panel_text("BEFORE", before))
        self._after_stats_lbl.setText(_panel_text("AFTER", after))

    @pyqtSlot(str)
    def _on_compare_failed(self, err: str):
        self._compare.clear()
        self._before_stats_lbl.setText("")
        self._after_stats_lbl.setText("")
        self._log_msg(f"⚠ Preview failed: {err.splitlines()[0]}")

    # ------------------------------------------------------------------
    # Output dir
    # ------------------------------------------------------------------

    def _browse_out_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Output Folder")
        if folder:
            self._out_dir_edit.setText(folder)
            self.output_dir_changed.emit(folder)

    def set_output_dir(self, path: str) -> None:
        """Set the output directory from an external source without emitting output_dir_changed."""
        self._out_dir_edit.blockSignals(True)
        self._out_dir_edit.setText(path)
        self._out_dir_edit.blockSignals(False)

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _run(self):
        if self._worker and self._worker.isRunning():
            return

        files = [self._file_list.item(i).text() for i in range(self._file_list.count())]
        if not files:
            QMessageBox.information(self, "No Files", "Please add files or a folder first.")
            return

        expanded = collect_files(files, recursive=self._recursive_check.isChecked())
        if not expanded:
            QMessageBox.information(self, "No Files", "No supported image files found.")
            return

        manual = self._build_manual_params()
        if self._apply_rgb_check.isChecked():
            manual["rgb"] = {
                "r": self._red_spin.value(),
                "g": self._green_spin.value(),
                "b": self._blue_spin.value(),
                "a": self._alpha_delta_spin.value(),
            }

        out_dir = self._out_dir_edit.text().strip() or None
        suffix = self._suffix_edit.text().strip()

        # Determine a common root directory for relative path preservation.
        # When an output_dir is set and files come from multiple subdirectories,
        # this allows AlphaWorker to mirror the source tree so that same-named
        # files from different subdirs never overwrite each other.
        input_root = None
        if len(expanded) > 1:
            try:
                dirs = [os.path.dirname(f) for f in expanded]
                input_root = os.path.commonpath(dirs)
            except ValueError:
                pass

        # Remember for history recording in _on_finished
        self._last_run_files = expanded
        self._last_run_preset = "manual"

        self._log.clear()
        self._progress.setValue(0)
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._status_lbl.setText("Processing…")
        self._batch_start_time = time.monotonic()
        self._batch_total = len(expanded)
        # Notify main window so it can play the process-start sound
        self.processing_started.emit()

        # Disconnect the previous worker's signals before replacing it to
        # prevent the signal connection table from growing across multiple
        # run → stop → run cycles in a long session.
        if self._worker is not None:
            try:
                self._worker.progress.disconnect()
                self._worker.file_done.disconnect()
                self._worker.finished.disconnect()
            except RuntimeError:
                pass  # already disconnected

        self._worker = AlphaWorker(
            files=expanded,
            manual_params=manual,
            output_dir=out_dir,
            input_root=input_root,
            overwrite=(suffix == ""),
            suffix=suffix,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._worker.stop()
            self._status_lbl.setText("Stopping…")

    # ------------------------------------------------------------------
    # Worker slots
    # ------------------------------------------------------------------

    @pyqtSlot(int, int, str)
    def _on_progress(self, current: int, total: int, path: str):
        from ._ui_utils import format_eta
        pct = int(current / max(total, 1) * 100)
        self._progress.setValue(pct)
        elapsed = time.monotonic() - self._batch_start_time
        eta_str = format_eta(current, total, elapsed)
        self._status_lbl.setText(
            f"Processing {current + 1}/{total}: {Path(path).name}{eta_str}"
        )

    @pyqtSlot(str, bool, str)
    def _on_file_done(self, src: str, ok: bool, msg: str):
        icon = "✔" if ok else "✘"
        name = Path(src).name
        if ok and msg:
            # Success but with a warning (e.g. JPEG/BMP discards alpha).
            self._log_msg(f"{icon} {name}  ⚠ {msg}")
        elif ok:
            self._log_msg(f"{icon} {name}")
        else:
            self._log_msg(f"{icon} {name}" + (f"  →  {msg.splitlines()[-1] if msg else ''}"))

    @pyqtSlot(int, int)
    def _on_finished(self, success: int, errors: int):
        self._progress.setValue(100)
        self._btn_run.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status_lbl.setText(f"Done. ✔ {success} succeeded, ✘ {errors} failed.")
        self._log_msg(f"─── Finished: {success} ok, {errors} error(s) ───")
        # Refresh compare for currently selected file to show the processed result
        if self._preview_path and success > 0:
            self._update_compare()

        # Record in history
        entry = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "preset": getattr(self, "_last_run_preset", "manual"),
            "file_count": len(getattr(self, "_last_run_files", [])),
            "success": success,
            "errors": errors,
            "files": [Path(f).name for f in getattr(self, "_last_run_files", [])[:10]],
        }
        self._settings.add_alpha_history(entry)
        # Notify main window so processing-based theme unlocks can fire
        if success > 0:
            self.processing_done.emit(success)
            # Emit first_alpha_fix signal the very first time processing succeeds
            if not self._settings.get("alpha_fix_done_once", False):
                self._settings.set("alpha_fix_done_once", True)
                self.first_alpha_fix.emit()
        if errors > 0:
            self.processing_error.emit(errors)

    def _log_msg(self, msg: str):
        self._log.append(msg)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
