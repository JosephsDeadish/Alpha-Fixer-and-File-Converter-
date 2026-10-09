from unittest.mock import Mock, patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


class ExportState(QObject):
    export_finished = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.active = True
        self.cancel_calls = 0

    def is_exporting(self):
        return self.active

    def request_export_cancel(self):
        self.cancel_calls += 1

    def finish(self):
        self.active = False
        self.export_finished.emit()


def test_main_window_defers_shutdown_until_gif_export_cleanup(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        window = MainWindow(settings)
    export = ExportState(window)
    window._gif_builder_dlg = export
    try:
        with patch.object(window, "close") as resume, \
                patch.object(settings, "sync") as sync:
            for _ in range(2):
                event = QCloseEvent()
                window.closeEvent(event)
                assert not event.isAccepted()
            assert export.cancel_calls == 1
            assert window._gif_shutdown_pending
            assert not getattr(window, "_shutdown_complete", False)
            sync.assert_not_called()
            export.finish()
            app.processEvents()
            resume.assert_called_once()
            assert not window._gif_shutdown_pending
            export.export_finished.emit()
            app.processEvents()
            resume.assert_called_once()
    finally:
        export.active = False
        window._gif_builder_dlg = None
        window.close()
        sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)


def test_direct_exit_drains_gif_cleanup_before_returning(app):
    import main

    events = []

    class Window(QWidget):
        def closeEvent(self, event):
            if self._gif_builder_dlg.is_exporting():
                events.append("deferred")
                event.ignore()
                return
            events.append("closed")
            super().closeEvent(event)

    window = Window()
    export = ExportState(window)
    window._gif_builder_dlg = export
    watchdog = Mock()
    window.show()

    def exit_during_export():
        app.exit(7)
        QTimer.singleShot(0, export.finish)

    QTimer.singleShot(0, exit_during_export)
    try:
        assert main._run_gui_event_loop(app, window, watchdog) == 7
        assert export.cancel_calls == 1
        assert not export.is_exporting()
        assert not window.isVisible()
        assert events[-1] == "closed"
        assert "deferred" in events
        assert not sip.isdeleted(app)
        assert not sip.isdeleted(window)
    finally:
        sip.delete(window)
