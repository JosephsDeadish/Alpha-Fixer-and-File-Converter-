"""Shared asynchronous project/recovery UI for the two standalone builders."""
from __future__ import annotations

import os
from pathlib import Path
import time
import uuid

from PyQt6.QtCore import QEvent, QObject, QThread, QTimer, QLockFile, QItemSelectionModel, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidgetItem,
    QMessageBox, QPushButton, QCheckBox, QComboBox, QSizePolicy, QWidget, QVBoxLayout, QBoxLayout,
)

from src.core.app_paths import user_data_directory
from src.core.editor_projects import (
    GIF_CONTROLS, VIDEO_CONTROLS, MAX_ENTRIES, MAX_TOTAL_PIXELS,
    encode_png, missing_sources, prepare_entries, read_project, validate, write_project, source_signature, _source_stamp,
)


def approve_parent_projects(dialogs, resume):
    """Keep recovery sessions live until every editor accepts shutdown."""
    projects = [dialog._project for dialog in dialogs if getattr(dialog, "_project", None) is not None]
    approved = [project._closing_approved for project in projects]
    for project in projects:
        if not project.allow_parent_close(resume):
            for candidate, previous in zip(projects, approved):
                candidate._closing_approved = previous
            return False
    for project in projects:
        project.commit_close()
        if project.kind == "video":
            project.dialog._release_clips()
        else:
            project.dialog._preview_timer.stop()
            project.dialog._release_frames()
    return True


class _ProjectJob(QThread):
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.operation()
        except Exception as error:
            self.error = str(error)


