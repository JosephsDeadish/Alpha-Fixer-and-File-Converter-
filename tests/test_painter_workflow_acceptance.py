from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.settings_manager import SettingsManager
from src.ui.selective_alpha_tool import NUM_ZONES, SelectiveAlphaTool


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def painter(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        widget = SelectiveAlphaTool(settings)
        widget._offer_delete_original = Mock()
        yield widget
        widget.close()
        sip.delete(widget)
        settings.sync()
        sip.delete(settings._qs)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def open_image(painter, tmp_path, size=(32, 24), name="source.png"):
    path = tmp_path / name
    pixels = np.empty((size[1], size[0], 4), dtype=np.uint8)
    pixels[:] = (30, 60, 90, 255)
    pixels[:, size[0] // 2:, :3] = (120, 150, 180)
    with Image.fromarray(pixels) as image:
        image.save(path)
    with patch("src.ui.selective_alpha_tool.QFileDialog.getOpenFileName",
               return_value=(str(path), "")):
        painter._btn_open.click()
    assert painter._canvas.has_image()
    return path, pixels


def draw(painter, app, tool, start, end):
    painter._tool_btns[tool].click()
    canvas = painter._canvas

    def point(xy):
        return QPoint(round(canvas._pan_x + (xy[0] + 0.25) * canvas._zoom),
                      round(canvas._pan_y + (xy[1] + 0.25) * canvas._zoom))

    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point(start))
    QTest.mouseMove(canvas, point(end))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point(end))
    app.processEvents()


def assert_masks_equal(left, right):
    assert len(left) == len(right)
    for before, after in zip(left, right):
        np.testing.assert_array_equal(before, after)


@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
def test_compact_controls_scroll_and_resize_preserve_canvas_and_history(painter, app, tmp_path, pixels, size):
    open_image(painter, tmp_path)
    canvas = painter._canvas
    canvas.set_mask_from_array(0, np.ones((24, 32), dtype=np.uint8))
    canvas.set_mask_from_array(1, np.ones((24, 32), dtype=np.uint8))
    canvas.undo_mask()
    masks = canvas.get_all_masks()
    steps = len(canvas._undo_stack), len(canvas._redo_stack)
    painter.setStyleSheet(f"QWidget {{ font-size: {pixels}px; }}")
    painter.resize(*size)
    painter.show()
    app.processEvents()
    scroll = painter._controls_scroll
    assert (painter.width(), painter.height()) == size
    assert 100 <= scroll.width() < painter.width() // 2
    assert canvas.width() >= 250 and canvas.height() >= 200
    assert scroll.horizontalScrollBar().maximum() > 0
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.accessibleName() == "Alpha Painter controls"
    assert painter._ze_alpha_spin.accessibleName() == "Active zone alpha (0–255)"
    assert painter._ze_alpha_spin.font().pixelSize() == pixels

    # Real Tab traversal must reveal off-screen numeric editors and bottom slots.
    painter._btn_open.setFocus()
    app.processEvents()
    seen = set()
    for _ in range(150):
        focused = app.focusWidget()
        if focused in (painter._ze_alpha_spin, painter._slot_combo,
                       painter._btn_az_slot_clear):
            rect = QRect(focused.mapTo(scroll.viewport(), QPoint()), focused.size())
            assert scroll.viewport().rect().intersects(rect)
            if focused.width() <= scroll.viewport().width():
                assert scroll.viewport().rect().contains(rect)
            seen.add(focused)
        if len(seen) == 3:
            break
        QTest.keyClick(focused, Qt.Key.Key_Tab)
        app.processEvents()
    assert seen == {painter._ze_alpha_spin, painter._slot_combo, painter._btn_az_slot_clear}
    assert scroll.verticalScrollBar().value() > 0
    painter._btn_slot_add.setFocus()
    app.processEvents()
    slot_count = painter._slot_combo.count()
    QTest.keyClick(painter._btn_slot_add, Qt.Key.Key_Space)
    app.processEvents()
    assert painter._slot_combo.count() == slot_count + 1

    # Widening and shrinking must not rebuild controls or discard undo/redo.
    painter.resize(1100, 800)
    app.processEvents()
    assert scroll.width() == 360
    painter.resize(*size)
    app.processEvents()
    app.processEvents()
    assert (painter.width(), painter.height()) == size
    focused_rect = QRect(painter._btn_slot_add.mapTo(scroll.viewport(), QPoint()),
                         painter._btn_slot_add.size())
    assert scroll.viewport().rect().intersects(focused_rect)
    assert painter._canvas is canvas
    assert_masks_equal(canvas.get_all_masks(), masks)
    assert (len(canvas._undo_stack), len(canvas._redo_stack)) == steps


