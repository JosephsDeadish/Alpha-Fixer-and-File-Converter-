from contextlib import ExitStack
import os
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from src.ui.gif_builder import GifBuilderDialog, _FrameEntry
from tests.gif_export_helpers import wait_for_gif_export, wait_until


@pytest.fixture
def builder(tmp_path):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    settings = Mock()
    settings.get_shortcut_binding.side_effect = lambda _, default: default
    parent._settings = settings
    widget = GifBuilderDialog(parent=parent)
    widget._frames = [
        _FrameEntry("red.png", 0, Image.new("RGBA", (18, 12), "red"), delay_ms=80),
        _FrameEntry("blue.png", 0, Image.new("RGBA", (18, 12), "blue"), delay_ms=140),
    ]
    widget._update_count()
    widget.show()
    output = tmp_path / "output.gif"
    with ExitStack() as stack:
        stack.enter_context(patch("src.ui.gif_builder.QFileDialog.getSaveFileName",
                                  return_value=(str(output), "")))
        info = stack.enter_context(patch("src.ui.gif_builder.QMessageBox.information"))
        error = stack.enter_context(patch("src.ui.gif_builder.QMessageBox.critical"))
        yield app, widget, output, settings, info, error
        if widget._export_worker is not None:
            widget._cancel_export()
            wait_for_gif_export(widget)
        widget.close()
        sip.delete(widget)
        sip.delete(parent)


def slow_save(stack):
    """Pause real encoding behind a barrier, with no Qt calls on the worker."""
    entered, release = Event(), Event()
    saved = Image.Image.save
    threads, images = [], []

    def save(image, filename, *args, **kwargs):
        images.extend([image, *kwargs.get("append_images", [])])
        threads.append(QThread.currentThread())
        entered.set()
        assert release.wait(10), "test failed to release encoder"
        saved(image, filename, *args, **kwargs)

    stack.enter_context(patch.object(Image.Image, "save", save))
    return entered, release, threads, images


def assert_closed(images):
    for image in images:
        with pytest.raises(ValueError, match="closed image"):
            image.getpixel((0, 0))


