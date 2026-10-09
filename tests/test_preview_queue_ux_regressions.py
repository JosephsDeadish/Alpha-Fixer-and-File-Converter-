import threading
from unittest.mock import Mock, patch

import pytest
import numpy as np
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QAction, QImage, QColor, QPalette
from PyQt6.QtWidgets import QApplication, QMenu, QLabel

from src.core.presets import PresetManager
from src.core.settings_manager import SettingsManager
from src.ui.alpha_tool import AlphaFixerTab, _AlphaPreviewLoader
from src.ui.drop_list import DropFileList
from src.ui.selective_alpha_tool import SelectiveAlphaTool
from src.ui.theme_engine import PRESET_THEMES, build_stylesheet


class PreviewLoader(QObject):
    preview_ready = pyqtSignal(QImage, QImage)
    stats_ready = pyqtSignal(dict, dict)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.running = False
        self.stopped = False

    def start(self):
        self.running = True

    def stop(self):
        self.stopped = True

    def isRunning(self):
        return self.running

    def finish(self):
        self.running = False
        self.finished.emit()


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def alpha(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        manager = SettingsManager()
        widget = AlphaFixerTab(PresetManager(manager), manager)
        with patch("src.ui.alpha_tool._AlphaPreviewLoader", PreviewLoader):
            yield widget
            widget._preview_debounce.stop()
            widget._stop_preview_loader()
            for loader in tuple(widget._retired_preview_loaders):
                loader.finish()
        widget.close()
        sip.delete(widget)
        manager.sync()
        sip.delete(manager._qs)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def add_sources(alpha, tmp_path):
    for index in range(2):
        path = tmp_path / f"source{index}.png"
        with Image.new("RGBA", (8, 8)) as image:
            image.save(path)
        alpha._file_list.addItem(str(path))
    alpha._file_list.setCurrentRow(0)
    alpha._preview_debounce.stop()
    alpha._update_compare()
    return alpha._preview_loader


def sample_image():
    image = QImage(8, 8, QImage.Format.Format_RGBA8888)
    image.fill(0xFFFFFFFF)
    return image


def sample_stats():
    return {"min": 0, "max": 255, "mean": 100.0, "percent_nonzero": 50.0}


def test_selection_change_invalidates_results_during_debounce(alpha, tmp_path):
    previous = add_sources(alpha, tmp_path)
    request_id, path = alpha._preview_request_id, alpha._preview_path
    previous.preview_ready.emit(sample_image(), sample_image())
    previous.stats_ready.emit(sample_stats(), sample_stats())
    assert alpha._compare.has_images()
    alpha._file_list.setCurrentRow(1)
    assert previous.stopped
    assert previous in alpha._retired_preview_loaders
    assert alpha._preview_loader is None
    assert not alpha._compare.has_images()
    assert alpha._before_stats_lbl.text() == ""
    assert alpha._preview_debounce.isActive()
    for callback, args in [
        (alpha._on_compare_ready, (sample_image(), sample_image())),
        (alpha._on_stats_ready, (sample_stats(), sample_stats())),
        (alpha._on_compare_failed, ("stale failure",)),
    ]:
        alpha._apply_preview_result(request_id, path, callback, *args)
    assert not alpha._compare.has_images()
    assert alpha._before_stats_lbl.text() == ""
    assert "stale failure" not in alpha._log.toPlainText()


def test_clear_queue_stops_loader_timer_and_clears_helper_data(alpha, tmp_path):
    previous = add_sources(alpha, tmp_path)
    request_id, path = alpha._preview_request_id, alpha._preview_path
    alpha._atlas_cells = [(0, 0, 4, 4)]
    alpha._preview_debounce.start()
    alpha._file_list.clear()
    assert previous.stopped
    assert alpha._preview_path is None
    assert not alpha._preview_debounce.isActive()
    assert alpha._atlas_cells == []
    assert not alpha._compare.has_images()
    alpha._apply_preview_result(request_id, path, alpha._on_compare_ready,
                                sample_image(), sample_image())
    assert not alpha._compare.has_images()
    previous.finish()
    assert previous not in alpha._retired_preview_loaders


def test_same_source_reload_ignores_old_parameter_results(alpha, tmp_path):
    previous = add_sources(alpha, tmp_path)
    request_id, path = alpha._preview_request_id, alpha._preview_path
    alpha._update_compare()
    current = alpha._preview_loader
    assert current is not previous
    stale = Mock()
    alpha._apply_preview_result(request_id, path, stale, sample_stats())
    stale.assert_not_called()
    current.preview_ready.emit(sample_image(), sample_image())
    current.stats_ready.emit(sample_stats(), sample_stats())
    assert alpha._compare.has_images()
    assert "100.0" in alpha._before_stats_lbl.text()
    current.failed.emit("current failure")
    assert not alpha._compare.has_images()
    assert "current failure" in alpha._log.toPlainText()


def test_real_inflight_loader_survives_selection_change_until_finished(alpha, tmp_path, app):
    entered = threading.Event()
    release = threading.Event()
    source = tmp_path / "blocked.png"
    alpha._file_list.addItem(str(source))
    alpha._preview_path = str(source)

    def delayed_load(path):
        entered.set()
        assert release.wait(5)
        return Image.new("RGBA", (8, 8), (30, 40, 50, 255))

    with patch("src.core.alpha_processor.load_image", delayed_load):
        with patch("src.ui.alpha_tool._AlphaPreviewLoader", _AlphaPreviewLoader):
            alpha._update_compare()
            loader = alpha._preview_loader
            try:
                assert entered.wait(5)
                alpha._on_selection_changed(-1)
                assert loader in alpha._retired_preview_loaders
                assert loader.isRunning()
                assert alpha._preview_loader is None
            finally:
                release.set()
                assert loader.wait(5000)
            app.processEvents()
    assert loader not in alpha._retired_preview_loaders
    assert not alpha._compare.has_images()
    assert alpha._before_stats_lbl.text() == ""


@pytest.fixture
def queue(app):
    widget = DropFileList()
    yield widget
    widget.clear()
    sip.delete(widget)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("method", ["clear", "_clear_all"])
def test_queue_clear_has_identical_notifications_and_cancellation(queue, method):
    queue.addItem("/tmp/source.png")
    count = Mock()
    cleared = Mock()
    queue.count_changed.connect(count)
    queue.list_cleared.connect(cleared)
    old_cancel = queue._cancel_event
    queue._thumb_cache["/tmp/source.png"] = object()
    queue._pending.add("/tmp/source.png")
    getattr(queue, method)()
    count.assert_called_once_with(0)
    cleared.assert_called_once_with()
    assert old_cancel.is_set()
    assert not queue._cancel_event.is_set()
    assert not queue._thumb_cache
    assert not queue._pending
    getattr(queue, method)()
    cleared.assert_called_once_with()


def test_context_menu_actions_are_released_between_openings(queue):
    queue.addItem("/tmp/source.png")
    queue.setCurrentRow(0)
    baseline = len(queue.findChildren(QAction))
    with patch.object(QMenu, "exec", return_value=None):
        for _ in range(20):
            queue._show_context_menu(QPoint(0, 0))
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert queue.findChildren(QMenu) == []
    assert len(queue.findChildren(QAction)) == baseline


@pytest.fixture
def painter(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "painter-settings.ini")):
        manager = SettingsManager()
        widget = SelectiveAlphaTool(manager)
        yield widget
        widget.close()
        sip.delete(widget)
        manager.sync()
        sip.delete(manager._qs)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def open_painter_image(painter, tmp_path, size=(8, 8)):
    path = tmp_path / f"painter-{size[0]}-{size[1]}.png"
    with Image.new("RGBA", size, (30, 40, 50, 255)) as image:
        image.save(path)
    with patch("src.ui.selective_alpha_tool.QFileDialog.getOpenFileName",
               return_value=(str(path), "")):
        painter._on_open()
    return path


