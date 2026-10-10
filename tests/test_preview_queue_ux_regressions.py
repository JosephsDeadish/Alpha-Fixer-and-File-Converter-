import threading
from unittest.mock import Mock, patch

import pytest
import numpy as np
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QAction, QImage, QColor, QPalette
from PyQt6.QtWidgets import QApplication, QMenu, QLabel, QWidget

from src.core.presets import PresetManager
from src.core.settings_manager import SettingsManager
from src.ui.alpha_tool import AlphaFixerTab, _AlphaPreviewLoader
from src.ui.drop_list import DropFileList
from src.ui.selective_alpha_tool import (
    SelectiveAlphaTool, _FloatingZoomOverlay, _FloatingHistoryOverlay,
)
from src.ui.theme_engine import PRESET_THEMES, HIDDEN_THEMES, build_stylesheet


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


@pytest.mark.parametrize("name", list(PRESET_THEMES) + list(HIDDEN_THEMES))
def test_painter_history_overlay_scales_counts_and_preserves_actions(app, name):
    host = QWidget()
    undo, redo = Mock(), Mock()
    overlay = _FloatingHistoryOverlay(undo, redo, host)
    zoom = _FloatingZoomOverlay(Mock(), Mock(), Mock(), host)
    theme = {**PRESET_THEMES, **HIDDEN_THEMES}[name]
    highlight, labels, zones = Mock(), Mock(), Mock()
    overlay.highlight_toggled.connect(highlight)
    overlay.labels_toggled.connect(labels)
    overlay.all_zones_toggled.connect(zones)
    try:
        host.resize(640, 500)
        host.show()
        overlay.show()
        zoom.show()
        controls = [overlay._btn_undo, overlay._btn_redo, overlay._btn_all_vis,
                    overlay._chk_highlight, overlay._chk_labels]
        assert len({control.accessibleName() for control in controls}) == 5
        for pixels in [13, 24, 32, 13]:
            host.setStyleSheet(build_stylesheet(theme)
                              + f"\nQWidget {{ font-size: {pixels}px; }}")
            for count in [0, 1, 123, 9999, 0]:
                overlay.set_undo_count(count)
                overlay.set_redo_count(count)
                for _ in range(15):
                    app.processEvents()
                assert not overlay._position_timer.isActive()
                assert not zoom._position_timer.isActive()
                assert host.rect().contains(overlay.geometry())
                assert host.rect().contains(zoom.geometry())
                assert not overlay.geometry().intersects(zoom.geometry())
                for control in controls:
                    assert not control.styleSheet()
                    assert control.font().pixelSize() == pixels
                    assert control.width() >= control.sizeHint().width()
                    assert control.height() >= control.sizeHint().height()
                for button in (overlay._btn_undo, overlay._btn_redo):
                    assert button.isEnabled() == (count > 0)
                    assert button.accessibleDescription() == button.toolTip()
                    if count:
                        assert str(count) in button.text()
                        assert button.palette().color(QPalette.ColorRole.ButtonText) == QColor(theme["text"])
        overlay.set_highlight_checked(True)
        overlay.set_labels_checked(False)
        overlay.set_all_zones_visible(False)
        highlight.assert_not_called()
        labels.assert_not_called()
        zones.assert_not_called()
        host.activateWindow()
        for button, callback in [(overlay._btn_undo, undo), (overlay._btn_redo, redo)]:
            button.setFocus()
            app.processEvents()
            QTest.keyClick(button, Qt.Key.Key_Space)
            callback.assert_not_called()
        overlay.set_undo_count(1)
        overlay.set_redo_count(1)
        app.processEvents()
        QTest.mouseMove(host, host.rect().bottomRight())
        for control in controls:
            other = overlay._btn_undo if control is not overlay._btn_undo else overlay._btn_redo
            other.setFocus()
            app.processEvents()
            geometry, hint = control.geometry(), control.sizeHint()
            image = control.grab().toImage()
            control.setFocus(Qt.FocusReason.TabFocusReason)
            app.processEvents()
            assert control.hasFocus()
            assert control.geometry() == geometry
            assert control.sizeHint() == hint
            assert control.grab().toImage() != image, name
        for button, callback in [(overlay._btn_undo, undo), (overlay._btn_redo, redo)]:
            button.setFocus()
            app.processEvents()
            QTest.keyClick(button, Qt.Key.Key_Space)
            callback.assert_called_once()
        QTest.keyClick(overlay._chk_highlight, Qt.Key.Key_Space)
        highlight.assert_called_once_with(False)
        QTest.keyClick(overlay._chk_labels, Qt.Key.Key_Space)
        labels.assert_called_once_with(True)
        QTest.keyClick(overlay._btn_all_vis, Qt.Key.Key_Space)
        zones.assert_called_once_with(True)
        assert overlay._btn_all_vis.text() == "👁  Hide All Zones"
        host.setStyleSheet(build_stylesheet(theme) + "\nQWidget { font-size: 32px; }")
        host.resize(200, 600)
        overlay.set_undo_count(9999)
        overlay.set_redo_count(9999)
        for _ in range(20):
            app.processEvents()
        assert host.rect().contains(overlay.geometry())
        assert host.rect().contains(zoom.geometry())
        assert not overlay.geometry().intersects(zoom.geometry())
        assert not overlay._position_timer.isActive()
        assert overlay._chk_highlight.text() == "α=0"
        assert overlay._btn_all_vis.text() == "👁"
        assert all(control.accessibleName() for control in controls)
        for control in controls:
            assert control.width() >= control.sizeHint().width()
        host.resize(900, 600)
        for _ in range(20):
            app.processEvents()
        assert overlay._chk_highlight.text() == "Highlight transparent"
        assert overlay._btn_all_vis.text() == "👁  Hide All Zones"
    finally:
        host.close()
        sip.delete(host)


