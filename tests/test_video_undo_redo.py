from pathlib import Path
import tempfile
from types import MethodType
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget

from src.ui.video_tool import VideoToolDialog, _VideoFrameGetter, _ClipEntry
from src.core.editor_projects import read_project, prepare_entries, write_project


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folder():
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        yield Path(directory)


@pytest.fixture
def builder(app, folder):
    with patch("src.ui.editor_project.user_data_directory", return_value=folder / "data"):
        widget = VideoToolDialog()
    widget._project.timer.stop()
    yield widget
    widget._project._closing_approved = True
    widget._project._release_session()
    widget.close()
    sip.delete(widget)


@pytest.fixture
def media(folder):
    images = [Image.new("RGBA", (12, 8), color) for color in
              ("red", "green", "blue", "yellow", "purple", "cyan")]
    gif, still = folder / "frames.gif", folder / "still.png"
    images[0].save(gif, save_all=True, append_images=images[1:], duration=100, loop=0)
    images[0].save(still)
    for image in images:
        image.close()
    return gif, still


def samples(widget):
    result = []
    for clip in widget._clips:
        for index in range(clip.active_frames):
            image = clip.get_frame(index)
            result.append(image.getpixel((0, 0)))
            image.close()
    return result


def test_import_remove_clear_identity_selection_playhead(builder, media):
    builder._load_image_paths(list(map(str, media)))
    entries = tuple(builder._clips)
    original = samples(builder)
    builder._undo()
    assert not builder._clips
    builder._redo()
    assert tuple(builder._clips) == entries
    assert samples(builder) == original
    builder._clip_list.setCurrentRow(0)
    builder._scrubber.setValue(3)
    builder._remove_selected()
    builder._undo()
    assert builder._clip_list.currentRow() == 0
    assert builder._scrubber.value() == 3
    assert tuple(builder._clips) == entries
    builder._clear_all()
    builder._undo()
    assert samples(builder) == original
    builder._redo()
    assert not builder._clips


def test_reorder_failed_import_noop_redo_branch(builder, media):
    builder._load_image_paths(list(map(str, media)))
    entries = tuple(builder._clips)
    item = builder._clip_list.takeItem(1)
    builder._clip_list.insertItem(0, item)
    builder._sync_clips_from_list()
    assert builder._clips == list(reversed(entries))
    builder._undo()
    builder._sync_clips_from_list()
    builder._load_image_paths(["missing.png"])
    with patch("src.ui.video_tool._load_video_clip", return_value=None), \
            patch("src.ui.video_tool._video_load_failure_hint", return_value="failed"):
        builder._load_video_paths(["missing.mp4"])
    assert len(builder._redo_stack) == 1
    builder._clip_list.setCurrentRow(0)
    builder._on_trim_start_changed(0)
    assert len(builder._redo_stack) == 1
    builder._trim_start_slider.setValue(1)
    assert not builder._redo_stack


def test_split_phase_speed_trim_roundtrip(builder, media):
    builder._load_image_paths([str(media[0])])
    builder._clip_speed_slider.setValue(70)
    original = samples(builder)
    original_clip = builder._clips[0]
    builder._scrubber.setValue(2)
    builder._split_clip_at_playhead()
    split_clips = tuple(builder._clips)
    split_timing = tuple(c._split_timing for c in split_clips)
    audio = tuple(c._split_audio_range() for c in split_clips)
    assert samples(builder) == original
    builder._undo()
    assert builder._clips == [original_clip]
    assert original_clip._split_timing is None
    assert samples(builder) == original
    builder._redo()
    assert tuple(builder._clips) == split_clips
    assert tuple(c._split_timing for c in split_clips) == split_timing
    assert tuple(c._split_audio_range() for c in split_clips) == audio
    builder._on_trim_end_changed(original_clip.trim_end)
    assert original_clip._split_timing == split_timing[0]
    builder._clip_speed_slider.setValue(120)
    builder._undo()
    assert original_clip._split_timing == split_timing[0]
    assert samples(builder) == original
    builder._trim_end_slider.setValue(0)
    builder._undo()
    assert original_clip._split_timing == split_timing[0]


