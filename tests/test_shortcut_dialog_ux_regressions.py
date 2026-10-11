from types import MethodType
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QPushButton,
    QScrollArea, QTableWidget, QWidget,
)

from src.ui.main_window import MainWindow, QMessageBox
from src.ui.theme_engine import PRESET_THEMES, build_stylesheet


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app):
    widget = QWidget()
    widget._settings = MagicMock()
    widget._builder = MagicMock()
    shortcut = QShortcut(QKeySequence("Ctrl+J"), widget)
    widget._shortcut_map = {
        "export": {
            "group": "GIF Builder", "desc": "Export animated GIF with transparency",
            "current": "Ctrl+J", "default": "F5", "sc": shortcut,
            "owner": MagicMock(), "owner_attr": "_builder",
        },
        "load": {
            "group": "Global", "desc": "Load media", "current": "Ctrl+K",
            "default": "Ctrl+K",
        },
    }
    widget._update_shortcut = MethodType(MainWindow._update_shortcut, widget)
    widget.show()
    yield widget
    widget.close()
    sip.delete(widget)
    app.processEvents()


def export_controls(dialog):
    table = dialog.findChild(QTableWidget)
    row = next(i for i in range(table.rowCount())
               if table.item(i, 1).text().startswith("Export"))
    change, reset = table.cellWidget(row, 3).findChildren(QPushButton)
    return table, row, change, reset


@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("theme", ["Panda Dark", "Panda Light"])
def test_shortcut_and_capture_layouts_remain_reachable(app, host, size, pixels, theme):
    host.setStyleSheet(build_stylesheet(PRESET_THEMES[theme])
                      + f"\nQWidget {{ font-size: {pixels}px; }}")
    screen = MagicMock()
    screen.availableGeometry.return_value = QRect(0, 0, *size)
    seen = []

    def inspect(dialog):
        seen.append(dialog.windowTitle())
        dialog.show()
        app.processEvents()
        assert dialog.frameGeometry().width() <= size[0] - 16
        assert dialog.frameGeometry().height() <= size[1] - 16
        scroll = dialog.findChild(QScrollArea, "dialogContentScroll")
        assert scroll is not None
        action = (dialog.findChild(QPushButton) if len(seen) == 2 else
                  dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close))
        assert dialog.rect().contains(action.mapTo(dialog, action.rect().topLeft()))
        assert dialog.rect().contains(action.mapTo(dialog, action.rect().bottomRight()))
        assert action.height() >= action.minimumSizeHint().height()
        if len(seen) == 1:
            table, row, change, reset = export_controls(dialog)
            assert table.accessibleName()
            assert change.accessibleName() != reset.accessibleName()
            assert table.rowHeight(row) >= table.cellWidget(row, 3).minimumSizeHint().height()
            assert table.columnWidth(1) >= table.fontMetrics().horizontalAdvance(table.item(row, 1).text())
            reference = next(label for label in dialog.findChildren(QLabel)
                             if "Select all files in queue" in label.text())
            reference_scroll = reference.parentWidget().parentWidget()
            assert isinstance(reference_scroll, QScrollArea)
            assert reference.height() >= reference.heightForWidth(reference.width())
            reference_scroll.horizontalScrollBar().setValue(
                reference_scroll.horizontalScrollBar().maximum())
            scroll.ensureWidgetVisible(reference_scroll)
            scroll.ensureWidgetVisible(table)
            change.setFocus()
            app.processEvents()
            assert change.hasFocus()
            # Enter on the focused Change must not trigger another row's default button.
            QTest.keyClick(change, Qt.Key.Key_Return)
            assert len(seen) == 2
        else:
            labels = dialog.findChildren(QLabel)
            for label in labels:
                scroll.ensureWidgetVisible(label)
                app.processEvents()
                assert label.height() >= label.heightForWidth(label.width())
            status = next(label for label in labels if label.text() == "Waiting for key…")
            assert status.accessibleName()
            action.setFocus()
            QTest.keyClick(action, Qt.Key.Key_Escape)
            assert dialog.result() == QDialog.DialogCode.Rejected
        dialog.hide()
        return QDialog.DialogCode.Rejected

    with patch.object(QDialog, "screen", return_value=screen), patch.object(QDialog, "exec", inspect):
        MainWindow._show_shortcuts(host)
    assert len(seen) == 2
    host._settings.set_shortcut_binding.assert_not_called()


@pytest.mark.parametrize("key,mods,expected", [
    (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "Return"),
    (Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier, "Enter"),
    (Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier, "Ctrl+L"),
    (Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier, "conflict"),
    (Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, None),
    (Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier, None),
    (Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, None),
])
def test_capture_keyboard_preserves_binding_and_owner_semantics(app, host, key, mods, expected):
    timers = []
    activations = []
    competing = QShortcut(QKeySequence("Ctrl+L"), host)
    competing.setContext(Qt.ShortcutContext.ApplicationShortcut)
    competing.activated.connect(lambda: activations.append("competing"))
    old = host._shortcut_map["export"]["current"]

    def inspect(dialog):
        dialog.show()
        app.processEvents()
        if dialog.windowTitle() == "Press a Key Combination":
            cancel = dialog.findChild(QPushButton)
            cancel.setFocus()
            QTest.keyClick(cancel, key, mods)
            accept = [callback for delay, callback in timers if delay == 400]
            if expected is None:
                assert not accept
                if key != Qt.Key.Key_Escape:
                    assert any(label.text() == "Waiting for key…"
                               for label in dialog.findChildren(QLabel))
                    cancel.click()
                assert dialog.result() == QDialog.DialogCode.Rejected
            else:
                assert len(accept) == 1
                accept[0]()
                assert dialog.result() == QDialog.DialogCode.Accepted
            result = dialog.result()
        else:
            table, row, change, reset = export_controls(dialog)
            change.click()
            assert change.isEnabled() and change.text() == "Change"
            assert table.item(row, 2).text() == (
                old if expected in (None, "conflict") else expected)
            assert reset.isEnabled()
            if expected not in (None, "conflict"):
                reset.setFocus()
                QTest.keyClick(reset, Qt.Key.Key_Return)
                assert table.item(row, 2).text() == "F5"
                assert not reset.isEnabled()
            result = QDialog.DialogCode.Rejected
        dialog.hide()
        return result

    with patch.object(QDialog, "exec", inspect), patch.object(QMessageBox, "warning") as warning, \
            patch("PyQt6.QtCore.QTimer.singleShot", side_effect=lambda delay, cb: timers.append((delay, cb))):
        MainWindow._show_shortcuts(host)
    assert not activations
    info = host._shortcut_map["export"]
    if expected in (None, "conflict"):
        assert info["current"] == old
        assert info["sc"].key() == QKeySequence(old)
        host._settings.set_shortcut_binding.assert_not_called()
        info["owner"].update_shortcut_binding.assert_not_called()
        host._builder.update_shortcut_binding.assert_not_called()
        assert warning.call_count == (1 if expected == "conflict" else 0)
    else:
        assert info["current"] == "F5"
        assert info["sc"].key() == QKeySequence("F5")
        assert host._settings.set_shortcut_binding.call_count == 2
        info["owner"].update_shortcut_binding.assert_any_call("export", expected)
        host._builder.update_shortcut_binding.assert_any_call("export", expected)
