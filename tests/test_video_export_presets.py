"""Export presets use isolated settings and never store timeline or source data."""
import json
from pathlib import Path
import shutil

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QSettings, QPoint
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.settings_manager import SettingsManager
from src.core.video_presets import VideoExportPresets
from src.ui.video_tool import VideoToolDialog, _cached_vignette_mask


PREFERENCES = {
    "format": "gif", "fps": 30, "keep_audio": False,
    "mute_audio": True, "volume": 175,
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings():
    directory = Path("build/video-export-preset-tests")
    directory.mkdir(parents=True, exist_ok=True)
    manager = SettingsManager.__new__(SettingsManager)
    manager._qs = QSettings(str(directory / "settings.ini"), QSettings.Format.IniFormat)
    manager._qs.clear()
    yield manager, directory
    manager._qs.clear()
    manager.sync()
    shutil.rmtree(directory)


@pytest.fixture
def video(app, settings):
    manager, directory = settings
    parent = QWidget()
    parent._settings = manager
    dialog = VideoToolDialog(parent)
    dialog._project.timer.stop()
    image = Image.new("RGBA", (8, 8), "red")
    image.save(directory / "still.png")
    image.close()
    dialog._load_image_paths([str(directory / "still.png")])
    dialog._clip_list.setCurrentRow(0)
    yield dialog
    dialog._project._closing_approved = True
    dialog._preview_timer.stop()
    dialog.close()
    sip.delete(dialog)
    sip.delete(parent)
    app.processEvents()


def test_named_save_apply_delete_and_relaunch(video, settings):
    manager, directory = settings
    manager.set("custom_presets", '[{"name":"unrelated alpha"}]')
    video._fps_slider.setValue(PREFERENCES["fps"])
    video._audio_enable_check.setChecked(False)
    video._audio_mute_check.setChecked(True)
    video._audio_volume_slider.setValue(175)
    video._export_preset_name.setText("GIF review")
    video._btn_save_export_preset.click()
    raw = manager.get(VideoExportPresets.KEY)
    assert json.loads(raw)["presets"] == {"GIF review": PREFERENCES}
    assert set(json.loads(raw)["presets"]["GIF review"]) == VideoExportPresets.FIELDS
    reopened = SettingsManager.__new__(SettingsManager)
    reopened._qs = QSettings(str(directory / "settings.ini"), QSettings.Format.IniFormat)
    assert VideoExportPresets(reopened).available() == {"GIF review": PREFERENCES}
    relaunched = VideoToolDialog(video.parentWidget())
    relaunched._project.timer.stop()
    try:
        assert relaunched._export_preset_combo.findData("GIF review") > 0
        assert relaunched._fps_slider.value() == 25  # selection never auto-applies
    finally:
        relaunched._project._closing_approved = True
        relaunched.close()
        sip.delete(relaunched)
    clip = video._clips[0]
    video._brightness_slider.setValue(15)
    video._still_duration_spin.setValue(47)
    video._fps_slider.setValue(12)
    video._audio_volume_slider.setValue(20)
    before = video._capture_video_state()
    render = video._snapshot_clip_render_state(clip, 25)
    video._btn_apply_export_preset.click()
    assert video._export_preferences() == PREFERENCES
    after = video._capture_video_state()
    assert (before.clips, before.timing, before.selected, before.current, before.playhead) == (
        after.clips, after.timing, after.selected, after.current, after.playhead)
    assert video._snapshot_clip_render_state(clip, 25) == render
    assert video._fps_val_lbl.text() == "30 fps"
    assert video._audio_volume_lbl.text() == "175%"
    video._btn_delete_export_preset.click()
    assert VideoExportPresets(reopened).available() == {}
    assert manager.get("custom_presets") == '[{"name":"unrelated alpha"}]'


@pytest.mark.parametrize("raw", ["{", "[]", '{"version":2,"presets":{}}', "null"])
def test_malformed_store_is_not_rewritten(video, settings, raw):
    manager, _ = settings
    manager.set(VideoExportPresets.KEY, raw)
    video._reload_export_presets()
    before = video._export_preferences()
    video._export_preset_name.setText("New")
    video._save_export_preset()
    video._apply_export_preset()
    video._delete_export_preset()
    assert manager.get(VideoExportPresets.KEY) == raw
    assert video._export_preferences() == before


@pytest.mark.parametrize("field,value", [
    ("fps", 0), ("fps", 61), ("fps", True), ("volume", 201),
    ("volume", -1), ("volume", "100"), ("keep_audio", 1),
    ("mute_audio", "false"), ("format", "webm"), ("source_path", "private.mp4"),
])
def test_invalid_entries_rejected_atomically_and_preserved(video, settings, field, value):
    manager, _ = settings
    invalid = dict(PREFERENCES, **{field: value})
    manager.set(VideoExportPresets.KEY, json.dumps({
        "version": 1, "presets": {"Broken": invalid, "Good": PREFERENCES},
    }))
    video._reload_export_presets()
    assert video._export_preset_combo.findData("Broken") == -1
    video._export_preset_combo.addItem("Broken", "Broken")
    video._export_preset_combo.setCurrentIndex(video._export_preset_combo.findData("Broken"))
    before = video._export_preferences()
    video._apply_export_preset()
    assert video._export_preferences() == before
    video._export_presets.save("Other", PREFERENCES)
    video._export_presets.delete("Good")
    assert json.loads(manager.get(VideoExportPresets.KEY))["presets"] == {
        "Broken": invalid, "Other": PREFERENCES,
    }


def test_duplicate_names_never_replace_or_delete_other_presets(video, settings):
    manager, _ = settings
    video._export_presets.save("One", PREFERENCES)
    video._export_presets.save("Two", PREFERENCES)
    before = manager.get(VideoExportPresets.KEY)
    with pytest.raises(ValueError):
        video._export_presets.save("One", dict(PREFERENCES, fps=60))
    assert manager.get(VideoExportPresets.KEY) == before
    video._export_presets.delete("One")
    assert video._export_presets.available() == {"Two": PREFERENCES}


def test_unavailable_mp4_leaves_all_controls_unchanged(video):
    video._export_presets.save("MP4", dict(PREFERENCES, format="mp4"))
    video._reload_export_presets("MP4")
    index = video._export_fmt_combo.findData("mp4")
    if index >= 0:
        video._export_fmt_combo.removeItem(index)
    before = video._export_preferences()
    video._apply_export_preset()
    assert video._export_preferences() == before
    assert "unavailable MP4" in video._export_preset_status.text()


@pytest.mark.parametrize("fps,volume", [(1, 0), (60, 200)])
def test_supported_range_boundaries(video, fps, volume):
    expected = dict(PREFERENCES, fps=fps, volume=volume)
    video._export_presets.save("Boundary", expected)
    video._reload_export_presets("Boundary")
    video._apply_export_preset()
    assert video._export_preferences() == expected


def test_programmatic_apply_blocks_intermediate_control_signals(video):
    video._export_presets.save("Quiet", PREFERENCES)
    video._reload_export_presets("Quiet")
    spies = [
        QSignalSpy(video._fps_slider.valueChanged),
        QSignalSpy(video._audio_enable_check.toggled),
        QSignalSpy(video._audio_mute_check.toggled),
        QSignalSpy(video._audio_volume_slider.valueChanged),
    ]
    video._apply_export_preset()
    assert video._export_preferences() == PREFERENCES
    assert all(len(spy) == 0 for spy in spies)


def test_apply_is_one_undoable_edit_and_manual_undo_preserves_preset(video):
    video._export_presets.save("History", PREFERENCES)
    video._reload_export_presets("History")
    video._brightness_slider.setValue(140)
    video._still_duration_spin.setValue(47)
    video._filter_combo.setCurrentIndex(video._filter_combo.findData("vignette"))
    video._fps_slider.setValue(12)
    before = video._capture_video_state()
    preferences = video._export_preferences()
    count = len(video._undo_stack)
    mask = _cached_vignette_mask((8, 8))
    mask_pixels = mask.tobytes()
    clip = video._clips[0]
    frame = clip.get_frame(0)
    pixels = frame.tobytes()
    frame.close()
    video._project._saved = video._project.fingerprint()
    changed = QSignalSpy(video._project.changed)

    video._apply_export_preset()

    assert len(video._undo_stack) == count + 1
    assert video._undo_stack[-1].label == "Apply export preset"
    assert video._history_controls == tuple(video._project.controls().items())
    assert len(changed) == 1
    assert video._project.dirty
    assert video._project._observed == video._project.fingerprint()
    after = video._capture_video_state()
    video._undo()
    assert video._export_preferences() == preferences
    assert video._capture_video_state() == before
    assert not video._project.dirty
    video._redo()
    assert video._capture_video_state() == after
    assert video._export_preferences() == PREFERENCES
    assert video._fps_val_lbl.text() == "30 fps"
    assert video._audio_volume_lbl.text() == "175%"
    video._brightness_slider.setValue(150)
    video._undo()
    assert video._brightness_slider.value() == 140
    assert video._export_preferences() == PREFERENCES
    assert video._capture_video_state() == after
    assert (before.clips, before.timing, before.selected, before.current, before.playhead) == (
        after.clips, after.timing, after.selected, after.current, after.playhead)
    assert _cached_vignette_mask((8, 8)) is mask
    assert mask.tobytes() == mask_pixels
    frame = clip.get_frame(0)
    assert frame.tobytes() == pixels
    frame.close()


def test_apply_clears_stale_redo(video):
    video._export_presets.save("Branch", PREFERENCES)
    video._reload_export_presets("Branch")
    video._brightness_slider.setValue(140)
    video._undo()
    assert video._redo_stack
    count = len(video._undo_stack)
    video._apply_export_preset()
    assert len(video._undo_stack) == count + 1
    assert not video._redo_stack
    assert not video._btn_redo.isEnabled()
    after = video._capture_video_state()
    video._redo()
    assert video._capture_video_state() == after


@pytest.mark.parametrize("case", ["invalid", "unavailable", "noop", "busy"])
def test_rejected_or_noop_apply_preserves_history(video, settings, monkeypatch, case):
    manager, _ = settings
    expected = video._export_preferences() if case == "noop" else PREFERENCES
    if case == "unavailable":
        expected = dict(expected, format="mp4")
    video._export_presets.save("Preserved", expected)
    video._reload_export_presets("Preserved")
    if case == "invalid":
        manager.set(VideoExportPresets.KEY, "{")
    elif case == "unavailable":
        index = video._export_fmt_combo.findData("mp4")
        if index >= 0:
            video._export_fmt_combo.removeItem(index)
    video._brightness_slider.setValue(140)
    video._undo()
    if case == "busy":
        monkeypatch.setattr(video, "_editing_busy", lambda: True)
    undo, redo = list(video._undo_stack), list(video._redo_stack)
    state, controls = video._capture_video_state(), video._history_controls
    video._apply_export_preset()
    assert video._undo_stack == undo
    assert video._redo_stack == redo
    assert video._history_controls == controls
    assert video._capture_video_state() == state


def test_preset_controls_accessible_at_large_font_and_small_window(app, video):
    video._export_presets.save("P" * 80, PREFERENCES)
    video._reload_export_presets("P" * 80)
    video.setStyleSheet("QWidget { font-size: 32px; }")
    video.resize(480, 400)
    video.show()
    for control in (
        video._export_preset_combo, video._btn_apply_export_preset,
        video._btn_delete_export_preset, video._export_preset_name,
        video._btn_save_export_preset,
    ):
        control.setFocus()
        for _ in range(30):
            app.processEvents()
        assert control.accessibleName()
        assert app.focusWidget() is control
        viewport = video._content_scroll.viewport()
        assert viewport.rect().contains(control.mapTo(viewport, QPoint()))
        assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight()))
        assert video._content_scroll.horizontalScrollBar().maximum() == 0