def test_real_draw_edit_clipboards_slots_save_reopen(painter, app, tmp_path):
    source, pixels = open_image(painter, tmp_path)
    painter.resize(1100, 800)
    painter.show()
    app.processEvents()
    canvas = painter._canvas
    assert not painter._btn_show_highlights.isChecked()
    assert not painter._history_overlay.labels_checked()
    assert not any(canvas._zone_visible)
    painter._ze_alpha_spin.setValue(64)
    draw(painter, app, "rect", (2, 2), (10, 10))
    first = canvas.get_mask_as_array(0)
    assert first[5, 5] and not first[0, 0]
    painter._ze_copy_btn.click()
    painter._btn_slot_save.click()
    painter._active_zone_combo.setCurrentIndex(1)
    painter._ze_alpha_spin.setValue(0)
    painter._ze_paste_btn.click()
    np.testing.assert_array_equal(canvas.get_mask_as_array(1), first)
    painter._eraser_spin.setValue(1)
    draw(painter, app, "eraser", (5, 5), (5, 5))
    assert not canvas._masks[1][5, 5]
    draw(painter, app, "rect", (18, 2), (24, 8))
    painter._active_zone_combo.setCurrentIndex(2)
    painter._ze_alpha_spin.setValue(128)
    painter._btn_slot_paste.click()
    np.testing.assert_array_equal(canvas.get_mask_as_array(2), first)
    painter._btn_copy_all_zones.click()
    layout = canvas.get_all_masks()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        painter._btn_clear_all.click()
    assert not any(mask.any() for mask in canvas.get_all_masks())
    painter._btn_paste_all_zones.click()
    assert_masks_equal(canvas.get_all_masks(), layout)
    painter._history_overlay._btn_undo.click()
    assert not any(mask.any() for mask in canvas.get_all_masks())
    painter._history_overlay._btn_redo.click()
    assert_masks_equal(canvas.get_all_masks(), layout)
    painter._ze_clear_btn.click()
    expected = pixels.copy()
    expected[canvas._masks[1].astype(bool), 3] = 0
    expected[first.astype(bool), 3] = 64
    output = tmp_path / "painted.png"
    with patch("src.ui.selective_alpha_tool.QFileDialog.getSaveFileName",
               return_value=(str(output), "PNG (*.png)")):
        painter._btn_save.click()
    with Image.open(output) as image:
        np.testing.assert_array_equal(np.array(image), expected)
    assert source.exists()
    assert painter._settings.get_selective_alpha_history()[-1]["output"] == str(output)
    with patch("src.ui.selective_alpha_tool.QFileDialog.getOpenFileName",
               return_value=(str(output), "")):
        painter._btn_open.click()
    assert painter._result_img is None
    assert painter._on_apply()
    np.testing.assert_array_equal(np.array(painter._result_img), expected)
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("tool", ["freehand", "line", "rect", "ellipse", "fill", "polygon", "transform"])
def test_drawing_tools_are_single_mask_history_steps(painter, app, tmp_path, tool):
    open_image(painter, tmp_path)
    painter.resize(1100, 800)
    painter.show()
    app.processEvents()
    canvas = painter._canvas
    painter._brush_spin.setValue(1)
    if tool == "transform":
        draw(painter, app, "rect", (2, 2), (6, 6))
    before = canvas.get_all_masks()
    steps = len(canvas._undo_stack)
    if tool == "polygon":
        for point in [(3, 3), (12, 3), (8, 12)]:
            draw(painter, app, tool, point, point)
        painter._on_close_polygon()
    else:
        draw(painter, app, tool, (4, 4), (12, 12))
    after = canvas.get_all_masks()
    assert not np.array_equal(before[0], after[0])
    assert len(canvas._undo_stack) == steps + 1
    canvas.undo_mask()
    assert_masks_equal(canvas.get_all_masks(), before)
    canvas.redo_mask()
    assert_masks_equal(canvas.get_all_masks(), after)


