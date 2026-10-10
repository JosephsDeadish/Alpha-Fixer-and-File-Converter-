import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from src.ui.video_tool import VideoToolDialog, _ClipEntry, _CLIP_ROLE


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def video(app):
    widget = VideoToolDialog()
    yield widget
    widget._preview_timer.stop()
    widget.close()
    sip.delete(widget)


def add_clip(video, count=10):
    requested = []

    def frame(index):
        requested.append(index)
        return Image.new("RGBA", (8, 8), (index * 20, 0, 0, 255))

    clip = _ClipEntry("/tmp/timing.mp4", count, frame, frame_size=(8, 8))
    video._clips.append(clip)
    item = QListWidgetItem("test clip")
    item.setData(_CLIP_ROLE, clip)
    video._clip_list.addItem(item)
    video._clip_list.setCurrentRow(0)
    video._update_scrubber()
    video._update_preview()
    video._update_ui_state()
    return clip, requested


def test_trim_start_refreshes_paused_preview_without_scrubbing(video):
    clip, requested = add_clip(video)
    requested.clear()
    video._trim_start_slider.setValue(3)
    assert clip.trim_start == 3
    assert requested[-1] == 3
    assert video._scrubber.value() == 0
    assert video._pos_lbl.text() == "1 / 7"
    assert "7 frames" in video._timeline_summary_lbl.text()
    assert video._preview_lbl.pixmap().toImage().pixelColor(0, 0).red() == 60


def test_trim_end_refreshes_clamped_playhead_and_image(video):
    clip, requested = add_clip(video)
    video._scrubber.setValue(9)
    requested.clear()
    video._trim_end_slider.setValue(4)
    assert clip.trim_end == 4
    assert video._scrubber.value() == 4
    assert requested[-1] == 4
    assert video._pos_lbl.text() == "5 / 5"
    assert video._preview_lbl.pixmap().toImage().pixelColor(0, 0).red() == 80


def test_fps_edits_update_running_timer_and_duration_summaries(video):
    add_clip(video)
    video._btn_play.setChecked(True)
    assert video._preview_timer.interval() == 40
    video._fps_slider.setValue(10)
    assert video._preview_timer.isActive()
    assert video._preview_timer.interval() == 100
    assert video._fps_val_lbl.text() == "10 fps"
    assert "1.00 s" in video._timeline_summary_lbl.text()
    video._btn_play.setChecked(False)
    video._fps_slider.setValue(20)
    assert not video._preview_timer.isActive()
    assert "0.50 s" in video._timeline_summary_lbl.text()
    video._btn_play.setChecked(True)
    assert video._preview_timer.interval() == 50


def test_trim_to_single_frame_stops_playback_but_keeps_export_available(video):
    add_clip(video)
    video._btn_play.setChecked(True)
    video._trim_end_slider.setValue(0)
    assert video._total_preview_frames() == 1
    assert not video._preview_timer.isActive()
    assert not video._is_playing
    assert not video._btn_play.isChecked()
    assert not video._btn_play.isEnabled()
    assert not video._scrubber.isEnabled()
    assert video._btn_export.isEnabled()
    assert video._btn_rewind.isEnabled()
    video._trim_end_slider.setValue(4)
    assert video._btn_play.isEnabled()
    assert video._scrubber.isEnabled()
    assert not video._preview_timer.isActive()
