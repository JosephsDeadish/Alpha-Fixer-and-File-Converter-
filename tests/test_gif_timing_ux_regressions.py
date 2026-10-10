from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.ui.gif_builder import GifBuilderDialog, _load_pillow_rgba
from tests.gif_export_helpers import wait_for_gif_export


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def builder(app):
    widget = GifBuilderDialog()
    yield widget
    if widget.is_exporting():
        widget._cancel_export()
        wait_for_gif_export(widget)
    widget.close()
    sip.delete(widget)


def make_sources(folder, durations=(80, 140, 230)):
    frames = [Image.new("RGBA", (12, 8), color) for color in ("red", "green", "blue")]
    try:
        path = folder / "source.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=list(durations), loop=0)
        still = folder / "still.png"
        frames[0].save(still)
        return path, still
    finally:
        for frame in frames:
            frame.close()


def decoded_delays(path):
    with Image.open(path) as image:
        delays = []
        for index in range(image.n_frames):
            image.seek(index)
            delays.append(image.info.get("duration", 0))
        return delays


def export(builder, path):
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName", return_value=(str(path), "")), \
            patch("src.ui.gif_builder.QMessageBox.information"), \
            patch("src.ui.gif_builder.QMessageBox.critical") as error:
        builder._export()
        wait_for_gif_export(builder)
        error.assert_not_called()
    return decoded_delays(path)


def test_composited_gif_frames_keep_individual_source_duration(tmp_path):
    source, _ = make_sources(tmp_path)
    frames = _load_pillow_rgba(str(source))
    try:
        assert [frame.info.get("duration") for frame in frames] == [80, 140, 230]
        assert [frame.getpixel((0, 0)) for frame in frames] == [
            (255, 0, 0, 255), (0, 128, 0, 255), (0, 0, 255, 255),
        ]
    finally:
        for frame in frames:
            frame.close()


def test_import_preview_export_preserve_variable_gif_timing(builder, tmp_path):
    source, still = make_sources(tmp_path)
    builder.add_media_paths([str(source), str(still)])
    assert [frame.source_delay_ms for frame in builder._frames] == [80, 140, 230, None]
    builder._delay_slider.setValue(170)
    builder._frame_list.setCurrentRow(0)
    assert builder._pf_check.isChecked()
    assert builder._pf_slider.value() == 80
    assert "80 ms (source timing)" in builder._frame_diag_lbl.text()
    builder._btn_play.setChecked(True)
    for index, delay in enumerate((80, 140, 230, 170)):
        assert builder._preview_idx == index
        assert builder._preview_timer.interval() == delay
        builder._advance_preview()
    assert builder._preview_idx == 0
    assert export(builder, tmp_path / "roundtrip.gif") == [80, 140, 230, 170]
    assert not builder._preview_timer.isActive()


def test_unrelated_timing_edits_do_not_restart_current_frame(builder, tmp_path):
    source, _ = make_sources(tmp_path)
    builder.add_media_paths([str(source)])
    builder._frame_list.setCurrentRow(0)
    builder._pf_check.setChecked(True)
    builder._pf_slider.setValue(800)
    builder._btn_play.setChecked(True)
    timer_id = builder._preview_timer.timerId()
    builder._frame_list.setCurrentRow(1)
    builder._pf_check.setChecked(True)
    builder._pf_slider.setValue(600)
    builder._delay_slider.setValue(500)
    builder._update_preview_frame()
    assert builder._preview_idx == 0
    assert builder._preview_timer.interval() == 800
    assert builder._preview_timer.timerId() == timer_id
    builder._frame_list.setCurrentRow(0)
    builder._pf_slider.setValue(700)
    assert builder._preview_timer.interval() == 700
    assert builder._preview_timer.timerId() != timer_id


@pytest.mark.parametrize("jump", ["scrub", "rewind", "rewind-current"])
def test_same_delay_preview_jump_restarts_full_hold_but_refresh_does_not(
    builder, tmp_path, jump,
):
    source, _ = make_sources(tmp_path, durations=(800, 800, 800))
    builder.add_media_paths([str(source)])
    if jump == "rewind":
        builder._scrubber.setValue(2)
    builder._btn_play.setChecked(True)
    timer_id = builder._preview_timer.timerId()
    with patch.object(builder._preview_timer, "start",
                      wraps=builder._preview_timer.start) as start:
        builder._update_preview_frame()
        start.assert_not_called()
        assert builder._preview_timer.timerId() == timer_id
        if jump == "scrub":
            builder._scrubber.setValue(2)
        else:
            builder._rewind()
        start.assert_called_once_with(800)
    assert builder._preview_idx == (2 if jump == "scrub" else 0)
    assert builder._preview_timer.isActive()
    assert builder._preview_timer.interval() == 800
    assert builder._preview_timer.timerId() != timer_id
    timer_id = builder._preview_timer.timerId()
    builder._update_preview_frame()
    assert builder._preview_timer.timerId() == timer_id


def test_live_global_edits_do_not_stall_overridden_frame(builder, tmp_path):
    source, _ = make_sources(tmp_path, durations=(300, 1000, 1000))
    builder.add_media_paths([str(source)])
    builder._frame_list.setCurrentRow(0)
    builder._btn_play.setChecked(True)
    for value in (110, 120, 130, 140):
        QTest.qWait(90)
        builder._delay_slider.setValue(value)
    assert builder._preview_idx == 1
    assert builder._preview_timer.interval() == 1000