class EditorProjectController(QObject):
    changed = pyqtSignal()

    def __init__(self, dialog, kind):
        super().__init__(dialog)
        self.dialog, self.kind = dialog, kind
        self.rules = GIF_CONTROLS if kind == "gif" else VIDEO_CONTROLS
        self.path = ""
        self.session_id = uuid.uuid4().hex
        self.recovery_directory = user_data_directory() / "editor-recovery"
        self.recovery_path = self.recovery_directory / f"{kind}-{self.session_id}.json"
        self._lock = None
        self._job = None
        self._loading = False
        self._closing_approved = False
        self._pending_close = None
        self._pending_close_provisional = False
        self._parent_resume = None
        self._retry_after = 0.0
        self._observed = self.fingerprint()
        self._saved = self._observed
        self._autosaved = self._observed
        self._changed_at = time.monotonic()
        self._last_autosave = time.monotonic()
        self._last_recovery_scan = 0.0
        self._recoveries = []
        self.header = QWidget(dialog._export_content)
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        self._row = row = QHBoxLayout()
        self.save_button = QPushButton("&Save Project…")
        self.open_button = QPushButton("&Open Project…")
        self.recover_button = QPushButton("&Recover…")
        self.status = QLabel("Project: new")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for button in (self.save_button, self.open_button, self.recover_button):
            row.addWidget(button)
            button.setAutoDefault(False)
            button.setAccessibleName(button.text().replace("&", ""))
        # Project controls share the builder's scroll/reflow and focus-reveal path.
        header_layout.addLayout(row)
        header_layout.addWidget(self.status)
        dialog._export_content.layout().insertWidget(1, self.header)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._reflow_header)
        dialog._content_scroll.viewport().installEventFilter(self)
        self.header.installEventFilter(self)
        self._layout_timer.start(0)
        self.save_button.clicked.connect(self.save_project)
        self.open_button.clicked.connect(self.open_project)
        self.recover_button.clicked.connect(self.recover_project)
        self.recover_button.setToolTip(
            "Alt+R: choose a retained autosave from a closed/crashed editor. Active sessions are excluded. "
            "Recovery copies are retained until you remove them manually."
        )
        self.save_button.setToolTip("Alt+S: save editable project (not media export). Ctrl+S still exports media.")
        self.open_button.setToolTip("Alt+O: open an editable project, replacing this editor's current contents.")
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.refresh_recoveries()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._layout_timer.start(0)
        return super().eventFilter(watched, event)

    def _reflow_header(self):
        available = self.dialog._content_scroll.viewport().width()
        buttons = (self.save_button, self.open_button, self.recover_button)
        required = sum(button.minimumSizeHint().width() for button in buttons)
        required += self._row.spacing() * (len(buttons) - 1)
        self._row.setDirection(QBoxLayout.Direction.LeftToRight if required <= available
                               else QBoxLayout.Direction.TopToBottom)
        self.header.setMaximumWidth(max(available, max(button.minimumSizeHint().width() for button in buttons)))

    @property
    def entries(self):
        return self.dialog._frames if self.kind == "gif" else self.dialog._clips

    @property
    def entry_list(self):
        return self.dialog._frame_list if self.kind == "gif" else self.dialog._clip_list

    def controls(self):
        result = {}
        for name in self.rules:
            control = getattr(self.dialog, name)
            result[name] = (control.isChecked() if isinstance(control, QCheckBox) else
                            control.currentData() if isinstance(control, QComboBox) else control.value())
        return result

    def fingerprint(self):
        # Embedded images are owned and immutable; never encode/hash their pixels on a slider tick.
        if self.kind == "gif":
            metadata = tuple((id(e._pil), e.source_path, e.source_kind, e.frame_index,
                              e.delay_ms, e.source_delay_ms) for e in self.entries)
        else:
            metadata = tuple((e.source_path, e.total_frames, e.fps, e.clip_type,
                              e.trim_start, e.trim_end, e.speed_percent, e.still_duration_frames,
                              e.preferred_video_stream_index, e.preferred_audio_stream_index,
                              e._split_timing) for e in self.entries)
        return metadata, tuple(self.controls().items())

    @property
    def dirty(self):
        return self.fingerprint() != self._saved

    def poll(self):
        if self._loading or self._closing_approved:
            return
        current = self.fingerprint()
        now = time.monotonic()
        if current != self._observed:
            self._observed = current
            self._changed_at = now
            self.status.setText("Project: unsaved edits" if self.dirty else "Project: saved")
            self.changed.emit()
        if now - self._last_recovery_scan > 30:
            self.refresh_recoveries()
        if (self._job is None and not self.dialog.is_exporting() and self.dirty
                and current != self._autosaved and now >= self._retry_after
                and (now - self._changed_at >= 5 or now - self._last_autosave >= 30)):
            self.autosave()

    def _document(self, destination):
        entries = []
        images = []
        initial_pixels = 0
        try:
            if len(self.entries) > MAX_ENTRIES:
                raise ValueError(f"Projects support at most {MAX_ENTRIES} entries")
            if self.kind == "gif" and sum(e._pil.width * e._pil.height for e in self.entries) > MAX_TOTAL_PIXELS:
                raise ValueError("GIF projects support 8,388,608 total pixels (32 MiB RGBA); reduce frames/resolution")
            for entry in self.entries:
                if self.kind == "gif":
                    image = entry._pil.copy()
                    images.append(image)
                    entries.append(dict(source=entry.source_path, source_kind=entry.source_kind,
                                        index=entry.frame_index, delay=entry.delay_ms,
                                        source_delay=entry.source_delay_ms, png="", size=list(image.size)))
                else:
                    source = Path(entry.source_path).absolute()
                    if not source.is_file():
                        raise ValueError(f"Missing original media: {source}")
                    try:
                        reference = os.path.relpath(source, Path(destination).absolute().parent)
                    except ValueError:  # Different Windows drive.
                        reference = str(source)
                    from .video_tool import _VIDEO_EXTS
                    loader = "video" if (source.suffix.lower() in _VIDEO_EXTS or entry.clip_type == "video"
                                         or entry.source_probe is not None) else "image"
                    if entry.frame_size:
                        width, height = entry.frame_size
                        if max(width, height) > 8192:
                            raise ValueError("Video project sources support dimensions up to 8192")
                        initial_pixels += width * height * (entry.total_frames if loader == "image" else 1)
                        if initial_pixels > MAX_TOTAL_PIXELS:
                            raise ValueError("Video project initial decoded images exceed 8,388,608 pixels")
                    entries.append(dict(source=reference, source_sha256="0" * 64,
                                         loader=loader, type=entry.clip_type,
                                        frames=entry.total_frames, fps=entry.fps,
                                        trim_start=entry.trim_start, trim_end=entry.trim_end,
                                        speed=entry.speed_percent, still_duration=entry.still_duration_frames,
                                        video_stream=entry.preferred_video_stream_index,
                                        audio_stream=entry.preferred_audio_stream_index,
                                        split=list(entry._split_timing) if entry._split_timing else None))
            document = dict(format="alpha-editor-project", version=1, kind=self.kind,
                            controls=self.controls(), entries=entries, selected=self.entry_list.currentRow(),
                            playhead=self.dialog._preview_idx if self.kind == "gif" else self.dialog._scrubber.value())
            validate(document, self.kind)
            return document, images
        except Exception:
            for image in images:
                image.close()
            raise

    def _busy(self):
        return self._job is not None or self._loading or self.dialog.is_exporting()

    def _start(self, operation, finished, *, loading=False):
        self._loading = loading
        self._job = job = _ProjectJob(operation, self)
        self.save_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.recover_button.setEnabled(False)
        if loading:
            self._was_playing = self.dialog._btn_play.isChecked()
            self._loading_shortcuts = [(shortcut, shortcut.isEnabled()) for shortcut in
                                       getattr(self.dialog, "_shortcut_objects", {}).values()]
            for shortcut, _enabled in self._loading_shortcuts:
                shortcut.setEnabled(False)
            self.dialog._preview_timer.stop()
            self.dialog._btn_play.setChecked(False)
            self.dialog._content_scroll.setEnabled(False)
        job.finished.connect(lambda: self._finish(job, finished, loading))
        job.start()

    def _finish(self, job, finished, loading):
        self._job = None
        self._loading = False
        if loading:
            self.dialog._content_scroll.setEnabled(True)
            for shortcut, enabled in self._loading_shortcuts:
                shortcut.setEnabled(enabled)
        self.save_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.recover_button.setEnabled(bool(self._recoveries))
        try:
            try:
                finished(job)
            except Exception as error:
                self._failure(str(error))
        finally:
            job.deleteLater()
            self.dialog._update_history_controls()
            if self._parent_resume is not None:
                resume = self._parent_resume
                self._parent_resume = None
                QTimer.singleShot(0, resume)

    def _failure(self, message, *, automatic=False):
        self.status.setText(f"Project {'autosave' if automatic else 'operation'} failed: {message}")
        self.status.setToolTip(str(self.recovery_path))
        self.dialog.status_notice.emit(self.status.text(), 10000)
        self._retry_after = time.monotonic() + 30
        if not automatic:
            QMessageBox.critical(self.dialog, "Project Error", message)

    def _save(self, destination, *, automatic=False):
        if self._busy():
            return False
        try:
            document, images = self._document(destination)
            source_entries = tuple(self.entries) if self.kind == "video" else ()
            identities = tuple((entry._source_stamp, entry._source_sha256) for entry in source_entries)
            captured = self.fingerprint()
            if automatic:
                self.recovery_directory.mkdir(parents=True, exist_ok=True)
                if self._lock is None:
                    lock = QLockFile(str(self.recovery_path) + ".lock")
                    lock.setStaleLockTime(0)
                    if not lock.tryLock(0):
                        raise OSError("Cannot claim this editor's recovery file")
                    self._lock = lock
        except Exception as error:
            for image in locals().get("images", []):
                image.close()
            self._failure(str(error), automatic=automatic)
            return False

        def operation():
            try:
                for entry, image in zip(document["entries"], images):
                    entry["png"] = encode_png(image)
                signatures = {}
                if self.kind == "video":
                    for entry, (stamp, expected_hash) in zip(document["entries"], identities):
                        source = str((Path(destination).absolute().parent / entry["source"]).resolve())
                        if stamp is None or (expected_hash is None and stamp != _source_stamp(os.stat(source))):
                            raise ValueError(f"Media changed since import: {source}")
                        if source not in signatures:
                            signatures[source] = source_signature(source)
                        if expected_hash is not None and signatures[source] != expected_hash:
                            raise ValueError(f"Media changed since project save/import: {source}")
                        if expected_hash is None and stamp != _source_stamp(os.stat(source)):
                            raise ValueError(f"Media changed during project I/O: {source}")
                        entry["source_sha256"] = signatures[source]
                write_project(destination, document)
                return tuple(entry["source_sha256"] for entry in document["entries"]) if self.kind == "video" else ()
            finally:
                for image in images:
                    image.close()

        def finished(job):
            if job.error:
                self._pending_close = None
                self._failure(job.error, automatic=automatic)
                return
            for entry, signature in zip(source_entries, job.result):
                entry._source_sha256 = signature
            if automatic:
                self._autosaved = captured
                self._last_autosave = time.monotonic()
                self.status.setText("Recovery saved • project has unsaved edits")
                self.status.setToolTip(str(self.recovery_path))
            else:
                self.path = str(destination)
                self._saved = captured
                self.status.setText(f"Project: {Path(destination).name}" + (" • unsaved edits" if self.dirty else " • saved"))
                if self._pending_close is not None and not self.dirty:
                    close = self._pending_close
                    self._pending_close = None
                    if not self._pending_close_provisional:
                        self.commit_close()
                    close()
                else:
                    self._pending_close = None
        self.status.setText("Saving recovery…" if automatic else "Saving project…")
        self._start(operation, finished)
        return True

    def save_project(self, _checked=False):
        if self._busy():
            return False
        path, _ = QFileDialog.getSaveFileName(
            self.dialog, "Save Editable Project", self.path, f"{self.kind.upper()} Project (*.{self.kind}project.json)")
        if not path:
            return False
        chosen_path = path
        if not path.lower().endswith(".json"):
            path += f".{self.kind}project.json"
        from ._ui_utils import confirm_normalized_save_path
        if not confirm_normalized_save_path(self.dialog, chosen_path, path):
            return False
        return self._save(path)

    def autosave(self):
        return self._save(self.recovery_path, automatic=True)

    def confirm_replace(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self.dialog, "Unsaved Project", "Replace unsaved project edits? Save Project first to keep them.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Discard

    def open_project(self, _checked=False):
        if self._busy():
            return
        path, _ = QFileDialog.getOpenFileName(
            self.dialog, "Open Editable Project", self.path, f"{self.kind.upper()} Project (*.json)")
        if path:
            self.load(path)

    def load(self, path, *, recovery=False):
        if self._busy():
            return False
        # Prepare first: even a discarded replacement must never destroy edits on decode failure.
        captured = self.fingerprint()
        was_playing = self.dialog._btn_play.isChecked()
        replacements = {}

        def operation():
            document = read_project(path, self.kind)
            return document, missing_sources(document, path)

        def failed(message):
            if was_playing:
                self.dialog._btn_play.setChecked(True)
            self._failure(f"Could not open project: {path}\n{message}\n"
                          "The current project has been kept intact.")

        def discovered(job):
            if job.error:
                failed(job.error)
                return
            document, missing = job.result
            # Modal file pickers run a nested event loop; suppress autosave and other
            # project actions until every replacement has been chosen.
            self._loading = True
            try:
                for source in missing:
                    replacement, _ = QFileDialog.getOpenFileName(
                        self.dialog,
                        f"Locate Missing Project Media (same original file): {source}",
                        str(Path(source).parent), "Media files (*)")
                    if not replacement:
                        self.status.setText(f"Project open cancelled • missing media: {source} • current project kept")
                        if was_playing:
                            self.dialog._btn_play.setChecked(True)
                        return
                    replacements[source] = replacement
            finally:
                self._loading = False
            self.status.setText("Verifying and preparing project media…")
            self._start(lambda: (document, prepare_entries(document, path, replacements)),
                        finished, loading=True)

        def finished(job):
            if job.error:
                failed(job.error)
                return
            document, entries = job.result
            for name, value in document["controls"].items():
                control = getattr(self.dialog, name)
                if isinstance(control, QComboBox) and control.findData(value) < 0:
                    for entry in entries:
                        entry.close()
                    failed(f"Project setting unavailable in this runtime: {value}")
                    return
            # The replacement prompt runs a nested event loop after the worker
            # finishes. Keep project actions/autosave blocked until install ends.
            self._loading = True
            try:
                if (captured != self.fingerprint() or not self.confirm_replace()
                        or captured != self.fingerprint()):
                    for entry in entries:
                        entry.close()
                    if was_playing:
                        self.dialog._btn_play.setChecked(True)
                    return
                try:
                    self._install(document, entries)
                except Exception as error:
                    failed(str(error))
                    return
            finally:
                self._loading = False
            self.path = "" if recovery else str(path)
            self._observed = self.fingerprint()
            unsaved = recovery or bool(replacements)
            self._saved = ((), ()) if unsaved else self._observed
            self._autosaved = ((), ()) if unsaved else self._observed
            self._changed_at = time.monotonic()
            self.status.setText(
                "Recovered edits • Save Project to keep them" if recovery else
                f"Project: {Path(path).name} • media relinked • Save Project to keep new paths" if replacements else
                f"Project: {Path(path).name} • saved")
        self.status.setText("Preparing project media…")
        self._start(operation, discovered, loading=True)
        return True

    def _install(self, document, entries):
        dialog = self.dialog
        from .gif_builder import _ENTRY_ROLE, _THUMB_W, _THUMB_H
        from .video_tool import _CLIP_ROLE, _format_clip_label
        # Build thumbnails/items before replacing any live resources.
        items = []
        try:
            for entry in entries:
                item = QListWidgetItem()
                if self.kind == "gif":
                    item.setData(_ENTRY_ROLE, entry)
                    item.setText(f"{Path(entry.source_path).stem or 'Frame'} [{entry.frame_index + 1}]")
                    item.setIcon(QIcon(entry.thumbnail(_THUMB_W, _THUMB_H)))
                else:
                    item.setData(_CLIP_ROLE, entry)
                    item.setText(_format_clip_label(entry, entry.source_path, "🎞" if entry.clip_type == "video" else "🖼"))
                items.append(item)
        except Exception:
            for entry in entries:
                entry.close()
            raise
        try:
            old = list(self.entries)
            previous = dict(controls=self.controls(), selected=self.entry_list.currentRow(),
                            playhead=dialog._preview_idx if self.kind == "gif" else dialog._scrubber.value(),
                            selected_rows=[self.entry_list.row(item) for item in self.entry_list.selectedItems()])
            old_items = [self.entry_list.item(i).clone() for i in range(self.entry_list.count())]
            if self.kind == "video":
                dialog._history_restoring = True
            try:
                self._apply_state(document, entries, items)
            except Exception:
                self._apply_state(previous, old, old_items)
                raise
            finally:
                if self.kind == "video":
                    dialog._history_restoring = False
        except Exception:
            for entry in entries:
                entry.close()
            raise
        if self.kind == "gif":
            self.dialog._history_entries.update(old)
            self.dialog._reset_frame_history()
        else:
            dialog._history_entries.update(old)
            dialog._reset_video_history()

    def _apply_state(self, document, entries, items):
        dialog = self.dialog
        self.entry_list.blockSignals(True)
        self.entry_list.clear()
        if self.kind == "gif":
            dialog._frames = entries
        else:
            dialog._clips = entries
        for item in items:
            self.entry_list.addItem(item)
        if "selected_rows" in document:
            # Rollback keeps multi-selection independent of the current/preview row.
            self.entry_list.setCurrentRow(document["selected"], QItemSelectionModel.SelectionFlag.NoUpdate)
            for row in document["selected_rows"]:
                self.entry_list.item(row).setSelected(True)
        else:
            self.entry_list.setCurrentRow(document["selected"])
        self.entry_list.blockSignals(False)
        # Block per-clip controls while setting global controls.
        for name, value in document["controls"].items():
            control = getattr(dialog, name)
            control.blockSignals(True)
            if isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, QComboBox):
                control.setCurrentIndex(control.findData(value))
            else:
                control.setValue(value)
            control.blockSignals(False)
        # Global-control listeners only refresh labels/preview; unlike timing controls
        # they do not rewrite the restored per-entry trim/split mappings.
        for name, value in document["controls"].items():
            control = getattr(dialog, name)
            if not isinstance(control, (QCheckBox, QComboBox)):
                control.valueChanged.emit(value)
        if self.kind == "gif":
            dialog._preview_idx = document["playhead"]
            dialog._on_delay_changed(dialog._delay_slider.value())
            dialog._on_selection_changed(document["selected"], update_preview=False)
            dialog._update_count()
            dialog._update_scrubber()
            dialog._update_preview_frame()
        else:
            dialog._on_clip_selected(document["selected"])
            dialog._update_scrubber()
            dialog._scrubber.setValue(document["playhead"])
            dialog._update_preview()
            dialog._update_ui_state()
            dialog._on_preview_fps_changed(dialog._fps_slider.value())
    def refresh_recoveries(self):
        self._last_recovery_scan = time.monotonic()
        candidates = []
        try:
            if self.recovery_directory.is_dir():
                for path in sorted(self.recovery_directory.glob(f"{self.kind}-*.json"))[:1000]:
                    if path == self.recovery_path:
                        continue
                    # QLockFile detects dead processes; a live writer's snapshot is not offered.
                    lock = QLockFile(str(path) + ".lock")
                    lock.setStaleLockTime(0)
                    if lock.tryLock(0):
                        lock.unlock()
                        candidates.append(path)
        except OSError as error:
            self.status.setText(f"Recovery discovery failed: {error}")
        self._recoveries = candidates
        label = f"Recover… ({len(candidates)})"
        self.recover_button.setText(f"&{label}")
        self.recover_button.setAccessibleName(label)
        self.recover_button.setEnabled(bool(candidates) and self._job is None)

    def recover_project(self, _checked=False):
        if self._busy():
            return
        self.refresh_recoveries()
        if not self._recoveries:
            self.status.setText("No closed/crashed editor recovery copies found")
            return
        try:
            labels = [f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.stat().st_mtime))} • {p.name}"
                      for p in self._recoveries]
        except OSError as error:
            self._failure(str(error))
            return
        label, accepted = QInputDialog.getItem(self.dialog, "Recover Project", "Retained recovery copies:", labels, 0, False)
        if accepted:
            self.load(self._recoveries[labels.index(label)], recovery=True)

    def allow_close(self, resume, *, provisional=False):
        if self._closing_approved:
            if not provisional:
                self.commit_close()
            return True

        if self._job is not None or self._loading:
            self.status.setText("Wait for project I/O to finish before closing")
            return False
        if self.dirty:
            answer = QMessageBox.question(
                self.dialog, "Unsaved Project", "Save editable project before closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Save:
                self._pending_close = resume
                self._pending_close_provisional = provisional
                if not self.save_project():
                    self._pending_close = None
                return False
        self._closing_approved = True
        if not provisional:
            self.commit_close()
        # Retain the last complete recovery even after clean close/save, never delete on I/O failure.
        return True

    def commit_close(self):
        self._closing_approved = True
        self._release_session()

    def _release_session(self):
        self.timer.stop()
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    def allow_parent_close(self, resume):
        if self._loading and self._job is None:
            self.status.setText("Finish locating project media before closing")
            return False
        if self._job is not None:
            self._parent_resume = resume
            self.status.setText("Finishing project I/O before application shutdown…")
            return False
        return self.allow_close(resume, provisional=True)

    def resume_session(self):
        if not self._closing_approved:
            return
        self._closing_approved = False
        self.session_id = uuid.uuid4().hex
        self.recovery_path = self.recovery_directory / f"{self.kind}-{self.session_id}.json"
        self.path = ""
        self._observed = self.fingerprint()
        self._saved = ((), tuple(self.controls().items()))
        self._autosaved = self._saved
        self._changed_at = self._last_autosave = time.monotonic()
        self.timer.start()
        self.refresh_recoveries()
