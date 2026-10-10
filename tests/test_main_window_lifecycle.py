from unittest.mock import patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("iteration", range(6))
def test_real_builders_and_queued_status_survive_window_teardown(app, tmp_path, iteration):
    original_stylesheet = app.styleSheet()
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / f"settings-{iteration}.ini")):
        settings = SettingsManager()
        window = MainWindow(settings)
    try:
        window.show()
        window._open_or_focus_gif_builder()
        window._open_or_focus_video_builder()
        app.processEvents()
        for _ in range(3):
            for dialog in (window._gif_builder_dlg, window._video_tool_dlg):
                dialog.hide()
                dialog.show()
                dialog.activateWindow()
            window._apply_theme()
            app.processEvents()
        window._gif_builder_dlg.hide()
        window._video_tool_dlg.hide()
        window.close()
        sip.delete(window)
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        unrelated = QWidget()
        unrelated.show()
        app.processEvents()
        sip.delete(unrelated)
    finally:
        if not sip.isdeleted(window):
            window.close()
            sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)
        app.processEvents()
        app.setStyleSheet(original_stylesheet)
