"""Precise source trim and rendered playhead entry using real Qt key events."""
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from src.ui.video_tool import VideoToolDialog, _ClipEntry
from src.ui.clock_timecode import (
    parse_clock_timecode, format_clock_timecode, parse_smpte_timecode, format_smpte_timecode,
)


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


def add_clip(video, count=12, offset=0, clip_type="video", fps=25.0):
    def frame(index):
        return Image.new("RGBA", (8, 8), (offset + index * 10, 0, 0, 255))

    clip = _ClipEntry("generated.video", count, frame, fps=fps,
                      frame_size=(8, 8), clip_type=clip_type)
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
    assert video._playhead_seconds.value() == pytest.approx(
        output / video._fps_slider.value(), abs=5e-10)
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


@pytest.mark.parametrize("speed", [50, 75, 200])
@pytest.mark.parametrize("unit", ["seconds", "timecode", "smpte", "drop"])
def test_numeric_playhead_split_preserves_cadence_and_restores_entries(video, speed, unit):
    fps = 30000 / 1001
    clip = add_clip(video, fps=fps)

    def timestamp(frame, rate):
        if unit in ("smpte", "drop"):
            return format_smpte_timecode(frame, rate, unit == "drop" and rate == fps)
        return (format_clock_timecode(frame, rate) if unit == "timecode"
                else f"{frame / rate:.9f}")

    field = "seconds" if unit == "seconds" else "timecode"
    enter(getattr(video, f"_trim_start_{field}"), timestamp(2, fps))
    enter(getattr(video, f"_trim_end_{field}"), timestamp(9, fps))
    video._clip_speed_slider.setValue(speed)
    expected = [clip.trim_start + clip.output_index_to_source_offset(i)
                for i in range(clip.active_frames)]
    split_at = min(3, clip.active_frames - 2)
    enter(getattr(video, f"_playhead_{field}"),
          timestamp(split_at, video._fps_slider.value()))
    with patch("src.ui.video_tool._load_video_clip", side_effect=lambda *a, **kw:
               _ClipEntry("generated.video", 12, clip._get_frame, fps=fps, frame_size=(8, 8))):
        video._split_clip_at_playhead()
    assert len(video._clips) == 2
    for index, source in enumerate(expected):
        enter(getattr(video, f"_playhead_{field}"),
              timestamp(index, video._fps_slider.value()))
        assert_playhead(video, index, source * 10)
    count = len(video._undo_stack)
    for row in (0, 1):
        video._clip_list.setCurrentRow(row)
        selected = video._clips[row]
        assert video._trim_start_spin.value() == selected.trim_start
        assert video._trim_end_spin.value() == selected.trim_end
        assert video._trim_start_seconds.value() == pytest.approx(
            selected.trim_start / fps, abs=5e-10)
        assert video._trim_end_seconds.value() == pytest.approx(
            selected.trim_end / fps, abs=5e-10)
    assert len(video._undo_stack) == count
    video._undo()
    assert video._clips == [clip]
    assert video._trim_start_spin.value() == 2
    assert video._trim_end_spin.value() == 9
    assert_playhead(video, split_at, expected[split_at] * 10)
    video._redo()
    assert len(video._clips) == 2
    assert video._playhead_spin.maximum() == len(expected) - 1