@pytest.mark.parametrize("edge,playhead,row", [("start", 0, 0), ("end", 8, 1)])
def test_clamped_trim_noop_syncs_slider_preserves_redo_and_split_phase(
        builder, media, edge, playhead, row):
    builder._load_image_paths([str(media[0])])
    builder._clip_speed_slider.setValue(50)
    builder._scrubber.setValue(playhead)
    with patch("src.ui.video_tool.QMessageBox.information") as information:
        builder._split_clip_at_playhead()
        information.assert_not_called()
    builder._clip_list.setCurrentRow(row)
    clip = builder._clips[row]
    assert clip.trim_start == clip.trim_end
    assert clip._split_timing is not None
    builder._brightness_slider.setValue(120)
    builder._undo()
    undo, redo = list(builder._undo_stack), list(builder._redo_stack)
    state, expected = builder._capture_video_state(), samples(builder)
    slider = getattr(builder, f"_trim_{edge}_slider")
    value = getattr(clip, f"trim_{edge}")
    slider.setValue(value + (1 if edge == "start" else -1))
    assert slider.value() == value
    assert getattr(builder, f"_trim_{edge}_lbl").text() == str(value)
    assert builder._capture_video_state() == state
    assert builder._undo_stack == undo and builder._redo_stack == redo
    assert samples(builder) == expected
    builder._redo()
    assert builder._brightness_slider.value() == 120
    assert clip._split_timing == state.timing[row][-1]


@pytest.mark.parametrize("control", ["_clip_speed_slider", "_brightness_slider",
                                     "_audio_volume_slider", "_trim_end_slider"])
def test_slider_gesture_one_edit(builder, media, control):
    builder._load_image_paths([str(media[0])])
    slider = getattr(builder, control)
    initial = slider.value()
    count = len(builder._undo_stack)
    slider.sliderPressed.emit()
    for value in (initial - 1, initial - 2, initial - 3):
        slider.setValue(max(slider.minimum(), value))
    slider.sliderReleased.emit()
    assert len(builder._undo_stack) == count + 1
    builder._undo()
    assert slider.value() == initial
    slider.sliderPressed.emit()
    slider.sliderReleased.emit()
    assert len(builder._redo_stack) == 1


def test_still_settings_reset_and_dirty(builder, media):
    builder._load_image_paths([str(media[1])])
    saved = builder._project.fingerprint()
    builder._project._saved = saved
    builder._still_duration_spin.setValue(80)
    assert builder._project.dirty
    builder._undo()
    assert not builder._project.dirty
    builder._redo()
    assert builder._clips[0].still_duration_frames == 80
    builder._brightness_slider.setValue(140)
    builder._contrast_slider.setValue(180)
    count = len(builder._undo_stack)
    builder._reset_adjustments()
    assert len(builder._undo_stack) == count + 1
    builder._undo()
    assert builder._brightness_slider.value() == 140
    assert builder._contrast_slider.value() == 180
    builder._filter_combo.setCurrentIndex(builder._filter_combo.findData("sepia"))
    builder._undo()
    assert builder._filter_combo.currentData() == "none"


def test_caps_branch_cleanup_and_close(builder, media):
    builder._load_image_paths([str(media[0])])
    original = builder._clips[0]
    builder._undo()
    builder._load_image_paths([str(media[1])])
    assert original._get_frame._frames == []
    live = builder._clips[0]
    with patch("src.ui.video_tool._HISTORY_LIMIT", 2):
        for value in (30, 40, 50, 60):
            builder._still_duration_spin.setValue(value)
        assert len(builder._undo_stack) == 2
    with patch("src.ui.video_tool._HISTORY_PIXEL_LIMIT", 0):
        builder._clear_all()
    assert not builder._undo_stack
    with pytest.raises(ValueError):
        live._get_frame._img.getpixel((0, 0))