def test_shared_full_layout_clears_unrepresented_zones_and_resizes_atomically(painter, tmp_path):
    open_image(painter, tmp_path, (8, 6))
    canvas = painter._canvas
    old = np.ones((6, 8), dtype=np.uint8)
    canvas.set_mask_from_array(3, old)
    mask = np.zeros((3, 4), dtype=bool)
    mask[:2, :2] = True
    painter.receive_shared_zones([(100, mask)])
    painter._on_import_to_az_slot()
    before = canvas.get_all_masks()
    painter._btn_paste_all_zones.click()
    assert np.all(canvas._masks[0][:4, :4] == 255)
    assert not canvas._masks[0][4:, :].any()
    assert not any(m.any() for m in canvas._masks[1:])
    canvas.undo_mask()
    assert_masks_equal(canvas.get_all_masks(), before)
    canvas.redo_mask()
    assert not canvas._masks[3].any()


@pytest.mark.parametrize("edit", ["paint", "alpha", "undo", "redo", "clear"])
def test_edits_invalidate_applied_result_and_guidance(painter, tmp_path, edit):
    open_image(painter, tmp_path)
    canvas = painter._canvas
    mask = np.ones((24, 32), dtype=np.uint8)
    canvas.set_mask_from_array(0, mask)
    canvas.set_mask_from_array(1, mask)
    if edit == "redo":
        canvas.undo_mask()
    assert painter._on_apply()
    cached = painter._result_img
    assert "result ready to save" in painter.get_status_bar_text()
    if edit == "paint":
        canvas.set_mask_from_array(2, mask)
    elif edit == "alpha":
        painter._ze_alpha_spin.setValue(42)
    elif edit == "undo":
        canvas.undo_mask()
    elif edit == "redo":
        canvas.redo_mask()
    else:
        canvas.clear_all_masks()
    assert painter._result_img is None
    assert "result ready to save" not in painter._session_status_lbl.text()
    with pytest.raises(ValueError):
        cached.getpixel((0, 0))


@pytest.mark.parametrize("invalid", [
    np.zeros((2, 2, 4), dtype=np.uint8), np.zeros((0, 2), dtype=np.uint8),
    np.array([["bad"]]), np.array([[float("nan")]]),
])
@pytest.mark.parametrize("layout", [False, True])
def test_invalid_paste_preserves_masks_history_result_and_redo(painter, tmp_path, invalid, layout):
    open_image(painter, tmp_path)
    canvas = painter._canvas
    canvas.set_mask_from_array(0, np.ones((24, 32), dtype=np.uint8))
    canvas.set_mask_from_array(1, np.ones((24, 32), dtype=np.uint8))
    canvas.undo_mask()
    assert painter._on_apply()
    result = painter._result_img
    before = canvas.get_all_masks()
    steps = len(canvas._undo_stack), len(canvas._redo_stack)
    with patch("src.ui.selective_alpha_tool.QMessageBox.warning") as warning:
        if layout:
            painter._az_slots[0] = [np.ones((2, 2), dtype=np.uint8), invalid] + [None] * (NUM_ZONES - 2)
            painter._on_paste_all_zones()
        else:
            painter._mask_clipboard = invalid
            painter._on_paste_mask(0)
    warning.assert_called_once()
    assert_masks_equal(canvas.get_all_masks(), before)
    assert (len(canvas._undo_stack), len(canvas._redo_stack)) == steps
    assert painter._result_img is result