def test_slow_encoding_keeps_event_loop_alive_and_blocks_duplicate_edit_import(builder):
    app, widget, output, settings, info, error = builder
    # Preserve explicitly disabled controls and shortcuts, not just defaults.
    widget._optimize_check.setEnabled(False)
    shortcut = widget._shortcut_objects["gif_toggle_play"]
    shortcut.setEnabled(False)
    emitted = Mock()
    widget.exported.connect(emitted)
    beats = []
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(1))
    timer.start(5)
    with ExitStack() as stack:
        entered, release, threads, images = slow_save(stack)
        try:
            widget._export()
            worker = widget._export_worker
            wait_until(entered.is_set)
            baseline = len(beats)
            wait_until(lambda: len(beats) >= baseline + 5)
            assert not widget._export_content.isEnabled()
            assert not widget._btn_export.isEnabled()
            assert widget._export_progress.isVisible()
            assert threads == [worker]
            assert threads[0] is not app.thread()
            widget._export()
            assert widget._export_worker is worker
            widget._clear_all()
            widget._remove_selected()
            widget.add_media_paths(["missing.png"])
            assert len(widget._frames) == 2
            assert not output.exists()
        finally:
            release.set()
            wait_for_gif_export(widget)
            timer.stop()
    assert widget._export_content.isEnabled()
    assert not widget._optimize_check.isEnabled()
    assert not shortcut.isEnabled()
    assert widget._shortcut_objects["gif_export"].isEnabled()
    emitted.assert_called_once_with(str(output))
    settings.add_gif_builder_history.assert_called_once()
    info.assert_called_once()
    error.assert_not_called()
    assert_closed(images)
    with Image.open(output) as gif:
        assert gif.size == (18, 12)
        assert gif.n_frames == 2
        assert gif.info["loop"] == 0
        durations = []
        for index in range(gif.n_frames):
            gif.seek(index)
            durations.append(gif.info["duration"])
        assert durations == [80, 140]
    assert settings.add_gif_builder_history.call_args.args[0]["frame_count"] == 2
    assert not list(output.parent.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("cancel", ["button", "programmatic", "public", "close", "reject", "accept"])
def test_cancel_during_save_discards_output_and_defers_close(builder, cancel):
    _, widget, output, settings, info, error = builder
    output.write_bytes(b"old destination")
    emitted = Mock()
    widget.exported.connect(emitted)
    with ExitStack() as stack:
        entered, release, _, images = slow_save(stack)
        try:
            widget._export()
            worker = widget._export_worker
            completion_state = []
            widget.export_finished.connect(lambda: completion_state.append(
                (widget.is_exporting(), worker.isRunning(), worker.frames, widget._export_progress)
            ))
            wait_until(entered.is_set)
            if cancel == "button":
                widget._export_progress.canceled.emit()
            elif cancel == "programmatic":
                widget._export_progress.cancel()
            elif cancel == "public":
                widget.request_export_cancel()
            elif cancel == "close":
                assert not widget.close()
            elif cancel == "reject":
                widget.reject()
            else:
                widget.accept()
            wait_until(lambda: widget._export_canceling)
            assert worker.isRunning()
            assert widget._export_worker is worker
            assert widget.isVisible()
            assert "Cancelling" in widget._export_progress.labelText()
            wait_until(lambda: widget._export_progress.isVisible())
            assert output.read_bytes() == b"old destination"
            assert len(widget._frames) == 2
        finally:
            release.set()
            wait_for_gif_export(widget)
    assert output.read_bytes() == b"old destination"
    assert not list(output.parent.glob(".alpha_fixer_save_*"))
    emitted.assert_not_called()
    settings.add_gif_builder_history.assert_not_called()
    info.assert_not_called()
    error.assert_not_called()
    assert_closed(images)
    assert completion_state == [(False, False, (), None)]
    if cancel in ("public", "close", "reject", "accept"):
        assert not widget.isVisible()
        assert widget._frames == []
    else:
        assert widget._export_content.isEnabled()
        assert widget.isVisible()
        assert len(widget._frames) == 2


@pytest.mark.parametrize("stage", ["snapshot", "prepare", "save", "publish"])
def test_export_errors_clean_resources_preserve_destination_and_restore_ui(builder, stage):
    _, widget, output, settings, info, error = builder
    output.write_bytes(b"old destination")
    copied = []
    original_copy = Image.Image.copy

    def copy(image):
        result = original_copy(image)
        # Track export-owned copies, not Pillow encoder's internal temporaries.
        if any(image is entry._pil for entry in widget._frames) or any(image is item for item in copied):
            copied.append(result)
        return result

    def fail_save(image, path, *args, **kwargs):
        Path(path).write_bytes(b"partial GIF")
        raise OSError("encoder failure")

    with ExitStack() as stack:
        stack.enter_context(patch.object(Image.Image, "copy", copy))
        if stage == "snapshot":
            stack.enter_context(patch.object(widget._frames[1]._pil, "copy",
                                             side_effect=OSError("snapshot failure")))
        elif stage == "prepare":
            stack.enter_context(patch.object(Image.Image, "quantize",
                                             side_effect=OSError("quantize failure")))
        elif stage == "save":
            stack.enter_context(patch.object(Image.Image, "save", fail_save))
        else:
            stack.enter_context(patch("os.replace", side_effect=OSError("publish failure")))
        widget._export()
        wait_for_gif_export(widget)
    assert output.read_bytes() == b"old destination"
    assert not list(output.parent.glob(".alpha_fixer_save_*"))
    settings.add_gif_builder_history.assert_not_called()
    info.assert_not_called()
    error.assert_called_once()
    assert widget._export_content.isEnabled()
    assert widget._export_progress is None
    assert not widget._export_poll.isActive()
    assert_closed(copied)
    # Source images remain usable; failure must not close the timeline.
    assert widget._frames[0]._pil.getpixel((0, 0)) == (255, 0, 0, 255)


def test_cancel_before_snapshot_start_does_not_encode_or_publish(builder):
    _, widget, output, settings, info, error = builder
    completed = Mock()
    widget.export_finished.connect(completed)
    assert not widget.is_exporting()
    with patch.object(Image.Image, "save") as save:
        widget._export()
        assert widget.is_exporting()
        widget.request_export_cancel()
        wait_for_gif_export(widget)
    assert not widget.is_exporting()
    completed.assert_called_once_with()
    save.assert_not_called()
    assert not output.exists()
    settings.add_gif_builder_history.assert_not_called()
    info.assert_not_called()
    error.assert_not_called()
    assert widget._export_content.isEnabled()
    assert not widget.isVisible()


def test_success_uses_settings_and_owned_image_snapshot(builder):
    _, widget, output, settings, _, error = builder
    widget._width_slider.setValue(9)
    widget._height_slider.setValue(6)
    widget._loop_slider.setValue(3)
    with ExitStack() as stack:
        entered, release, _, _ = slow_save(stack)
        try:
            widget._export()
            wait_until(entered.is_set)
            widget._width_slider.setValue(18)
            widget._loop_slider.setValue(7)
            widget._frames[0]._pil.paste("green", (0, 0, 18, 12))
        finally:
            release.set()
            wait_for_gif_export(widget)
    error.assert_not_called()
    with Image.open(output) as gif:
        assert gif.size == (9, 6)
        assert gif.info["loop"] == 3
        assert gif.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    history = settings.add_gif_builder_history.call_args.args[0]
    assert history["loop"] == "3"
    assert history["resize"] == "≤9×6"


def test_cancel_after_pillow_save_before_publish_never_records_success(builder):
    _, widget, output, settings, info, error = builder
    output.write_bytes(b"old destination")
    original_save = Image.Image.save
    prepared, snapshots = [], []
    exported = Mock()
    widget.exported.connect(exported)

    def save(image, path, *args, **kwargs):
        worker = widget._export_worker
        snapshots.extend(frame for frame, _ in worker.frames)
        prepared.extend([image, *kwargs.get("append_images", [])])
        original_save(image, path, *args, **kwargs)
        assert Path(path).read_bytes().startswith(b"GIF")
        assert output.read_bytes() == b"old destination"
        assert worker.cancel()

    with patch.object(Image.Image, "save", save):
        widget._export_content.setEnabled(False)
        widget._export()
        worker = widget._export_worker
        wait_for_gif_export(widget)
    assert output.read_bytes() == b"old destination"
    assert not list(output.parent.glob(".alpha_fixer_save_*"))
    assert not widget._export_content.isEnabled()
    assert widget._export_worker is None
    assert worker.frames == ()
    assert_closed(prepared + snapshots)
    wait_until(lambda: sip.isdeleted(worker))
    exported.assert_not_called()
    settings.add_gif_builder_history.assert_not_called()
    info.assert_not_called()
    error.assert_not_called()


def test_late_cancel_during_atomic_publish_does_not_block_gui(builder):
    _, widget, output, settings, info, error = builder
    entered, release = Event(), Event()
    original_replace = os.replace

    def replace(source, destination):
        entered.set()
        assert release.wait(10), "test failed to release publication"
        original_replace(source, destination)

    with patch("os.replace", replace):
        try:
            widget._export()
            wait_until(entered.is_set)
            worker = widget._export_worker
            # Publication has committed to replacing the destination: cancellation
            # must return immediately rather than wait on its filesystem lock.
            assert not worker.cancel()
            widget.request_export_cancel()
            assert not widget._export_canceling
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(1))
            wait_until(lambda: heartbeat)
            assert worker.isRunning()
        finally:
            release.set()
            wait_for_gif_export(widget)
    with Image.open(output) as image:
        assert image.n_frames == 2
    settings.add_gif_builder_history.assert_called_once()
    info.assert_not_called()
    error.assert_not_called()
    assert not widget.isVisible()


