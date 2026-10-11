"""Generated-image Alpha tab geometry and keyboard checks without MainWindow."""
from pathlib import Path
import shutil
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

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
        assert not widget._advanced_toggle.isChecked()
        assert all(control.isHidden() for control in widget._advanced_controls)
        widget._advanced_toggle.setChecked(True)
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
        widget._invert_check, widget._advanced_toggle,
        widget._binary_cut_check, widget._threshold_spin,
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
                assert label.height() >= label.heightForWidth(label.width()), label.text()
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


@pytest.mark.parametrize("size,pixels", [((480, 400), 32), ((1200, 800), 13)])
def test_disclosure_preserves_active_settings_pixels_and_focus(app, alpha, size, pixels):
    alpha.setStyleSheet(build_stylesheet() + f"\nQWidget {{ font-size: {pixels}px; }}")
    alpha.resize(*size)
    alpha.show()
    settle(app)
    alpha._threshold_spin.setValue(128)
    alpha._binary_cut_check.setChecked(True)
    alpha._red_spin.setValue(7)
    alpha._alpha_delta_spin.setValue(-10)
    alpha._apply_rgb_check.setChecked(True)
    alpha._preview_debounce.stop()
    alpha._update_compare()
    params = alpha._build_manual_params()
    image = alpha._compare.after_image().copy()
    request = alpha._preview_request_id
    stats = alpha._after_stats_lbl.text()
    assert alpha._threshold_label.buddy() is alpha._threshold_spin
    assert alpha._threshold_spin.accessibleName() == "Binary cutoff (0–255)"
    assert "At 0, all pixels become opaque" in alpha._threshold_spin.accessibleDescription()
    alpha._red_spin.setFocus()
    alpha._advanced_toggle.setChecked(False)
    settle(app)
    assert app.focusWidget() is alpha._advanced_toggle
    visible(alpha, alpha._advanced_toggle)
    assert all(control.isHidden() for control in alpha._advanced_controls)
    assert not alpha._advanced_status.isHidden()
    assert "binary cut at 128" in alpha._advanced_status.text()
    assert "RGBA R +7, A -10" in alpha._advanced_status.text()
    assert "2 on" in alpha._advanced_toggle.text()
    assert alpha._compare.after_image() == image
    assert alpha._build_manual_params() == params
    assert alpha._preview_request_id == request
    assert alpha._after_stats_lbl.text() == stats
    assert not alpha._preview_debounce.isActive()
    QTest.keyClick(alpha._advanced_toggle, Qt.Key.Key_Space)
    settle(app)
    assert all(not control.isHidden() for control in alpha._advanced_controls)
    assert alpha._compare.after_image() == image
    assert alpha._build_manual_params() == params
    assert alpha._preview_request_id == request
    assert not alpha._preview_debounce.isActive()
    QTest.keyClick(alpha._advanced_toggle, Qt.Key.Key_Tab)
    assert app.focusWidget() is alpha._binary_cut_check
    alpha._apply_rgb_check.setChecked(False)
    alpha._binary_cut_check.setChecked(False)
    alpha._advanced_toggle.setChecked(False)
    assert "selective threshold 128" in alpha._advanced_status.text()
    assert "Saved RGBA deltas (disabled): R +7, A -10" in alpha._advanced_status.text()
    assert alpha._threshold_spin.accessibleName() == "Selective threshold (0–255)"


def test_basic_default_and_keyboard_skips_collapsed_controls(app, alpha):
    alpha._advanced_toggle.setChecked(False)
    alpha.resize(640, 480)
    alpha.show()
    settle(app)
    assert "no processing changes" in alpha._advanced_status.text()
    assert "0 processes all pixels" in alpha._advanced_status.text()
    alpha._advanced_toggle.setFocus()
    QTest.keyClick(alpha._advanced_toggle, Qt.Key.Key_Tab)
    assert app.focusWidget() is alpha._log
    QTest.keyClick(alpha._log, Qt.Key.Key_Backtab)
    assert app.focusWidget() is alpha._advanced_toggle


def test_packaged_root_style_guidance_height(app, alpha):
    """Use the launch font and application stylesheet, not a tab-local override."""
    previous_font, previous_style = app.font(), app.styleSheet()
    root = QWidget()
    root.setFixedSize(1024, 768)
    alpha.setParent(root)
    alpha.setGeometry(0, 190, 1024, 556)
    try:
        app.setFont(QFont("Segoe UI", 10))
        app.setStyleSheet(build_stylesheet(DEFAULT_THEME) + "\nQWidget { font-size: 13px; }")
        alpha._advanced_toggle.setChecked(False)
        root.show()
        alpha.show()
        settle(app)
        for label in alpha._content.findChildren(QLabel):
            if label.isVisible() and label.text():
                assert label.hasHeightForWidth(), label.text()
                assert label.height() >= label.heightForWidth(label.width()), label.text()
        hint = alpha._responsive_grids[1][1][0][0]
        assert hint.geometry().bottom() < alpha._clamp_min_spin.geometry().top()
        assert (alpha._preview_hint_lbl.geometry().bottom()
                < alpha._preview_helper_lbl.geometry().top())
        assert (alpha._preview_helper_lbl.geometry().bottom()
                < alpha._compare.geometry().top())
    finally:
        alpha.setParent(None)
        root.close()
        sip.delete(root)
        app.setStyleSheet(previous_style)
        app.setFont(previous_font)


def test_wrapped_label_minimum_shrinks_after_widening_and_shorter_text(app, alpha):
    alpha.setStyleSheet(build_stylesheet() + "\nQWidget { font-size: 32px; }")
    label = QLabel("Wrapped guidance must remain readable. " * 20, alpha._content)
    label.setFixedWidth(160)
    alpha._reflow_controls()
    narrow_height = label.minimumHeight()
    assert narrow_height > label.fontMetrics().height() * 2

    label.setFixedWidth(800)
    alpha._reflow_controls()
    wide_height = label.minimumHeight()
    assert 0 < wide_height < narrow_height

    label.setText("Short")
    alpha._reflow_controls()
    short_height = label.minimumHeight()
    assert 0 < short_height < wide_height
    label.setMinimumHeight(0)
    assert short_height == label.heightForWidth(label.width())
    alpha._reflow_controls()
    assert label.minimumHeight() == short_height
