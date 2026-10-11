"""Precise source trim and rendered playhead entry using real Qt key events."""
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.ui.video_tool import VideoToolDialog, _ClipEntry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def video(app):
    directory = Path("build/video-precise-frame-tests")
    directory.mkdir(parents=True, exist_ok=True)
    with patch("src.ui.editor_project.user_data_directory", return_value=directory / "data"):
        widget = VideoToolDialog()
    widget._project.timer.stop()
    widget.show()
    app.processEvents()
    yield widget
    widget._preview_timer.stop()
    widget._project._closing_approved = True
    widget._project._release_session()
    widget.close()
    sip.delete(widget)
    app.processEvents()
    shutil.rmtree(directory)


def add_clip(video, count=12, offset=0, clip_type="video"):
    def frame(index):
        return Image.new("RGBA", (8, 8), (offset + index * 10, 0, 0, 255))

    clip = _ClipEntry("generated.video", count, frame, frame_size=(8, 8), clip_type=clip_type)
    video._insert_clip(clip, "generated")
    video._clip_list.setCurrentRow(len(video._clips) - 1)
    video._update_scrubber()
    video._update_preview()
    video._update_ui_state()
    return clip


def enter(spin, value):
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, str(value))
    QTest.keyClick(spin, Qt.Key.Key_Return)
    assert spin.hasFocus()


def assert_playhead(video, output, red):
    assert video._playhead_spin.value() == video._scrubber.value() == output
    assert video._preview_lbl.pixmap().toImage().pixelColor(0, 0).red() == red


def test_empty_single_frame_and_selection_ranges(video):
    assert not video._trim_start_spin.isEnabled()
    assert not video._trim_end_spin.isEnabled()
    assert not video._playhead_spin.isEnabled()
    clip = add_clip(video)
    enter(video._trim_start_spin, 3)
    enter(video._trim_end_spin, 8)
    count = len(video._undo_stack)
    other = add_clip(video, 1)
    assert video._trim_start_spin.maximum() == video._trim_end_spin.maximum() == 0
    assert video._trim_start_spin.value() == video._trim_end_spin.value() == 0
    video._clip_list.setCurrentRow(0)
    assert video._trim_start_spin.value() == 3
    assert video._trim_start_spin.maximum() == 8
    assert video._trim_end_spin.minimum() == 3
    assert video._trim_end_spin.maximum() == 11
    assert len(video._undo_stack) == count
    assert (clip.trim_start, clip.trim_end, other.trim_end) == (3, 8, 0)
    video._clip_list.setCurrentRow(-1)
    assert video._trim_start_spin.value() == video._trim_end_spin.value() == 0
    assert not video._trim_start_spin.isEnabled()
    video._clear_all()
    add_clip(video, 1)
    assert not video._playhead_spin.isEnabled()
    assert video._playhead_spin.maximum() == 0
    assert video._btn_export.isEnabled()


def test_keyboard_commit_trim_slider_boundaries_and_history(video):
    clip = add_clip(video)
    count = len(video._undo_stack)
    video._trim_start_spin.setFocus()
    video._trim_start_spin.selectAll()
    QTest.keyClicks(video._trim_start_spin, "10")
    assert clip.trim_start == 0
    QTest.keyClick(video._trim_start_spin, Qt.Key.Key_Return)
    assert clip.trim_start == video._trim_start_slider.value() == 10
    assert len(video._undo_stack) == count + 1
    assert_playhead(video, 0, 100)
    video._undo()
    assert video._trim_start_spin.value() == clip.trim_start == 0
    video._redo()
    assert video._trim_end_spin.minimum() == 10
    enter(video._trim_end_spin, 10)
    assert clip.trim_end == 10
    assert video._playhead_spin.maximum() == 0
    assert not video._playhead_spin.isEnabled()
    enter(video._trim_start_spin, 10)
    QTest.keyClick(video._trim_start_spin, Qt.Key.Key_Up)
    assert video._trim_start_spin.value() == clip.trim_start == 10
    enter(video._trim_end_spin, 10)
    QTest.keyClick(video._trim_end_spin, Qt.Key.Key_Down)
    assert video._trim_end_spin.value() == clip.trim_end == 10
    video._trim_start_slider.setValue(11)
    assert video._trim_start_spin.value() == video._trim_start_slider.value() == 10
    video._trim_end_slider.setValue(0)
    assert video._trim_end_spin.value() == video._trim_end_slider.value() == 10
    video._trim_end_slider.setValue(11)
    assert video._trim_end_spin.value() == 11
    assert video._trim_start_spin.maximum() == 11