@pytest.mark.parametrize("fps", [24000 / 1001, 30000 / 1001, 60000 / 1001])
def test_seconds_source_trim_roundtrip_and_rendered_output_clock(video, fps):
    clip = add_clip(video, fps=fps)
    count = len(video._undo_stack)
    video._trim_start_seconds.setFocus()
    video._trim_start_seconds.selectAll()
    QTest.keyClicks(video._trim_start_seconds, f"{2 / fps:.9f}")
    assert clip.trim_start == 0
    QTest.keyClick(video._trim_start_seconds, Qt.Key.Key_Return)
    assert clip.trim_start == 2
    assert len(video._undo_stack) == count + 1
    enter(video._trim_end_seconds, f"{9 / fps:.9f}")
    assert (clip.trim_start, clip.trim_end) == (2, 9)
    assert video._trim_end_seconds.minimum() == pytest.approx(2 / fps, abs=5e-10)
    assert video._trim_start_seconds.maximum() == pytest.approx(9 / fps, abs=5e-10)
    # Inclusive Out is frame 9's timestamp: the included range has eight frames.
    assert clip.base_active_frames == 8
    video._clip_speed_slider.setValue(50)
    video._fps_slider.setValue(30)
    assert video._playhead_seconds.maximum() == pytest.approx(15 / 30)
    enter(video._playhead_seconds, 15 / 30)
    assert_playhead(video, 15, 90)
    video._clip_speed_slider.setValue(200)
    assert_playhead(video, 3, 80)
    history = len(video._undo_stack)
    video._fps_slider.setValue(60)
    assert_playhead(video, 3, 80)
    assert video._playhead_seconds.value() == 0.05
    assert video._trim_start_seconds.value() == pytest.approx(2 / fps, abs=5e-10)
    assert len(video._undo_stack) == history + 1  # Existing FPS edit, not selector sync.
    assert video._undo_stack[-1].label != "Trim start"
    video._undo()
    assert video._fps_slider.value() == 30
    assert clip.speed_percent == 200
    video._undo()
    assert clip.speed_percent == 50
    assert video._trim_end_seconds.value() == pytest.approx(9 / fps, abs=5e-10)
    video._redo()
    assert clip.speed_percent == 200


def test_seconds_rounding_bounds_noop_and_selection_do_not_edit(video):
    assert not video._trim_start_seconds.isEnabled()
    assert not video._trim_end_seconds.isEnabled()
    assert not video._playhead_seconds.isEnabled()
    first = add_clip(video, fps=25)
    enter(video._trim_start_seconds, 0.099999)
    assert first.trim_start == 2
    enter(video._trim_start_seconds, 0.1)
    assert first.trim_start == 3  # Exactly halfway between frames 2 and 3.
    count = len(video._undo_stack)
    enter(video._trim_start_seconds, 0.121)
    assert first.trim_start == 3
    assert video._trim_start_seconds.value() == 0.12
    assert len(video._undo_stack) == count
    enter(video._playhead_seconds, 0.02)
    assert_playhead(video, 1, 40)
    second = add_clip(video, 1, fps=30000 / 1001)
    assert video._trim_start_seconds.maximum() == video._trim_end_seconds.maximum() == 0
    video._clip_list.setCurrentRow(0)
    assert video._trim_start_seconds.value() == 0.12
    assert (first.trim_start, second.trim_start) == (3, 0)
    assert len(video._undo_stack) == count
    video._clip_list.setCurrentRow(-1)
    assert video._trim_start_seconds.value() == video._trim_end_seconds.value() == 0
    assert not video._trim_end_seconds.isEnabled()
    video._clear_all()
    assert video._playhead_seconds.maximum() == video._playhead_seconds.value() == 0
    assert not video._playhead_seconds.isEnabled()


@pytest.mark.parametrize("seconds, expected", [
    ("1.024999999", 61),
    ("1.025", 62),
    ("1.025000001", 62),
])
@pytest.mark.parametrize("control", ["trim_start", "trim_end", "playhead"])
def test_seconds_decimal_half_frame_ui_rounding(video, seconds, expected, control):
    clip = add_clip(video, count=100, fps=60)
    video._fps_slider.setValue(60)
    enter(getattr(video, f"_{control}_seconds"), seconds)
    if control == "playhead":
        assert video._scrubber.value() == video._playhead_spin.value() == expected
    else:
        assert getattr(clip, control) == expected
        assert getattr(video, f"_{control}_spin").value() == expected


