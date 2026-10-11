"""Generated-image Alpha tab geometry and keyboard checks without MainWindow."""
from pathlib import Path
import shutil
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from src.core.presets import PresetManager
from src.core.settings_manager import SettingsManager
from src.ui.alpha_tool import AlphaFixerTab, _AlphaPreviewLoader
from src.ui.theme_engine import DEFAULT_THEME, LIGHT_THEME, build_stylesheet


class ImmediatePreview(_AlphaPreviewLoader):
    def start(self):
        self.run()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def alpha(app):
    directory = Path("build/alpha-layout-tests")
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "generated.png"
    data = np.array([[[10, 40, 90, a] for a in (0, 64, 128, 255)]], dtype=np.uint8)
    with Image.fromarray(data) as image:
        image.save(source)
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(directory / "settings.ini")):
        settings = SettingsManager()
    with patch("src.ui.alpha_tool._AlphaPreviewLoader", ImmediatePreview):
        widget = AlphaFixerTab(PresetManager(settings), settings)
        widget._file_list.addItem(str(source))
        widget._file_list.setCurrentRow(0)
        widget._preview_debounce.stop()
        widget._update_compare()
        widget._btn_undo_batch.show()
        try:
            yield widget
        finally:
            widget._preview_debounce.stop()
            widget._stop_preview_loader()
            widget.close()
            sip.delete(widget)
            settings.sync()
            sip.delete(settings._qs)
            app.processEvents()
            shutil.rmtree(directory)


def settle(app):
    for _ in range(60):
        app.processEvents()


def visible(widget, control):
    viewport = widget._content_scroll.viewport()
    assert viewport.rect().contains(control.mapTo(viewport, QPoint()))
    assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight()))


def controls(widget):
    return [
        widget._btn_add_files, widget._btn_add_folder, widget._btn_clear,
        widget._recursive_check, widget._out_dir_edit, widget._btn_out_dir,
        widget._suffix_edit, widget._file_list, widget._alpha_vis_check,
        widget._atlas_detect_check, widget._compare, widget._btn_run,
        widget._btn_undo_batch, widget._clamp_min_spin, widget._clamp_max_spin,
        widget._invert_check, widget._binary_cut_check, widget._threshold_spin,
        widget._red_spin, widget._green_spin, widget._blue_spin,
        widget._alpha_delta_spin, widget._apply_rgb_check, widget._log,
    ]


@pytest.mark.parametrize("theme", [DEFAULT_THEME, LIGHT_THEME], ids=["dark", "light"])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
def test_compact_geometry_focus_resize_preserves_preview(app, alpha, theme, pixels, size):
    alpha.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
    alpha.resize(*size)
    alpha.show()
    settle(app)
    assert (alpha.width(), alpha.height()) == size
    assert alpha._splitter.orientation() == Qt.Orientation.Vertical
    for scroll in (alpha._content_scroll, alpha._left_scroll, alpha._right_scroll):
        assert scroll.horizontalScrollBar().maximum() == 0
    snapshot = (alpha._preview_path, alpha._build_manual_params(), alpha._preview_request_id,
                alpha._before_stats_lbl.text(), alpha._after_stats_lbl.text())
    assert "111.8" in alpha._after_stats_lbl.text()
    for control in controls(alpha):
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()
        control.setFocus()
        settle(app)
        assert app.focusWidget() is control
        visible(alpha, control)
    for label in alpha._content.findChildren(QLabel):
        if label.isVisible() and label.text():
            assert label.width() >= label.minimumSizeHint().width()
            if label.hasHeightForWidth():
                assert label.height() >= label.heightForWidth(label.width())
    alpha.resize(2400, 1400)
    settle(app)
    assert alpha._splitter.orientation() == Qt.Orientation.Horizontal
    alpha.resize(*size)
    settle(app)
    visible(alpha, alpha._log)
    assert snapshot == (alpha._preview_path, alpha._build_manual_params(), alpha._preview_request_id,
                        alpha._before_stats_lbl.text(), alpha._after_stats_lbl.text())


