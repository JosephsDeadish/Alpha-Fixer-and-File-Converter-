from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog

from src.core.presets import PresetManager
from src.core.settings_manager import SettingsManager
from src.ui.alpha_tool import AlphaFixerTab
from src.ui.converter_tool import ConverterTab
from src.ui.preview_pane import BeforeAfterWidget, ImagePreviewPane


class PreviewLoader(QObject):
    ready = pyqtSignal(QImage, QImage, str, str)
    failed = pyqtSignal(str)

    def __init__(self, *args):
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
