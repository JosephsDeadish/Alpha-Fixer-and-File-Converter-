"""Main-window UI wording and menu copy regressions."""

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

try:
    from PyQt6 import sip
    from PyQt6.QtWidgets import QApplication, QMenu
    _QT_GUI_AVAILABLE = True
except (ImportError, OSError, RuntimeError):
    _QT_GUI_AVAILABLE = False


def _qt_gui_smoke_test() -> bool:
    if not _QT_GUI_AVAILABLE:
        return False
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from PyQt6.QtWidgets import QApplication; "
                "from src.core.settings_manager import SettingsManager; "
                "from src.ui.main_window import MainWindow; "
                "app = QApplication([]); "
                "settings = SettingsManager(); "
                "settings._qs.clear(); "
                "window = MainWindow(settings); "
                "print(window._btn_help.text())"
            ),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode == 0


_QT_WINDOW_AVAILABLE = _qt_gui_smoke_test()

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    if not _QT_WINDOW_AVAILABLE:
        pytest.skip("Qt GUI stack unavailable — skipping main-window UX regressions")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path):
    if not _QT_WINDOW_AVAILABLE:
        pytest.skip("Qt GUI stack unavailable — skipping main-window UX regressions")
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        widget = MainWindow(settings)
        yield widget, settings
        widget.close()
        sip.delete(widget)
        settings.sync()
        sip.delete(settings._qs)


def test_main_window_utility_buttons_use_clearer_accessible_copy(window):
    widget, _settings = window
    assert widget._btn_settings.accessibleName() == "Open settings"
    assert "backup/recovery" in widget._btn_settings.toolTip().lower()
    assert "settings dialog" in widget._btn_settings.accessibleDescription().lower()
    assert "backup/recovery" in widget._btn_settings.accessibleDescription().lower()
    assert widget._btn_help.accessibleName() == "Open help menu"
    assert "help menu" in widget._btn_help.toolTip().lower()
    assert "backup/recovery" in widget._btn_help.toolTip().lower()
    assert "backup/recovery" in widget._btn_help.accessibleDescription().lower()
    assert widget._btn_patreon.accessibleName() == "Open Patreon page"
    assert "support development" in widget._btn_patreon.toolTip().lower()


def test_help_menu_actions_use_backup_language(window):
    widget, _settings = window
    captured = []

    def _capture_exec(menu, *_args, **_kwargs):
        captured.append(menu)
        return None

    with patch.object(QMenu, "exec", new=_capture_exec):
        widget._show_help_menu()

    assert captured, "Help menu was not shown"
    actions = [action for action in captured[0].actions() if not action.isSeparator()]
    assert [action.text() for action in actions] == [
        "📚  Tutorial  (how to use this app)",
        "⌨  Keyboard Shortcuts  (F1)",
        "ℹ  About",
        "❤  Support on Patreon…",
        "📤  Export Preferences Backup…",
        "📥  Import Preferences Backup…",
    ]
    assert actions[0].toolTip().endswith("tutorial for this app.")
    assert actions[1].statusTip() == "View keyboard shortcuts for all tools and builders."
    assert "version and feature information" in actions[2].statusTip().lower()
    assert "support page" in actions[3].statusTip().lower()
    assert "recovery backup" in actions[4].toolTip().lower()
    assert "recovery backup" in actions[5].statusTip().lower()
