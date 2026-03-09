"""
File Converter tab widget.
"""
import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QSlider, QCheckBox, QFileDialog,
    QListWidget, QProgressBar, QGroupBox, QGridLayout,
    QLineEdit, QSplitter, QMessageBox, QTextEdit, QSizePolicy,
)

from ..core.alpha_processor import collect_files
from ..core.file_converter import SUPPORTED_OUTPUT_FORMATS, OUTPUT_FORMAT_LIST
from ..core.worker import ConverterWorker


class ConverterTab(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._worker = None
        self._files: list[str] = []
        self._setup_ui()

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

        # ---- Left: input files ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)

        lbl_files = QLabel("Input Files / Folders")
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

        self._file_list = QListWidget()
        self._file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        lv.addWidget(self._file_list)

        self._file_count_lbl = QLabel("0 files")
        self._file_count_lbl.setObjectName("subheader")
        lv.addWidget(self._file_count_lbl)

        splitter.addWidget(left)

        # ---- Right: options ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)

        # Output format
        grp_fmt = QGroupBox("Output Format")
        gf_layout = QGridLayout(grp_fmt)

        gf_layout.addWidget(QLabel("Convert to:"), 0, 0)
        self._fmt_combo = QComboBox()
        for name, ext in OUTPUT_FORMAT_LIST:
            self._fmt_combo.addItem(f"{name}  ({ext})", userData=(name, ext))
        gf_layout.addWidget(self._fmt_combo, 0, 1)

        gf_layout.addWidget(QLabel("JPEG/WEBP quality:"), 1, 0)
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(1, 100)
        self._quality_spin.setValue(90)
        gf_layout.addWidget(self._quality_spin, 1, 1)

        rv.addWidget(grp_fmt)

        # Resize (optional)
        grp_resize = QGroupBox("Resize (optional)")
        gr_layout = QGridLayout(grp_resize)

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
        go_layout = QGridLayout(grp_out)

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
        self._btn_run = QPushButton("▶  Convert")
        self._btn_run.setObjectName("accent")
        self._btn_run.setMinimumHeight(42)
        self._btn_stop = QPushButton("■  Stop")
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
        self._log.setMaximumHeight(130)
        self._log.setPlaceholderText("Conversion log…")
        rv.addWidget(self._log)

        rv.addStretch(1)
        splitter.addWidget(right)
        splitter.setSizes([340, 560])
        main_layout.addWidget(splitter, 1)

        # ---- Connections ----
        self._btn_add_files.clicked.connect(self._add_files)
        self._btn_add_folder.clicked.connect(self._add_folder)
        self._btn_clear.clicked.connect(self._clear_files)
        self._btn_run.clicked.connect(self._run)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_out_dir.clicked.connect(self._browse_out_dir)
        self._resize_check.toggled.connect(self._width_spin.setEnabled)
        self._resize_check.toggled.connect(self._height_spin.setEnabled)

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
        for p in paths:
            if p not in existing:
                self._file_list.addItem(p)
                self._files.append(p)
        self._update_count()

    def _clear_files(self):
        self._file_list.clear()
        self._files.clear()
        self._update_count()

    def _update_count(self):
        n = self._file_list.count()
        self._file_count_lbl.setText(f"{n} file{'s' if n != 1 else ''}")

    def _browse_out_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Output Folder")
        if folder:
            self._out_dir_edit.setText(folder)
            self._settings.set("converter_output_dir", folder)

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
        self._log.append(f"{icon} {name}" + ("" if ok else f"  →  {msg.splitlines()[-1] if msg else ''}"))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    @pyqtSlot(int, int)
    def _on_finished(self, success: int, errors: int):
        self._progress.setValue(100)
        self._btn_run.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status_lbl.setText(f"Done. ✔ {success} succeeded, ✘ {errors} failed.")
        self._log.append(f"─── Finished: {success} ok, {errors} error(s) ───")
