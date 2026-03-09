"""
Alpha Fixer tab widget.
"""
import datetime
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QKeySequence, QShortcut
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
from .preview_pane import BeforeAfterWidget


# ---------------------------------------------------------------------------
# Background worker: load + process one image for the comparison pane
# ---------------------------------------------------------------------------

class _AlphaPreviewLoader(QThread):
    """
    Load a single image, apply the current preset/manual settings,
    and emit both the original and processed images as QImages.
    """
    preview_ready = pyqtSignal(QImage, QImage)   # (before, after)
    failed = pyqtSignal(str)

    def __init__(self, path: str, preset=None, manual_params: dict | None = None):
        super().__init__()
        self._path = path
        self._preset = preset
        self._manual = manual_params

    def run(self):
        try:
            from ..core.alpha_processor import (
                load_image,
                apply_alpha_preset,
                apply_manual_alpha,
            )
            from .preview_pane import _pil_to_qimage

            orig = load_image(self._path)  # always RGBA PIL image

            before_qi = _pil_to_qimage(orig)

            if self._preset is not None:
                processed = apply_alpha_preset(orig, self._preset)
            elif self._manual is not None:
                processed = apply_manual_alpha(
                    orig,
                    mode=self._manual.get("mode", "set"),
                    value=self._manual.get("value", 255),
                    threshold=self._manual.get("threshold", 0),
                    invert=self._manual.get("invert", False),
                    clamp_min=self._manual.get("clamp_min", 0),
                    clamp_max=self._manual.get("clamp_max", 255),
                )
            else:
                processed = orig

            after_qi = _pil_to_qimage(processed)
            self.preview_ready.emit(before_qi, after_qi)
        except Exception as exc:
            import traceback
            self.failed.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------