def test_painter_zoom_overlay_avoids_history_and_updates_actual_canvas(painter, app, tmp_path):
    open_painter_image(painter, tmp_path)
    painter.show()
    painter.setStyleSheet(build_stylesheet(PRESET_THEMES["Panda Light"])
                         + "\nQWidget { font-size: 24px; }")
    painter._canvas.setFixedSize(360, 400)
    for _ in range(15):
        app.processEvents()
    overlay = painter._zoom_overlay
    assert painter._canvas.rect().contains(overlay.geometry())
    assert not overlay.geometry().intersects(painter._history_overlay.geometry())
    overlay._zoom_buttons[1].click()
    before = overlay._zoom_lbl.text()
    overlay._zoom_buttons[2].click()
    for _ in range(15):
        app.processEvents()
    assert overlay._zoom_lbl.text() != before
    assert not overlay._position_timer.isActive()
    overlay._zoom_buttons[1].click()
    assert overlay._zoom_lbl.text() == before


def settle_painter_overlays(app, host, history, zoom=None):
    for _ in range(25):
        app.processEvents()
    assert not history._position_timer.isActive()
    assert host.rect().contains(history.geometry())
    if zoom is not None:
        assert not zoom._position_timer.isActive()
        assert host.rect().contains(zoom.geometry())
        assert not history.geometry().intersects(zoom.geometry())


def focus_visible_history_control(app, history, control):
    control.setFocus(Qt.FocusReason.TabFocusReason)
    app.processEvents()
    assert control.hasFocus()
    viewport = history._scroll_area.viewport()
    assert viewport.rect().contains(
        control.rect().translated(control.mapTo(viewport, QPoint()))
    )


