"""Generated-image compact geometry and keyboard checks, without MainWindow."""
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.ui.gif_builder import GifBuilderDialog
from src.ui.theme_engine import DEFAULT_THEME, LIGHT_THEME, build_stylesheet


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def builder(app):
    directory = Path("build/gif-builder-layout-tests")
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, color in enumerate(("red", "green", "blue")):
        path = directory / f"{index}.png"
        with Image.new("RGBA", (24, 16), color) as image:
            image.save(path)
        paths.append(str(path))
    dialog = GifBuilderDialog(paths)
    dialog._frame_list.setCurrentRow(0)
    dialog._pf_check.setChecked(True)
    try:
        yield dialog
    finally:
        dialog.close()
        sip.delete(dialog)
        app.processEvents()
        shutil.rmtree(directory)


def settle(app):
    for _ in range(30):
        app.processEvents()


def visible(builder, control):
    viewport = builder._content_scroll.viewport()
    assert viewport.rect().contains(control.mapTo(viewport, QPoint()))
    assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight()))


def controls(builder):
    return [
        builder._btn_add, builder._btn_remove, builder._btn_clear,
        builder._import_detail_toggle_btn, builder._import_copy_btn,
        builder._frame_list, builder._pf_check, builder._pf_slider,
        builder._delay_slider, builder._loop_slider, builder._width_slider,
        builder._height_slider, builder._optimize_check, builder._scrubber,
        builder._btn_rewind, builder._btn_play, builder._btn_export,
    ]


@pytest.mark.parametrize("theme", [DEFAULT_THEME, LIGHT_THEME], ids=["dark", "light"])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
def test_compact_theme_controls_focus_and_resize(app, builder, theme, pixels, size):
    builder.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
    builder.resize(*size)
    builder.show()
    settle(app)
    frames = list(builder._frames)
    images = [frame._pil.tobytes() for frame in frames]
    delays = [frame.delay_ms for frame in frames]
    assert (builder.width(), builder.height()) == size
    assert builder._content_scroll.horizontalScrollBar().maximum() == 0
    assert builder._splitter.orientation() == Qt.Orientation.Vertical
    for control in controls(builder):
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()
        control.setFocus()
        settle(app)
        assert app.focusWidget() is control
        visible(builder, control)
    builder._btn_play.setChecked(True)
    with patch.object(builder._preview_timer, "start", wraps=builder._preview_timer.start) as start:
        builder.resize(1800, 1000)
        settle(app)
        assert builder._splitter.orientation() == Qt.Orientation.Horizontal
        builder.resize(*size)
        settle(app)
        start.assert_not_called()
    builder._btn_play.setChecked(False)
    visible(builder, builder._btn_export)
    assert builder._frames == frames
    assert [frame._pil.tobytes() for frame in frames] == images
    assert [frame.delay_ms for frame in frames] == delays
    assert builder._content_scroll.horizontalScrollBar().maximum() == 0


def test_keyboard_tab_space_edit_scrub_and_actions(app, builder):
    builder.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    builder.resize(480, 400)
    builder.show()
    settle(app)
    builder._btn_add.setFocus()
    expected = controls(builder)
    for index, control in enumerate(expected):
        assert app.focusWidget() is control
        settle(app)
        visible(builder, control)
        if index < len(expected) - 1:
            QTest.keyClick(control, Qt.Key.Key_Tab)
    QTest.keyClick(builder._btn_export, Qt.Key.Key_Backtab)
    assert app.focusWidget() is builder._btn_play
    builder._import_detail_toggle_btn.setFocus()
    QTest.keyClick(builder._import_detail_toggle_btn, Qt.Key.Key_Space)
    assert builder._import_detail_box.isVisible()
    settle(app)
    assert builder._content_scroll.horizontalScrollBar().maximum() == 0
    builder._import_copy_btn.setFocus()
    QTest.keyClick(builder._import_copy_btn, Qt.Key.Key_Space)
    assert "Import summary" in app.clipboard().text()
    builder._import_detail_toggle_btn.setFocus()
    QTest.keyClick(builder._import_detail_toggle_btn, Qt.Key.Key_Space)
    assert not builder._import_detail_box.isVisible()
    builder._pf_check.setFocus()
    QTest.keyClick(builder._pf_check, Qt.Key.Key_Space)
    assert not builder._pf_check.isChecked()
    assert not builder._btn_play.isChecked()
    QTest.keyClick(builder._pf_check, Qt.Key.Key_Space)
    assert builder._pf_check.isChecked()
    builder._pf_slider.setFocus()
    QTest.keyClick(builder._pf_slider, Qt.Key.Key_Right)
    assert builder._frames[0].delay_ms == 101
    for slider in (builder._delay_slider, builder._loop_slider,
                   builder._width_slider, builder._height_slider):
        slider.setFocus()
        before = slider.value()
        QTest.keyClick(slider, Qt.Key.Key_Right)
        assert slider.value() == before + 1
    builder._optimize_check.setFocus()
    QTest.keyClick(builder._optimize_check, Qt.Key.Key_Space)
    assert not builder._optimize_check.isChecked()
    assert not builder._preview_timer.isActive()
    builder._scrubber.setFocus()
    QTest.keyClick(builder._scrubber, Qt.Key.Key_Right)
    assert builder._preview_idx == 1
    builder._btn_rewind.setFocus()
    QTest.keyClick(builder._btn_rewind, Qt.Key.Key_Space)
    assert builder._preview_idx == 0
    builder._btn_play.setFocus()
    QTest.keyClick(builder._btn_play, Qt.Key.Key_Space)
    assert builder._preview_timer.isActive()
    assert builder._preview_timer.interval() == 101
    QTest.keyClick(builder._btn_play, Qt.Key.Key_Space)
    assert not builder._preview_timer.isActive()
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName", return_value=("", "")) as save:
        builder._btn_export.setFocus()
        QTest.keyClick(builder._btn_export, Qt.Key.Key_Space)
        save.assert_called_once()
    with patch("src.ui.gif_builder.QFileDialog.getOpenFileNames", return_value=([], "")) as add:
        builder._btn_add.setFocus()
        QTest.keyClick(builder._btn_add, Qt.Key.Key_Space)
        add.assert_called_once()
    builder._btn_remove.setFocus()
    QTest.keyClick(builder._btn_remove, Qt.Key.Key_Space)
    assert len(builder._frames) == 2
    builder._btn_clear.setFocus()
    QTest.keyClick(builder._btn_clear, Qt.Key.Key_Space)
    assert not builder._frames


def test_live_font_and_theme_change_preserves_keyboard_focus(app, builder):
    builder.resize(640, 480)
    builder.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 13px; }")
    builder.show()
    settle(app)
    builder._width_slider.setFocus()
    for theme, pixels in ((LIGHT_THEME, 32), (DEFAULT_THEME, 24), (LIGHT_THEME, 13)):
        builder.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
        settle(app)
        assert app.focusWidget() is builder._width_slider
        assert builder._content_scroll.horizontalScrollBar().maximum() == 0
        visible(builder, builder._width_slider)
        assert len(builder._frames) == 3