def test_missing_or_zero_gif_durations_use_global_delay(builder, tmp_path):
    source, _ = make_sources(tmp_path, durations=(0, 140, 0))
    builder.add_media_paths([str(source)])
    builder._delay_slider.setValue(190)
    assert [frame.delay_ms for frame in builder._frames] == [None, 140, None]
    assert export(builder, tmp_path / "zero-delay.gif") == [190, 140, 190]


def test_long_source_hold_is_not_clamped_or_misreported(builder, tmp_path):
    source, _ = make_sources(tmp_path, durations=(4200, 140, 230))
    builder.add_media_paths([str(source)])
    builder._frame_list.setCurrentRow(0)
    assert builder._pf_slider.value() == 4200
    assert builder._pf_val_lbl.text() == "4200 ms"
    builder._btn_play.setChecked(True)
    assert builder._preview_timer.interval() == 4200
    assert export(builder, tmp_path / "long-hold.gif") == [4200, 140, 230]


def test_scrub_rewind_and_live_override_keep_current_frame_delay(builder, tmp_path):
    source, _ = make_sources(tmp_path)
    builder.add_media_paths([str(source)])
    builder._frame_list.setCurrentRow(0)
    builder._btn_play.setChecked(True)
    builder._scrubber.setValue(2)
    assert builder._preview_idx == 2
    assert builder._preview_timer.interval() == 230
    builder._rewind()
    assert builder._preview_timer.interval() == 80
    builder._pf_slider.setValue(360)
    assert builder._preview_timer.interval() == 360
    builder._pf_check.setChecked(False)
    assert builder._preview_timer.interval() == 100
    builder._delay_slider.setValue(180)
    assert builder._preview_timer.interval() == 180
    builder._btn_play.setChecked(False)
    builder._delay_slider.setValue(220)
    assert not builder._preview_timer.isActive()
    builder._btn_play.setChecked(True)
    assert builder._preview_timer.interval() == 220
    builder._frame_list.clearSelection()
    builder._frame_list.item(1).setSelected(True)
    builder._frame_list.item(2).setSelected(True)
    builder._remove_selected()
    assert len(builder._frames) == 1
    assert not builder._preview_timer.isActive()
    assert not builder._btn_play.isChecked()
    assert not builder._scrubber.isEnabled()
    assert export(builder, tmp_path / "single.gif") == [220]


def test_selected_frame_override_and_inheritance_with_multiple_selection(builder, tmp_path):
    source, still = make_sources(tmp_path)
    builder.add_media_paths([str(source), str(still)])
    builder._frame_list.setCurrentRow(0)
    builder._frame_list.item(1).setSelected(True)
    assert len(builder._frame_list.selectedItems()) == 2
    builder._pf_slider.setValue(320)
    assert builder._frames[0].delay_ms == 320
    assert builder._frames[1].delay_ms == 140
    builder._delay_slider.setValue(210)
    builder._pf_check.setChecked(False)
    assert builder._frames[0].delay_ms is None
    assert builder._pf_slider.value() == 210
    assert builder._pf_val_lbl.text() == "210 ms"
    builder._delay_slider.setValue(250)
    assert builder._pf_slider.value() == 250
    assert builder._frames[1].delay_ms == 140
    builder._pf_check.setChecked(True)
    assert builder._frames[0].delay_ms == 250
    assert export(builder, tmp_path / "overrides.gif") == [250, 140, 230, 250]


@pytest.mark.parametrize("delay", [33, 149])
def test_gif_export_has_centisecond_granularity(builder, tmp_path, delay):
    _, still = make_sources(tmp_path)
    builder.add_media_paths([str(still)])
    builder._delay_slider.setValue(delay)
    assert builder._current_preview_delay() == delay
    assert export(builder, tmp_path / "quantized.gif") == [(delay // 10) * 10]


def test_empty_and_canceled_export_restore_editable_timeline(builder, tmp_path):
    with patch("src.ui.gif_builder.QMessageBox.information") as info:
        builder._export()
        info.assert_called_once()
    assert not builder.is_exporting()
    assert not builder._btn_play.isEnabled()
    source, still = make_sources(tmp_path)
    builder.add_media_paths([str(source)])
    builder._frame_list.setCurrentRow(0)
    builder._btn_play.setChecked(True)
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName", return_value=("", "")):
        builder._export()
    assert not builder._export_selecting
    assert builder._preview_timer.isActive()
    output = tmp_path / "canceled.gif"
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName", return_value=(str(output), "")):
        builder._export()
        builder._cancel_export()
        wait_for_gif_export(builder)
    assert not output.exists()
    assert builder._export_content.isEnabled()
    assert [frame.delay_ms for frame in builder._frames] == [80, 140, 230]
    builder._clear_all()
    assert builder._preview_frame_lbl.text() == "0 / 0"
    assert not builder._pf_check.isEnabled()
    assert not builder._preview_timer.isActive()
    builder.add_media_paths([str(still)])
    builder._frame_list.setCurrentRow(0)
    builder._delay_slider.setValue(200)
    assert builder._pf_slider.value() == 200
    assert export(builder, tmp_path / "recovered.gif") == [200]