@pytest.mark.parametrize("tool", ["freehand", "line"])
def test_autocorrect_reaches_real_image_edges_and_undo_reverts(painter, app, tmp_path, tool):
    open_image(painter, tmp_path)
    painter.resize(1100, 800)
    painter.show()
    app.processEvents()
    painter._brush_spin.setValue(1)
    painter._autocorrect_chk.setChecked(True)
    draw(painter, app, tool, (10, 8), (10, 12))
    mask = painter._canvas.get_mask_as_array(0)
    assert mask[10, 10]
    assert mask[10, 15] and mask[10, 16]
    painter._canvas.undo_mask()
    assert painter._canvas.get_mask_as_array(0) is None
    painter._canvas.redo_mask()
    np.testing.assert_array_equal(painter._canvas.get_mask_as_array(0), mask)


def test_fill_adds_region_without_erasing_other_strokes(painter, app, tmp_path):
    open_image(painter, tmp_path)
    painter.resize(1100, 800)
    painter.show()
    app.processEvents()
    draw(painter, app, "rect", (20, 2), (25, 5))
    before = painter._canvas.get_mask_as_array(0)
    draw(painter, app, "fill", (4, 10), (4, 10))
    mask = painter._canvas.get_mask_as_array(0)
    assert mask[10, 4]
    assert mask[3, 22]
    assert not mask[10, 22]
    painter._canvas.undo_mask()
    np.testing.assert_array_equal(painter._canvas.get_mask_as_array(0), before)


@pytest.mark.parametrize("storage", ["clipboard", "single_slot", "full_layout"])
def test_mask_storage_survives_image_change_and_detaches_from_edits(painter, tmp_path, storage):
    open_image(painter, tmp_path, (8, 6))
    mask = np.zeros((6, 8), dtype=np.uint8)
    mask[:3, :4] = 1
    painter._canvas.set_mask_from_array(0, mask)
    if storage == "clipboard":
        painter._ze_copy_btn.click()
    elif storage == "single_slot":
        painter._btn_slot_save.click()
    else:
        painter._btn_copy_all_zones.click()
    painter._canvas.clear_all_masks()
    open_image(painter, tmp_path, (16, 12), "second.png")
    if storage == "clipboard":
        painter._ze_paste_btn.click()
    elif storage == "single_slot":
        painter._btn_slot_paste.click()
    else:
        painter._btn_paste_all_zones.click()
    resized = np.zeros((12, 16), dtype=np.uint8)
    resized[:6, :8] = 1
    np.testing.assert_array_equal(painter._canvas.get_mask_as_array(0), resized)
    assert len(painter._canvas._undo_stack) == 1
    painter._canvas.undo_mask()
    assert painter._canvas.get_mask_as_array(0) is None


def test_shared_masks_are_detached_and_invalid_receive_preserves_clipboard(painter, tmp_path):
    open_image(painter, tmp_path)
    mask = np.ones((24, 32), dtype=bool)
    painter.receive_shared_zones([(73, mask)])
    mask.fill(False)
    assert painter._shared_zones[0][1].all()
    clipboard = painter._mask_clipboard.copy()
    with patch.object(QMessageBox, "warning") as warning:
        painter.receive_shared_zones([(73, np.zeros((0, 2), dtype=bool))])
    warning.assert_called_once()
    np.testing.assert_array_equal(painter._mask_clipboard, clipboard)
    painter._btn_import_shared.click()
    assert painter._ze_alpha_spin.value() == 73
    assert painter._on_apply()
    assert np.all(np.array(painter._result_img)[:, :, 3] == 73)


def test_cancelled_save_preserves_disk_history_and_masks(painter, tmp_path):
    source, pixels = open_image(painter, tmp_path)
    painter._canvas.set_mask_from_array(0, np.ones((24, 32), dtype=np.uint8))
    painter._ze_alpha_spin.setValue(0)
    before = painter._canvas.get_all_masks()
    with patch("src.ui.selective_alpha_tool.QFileDialog.getSaveFileName",
               return_value=("", "")):
        painter._btn_save.click()
    assert_masks_equal(painter._canvas.get_all_masks(), before)
    assert not painter._settings.get_selective_alpha_history()
    painter._offer_delete_original.assert_not_called()
    with Image.open(source) as image:
        np.testing.assert_array_equal(np.array(image), pixels)