def test_remux_ownership_bounded_release(builder, folder):
    import numpy as np
    path = folder / "remux.mp4"
    path.write_bytes(b"held resource")
    frame = np.full((8, 8, 3), 120, dtype=np.uint8)
    getter = _VideoFrameGetter(str(path), 1, frame, cleanup_paths=[str(path)])
    clip = _ClipEntry(str(path), 1, getter, has_audio=True)
    with patch("src.ui.video_tool._load_video_clip", return_value=clip):
        builder._load_video_paths([str(path)])
    builder._remove_selected()
    assert path.exists()
    builder._undo()
    assert builder._clips == [clip] and clip.has_audio
    builder._redo()
    assert path.exists()
    with patch("src.ui.video_tool._HISTORY_TEMP_BYTES_LIMIT", 0):
        builder._trim_video_history()
        builder._release_unused_history()
    assert not path.exists()


def test_failed_split_preserves_redo_and_timing(builder, media):
    builder._load_image_paths([str(media[0])])
    builder._clip_speed_slider.setValue(70)
    builder._undo()
    clip = builder._clips[0]
    state = builder._capture_video_state()
    with patch.object(builder, "_reload_clip", return_value=None), \
            patch("src.ui.video_tool.QMessageBox.warning"):
        builder._split_clip_at_playhead()
    assert len(builder._redo_stack) == 1
    assert builder._capture_video_state() == state
    assert clip._split_timing is None


def test_count_resource_cap_and_close_release_history(builder, media):
    builder._load_image_paths(list(map(str, media)))
    entries = tuple(builder._clips)
    builder._clear_all()
    assert entries[0]._get_frame._frames
    with patch("src.ui.video_tool._HISTORY_RESOURCE_LIMIT", 0):
        builder._trim_video_history()
        builder._release_unused_history()
    assert not builder._undo_stack
    assert entries[0]._get_frame._frames == []
    builder._load_image_paths(list(map(str, media)))
    entries = tuple(builder._clips)
    builder._remove_selected()
    builder._release_clips()
    assert not builder._undo_stack and not builder._redo_stack
    assert entries[0]._get_frame._frames == []
    with pytest.raises(ValueError):
        entries[1]._get_frame._img.getpixel((0, 0))


def test_real_export_pins_history_and_timing(builder, media, folder):
    from tests.video_export_helpers import wait_for_video_export
    builder._load_image_paths([str(media[0])])
    builder._clip_speed_slider.setValue(70)
    builder._scrubber.setValue(2)
    builder._split_clip_at_playhead()
    expected = samples(builder)
    entries = tuple(builder._clips)
    output = folder / "history.gif"
    builder._export_fmt_combo.setCurrentIndex(builder._export_fmt_combo.findData("gif"))
    with patch("src.ui.video_tool.QFileDialog.getSaveFileName", return_value=(str(output), "")), \
            patch("src.ui.video_tool.QMessageBox.information"):
        builder._export()
        assert builder.is_exporting()
        builder._undo()
        builder._remove_selected()
        builder._clear_all()
        builder._clip_speed_slider.setValue(130)
        assert tuple(builder._clips) == entries
        assert entries[0].speed_percent == 70
        wait_for_video_export(builder)
    with Image.open(output) as image:
        output_samples = []
        for index in range(image.n_frames):
            image.seek(index)
            output_samples.append(image.convert("RGBA").getpixel((0, 0)))
    # GIF merges consecutive duplicate frames but does not change sampled colors.
    unique = [color for index, color in enumerate(expected)
              if index == 0 or color != expected[index - 1]]
    assert output_samples == unique


def test_project_recovery_split_roundtrip_and_reset(builder, media, folder):
    builder._load_image_paths([str(media[0])])
    builder._clip_speed_slider.setValue(70)
    builder._scrubber.setValue(2)
    builder._split_clip_at_playhead()
    expected = samples(builder)
    timing = [c._split_timing for c in builder._clips]
    for name in ("project.json", "recovery.json"):
        path = folder / name
        document, images = builder._project._document(path)
        assert not images
        for entry, clip in zip(document["entries"], builder._clips):
            from src.core.editor_projects import source_signature
            entry["source_sha256"] = source_signature(clip.source_path)
        write_project(path, document)
        loaded = read_project(path, "video")
        entries = prepare_entries(loaded, path)
        old = tuple(builder._clips)
        builder._project._install(loaded, entries)
        assert not builder._undo_stack and not builder._redo_stack
        assert [c._split_timing for c in builder._clips] == timing
        assert samples(builder) == expected
        for clip in old:
            assert clip._get_frame._frames == []


