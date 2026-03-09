"""
Alpha Fixer tab widget.
"""
import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QSlider, QCheckBox, QFileDialog,
    QProgressBar, QGroupBox,
    QGridLayout, QLineEdit, QSplitter,
    QMessageBox, QInputDialog, QTextEdit,
)

from ..core.presets import AlphaPreset, PresetManager
from ..core.alpha_processor import collect_files, SUPPORTED_READ
from ..core.worker import AlphaWorker
from .drop_list import DropFileList


class AlphaFixerTab(QWidget):
    def __init__(self, preset_manager: PresetManager, settings_manager, parent=None):
        super().__init__(parent)
        self._presets = preset_manager
        self._settings = settings_manager
        self._worker = None
        self._setup_ui()
        self._populate_presets()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Header
        hdr = QLabel("🐼  Alpha Fixer")
        hdr.setObjectName("header")
        main_layout.addWidget(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ---- Left panel: input files ----
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
            self._settings.get("batch_recursive", True)
        )
        lv.addWidget(self._recursive_check)

        self._file_list = DropFileList()
        self._file_list.setToolTip(
            "Files queued for processing.\n"
            "• Drag files/folders here from Explorer/Finder\n"
            "• Delete key or right-click → Remove Selected"
        )
        lv.addWidget(self._file_list)

        self._file_count_lbl = QLabel("0 files")
        self._file_count_lbl.setObjectName("subheader")
        lv.addWidget(self._file_count_lbl)

        splitter.addWidget(left)

        # ---- Right panel: options ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)

        # Preset section
        grp_preset = QGroupBox("Preset")
        gp_layout = QVBoxLayout(grp_preset)

        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(160)
        self._btn_save_preset = QPushButton("Save")
        self._btn_delete_preset = QPushButton("Delete")
        preset_row.addWidget(QLabel("Preset:"))
        preset_row.addWidget(self._preset_combo, 1)
        preset_row.addWidget(self._btn_save_preset)
        preset_row.addWidget(self._btn_delete_preset)
        gp_layout.addLayout(preset_row)

        self._preset_desc = QLabel("")
        self._preset_desc.setWordWrap(True)
        self._preset_desc.setObjectName("subheader")
        gp_layout.addWidget(self._preset_desc)

        rv.addWidget(grp_preset)

        # Fine-tune section
        grp_tune = QGroupBox("Fine-Tune Alpha")
        gt_layout = QGridLayout(grp_tune)

        gt_layout.addWidget(QLabel("Mode:"), 0, 0)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["set", "multiply", "add", "subtract", "clamp_min", "clamp_max"])
        gt_layout.addWidget(self._mode_combo, 0, 1)

        gt_layout.addWidget(QLabel("Alpha Value (0–255):"), 1, 0)
        self._alpha_spin = QSpinBox()
        self._alpha_spin.setRange(0, 255)
        self._alpha_spin.setValue(255)
        gt_layout.addWidget(self._alpha_spin, 1, 1)

        self._alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self._alpha_slider.setRange(0, 255)
        self._alpha_slider.setValue(255)
        gt_layout.addWidget(self._alpha_slider, 2, 0, 1, 2)

        gt_layout.addWidget(QLabel("Threshold (0=all pixels):"), 3, 0)
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(0, 255)
        self._threshold_spin.setValue(0)
        gt_layout.addWidget(self._threshold_spin, 3, 1)

        self._invert_check = QCheckBox("Invert Alpha")
        gt_layout.addWidget(self._invert_check, 4, 0, 1, 2)

        self._use_preset_check = QCheckBox("Use preset (ignore fine-tune)")
        self._use_preset_check.setChecked(True)
        gt_layout.addWidget(self._use_preset_check, 5, 0, 1, 2)

        rv.addWidget(grp_tune)

        # Output section
        grp_out = QGroupBox("Output")
        go_layout = QGridLayout(grp_out)

        go_layout.addWidget(QLabel("Output folder:"), 0, 0)
        out_row = QHBoxLayout()
        self._out_dir_edit = QLineEdit()
        self._out_dir_edit.setPlaceholderText("Same as source (default)")
        self._btn_out_dir = QPushButton("Browse")
        out_row.addWidget(self._out_dir_edit, 1)
        out_row.addWidget(self._btn_out_dir)
        go_layout.addLayout(out_row, 0, 1)

        go_layout.addWidget(QLabel("Filename suffix:"), 1, 0)
        self._suffix_edit = QLineEdit()
        self._suffix_edit.setPlaceholderText("e.g. _fixed  (blank=overwrite)")
        go_layout.addWidget(self._suffix_edit, 1, 1)

        rv.addWidget(grp_out)

        # Run controls
        run_row = QHBoxLayout()
        self._btn_run = QPushButton("▶  Process")
        self._btn_run.setObjectName("accent")
        self._btn_run.setMinimumHeight(42)
        self._btn_stop = QPushButton("■  Stop")
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

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(130)
        self._log.setPlaceholderText("Processing log…")
        rv.addWidget(self._log)

        rv.addStretch(1)
        splitter.addWidget(right)
        splitter.setSizes([340, 560])
        main_layout.addWidget(splitter, 1)

        # ---- Connections ----
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

    # ------------------------------------------------------------------
    # Preset management
    # ------------------------------------------------------------------

    def _populate_presets(self):
        self._preset_combo.blockSignals(True)
        current = self._preset_combo.currentText()
        self._preset_combo.clear()
        for p in self._presets.all_presets():
            self._preset_combo.addItem(p.name)

        # Restore last-used preset from settings
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
        # Persist last selection
        self._settings.set("last_alpha_preset", name)
        # Show description
        self._preset_desc.setText(preset.description)
        # Mirror values into fine-tune controls
        self._mode_combo.setCurrentText(preset.fill_mode)
        val = preset.alpha_value if preset.alpha_value is not None else preset.fill_value
        self._alpha_spin.setValue(int(val))
        self._alpha_slider.setValue(int(val))
        self._threshold_spin.setValue(int(preset.threshold))
        self._invert_check.setChecked(bool(preset.invert))
        # Disable delete for built-ins
        self._btn_delete_preset.setEnabled(not preset.builtin)

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        preset = AlphaPreset(
            name=name,
            alpha_value=self._alpha_spin.value(),
            fill_mode=self._mode_combo.currentText(),
            fill_value=self._alpha_spin.value(),
            threshold=self._threshold_spin.value(),
            invert=self._invert_check.isChecked(),
            description=f"Custom preset '{name}'",
            builtin=False,
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
            recursive = self._recursive_check.isChecked()
            files = collect_files([folder], recursive=recursive)
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

    @pyqtSlot(int)
    def _update_file_count(self, n: int):
        self._file_count_lbl.setText(f"{n} file{'s' if n != 1 else ''}")

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

        # Expand any directories that were added (edge case)
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
                "mode": self._mode_combo.currentText(),
                "value": self._alpha_spin.value(),
                "threshold": self._threshold_spin.value(),
                "invert": self._invert_check.isChecked(),
            }

        out_dir = self._out_dir_edit.text().strip() or None
        suffix = self._suffix_edit.text().strip()

        self._log.clear()
        self._progress.setValue(0)
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._status_lbl.setText("Processing…")

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
        pct = int(current / max(total, 1) * 100)
        self._progress.setValue(pct)
        self._status_lbl.setText(f"Processing {current + 1}/{total}: {Path(path).name}")

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

    def _log_msg(self, msg: str):
        self._log.append(msg)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())


    # ------------------------------------------------------------------