def test_keyboard_adjustments_helpers_output_and_queue(app, alpha):
    alpha.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    alpha.resize(480, 400)
    alpha.show()
    settle(app)
    alpha._btn_add_files.setFocus()
    for control in controls(alpha):
        if control in (alpha._btn_run, alpha._clamp_min_spin):
            for _ in range(12):
                if app.focusWidget() is control:
                    break
                QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert app.focusWidget() is control
        settle(app)
        visible(alpha, control)
        QTest.keyClick(control, Qt.Key.Key_Tab)
    alpha._green_spin.setFocus()
    QTest.keyClick(alpha._green_spin, Qt.Key.Key_Backtab)
    assert app.focusWidget() is alpha._red_spin
    alpha._threshold_spin.setFocus()
    QTest.keyClick(alpha._threshold_spin, Qt.Key.Key_Up)
    assert alpha._threshold_spin.value() == 1
    assert alpha._preview_debounce.isActive()
    alpha._binary_cut_check.setFocus()
    QTest.keyClick(alpha._binary_cut_check, Qt.Key.Key_Space)
    alpha._preview_debounce.stop()
    alpha._update_compare()
    assert alpha._compare.after_image().pixelColor(0, 0).alpha() == 0
    assert alpha._compare.after_image().pixelColor(1, 0).alpha() == 255
    assert "191.2" in alpha._after_stats_lbl.text()
    alpha._red_spin.setFocus()
    QTest.keyClick(alpha._red_spin, Qt.Key.Key_Up)
    alpha._alpha_delta_spin.setFocus()
    QTest.keyClick(alpha._alpha_delta_spin, Qt.Key.Key_Down)
    alpha._apply_rgb_check.setFocus()
    QTest.keyClick(alpha._apply_rgb_check, Qt.Key.Key_Space)
    alpha._preview_debounce.stop()
    alpha._update_compare()
    assert alpha._compare.after_image().pixelColor(1, 0).getRgb() == (11, 40, 90, 254)
    assert "190.5" in alpha._after_stats_lbl.text()
    stats = alpha._after_stats_lbl.text()
    alpha._alpha_vis_check.setFocus()
    QTest.keyClick(alpha._alpha_vis_check, Qt.Key.Key_Space)
    assert alpha._settings.get("alpha_preview_highlight") is True
    alpha._atlas_detect_check.setFocus()
    QTest.keyClick(alpha._atlas_detect_check, Qt.Key.Key_Space)
    assert alpha._settings.get("alpha_preview_detect_atlas") is True
    assert alpha._after_stats_lbl.text() == stats
    alpha._suffix_edit.setFocus()
    QTest.keyClicks(alpha._suffix_edit, "_fixed")
    assert alpha._settings.get("output_suffix") == "_fixed"
    with patch("src.ui.alpha_tool.QFileDialog.getExistingDirectory",
               return_value="build/alpha-layout-tests") as folder:
        alpha._btn_out_dir.setFocus()
        QTest.keyClick(alpha._btn_out_dir, Qt.Key.Key_Space)
        folder.assert_called_once()
    assert alpha._out_dir_edit.text() == "build/alpha-layout-tests"
    with patch("src.ui.alpha_tool.QFileDialog.getOpenFileNames", return_value=([], "")) as files:
        alpha._btn_add_files.setFocus()
        QTest.keyClick(alpha._btn_add_files, Qt.Key.Key_Space)
        files.assert_called_once()
    alpha._file_list.setFocus()
    QTest.keyClick(alpha._file_list, Qt.Key.Key_Delete)
    assert alpha._file_list.count() == 0


def test_compact_preview_redock_and_busy_actions(app, alpha):
    alpha.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    alpha.resize(480, 400)
    alpha.show()
    settle(app)
    before, after = alpha._before_stats_lbl.text(), alpha._after_stats_lbl.text()
    alpha._compare._on_popout_clicked()
    settle(app)
    assert alpha._btn_dock_back.isVisible()
    alpha.activateWindow()
    settle(app)
    alpha._btn_dock_back.setFocus()
    settle(app)
    visible(alpha, alpha._btn_dock_back)
    QTest.keyClick(alpha._btn_dock_back, Qt.Key.Key_Space)
    settle(app)
    assert alpha._compare._popout_dialog is None
    assert (alpha._before_stats_lbl.text(), alpha._after_stats_lbl.text()) == (before, after)
    alpha._compare.setFocus()
    settle(app)
    visible(alpha, alpha._compare)
    alpha._btn_stop.setEnabled(True)
    alpha._btn_stop.setFocus()
    settle(app)
    visible(alpha, alpha._btn_stop)
    with patch.object(alpha, "_worker") as worker:
        QTest.keyClick(alpha._btn_stop, Qt.Key.Key_Space)
        worker.stop.assert_called_once()


def test_progress_preserves_manually_scrolled_viewport(app, alpha):
    alpha.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    alpha.resize(480, 400)
    alpha.show()
    settle(app)
    alpha._file_list.setFocus()
    settle(app)
    visible(alpha, alpha._file_list)
    bar = alpha._content_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    settle(app)
    position = bar.value()
    assert position == bar.maximum()
    alpha._batch_start_time = 0
    with patch("src.ui.alpha_tool.time.monotonic", return_value=100):
        for current in (1, 50, 99):
            alpha._on_progress(current, 100, "generated.png")
            settle(app)
            assert app.focusWidget() is alpha._file_list
            assert bar.value() == position