def test_shortcuts_text_fields_busy_and_rebinding(builder, media):
    builder._load_image_paths([str(media[1])])
    builder.show()
    builder.activateWindow()
    builder._clip_list.setFocus()
    QApplication.processEvents()
    QTest.keyClick(builder._clip_list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not builder._clips
    QTest.keyClick(builder._clip_list, Qt.Key.Key_Z,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert builder._clips
    field = QLineEdit(builder)
    field.show()
    field.setFocus()
    QApplication.processEvents()
    field.setText("text")
    builder._history_shortcut(False)
    assert builder._clips
    builder.update_shortcut_binding("video_undo", "Ctrl+U")
    assert builder._shortcut_objects["video_undo"].key().toString() == "Ctrl+U"
    for attribute in ("_export_worker", "_export_selecting"):
        previous = getattr(builder, attribute)
        setattr(builder, attribute, object() if attribute == "_export_worker" else True)
        builder._undo()
        builder._clear_all()
        builder._load_image_paths([str(media[1])])
        assert len(builder._clips) == 1
        setattr(builder, attribute, previous)
    builder._project._loading = True
    builder._undo()
    assert len(builder._clips) == 1
    builder._project._loading = False


@pytest.mark.parametrize("existing,key,history_id", [
    ("video_split_clip", "Ctrl+Z", "video_undo"),
    ("video_split_clip", "Ctrl+Shift+Z", "video_redo"),
    ("help", "Ctrl+Z", "video_undo"),
    ("help", "Ctrl+Shift+Z", "video_redo"),
    ("video_undo", "Ctrl+Shift+Z", "video_redo"),
])
def test_saved_shortcut_real_activation_and_history_registry(
        app, folder, media, existing, key, history_id):
    from src.ui.main_window import MainWindow

    host = QWidget()
    custom = {existing: key}
    host._settings = MagicMock()
    host._settings.get_shortcut_binding.side_effect = lambda sid, default: custom.get(sid, default)
    global_shortcut = QShortcut(QKeySequence(custom.get("help", "F1")), host)
    global_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    activations = []
    global_shortcut.activated.connect(lambda: activations.append("help"))
    host._shortcut_map = {
        "help": {"group": "Global", "default": "F1",
                 "current": custom.get("help", "F1"), "sc": global_shortcut, "desc": "Help"},
    }
    host._update_shortcut = MethodType(MainWindow._update_shortcut, host)
    MainWindow._register_shortcut_provider(host, VideoToolDialog, "_video_tool_dlg")
    with patch("src.ui.editor_project.user_data_directory", return_value=folder / "data"):
        widget = VideoToolDialog(parent=host)
    host._video_tool_dlg = widget
    widget._project.timer.stop()
    try:
        host.show()
        widget._load_image_paths([str(media[0])])
        widget.show()
        widget.activateWindow()
        widget._clip_list.setFocus()
        app.processEvents()
        info = host._shortcut_map[history_id]
        assert info["current"] == ""
        assert info["default"] == key
        assert info["owner_attr"] == "_video_tool_dlg"
        assert widget._shortcut_objects[history_id].key().isEmpty()
        QTest.keySequence(widget._clip_list, QKeySequence(key))
        if existing == "video_split_clip":
            assert len(widget._clips) == 2
            assert host._shortcut_map[existing]["current"] == key
        elif existing == "video_undo":
            assert not widget._clips
            assert widget._shortcut_objects[existing].key() == QKeySequence(key)
        else:
            assert activations == ["help"] and len(widget._clips) == 1
        host._settings.set_shortcut_binding.assert_not_called()
        MainWindow._apply_shortcut_settings(host)
        assert info["current"] == ""
        assert widget._shortcut_objects[history_id].key().isEmpty()
        host._settings.set_shortcut_binding.assert_not_called()
        custom.clear()
        MainWindow._apply_shortcut_settings(host)
        assert info["current"] == key
        assert widget._shortcut_objects[history_id].key() == QKeySequence(key)
        custom[existing] = key
        MainWindow._apply_shortcut_settings(host)
        assert info["current"] == ""
        assert widget._shortcut_objects[history_id].key().isEmpty()
        host._settings.set_shortcut_binding.assert_not_called()
        assert host._update_shortcut(history_id, "Ctrl+U")
        if history_id == "video_redo":
            if existing != "video_undo":
                widget._undo()
            expected = 2 if existing == "video_split_clip" else 1
        else:
            expected = 1 if existing == "video_split_clip" else 0
        QTest.keySequence(widget._clip_list, QKeySequence("Ctrl+U"))
        assert len(widget._clips) == expected
    finally:
        widget._project._closing_approved = True
        widget._project._release_session()
        widget.close()
        sip.delete(widget)
        host.close()
        sip.delete(host)
        app.processEvents()


def test_mixed_drop_partial_failure_is_single_edit_and_reentrant_safe(builder, media):
    observed = []
    from src.ui.video_tool import _load_image_as_clip

    def load(path):
        observed.append(path)
        builder._undo()
        builder._clear_all()
        return _load_image_as_clip(path)

    with patch("src.ui.video_tool._load_image_as_clip", side_effect=load):
        builder._on_files_dropped([str(media[0]), "missing.png", str(media[1])], 0)
    assert len(observed) == 3
    assert len(builder._clips) == 2 and len(builder._undo_stack) == 1
    builder._undo()
    assert not builder._clips
    builder._redo()
    assert len(builder._clips) == 2


def test_stream_replacement_undo_preserves_audio_resource_metadata(builder, media):
    from src.ui.video_tool import _load_image_as_clip
    first = _load_image_as_clip(str(media[0]))
    first.clip_type = "video"
    first.has_audio = True
    first.preferred_video_stream_index = 1
    first.preferred_audio_stream_index = 2
    first.load_note = "original stream"
    second = _load_image_as_clip(str(media[0]))
    second.clip_type = "video"
    second.has_audio = False
    second.preferred_video_stream_index = 3
    second.preferred_audio_stream_index = 4
    second.load_note = "audio dropped"
    with patch("src.ui.video_tool._load_video_clip", return_value=first):
        builder._load_video_paths(["generated.mp4"])
    builder._stream_picker_combo.addItem("alternate", 3)
    builder._stream_picker_combo.setCurrentIndex(builder._stream_picker_combo.count() - 1)
    builder._audio_stream_picker_combo.addItem("alternate", 4)
    builder._audio_stream_picker_combo.setCurrentIndex(builder._audio_stream_picker_combo.count() - 1)
    with patch.object(builder, "_reload_clip", return_value=second):
        builder._apply_selected_stream_choice()
    assert builder._clips == [second] and not second.has_audio
    builder._undo()
    assert builder._clips == [first] and first.has_audio
    assert first.preferred_video_stream_index == 1 and first.preferred_audio_stream_index == 2
    builder._redo()
    assert builder._clips == [second] and second.load_note == "audio dropped"


def test_history_closes_video_readers_but_holds_recovery_path(builder, folder):
    import numpy as np
    from unittest.mock import Mock
    path = folder / "retained.mp4"
    path.write_bytes(b"held")
    getter = _VideoFrameGetter(str(path), 2, cleanup_paths=[str(path)])
    clip = _ClipEntry(str(path), 2, getter)
    with patch("src.ui.video_tool._load_video_clip", return_value=clip), \
            patch.object(builder, "_update_preview"):
        builder._load_video_paths([str(path)])
        reader = Mock()
        getter._reader = reader
        getter._last_frame = np.zeros((8, 8, 3), dtype=np.uint8)
        builder._clear_all()
        reader.close.assert_called_once()
        assert getter._reader is None and path.exists()
        builder._undo()
        assert builder._clips == [clip]
        builder._release_clips()
    assert not path.exists()