def test_shared_import_saves_alphas_without_requiring_another_edit(painter, tmp_path):
    open_image(painter, tmp_path, (8, 6))
    painter._active_zone_combo.setCurrentIndex(1)
    painter._ze_alpha_spin.setValue(42)
    left = np.zeros((6, 8), dtype=bool)
    left[:, :4] = True
    painter.receive_shared_zones([(73, left), (196, ~left)])
    painter._btn_import_shared.click()
    assert painter._ze_cur_idx == painter._canvas.get_active_zone() == 1
    assert painter._ze_alpha_spin.value() == 196
    assert painter._canvas._zone_alphas[:2] == [73, 196]
    painter._settings.sync()
    reopened = SettingsManager()
    try:
        assert reopened.get_sa_zone_alphas()[:2] == [73, 196]
    finally:
        sip.delete(reopened._qs)
    painter._active_zone_combo.setCurrentIndex(0)
    assert painter._ze_alpha_spin.value() == 73
    assert painter._on_apply()
    expected = np.where(left, 73, 196).astype(np.uint8)
    np.testing.assert_array_equal(np.array(painter._result_img)[:, :, 3], expected)
    backup = tmp_path / "painter-settings.json"
    painter._settings.export_settings(str(backup))
    painter._settings.reset_all()
    assert painter._settings.get_sa_zone_alphas() == [128] * painter._active_zone_combo.count()
    painter._settings.import_settings(str(backup))
    reloaded_painter = SelectiveAlphaTool(painter._settings)
    try:
        assert reloaded_painter._canvas._zone_alphas[:2] == [73, 196]
        reloaded_painter._active_zone_combo.setCurrentIndex(1)
        assert reloaded_painter._ze_alpha_spin.value() == 196
    finally:
        reloaded_painter.close()
        sip.delete(reloaded_painter)


@pytest.mark.parametrize("clear", ["zone", "all"])
def test_clearing_empty_masks_preserves_redo_and_restored_pixels(painter, tmp_path, clear):
    _, pixels = open_image(painter, tmp_path, (8, 6))
    painter._ze_alpha_spin.setValue(42)
    painter._canvas.set_mask_from_array(0, np.ones((6, 8), dtype=np.uint8))
    painter._history_overlay._btn_undo.click()
    assert not painter._canvas._undo_stack
    assert len(painter._canvas._redo_stack) == 1
    if clear == "zone":
        painter._ze_clear_btn.click()
    else:
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            painter._btn_clear_all.click()
    assert not painter._canvas._undo_stack
    assert len(painter._canvas._redo_stack) == 1
    assert not painter._history_overlay._btn_undo.isEnabled()
    assert painter._history_overlay._btn_redo.isEnabled()
    painter._history_overlay._btn_redo.click()
    assert painter._on_apply()
    expected = pixels.copy()
    expected[:, :, 3] = 42
    np.testing.assert_array_equal(np.array(painter._result_img), expected)


def test_clearing_empty_selected_zone_keeps_other_zone_result_and_history(painter, tmp_path):
    _, pixels = open_image(painter, tmp_path, (8, 6))
    painter._ze_alpha_spin.setValue(42)
    painter._canvas.set_mask_from_array(0, np.ones((6, 8), dtype=np.uint8))
    assert painter._on_apply()
    result = painter._result_img
    painter._active_zone_combo.setCurrentIndex(1)
    painter._ze_clear_btn.click()
    assert painter._result_img is result
    assert len(painter._canvas._undo_stack) == 1
    assert not painter._canvas._redo_stack
    assert painter._ze_cur_idx == painter._canvas.get_active_zone() == 1
    assert painter._ze_alpha_spin.value() == 128
    expected = pixels.copy()
    expected[:, :, 3] = 42
    np.testing.assert_array_equal(np.array(result), expected)