def test_snapshot_error_during_shutdown_emits_safe_completion_without_modal(builder):
    _, widget, output, settings, info, error = builder
    completed = []
    widget.export_finished.connect(lambda: completed.append(
        (widget.is_exporting(), widget._export_progress, widget._export_snapshot)
    ))

    def fail_snapshot():
        widget.request_export_cancel()
        raise OSError("snapshot failed during shutdown")

    with patch.object(widget._frames[1]._pil, "copy", fail_snapshot):
        widget._export()
        worker = widget._export_worker
        wait_for_gif_export(widget)
    assert completed == [(False, None, [])]
    assert worker.frames == ()
    assert not widget.isVisible()
    assert widget._frames == []
    assert not output.exists()
    settings.add_gif_builder_history.assert_not_called()
    info.assert_not_called()
    error.assert_not_called()


def test_real_main_window_close_waits_for_parented_gif_worker_cleanup(tmp_path):
    from src.core.settings_manager import SettingsManager
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        window = MainWindow(settings)
    window.show()
    window._open_or_focus_gif_builder()
    widget = window._gif_builder_dlg
    assert widget.parentWidget() is window
    widget._frames = [
        _FrameEntry("red.png", 0, Image.new("RGBA", (18, 12), "red"), delay_ms=80),
        _FrameEntry("blue.png", 0, Image.new("RGBA", (18, 12), "blue"), delay_ms=140),
    ]
    widget._update_count()
    output = tmp_path / "parented.gif"
    output.write_bytes(b"old destination")
    exported = Mock()
    widget.exported.connect(exported)
    release = None
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("src.ui.gif_builder.QFileDialog.getSaveFileName",
                                      return_value=(str(output), "")))
            info = stack.enter_context(patch("src.ui.gif_builder.QMessageBox.information"))
            error = stack.enter_context(patch("src.ui.gif_builder.QMessageBox.critical"))
            sync = stack.enter_context(patch.object(settings, "sync", wraps=settings.sync))
            entered, release, _, images = slow_save(stack)
            try:
                widget._export()
                worker = widget._export_worker
                completion = []
                widget.export_finished.connect(lambda: completion.append(
                    (widget.is_exporting(), worker.isRunning(), worker.frames,
                     sip.isdeleted(window), getattr(window, "_shutdown_complete", False))
                ))
                wait_until(entered.is_set)
                assert widget.is_exporting()
                assert not window.close()
                assert not window.close()
                assert window._builder_shutdown_pending == [widget]
                assert window.isVisible()
                assert widget._close_after_export
                assert widget._export_canceling
                assert worker.isRunning()
                assert not sip.isdeleted(window)
                assert output.read_bytes() == b"old destination"
                sync.assert_not_called()
                heartbeat = []
                QTimer.singleShot(0, lambda: heartbeat.append(1))
                wait_until(lambda: heartbeat)
            finally:
                release.set()
                wait_for_gif_export(widget)
            wait_until(lambda: getattr(window, "_shutdown_complete", False))
            assert completion == [(False, False, (), False, False)]
            assert not window._builder_shutdown_pending
            assert not window.isVisible()
            assert not widget.isVisible()
            assert widget._frames == []
            assert output.read_bytes() == b"old destination"
            assert not list(tmp_path.glob(".alpha_fixer_save_*"))
            exported.assert_not_called()
            assert settings.get_gif_builder_history() == []
            info.assert_not_called()
            error.assert_not_called()
            sync.assert_called_once()
            assert_closed(images)
            wait_until(lambda: sip.isdeleted(worker))
    finally:
        if release is not None:
            release.set()
        if widget.is_exporting():
            widget.request_export_cancel()
            wait_for_gif_export(widget)
        window.close()
        sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)
        app.processEvents()