def test_rendered_speed_trim_reorder_and_undo_mapping(video):
    first = add_clip(video)
    enter(video._trim_start_spin, 2)
    enter(video._trim_end_spin, 9)
    video._clip_speed_slider.setValue(50)
    assert video._playhead_spin.maximum() == 15
    enter(video._playhead_spin, 15)
    assert_playhead(video, 15, 90)
    video._clip_speed_slider.setValue(200)
    assert video._playhead_spin.maximum() == 3
    assert_playhead(video, 3, 80)
    video._undo()
    assert video._playhead_spin.maximum() == 15
    assert_playhead(video, 15, 90)
    video._redo()
    assert_playhead(video, 3, 80)
    second = add_clip(video, 3, offset=150)
    enter(video._playhead_spin, 4)
    assert_playhead(video, 4, 150)
    item = video._clip_list.takeItem(1)
    video._clip_list.insertItem(0, item)
    video._sync_clips_from_list()
    video._clip_list.setCurrentRow(0)
    assert video._clips == [second, first]
    assert video._trim_end_spin.maximum() == 2
    assert_playhead(video, 4, 40)
    video._undo()
    assert video._clips == [first, second]
    assert_playhead(video, 4, 150)
    video._redo()
    assert_playhead(video, 4, 40)
    enter(video._playhead_spin, 0)
    assert_playhead(video, 0, 150)
    video._scrubber.setValue(6)
    assert_playhead(video, 6, 80)
    video._rewind()
    assert_playhead(video, 0, 150)


def test_still_holds_and_running_transport_remain_unchanged(video):
    add_clip(video, 1, offset=170, clip_type="image")
    video._still_duration_spin.setValue(7)
    assert video._playhead_spin.maximum() == 6
    enter(video._playhead_spin, 6)
    assert_playhead(video, 6, 170)
    video._btn_play.setChecked(True)
    video._preview_timer.stop()
    with patch.object(video, "_preview_clock", return_value=video._preview_anchor + 0.10):
        video._advance_preview()
    assert_playhead(video, 1, 170)
    assert video._is_playing
    assert video._playhead_spin.value() == video._scrubber.value()
    enter(video._playhead_spin, 3)
    assert video._is_playing
    assert_playhead(video, 3, 170)
    video._still_duration_spin.setValue(1)
    assert_playhead(video, 0, 170)
    assert not video._is_playing
    assert not video._playhead_spin.isEnabled()


def test_numeric_playhead_split_preserves_cadence_and_restores_entries(video):
    clip = add_clip(video)
    enter(video._trim_start_spin, 2)
    enter(video._trim_end_spin, 9)
    video._clip_speed_slider.setValue(75)
    expected = [clip.trim_start + clip.output_index_to_source_offset(i)
                for i in range(clip.active_frames)]
    enter(video._playhead_spin, 3)
    with patch("src.ui.video_tool._load_video_clip", side_effect=lambda *a, **kw:
               _ClipEntry("generated.video", 12, clip._get_frame, frame_size=(8, 8))):
        video._split_clip_at_playhead()
    assert len(video._clips) == 2
    for index, source in enumerate(expected):
        enter(video._playhead_spin, index)
        assert_playhead(video, index, source * 10)
    count = len(video._undo_stack)
    for row in (0, 1):
        video._clip_list.setCurrentRow(row)
        selected = video._clips[row]
        assert video._trim_start_spin.value() == selected.trim_start
        assert video._trim_end_spin.value() == selected.trim_end
    assert len(video._undo_stack) == count
    video._undo()
    assert video._clips == [clip]
    assert video._trim_start_spin.value() == 2
    assert video._trim_end_spin.value() == 9
    assert_playhead(video, 3, expected[3] * 10)
    video._redo()
    assert len(video._clips) == 2
    assert video._playhead_spin.maximum() == len(expected) - 1
