from unittest.mock import Mock, patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication, QMainWindow

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow, _SCREEN_CHANGE_INTERNAL


class GeometryWindow(MainWindow):
    """Real Qt window exercising geometry without unrelated media/effect setup."""

    def __init__(self):
        QMainWindow.__init__(self)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self._settings = {}

    def resizeEvent(self, event):
        QMainWindow.resizeEvent(self, event)

    def changeEvent(self, event):
        QMainWindow.changeEvent(self, event)

    def closeEvent(self, event):
        QMainWindow.closeEvent(self, event)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    widget = GeometryWindow()
    yield widget
    widget.hide()
    sip.delete(widget)


def screens_for(rectangles):
    screens = []
    for rectangle in rectangles:
        screen = Mock(spec=QScreen)
        screen.availableGeometry.return_value = QRect(*rectangle)
        screen.devicePixelRatio.return_value = 1.0
        screens.append(screen)
    return screens


GEOMETRY_CASES = [
    pytest.param(
        [(0, 0, 1280, 760), (1280, 0, 2560, 1440)],
        (1600, 100, 1800, 1000), (1600, 100, 1800, 1000), 1,
        id="secondary-larger-than-primary",
    ),
    pytest.param(
        [(0, 0, 1920, 1040), (1920, 40, 1000, 720)],
        (2120, 100, 1200, 900), (1920, 40, 1000, 720), 1,
        id="secondary-smaller-than-primary",
    ),
    pytest.param(
        [(0, 0, 1920, 1040), (-1600, -200, 1600, 1000)],
        (-1500, -100, 1000, 700), (-1500, -100, 1000, 700), 1,
        id="negative-secondary-coordinates",
    ),
    pytest.param(
        [(40, 60, 1280, 760)],
        (4000, 500, 1000, 700), (180, 90, 1000, 700), 0,
        id="detached-monitor-centred-on-primary",
    ),
    pytest.param(
        [(0, 0, 1280, 900), (1280, 0, 2000, 1200)],
        (1000, 100, 1000, 700), (1280, 100, 1000, 700), 1,
        id="greatest-overlap-secondary",
    ),
    pytest.param(
        [(0, 0, 1280, 900), (1280, 0, 2000, 1200)],
        (200, 100, 1200, 700), (80, 100, 1200, 700), 0,
        id="greatest-overlap-primary",
    ),
    pytest.param(
        [(0, 0, 1600, 900), (0, 900, 1600, 1200)],
        (100, 850, 1000, 800), (100, 900, 1000, 800), 1,
        id="body-overlap-not-title-strip",
    ),
    pytest.param(
        [(0, 0, 1200, 900), (1200, 0, 1200, 900)],
        (700, 100, 1000, 700), (200, 100, 1000, 700), 0,
        id="equal-overlap-primary-tie",
    ),
    pytest.param(
        [(0, 0, 1920, 1040)],
        (-100, -40, 1000, 700), (0, 0, 1000, 700), 0,
        id="partial-top-left-overhang",
    ),
    pytest.param(
        [(100, 80, 1400, 900)],
        (900, 500, 1000, 700), (500, 280, 1000, 700), 0,
        id="partial-bottom-right-taskbar-offset",
    ),
    pytest.param(
        [(0, 0, 1920, 1040), (-1000, 80, 800, 600)],
        (-950, 100, 1100, 800), (-1000, 80, 800, 600), 1,
        id="oversized-secondary-old-minimum",
    ),
    pytest.param(
        [(30, 50, 480, 360)],
        (100, 100, 1200, 900), (30, 50, 480, 360), 0,
        id="screen-smaller-than-minimum-floors",
    ),
    pytest.param(
        [(0, 0, 1920, 1040)],
        (100, 100, 100, 100), (100, 100, 900, 700), 0,
        id="saved-size-below-design-minimum",
    ),
]


@pytest.mark.parametrize("mode", ["restore", "live"])
@pytest.mark.parametrize("rectangles,saved,expected,selected", GEOMETRY_CASES)
def test_restore_and_live_clamp_share_monitor_policy(
    window, mode, rectangles, saved, expected, selected,
):
    screens = screens_for(rectangles)
    # Qt's current screen may lag behind a move/topology change.
    with patch.object(QApplication, "screens", return_value=screens), \
            patch.object(QApplication, "primaryScreen", return_value=screens[0]), \
            patch.object(window, "screen", return_value=screens[0]):
        if mode == "restore":
            window.setMinimumSize(900, 700)
            window._settings.update(zip(
                ["window_x", "window_y", "window_w", "window_h"], saved,
            ))
            window._restore_geometry()
        else:
            window.setGeometry(QRect(*saved))
            if saved[2] >= 900 and saved[3] >= 700:
                window.setMinimumSize(900, 700)
            window._clamp_to_screen()
        assert window.geometry() == QRect(*expected)
        assert screens[selected].availableGeometry().contains(window.frameGeometry())
        # Minimum recomputation must not resize a newly fitted secondary window.
        window._update_minimum_size()
        window._clamp_to_screen()
        assert window.geometry() == QRect(*expected)


