from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QScrollArea, QWidget

from src.ui.main_window import MainWindow, PATREON_URL
from src.ui.theme_engine import PRESET_THEMES, build_stylesheet


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app):
    widget = QWidget()
    widget._shortcut_map = {"help": {"current": "Ctrl+Alt+H"}}
    widget.show()
    yield widget
    widget.close()
    sip.delete(widget)
    app.processEvents()


@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("theme", ["Panda Dark", "Panda Light"])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_about_compact_layout_keyboard_and_focus(app, host, size, pixels, theme, key):
    host.setStyleSheet(build_stylesheet(PRESET_THEMES[theme])
                      + f"\nQWidget {{ font-size: {pixels}px; }}")
    screen = MagicMock()
    available = QRect(170, 90, *size)
    screen.availableGeometry.return_value = available
    observed = {}

    def inspect(dialog):
        dialog.show()
        dialog.activateWindow()
        app.processEvents()
        scroll = dialog.findChild(QScrollArea)
        label = scroll.findChild(QLabel)
        button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        observed.update(
            frame=QRect(dialog.frameGeometry()),
            button_rect=QRect(button.mapTo(dialog, button.rect().topLeft()), button.size()),
            dialog_rect=QRect(dialog.rect()),
            button_height=button.height(),
            button_hint=button.minimumSizeHint().height(),
            label_height=label.height(),
            label_hint=label.heightForWidth(label.width()),
            initial_focus=button.hasFocus(),
            label_accessible=label.accessibleName(),
            scroll_accessible=scroll.accessibleName(),
            horizontal_max=scroll.horizontalScrollBar().maximum(),
        )
        QTest.keyClick(button, Qt.Key.Key_Tab)
        app.processEvents()
        observed["scroll_focus"] = scroll.hasFocus()
        bar = scroll.verticalScrollBar()
        for _ in range(bar.maximum() // max(1, bar.pageStep()) + 2):
            QTest.keyClick(scroll, Qt.Key.Key_PageDown)
        app.processEvents()
        observed["scroll_bottom"] = scroll.verticalScrollBar().value()
        observed["scroll_max"] = scroll.verticalScrollBar().maximum()
        for _ in range(scroll.horizontalScrollBar().maximum() // 20 + 2):
            QTest.keyClick(scroll, Qt.Key.Key_Right)
        observed["scroll_right"] = scroll.horizontalScrollBar().value()
        QTest.keyClick(scroll, Qt.Key.Key_Tab)
        app.processEvents()
        observed["label_focus"] = label.hasFocus()
        button.setFocus()
        QTest.keyClick(button, key)
        observed["result"] = dialog.result()
        observed["closed"] = not dialog.isVisible()
        dialog.hide()
        return dialog.result()

    with patch.object(QWidget, "screen", return_value=screen), patch.object(QDialog, "exec", inspect):
        MainWindow._show_about(host)
    assert available.adjusted(8, 8, -8, -8).contains(observed["frame"])
    assert observed["dialog_rect"].contains(observed["button_rect"])
    assert observed["button_height"] >= observed["button_hint"]
    assert observed["label_height"] >= observed["label_hint"]
    assert observed["initial_focus"] and observed["scroll_focus"] and observed["label_focus"]
    assert observed["label_accessible"] and observed["scroll_accessible"]
    assert observed["scroll_right"] == observed["horizontal_max"]
    assert observed["scroll_max"] > 0
    assert observed["scroll_bottom"] == observed["scroll_max"]
    assert observed["closed"]
    assert observed["result"] == (QDialog.DialogCode.Accepted if key == Qt.Key.Key_Return
                                  else QDialog.DialogCode.Rejected)


@pytest.mark.parametrize("binding,expected", [
    ("Ctrl+Alt+H", "Ctrl+Alt+H"), ("", "unassigned"), ("Ctrl+<", "Ctrl+&lt;"),
])
def test_about_guidance_matches_alpha_model_and_current_shortcuts(host, binding, expected):
    host._shortcut_map["help"]["current"] = binding
    observed = []

    def inspect(dialog):
        observed.extend(label.text() for label in dialog.findChildren(QLabel))
        return QDialog.DialogCode.Rejected

    with patch.object(QDialog, "exec", inspect):
        MainWindow._show_about(host)
    content = "\n".join(observed)
    assert f"current key: {expected}" in content
    assert "Keyboard shortcuts are configurable" in content
    assert "No Alpha, Max Alpha" not in content
    assert "set / multiply / add / subtract" not in content
    assert "N64 Binary Alpha" in content and "Force Transparent" in content
    assert "custom alpha range" in content
    assert "F5 run" not in content and "F1 shortcuts" not in content


@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("theme", ["Panda Dark", "Panda Light"])
def test_about_keyboard_support_link_stays_visible(app, host, size, pixels, theme):
    host.setStyleSheet(build_stylesheet(PRESET_THEMES[theme])
                      + f"\nQWidget {{ font-size: {pixels}px; }}")
    screen = MagicMock()
    screen.availableGeometry.return_value = QRect(170, 90, *size)
    observed = {}

    def inspect(dialog):
        dialog.show()
        dialog.activateWindow()
        app.processEvents()
        scroll = dialog.findChild(QScrollArea)
        label = scroll.findChild(QLabel)
        button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        observed["external_links"] = label.openExternalLinks()
        label.setOpenExternalLinks(False)
        links = []
        label.linkActivated.connect(links.append)
        # The first label stop selects text; the next stop selects its support anchor.
        for _ in range(3):
            QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
            app.processEvents()
        observed["link_focus"] = label.hasFocus()
        observed["bottom"] = scroll.verticalScrollBar().value()
        observed["maximum"] = scroll.verticalScrollBar().maximum()
        QTest.keyClick(label, Qt.Key.Key_Return)
        observed["links"] = links
        observed["link_keeps_open"] = dialog.isVisible()
        QTest.keyClick(label, Qt.Key.Key_Tab)
        app.processEvents()
        observed["footer_focus"] = button.hasFocus()
        scroll.verticalScrollBar().setValue(0)
        QTest.keyClick(button, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        app.processEvents()
        QTest.keyClick(label, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        app.processEvents()
        observed["reverse_focus"] = label.hasFocus()
        observed["reverse_bottom"] = scroll.verticalScrollBar().value()
        QTest.keyClick(label, Qt.Key.Key_Return)
        QTest.keyClick(label, Qt.Key.Key_Escape)
        observed["closed"] = not dialog.isVisible()
        dialog.hide()
        return dialog.result()

    with patch.object(QWidget, "screen", return_value=screen), patch.object(QDialog, "exec", inspect):
        MainWindow._show_about(host)
    assert observed["link_focus"] and observed["footer_focus"] and observed["reverse_focus"]
    assert observed["bottom"] == observed["maximum"]
    assert observed["reverse_bottom"] == observed["maximum"]
    assert observed["external_links"]
    assert observed["links"] == [PATREON_URL, PATREON_URL]
    assert observed["link_keeps_open"] and observed["closed"]