@pytest.mark.parametrize("width", [200, 360])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("with_zoom", [False, True])
def test_painter_short_history_scrolls_and_restores(app, width, pixels, with_zoom):
    host = QWidget()
    undo, redo = Mock(), Mock()
    history = _FloatingHistoryOverlay(undo, redo, host)
    zoom = _FloatingZoomOverlay(Mock(), Mock(), Mock(), host) if with_zoom else None
    highlight, labels, zones = Mock(), Mock(), Mock()
    history.highlight_toggled.connect(highlight)
    history.labels_toggled.connect(labels)
    history.all_zones_toggled.connect(zones)
    controls = [history._btn_undo, history._btn_redo, history._chk_highlight,
                history._chk_labels, history._btn_all_vis]
    try:
        host.setStyleSheet(build_stylesheet(PRESET_THEMES["Panda Light"])
                          + f"\nQWidget {{ font-size: {pixels}px; }}")
        host.resize(width, 200)
        history.set_undo_count(9999)
        history.set_redo_count(9999)
        host.show()
        host.activateWindow()
        history.show()
        if zoom is not None:
            zoom.show()
        settle_painter_overlays(app, host, history, zoom)
        scrollbar = history._scroll_area.verticalScrollBar()
        if pixels == 32 and (width == 200 or with_zoom):
            assert scrollbar.maximum() > 0
        for control in controls:
            assert control.font().pixelSize() == pixels
            assert control.width() >= control.sizeHint().width()
            assert control.height() >= control.sizeHint().height()
        focus_visible_history_control(app, history, controls[0])
        for index, control in enumerate(controls):
            assert control.hasFocus()
            focus_visible_history_control(app, history, control)
            QTest.keyClick(control, Qt.Key.Key_Space)
            if index < len(controls) - 1:
                QTest.keyClick(control, Qt.Key.Key_Tab)
                app.processEvents()
                assert controls[index + 1].hasFocus()
        undo.assert_called_once()
        redo.assert_called_once()
        highlight.assert_called_once_with(True)
        labels.assert_called_once_with(False)
        zones.assert_called_once_with(False)
        if scrollbar.maximum():
            focus_visible_history_control(app, history, controls[0])
            assert scrollbar.value() <= 3
        if zoom is not None:
            focus_visible_history_control(app, history, controls[-1])
            QTest.keyClick(controls[-1], Qt.Key.Key_Tab)
            app.processEvents()
            assert zoom._zoom_buttons[0].hasFocus()
            for button in zoom._zoom_buttons:
                button.setFocus()
                QTest.keyClick(button, Qt.Key.Key_Space)
        settle_painter_overlays(app, host, history, zoom)
        host.resize(900, 700)
        settle_painter_overlays(app, host, history, zoom)
        assert history._chk_highlight.text() == "Highlight transparent"
        assert history._btn_all_vis.text() == "👁  Show All Zones"
        assert history._scroll_area.verticalScrollBar().maximum() == 0
        assert history._scroll_area.horizontalScrollBar().maximum() == 0
        if zoom is not None:
            assert not zoom._compact
        host.resize(200, 700)
        settle_painter_overlays(app, host, history, zoom)
        assert history._scroll_area.verticalScrollBar().maximum() == 0
        host.resize(width, 200)
        settle_painter_overlays(app, host, history, zoom)
    finally:
        host.close()
        sip.delete(host)


@pytest.mark.parametrize("width", [200, 360])
@pytest.mark.parametrize("pixels", [13, 24, 32])
def test_painter_short_actual_canvas_keeps_controls_and_masks(painter, app, tmp_path, width, pixels):
    open_painter_image(painter, tmp_path)
    painter.setStyleSheet(build_stylesheet(PRESET_THEMES["Panda Dark"])
                         + f"\nQWidget {{ font-size: {pixels}px; }}")
    painter._canvas.setFixedSize(width, 200)
    painter.show()
    painter.activateWindow()
    history, zoom, canvas = painter._history_overlay, painter._zoom_overlay, painter._canvas
    canvas._push_history()
    canvas._masks[0][0, 0] = 1
    masks = canvas.get_all_masks()
    history.set_undo_count(9999)
    history.set_redo_count(9999)
    settle_painter_overlays(app, canvas, history, zoom)
    for control, attribute in [
        (history._chk_highlight, "_show_zero_alpha"),
        (history._chk_labels, "_show_alpha_labels"),
    ]:
        before = getattr(canvas, attribute)
        focus_visible_history_control(app, history, control)
        QTest.keyClick(control, Qt.Key.Key_Space)
        assert getattr(canvas, attribute) != before
    focus_visible_history_control(app, history, history._btn_all_vis)
    QTest.keyClick(history._btn_all_vis, Qt.Key.Key_Space)
    assert all(np.array_equal(before, after) for before, after in zip(masks, canvas.get_all_masks()))
    focus_visible_history_control(app, history, history._btn_undo)
    QTest.keyClick(history._btn_undo, Qt.Key.Key_Space)
    assert not canvas._masks[0].any()
    settle_painter_overlays(app, canvas, history, zoom)
    focus_visible_history_control(app, history, history._btn_redo)
    QTest.keyClick(history._btn_redo, Qt.Key.Key_Space)
    assert all(np.array_equal(before, after) for before, after in zip(masks, canvas.get_all_masks()))
    for button in zoom._zoom_buttons:
        button.setFocus()
        assert button.hasFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
    settle_painter_overlays(app, canvas, history, zoom)
    canvas.setFixedSize(900, 700)
    settle_painter_overlays(app, canvas, history, zoom)
    assert history._scroll_area.verticalScrollBar().maximum() == 0
    assert history._chk_highlight.text() == "Highlight transparent"


