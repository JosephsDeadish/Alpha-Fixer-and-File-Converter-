"""Generated-media compact geometry and keyboard checks without MainWindow."""
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.ui.video_tool import VideoToolDialog
from src.ui.theme_engine import DEFAULT_THEME, LIGHT_THEME, build_stylesheet


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def video(app):
    directory = Path("build/video-tool-layout-tests")
    directory.mkdir(parents=True, exist_ok=True)
    images = [Image.new("RGBA", (160, 90), color) for color in ("red", "green", "blue")]
    gif = directory / "generated.gif"
    still = directory / "generated.png"
    images[0].save(gif, save_all=True, append_images=images[1:], duration=100, loop=0)
    images[0].save(still)
    for image in images:
        image.close()
    dialog = VideoToolDialog()
    dialog._load_image_paths([str(gif), str(still)])
    dialog._clip_list.setCurrentRow(0)
    dialog._set_import_status("Import summary", detail="Generated media loaded.")
    dialog._import_detail_expanded = False
    dialog._apply_import_detail_visibility()
    try:
        yield dialog
    finally:
        dialog._preview_timer.stop()
        dialog.close()
        sip.delete(dialog)
        app.processEvents()
        shutil.rmtree(directory)


def settle(app):
    for _ in range(30):
        app.processEvents()


def visible(video, control):
    viewport = video._content_scroll.viewport()
    assert viewport.rect().contains(control.mapTo(viewport, QPoint()))
    assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight()))


def controls(video):
    return [
        video._btn_add_img, video._btn_remove, video._btn_split,
        video._btn_undo, video._btn_clear,
        video._insert_mode_combo, video._import_detail_toggle_btn,
        video._import_copy_btn, video._clip_list, video._trim_start_slider,
        video._trim_start_spin, video._trim_start_seconds,
        video._trim_end_slider, video._trim_end_spin, video._trim_end_seconds,
        video._clip_speed_slider, video._scrubber, video._playhead_spin,
        video._playhead_seconds,
        video._btn_rewind, video._btn_play, video._preview_audio_check,
        video._preview_mute_check, video._preview_volume_slider, video._fps_slider,
        video._brightness_slider, video._contrast_slider, video._saturation_slider,
        video._sharpness_slider, video._black_slider, video._white_slider,
        video._filter_combo, video._export_fmt_combo, video._audio_enable_check,
        video._audio_mute_check, video._audio_volume_slider, video._btn_export,
    ]


@pytest.mark.parametrize("theme", [DEFAULT_THEME, LIGHT_THEME], ids=["dark", "light"])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
def test_compact_geometry_focus_and_resize_preserves_media(app, video, theme, pixels, size):
    video.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
    video.resize(*size)
    video.show()
    settle(app)
    clips = list(video._clips)
    snapshots = [video._snapshot_clip_render_state(clip, 25) for clip in clips]
    cached = video._preview_pixmap.cacheKey()
    assert (video.width(), video.height()) == size
    assert video._content_scroll.horizontalScrollBar().maximum() == 0
    assert video._splitter.orientation() == Qt.Orientation.Vertical
    for control in controls(video):
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()
        if not control.isEnabled():
            continue
        control.setFocus()
        settle(app)
        assert app.focusWidget() is control
        visible(video, control)
    pixmap = video._preview_lbl.pixmap()
    assert pixmap.width() <= video._preview_lbl.contentsRect().width()
    assert pixmap.height() <= video._preview_lbl.contentsRect().height()
    video._btn_play.setChecked(True)
    video._preview_timer.stop()
    with patch.object(video._preview_timer, "start", wraps=video._preview_timer.start) as start:
        video.resize(2200, 1000)
        settle(app)
        assert video._splitter.orientation() == Qt.Orientation.Horizontal
        video.resize(*size)
        settle(app)
        start.assert_not_called()
    video._btn_play.setChecked(False)
    assert video._clips == clips
    assert [video._snapshot_clip_render_state(clip, 25) for clip in clips] == snapshots
    assert video._preview_pixmap.cacheKey() == cached
    assert video._content_scroll.horizontalScrollBar().maximum() == 0
    visible(video, video._btn_export)