def test_painter_named_inputs_keep_zone_switching_and_keyboard_edits(painter, app, tmp_path):
    open_painter_image(painter, tmp_path)
    controls = [painter._brush_spin, painter._eraser_spin, painter._overlay_opacity_slider,
                painter._active_zone_combo, painter._ze_alpha_spin,
                painter._slot_combo, painter._az_slot_combo]
    names = [control.accessibleName() for control in controls]
    assert all(names) and len(set(names)) == len(names)
    labels = painter.findChildren(QLabel)
    for control in controls:
        assert sum(label.buddy() is control for label in labels) == 1
    assert painter._ze_name_edit.accessibleName()
    assert painter._ze_swatch_btn.accessibleName()
    actions = [painter._btn_slot_add, painter._btn_slot_del, painter._btn_slot_save,
               painter._btn_slot_paste, painter._btn_slot_rename, painter._btn_slot_clear,
               painter._btn_az_slot_add, painter._btn_az_slot_del, painter._btn_copy_all_zones,
               painter._btn_paste_all_zones, painter._btn_az_slot_rename, painter._btn_az_slot_clear]
    action_names = [action.accessibleName() for action in actions]
    assert all(action_names) and len(set(action_names)) == len(action_names)
    assert all(action.accessibleDescription() for action in actions)
    assert not painter._btn_slot_paste.isEnabled()
    assert not painter._btn_paste_all_zones.isEnabled()
    assert "0" in painter._ze_alpha_spin.accessibleDescription()
    painter.show()
    painter.activateWindow()
    app.processEvents()
    painter._active_zone_combo.setCurrentIndex(1)
    assert painter._ze_cur_idx == 1
    before = painter._ze_alpha_spin.value()
    painter._ze_alpha_spin.setFocus()
    QTest.keyClick(painter._ze_alpha_spin, Qt.Key.Key_Up)
    assert painter._ze_alpha_spin.value() == before + 1
    assert painter._canvas._zone_alphas[1] == before + 1
    painter._active_zone_combo.setCurrentIndex(0)
    assert painter._canvas._zone_alphas[0] == before
    painter._active_zone_combo.setCurrentIndex(1)
    assert painter._ze_alpha_spin.value() == before + 1
    assert [control.accessibleName() for control in controls] == names
    painter._ze_alpha_spin.setEnabled(False)
    QTest.keyClick(painter._ze_alpha_spin, Qt.Key.Key_Up)
    assert painter._canvas._zone_alphas[1] == before + 1