@pytest.mark.parametrize("outcome", ["success", "error"])
def test_shutdown_after_native_finish_before_queued_cleanup_never_opens_modal(builder, outcome):
    from PyQt6.QtCore import Qt

    _, widget, output, settings, info, error = builder
    output.write_bytes(b"old destination")
    entered, release, native_finished = Event(), Event(), Event()
    original_quantize = Image.Image.quantize
    exported, completed = Mock(), Mock()
    widget.exported.connect(exported)
    widget.export_finished.connect(completed)

    def quantize(image, *args, **kwargs):
        entered.set()
        assert release.wait(10), "test failed to release preparation"
        if outcome == "error":
            raise OSError("queued worker failure")
        return original_quantize(image, *args, **kwargs)

    with patch.object(Image.Image, "quantize", quantize):
        try:
            widget._export()
            worker = widget._export_worker
            # Only a threading.Event is touched directly by the native signal.
            worker.finished.connect(native_finished.set, Qt.ConnectionType.DirectConnection)
            wait_until(entered.is_set)
            release.set()
            # Deliberately do not pump Qt here: cleanup remains queued while
            # shutdown requests cancellation after the worker's final outcome.
            assert native_finished.wait(10)
            assert worker.outcome == outcome
            assert worker.frames == ()
            assert widget.is_exporting()
            widget.request_export_cancel()
            assert widget._close_after_export
            wait_for_gif_export(widget)
        finally:
            release.set()
            if widget.is_exporting():
                widget.request_export_cancel()
                wait_for_gif_export(widget)
    assert not widget.is_exporting()
    assert not widget.isVisible()
    completed.assert_called_once_with()
    info.assert_not_called()
    error.assert_not_called()
    assert not list(output.parent.glob(".alpha_fixer_save_*"))
    if outcome == "success":
        exported.assert_called_once_with(str(output))
        settings.add_gif_builder_history.assert_called_once()
        with Image.open(output) as image:
            assert image.n_frames == 2
    else:
        exported.assert_not_called()
        settings.add_gif_builder_history.assert_not_called()
        assert output.read_bytes() == b"old destination"
