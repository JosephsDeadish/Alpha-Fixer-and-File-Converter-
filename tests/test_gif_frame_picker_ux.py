"""Offscreen geometry and keyboard regressions for the GIF frame picker."""
import shutil
from pathlib import Path

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QBoxLayout, QDialog, QDialogButtonBox, QLabel

from src.ui.gif_frame_picker import GifFramePickerDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def picker(app):
    directory = Path("build/frame-picker-tests/ux")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "frames.gif"
    frames = [Image.new("RGB", (16, 16), (idx * 7, 20, 100)) for idx in range(24)]
    try:
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=40)
    finally:
        for frame in frames:
            frame.close()
    dialog = GifFramePickerDialog(str(path))
    try:
        yield dialog
    finally:
        dialog.close()
        sip.delete(dialog)
        app.processEvents()
        shutil.rmtree(directory)


def settle(app):
    for _ in range(8):
        app.processEvents()


def assert_focused_visible(picker, checkbox):
    viewport = picker._scroll.viewport()
    top_left = checkbox.mapTo(viewport, QPoint())
    assert viewport.rect().contains(top_left)
    assert viewport.rect().contains(
        checkbox.mapTo(viewport, checkbox.rect().bottomRight())
    )


@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(320, 300), (480, 400)])
def test_narrow_large_font_layout_and_resize(app, picker, pixels, size):
    picker.setStyleSheet(f"QWidget {{ font-size: {pixels}px; }}")
    picker.resize(*size)
    picker.show()
    settle(app)
    assert picker.width() == size[0]
    assert picker._scroll.horizontalScrollBar().maximum() == 0
    narrow_columns = picker._columns
    original_cells = list(picker._cells)
    original_checks = list(picker._checkboxes)
    thumbnails = [
        cell.findChild(QLabel).pixmap().toImage() for cell in original_cells
    ]
    for idx, thumbnail in enumerate(thumbnails):
        assert thumbnail.pixelColor(8, 8).getRgb() == (idx * 7, 20, 100, 255)
    for checkbox in original_checks:
        assert checkbox.width() >= checkbox.minimumSizeHint().width()
        checkbox.setFocus()
        settle(app)
        assert_focused_visible(picker, checkbox)
    for control in (picker._btn_all, picker._btn_none, picker._btn_invert, picker._btn_box):
        assert picker.rect().contains(control.geometry())
    picker.resize(900, 600)
    settle(app)
    assert picker._columns > narrow_columns
    assert picker._cells == original_cells
    assert picker._checkboxes == original_checks
    assert [
        cell.findChild(QLabel).pixmap().toImage() for cell in original_cells
    ] == thumbnails
    assert picker.selected_indices() == list(range(24))
    picker.resize(*size)
    settle(app)
    assert picker._scroll.horizontalScrollBar().maximum() == 0
    assert_focused_visible(picker, original_checks[-1])


def test_live_font_change_reflows_toolbar_and_grid(app, picker):
    picker.resize(320, 420)
    picker.show()
    settle(app)
    columns = picker._columns
    font = picker.font()
    font.setPixelSize(32)
    picker.setFont(font)
    settle(app)
    assert picker._columns < columns
    assert picker._tool_row.direction() == QBoxLayout.Direction.TopToBottom
    assert picker._scroll.horizontalScrollBar().maximum() == 0


def test_keyboard_focus_scroll_selection_and_accept(app, picker):
    picker.resize(320, 300)
    picker.show()
    settle(app)
    ok = picker._btn_box.button(QDialogButtonBox.StandardButton.Ok)
    picker._btn_none.setFocus()
    QTest.keyClick(picker._btn_none, Qt.Key.Key_Space)
    assert picker.selected_indices() == []
    assert not ok.isEnabled()
    picker.accept()
    assert picker.result() != QDialog.DialogCode.Accepted
    QTest.keyClick(picker._btn_none, Qt.Key.Key_Tab)
    assert app.focusWidget() is picker._btn_invert
    QTest.keyClick(picker._btn_invert, Qt.Key.Key_Tab)
    assert app.focusWidget() is picker._checkboxes[0]
    for idx, checkbox in enumerate(picker._checkboxes):
        assert app.focusWidget() is checkbox
        settle(app)
        assert_focused_visible(picker, checkbox)
        assert checkbox.accessibleDescription().startswith(f"Select frame {idx + 1}")
        if idx in (0, 12, 23):
            QTest.keyClick(checkbox, Qt.Key.Key_Space)
        if idx != 23:
            QTest.keyClick(checkbox, Qt.Key.Key_Tab)
    assert picker.selected_indices() == [0, 12, 23]
    assert picker._sel_lbl.text() == "3 / 24 selected"
    assert ok.isEnabled()
    QTest.keyClick(picker._checkboxes[-1], Qt.Key.Key_Backtab)
    assert app.focusWidget() is picker._checkboxes[-2]
    QTest.keyClick(picker._checkboxes[-2], Qt.Key.Key_Left)
    settle(app)
    assert app.focusWidget() is picker._checkboxes[-3]
    assert_focused_visible(picker, picker._checkboxes[-3])
    assert picker.selected_indices() == [0, 12, 23]
    assert picker.result() != QDialog.DialogCode.Accepted
    picker.accept()
    assert picker.result() == QDialog.DialogCode.Accepted
