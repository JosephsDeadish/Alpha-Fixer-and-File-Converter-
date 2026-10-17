from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from src.ui.gif_builder import GifBuilderDialog, _FrameEntry, _ENTRY_ROLE
from tests.gif_export_helpers import wait_for_gif_export


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def builder(app):
    widget = GifBuilderDialog()
    widget._project.timer.stop()
    with patch.object(widget._project, "allow_close", return_value=True):
        yield widget
        if widget.is_exporting():
            widget._cancel_export()
            wait_for_gif_export(widget)
        widget.close()
        sip.delete(widget)


@pytest.fixture
def source():
    # Keep generated media inside the repository rather than system temp paths.
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
        path = Path(folder) / "frames.gif"
        frames = [Image.new("RGBA", (12, 8), color) for color in ("red", "green", "blue")]
        try:
            frames[0].save(path, save_all=True, append_images=frames[1:],
                           duration=[80, 140, 230], loop=0)
        finally:
            for frame in frames:
                frame.close()
        yield path


def pixels(builder):
    return [entry._pil.getpixel((0, 0)) for entry in builder._frames]


def test_import_remove_clear_roundtrip_identity_selection(builder, source):
    builder.add_media_paths([str(source)])
    entries = tuple(builder._frames)
    original_pixels = pixels(builder)
    builder._undo()
    assert builder._frames == []
    builder._redo()
    assert tuple(builder._frames) == entries
    assert pixels(builder) == original_pixels
    builder._frame_list.setCurrentRow(1)
    builder._frame_list.item(2).setSelected(True)
    builder._remove_selected()
    assert builder._frames == [entries[0]]
    builder._undo()
    assert builder._frame_list.currentRow() == 1
    assert [builder._frame_list.row(item) for item in builder._frame_list.selectedItems()] == [1, 2]
    assert pixels(builder) == original_pixels
    builder._redo()
    builder._clear_all()
    builder._undo()
    assert builder._frames == [entries[0]]
    builder._undo()
    assert tuple(builder._frames) == entries
    assert [entry.delay_ms for entry in entries] == [80, 140, 230]


def test_reorder_noop_and_redo_branch(builder, source):
    builder.add_media_paths([str(source)])
    entries = tuple(builder._frames)
    builder._frame_list.setCurrentRow(1)
    item = builder._frame_list.takeItem(2)
    builder._frame_list.insertItem(0, item)
    builder._sync_frames_from_list()
    assert builder._frames == [entries[2], entries[0], entries[1]]
    builder._undo()
    assert tuple(builder._frames) == entries
    builder._sync_frames_from_list()
    builder._frame_list.clearSelection()
    builder._remove_selected()
    assert len(builder._redo_stack) == 1
    builder._redo()
    assert builder._frames[0] is entries[2]
    builder._undo()
    builder._pf_slider.setValue(500)
    assert builder._redo_stack == []
    assert builder._frame_list.item(1).data(_ENTRY_ROLE) is entries[1]


def test_delay_gesture_source_timing_and_playback(builder, source):
    builder.add_media_paths([str(source)])
    builder._frame_list.setCurrentRow(0)
    builder._btn_play.setChecked(True)
    baseline = len(builder._undo_stack)
    builder._pf_slider.sliderPressed.emit()
    builder._pf_slider.setValue(300)
    builder._pf_slider.setValue(400)
    builder._pf_slider.setValue(500)
    builder._pf_slider.sliderReleased.emit()
    assert len(builder._undo_stack) == baseline + 1
    assert builder._preview_timer.interval() == 500
    builder._undo()
    assert builder._frames[0].delay_ms == 80
    redo_count = len(builder._redo_stack)
    builder._pf_slider.sliderPressed.emit()
    builder._pf_slider.sliderReleased.emit()
    assert len(builder._redo_stack) == redo_count
    assert builder._preview_timer.interval() == 80
    assert builder._pf_slider.value() == 80
    builder._redo()
    assert builder._frames[0].delay_ms == 500
    builder._pf_check.setChecked(False)
    assert builder._frames[0].delay_ms is None
    builder._undo()
    assert builder._frames[0].delay_ms == 500
    assert builder._frames[0].source_delay_ms == 80
    builder._undo()
    assert builder._frames[0].delay_ms == 80


def test_partial_import_cancel_is_one_edit_and_reentrant_edits_are_blocked(builder, source):
    observed = []

    def canceled():
        observed.append(len(builder._frames))
        builder._undo()
        builder._clear_all()
        return bool(builder._frames)

    with patch("src.ui.gif_builder.QProgressDialog.wasCanceled", side_effect=canceled):
        builder.add_media_paths([str(source), str(source)])
    assert len(builder._frames) == 3
    assert len(builder._undo_stack) == 1
    builder._undo()
    assert not builder._frames
    builder._redo()
    assert len(builder._frames) == 3


def test_project_replacement_resets_history_and_releases_previous_entries(builder, source):
    builder.add_media_paths([str(source)])
    old = tuple(builder._frames)
    builder._clear_all()
    replacement = _FrameEntry("replacement.png", 0, Image.new("RGBA", (8, 8), "yellow"))
    document = dict(controls=builder._project.controls(), selected=0, playhead=0)
    builder._project._install(document, [replacement])
    assert builder._frames == [replacement]
    assert not builder._undo_stack and not builder._redo_stack
    assert not builder._btn_undo.isEnabled()
    for entry in old:
        with pytest.raises(ValueError):
            entry._pil.getpixel((0, 0))