def test_painter_history_scrolls_oversized_count_labels_without_font_reduction(app):
    host = QWidget()
    undo = Mock()
    history = _FloatingHistoryOverlay(undo, Mock(), host)
    zoom = _FloatingZoomOverlay(Mock(), Mock(), Mock(), host)
    try:
        host.resize(200, 200)
        host.setStyleSheet(build_stylesheet(PRESET_THEMES["Panda Dark"])
                          + "\nQWidget { font-size: 32px; }")
        history.set_undo_count(123456789012)
        history.set_redo_count(123456789012)
        host.show()
        host.activateWindow()
        history.show()
        zoom.show()
        settle_painter_overlays(app, host, history, zoom)
        scrollbar = history._scroll_area.horizontalScrollBar()
        assert scrollbar.maximum() > 0
        assert history._btn_undo.font().pixelSize() == 32
        viewport = history._scroll_area.viewport()
        for value, corner in [(0, history._btn_undo.rect().topLeft()),
                              (scrollbar.maximum(), history._btn_undo.rect().topRight())]:
            scrollbar.setValue(value)
            app.processEvents()
            assert viewport.rect().contains(history._btn_undo.mapTo(viewport, corner))
        history._btn_undo.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(history._btn_undo, Qt.Key.Key_Space)
        undo.assert_called_once()
        settle_painter_overlays(app, host, history, zoom)
        history.set_undo_count(1)
        history.set_redo_count(1)
        host.resize(900, 700)
        settle_painter_overlays(app, host, history, zoom)
        assert scrollbar.maximum() == 0
    finally:
        host.close()
        sip.delete(host)


@pytest.mark.parametrize("name", list(PRESET_THEMES) + list(HIDDEN_THEMES))
def test_painter_zoom_overlay_scales_fits_and_preserves_keyboard_actions(app, name):
    host = QWidget()
    callbacks = [Mock(), Mock(), Mock()]
    overlay = _FloatingZoomOverlay(*callbacks, parent=host)
    theme = {**PRESET_THEMES, **HIDDEN_THEMES}[name]
    try:
        host.resize(700, 300)
        host.show()
        overlay.show()
        for pixels in [13, 24, 32, 13]:
            host.setStyleSheet(build_stylesheet(theme)
                              + f"\nQWidget {{ font-size: {pixels}px; }}")
            for width in [180, 360, 700]:
                host.resize(width, 300)
                for zoom in [1.0, 12.5, 0.125]:
                    overlay.set_zoom(zoom)
                    for _ in range(15):
                        app.processEvents()
                    assert not overlay._position_timer.isActive()
                    assert host.rect().contains(overlay.geometry())
                    assert overlay._zoom_lbl.text() == f"{int(round(zoom * 100))}%"
                    assert overlay._zoom_lbl.width() >= overlay._zoom_lbl.sizeHint().width()
                    assert overlay._zoom_lbl.font().pixelSize() == pixels
                    assert overlay._zoom_lbl.palette().color(QPalette.ColorRole.WindowText) == QColor(theme["text"])
                    for button in overlay._zoom_buttons:
                        assert button.font().pixelSize() == pixels
                        assert button.palette().color(QPalette.ColorRole.ButtonText) == QColor(theme["text"])
                        assert button.width() >= button.sizeHint().width()
                        assert button.height() >= button.sizeHint().height()
                if width == 700:
                    assert not overlay._compact
        assert [button.accessibleName() for button in overlay._zoom_buttons] == [
            "Zoom out", "Fit Painter canvas to window", "Zoom in",
        ]
        host.activateWindow()
        for button, callback in zip(overlay._zoom_buttons, [callbacks[1], callbacks[2], callbacks[0]]):
            button.setFocus()
            app.processEvents()
            QTest.keyClick(button, Qt.Key.Key_Space)
            callback.assert_called_once()
            button.setEnabled(False)
            QTest.keyClick(button, Qt.Key.Key_Space)
            callback.assert_called_once()
    finally:
        host.close()
        sip.delete(host)


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
