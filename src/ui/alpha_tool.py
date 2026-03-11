"""
Alpha Fixer tab widget.
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
    QComboBox, QSpinBox, QSlider, QCheckBox, QFileDialog,
    QProgressBar, QGroupBox, QScrollArea,
    QGridLayout, QLineEdit, QSplitter,
    QMessageBox, QInputDialog, QTextEdit,
)

from ..core.presets import AlphaPreset, PresetManager
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

    @staticmethod
    def _alpha_stats(img) -> dict:
        """Return min/max/mean/percent_nonzero alpha for a PIL RGBA image."""
        import numpy as np
        arr = np.array(img.convert("RGBA"), dtype=np.uint8)
        alpha = arr[:, :, 3]
        total_pixels = alpha.size
        nonzero = int(np.count_nonzero(alpha))
        return {
            "min": int(alpha.min()),
            "max": int(alpha.max()),
            "mean": float(alpha.mean()),
            "percent_nonzero": round(nonzero / max(total_pixels, 1) * 100, 1),
        }

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

            before_qi = _pil_to_qimage(orig)
            before_stats = self._alpha_stats(orig)

            if self._preset is not None:
                processed = apply_alpha_preset(orig, self._preset)
            elif self._manual is not None:
                processed = apply_manual_alpha(
                    orig,
                    value=self._manual.get("value"),  # None = clamp only
                    threshold=self._manual.get("threshold", 0),
                    invert=self._manual.get("invert", False),
                    clamp_min=self._manual.get("clamp_min", 0),
                    clamp_max=self._manual.get("clamp_max", 255),
                    binary_cut=self._manual.get("binary_cut", False),
                    mode=self._manual.get("mode", "set"),
                )
            else:
                processed = orig

            # Apply optional RGBA channel adjustments
            rgb = self._manual.get("rgb") if self._manual else None
            if rgb and (rgb.get("r") or rgb.get("g") or rgb.get("b") or rgb.get("a")):
                processed = apply_rgba_adjust(
                    processed,
                    red_delta=rgb.get("r", 0),
                    green_delta=rgb.get("g", 0),
                    blue_delta=rgb.get("b", 0),
                    alpha_delta=rgb.get("a", 0),
                )

            after_qi = _pil_to_qimage(processed)
            after_stats = self._alpha_stats(processed)
            self.preview_ready.emit(before_qi, after_qi)
            self.stats_ready.emit(before_stats, after_stats)
        except Exception as exc:
            import traceback
            self.failed.emit(traceback.format_exc())


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
    # Emitted the very first time a batch completes successfully.
    # MainWindow uses this to trigger the 'first alpha fix' theme unlock.
    first_alpha_fix = pyqtSignal()
    # Emitted whenever at least one file is added to the queue.
    files_added = pyqtSignal()

    def __init__(self, preset_manager: PresetManager, settings_manager, parent=None):
        super().__init__(parent)
        self._presets = preset_manager
        self._settings = settings_manager
        self._worker = None
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
        self._populate_presets()

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
        go_layout.setRowMinimumHeight(0, 36)
        go_layout.setRowMinimumHeight(1, 36)

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
        left.setMinimumHeight(300)
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

        # Row: [Before stats panel] [BeforeAfterWidget] [After stats panel]
        compare_row = QHBoxLayout()
        compare_row.setContentsMargins(0, 0, 0, 0)
        compare_row.setSpacing(4)

        def _make_stats_panel() -> QLabel:
            """Return a small fixed-width label used for alpha statistics."""
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            lbl.setFixedWidth(84)
            lbl.setWordWrap(True)
            lbl.setObjectName("stats_panel")
            lbl.setContentsMargins(2, 4, 2, 4)
            return lbl

        self._before_stats_lbl = _make_stats_panel()
        self._after_stats_lbl = _make_stats_panel()

        self._compare = BeforeAfterWidget()
        self._compare.setMinimumHeight(180)

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
        left_vsplit.setSizes([300, 380])
        outer_splitter.addWidget(left_vsplit)

        # ==============================================================
        # Right panel: run controls (top) + presets + fine-tune
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

        # Preset section
        grp_preset = QGroupBox("Preset")
        self._grp_preset = grp_preset
        gp_layout = QVBoxLayout(grp_preset)
        gp_layout.setSpacing(8)

        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(180)
        self._preset_combo.setMinimumHeight(28)
        self._btn_save_preset = QPushButton("Save")
        self._btn_save_preset.setMinimumWidth(60)
        self._btn_delete_preset = QPushButton("Delete")
        self._btn_delete_preset.setMinimumWidth(60)
        preset_row.addWidget(QLabel("Preset:"))
        preset_row.addWidget(self._preset_combo, 1)
        preset_row.addWidget(self._btn_save_preset)
        preset_row.addWidget(self._btn_delete_preset)
        gp_layout.addLayout(preset_row)

        self._preset_desc = QLabel("")
        self._preset_desc.setWordWrap(True)
        self._preset_desc.setObjectName("subheader")
        self._preset_desc.setMinimumHeight(44)
        gp_layout.addWidget(self._preset_desc)

        rv.addWidget(grp_preset)

        # Fine-tune section
        grp_tune = QGroupBox("Fine-Tune Alpha && RGBA Channels")
        self._grp_tune = grp_tune
        gt_layout = QGridLayout(grp_tune)
        gt_layout.setContentsMargins(10, 14, 10, 12)
        gt_layout.setColumnStretch(0, 0)
        gt_layout.setColumnStretch(1, 1)
        gt_layout.setColumnMinimumWidth(0, 165)
        gt_layout.setHorizontalSpacing(12)
        gt_layout.setVerticalSpacing(8)

        # "Apply fixed alpha value" checkbox — controls whether the alpha spinbox value
        # is applied to pixels.  Unchecked = clamp-only mode (alpha values are preserved,
        # only clamping is applied).  Checked = set alpha to the specified value first.
        self._apply_alpha_check = QCheckBox("Apply fixed alpha value")
        self._apply_alpha_check.setChecked(True)
        self._apply_alpha_check.setToolTip(
            "When checked, all pixels (or pixels below threshold) are set to the value above.\n"
            "When unchecked, pixel alpha values are preserved — only Clamp Min/Max are applied."
        )
        gt_layout.addWidget(self._apply_alpha_check, 0, 0, 1, 2)

        lbl_mode = QLabel("Apply mode:")
        lbl_mode.setMinimumHeight(24)
        gt_layout.addWidget(lbl_mode, 1, 0)
        self._mode_combo = QComboBox()
        _MODE_OPTIONS = [
            ("set",      "set — replace each pixel's alpha with the value below"),
            ("multiply", "multiply — scale: new = old × (value ÷ 255)"),
            ("add",      "add — shift: new = old + value  (max 255)"),
            ("subtract", "subtract — shift: new = old − value  (min 0)"),
        ]
        _MODE_TIPS = {
            "set":      "Replace every pixel's alpha with the exact value you set below. The most common mode.",
            "multiply": "Scale existing alpha. new = old × (value ÷ 255). Value=128 gives 50% of original. Classic PS2 fix.",
            "add":      "Add the value to each pixel's existing alpha, clamped at 255. Brightens transparency.",
            "subtract": "Subtract the value from each pixel's existing alpha, clamped at 0. Deepens transparency.",
        }
        for key, label in _MODE_OPTIONS:
            self._mode_combo.addItem(label, userData=key)
            idx = self._mode_combo.count() - 1
            self._mode_combo.setItemData(idx, _MODE_TIPS[key], Qt.ItemDataRole.ToolTipRole)
        self._mode_combo.setToolTip(
            "How the alpha value below is applied to each pixel.\n"
            "'set' replaces alpha; 'multiply' scales it; 'add'/'subtract' shift it."
        )
        self._mode_combo.setMinimumHeight(26)
        gt_layout.addWidget(self._mode_combo, 1, 1)

        lbl_alpha_val = QLabel("Alpha value (0–255):")
        lbl_alpha_val.setMinimumHeight(24)
        lbl_alpha_val.setToolTip(
            "Value used by the apply mode above.\n"
            "set: target alpha for all pixels.  multiply: factor (255 = no change).\n"
            "add/subtract: amount to shift each pixel's alpha.\n"
            "Only active when 'Apply fixed alpha value' is checked."
        )
        gt_layout.addWidget(lbl_alpha_val, 2, 0)
        self._alpha_spin = QSpinBox()
        self._alpha_spin.setRange(0, 255)
        self._alpha_spin.setValue(255)
        self._alpha_spin.setMinimumHeight(26)
        gt_layout.addWidget(self._alpha_spin, 2, 1)

        self._alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self._alpha_slider.setRange(0, 255)
        self._alpha_slider.setValue(255)
        gt_layout.addWidget(self._alpha_slider, 3, 0, 1, 2)

        lbl_thresh = QLabel("Threshold (0 = all pixels):")
        lbl_thresh.setMinimumHeight(24)
        lbl_thresh.setToolTip(
            "Only pixels with alpha BELOW this value are affected by the 'Set alpha to' value.\n"
            "0 means every pixel is affected regardless of its current alpha."
        )
        gt_layout.addWidget(lbl_thresh, 4, 0)
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(0, 255)
        self._threshold_spin.setValue(0)
        self._threshold_spin.setMinimumHeight(26)
        gt_layout.addWidget(self._threshold_spin, 4, 1)

        lbl_cmin = QLabel("Clamp Min (floor, 0–255):")
        lbl_cmin.setMinimumHeight(24)
        gt_layout.addWidget(lbl_cmin, 5, 0)
        self._clamp_min_spin = QSpinBox()
        self._clamp_min_spin.setRange(0, 255)
        self._clamp_min_spin.setValue(0)
        self._clamp_min_spin.setMinimumHeight(26)
        self._clamp_min_spin.setToolTip(
            "Output floor: any pixel alpha below this value is raised to this value.\n"
            "0 = no floor (default). Applied after any fixed alpha value is set."
        )
        gt_layout.addWidget(self._clamp_min_spin, 5, 1)

        lbl_cmax = QLabel("Clamp Max (ceiling, 0–255):")
        lbl_cmax.setMinimumHeight(24)
        gt_layout.addWidget(lbl_cmax, 6, 0)
        self._clamp_max_spin = QSpinBox()
        self._clamp_max_spin.setRange(0, 255)
        self._clamp_max_spin.setValue(255)
        self._clamp_max_spin.setMinimumHeight(26)
        self._clamp_max_spin.setToolTip(
            "Output ceiling: any pixel alpha above this value is capped to this value.\n"
            "255 = no ceiling (default). Applied after any fixed alpha value is set.\n"
            "Example: set max=128 to replicate PS2's 0–128 alpha range."
        )
        gt_layout.addWidget(self._clamp_max_spin, 6, 1)

        self._invert_check = QCheckBox("Invert Alpha")
        gt_layout.addWidget(self._invert_check, 7, 0, 1, 2)

        self._binary_cut_check = QCheckBox("Binary cut (\u2265 threshold \u2192 255, else \u2192 0)")
        self._binary_cut_check.setToolTip(
            "When checked, a hard 0/255 split is applied at the threshold:\n"
            "  \u2022 pixels with alpha \u2265 threshold become fully opaque (255)\n"
            "  \u2022 pixels with alpha < threshold become fully transparent (0)\n"
            "This overrides the 'Set alpha to' value for a strict cutout mask.\n"
            "Used by N64 1-bit alpha textures and similar hard-edge formats."
        )
        gt_layout.addWidget(self._binary_cut_check, 8, 0, 1, 2)

        self._use_preset_check = QCheckBox("Use preset (ignore fine-tune)")
        self._use_preset_check.setChecked(True)
        gt_layout.addWidget(self._use_preset_check, 9, 0, 1, 2)

        # Live "Current Params" display — updates whenever any fine-tune control changes.
        # Populated by _refresh_finetune_label() at the end of _setup_ui.
        self._finetune_params_lbl = QLabel("")
        self._finetune_params_lbl.setObjectName("subheader")
        self._finetune_params_lbl.setWordWrap(True)
        self._finetune_params_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._finetune_params_lbl.setToolTip(
            "Live summary of the current fine-tune parameters.\n"
            "Updates instantly as you change any control above."
        )
        gt_layout.addWidget(self._finetune_params_lbl, 10, 0, 1, 2)

        # --- RGBA channel adjustments ---
        rgb_sep = QLabel("─── RGBA Channel Adjust (delta \u2013255 to +255) ───")
        rgb_sep.setObjectName("subheader")
        rgb_sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gt_layout.addWidget(rgb_sep, 10, 0, 1, 2)

        lbl_red = QLabel("Red adjust:")
        lbl_red.setMinimumHeight(24)
        gt_layout.addWidget(lbl_red, 11, 0)
        self._red_spin = QSpinBox()
        self._red_spin.setRange(-255, 255)
        self._red_spin.setValue(0)
        self._red_spin.setPrefix("R ")
        self._red_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._red_spin, 11, 1)

        lbl_green = QLabel("Green adjust:")
        lbl_green.setMinimumHeight(24)
        gt_layout.addWidget(lbl_green, 12, 0)
        self._green_spin = QSpinBox()
        self._green_spin.setRange(-255, 255)
        self._green_spin.setValue(0)
        self._green_spin.setPrefix("G ")
        self._green_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._green_spin, 12, 1)

        lbl_blue = QLabel("Blue adjust:")
        lbl_blue.setMinimumHeight(24)
        gt_layout.addWidget(lbl_blue, 13, 0)
        self._blue_spin = QSpinBox()
        self._blue_spin.setRange(-255, 255)
        self._blue_spin.setValue(0)
        self._blue_spin.setPrefix("B ")
        self._blue_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._blue_spin, 13, 1)

        lbl_alpha_adj = QLabel("Alpha adjust:")
        lbl_alpha_adj.setMinimumHeight(24)
        gt_layout.addWidget(lbl_alpha_adj, 14, 0)
        self._alpha_delta_spin = QSpinBox()
        self._alpha_delta_spin.setRange(-255, 255)
        self._alpha_delta_spin.setValue(0)
        self._alpha_delta_spin.setPrefix("A\u25b3 ")
        self._alpha_delta_spin.setMinimumHeight(28)
        gt_layout.addWidget(self._alpha_delta_spin, 14, 1)

        self._apply_rgb_check = QCheckBox("Apply RGBA adjustments")
        self._apply_rgb_check.setChecked(False)
        self._apply_rgb_check.setToolTip(
            "When checked, the Red/Green/Blue/Alpha deltas are applied on top of\n"
            "the alpha fix. Useful for colour-correcting game textures."
        )
        gt_layout.addWidget(self._apply_rgb_check, 15, 0, 1, 2)

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
        self._btn_save_preset.clicked.connect(self._save_preset)
        self._btn_delete_preset.clicked.connect(self._delete_preset)
        self._preset_combo.currentTextChanged.connect(self._on_preset_changed)
        self._alpha_spin.valueChanged.connect(self._alpha_slider.setValue)
        self._alpha_slider.valueChanged.connect(self._alpha_spin.setValue)
        # DropFileList signals
        self._file_list.paths_dropped.connect(self._add_to_list)
        self._file_list.count_changed.connect(self._update_file_count)
        # Selection → compare preview
        self._file_list.currentRowChanged.connect(self._on_selection_changed)
        # Fine-tune controls → refresh compare preview AND live params label
        self._alpha_spin.valueChanged.connect(self._on_finetune_changed)
        self._threshold_spin.valueChanged.connect(self._on_finetune_changed)
        # Cross-validate clamp min/max to enforce min ≤ max and prevent
        # invalid bounds being passed to np.clip().
        self._clamp_min_spin.valueChanged.connect(self._on_clamp_min_changed)
        self._clamp_max_spin.valueChanged.connect(self._on_clamp_max_changed)
        self._invert_check.toggled.connect(self._on_finetune_changed)
        self._binary_cut_check.toggled.connect(self._on_finetune_changed)
        self._apply_alpha_check.toggled.connect(self._on_apply_alpha_toggled)
        self._mode_combo.currentIndexChanged.connect(self._on_finetune_changed)
        self._use_preset_check.toggled.connect(self._update_compare)
        self._red_spin.valueChanged.connect(self._on_finetune_changed)
        self._green_spin.valueChanged.connect(self._on_finetune_changed)
        self._blue_spin.valueChanged.connect(self._on_finetune_changed)
        self._alpha_delta_spin.valueChanged.connect(self._on_finetune_changed)
        self._apply_rgb_check.toggled.connect(self._on_finetune_changed)
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
        mgr.register(self._preset_combo, "preset_combo")
        mgr.register(self._btn_save_preset, "save_preset")
        mgr.register(self._btn_delete_preset, "delete_preset")
        mgr.register(self._alpha_slider, "alpha_slider")
        mgr.register(self._alpha_spin, "alpha_spin")
        mgr.register(self._mode_combo, "mode_combo")
        mgr.register(self._threshold_spin, "threshold_spin")
        mgr.register(self._clamp_min_spin, "clamp_min_spin")
        mgr.register(self._clamp_max_spin, "clamp_max_spin")
        mgr.register(self._invert_check, "invert_check")
        mgr.register(self._binary_cut_check, "binary_cut_check")
        mgr.register(self._use_preset_check, "use_preset_check")
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

    def update_theme(self, theme_name: str) -> None:
        """Update inner header, section labels and group-box titles to match the active theme."""
        from .theme_engine import get_theme_tab_labels, get_theme_icon
        labels = get_theme_tab_labels(theme_name)
        # labels[0] is e.g. "🩸🖼  Alpha Fixer" – use it directly as the header
        self._hdr.setText(labels[0])
        # Decorate section labels and group-box titles with the theme's representative icon.
        icon = get_theme_icon(theme_name)
        self._lbl_files.setText(f"{icon}  Input Files / Folders  (drag & drop supported)")
        self._grp_out.setTitle(f"{icon}  Output")
        self._grp_preset.setTitle(f"{icon}  Preset")
        self._grp_tune.setTitle(f"{icon}  Fine-Tune Alpha && RGBA Channels")
        self._compare_lbl.setText(f"{icon}  Before / After Comparison  ◀▶ drag to compare")

    # ------------------------------------------------------------------
    # Preset management
    # ------------------------------------------------------------------

    def _populate_presets(self):
        self._preset_combo.blockSignals(True)
        current = self._preset_combo.currentText()
        self._preset_combo.clear()
        for i, p in enumerate(self._presets.all_presets()):
            self._preset_combo.addItem(p.name)
            # Show the preset description as a hover tooltip on each item
            if p.description:
                self._preset_combo.setItemData(
                    i, p.description, Qt.ItemDataRole.ToolTipRole
                )

        last = self._settings.get("last_alpha_preset", "")
        target = last if last else current
        idx = self._preset_combo.findText(target)
        self._preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._preset_combo.blockSignals(False)
        self._on_preset_changed(self._preset_combo.currentText())

    @pyqtSlot(str)
    def _on_preset_changed(self, name: str):
        preset = self._presets.get_preset(name)
        if preset is None:
            return
        self._settings.set("last_alpha_preset", name)
        self._preset_desc.setText(preset.description)
        # Block signals on fine-tune controls while we populate them so each
        # value change doesn't restart the debounce timer individually.
        finetune_controls = [
            self._apply_alpha_check,
            self._alpha_spin, self._alpha_slider,
            self._threshold_spin, self._clamp_min_spin, self._clamp_max_spin,
            self._invert_check, self._binary_cut_check,
        ]
        for c in finetune_controls:
            c.blockSignals(True)
        # If the preset has a fixed alpha_value, enable the "Apply fixed alpha" checkbox
        # and show that value.  If alpha_value is None (clamp-only presets), uncheck it
        # so the fine-tune shows the correct "clamp only" intent.
        has_value = preset.alpha_value is not None
        self._apply_alpha_check.setChecked(has_value)
        val = preset.alpha_value if has_value else 255
        self._alpha_spin.setValue(int(val))
        self._alpha_slider.setValue(int(val))
        self._alpha_spin.setEnabled(has_value)
        self._alpha_slider.setEnabled(has_value)
        self._threshold_spin.setValue(int(preset.threshold))
        self._clamp_min_spin.setValue(int(preset.clamp_min))
        self._clamp_max_spin.setValue(int(preset.clamp_max))
        self._invert_check.setChecked(bool(preset.invert))
        self._binary_cut_check.setChecked(bool(preset.binary_cut))
        for c in finetune_controls:
            c.blockSignals(False)
        self._btn_delete_preset.setEnabled(not preset.builtin)
        # Refresh live params label to match newly-loaded preset values
        self._refresh_finetune_label()
        # Refresh compare preview (via debounce so rapid preset changes don't
        # stack up many simultaneous background threads).
        self._preview_debounce.start()

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        apply_val = self._apply_alpha_check.isChecked()
        alpha_val = self._alpha_spin.value() if apply_val else None
        binary_cut = self._binary_cut_check.isChecked()
        desc_parts = []
        if alpha_val is not None:
            desc_parts.append(f"value={alpha_val}")
        desc_parts.append(
            f"threshold={self._threshold_spin.value()}  "
            f"clamp={self._clamp_min_spin.value()}–{self._clamp_max_spin.value()}"
        )
        if binary_cut:
            desc_parts.append("binary_cut=yes")
        preset = AlphaPreset(
            name=name,
            alpha_value=alpha_val,
            threshold=self._threshold_spin.value(),
            invert=self._invert_check.isChecked(),
            description=(
                f"Custom preset '{name}'  " + "  ".join(desc_parts)
            ),
            builtin=False,
            clamp_min=self._clamp_min_spin.value(),
            clamp_max=self._clamp_max_spin.value(),
            binary_cut=binary_cut,
        )
        saved = self._presets.save_custom_preset(preset)
        if not saved:
            QMessageBox.warning(self, "Save Preset", f"Cannot overwrite built-in preset '{name}'.")
            return
        self._populate_presets()
        idx = self._preset_combo.findText(name)
        if idx >= 0:
            self._preset_combo.setCurrentIndex(idx)
        self._log_msg(f"✔ Preset '{name}' saved.")

    def _delete_preset(self):
        name = self._preset_combo.currentText()
        reply = QMessageBox.question(
            self, "Delete Preset",
            f"Delete preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._presets.delete_custom_preset(name)
            self._populate_presets()
            self._log_msg(f"🗑 Preset '{name}' deleted.")

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _add_files(self):
        last_dir = self._settings.get("last_input_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Files", last_dir,
            "Images (*.png *.dds *.jpg *.jpeg *.bmp *.tiff *.tif *.webp *.tga *.ico *.gif *.ppm *.pcx *.avif *.qoi);;All Files (*)",
        )
        if paths:
            self._settings.set("last_input_dir", os.path.dirname(paths[0]))
            self._add_to_list(paths)

    def _add_folder(self):
        last_dir = self._settings.get("last_input_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", last_dir)
        if folder:
            self._settings.set("last_input_dir", folder)
            recursive = self._recursive_check.isChecked()
            files = collect_files([folder], recursive=recursive)
            self._add_to_list(files)

    def _add_to_list(self, paths: list[str]):
        """Add paths using the batch helper to stay responsive for large imports."""
        was_empty = self._file_list.count() == 0
        self._file_list.add_paths_batch(paths)
        # Auto-select the first item so the preview pane shows immediately
        if was_empty and self._file_list.count() > 0:
            self._file_list.setCurrentRow(0)
        # Notify main window so it can play the file-add sound
        if paths:
            self.files_added.emit()
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
            from ..core.rom_detector import detect_from_paths
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

    def _refresh_finetune_label(self) -> None:
        """Update the live fine-tune params summary label."""
        cmin   = self._clamp_min_spin.value()
        cmax   = self._clamp_max_spin.value()
        thresh = self._threshold_spin.value()
        parts  = []
        if self._apply_alpha_check.isChecked():
            mode = self._mode_combo.currentData() or "set"
            parts.append(f"{mode}={self._alpha_spin.value()}")
        else:
            parts.append("clamp only")
        if cmin > 0 or cmax < 255:
            parts.append(f"clamp={cmin}–{cmax}")
        if thresh:
            parts.append(f"thresh={thresh}")
        if self._invert_check.isChecked():
            parts.append("invert=yes")
        if self._binary_cut_check.isChecked():
            parts.append("binary_cut=yes")
        self._finetune_params_lbl.setText("  ·  ".join(parts))

    @pyqtSlot(bool)
    def _on_apply_alpha_toggled(self, checked: bool) -> None:
        """Enable/disable alpha value controls based on the 'Apply fixed alpha value' checkbox."""
        self._alpha_spin.setEnabled(checked)
        self._alpha_slider.setEnabled(checked)
        self._mode_combo.setEnabled(checked)
        self._refresh_finetune_label()
        self._preview_debounce.start()

    def _switch_to_manual_if_preset_active(self) -> None:
        """Uncheck 'Use preset' if it is currently active.

        Called from clamp-bound handlers before they directly call
        _refresh_finetune_label() and _preview_debounce.start().  Those
        handlers bypass _on_finetune_changed() so self.sender() would be None,
        preventing the automatic "Use preset → manual" switch from firing.
        This helper performs that switch explicitly so clamp edits always take
        effect regardless of how the handler was invoked.

        The ``signalsBlocked()`` guard ensures this method is a no-op when a
        preset is being loaded programmatically (all finetune-control signals
        are blocked during _on_preset_changed → _load_preset_values), so
        programmatic preset loads never accidentally switch to manual mode.
        """
        if not self._preset_combo.signalsBlocked() and self._use_preset_check.isChecked():
            was_blocked = self._use_preset_check.blockSignals(True)
            self._use_preset_check.setChecked(False)
            self._use_preset_check.blockSignals(was_blocked)

    @pyqtSlot(int)
    def _on_clamp_min_changed(self, value: int) -> None:
        """Ensure clamp_max >= clamp_min, then trigger the normal finetune update."""
        # Prevent clamp_max from falling below the new minimum without preventing
        # the user from typing an independent value into clamp_max later.
        if self._clamp_max_spin.value() < value:
            self._clamp_max_spin.blockSignals(True)
            self._clamp_max_spin.setValue(value)
            self._clamp_max_spin.blockSignals(False)
        self._switch_to_manual_if_preset_active()
        self._refresh_finetune_label()
        self._preview_debounce.start()

    @pyqtSlot(int)
    def _on_clamp_max_changed(self, value: int) -> None:
        """Ensure clamp_min <= clamp_max, then trigger the normal finetune update."""
        if self._clamp_min_spin.value() > value:
            self._clamp_min_spin.blockSignals(True)
            self._clamp_min_spin.setValue(value)
            self._clamp_min_spin.blockSignals(False)
        self._switch_to_manual_if_preset_active()
        self._refresh_finetune_label()
        self._preview_debounce.start()

    @pyqtSlot()
    def _on_finetune_changed(self, *args):
        """Refresh live params label and debounce the compare preview update.

        If the user directly edits a fine-tune control we automatically switch
        to manual mode (uncheck 'Use preset') so their new values are actually
        used for processing and the preview reflects what they typed.
        """
        sender = self.sender()
        if (
            sender is not None
            and sender is not self._use_preset_check
            and not self._preset_combo.signalsBlocked()
            and self._use_preset_check.isChecked()
        ):
            # Silently uncheck "Use preset" so fine-tune values take effect.
            was_blocked = self._use_preset_check.blockSignals(True)
            self._use_preset_check.setChecked(False)
            self._use_preset_check.blockSignals(was_blocked)
        self._refresh_finetune_label()
        self._preview_debounce.start()

    # ------------------------------------------------------------------
    # Compare preview
    # ------------------------------------------------------------------

    def _update_compare(self, *args):
        """Start a background load+process to update the before/after comparison."""
        if not self._preview_path:
            return

        # Disconnect the previous loader's signals before replacing it so that
        # a stale thread finishing late cannot overwrite the current result.
        # Do NOT wait for the thread — waiting blocks the UI thread and causes
        # severe lag when the user rapidly moves a slider.  The thread will
        # finish naturally; its signals are already disconnected.
        if self._preview_loader is not None:
            try:
                self._preview_loader.preview_ready.disconnect()
                self._preview_loader.failed.disconnect()
            except RuntimeError:
                pass  # already disconnected

        preset = None
        manual = None
        if self._use_preset_check.isChecked():
            preset = self._presets.get_preset(self._preset_combo.currentText())
        else:
            manual = {
                "value": self._alpha_spin.value() if self._apply_alpha_check.isChecked() else None,
                "mode": self._mode_combo.currentData() or "set",
                "threshold": self._threshold_spin.value(),
                "invert": self._invert_check.isChecked(),
                "clamp_min": self._clamp_min_spin.value(),
                "clamp_max": self._clamp_max_spin.value(),
                "binary_cut": self._binary_cut_check.isChecked(),
            }

        # Attach RGBA adjustments when enabled
        if self._apply_rgb_check.isChecked():
            rgb_params = {
                "r": self._red_spin.value(),
                "g": self._green_spin.value(),
                "b": self._blue_spin.value(),
                "a": self._alpha_delta_spin.value(),
            }
            if manual is not None:
                manual["rgb"] = rgb_params
            else:
                # preset mode + RGBA adjust: build a passthrough manual with rgb
                manual = {
                    "value": None,
                    "threshold": 0,
                    "invert": False,
                    "clamp_min": 0,
                    "clamp_max": 255,
                    "rgb": rgb_params,
                }

        self._compare.set_loading()
        self._preview_loader = _AlphaPreviewLoader(
            self._preview_path, preset=preset, manual_params=manual
        )
        self._preview_loader.preview_ready.connect(self._on_compare_ready)
        self._preview_loader.stats_ready.connect(self._on_stats_ready)
        self._preview_loader.failed.connect(self._on_compare_failed)
        self._preview_loader.start()

    @pyqtSlot(QImage, QImage)
    def _on_compare_ready(self, before_qi: QImage, after_qi: QImage):
        self._compare.set_before(before_qi)
        self._compare.set_after(after_qi)

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

        preset = None
        manual = None
        if self._use_preset_check.isChecked():
            preset = self._presets.get_preset(self._preset_combo.currentText())
        else:
            manual = {
                "value": self._alpha_spin.value() if self._apply_alpha_check.isChecked() else None,
                "mode": self._mode_combo.currentData() or "set",
                "threshold": self._threshold_spin.value(),
                "invert": self._invert_check.isChecked(),
                "clamp_min": self._clamp_min_spin.value(),
                "clamp_max": self._clamp_max_spin.value(),
                "binary_cut": self._binary_cut_check.isChecked(),
            }

        # Attach RGB channel adjustments if the checkbox is enabled
        if self._apply_rgb_check.isChecked():
            rgb_params = {
                "r": self._red_spin.value(),
                "g": self._green_spin.value(),
                "b": self._blue_spin.value(),
                "a": self._alpha_delta_spin.value(),
            }
            if manual is not None:
                manual["rgb"] = rgb_params
            else:
                manual = {
                    "value": None,
                    "threshold": 0,
                    "invert": False,
                    "clamp_min": 0,
                    "clamp_max": 255,
                    "rgb": rgb_params,
                }

        out_dir = self._out_dir_edit.text().strip() or None
        suffix = self._suffix_edit.text().strip()

        # Remember for history recording in _on_finished
        self._last_run_files = expanded
        self._last_run_preset = (
            self._preset_combo.currentText() if self._use_preset_check.isChecked() else "manual"
        )

        self._log.clear()
        self._progress.setValue(0)
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._status_lbl.setText("Processing…")
        self._batch_start_time = time.monotonic()
        self._batch_total = len(expanded)

        self._worker = AlphaWorker(
            files=expanded,
            preset=preset,
            manual_params=manual,
            output_dir=out_dir,
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
        self._log_msg(f"{icon} {name}" + ("" if ok else f"  →  {msg.splitlines()[-1] if msg else ''}"))

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

    def _log_msg(self, msg: str):
        self._log.append(msg)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
