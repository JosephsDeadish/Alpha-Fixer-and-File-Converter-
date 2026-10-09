from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QLineEdit, QListWidget, QPushButton,
    QScrollArea, QScrollBar, QSlider, QSpinBox, QTextEdit, QWidget,
)

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("auto_fill", [False, True])
def test_background_transparency_only_refreshes_changed_properties(app, auto_fill):
    widget = QWidget()
    widget.setAutoFillBackground(auto_fill)
    sibling = QWidget()
    sibling.setAutoFillBackground(not auto_fill)
    host = SimpleNamespace(_background_transparency_targets=lambda: [widget, sibling])
    style = Mock()
    try:
        with patch.object(widget, "style", return_value=style), \
                patch.object(sibling, "style", return_value=style), \
                patch.object(widget, "setProperty", wraps=widget.setProperty) as set_property, \
                patch.object(widget, "setAutoFillBackground",
                             wraps=widget.setAutoFillBackground) as set_fill, \
                patch.object(app, "styleSheet", return_value="QWidget { color: red; }"), \
                patch.object(app, "setStyleSheet") as refresh:
            MainWindow._set_background_host_transparency(host, False)
            assert widget.property("customBgTransparent") is None
            set_property.assert_not_called()
            set_fill.assert_not_called()
            style.unpolish.assert_not_called()
            style.polish.assert_not_called()
            refresh.assert_not_called()

            for enabled in (True, True, False, False, True, False):
                MainWindow._set_background_host_transparency(host, enabled)
                assert widget.property("customBgTransparent") is enabled
                assert widget.autoFillBackground() is (False if enabled else auto_fill)
                assert sibling.property("customBgTransparent") is enabled
                assert sibling.autoFillBackground() is (False if enabled else not auto_fill)
            assert set_property.call_count == set_fill.call_count == 4
            style.unpolish.assert_not_called()
            style.polish.assert_not_called()
            assert refresh.call_count == 4
    finally:
        sip.delete(widget)
        sip.delete(sibling)


def test_background_targets_exclude_controls_and_their_private_children(app):
    root = QWidget()
    controls = [
        QPushButton(root), QComboBox(root), QSpinBox(root), QScrollBar(root),
        QSlider(root), QLineEdit(root), QTextEdit(root), QListWidget(root),
    ]
    controls[1].setEditable(True)
    scroll = QScrollArea(root)
    content = QWidget()
    scroll.setWidget(content)
    host = SimpleNamespace(_bg_host_widgets=[root, scroll], _bg_tabs=None)
    try:
        targets = MainWindow._background_transparency_targets(host)
        assert all(widget in targets for widget in (root, scroll, content, scroll.viewport()))
        assert len({id(widget) for widget in targets}) == len(targets)
        for control in controls + [scroll.horizontalScrollBar(), scroll.verticalScrollBar()]:
            assert control not in targets
            assert all(child not in targets for child in control.findChildren(QWidget))
    finally:
        sip.delete(root)


@pytest.mark.parametrize("background_kind", ["disabled", "png", "gif"])
def test_repeated_native_main_window_theme_and_background_lifecycle(app, tmp_path, background_kind):
    background = tmp_path / f"background.{background_kind}"
    if background_kind != "disabled":
        with Image.new("RGB", (16, 16), "red") as image:
            image.save(background)
    original_stylesheet = app.styleSheet()
    try:
        for iteration in range(3):
            with patch("src.core.settings_manager._settings_ini_path",
                       return_value=str(tmp_path / f"settings-{iteration}.ini")):
                settings = SettingsManager()
                settings.set("custom_bg_enabled", background_kind != "disabled")
                settings.set("custom_bg_path", str(background))
                window = MainWindow(settings)
            try:
                window.show()
                app.processEvents()
                for theme, use_theme, enabled, path_exists in [
                    ("Panda Dark", False, True, True),
                    ("Panda Light", False, True, True),
                    ("Panda Light", True, True, True),
                    ("Panda Dark", False, False, True),
                    ("Panda Dark", False, True, False),
                    ("Panda Dark", False, True, True),
                ]:
                    settings.set("theme", theme)
                    settings.set("use_theme_bg", use_theme)
                    settings.set("custom_bg_enabled", enabled and background_kind != "disabled")
                    settings.set("custom_bg_path", str(background if path_exists else tmp_path / "missing.png"))
                    window._settings_apply_timer.stop()
                    window._apply_theme()
                    window._apply_theme()
                    app.processEvents()
                    transparent = background_kind != "disabled" and not use_theme and enabled and path_exists
                    assert bool(window.centralWidget().property("customBgTransparent")) == transparent
                    assert window._bg_overlay.isVisible() == transparent
                    assert not window._btn_settings.property("customBgTransparent")
                    assert window._btn_settings.palette().color(QPalette.ColorRole.Button).alpha() == 255
                    if transparent:
                        assert window.centralWidget().palette().color(QPalette.ColorRole.Window).alpha() == 0
                    else:
                        assert window.centralWidget().palette().color(QPalette.ColorRole.Window).alpha() == 255
                    assert not window.grab().isNull()
            finally:
                window.close()
                sip.delete(window)
                settings.sync()
                sip.delete(settings._qs)
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        app.setStyleSheet(original_stylesheet)