def test_keyboard_tab_details_trim_speed_scrub_and_actions(app, video):
    video.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    video.resize(480, 400)
    video.show()
    settle(app)
    video._btn_add_img.setFocus()
    for control in controls(video):
        if not control.isEnabled():
            continue
        # Reset All is an additional focusable control between adjustments/filter.
        if control is video._filter_combo:
            QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert app.focusWidget() is control
        settle(app)
        visible(video, control)
        QTest.keyClick(control, Qt.Key.Key_Tab)
    video._btn_export.setFocus()
    QTest.keyClick(video._btn_export, Qt.Key.Key_Backtab)
    assert app.focusWidget() is video._export_fmt_combo
    video._import_detail_toggle_btn.setFocus()
    QTest.keyClick(video._import_detail_toggle_btn, Qt.Key.Key_Space)
    assert video._import_detail_box.isVisible()
    settle(app)
    assert video._content_scroll.horizontalScrollBar().maximum() == 0
    video._import_detail_box.setFocus()
    settle(app)
    visible(video, video._import_detail_box)
    QTest.keyClick(video._import_detail_box, Qt.Key.Key_Space)
    assert not video._preview_timer.isActive()
    video._import_copy_btn.setFocus()
    QTest.keyClick(video._import_copy_btn, Qt.Key.Key_Space)
    assert "Generated media loaded." in app.clipboard().text()
    for slider in (video._trim_start_slider, video._clip_speed_slider, video._fps_slider):
        slider.setFocus()
        before = slider.value()
        QTest.keyClick(slider, Qt.Key.Key_Right)
        assert slider.value() == before + 1
    video._trim_end_slider.setFocus()
    before = video._trim_end_slider.value()
    QTest.keyClick(video._trim_end_slider, Qt.Key.Key_Left)
    assert video._trim_end_slider.value() == before - 1
    video._scrubber.setFocus()
    QTest.keyClick(video._scrubber, Qt.Key.Key_Right)
    assert video._scrubber.value() == 1
    video._btn_rewind.setFocus()
    QTest.keyClick(video._btn_rewind, Qt.Key.Key_Space)
    assert video._scrubber.value() == 0
    video._btn_play.setFocus()
    QTest.keyClick(video._btn_play, Qt.Key.Key_Space)
    assert video._preview_timer.isActive()
    QTest.keyClick(video._btn_play, Qt.Key.Key_Space)
    assert not video._preview_timer.isActive()
    video._clip_list.setCurrentRow(1)
    video._still_duration_spin.setFocus()
    QTest.keyClick(video._still_duration_spin, Qt.Key.Key_Up)
    assert video._still_duration_spin.value() == 26
    with patch("src.ui.video_tool.QFileDialog.getSaveFileName", return_value=("", "")) as save:
        video._btn_export.setFocus()
        QTest.keyClick(video._btn_export, Qt.Key.Key_Space)
        save.assert_called_once()
    with patch("src.ui.video_tool.QFileDialog.getOpenFileNames", return_value=([], "")) as add:
        video._btn_add_img.setFocus()
        QTest.keyClick(video._btn_add_img, Qt.Key.Key_Space)
        add.assert_called_once()
    video._btn_remove.setFocus()
    QTest.keyClick(video._btn_remove, Qt.Key.Key_Space)
    assert len(video._clips) == 1


def test_live_font_theme_change_keeps_focus_visible(app, video):
    video.resize(640, 480)
    video.show()
    settle(app)
    video._clip_speed_slider.setFocus()
    for theme, pixels in ((LIGHT_THEME, 32), (DEFAULT_THEME, 24), (LIGHT_THEME, 13)):
        video.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
        settle(app)
        assert app.focusWidget() is video._clip_speed_slider
        visible(video, video._clip_speed_slider)
        assert video._content_scroll.horizontalScrollBar().maximum() == 0
        assert len(video._clips) == 2


@pytest.mark.parametrize("theme", [DEFAULT_THEME, LIGHT_THEME], ids=["dark", "light"])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
def test_generated_video_audio_controls_and_toolbar_actions(app, video, theme, pixels, size):
    # Exercise available-backend UI with generated frames, without native I/O.
    video._mp4_export_available = True
    video._btn_add_video.setEnabled(True)
    video._export_fmt_combo.addItem("MP4 Video (.mp4)", userData="mp4")
    video._clips[0].clip_type = "video"
    video._clips[0].has_audio = True
    video._export_fmt_combo.setCurrentIndex(1)
    video.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
    video.resize(*size)
    video.show()
    settle(app)
    for control in (video._btn_add_video, video._audio_enable_check,
                    video._audio_mute_check, video._audio_volume_slider):
        assert control.isEnabled()
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()
        control.setFocus()
        settle(app)
        visible(video, control)
    assert video._content_scroll.horizontalScrollBar().maximum() == 0
    video._audio_volume_slider.setFocus()
    QTest.keyClick(video._audio_volume_slider, Qt.Key.Key_Right)
    assert video._audio_volume_slider.value() == 101
    video._audio_mute_check.setFocus()
    QTest.keyClick(video._audio_mute_check, Qt.Key.Key_Space)
    assert not video._audio_volume_slider.isEnabled()
    assert not video._preview_timer.isActive()
    QTest.keyClick(video._audio_mute_check, Qt.Key.Key_Space)
    assert video._audio_volume_slider.isEnabled()
    with patch("src.ui.video_tool.QFileDialog.getOpenFileNames", return_value=([], "")) as add:
        video._btn_add_video.setFocus()
        QTest.keyClick(video._btn_add_video, Qt.Key.Key_Space)
        add.assert_called_once()
    video._clips[0].clip_type = "gif"
    video._scrubber.setValue(1)
    video._btn_split.setFocus()
    QTest.keyClick(video._btn_split, Qt.Key.Key_Space)
    assert len(video._clips) == 3
    video._clip_list.setCurrentRow(2)
    video._still_duration_spin.setFocus()
    settle(app)
    visible(video, video._still_duration_spin)
    assert video._still_duration_spin.height() >= video._still_duration_spin.minimumSizeHint().height()
