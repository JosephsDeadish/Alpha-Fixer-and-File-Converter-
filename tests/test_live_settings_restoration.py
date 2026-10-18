"""Bounded restoration tests using real tools, without native MainWindow effects."""
import json
from types import MethodType
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow
from src.ui.selective_alpha_tool import SelectiveAlphaTool
from src.ui.settings_dialog import SettingsDialog


@pytest.fixture
def live_session(tmp_path):
    app = QApplication.instance() or QApplication([])
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        owner = QWidget()
        painter = SelectiveAlphaTool(settings, parent=owner)
        host = owner
        host._settings = settings
        host._shortcut_map = {}
        host._click_effects = None
        host._painter_dialog = painter
        for name in ("_apply_shortcut_settings", "_on_settings_changed",
                     "_apply_settings_now", "_register_shortcut_provider"):
            setattr(host, name, MethodType(getattr(MainWindow, name), host))
        for name in ("_apply_theme", "_apply_cursor", "_apply_font_size",
                     "_apply_theme_effect", "_apply_trail", "_apply_button_anim",
                     "_apply_bg_drip", "_apply_bg_flock", "_apply_bg_ambient",
                     "_apply_hold_effects"):
            setattr(host, name, Mock())
        host._settings_apply_timer = QTimer(owner)
        host._settings_apply_timer.setSingleShot(True)
        host._settings_apply_timer.timeout.connect(host._apply_settings_now)
        shortcut = QShortcut(QKeySequence("F1"), owner)
        host._shortcut_map["help"] = {
            "sc": shortcut, "default": "F1", "current": "F1",
        }
        host._register_shortcut_provider(painter)
        yield app, settings, host, painter, shortcut
        host._settings_apply_timer.stop()
        painter.close()
        sip.delete(owner)
        settings.sync()
        sip.delete(settings._qs)


@pytest.mark.parametrize("restore", ["import", "reset"])
def test_restore_updates_live_shortcuts_without_reloading_media(
        live_session, tmp_path, restore):
    _, settings, host, painter, shortcut = live_session
    source = tmp_path / "source.png"
    with Image.new("RGBA", (8, 8), (30, 60, 90, 255)) as image:
        image.save(source)
    assert painter._canvas.load_image(str(source))
    masks = painter._canvas.get_all_masks()
    masks[0][2:5, 2:5] = 1
    assert painter._canvas.set_all_masks(masks)
    painter._ze_alpha_spin.setValue(64)
    painter._brush_spin.setValue(17)
    before_masks = painter._canvas.get_all_masks()
    before_image = painter._canvas._src_img
    before_undo = painter._canvas._undo_stack

    if restore == "import":
        backup = tmp_path / "preferences.json"
        backup.write_text(json.dumps({
            "custom_shortcuts": json.dumps({
                "help": "Ctrl+Alt+H", "sa_save": "Ctrl+Alt+S",
            }),
            "sa_zone_alphas": json.dumps([200] * len(masks)),
            "sa_brush_size": 3,
        }), encoding="utf-8")
        with patch("src.ui.main_window.QFileDialog.getOpenFileName",
                   return_value=(str(backup), "")), \
             patch.object(QMessageBox, "information"):
            MainWindow._import_settings(host)
        expected_help, expected_save = "Ctrl+Alt+H", "Ctrl+Alt+S"
    else:
        settings.set_shortcut_binding("help", "Ctrl+Alt+H", "F1")
        settings.set_shortcut_binding("sa_save", "Ctrl+Alt+S", "Ctrl+S")
        shortcut.setKey(QKeySequence("Ctrl+Alt+H"))
        host._shortcut_map["help"]["current"] = "Ctrl+Alt+H"
        painter.update_shortcut_binding("sa_save", "Ctrl+Alt+S")
        host._shortcut_map["sa_save"]["current"] = "Ctrl+Alt+S"
        assert shortcut.key().toString() == "Ctrl+Alt+H"
        dialog = SettingsDialog(settings)
        dialog.settings_changed.connect(host._on_settings_changed)
        try:
            with patch.object(QMessageBox, "question",
                              return_value=QMessageBox.StandardButton.Yes), \
                 patch.object(QMessageBox, "information"):
                dialog._reset_all_settings()
        finally:
            sip.delete(dialog)
        expected_help, expected_save = "F1", "Ctrl+S"

    assert host._settings_apply_timer.isActive()
    host._settings_apply_timer.stop()
    with patch.object(settings, "set_shortcut_binding") as write:
        host._apply_settings_now()
        host._apply_settings_now()
    write.assert_not_called()
    assert shortcut.key().toString() == expected_help
    assert painter._shortcut_objects["sa_save"].key().toString() == expected_save
    assert host._shortcut_map["help"]["current"] == expected_help
    assert host._shortcut_map["sa_save"]["current"] == expected_save
    assert painter._canvas._src_img is before_image
    assert painter._canvas._undo_stack is before_undo
    for before, after in zip(before_masks, painter._canvas.get_all_masks()):
        np.testing.assert_array_equal(before, after)
    assert painter._ze_alpha_spin.value() == 64
    assert painter._brush_spin.value() == 17


def test_restoration_updates_open_lazy_provider_and_keeps_closed_provider_metadata(
        live_session):
    _, settings, host, painter, _ = live_session
    host._register_shortcut_provider(painter, "_painter_dialog")
    settings.set_shortcut_binding("sa_save", "Ctrl+Alt+S", "Ctrl+S")
    host._apply_settings_now()
    assert painter._shortcut_objects["sa_save"].key().toString() == "Ctrl+Alt+S"
    host._painter_dialog = None
    settings.reset_all()
    host._apply_settings_now()
    assert host._shortcut_map["sa_save"]["current"] == "Ctrl+S"