@pytest.mark.parametrize("primary_index", [None, 0, 1])
@pytest.mark.parametrize("overlap", [False, True])
def test_monitor_selection_fallback_and_ties(window, primary_index, overlap):
    screens = screens_for([(0, 0, 1200, 900), (1200, 0, 1200, 900)])
    primary = screens[primary_index] if primary_index is not None else None
    geometry = QRect(700, 100, 1000, 700) if overlap else QRect(5000, 100, 1000, 700)
    with patch.object(QApplication, "screens", return_value=screens), \
            patch.object(QApplication, "primaryScreen", return_value=primary):
        selected, on_screen = window._screen_for_geometry(geometry)
        assert selected is (primary or screens[0])
        assert on_screen is overlap
        window.setGeometry(geometry)
        window._clamp_to_screen()
        assert selected.availableGeometry().contains(window.frameGeometry())


@pytest.mark.parametrize("mode", ["restore", "live"])
@pytest.mark.parametrize("margins", [(4, 32, 4, 4), (8, 48, 8, 8)])
def test_decorated_geometry_uses_client_coordinates(window, mode, margins):
    screens = screens_for([(0, 0, 1280, 760), (-1600, 40, 1600, 1000)])
    saved = (-1599, 41, 1800, 1200)
    left, top, right, bottom = margins
    with patch.object(QApplication, "screens", return_value=screens), \
            patch.object(QApplication, "primaryScreen", return_value=screens[0]), \
            patch.object(window, "_window_frame_margins", return_value=margins):
        window.setGeometry(QRect(*saved))
        window.setMinimumSize(900, 700)
        window._settings.update(zip(
            ["window_x", "window_y", "window_w", "window_h"], saved,
        ))
        (window._restore_geometry if mode == "restore" else window._clamp_to_screen)()
        assert window.geometry() == QRect(
            -1600 + left, 40 + top, 1600 - left - right, 1000 - top - bottom,
        )
        assert screens[1].availableGeometry().contains(
            window.geometry().adjusted(-left, -top, right, bottom),
        )


@pytest.mark.parametrize("mode", ["restore", "live"])
@pytest.mark.parametrize("state", [Qt.WindowState.WindowMaximized, Qt.WindowState.WindowFullScreen])
def test_normal_geometry_operations_leave_window_states_untouched(window, mode, state):
    window.setGeometry(100, 100, 1000, 700)
    window.setWindowState(state)
    geometry = window.geometry()
    with patch.object(QApplication, "screens") as screens, \
            patch.object(window, "setGeometry", wraps=window.setGeometry) as setter:
        (window._restore_geometry if mode == "restore" else window._clamp_to_screen)()
        screens.assert_not_called()
        setter.assert_not_called()
        assert window.geometry() == geometry
        assert window.windowState() == state


def test_saved_maximized_state_is_restored(window):
    window._settings["window_maximized"] = True
    with patch.object(QApplication, "screens") as screens:
        window._restore_geometry()
        assert window.isMaximized()
        screens.assert_not_called()


@pytest.mark.parametrize("mode", ["restore", "live"])
def test_no_screens_preserve_requested_geometry(window, mode):
    saved = (-1500, -100, 1000, 700)
    window.setGeometry(QRect(*saved))
    window._settings.update(zip(
        ["window_x", "window_y", "window_w", "window_h"], saved,
    ))
    with patch.object(QApplication, "screens", return_value=[]), \
            patch.object(QApplication, "primaryScreen", return_value=None):
        (window._restore_geometry if mode == "restore" else window._clamp_to_screen)()
        assert window.geometry() == QRect(*saved)


def test_topology_and_dpi_callbacks_fit_before_old_minimum_can_overflow(window):
    screens = screens_for([(25, 45, 480, 360)])
    window.setGeometry(2000, 100, 1000, 700)
    window.setMinimumSize(900, 700)
    window._apply_font_size = Mock()
    with patch.object(QApplication, "screens", return_value=screens), \
            patch.object(QApplication, "primaryScreen", return_value=screens[0]), \
            patch("src.ui.main_window.QTimer.singleShot", side_effect=lambda delay, fn: fn()):
        window._on_screens_changed()
        assert window.geometry() == QRect(25, 45, 480, 360)
        window.setGeometry(2000, 100, 1000, 700)
        MainWindow.changeEvent(window, QEvent(_SCREEN_CHANGE_INTERNAL))
        assert window.geometry() == QRect(25, 45, 480, 360)
        assert window._apply_font_size.call_count == 2


def test_native_main_window_initial_show_accounts_for_frame_margins(app, tmp_path):
    screens = screens_for([(20, 40, 1100, 800)])
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        settings.set("window_x", 5000)
        settings.set("window_y", 5000)
        settings.set("window_w", 1200)
        settings.set("window_h", 900)
    original_stylesheet = app.styleSheet()
    with patch.object(QApplication, "screens", return_value=screens), \
            patch.object(QApplication, "primaryScreen", return_value=screens[0]):
        widget = MainWindow(settings)
        try:
            widget.show()
            app.processEvents()
            assert screens[0].availableGeometry().contains(widget.frameGeometry())
            assert widget.minimumWidth() <= widget.width()
            assert widget.minimumHeight() <= widget.height()
            before = widget.geometry()
            widget._clamp_to_screen()
            assert widget.geometry() == before
        finally:
            widget.close()
            sip.delete(widget)
            settings.sync()
            sip.delete(settings._qs)
            app.processEvents()
            app.setStyleSheet(original_stylesheet)
