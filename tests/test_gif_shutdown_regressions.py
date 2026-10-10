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


@pytest.mark.parametrize("attributes", [
    ("_gif_builder_dlg",), ("_video_tool_dlg",),
    ("_gif_builder_dlg", "_video_tool_dlg"),
])
def test_main_window_defers_shutdown_until_builder_export_cleanup(app, tmp_path, attributes):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        window = MainWindow(settings)
    exports = [ExportState(window) for _ in attributes]
    for attribute, export in zip(attributes, exports):
        setattr(window, attribute, export)
    try:
        with patch.object(window, "close") as resume, \
                patch.object(settings, "sync") as sync:
            for _ in range(2):
                event = QCloseEvent()
                window.closeEvent(event)
                assert not event.isAccepted()
            assert all(export.cancel_calls == 1 for export in exports)
            assert window._builder_shutdown_pending == exports
            assert not getattr(window, "_shutdown_complete", False)
            sync.assert_not_called()
            for export in exports[:-1]:
                export.finish()
                app.processEvents()
                resume.assert_not_called()
                assert not getattr(window, "_shutdown_complete", False)
            exports[-1].finish()
            app.processEvents()
            resume.assert_called_once()
            assert not window._builder_shutdown_pending
            for export in exports:
                export.export_finished.emit()
            app.processEvents()
            resume.assert_called_once()
    finally:
        for attribute, export in zip(attributes, exports):
            export.active = False
            setattr(window, attribute, None)
        window.close()
        sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)


@pytest.mark.parametrize("attributes", [
    ("_gif_builder_dlg",), ("_video_tool_dlg",),
    ("_gif_builder_dlg", "_video_tool_dlg"),
])
def test_direct_exit_drains_builder_cleanup_before_returning(app, attributes):
    import main

    events = []

    class Window(QWidget):
        def closeEvent(self, event):
            if any(export.is_exporting() for export in exports):
                events.append("deferred")
                event.ignore()
                return
            events.append("closed")
            super().closeEvent(event)

    window = Window()
    exports = [ExportState(window) for _ in attributes]
    for attribute, export in zip(attributes, exports):
        setattr(window, attribute, export)
    watchdog = Mock()
    window.show()

    def exit_during_export():
        app.exit(7)
        for index, export in enumerate(exports):
            QTimer.singleShot(index * 10, export.finish)

    QTimer.singleShot(0, exit_during_export)
    try:
        assert main._run_gui_event_loop(app, window, watchdog) == 7
        assert all(export.cancel_calls == 1 for export in exports)
        assert not any(export.is_exporting() for export in exports)
        assert not window.isVisible()
        assert events[-1] == "closed"
        assert "deferred" in events
        assert not sip.isdeleted(app)
        assert not sip.isdeleted(window)
    finally:
        sip.delete(window)