def test_painter_guidance_preserves_slot_states_across_themes_and_scales(painter, app, tmp_path):
    open_painter_image(painter, tmp_path)
    labels = [label for label in painter.findChildren(QLabel) if label.property("toolGuidance")]
    assert len(labels) == 10
    capability = painter._capability_lbl
    capability_text = capability.text(), capability.toolTip()
    painter.show()
    for name, pixels in [("Panda Dark", 13), ("Panda Light", 24), ("Panda Dark", 18)]:
        theme = PRESET_THEMES[name]
        painter.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
        painter._mask_slots[0] = np.ones((8, 8), dtype=np.uint8)
        painter._mask_slot_info[0] = "Zone 1"
        painter._az_slots[0] = [np.ones((8, 8), dtype=np.uint8)]
        painter._az_slot_info[0] = "Full layout"
        painter._on_slot_selected(0)
        painter._on_az_slot_selected(0)
        assert painter._btn_slot_paste.isEnabled()
        assert painter._btn_paste_all_zones.isEnabled()
        filled_text = painter._slot_info_lbl.text(), painter._az_slot_info_lbl.text()
        painter.receive_shared_zones([(128, np.ones((8, 8), dtype=bool))])
        painter._refresh_session_status()
        app.processEvents()
        for label in labels + [capability]:
            assert not label.styleSheet()
            assert label.wordWrap() or label is painter._coord_lbl
            assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(theme["text"])
            assert label.font().pixelSize() == pixels
        assert capability.palette().color(QPalette.ColorRole.Window) == QColor(theme["surface"])
        assert (capability.text(), capability.toolTip()) == capability_text
        assert "ready" in painter._import_shared_status.text()
        assert painter._btn_import_shared.isEnabled()
        painter._mask_slots[0] = None
        painter._az_slots[0] = None
        painter._on_slot_selected(0)
        painter._on_az_slot_selected(0)
        assert not painter._btn_slot_paste.isEnabled()
        assert not painter._btn_paste_all_zones.isEnabled()
        assert (painter._slot_info_lbl.text(), painter._az_slot_info_lbl.text()) != filled_text
        assert not painter._slot_info_lbl.styleSheet()
        assert not painter._az_slot_info_lbl.styleSheet()
        painter._canvas.cursor_moved.emit(123, 456)
        assert painter._coord_lbl.text() == "x:123  y:456"
        assert painter._status_lbl.text()
        assert painter._status_lbl.wordWrap()


