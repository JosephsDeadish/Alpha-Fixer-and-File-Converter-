"""
File Converter tab widget.
"""
import datetime
import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QFileDialog,
    QProgressBar, QGroupBox, QGridLayout, QScrollArea,
    QLineEdit, QSplitter, QMessageBox, QTextEdit,
)

from ..core.alpha_processor import collect_files
from ..core.file_converter import SUPPORTED_OUTPUT_FORMATS, OUTPUT_FORMAT_LIST
from ..core.worker import ConverterWorker
from .drop_list import DropFileList
from .preview_pane import ImagePreviewPane


class ConverterTab(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._worker = None
        # Track source files so we can record history
        self._last_run_files: list[str] = []
        self._last_run_format: str = ""
        self._setup_ui()
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        hdr = QLabel("🔄  File Converter")
        hdr.setObjectName("header")
        main_layout.addWidget(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ---- Left: input files + preview ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)

        lbl_files = QLabel("Input Files / Folders  (drag & drop supported)")
        lbl_files.setObjectName("section")
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
        self._file_list.setToolTip(
            "Files queued for conversion.\n"
            "• Drag files/folders here from Explorer/Finder\n"
            "• Delete key or right-click → Remove Selected"
        )
        lv.addWidget(self._file_list, 1)

        self._file_count_lbl = QLabel("0 files  |  F5 to convert  |  Esc to stop")
        self._file_count_lbl.setObjectName("subheader")
        lv.addWidget(self._file_count_lbl)

        # Preview pane
        self._preview = ImagePreviewPane()
        self._preview.setFixedHeight(260)
        lv.addWidget(self._preview)

        splitter.addWidget(left)

        # ---- Right: options ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(8)

        # Output format
        grp_fmt = QGroupBox("Output Format")
        gf_layout = QGridLayout(grp_fmt)
        gf_layout.setColumnStretch(0, 0)
        gf_layout.setColumnStretch(1, 1)
        gf_layout.setColumnMinimumWidth(0, 145)
        gf_layout.setHorizontalSpacing(12)
        gf_layout.setVerticalSpacing(8)

        gf_layout.addWidget(QLabel("Convert to:"), 0, 0)
        self._fmt_combo = QComboBox()
        self._fmt_combo.setMinimumWidth(130)
        for name, ext in OUTPUT_FORMAT_LIST:
            self._fmt_combo.addItem(f"{name}  ({ext})", userData=(name, ext))
        gf_layout.addWidget(self._fmt_combo, 0, 1)

        # Restore last-used format
        last_fmt = self._settings.get("last_converter_format", "PNG")
        idx = self._fmt_combo.findText(last_fmt, Qt.MatchFlag.MatchContains)
        if idx >= 0:
            self._fmt_combo.setCurrentIndex(idx)

        gf_layout.addWidget(QLabel("JPEG/WEBP quality:"), 1, 0)
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(1, 100)
        self._quality_spin.setValue(self._settings.get("last_converter_quality", 90))
        gf_layout.addWidget(self._quality_spin, 1, 1)

        rv.addWidget(grp_fmt)

        # Resize (optional)
        grp_resize = QGroupBox("Resize (optional)")
        gr_layout = QGridLayout(grp_resize)
        gr_layout.setColumnStretch(0, 0)
        gr_layout.setColumnStretch(1, 1)
        gr_layout.setColumnMinimumWidth(0, 80)
        gr_layout.setHorizontalSpacing(12)
        gr_layout.setVerticalSpacing(8)

        self._resize_check = QCheckBox("Enable resize")
        gr_layout.addWidget(self._resize_check, 0, 0, 1, 2)

        gr_layout.addWidget(QLabel("Width:"), 1, 0)
        self._width_spin = QSpinBox()
        self._width_spin.setRange(1, 32768)
        self._width_spin.setValue(1024)
        self._width_spin.setEnabled(False)
        gr_layout.addWidget(self._width_spin, 1, 1)

        gr_layout.addWidget(QLabel("Height:"), 2, 0)
        self._height_spin = QSpinBox()
        self._height_spin.setRange(1, 32768)
        self._height_spin.setValue(1024)
        self._height_spin.setEnabled(False)
        gr_layout.addWidget(self._height_spin, 2, 1)

        rv.addWidget(grp_resize)

        # Output folder
        grp_out = QGroupBox("Output")
        grp_out.setMinimumHeight(80)
        go_layout = QGridLayout(grp_out)
        go_layout.setColumnStretch(0, 0)
        go_layout.setColumnStretch(1, 1)
        go_layout.setColumnMinimumWidth(0, 120)
        go_layout.setHorizontalSpacing(12)
        go_layout.setVerticalSpacing(6)

        go_layout.addWidget(QLabel("Output folder:"), 0, 0)
        out_row = QHBoxLayout()
        self._out_dir_edit = QLineEdit()
        self._out_dir_edit.setPlaceholderText("Same as source (default)")
        saved_out = self._settings.get("converter_output_dir", "")
        if saved_out:
            self._out_dir_edit.setText(saved_out)
        self._btn_out_dir = QPushButton("Browse")
        out_row.addWidget(self._out_dir_edit, 1)
        out_row.addWidget(self._btn_out_dir)
        go_layout.addLayout(out_row, 0, 1)

        rv.addWidget(grp_out)

        # Run
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

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(70)
        self._log.setMaximumHeight(110)
        self._log.setPlaceholderText("Conversion log…")
        rv.addWidget(self._log, 1)

        right_scroll = QScrollArea()
        right_scroll.setWidget(right)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        splitter.addWidget(right_scroll)
        splitter.setSizes([340, 560])
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
        # DropFileList signals
        self._file_list.paths_dropped.connect(self._add_to_list)
        self._file_list.count_changed.connect(self._update_count)
        # Persist format/quality on change
        self._fmt_combo.currentIndexChanged.connect(self._save_format_setting)
        self._quality_spin.valueChanged.connect(
            lambda v: self._settings.set("last_converter_quality", v)
        )
        # Preview on selection change
        self._file_list.currentRowChanged.connect(self._on_selection_changed)

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
        mgr.register(self._out_dir_edit, "out_dir")
        mgr.register(self._recursive_check, "recursive_check")
        mgr.register(self._file_list, "file_list")

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _add_files(self):
        last_dir = self._settings.get("last_input_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Files", last_dir,
            "Images (*.png *.dds *.jpg *.jpeg *.bmp *.tiff *.tif *.webp *.tga *.ico *.gif);;All Files (*)",
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
        existing = {self._file_list.item(i).text() for i in range(self._file_list.count())}
        added = 0
        for p in paths:
            if p not in existing:
                self._file_list.addItem(p)
                existing.add(p)
                added += 1
        if added:
            self._file_list.count_changed.emit(self._file_list.count())
            # Auto-select the first item so the preview pane shows immediately
            if self._file_list.currentRow() < 0:
                self._file_list.setCurrentRow(0)

    @pyqtSlot(int)
    def _update_count(self, n: int):
        self._file_count_lbl.setText(
            f"{n} file{'s' if n != 1 else ''}  |  F5 to convert  |  Esc to stop"
        )

    @pyqtSlot(int)
    def _on_selection_changed(self, row: int):
        item = self._file_list.item(row)
        if item:
            self._preview.show_file(item.text())
        else:
            self._preview.clear()

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

        self._worker = ConverterWorker(
            files=expanded,
            target_format=target_format,
            target_ext=target_ext,
            output_dir=out_dir,
            input_root=input_root,
            quality=quality,
            resize=resize,
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
        pct = int(current / max(total, 1) * 100)
        self._progress.setValue(pct)
        self._status_lbl.setText(f"Converting {current + 1}/{total}: {Path(path).name}")

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

    def _log_msg(self, msg: str) -> None:
        self._log.append(msg)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())