@pytest.mark.parametrize("seconds, fps, expected", [
    (1.024999999, 60, 61),
    (1.025, 60, 62),
    (1.025000001, 60, 62),
    (4.919999999, 12.5, 61),
    (4.92, 12.5, 62),
    (4.920000001, 12.5, 62),
    (49.999999999, 29.97, 1498),
    (50, 29.97, 1499),
    (50.000000001, 29.97, 1499),
])
def test_seconds_decimal_half_up_with_fractional_fps(seconds, fps, expected):
    assert VideoToolDialog._seconds_to_frame(seconds, fps) == expected


def test_seconds_frame_steps_and_still_transport(video):
    clip = add_clip(video, fps=60000 / 1001)
    video._trim_start_seconds.setFocus()
    QTest.keyClick(video._trim_start_seconds, Qt.Key.Key_Up)
    assert clip.trim_start == 1
    QTest.keyClick(video._trim_start_seconds, Qt.Key.Key_Down)
    assert clip.trim_start == 0
    video._trim_end_seconds.setFocus()
    QTest.keyClick(video._trim_end_seconds, Qt.Key.Key_Up)
    assert clip.trim_end == 11
    QTest.keyClick(video._trim_end_seconds, Qt.Key.Key_Down)
    assert clip.trim_end == 10
    video._playhead_seconds.setFocus()
    QTest.keyClick(video._playhead_seconds, Qt.Key.Key_Up)
    assert_playhead(video, 1, 10)
    video._clear_all()
    add_clip(video, 1, offset=170, clip_type="image")
    video._still_duration_spin.setValue(7)
    enter(video._playhead_seconds, 6 / video._fps_slider.value())
    assert_playhead(video, 6, 170)
    video._btn_play.setChecked(True)
    video._preview_timer.stop()
    enter(video._playhead_seconds, 3 / video._fps_slider.value())
    assert video._is_playing
    assert_playhead(video, 3, 170)
    video._still_duration_spin.setValue(1)
    assert not video._playhead_seconds.isEnabled()
    assert_playhead(video, 0, 170)


def test_seconds_long_clip_range_and_last_frame_roundtrip(video):
    fps = 30000 / 1001
    clip = add_clip(video, fps=fps)
    # Extend generated metadata without decoding or allocating a long native clip.
    clip.total_frames = 2_000_000_000
    clip.trim_end = clip.total_frames - 1
    video._on_clip_selected(0)
    maximum = video._trim_end_seconds.maximum()
    assert maximum > 60_000_000
    assert video._seconds_to_frame(maximum, fps) == clip.trim_end
    assert video._seconds_to_frame(video._trim_end_seconds.value(), fps) == clip.trim_end
    count = len(video._undo_stack)
    enter(video._trim_end_seconds, f"{maximum:.9f}")
    assert clip.trim_end == 1_999_999_999
    assert len(video._undo_stack) == count
    assert video._trim_end_timecode.text() == format_clock_timecode(clip.trim_end, fps)
    enter(video._trim_end_timecode, video._trim_end_timecode.text())
    assert clip.trim_end == 1_999_999_999
    assert len(video._undo_stack) == count


@pytest.mark.parametrize("text, expected", [
    ("00:00:00.000", "0"),
    ("01:02:03.004", "3723.004"),
    ("100:59:59.999", "363599.999"),
])
def test_clock_exact_decimal_parse(text, expected):
    from decimal import Decimal
    assert parse_clock_timecode(text) == Decimal(expected)


@pytest.mark.parametrize("fps, frame, expected", [
    (29.97, 1799, "00:00:59;29"),
    (30000 / 1001, 1800, "00:01:00;02"),
    (29.97, 3598, "00:02:00;02"),
    (29.97, 17981, "00:09:59;29"),
    (29.97, 17982, "00:10:00;00"),
    (29.97, 107892, "01:00:00;00"),
    (29.97, 2589408, "24:00:00;00"),
    (59.94, 3599, "00:00:59;59"),
    (60000 / 1001, 3600, "00:01:00;04"),
    (59.94, 35964, "00:10:00;00"),
    (59.94, 215784, "01:00:00;00"),
    (59.94, 5178816, "24:00:00;00"),
])
def test_drop_frame_known_labels_and_absolute_hours(fps, frame, expected):
    assert format_smpte_timecode(frame, fps, True) == expected
    assert parse_smpte_timecode(expected, fps) == frame