def menu_state(canvas):
    observed = {}

    def inspect(menu, *args):
        observed.update({action.text(): action.isEnabled()
                         for action in menu.actions() if not action.isSeparator()})
        return None

    with patch.object(QMenu, "exec", inspect):
        canvas._show_context_menu(QPoint(0, 0))
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert canvas.findChildren(QMenu) == []
    return observed


def test_shared_clipboard_enables_regular_paste_and_resizes_across_images(painter, tmp_path):
    mask = np.zeros((4, 4), dtype=bool)
    mask[:2, :2] = True
    painter.receive_shared_zones([(120, mask)])
    assert not painter._ze_paste_btn.isEnabled()
    assert not any(menu_state(painter._canvas).values())
    open_painter_image(painter, tmp_path)
    assert painter._ze_paste_btn.isEnabled()
    assert menu_state(painter._canvas)["Paste Zone Mask"]
    painter._on_ze_paste_mask()
    pasted = painter._canvas.get_mask_as_array(painter._ze_cur_idx)
    assert pasted.shape == (8, 8)
    assert np.all(pasted[:4, :4] == 255)
    assert np.all(pasted[4:, :] == 0)
    open_painter_image(painter, tmp_path, (12, 12))
    painter._on_ze_paste_mask()
    pasted = painter._canvas.get_mask_as_array(painter._ze_cur_idx)
    assert pasted.shape == (12, 12)
    assert np.all(pasted[:6, :6] == 255)


def test_full_layout_paste_availability_is_independent_of_single_clipboard(painter, tmp_path):
    open_painter_image(painter, tmp_path)
    painter._canvas.set_mask_from_array(0, np.ones((8, 8), dtype=np.uint8))
    painter._on_copy_all_zones()
    assert painter._mask_clipboard is None
    states = menu_state(painter._canvas)
    assert states["Paste All Zones"]
    assert not states["Paste Zone Mask"]
    painter._canvas.clear_mask(0)

    def choose_paste(menu, *args):
        return next(action for action in menu.actions() if action.text() == "Paste All Zones")

    with patch.object(QMenu, "exec", choose_paste):
        painter._canvas._show_context_menu(QPoint(0, 0))
    assert np.all(painter._canvas.get_mask_as_array(0) == 1)
    painter._on_az_slot_clear()
    assert not menu_state(painter._canvas)["Paste All Zones"]
    painter._on_copy_mask(0)
    states = menu_state(painter._canvas)
    assert states["Paste Zone Mask"]
    assert not states["Paste All Zones"]


def test_failed_painter_load_preserves_existing_edits_and_save_target(painter, tmp_path):
    path = open_painter_image(painter, tmp_path)
    painter._canvas.set_mask_from_array(0, np.ones((8, 8), dtype=np.uint8))
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with patch("src.ui.selective_alpha_tool.QFileDialog.getOpenFileName",
               return_value=(str(bad), "")):
        with patch("src.ui.selective_alpha_tool.QMessageBox.warning") as warning:
            painter._on_open()
    warning.assert_called_once()
    assert painter._src_path == str(path)
    assert painter._btn_save.isEnabled()
    assert np.all(painter._canvas.get_mask_as_array(0) == 1)