def test_new_import_releases_abandoned_redo_pixels_and_globals_are_not_history(builder, source):
    builder.add_media_paths([str(source)])
    original = tuple(builder._frames)
    builder._undo()
    builder._delay_slider.setValue(300)
    builder._loop_slider.setValue(2)
    assert len(builder._redo_stack) == 1
    assert not builder._undo_stack
    builder.add_media_paths([str(source)])
    assert not builder._redo_stack
    for entry in original:
        with pytest.raises(ValueError):
            entry._pil.getpixel((0, 0))


def test_count_pixel_caps_release_unreachable_images(builder, source):
    builder.add_media_paths([str(source)])
    entries = tuple(builder._frames)
    builder._frame_list.setCurrentRow(0)
    with patch("src.ui.gif_builder._HISTORY_LIMIT", 3):
        for value in range(200, 260, 10):
            builder._pf_slider.setValue(value)
        assert len(builder._undo_stack) == 3
    with patch("src.ui.gif_builder._HISTORY_PIXEL_LIMIT", 0):
        builder._clear_all()
    assert not builder._undo_stack
    for entry in entries:
        with pytest.raises(ValueError):
            entry._pil.getpixel((0, 0))


@pytest.mark.parametrize("finish", ["close", "reject"])
def test_close_releases_live_and_history_images(builder, source, finish):
    builder.add_media_paths([str(source)])
    entries = tuple(builder._frames)
    builder._frame_list.setCurrentRow(1)
    builder._remove_selected()
    getattr(builder, finish)()
    assert not builder._frames and not builder._undo_stack and not builder._redo_stack
    assert builder._frame_list.count() == 0
    for entry in entries:
        with pytest.raises(ValueError):
            entry._pil.getpixel((0, 0))


def test_shortcuts_registry_buttons_and_text_local_undo(builder, source):
    builder.add_media_paths([str(source)])
    assert {"gif_undo", "gif_redo"} <= {item[0] for item in builder.shortcut_definitions()}
    builder.show()
    builder.activateWindow()
    builder._frame_list.setFocus()
    QTest.qWait(20)
    QTest.keyClick(builder._frame_list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not builder._frames
    assert builder._btn_redo.isEnabled()
    QTest.keyClick(builder._frame_list, Qt.Key.Key_Z,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert len(builder._frames) == 3
    text = QLineEdit(builder)
    text.show()
    text.setFocus()
    QTest.keyClicks(text, "hello")
    QTest.keyClick(text, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert text.text() == ""
    assert len(builder._frames) == 3
    builder.update_shortcut_binding("gif_undo", "Ctrl+U")
    builder._frame_list.setFocus()
    QTest.keyClick(builder._frame_list, Qt.Key.Key_U, Qt.KeyboardModifier.ControlModifier)
    assert not builder._frames


@pytest.mark.parametrize("cancel", [False, True])
def test_restored_export_and_busy_guards(builder, source, cancel):
    builder.add_media_paths([str(source)])
    entries = tuple(builder._frames)
    builder._clear_all()
    builder._undo()
    destination = source.parent / "export.gif"
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName",
               return_value=(str(destination), "")), \
            patch("src.ui.gif_builder.QMessageBox.information"), \
            patch("src.ui.gif_builder.QMessageBox.critical") as error:
        builder._export()
        assert not builder._btn_undo.isEnabled()
        builder._undo()
        builder._clear_all()
        builder._frame_list.setCurrentRow(0)
        builder._pf_slider.setValue(500)
        assert tuple(builder._frames) == entries
        assert entries[0].delay_ms == 80
        if cancel:
            builder._cancel_export()
        wait_for_gif_export(builder)
        error.assert_not_called()
    assert builder._btn_undo.isEnabled()
    assert pixels(builder) == [(255, 0, 0, 255), (0, 128, 0, 255), (0, 0, 255, 255)]
    if cancel:
        assert not destination.exists()
    else:
        with Image.open(destination) as image:
            delays, colors = [], []
            for index in range(image.n_frames):
                image.seek(index)
                delays.append(image.info["duration"])
                colors.append(image.convert("RGBA").getpixel((0, 0)))
        assert delays == [80, 140, 230]
        assert colors == pixels(builder)


def test_close_during_export_waits_then_releases_history_and_live_images(builder, source):
    builder.add_media_paths([str(source)])
    entries = tuple(builder._frames)
    builder._frame_list.setCurrentRow(1)
    builder._remove_selected()
    destination = source.parent / "cancel-on-close.gif"
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName",
               return_value=(str(destination), "")), \
            patch("src.ui.gif_builder.QMessageBox.information"), \
            patch("src.ui.gif_builder.QMessageBox.critical"):
        builder._export()
        builder.close()
        assert builder.is_exporting()
        assert entries[1]._pil.getpixel((0, 0)) == (0, 128, 0, 255)
        wait_for_gif_export(builder)
    assert not builder._frames and not builder._history_entries
    for entry in entries:
        with pytest.raises(ValueError):
            entry._pil.getpixel((0, 0))
