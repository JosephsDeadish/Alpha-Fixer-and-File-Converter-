"""Bounded project lifecycles using real Qt pickers and unsaved-edit prompts."""
from unittest.mock import Mock, patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialogButtonBox, QFileDialog, QInputDialog, QMessageBox,
)

from src.ui.editor_project import _ProjectJob, prepare_entries
from tests.test_editor_projects import app, dialogs, sources, wait


def drive_modals(operation, actions):
    """Drive modal Qt widgets without replacing the production dialog APIs."""
    pending = list(actions)
    visited, failures = [], []
    timer = QTimer()
    timer.setInterval(5)
    deadline = QTimer()
    deadline.setSingleShot(True)

    def dismiss():
        modal = QApplication.activeModalWidget()
        if modal is None or any(modal is previous for previous in visited):
            return
        visited.append(modal)
        try:
            expected, action = pending.pop(0)
            assert isinstance(modal, expected)
            if isinstance(modal, QFileDialog):
                if action is None:
                    QTest.keyClick(modal, Qt.Key.Key_Escape)
                else:
                    modal.setDirectory(str(action.parent))
                    modal.selectFile(action.name)
                    buttons = modal.findChild(QDialogButtonBox)
                    buttons.button(QDialogButtonBox.StandardButton.Save if
                                   modal.acceptMode() == QFileDialog.AcceptMode.AcceptSave else
                                   QDialogButtonBox.StandardButton.Open).click()
            elif isinstance(modal, QMessageBox):
                modal.button(action).click()
            else:
                combo = modal.findChild(QComboBox)
                index = next(i for i in range(combo.count()) if str(action) in combo.itemText(i))
                combo.setCurrentIndex(index)
                modal.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        except Exception as error:
            failures.append(error)
            modal.reject()

    def expired():
        failures.append(AssertionError("Qt modal workflow exceeded 10 seconds"))
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.reject()

    timer.timeout.connect(dismiss)
    deadline.timeout.connect(expired)
    timer.start()
    deadline.start(10000)
    try:
        operation()
        assert not failures, failures
        assert not pending
        return len(visited)
    finally:
        timer.stop()
        deadline.stop()
        visited.clear()


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_real_qt_project_cancel_recover_save_reopen_teardown(dialogs, tmp_path, kind):
    old_native = QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    widget = dialogs(kind)
    project = widget._project
    source, still = sources(tmp_path)
    workers = []
    resources = []
    live_resource_counts, live_worker_counts = [], []
    original_finish = project._finish
    modal_count = 0

    def track(entries):
        for entry in entries:
            entry.close = Mock(wraps=entry.close)
            images = ((entry._pil,) if kind == "gif" else
                      tuple(entry._get_frame._frames) if entry.clip_type == "gif" else
                      (entry._get_frame._img,))
            resources.append((entry, images))
        live_resource_counts.append(sum(not entry.close.call_count for entry, _ in resources))
        assert live_resource_counts[-1] <= (8 if kind == "gif" else 4)
        return entries

    def prepare(*args, **kwargs):
        return track(prepare_entries(*args, **kwargs))

    def finished(job, callback, loading):
        workers.append(job)
        original_finish(job, callback, loading)
        live_worker_counts.append(len(project.findChildren(_ProjectJob)))

    def flush():
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        assert all(sip.isdeleted(job) for job in workers)
        assert not project.findChildren(_ProjectJob)
        assert project._job is None and not project._loading

    try:
        with patch.object(project, "_finish", side_effect=finished), \
                patch("src.ui.editor_project.prepare_entries", side_effect=prepare):
            for cycle in range(3):
                widget.show()
                QApplication.processEvents()
                project.timer.stop()
                if kind == "gif":
                    widget._add_paths([str(source), str(still)])
                    control = widget._delay_slider
                else:
                    widget._load_image_paths([str(source), str(still)])
                    control = widget._brightness_slider
                track(project.entries)
                destination = tmp_path / f"saved-{cycle}.json"
                modal_count += drive_modals(
                    lambda: (project.save_button.click(), wait(widget)),
                    [(QFileDialog, destination)])
                assert project.path == str(destination) and not project.dirty
                control.setValue(control.value() + 1)
                assert project.autosave()
                wait(widget)
                recovery = project.recovery_path
                last_good = recovery.read_bytes()
                retained = tuple(project.entries), project.fingerprint()
                modal_count += drive_modals(project.open_button.click, [(QFileDialog, None)])
                assert (tuple(project.entries), project.fingerprint()) == retained
                modal_count += drive_modals(
                    lambda: (project.open_button.click(), wait(widget)),
                    [(QFileDialog, destination), (QMessageBox, QMessageBox.StandardButton.Cancel)])
                assert (tuple(project.entries), project.fingerprint()) == retained
                assert project.dirty and recovery.read_bytes() == last_good
                modal_count += drive_modals(
                    lambda: (project.open_button.click(), wait(widget)),
                    [(QFileDialog, destination), (QMessageBox, QMessageBox.StandardButton.Discard)])
                assert not project.dirty
                assert all(new is not old for new, old in zip(project.entries, retained[0]))
                control.setValue(control.value() + 2)
                assert project.autosave()
                wait(widget)
                last_good = recovery.read_bytes()
                retained = tuple(project.entries), project.fingerprint()
                modal_count += drive_modals(widget.close, [(QMessageBox, QMessageBox.StandardButton.Cancel)])
                assert widget.isVisible() and project._lock.isLocked()
                assert (tuple(project.entries), project.fingerprint()) == retained
                modal_count += drive_modals(widget.close, [(QMessageBox, QMessageBox.StandardButton.Discard)])
                flush()
                assert not widget.isVisible() and not project.entries
                assert not project._lock and not project.timer.isActive()
                assert not widget._preview_timer.isActive() and not widget._history_entries
                assert recovery.read_bytes() == last_good
                previous_id = project.session_id
                widget.show()
                QApplication.processEvents()
                project.timer.stop()
                assert project.session_id != previous_id and project.path == ""
                modal_count += drive_modals(
                    lambda: (project.recover_button.click(), wait(widget)),
                    [(QInputDialog, recovery.name)])
                assert project.dirty and project.path == ""
                assert recovery.read_bytes() == last_good
                restored = tmp_path / f"restored-{cycle}.json"
                modal_count += drive_modals(
                    lambda: (project.save_button.click(), wait(widget)),
                    [(QFileDialog, restored)])
                assert not project.dirty and project.path == str(restored)
                assert widget.close()
                flush()
                assert not project.entries and not widget._history_entries
                for entry, images in resources:
                    entry.close.assert_called_once()
                    for image in images:
                        with pytest.raises(ValueError, match="closed"):
                            image.getpixel((0, 0))
                assert not list(tmp_path.rglob("*.lock"))
                assert not list(tmp_path.rglob(".alpha_fixer_save_*"))
        assert modal_count == 30
        assert len(workers) == 30
        assert max(live_worker_counts) <= 2
        assert len(resources) == (48 if kind == "gif" else 24)
        assert max(live_resource_counts) == (8 if kind == "gif" else 4)
        assert sum(len(images) for _, images in resources) == 48
    finally:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, old_native)