@pytest.mark.parametrize("fps", [29.97, 30000 / 1001, 59.94, 60000 / 1001])
def test_drop_frame_boundary_and_long_timeline_exact_inverse(fps):
    nominal = 30 if fps < 40 else 60
    ten_minutes = nominal * 600 - (nominal // 15) * 9
    for boundary in (nominal * 60, ten_minutes, ten_minutes * 6,
                     ten_minutes * 144, 2_000_000_000):
        for frame in range(boundary - 5, boundary + 6):
            label = format_smpte_timecode(frame, fps, True)
            assert parse_smpte_timecode(label, fps) == frame


@pytest.mark.parametrize("fps, text", [
    (29.97, "00:01:00;00"), (29.97, "00:01:00;01"),
    (59.94, "01:11:00;03"), (59.94, "00:00:00;60"),
    (30, "00:01:00;02"), (60, "00:01:00;04"),
    (24000 / 1001, "00:00:01:00"), (29.9701, "00:01:00;02"),
    (25, "00:00:01:25"), (25, "00:60:00:00"),
    (29.97, "00:00:00;000"), (29.97, "-00:00:00;02"),
])
def test_smpte_rejects_skipped_labels_and_unsupported_rates(fps, text):
    with pytest.raises(ValueError):
        parse_smpte_timecode(text, fps)


@pytest.mark.parametrize("fps", [1, 24, 25, 30, 50, 60, 29.97, 59.94])
def test_non_drop_frame_is_label_clock_not_elapsed_seconds(fps):
    nominal = round(fps)
    for frame in (nominal * 60, nominal * 3600 * 100 + 17, 1_999_999_999):
        assert parse_smpte_timecode(format_smpte_timecode(frame, fps), fps) == frame
    assert format_smpte_timecode(nominal * 60, fps) == "00:01:00:00"


@pytest.mark.parametrize("fps, expected", [(30000 / 1001, 1800), (60000 / 1001, 3600)])
def test_drop_source_ui_invalid_label_noop_display_history_and_selection(video, fps, expected):
    clip = add_clip(video, fps=fps)
    clip.total_frames = 2_000_000_000
    clip.trim_end = clip.total_frames - 1
    video._on_clip_selected(0)
    entry = video._trim_start_timecode
    entry.setDisplayFormat("drop")
    before = len(video._undo_stack)
    enter(entry, "00:01:00;00")
    assert clip.trim_start == 0 and len(video._undo_stack) == before
    enter(entry, format_smpte_timecode(expected, fps, True))
    assert clip.trim_start == expected and len(video._undo_stack) == before + 1
    assert entry.text() == format_smpte_timecode(expected, fps, True)
    video._undo()
    assert entry.text() == "00:00:00;00"
    video._redo()
    assert clip.trim_start == expected
    # Clock input remains usable even with a frame-label display selected.
    enter(entry, format_clock_timecode(expected + 1, fps))
    assert clip.trim_start == expected + 1
    add_clip(video, fps=25)
    assert entry.text() == "00:00:00.000"  # Unsupported DF falls back, never relabels 25 FPS.
    menu = entry.createStandardContextMenu()
    actions = menu.actions()[-1].menu().actions()
    assert actions[1].isEnabled() and not actions[2].isEnabled()
    actions[1].trigger()
    assert entry.text() == "00:00:00:00"
    menu.deleteLater()
    video._clip_list.setCurrentRow(0)
    enter(video._trim_end_timecode, format_smpte_timecode(1_999_999_999, fps, True))
    assert clip.trim_end == 1_999_999_999


def test_smpte_rendered_seek_clamps_and_preserves_transport(video):
    add_clip(video, count=100, fps=29.97)
    video._fps_slider.setValue(25)
    video._playhead_timecode.setDisplayFormat("smpte")
    video._btn_play.setChecked(True)
    video._preview_timer.stop()
    enter(video._playhead_timecode, "00:00:02:12")
    assert video._scrubber.value() == 62 and video._is_playing
    assert video._playhead_timecode.text() == "00:00:02:12"
    enter(video._playhead_timecode, "100:00:00:00")
    assert video._scrubber.value() == 99 and video._is_playing
    enter(video._playhead_timecode, "00:00:01;02")
    assert video._scrubber.value() == 99  # DF cannot be applied to integer output FPS.


@pytest.mark.parametrize("frame, fps, expected", [
    (1, 16, "00:00:00.063"),
    (1, 30000 / 1001, "00:00:00.033"),
    (59999, 1000, "00:00:59.999"),
    (60000, 1000, "00:01:00.000"),
    (3600000, 1000, "01:00:00.000"),
    (360000000, 1000, "100:00:00.000"),
])
def test_clock_format_rounding_and_carries(frame, fps, expected):
    assert format_clock_timecode(frame, fps) == expected


@pytest.mark.parametrize("clock, expected", [
    ("00:00:01.024", 61),
    ("00:00:01.025", 62),
    ("00:00:01.026", 62),
])
@pytest.mark.parametrize("control", ["trim_start", "trim_end", "playhead"])
def test_clock_decimal_half_frame_ui_rounding(video, clock, expected, control):
    clip = add_clip(video, count=100, fps=60)
    video._fps_slider.setValue(60)
    enter(getattr(video, f"_{control}_timecode"), clock)
    assert getattr(video, f"_{control}_spin").value() == expected
    if control != "playhead":
        assert getattr(clip, control) == expected


@pytest.mark.parametrize("clock", [
    "", "bad", "-00:00:01.000", "00:60:00.000", "00:00:60.000",
    "0:00:01.000", "00:00:01.00", "00:00:01;000", "00:00:01.000 ",
])
@pytest.mark.parametrize("control", ["trim_start", "trim_end", "playhead"])
def test_clock_invalid_is_noop_on_enter_and_focus_loss(video, clock, control):
    add_clip(video)
    video._scrubber.setValue(3)
    project = video._project
    project._saved = project.fingerprint()
    count = len(video._undo_stack)
    fingerprint = project.fingerprint()
    entry = getattr(video, f"_{control}_timecode")
    original = entry.text()
    with patch.object(video, "_export") as export, \
            patch("src.ui.video_tool.QFileDialog.getSaveFileName") as picker:
        enter(entry, clock)
        assert entry.text() == original
        entry.selectAll()
        QTest.keyClicks(entry, clock)
        video._playhead_spin.setFocus()
        QApplication.processEvents()
    export.assert_not_called()
    picker.assert_not_called()
    assert entry.text() == original
    assert video._scrubber.value() == 3
    assert project.fingerprint() == fingerprint
    assert not project.dirty
    assert len(video._undo_stack) == count


def test_clock_bounds_fps_selection_and_history_sync(video):
    assert not video._trim_start_timecode.isEnabled()
    assert not video._playhead_timecode.isEnabled()
    first = add_clip(video, fps=30000 / 1001)
    enter(video._trim_start_timecode, "00:00:00.067")
    enter(video._trim_end_timecode, "00:00:00.300")
    assert (first.trim_start, first.trim_end) == (2, 9)
    video._clip_speed_slider.setValue(50)
    video._fps_slider.setValue(30)
    enter(video._playhead_timecode, "99:59:59.999")
    assert_playhead(video, 15, 90)
    assert video._playhead_timecode.text() == "00:00:00.500"
    count = len(video._undo_stack)
    video._fps_slider.setValue(60)
    assert video._playhead_timecode.text() == "00:00:00.250"
    assert video._trim_start_timecode.text() == "00:00:00.067"
    assert len(video._undo_stack) == count + 1
    video._undo()
    assert video._playhead_timecode.text() == "00:00:00.500"
    video._redo()
    assert video._playhead_timecode.text() == "00:00:00.250"
    second = add_clip(video, 1, fps=60)
    assert video._trim_start_timecode.text() == "00:00:00.000"
    video._clip_list.setCurrentRow(0)
    # Selection synchronization discards a draft without applying it to another clip.
    video._trim_start_timecode.setFocus()
    video._trim_start_timecode.selectAll()
    QTest.keyClicks(video._trim_start_timecode, "99:59:59.999")
    video._clip_list.setCurrentRow(1)
    assert video._trim_start_timecode.text() == "00:00:00.000"
    assert (first.trim_start, second.trim_start) == (2, 0)
    video._clip_list.setCurrentRow(0)
    enter(video._trim_end_timecode, "00:00:00.000")
    assert first.trim_end == first.trim_start == 2
    enter(video._trim_start_timecode, "99:59:59.999")
    assert first.trim_start == 2
    video._undo()
    assert first.trim_end == 9
    assert video._trim_end_timecode.text() == "00:00:00.300"
    video._clear_all()
    assert not video._trim_end_timecode.isEnabled()
    assert not video._playhead_timecode.isEnabled()
    assert video._playhead_timecode.text() == "00:00:00.000"


def test_clock_focus_noop_and_enter_inside_nested_dialog(video):
    add_clip(video, fps=30000 / 1001)
    host = QDialog()
    video.setParent(host, video.windowFlags())
    host.show()
    video.show()
    QApplication.processEvents()
    video._trim_start_slider.setValue(2)
    count = len(video._undo_stack)
    fingerprint = video._project.fingerprint()
    with patch("src.ui.video_tool.QFileDialog.getSaveFileName") as picker:
        for entry in (video._trim_start_timecode, video._trim_end_timecode,
                      video._playhead_timecode):
            entry.setFocus()
            QTest.keyClick(entry, Qt.Key.Key_Return)
        assert video._project.fingerprint() == fingerprint
        assert len(video._undo_stack) == count
        enter(video._trim_start_timecode, "00:00:00.100")
        enter(video._playhead_timecode, "00:00:00.080")
    picker.assert_not_called()
    assert video._clips[0].trim_start == 3
    assert_playhead(video, 2, 50)
    video.setParent(None, video.windowFlags())
    host.close()
    sip.delete(host)


@pytest.mark.parametrize("control", ["trim_start", "trim_end", "playhead"])
def test_clock_large_hours_clamp_before_qt_integer_conversion(video, control):
    add_clip(video)
    enter(getattr(video, f"_{control}_timecode"), "1000000000:00:00.000")
    assert getattr(video, f"_{control}_spin").value() == 11
    assert getattr(video, f"_{control}_timecode").text() == "00:00:00.440"


def test_clock_focus_loss_commits_and_seeking_does_not_dirty_project(video):
    clip = add_clip(video, fps=60)
    video._fps_slider.setValue(30)
    video._project._saved = video._project.fingerprint()
    count = len(video._undo_stack)
    entry = video._playhead_timecode
    entry.setFocus()
    entry.selectAll()
    QTest.keyClicks(entry, "00:00:00.100")
    assert video._scrubber.value() == 0
    video._trim_start_timecode.setFocus()
    assert_playhead(video, 3, 30)
    assert not video._project.dirty
    assert len(video._undo_stack) == count
    entry = video._trim_start_timecode
    entry.selectAll()
    QTest.keyClicks(entry, "00:00:00.050")
    assert clip.trim_start == 0
    video._playhead_timecode.setFocus()
    assert clip.trim_start == 3
    assert video._project.dirty
    assert len(video._undo_stack) == count + 1
