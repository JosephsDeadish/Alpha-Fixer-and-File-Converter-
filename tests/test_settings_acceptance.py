"""Disk-backed settings lifecycle acceptance, independent of user preferences."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QSettings
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.settings_manager import SettingsManager
from src.ui.settings_dialog import SettingsDialog, _background_file_filter


@pytest.fixture
def settings_session(tmp_path):
    app = QApplication.instance() or QApplication([])
    font, stylesheet = app.font(), app.styleSheet()
    managers, widgets = [], []
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        def launch():
            manager = SettingsManager()
            managers.append(manager)
            return manager

        def open_settings(manager, parent=None):
            widget = SettingsDialog(manager, parent)
            widgets.append(widget)
            return widget

        yield app, launch, open_settings, widgets
    for widget in reversed(widgets):
        if not sip.isdeleted(widget):
            widget.close()
            sip.delete(widget)
    for manager in managers:
        manager.sync()
        sip.delete(manager._qs)
    app.setFont(font)
    app.setStyleSheet(stylesheet)


_HISTORY_CONTROLS = [
    ("converter", "_history_max_conv_spin", "_chk_track_converter"),
    ("alpha", "_history_max_alpha_spin", "_chk_track_alpha"),
    ("selective_alpha", "_history_max_sel_spin", "_chk_track_sel_alpha"),
    ("gif_builder", "_history_max_gif_spin", "_chk_track_gif"),
    ("video_builder", "_history_max_video_spin", "_chk_track_video"),
]


def test_custom_background_picker_covers_supported_image_and_video_formats():
    flt = _background_file_filter()
    assert "All Supported Media" in flt
    assert "Images (" in flt
    assert "Videos (" in flt
    assert "*.avif" in flt
    assert "*.webm" in flt
    assert "*.mp4" in flt


def test_custom_background_path_edit_accepts_local_file_drops():
    from src.ui.settings_dialog import _BackgroundPathEdit

    class _Url:
        def __init__(self, path: str):
            self._path = path
        def isLocalFile(self):
            return True
        def toLocalFile(self):
            return self._path

    class _Mime:
        def __init__(self, path: str):
            self._url = _Url(path)
        def hasUrls(self):
            return True
        def urls(self):
            return [self._url]

    class _Event:
        def __init__(self, path: str):
            self._mime = _Mime(path)
            self.accepted = False
            self.ignored = False
        def mimeData(self):
            return self._mime
        def acceptProposedAction(self):
            self.accepted = True
        def ignore(self):
            self.ignored = True

    edit = _BackgroundPathEdit()
    try:
        event = _Event("/tmp/background.webm")
        edit.dropEvent(event)
        assert edit.text() == "/tmp/background.webm"
        assert event.accepted
        assert not event.ignored
    finally:
        edit.deleteLater()


def test_custom_background_path_clear_button_empties_the_field(app, dialog):
    widget, _ = dialog
    widget._custom_bg_check.setChecked(True)
    widget._custom_bg_path_edit.setText("/tmp/background.png")
    assert widget._custom_bg_path_edit.text() == "/tmp/background.png"
    widget._custom_bg_path_edit.clear()
    app.processEvents()
    assert widget._custom_bg_path_edit.text() == ""
    assert "Choose an image" in widget._custom_bg_status_lbl.text()


def test_custom_background_preview_updates_for_image_files(app, dialog, tmp_path):
    from PyQt6.QtGui import QColor, QImage

    widget, _ = dialog
    preview_path = tmp_path / "background.png"
    image = QImage(12, 12, QImage.Format.Format_ARGB32)
    image.fill(QColor("#336699"))
    assert image.save(str(preview_path))

    widget._custom_bg_check.setChecked(True)
    widget._custom_bg_path_edit.setText(str(preview_path))
    app.processEvents()

    assert not widget._custom_bg_preview.isHidden()
    assert not widget._custom_bg_preview.pixmap().isNull()
    assert "background.png" in widget._custom_bg_status_lbl.text()


def test_custom_background_preview_rescales_when_resized(app, dialog, tmp_path):
    from PyQt6.QtGui import QColor, QImage

    widget, _ = dialog
    preview_path = tmp_path / "background.png"
    image = QImage(40, 20, QImage.Format.Format_ARGB32)
    image.fill(QColor("#663399"))
    assert image.save(str(preview_path))

    widget._custom_bg_check.setChecked(True)
    widget._custom_bg_path_edit.setText(str(preview_path))
    app.processEvents()
    first_size = widget._custom_bg_preview.pixmap().size()

    widget._custom_bg_preview.resize(120, 220)
    app.processEvents()
    second_size = widget._custom_bg_preview.pixmap().size()

    assert not first_size.isEmpty()
    assert not second_size.isEmpty()
    assert first_size != second_size


def test_custom_background_preview_plays_gif_backgrounds(app, dialog, tmp_path):
    from PIL import Image
    from PyQt6.QtGui import QMovie

    widget, _ = dialog
    preview_path = tmp_path / "background.gif"
    frames = [
        Image.new("RGBA", (24, 12), color)
        for color in ("#ff3366", "#3366ff")
    ]
    frames[0].save(
        preview_path,
        save_all=True,
        append_images=frames[1:],
        duration=50,
        loop=0,
    )

    widget._custom_bg_check.setChecked(True)
    widget._custom_bg_path_edit.setText(str(preview_path))
    app.processEvents()

    movie = widget._custom_bg_preview.movie()
    assert movie is not None
    assert isinstance(movie, QMovie)
    assert movie.state() == QMovie.MovieState.Running
    original_size = movie.scaledSize()

    widget._custom_bg_preview.resize(180, 90)
    app.processEvents()
    assert movie.scaledSize() != original_size


def test_custom_background_group_accepts_dragged_local_files(dialog):
    widget, _ = dialog

    class _Url:
        def __init__(self, path: str):
            self._path = path
        def isLocalFile(self):
            return True
        def toLocalFile(self):
            return self._path

    class _Mime:
        def __init__(self, path: str):
            self._url = _Url(path)
        def hasUrls(self):
            return True
        def urls(self):
            return [self._url]

    class _Event:
        def __init__(self, path: str):
            self._mime = _Mime(path)
            self.accepted = False
        def type(self):
            from PyQt6.QtCore import QEvent
            return QEvent.Type.Drop
        def mimeData(self):
            return self._mime
        def acceptProposedAction(self):
            self.accepted = True

    event = _Event("/tmp/background.webm")
    handled = widget.eventFilter(widget._custom_bg_group, event)
    assert handled is True
    assert event.accepted
    assert widget._custom_bg_check.isChecked()
    assert not widget._use_theme_bg_check.isChecked()
    assert widget._custom_bg_path_edit.text() == "/tmp/background.webm"


@pytest.mark.parametrize("tool,limit_control,track_control", _HISTORY_CONTROLS)
def test_opening_existing_ini_does_not_rewrite_legacy_history_policy(
        settings_session, tool, limit_control, track_control):
    _, launch, open_settings, _ = settings_session
    manager = launch()
    assert isinstance(manager._qs, QSettings)
    limit_key, track_key = f"history_max_entries_{tool}", f"history_track_{tool}"
    manager.set(limit_key, "6000")
    manager.set(track_key, "no")
    manager.set("future_setting", "keep")
    manager.sync()
    path = Path(manager._qs.fileName())
    before = path.read_bytes()

    with patch.object(manager, "set", wraps=manager.set) as write:
        widget = open_settings(manager)
        widget._load_values()
        widget.close()
    write.assert_not_called()
    manager.sync()
    assert path.read_bytes() == before
    assert manager.get(limit_key, 0) == 6000
    assert getattr(widget, limit_control).value() == 5000
    assert not getattr(widget, track_control).isChecked()
    assert manager.get(track_key, True) is False
    assert manager.get("future_setting") == "keep"


@pytest.mark.parametrize("legacy", [False, True])
def test_change_export_reset_import_relaunch_preferences_and_live_appearance(
        settings_session, tmp_path, legacy):
    from src.ui.main_window import MainWindow

    app, launch, open_settings, widgets = settings_session
    manager = launch()
    assert manager._qs.allKeys() == []
    window = MainWindow(manager)
    widgets.append(window)
    widget = open_settings(manager, window)
    widget.settings_changed.connect(window._on_settings_changed)
    widget.theme_changed.connect(lambda _: window._on_settings_changed())
    widget.show()

    widget._theme_preset_combo.setCurrentText("Bat Cave")
    widget._font_size_spin.setValue(12)
    widget._ui_scale_combo.setCurrentIndex(3)  # Large
    widget._btn_height_combo.setCurrentIndex(2)  # Comfortable
    widget._widget_spacing_combo.setCurrentIndex(0)  # Tight
    widget._border_radius_combo.setCurrentIndex(3)  # Rounded
    widget._panel_padding_combo.setCurrentIndex(2)  # Spacious
    widget._click_effects_theme_check.setChecked(True)
    widget._use_theme_effect_check.setChecked(False)
    widget._hold_effects_check.setChecked(True)
    widget._use_theme_hold_check.setChecked(False)
    widget._hold_key_combo.setCurrentIndex(widget._hold_key_combo.findData("blood"))
    widget._history_max_spin.setValue(20)
    for _, control, _ in _HISTORY_CONTROLS:
        getattr(widget, control).setValue(3)
    widget._chk_track_gif.setChecked(False)
    assert window._update_shortcut("help", "Ctrl+Alt+H")
    assert window._update_shortcut("converter_run", "Ctrl+Alt+C")
    widget.close()  # Flush the real dialog's pending theme/combo changes.
    assert window._settings_apply_timer.isActive()
    QTest.qWait(550)
    assert manager.get_theme()["name"] == "Bat Cave"
    assert app.font().pointSize() == 14
    assert window._click_effects._enabled
    assert window._click_effects._effect_key == "bat"
    assert "border-radius" in app.styleSheet()
    expected = {key: manager.get(key) for key in manager.EXPORT_KEYS}
    histories = {}
    for tool, _, _ in _HISTORY_CONTROLS:
        key = f"{tool}_history"
        histories[key] = json.dumps([{"file": f"{tool}.png", "status": "completed"}])
        manager.set(key, histories[key])
    manager.sync()
    backup = tmp_path / "preferences.json"
    manager.export_settings(str(backup))
    payload = json.loads(backup.read_text(encoding="utf-8"))
    assert not any(key in payload for key in histories)
    if legacy:
        for key, value in payload.items():
            if type(value) is bool:
                payload[key] = "yes" if value else "no"
            elif type(value) is int:
                payload[key] = str(value)
        payload["future_setting"] = {"ignored": True}
        backup.write_text(json.dumps(payload), encoding="utf-8")

    manager.reset_all()
    assert manager.get_theme()["name"] == "Panda Dark"
    assert all(manager.get(key) is None for key in histories)
    window._on_settings_changed()
    QTest.qWait(550)
    assert app.font().pointSize() == 10
    assert not window._click_effects._enabled
    with patch("src.ui.main_window.QFileDialog.getOpenFileName",
               return_value=(str(backup), "")), \
         patch("src.ui.main_window.QMessageBox.information") as notice:
        window._import_settings()
    assert "Restart" in notice.call_args.args[2]
    assert window._settings_apply_timer.isActive()
    QTest.qWait(550)
    assert app.font().pointSize() == 14
    assert window._click_effects._enabled
    # Import merges recognized preferences; histories/unlocks are not backups.
    assert {key: manager.get(key) for key in manager.EXPORT_KEYS} == expected
    assert manager.get("future_setting") is None
    window.close()

    reloaded = launch()
    reopened = open_settings(reloaded)
    assert reopened._theme_preset_combo.currentText() == "Bat Cave"
    assert reopened._font_size_spin.value() == 12
    assert reopened._ui_scale_combo.currentText().startswith("Large")
    assert reopened._btn_height_combo.currentIndex() == 2
    assert reopened._widget_spacing_combo.currentIndex() == 0
    assert reopened._border_radius_combo.currentIndex() == 3
    assert reopened._panel_padding_combo.currentIndex() == 2
    assert reopened._click_effects_theme_check.isChecked()
    assert reopened._hold_key_combo.currentData() == "blood"
    for _, control, _ in _HISTORY_CONTROLS:
        assert getattr(reopened, control).value() == 3
    assert not reopened._chk_track_gif.isChecked()
    assert reloaded.get_custom_shortcuts()["help"] == "Ctrl+Alt+H"
    assert reloaded.get_custom_shortcuts()["converter_run"] == "Ctrl+Alt+C"
    restarted = MainWindow(reloaded)
    widgets.append(restarted)
    assert restarted._shortcut_map["help"]["sc"].key().toString() == "Ctrl+Alt+H"
    assert restarted._converter_tab._shortcut_objects["converter_run"].key().toString() == "Ctrl+Alt+C"
    assert app.font().pointSize() == 14


@pytest.mark.parametrize("reset_all", [False, True])
def test_confirmed_dialog_resets_survive_relaunch_with_intended_scope(
        settings_session, reset_all):
    _, launch, open_settings, _ = settings_session
    manager = launch()
    manager.set("font_size", 18)
    manager.set("custom_shortcuts", json.dumps({"help": "Ctrl+Alt+H"}))
    histories = {}
    for tool, _, _ in _HISTORY_CONTROLS:
        histories[f"{tool}_history"] = json.dumps([{"file": f"{tool}.png"}])
    for key, value in histories.items():
        manager.set(key, value)
    progress = [key for key in manager._DEFAULTS
                if key.startswith("unlock_") or key.endswith("_once")
                or key in ("total_clicks", "alpha_fixes_total", "conversions_total")]
    for key in progress:
        manager.set(key, 42 if type(manager._DEFAULTS[key]) is int else True)
    manager.sync()
    widget = open_settings(manager)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
         patch.object(QMessageBox, "information"):
        if reset_all:
            widget._reset_all_settings()
        else:
            widget._reset_unlocks_only()
    widget.close()
    reloaded = launch()
    reopened = open_settings(reloaded)
    assert all(reloaded.get(key) == reloaded._DEFAULTS[key] for key in progress)
    assert reopened._font_size_spin.value() == (10 if reset_all else 18)
    assert reloaded.get_custom_shortcuts() == ({} if reset_all else {"help": "Ctrl+Alt+H"})
    for key, value in histories.items():
        assert reloaded.get(key) == (None if reset_all else value)


def test_invalid_backup_preserves_every_ini_key_and_disk_after_relaunch(
        settings_session, tmp_path):
    _, launch, open_settings, _ = settings_session
    manager = launch()
    manager.set("font_size", "12")
    manager.set("unlock_skeleton", True)
    manager.set("converter_history", '[{"file": "retained.png"}]')
    manager.set("unknown_future_key", "retain")
    manager.sync()
    disk = Path(manager._qs.fileName())
    before_disk = disk.read_bytes()
    before = {key: manager._qs.value(key) for key in manager._qs.allKeys()}
    backup = tmp_path / "invalid.json"
    backup.write_text(json.dumps({
        "theme": "Replacement", "font_size": 20,
        "custom_shortcuts": '{"help": 42}', "unknown_future_key": "replace",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="custom_shortcuts"):
        manager.import_settings(str(backup))
    assert disk.read_bytes() == before_disk
    assert {key: manager._qs.value(key) for key in manager._qs.allKeys()} == before
    reloaded = launch()
    widget = open_settings(reloaded)
    assert widget._font_size_spin.value() == 12
    assert {key: reloaded._qs.value(key) for key in reloaded._qs.allKeys()} == before


@pytest.mark.parametrize("tool,limit_control,track_control", _HISTORY_CONTROLS)
def test_dialog_history_limits_take_effect_on_next_entry_and_zero_inherits_global(
        settings_session, tool, limit_control, track_control):
    _, launch, open_settings, _ = settings_session
    manager = launch()
    widget = open_settings(manager)
    widget._history_max_spin.setValue(10)
    getattr(widget, limit_control).setValue(3)
    add = getattr(manager, f"add_{tool}_history")
    history = getattr(manager, f"get_{tool}_history")
    for index in range(5):
        add({"file": f"{index}.png"})
    assert [entry["file"] for entry in history()] == ["4.png", "3.png", "2.png"]
    getattr(widget, limit_control).setValue(0)
    for index in range(5, 15):
        add({"file": f"{index}.png"})
    assert len(history()) == 10
    assert history()[0]["file"] == "14.png"
    if tool in ("gif_builder", "video_builder"):
        getattr(widget, track_control).setChecked(False)
        add({"file": "not-recorded.png"})
        assert history()[0]["file"] == "14.png"
    widget.close()
    reloaded = launch()
    assert len(getattr(reloaded, f"get_{tool}_history")()) == 10
    reopened = open_settings(reloaded)
    assert getattr(reopened, limit_control).value() == 0
    assert reopened._history_max_spin.value() == 10
