"""
File Converter tab widget.
"""
import datetime
import os
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QFileDialog,
    QProgressBar, QGroupBox, QGridLayout, QScrollArea,
    QLineEdit, QSplitter, QMessageBox, QTextEdit,
)

from ..core.alpha_processor import collect_files
from ..core.file_converter import SUPPORTED_OUTPUT_FORMATS, OUTPUT_FORMAT_LIST, FORMAT_DESCRIPTIONS
from ..core.worker import ConverterWorker
from .drop_list import DropFileList
from .preview_pane import ImagePreviewPane


class ConverterTab(QWidget):
    """Tab widget for batch file-format conversion."""

    # Emitted after every successful batch: carries the count of files converted.
    # MainWindow connects this to check for processing-based theme unlocks.
    processing_done = pyqtSignal(int)
    # Emitted the very first time a conversion batch completes successfully.
    # MainWindow uses this to trigger the 'first conversion' theme unlock.
    first_conversion = pyqtSignal()
    # Emitted whenever at least one file is added to the queue.
    files_added = pyqtSignal()

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._worker = None
        # ETA tracking for large batch runs
        self._batch_start_time: float = 0.0
        self._batch_total: int = 0
        # Track source files so we can record history
        self._last_run_files: list[str] = []
        self._last_run_format: str = ""
        # Cached aspect ratio (w, h) of the currently selected file
        # to avoid re-opening the image on every width spinbox tick.
        self._cached_aspect: tuple[int, int] | None = None
        # Debounce timer: waits 150 ms after the last format/quality change
        # before refreshing the preview so rapid spin-box steps don't each
        # kick off a separate background conversion.
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(150)
        self._preview_debounce.timeout.connect(self._update_converted_preview)
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
        _conv_prefix = _default_labels[1].split("  ", 1)[0] if "  " in _default_labels[1] else "🔄"
        hdr = QLabel(f"{_conv_prefix}  File Converter")
        hdr.setObjectName("header")
        self._hdr = hdr
        main_layout.addWidget(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ---- Left: input files + preview ----
        left = QWidget()
        left.setMinimumWidth(320)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)

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
            self._settings.get("converter_recursive", True)
        )
        lv.addWidget(self._recursive_check)

        self._file_list = DropFileList()
        self._file_list.setMinimumHeight(180)
        self._file_list.setToolTip(
            "Files queued for conversion.\n"
            "• Drag files/folders here from Explorer/Finder\n"
            "• Delete key or right-click → Remove Selected\n"
            "• Right-click → Thumbnails to toggle image previews"
        )
        lv.addWidget(self._file_list, 1)

        self._file_count_lbl = QLabel("0 files  |  F5 to convert  |  Esc to stop")
        self._file_count_lbl.setObjectName("subheader")
        lv.addWidget(self._file_count_lbl)

        # Output folder – placed here (adjacent to input) so source and
        # destination are together and the layout reads top-to-bottom.
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

        lbl_out = QLabel("Output folder:")
        lbl_out.setMinimumWidth(100)
        lbl_out.setMinimumHeight(24)
        go_layout.addWidget(lbl_out, 0, 0)
        out_row = QHBoxLayout()
        self._out_dir_edit = QLineEdit()
        self._out_dir_edit.setPlaceholderText("Same as source (default)")
        self._out_dir_edit.setMinimumHeight(28)
        saved_out = self._settings.get("converter_output_dir", "")
        if saved_out:
            self._out_dir_edit.setText(saved_out)
        self._btn_out_dir = QPushButton("Browse…")
        self._btn_out_dir.setMinimumWidth(80)
        self._btn_out_dir.setMinimumHeight(28)
        out_row.addWidget(self._out_dir_edit, 1)
        out_row.addWidget(self._btn_out_dir)
        go_layout.addLayout(out_row, 0, 1)

        lbl_suffix = QLabel("Filename suffix:")
        lbl_suffix.setMinimumHeight(24)
        go_layout.addWidget(lbl_suffix, 1, 0)
        self._suffix_edit = QLineEdit()
        self._suffix_edit.setPlaceholderText("e.g. _converted  (blank = overwrite source)")
        self._suffix_edit.setMinimumHeight(28)
        go_layout.addWidget(self._suffix_edit, 1, 1)

        lv.addWidget(grp_out)

        # Preview pane
        self._preview = ImagePreviewPane()
        self._preview.setMinimumHeight(220)
        lv.addWidget(self._preview, 1)

        splitter.addWidget(left)

        # ---- Right: options ----
        right = QWidget()
        right.setMinimumWidth(360)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(8)

        # Run controls – at the very top so the Convert button is always
        # immediately visible when the tab is opened.
        run_row = QHBoxLayout()
        self._btn_run = QPushButton("▶  Convert  [F5]")
        self._btn_run.setObjectName("accent")
        self._btn_run.setMinimumHeight(40)
        self._btn_stop = QPushButton("■  Stop  [Esc]")
        self._btn_stop.setEnabled(False)
        run_row.addWidget(self._btn_run, 1)
        run_row.addWidget(self._btn_stop)
        rv.addLayout(run_row)

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        rv.addWidget(self._progress)

        self._status_lbl = QLabel("Ready.")
        self._status_lbl.setObjectName("subheader")
        rv.addWidget(self._status_lbl)

        # Output format
        grp_fmt = QGroupBox("Output Format")
        self._grp_fmt = grp_fmt
        gf_layout = QGridLayout(grp_fmt)
        gf_layout.setContentsMargins(10, 14, 10, 12)
        gf_layout.setColumnStretch(0, 0)
        gf_layout.setColumnStretch(1, 1)
        gf_layout.setColumnMinimumWidth(0, 140)
        gf_layout.setHorizontalSpacing(12)
        gf_layout.setVerticalSpacing(10)

        lbl_fmt = QLabel("Convert to:")
        lbl_fmt.setMinimumHeight(24)
        gf_layout.addWidget(lbl_fmt, 0, 0)
        self._fmt_combo = QComboBox()
        self._fmt_combo.setMinimumWidth(140)
        self._fmt_combo.setMinimumHeight(28)
        for i, (name, ext) in enumerate(OUTPUT_FORMAT_LIST):
            self._fmt_combo.addItem(f"{name}  ({ext})", userData=(name, ext))
            desc = FORMAT_DESCRIPTIONS.get(name, "")
            if desc:
                self._fmt_combo.setItemData(i, desc, Qt.ItemDataRole.ToolTipRole)
        gf_layout.addWidget(self._fmt_combo, 0, 1)

        # Restore last-used format
        last_fmt = self._settings.get("last_converter_format", "PNG")
        idx = self._fmt_combo.findText(last_fmt, Qt.MatchFlag.MatchContains)
        if idx >= 0:
            self._fmt_combo.setCurrentIndex(idx)

        lbl_quality = QLabel("JPEG/WEBP quality:")
        lbl_quality.setMinimumHeight(24)
        gf_layout.addWidget(lbl_quality, 1, 0)
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(1, 100)
        self._quality_spin.setMinimumHeight(28)
        self._quality_spin.setValue(self._settings.get("last_converter_quality", 90))
        gf_layout.addWidget(self._quality_spin, 1, 1)

        self._keep_metadata_check = QCheckBox("Preserve metadata (EXIF/ICC)")
        self._keep_metadata_check.setChecked(
            bool(self._settings.get("converter_keep_metadata", False))
        )
        self._keep_metadata_check.setToolTip(
            "Copy EXIF, ICC profile, and DPI data from the source file to the output.\n"
            "Supported for JPEG, PNG, WEBP, and TIFF outputs."
        )
        gf_layout.addWidget(self._keep_metadata_check, 2, 0, 1, 2)

        rv.addWidget(grp_fmt)

        # Resize (optional)
        grp_resize = QGroupBox("Resize (optional)")
        self._grp_resize = grp_resize
        gr_layout = QGridLayout(grp_resize)
        gr_layout.setContentsMargins(10, 14, 10, 12)
        gr_layout.setColumnStretch(0, 0)
        gr_layout.setColumnStretch(1, 1)
        gr_layout.setColumnMinimumWidth(0, 80)
        gr_layout.setHorizontalSpacing(12)
        gr_layout.setVerticalSpacing(10)

        self._resize_check = QCheckBox("Enable resize")
        gr_layout.addWidget(self._resize_check, 0, 0, 1, 2)

        lbl_w = QLabel("Width:")
        lbl_w.setMinimumHeight(24)
        gr_layout.addWidget(lbl_w, 1, 0)
        self._width_spin = QSpinBox()
        self._width_spin.setRange(1, 32768)
        self._width_spin.setValue(1024)
        self._width_spin.setMinimumHeight(26)
        self._width_spin.setEnabled(False)
        gr_layout.addWidget(self._width_spin, 1, 1)

        lbl_h = QLabel("Height:")
        lbl_h.setMinimumHeight(24)
        gr_layout.addWidget(lbl_h, 2, 0)
        self._height_spin = QSpinBox()
        self._height_spin.setRange(1, 32768)
        self._height_spin.setValue(1024)
        self._height_spin.setMinimumHeight(26)
        self._height_spin.setEnabled(False)
        gr_layout.addWidget(self._height_spin, 2, 1)

        self._lock_aspect_check = QCheckBox("Lock aspect ratio")
        self._lock_aspect_check.setEnabled(False)
        self._lock_aspect_check.setChecked(True)
        gr_layout.addWidget(self._lock_aspect_check, 3, 0, 1, 2)

        rv.addWidget(grp_resize)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(80)
        self._log.setPlaceholderText("Conversion log…")
        rv.addWidget(self._log, 1)

        right_scroll = QScrollArea()
        right_scroll.setWidget(right)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        right_scroll.setMinimumWidth(360)
        splitter.addWidget(right_scroll)
        splitter.setSizes([440, 580])
        main_layout.addWidget(splitter, 1)

        # ---- Connections ----
        self._btn_add_files.clicked.connect(self._add_files)
        self._btn_add_folder.clicked.connect(self._add_folder)
        self._btn_clear.clicked.connect(self._file_list._clear_all)
        self._btn_run.clicked.connect(self._run)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_out_dir.clicked.connect(self._browse_out_dir)
        self._resize_check.toggled.connect(self._width_spin.setEnabled)
        self._resize_check.toggled.connect(self._height_spin.setEnabled)
        self._resize_check.toggled.connect(self._lock_aspect_check.setEnabled)
        # When width changes and lock is on, update height proportionally
        self._width_spin.valueChanged.connect(self._on_width_changed)
        # DropFileList signals
        self._file_list.paths_dropped.connect(self._add_to_list)
        self._file_list.count_changed.connect(self._update_count)
        # Persist format/quality on change; also refresh live preview
        self._fmt_combo.currentIndexChanged.connect(self._save_format_setting)
        self._fmt_combo.currentIndexChanged.connect(self._on_format_changed)
        self._quality_spin.valueChanged.connect(self._on_quality_changed)
        self._keep_metadata_check.toggled.connect(
            lambda v: self._settings.set("converter_keep_metadata", v)
        )
        # Preview on selection change
        self._file_list.currentRowChanged.connect(self._on_selection_changed)
        # Initialise quality spinbox enabled state for the default format
        self._on_format_changed(self._fmt_combo.currentIndex())

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
        mgr.register(self._btn_run, "convert_btn")
        mgr.register(self._btn_stop, "stop_btn")
        mgr.register(self._fmt_combo, "format_combo")
        mgr.register(self._quality_spin, "quality_spin")
        mgr.register(self._resize_check, "resize_check")
        mgr.register(self._width_spin, "width_spin")
        mgr.register(self._height_spin, "height_spin")
        mgr.register(self._out_dir_edit, "out_dir")
        mgr.register(self._btn_out_dir, "out_dir_browse")
        mgr.register(self._recursive_check, "recursive_check")
        mgr.register(self._file_list, "file_list")
        mgr.register(self._suffix_edit, "suffix_edit")
        mgr.register(self._keep_metadata_check, "keep_metadata_check")
        mgr.register(self._file_count_lbl, "conv_file_count_lbl")
        mgr.register(self._log, "processing_log")
        mgr.register(self._progress, "processing_progress")
        mgr.register(self._status_lbl, "conv_status_lbl")
        mgr.register(self._lock_aspect_check, "lock_aspect_check")

    def update_theme(self, theme_name: str) -> None:
        """Update inner header, section labels and group-box titles to match the active theme."""
        from .theme_engine import get_theme_tab_labels, get_theme_icon
        labels = get_theme_tab_labels(theme_name)
        # labels[1] is e.g. "🩸🔄  Converter" – extract the emoji prefix by splitting on
        # the first double-space separator, then rebuild with "File Converter" as the title.
        converter_label = labels[1]
        prefix = converter_label.split("  ", 1)[0] if "  " in converter_label else ""
        self._hdr.setText(f"{prefix}  File Converter")
        # Decorate section labels and group-box titles with the theme's representative icon.
        icon = get_theme_icon(theme_name)
        self._lbl_files.setText(f"{icon}  Input Files / Folders  (drag & drop supported)")
        self._grp_out.setTitle(f"{icon}  Output")
        self._grp_fmt.setTitle(f"{icon}  Output Format")
        self._grp_resize.setTitle(f"{icon}  Resize (optional)")
        self._preview.update_theme(icon)

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
            files = collect_files([folder], recursive=self._recursive_check.isChecked())
            self._add_to_list(files)

    def _add_to_list(self, paths: list[str]):
        """Add paths using the batch helper to stay responsive for large imports."""
        was_empty = self._file_list.count() == 0
        self._file_list.add_paths_batch(paths)
        if was_empty and self._file_list.count() > 0:
            self._file_list.setCurrentRow(0)
        if paths:
            self.files_added.emit()

    @pyqtSlot(int)
    def _update_count(self, n: int):
        self._file_count_lbl.setText(
            f"{n} file{'s' if n != 1 else ''}  |  F5 to convert  |  Esc to stop"
        )

    @pyqtSlot(int)
    def _on_selection_changed(self, row: int):
        item = self._file_list.item(row)
        # Invalidate cached aspect ratio whenever the selection changes
        self._cached_aspect = None
        if item:
            self._refresh_preview(item.text())
        else:
            self._preview.clear()

    @pyqtSlot(int)
    def _on_format_changed(self, _index: int):
        """Enable quality spinbox only for formats that support it (JPEG/WEBP/AVIF)."""
        fmt_data = self._fmt_combo.currentData()
        fmt = fmt_data[0] if fmt_data else ""
        self._quality_spin.setEnabled(fmt in ("JPEG", "WEBP", "AVIF"))
        self._preview_debounce.start()

    @pyqtSlot(int)
    def _on_quality_changed(self, value: int):
        self._settings.set("last_converter_quality", value)
        # Only debounce the preview refresh if quality affects the output format
        fmt_data = self._fmt_combo.currentData()
        fmt = fmt_data[0] if fmt_data else ""
        if fmt in ("JPEG", "WEBP", "AVIF"):
            self._preview_debounce.start()

    @pyqtSlot(int)
    def _on_width_changed(self, width: int) -> None:
        """Update height proportionally when lock aspect ratio is checked."""
        if not (self._lock_aspect_check.isChecked() and
                self._resize_check.isChecked()):
            return
        # Use cached aspect ratio to avoid re-opening the file on every tick
        if self._cached_aspect is None:
            row = self._file_list.currentRow()
            item = self._file_list.item(row)
            if item:
                try:
                    from PIL import Image
                    with Image.open(item.text()) as im:
                        self._cached_aspect = im.size
                except Exception:
                    return
        if self._cached_aspect is None:
            return
        orig_w, orig_h = self._cached_aspect
        if orig_w > 0:
            new_h = max(1, round(width * orig_h / orig_w))
            # Block signals to avoid recursive update
            self._height_spin.blockSignals(True)
            self._height_spin.setValue(new_h)
            self._height_spin.blockSignals(False)

    def _update_converted_preview(self):
        """Refresh the preview pane to reflect the current format and quality."""
        row = self._file_list.currentRow()
        item = self._file_list.item(row)
        if item:
            self._refresh_preview(item.text())

    def _refresh_preview(self, path: str) -> None:
        """Show *path* in the preview pane using the current format and quality."""
        fmt_data = self._fmt_combo.currentData()
        if fmt_data:
            self._preview.show_converted(path, fmt_data[0], self._quality_spin.value())
        else:
            self._preview.show_file(path)

    def _browse_out_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Output Folder")
        if folder:
            self._out_dir_edit.setText(folder)
            self._settings.set("converter_output_dir", folder)

    def _save_format_setting(self):
        fmt_data = self._fmt_combo.currentData()
        if fmt_data:
            self._settings.set("last_converter_format", fmt_data[0])

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

        fmt_data = self._fmt_combo.currentData()
        if not fmt_data:
            return
        target_format, target_ext = fmt_data

        out_dir = self._out_dir_edit.text().strip() or None
        suffix = self._suffix_edit.text().strip()
        quality = self._quality_spin.value()
        resize = None
        if self._resize_check.isChecked():
            resize = (self._width_spin.value(), self._height_spin.value())

        # Determine a common root directory for relative path preservation
        input_root = None
        if len(expanded) > 1:
            try:
                dirs = [os.path.dirname(f) for f in expanded]
                input_root = os.path.commonpath(dirs)
            except ValueError:
                pass

        # Remember for history
        self._last_run_files = expanded
        self._last_run_format = target_format

        self._log.clear()
        self._progress.setValue(0)
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._status_lbl.setText("Converting…")
        self._batch_start_time = time.monotonic()
        self._batch_total = len(expanded)

        self._worker = ConverterWorker(
            files=expanded,
            target_format=target_format,
            target_ext=target_ext,
            output_dir=out_dir,
            input_root=input_root,
            quality=quality,
            resize=resize,
            keep_metadata=self._keep_metadata_check.isChecked(),
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
            f"Converting {current + 1}/{total}: {Path(path).name}{eta_str}"
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

        # Refresh preview for the currently selected file so the pane stays
        # in sync after conversion (e.g. if the file was converted in-place).
        row = self._file_list.currentRow()
        item = self._file_list.item(row)
        if item:
            self._refresh_preview(item.text())

        # Record in history
        entry = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "format": self._last_run_format,
            "file_count": len(self._last_run_files),
            "success": success,
            "errors": errors,
            "files": [Path(f).name for f in self._last_run_files[:10]],  # trim for storage
        }
        self._settings.add_converter_history(entry)
        # Notify main window so processing-based theme unlocks can fire
        if success > 0:
            self.processing_done.emit(success)
            # Emit first_conversion signal the very first time conversion succeeds
            if not self._settings.get("conversion_done_once", False):
                self._settings.set("conversion_done_once", True)
                self.first_conversion.emit()

    def _log_msg(self, msg: str) -> None:
        self._log.append(msg)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())