class AlphaFixerTab(QWidget):
    def __init__(self, preset_manager: PresetManager, settings_manager, parent=None):
        super().__init__(parent)
        self._presets = preset_manager
        self._settings = settings_manager
        self._worker = None
        # Compare preview state
        self._preview_path: str | None = None
        self._preview_loader: _AlphaPreviewLoader | None = None
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

        # Header
        hdr = QLabel("🐼  Alpha Fixer")
        hdr.setObjectName("header")
        main_layout.addWidget(hdr)

        outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        outer_splitter.setChildrenCollapsible(False)

        # ==============================================================
        # Left panel: file list  +  before/after comparison
        # ==============================================================
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(6)

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

        # ---- Vertical splitter: file list (top) / compare (bottom) ----
        left_vsplit = QSplitter(Qt.Orientation.Vertical)
        left_vsplit.setChildrenCollapsible(False)

        # Top: file list
        list_area = QWidget()
        la_layout = QVBoxLayout(list_area)
        la_layout.setContentsMargins(0, 0, 0, 0)
        la_layout.setSpacing(4)

        self._file_list = DropFileList()
        self._file_list.setToolTip(
            "Files queued for processing.\n"
            "• Drag files/folders here from Explorer/Finder\n"
            "• Delete key or right-click → Remove Selected"
        )
        la_layout.addWidget(self._file_list, 1)

        self._file_count_lbl = QLabel("0 files  |  F5 to process  |  Esc to stop")
        self._file_count_lbl.setObjectName("subheader")
        la_layout.addWidget(self._file_count_lbl)

        left_vsplit.addWidget(list_area)

        # Bottom: before/after compare widget
        compare_area = QWidget()
        ca_layout = QVBoxLayout(compare_area)
        ca_layout.setContentsMargins(0, 0, 0, 0)
        ca_layout.setSpacing(2)

        compare_lbl = QLabel("Before / After Comparison  ◀▶ drag to compare")
        compare_lbl.setObjectName("section")
        compare_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ca_layout.addWidget(compare_lbl)

        self._compare = BeforeAfterWidget()
        self._compare.setMinimumHeight(200)
        ca_layout.addWidget(self._compare, 1)

        left_vsplit.addWidget(compare_area)
        left_vsplit.setSizes([220, 280])

        lv.addWidget(left_vsplit, 1)
        outer_splitter.addWidget(left)

        # ==============================================================
        # Right panel: presets + fine-tune + output + run controls
        # ==============================================================
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
        self._btn_run = QPushButton("▶  Process  [F5]")
        self._btn_run.setObjectName("accent")
        self._btn_run.setMinimumHeight(42)
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

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(130)
        self._log.setPlaceholderText("Processing log…")
        rv.addWidget(self._log)

        rv.addStretch(1)
        outer_splitter.addWidget(right)
        outer_splitter.setSizes([360, 540])
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
        # Fine-tune controls → refresh compare preview
        self._mode_combo.currentTextChanged.connect(self._on_finetune_changed)
        self._alpha_spin.valueChanged.connect(self._on_finetune_changed)
        self._threshold_spin.valueChanged.connect(self._on_finetune_changed)
        self._invert_check.toggled.connect(self._on_finetune_changed)
        self._use_preset_check.toggled.connect(self._update_compare)

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
        mgr.register(self._threshold_spin, "threshold_spin")
        mgr.register(self._invert_check, "invert_check")
        mgr.register(self._out_dir_edit, "out_dir")
        mgr.register(self._recursive_check, "recursive_check")
        mgr.register(self._file_list, "file_list")
        mgr.register(self._compare, "compare_widget")

    # ------------------------------------------------------------------
    # Preset management
    # ------------------------------------------------------------------

    def _populate_presets(self):
        self._preset_combo.blockSignals(True)
        current = self._preset_combo.currentText()
        self._preset_combo.clear()
        for p in self._presets.all_presets():
            self._preset_combo.addItem(p.name)

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
        self._mode_combo.setCurrentText(preset.fill_mode)
        val = preset.alpha_value if preset.alpha_value is not None else preset.fill_value
        self._alpha_spin.setValue(int(val))
        self._alpha_slider.setValue(int(val))
        self._threshold_spin.setValue(int(preset.threshold))
        self._invert_check.setChecked(bool(preset.invert))
        self._btn_delete_preset.setEnabled(not preset.builtin)
        # Refresh compare preview with the new preset
        self._update_compare()

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
        self._file_count_lbl.setText(
            f"{n} file{'s' if n != 1 else ''}  |  F5 to process  |  Esc to stop"
        )

    @pyqtSlot(int)
    def _on_selection_changed(self, row: int):
        item = self._file_list.item(row)
        if item and os.path.isfile(item.text()):
            self._preview_path = item.text()
            self._update_compare()
        else:
            self._preview_path = None
            self._compare.clear()

    @pyqtSlot()
    def _on_finetune_changed(self, *args):
        """Only refresh the compare when fine-tune mode is active."""
        if not self._use_preset_check.isChecked():
            self._update_compare()

    # ------------------------------------------------------------------
    # Compare preview
    # ------------------------------------------------------------------

    def _update_compare(self, *args):
        """Start a background load+process to update the before/after comparison."""
        if not self._preview_path:
            return

        # Cancel previous loader if still running (500 ms matches _ThumbLoader timeout)
        if self._preview_loader and self._preview_loader.isRunning():
            self._preview_loader.quit()
            self._preview_loader.wait(500)

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

        self._compare.set_loading()
        self._preview_loader = _AlphaPreviewLoader(
            self._preview_path, preset=preset, manual_params=manual
        )
        self._preview_loader.preview_ready.connect(self._on_compare_ready)
        self._preview_loader.failed.connect(self._on_compare_failed)
        self._preview_loader.start()

    @pyqtSlot(QImage, QImage)
    def _on_compare_ready(self, before_qi: QImage, after_qi: QImage):
        self._compare.set_before(before_qi)
        self._compare.set_after(after_qi)

    @pyqtSlot(str)
    def _on_compare_failed(self, err: str):
        self._compare.clear()
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
                "mode": self._mode_combo.currentText(),
                "value": self._alpha_spin.value(),
                "threshold": self._threshold_spin.value(),
                "invert": self._invert_check.isChecked(),
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

    def _log_msg(self, msg: str):
        self._log.append(msg)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
