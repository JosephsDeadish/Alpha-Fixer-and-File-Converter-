from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QColor, QPalette, QPainter, QFontMetrics
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QPushButton

from src.core.presets import PresetManager
from src.core.settings_manager import SettingsManager
from src.ui.alpha_tool import AlphaFixerTab
from src.ui.converter_tool import ConverterTab
from src.ui.preview_pane import BeforeAfterWidget, ImagePreviewPane
from src.ui.theme_engine import PRESET_THEMES, HIDDEN_THEMES, build_stylesheet


class PreviewLoader(QObject):
    ready = pyqtSignal(QImage, QImage, str, str)
    failed = pyqtSignal(str)

    def __init__(self, *args, **kwargs):
        super().__init__()

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture(params=["alpha", "converter"])
def tool(request, app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        widget = (AlphaFixerTab(PresetManager(settings), settings)
                  if request.param == "alpha" else ConverterTab(settings))
        widget.show()
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(0xFF123456)
        widget._compare.set_before(image)
        widget._compare.set_after(image)
        app.processEvents()
        yield widget
        widget._compare.close_popout_dialog()
        widget._preview_debounce.stop()
        widget._stop_preview_loader()
        widget.close()
        sip.delete(widget)
        settings.sync()
        sip.delete(settings._qs)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def dispose_events():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size", [(180, 120), (320, 240)])
@pytest.mark.parametrize("pixels", [13, 24, 32])
def test_compact_comparison_keyboard_divider_and_contained_controls(app, size, pixels):
    widget = BeforeAfterWidget()
    try:
        widget.setStyleSheet(build_stylesheet(PRESET_THEMES["Panda Dark"])
                            + f"\nQWidget {{ font-size: {pixels}px; }}")
        widget.resize(*size)
        before = QImage(32, 24, QImage.Format.Format_RGBA8888)
        before.fill(QColor("red"))
        after = QImage(32, 24, QImage.Format.Format_RGBA8888)
        after.fill(QColor("blue"))
        widget.set_before(before)
        widget.set_after(after)
        before_pixmap = widget._pix_before.toImage()
        after_pixmap = widget._pix_after.toImage()
        widget.show()
        widget.activateWindow()
        for _ in range(10):
            app.processEvents()
        assert widget.rect().contains(widget._zoom_bar.geometry())
        assert widget.rect().contains(widget._popout_btn.geometry())
        assert not widget._zoom_bar.geometry().intersects(widget._popout_btn.geometry())
        assert widget._zoom_bar.y() == widget._popout_btn.y()
        label_font = widget.font()
        label_font.setBold(True)
        label_height = QFontMetrics(label_font).height() + 4
        for control in [widget._popout_btn, widget._zoom_bar]:
            assert control.geometry().bottom() + 6 + label_height <= widget.height()

        widget._popout_btn.setFocus()
        app.processEvents()
        unfocused = widget.grab().toImage()
        widget.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        assert widget.hasFocus()
        assert widget.accessibleName() == "Comparison divider"
        assert "Home and End" in widget.accessibleDescription()
        assert widget.grab().toImage() != unfocused
        QTest.keyClick(widget, Qt.Key.Key_Right)
        assert widget._split == pytest.approx(0.52)
        QTest.keyClick(widget, Qt.Key.Key_Left)
        assert widget._split == pytest.approx(0.5)
        QTest.keyClick(widget, Qt.Key.Key_Home)
        assert widget._split == 0.02
        QTest.keyClick(widget, Qt.Key.Key_Left)
        assert widget._split == 0.02
        QTest.keyClick(widget, Qt.Key.Key_End)
        assert widget._split == 0.98
        QTest.keyClick(widget, Qt.Key.Key_Right)
        assert widget._split == 0.98
        for modifiers in [Qt.KeyboardModifier.ControlModifier,
                          Qt.KeyboardModifier.ShiftModifier,
                          Qt.KeyboardModifier.AltModifier]:
            QTest.keyClick(widget, Qt.Key.Key_Left, modifiers)
            assert widget._split == 0.98
        QTest.keyClick(widget, Qt.Key.Key_Tab)
        assert not widget.hasFocus()
        button = app.focusWidget()
        assert isinstance(button, QPushButton)
        QTest.keyClick(button, Qt.Key.Key_Left)
        assert widget._split == 0.98
        zoom_buttons = widget._zoom_bar.findChildren(QPushButton)
        zoom_buttons[2].setFocus()
        QTest.keyClick(zoom_buttons[2], Qt.Key.Key_Space)
        assert widget._zoom > 1
        QTest.mousePress(widget, Qt.MouseButton.MiddleButton, pos=QPoint(60, 90))
        QTest.mouseMove(widget, QPoint(70, 95))
        QTest.mouseRelease(widget, Qt.MouseButton.MiddleButton, pos=QPoint(70, 95))
        assert widget._pan_x != 0
        zoom_buttons[1].setFocus()
        QTest.keyClick(zoom_buttons[1], Qt.Key.Key_Space)
        assert (widget._zoom, widget._pan_x, widget._pan_y) == (1, 0, 0)
        widget._update_split(widget.width() / 2)
        QTest.mousePress(widget, Qt.MouseButton.LeftButton,
                         pos=QPoint(widget.width() // 2, widget.height() // 2))
        QTest.mouseMove(widget, QPoint(widget.width() * 3 // 4, widget.height() // 2))
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton,
                           pos=QPoint(widget.width() * 3 // 4, widget.height() // 2))
        assert widget._split == pytest.approx(0.75)
        assert widget._pix_before.toImage() == before_pixmap
        assert widget._pix_after.toImage() == after_pixmap
    finally:
        widget.close()
        sip.delete(widget)
        dispose_events()


def test_floating_comparison_inherits_keyboard_divider_and_zoom(app):
    widget = BeforeAfterWidget()
    try:
        image = QImage(16, 16, QImage.Format.Format_RGBA8888)
        image.fill(QColor("green"))
        widget.set_before(image)
        widget.set_after(image)
        widget.show()
        widget.activateWindow()
        widget._popout_btn.setFocus()
        QTest.keyClick(widget._popout_btn, Qt.Key.Key_Space)
        dialog = widget._popout_dialog
        assert dialog is not None
        compare = dialog.findChild(BeforeAfterWidget)
        dialog.activateWindow()
        compare.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        QTest.keyClick(compare, Qt.Key.Key_Right)
        assert compare._split == pytest.approx(0.52)
        assert widget._split == 0.5
        buttons = compare._zoom_bar.findChildren(QPushButton)
        buttons[2].setFocus()
        QTest.keyClick(buttons[2], Qt.Key.Key_Space)
        assert compare._zoom > 1
        assert widget._zoom == 1
        QTest.keyClick(buttons[2], Qt.Key.Key_Escape)
        app.processEvents()
        assert widget._popout_dialog is None
    finally:
        widget.close_popout_dialog()
        widget.close()
        sip.delete(widget)
        dispose_events()


@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("name", ["Panda Dark", "Panda Light"])
def test_painted_preview_text_inherits_live_font_and_elides_within_each_side(app, pixels, name):
    fonts, texts = [], []

    class RecordingPainter(QPainter):
        def setFont(self, font):
            fonts.append(font)
            super().setFont(font)

        def drawText(self, *args):
            texts.append((args, self.font()))
            return super().drawText(*args)

    widget = BeforeAfterWidget()
    try:
        widget.setStyleSheet(build_stylesheet(PRESET_THEMES[name])
                            + f"\nQWidget {{ font-size: {pixels}px; }}")
        widget.resize(240, 240)
        widget.show()
        app.processEvents()
        with patch("src.ui.preview_pane.QPainter", RecordingPainter):
            widget.grab()
            assert any(args[-1] == "Select a file to compare" for args, _ in texts)
            widget.set_loading()
            widget.grab()
            assert any(args[-1] == "Processing…" for args, _ in texts)
            image = QImage(8, 8, QImage.Format.Format_RGBA8888)
            image.fill(QColor("red"))
            widget.set_before(image)
            widget.set_after(image)
            widget.set_stats(
                {"min": 0, "max": 255, "mean": 100.0, "percent_nonzero": 50.0},
                {"min": 0, "max": 255, "mean": 120.0, "percent_nonzero": 75.0},
            )
            texts.clear()
            widget.grab()
        assert fonts
        assert all(font.pixelSize() == pixels for font in fonts)
        assert all(font.family() == widget.font().family() for font in fonts)
        painted = [(args, font) for args, font in texts if len(args) == 3]
        assert len(painted) == 4
        assert any("…" in args[-1] for args, _ in painted)
        for args, font in painted:
            assert QFontMetrics(font).horizontalAdvance(args[-1]) <= widget.width() // 2 - 8
        assert widget._stats_before and widget._stats_after
    finally:
        widget.close()
        sip.delete(widget)


@pytest.mark.parametrize("name", list(PRESET_THEMES) + list(HIDDEN_THEMES))
def test_embedded_preview_controls_scale_and_remain_contained_with_keyboard_zoom(app, name):
    widget = BeforeAfterWidget()
    theme = {**PRESET_THEMES, **HIDDEN_THEMES}[name]
    try:
        widget.resize(360, 220)
        widget.show()
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(QColor("red"))
        widget.set_before(image)
        widget.set_after(image)
        zoom_buttons = widget._zoom_bar.findChildren(QPushButton)
        assert [button.accessibleName() for button in zoom_buttons] == [
            "Zoom out", "Fit preview to window", "Zoom in",
        ]
        assert widget._popout_btn.accessibleName() == "Undock preview"
        for pixels in [13, 24, 32, 13]:
            widget.setStyleSheet(build_stylesheet(theme)
                                + f"\nQWidget {{ font-size: {pixels}px; }}")
            for width in [360, 180, 700]:
                widget.resize(width, 220)
                for _ in range(5):
                    app.processEvents()
                for _ in range(10):
                    app.processEvents()
                assert not widget._overlay_update_timer.isActive()
                assert widget.rect().contains(widget._zoom_bar.geometry())
                assert widget.rect().contains(widget._popout_btn.geometry())
                assert not widget._zoom_bar.geometry().intersects(widget._popout_btn.geometry())
                for button in zoom_buttons + [widget._popout_btn]:
                    assert not button.styleSheet()
                    assert button.font().pixelSize() == pixels
                    assert button.palette().color(QPalette.ColorRole.ButtonText) == QColor(theme["text"])
                    assert button.width() >= button.sizeHint().width()
                    assert button.height() >= button.sizeHint().height()
        widget.activateWindow()
        QTest.mouseMove(widget, widget.rect().bottomRight())
        for button in zoom_buttons + [widget._popout_btn]:
            other = zoom_buttons[0] if button is not zoom_buttons[0] else zoom_buttons[1]
            other.setFocus()
            app.processEvents()
            before_size, before_geometry = button.sizeHint(), button.geometry()
            before_image = button.grab().toImage()
            button.setFocus(Qt.FocusReason.TabFocusReason)
            app.processEvents()
            assert button.hasFocus()
            assert button.sizeHint() == before_size
            assert button.geometry() == before_geometry
            assert button.grab().toImage() != before_image, name
        zoom_buttons[2].setFocus()
        app.processEvents()
        initial = widget._zoom
        QTest.keyClick(zoom_buttons[2], Qt.Key.Key_Space)
        assert widget._zoom > initial
        QTest.keyClick(zoom_buttons[1], Qt.Key.Key_Space)
        assert widget._zoom == 1
        zoom_buttons[2].setEnabled(False)
        QTest.keyClick(zoom_buttons[2], Qt.Key.Key_Space)
        assert widget._zoom == 1
        widget.resize(700, 220)
        app.processEvents()
        assert widget._popout_btn.text() == "⇗ Undock"
        widget._popout_btn.click()
        assert widget._popout_btn.accessibleName() == "Redock preview"
        widget.close_popout_dialog()
        assert widget._popout_btn.accessibleName() == "Undock preview"
        widget.resize(180, 220)
        widget._popout_btn.click()
        assert widget._popout_btn.accessibleName() == "Redock preview"
        assert widget.rect().contains(widget._popout_btn.geometry())
        widget.close_popout_dialog()
        assert widget.rect().contains(widget._popout_btn.geometry())
    finally:
        widget.close_popout_dialog()
        widget.close()
        sip.delete(widget)
        dispose_events()


@pytest.mark.parametrize("name", ["Panda Dark", "Panda Light", "Secret Skeleton"])
def test_floating_redock_uses_shared_theme_and_keyboard_action(tool, app, name):
    theme = {**PRESET_THEMES, **HIDDEN_THEMES}[name]
    tool.setStyleSheet(build_stylesheet(theme) + "\nQWidget { font-size: 24px; }")
    tool._compare._on_popout_clicked()
    dialog = tool._compare._popout_dialog
    try:
        dialog.setStyleSheet(build_stylesheet(theme) + "\nQWidget { font-size: 24px; }")
        app.processEvents()
        button = next(button for button in dialog.findChildren(QPushButton)
                      if button.accessibleName() == "Redock preview" and button.isVisible())
        assert not button.styleSheet()
        assert button.font().pixelSize() == 24
        assert button.palette().color(QPalette.ColorRole.ButtonText) == QColor(theme["text"])
        assert button.width() >= button.sizeHint().width()
        dialog.activateWindow()
        button.setFocus()
        app.processEvents()
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert tool._compare._popout_dialog is None
        assert tool._compare.isVisible()
    finally:
        tool._compare.close_popout_dialog()
        dispose_events()


def test_repeated_popout_close_releases_windows_and_restores_controls(tool):
    for _ in range(3):
        tool._compare._on_popout_clicked()
        dialog = tool._compare._popout_dialog
        assert dialog is not None
        assert tool._btn_dock_back.isVisible()
        assert not tool._compare.isVisible()
        dialog.close()
        assert tool._compare._popout_dialog is None
        assert tool._compare.isVisible()
        assert not tool._btn_dock_back.isVisible()
        dispose_events()
        assert sip.isdeleted(dialog)
        assert tool.findChildren(QDialog) == []


@pytest.mark.parametrize("tool", ["converter"], indirect=True)
@pytest.mark.parametrize("animated", [False, True])
def test_converter_redock_restores_animation_controls_without_resetting_speed(tool, tmp_path, animated):
    path = tmp_path / ("source.gif" if animated else "source.png")
    with Image.new("RGBA", (8, 8), "red") as first:
        if animated:
            with Image.new("RGBA", (8, 8), "blue") as second:
                first.save(path, save_all=True, append_images=[second], duration=100)
        else:
            first.save(path)
    with patch("src.ui.converter_tool._ConverterPreviewLoader", PreviewLoader):
        tool._refresh_preview(str(path))
        if animated:
            tool._gif_speed_slider.setValue(250)
        tool._compare._on_popout_clicked()
        assert not tool._gif_speed_widget.isVisible()
        tool._on_dock_back_clicked()
        assert tool._gif_speed_widget.isVisible() == animated
        if animated:
            assert tool._gif_speed_slider.value() == 250
            assert tool._compare._movie.speed() == 250
        dispose_events()


@pytest.mark.parametrize("tool", ["alpha"], indirect=True)
def test_alpha_helper_settings_sync_only_with_current_window(tool):
    baseline_alpha = tool._alpha_vis_check.receivers(tool._alpha_vis_check.toggled)
    baseline_atlas = tool._atlas_detect_check.receivers(tool._atlas_detect_check.toggled)
    for _ in range(3):
        tool._compare._on_popout_clicked()
        dialog = tool._compare._popout_dialog
        checks = dialog.findChildren(QCheckBox)
        alpha = next(check for check in checks if "Highlight" in check.text())
        atlas = next(check for check in checks if "Detect Atlas" in check.text())
        assert tool._alpha_vis_check.receivers(tool._alpha_vis_check.toggled) == baseline_alpha + 1
        assert tool._atlas_detect_check.receivers(tool._atlas_detect_check.toggled) == baseline_atlas + 1
        alpha.setChecked(not alpha.isChecked())
        assert tool._alpha_vis_check.isChecked() == alpha.isChecked()
        tool._alpha_vis_check.setChecked(not tool._alpha_vis_check.isChecked())
        assert alpha.isChecked() == tool._alpha_vis_check.isChecked()
        atlas.setChecked(not atlas.isChecked())
        assert tool._atlas_detect_check.isChecked() == atlas.isChecked()
        tool._atlas_detect_check.setChecked(not tool._atlas_detect_check.isChecked())
        assert atlas.isChecked() == tool._atlas_detect_check.isChecked()
        dialog.close()
        assert tool._alpha_vis_check.receivers(tool._alpha_vis_check.toggled) == baseline_alpha
        assert tool._atlas_detect_check.receivers(tool._atlas_detect_check.toggled) == baseline_atlas
        dispose_events()
        assert sip.isdeleted(dialog)
        tool._alpha_vis_check.setChecked(not tool._alpha_vis_check.isChecked())
        tool._atlas_detect_check.setChecked(not tool._atlas_detect_check.isChecked())


def test_standalone_popout_cleanup_does_not_require_host_tool(app):
    widget = BeforeAfterWidget()
    try:
        widget._on_popout_clicked()
        dialog = widget._popout_dialog
        dialog.reject()
        dispose_events()
        assert sip.isdeleted(dialog)
        assert widget._popout_dialog is None
    finally:
        sip.delete(widget)


@pytest.mark.parametrize("tool", ["converter"], indirect=True)
@pytest.mark.parametrize("clear", ["selection", "missing"])
def test_converter_clear_closes_floating_window_and_removes_animation_controls(tool, tmp_path, clear):
    path = tmp_path / "animation.gif"
    with Image.new("RGBA", (8, 8), "red") as first, Image.new("RGBA", (8, 8), "blue") as second:
        first.save(path, save_all=True, append_images=[second], duration=100)
    with patch("src.ui.converter_tool._ConverterPreviewLoader", PreviewLoader):
        tool._refresh_preview(str(path))
        tool._compare._on_popout_clicked()
        dialog = tool._compare._popout_dialog
        if clear == "selection":
            tool._on_selection_changed(-1)
        else:
            tool._refresh_preview(str(tmp_path / "missing.gif"))
        assert tool._compare._popout_dialog is None
        assert not tool._compare.has_images()
        assert not tool._gif_speed_widget.isVisible()
        assert not tool._btn_dock_back.isVisible()
        dispose_events()
        assert sip.isdeleted(dialog)


def test_plain_preview_close_has_no_floating_window_dependency(app):
    widget = ImagePreviewPane()
    try:
        widget.show()
        assert widget.close()
    finally:
        sip.delete(widget)


@pytest.fixture
def comparison(app):
    widget = BeforeAfterWidget()
    image = QImage(8, 8, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    widget.set_before(image)
    widget.set_after(image)
    widget._on_popout_clicked()
    floating = widget._popout_dialog.findChild(BeforeAfterWidget)
    yield widget, floating
    widget.close_popout_dialog()
    dispose_events()
    sip.delete(widget)


def test_floating_preview_mirrors_images_raw_state_loading_stats_and_theme(comparison):
    widget, floating = comparison
    floating._zoom = 2.0
    floating._split = 0.7
    before = QImage(10, 12, QImage.Format.Format_RGBA8888)
    before.fill(0xFFAA1122)
    after = QImage(10, 12, QImage.Format.Format_RGBA8888)
    after.fill(0xFF2233AA)
    widget.set_loading()
    assert floating._loading
    assert floating._pix_after is None
    widget.set_before(before)
    widget.set_after(after)
    assert floating._pix_before.toImage().convertToFormat(before.format()) == before
    assert floating._pix_after.toImage().convertToFormat(after.format()) == after
    assert floating.before_image() == before
    assert floating.after_image() == after
    assert not floating._loading
    widget.set_stats({"min": 1, "max": 255, "mean": 100}, {"min": 0, "max": 200, "mean": 50})
    assert floating._stats_before == widget._stats_before
    assert floating._stats_after == widget._stats_after
    widget.set_divider_color("#abcdef")
    assert floating._divider_color == "#abcdef"
    assert floating._zoom == 2.0
    assert floating._split == 0.7
    widget.clear()
    assert floating._pix_before is None
    assert floating._pix_after is None
    assert not floating.has_images()
    assert floating._stats_before == floating._stats_after == ""


def test_floating_overlay_updates_do_not_replace_raw_images(comparison):
    widget, floating = comparison
    original = widget.before_image().copy()
    overlay = original.copy()
    overlay.fill(0xFFEE2211)
    widget.set_before(overlay, store_raw=False, stop_movie=False)
    widget.set_after(overlay, store_raw=False)
    assert floating._pix_before.toImage().convertToFormat(overlay.format()) == overlay
    assert floating.before_image() == original
    assert floating.after_image() == original


def test_floating_movie_tracks_replaced_animation_without_own_decoder(comparison, tmp_path):
    widget, floating = comparison
    for index, color in enumerate(["red", "green"]):
        path = tmp_path / f"animation{index}.gif"
        with Image.new("RGB", (8, 8), color) as first, Image.new("RGB", (8, 8), "blue") as second:
            first.save(path, save_all=True, append_images=[second], duration=100)
        widget.animate_before(str(path))
        widget._movie.setPaused(True)
        assert widget._movie.jumpToFrame(1)
        assert floating._movie is None
        assert floating._movie_path == str(path)
        assert floating._pix_before.toImage() == widget._pix_before.toImage()
        dispose_events()
    widget.clear()
    assert floating._movie_path == ""
    assert floating._pix_before is None
