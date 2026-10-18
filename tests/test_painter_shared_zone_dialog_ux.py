from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest
from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialogButtonBox, QLabel, QPushButton, QWidget,
)

from src.ui.selective_alpha_tool import SelectiveAlphaTool
from src.ui.theme_engine import PRESET_THEMES, build_stylesheet


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("theme", ["Panda Dark", "Panda Light"])
@pytest.mark.parametrize("cancel", [False, True])
def test_shared_zone_picker_fits_and_keyboard_preserves_selection(app, size, pixels, theme, cancel):
    host = QWidget()
    host.setStyleSheet(build_stylesheet(PRESET_THEMES[theme])
                      + f"\nQWidget {{ font-size: {pixels}px; }}")
    host._shared_zones = [(64, np.array([[True, False]])),
                          (128, np.array([[False, True]]))]
    original = np.array([[123]], dtype=np.uint8)
    host._mask_clipboard = original
    host._ze_paste_btn = QPushButton(host)
    host._ze_paste_btn.setEnabled(False)
    host._import_shared_status = QLabel("unchanged", host)
    host._sound = None
    host._refresh_session_status = Mock()
    host.show()
    screen = MagicMock()
    available = QRect(170, 90, *size)
    screen.availableGeometry.return_value = available
    observed = {}
    errors = []

    def inspect():
        dialog = app.activeModalWidget()
        try:
            dialog.activateWindow()
            app.processEvents()
            observed["frame"] = QRect(dialog.frameGeometry())
            combo = dialog.findChild(QComboBox)
            label = dialog.findChild(QLabel)
            observed["label_height"] = label.height()
            observed["label_hint"] = label.heightForWidth(label.width())
            observed["combo_rect"] = QRect(combo.mapTo(dialog, QPoint()), combo.size())
            observed["dialog_rect"] = QRect(dialog.rect())
            buttons = dialog.findChild(QDialogButtonBox)
            observed["buttons_fit"] = all(
                dialog.rect().contains(QRect(b.mapTo(dialog, QPoint()), b.size()))
                and b.height() >= b.minimumSizeHint().height()
                for b in buttons.buttons()
            )
            combo.setFocus()
            QTest.keyClick(combo, Qt.Key.Key_Down)
            observed["selected"] = combo.currentIndex()
            for _ in range(4):
                QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
                if app.focusWidget() in buttons.buttons():
                    break
            observed["footer_focus"] = app.focusWidget() in buttons.buttons()
            QTest.keyClick(dialog, Qt.Key.Key_Escape if cancel else Qt.Key.Key_Return)
            observed["closed"] = not dialog.isVisible()
        except Exception as exc:
            errors.append(exc)
        finally:
            if dialog.isVisible():
                dialog.reject()

    timer = QTimer(host)
    timer.setSingleShot(True)
    timer.timeout.connect(inspect)
    try:
        with patch.object(QWidget, "screen", return_value=screen):
            timer.start(0)
            SelectiveAlphaTool._on_import_zone_to_clipboard(host)
        assert not errors, errors
        assert available.adjusted(8, 8, -8, -8).contains(observed["frame"])
        assert observed["dialog_rect"].contains(observed["combo_rect"])
        assert observed["label_height"] >= observed["label_hint"]
        assert observed["buttons_fit"] and observed["footer_focus"] and observed["closed"]
        assert observed["selected"] == 1
        if cancel:
            assert host._mask_clipboard is original
            assert host._import_shared_status.text() == "unchanged"
            assert not host._ze_paste_btn.isEnabled()
            host._refresh_session_status.assert_not_called()
        else:
            np.testing.assert_array_equal(host._mask_clipboard, [[0, 255]])
            assert host._ze_paste_btn.isEnabled()
            assert "Zone 2 copied" in host._import_shared_status.text()
            host._refresh_session_status.assert_called_once_with()
    finally:
        host.close()
        sip.delete(host)
        app.processEvents()
