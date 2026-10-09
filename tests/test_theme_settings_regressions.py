import json
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QMessageBox, QGroupBox, QScrollArea

from src.core.settings_manager import SettingsManager
from src.ui.settings_dialog import SettingsDialog
from src.ui.theme_engine import HIDDEN_THEMES, PRESET_THEMES


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def dialog(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        manager = SettingsManager()
        widget = SettingsDialog(manager)
        yield widget, manager
        widget.close()
        sip.delete(widget)
        manager.sync()
        sip.delete(manager._qs)


@pytest.mark.parametrize("field,value", [
    ("name", []), ("name", ""), ("accent", None), ("accent", "not-a-color"),
    ("_cursor", []), ("_effect", {}), ("_trail_color", "not-a-color"),
])
def test_invalid_import_does_not_modify_themes(dialog, tmp_path, field, value):
    widget, manager = dialog
    original = manager.get_theme()
    imported = dict(original, **{field: value})
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(imported), encoding="utf-8")
    with patch("src.ui.settings_dialog.QFileDialog.getOpenFileName",
               return_value=(str(path), "")):
        with patch("src.ui.settings_dialog.QMessageBox.warning") as warning:
            with patch("src.ui.settings_dialog.QMessageBox.information"):
                widget._import_theme()
    warning.assert_called_once()
    assert manager.get_saved_themes() == {}
    assert manager.get_theme() == original


def test_invalid_utf8_import_reports_error(dialog, tmp_path):
    widget, manager = dialog
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff\xfe")
    with patch("src.ui.settings_dialog.QFileDialog.getOpenFileName",
               return_value=(str(path), "")):
        with patch("src.ui.settings_dialog.QMessageBox.warning") as warning:
            widget._import_theme()
    warning.assert_called_once()
    assert manager.get_saved_themes() == {}


@pytest.mark.parametrize("name", ["★ Star", "🔒 Lock", "Panda Dark"])
def test_custom_theme_selection_and_deletion_use_exact_identity(dialog, name):
    widget, manager = dialog
    theme = dict(manager.get_theme(), name=name, accent="#123456")
    manager.save_named_theme(name, theme)
    widget._rebuild_theme_combo(select=f"★ {name}")
    widget._on_preset_selected_live()
    assert manager.get_theme()["name"] == name
    assert manager.get_theme()["accent"] == "#123456"
    assert widget._btn_delete_theme.isEnabled()
    with patch("src.ui.settings_dialog.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes):
        widget._delete_custom_theme()
    assert name not in manager.get_saved_themes()


def test_theme_following_controls_show_the_new_theme_immediately(dialog):
    widget, manager = dialog
    widget._use_theme_trail_check.setChecked(True)
    widget._rebuild_theme_combo(select="Bat Cave")
    widget._on_preset_selected_live()
    assert manager.get_theme()["name"] == "Bat Cave"
    assert PRESET_THEMES["Bat Cave"]["_trail"] == "comet"
    assert widget._trail_style_combo.currentIndex() == 3


def test_click_effect_theme_toggle_updates_display_without_saving_manual_choice(dialog):
    from src.ui.theme_engine import THEME_EFFECTS

    widget, manager = dialog
    widget._click_effects_theme_check.setChecked(True)
    widget._use_theme_effect_check.setChecked(False)
    widget._effect_combo.setCurrentIndex(widget._effect_combo.findData("shark"))
    expected = THEME_EFFECTS[manager.get_theme()["name"]]
    widget._use_theme_effect_check.setChecked(True)
    assert widget._effect_combo.currentData() == expected
    assert not widget._effect_combo.isEnabled()
    assert "Shark" not in widget._effect_theme_info_lbl.text()
    assert manager.get("last_effect_key_pref") == "shark"
    widget._use_theme_effect_check.setChecked(False)
    assert widget._effect_combo.currentData() == "shark"
    assert widget._effect_combo.isEnabled()
    assert manager.get_theme()["_effect"] == "shark"


@pytest.mark.parametrize("method", ["preset", "import"])
def test_programmatic_theme_change_preserves_manual_effect_for_restoration(
        dialog, tmp_path, method):
    widget, manager = dialog
    widget._use_theme_effect_check.setChecked(False)
    widget._effect_combo.setCurrentIndex(widget._effect_combo.findData("shark"))
    theme = dict(PRESET_THEMES["Bat Cave"])
    if method == "preset":
        widget._rebuild_theme_combo(select="Bat Cave")
        widget._on_preset_selected_live()
    else:
        theme["name"] = "Imported bat theme"
        path = tmp_path / "theme.json"
        path.write_text(json.dumps(theme), encoding="utf-8")
        with patch("src.ui.settings_dialog.QFileDialog.getOpenFileName",
                   return_value=(str(path), "")), \
                patch.object(QMessageBox, "information"):
            widget._import_theme()
    assert manager.get_theme()["_effect"] == theme["_effect"]
    assert manager.get("last_effect_key_pref") == "shark"
    widget._use_theme_effect_check.setChecked(True)
    reopened = SettingsDialog(manager)
    try:
        reopened._use_theme_effect_check.setChecked(False)
        assert reopened._effect_combo.currentData() == "shark"
        assert manager.get_theme()["_effect"] == "shark"
    finally:
        reopened.close()
        sip.delete(reopened)


@pytest.mark.parametrize("use_theme", [False, True])
@pytest.mark.parametrize("name", ["Custom bat theme", "Panda Dark"])
def test_theme_effect_runtime_matches_settings_display(dialog, use_theme, name):
    from src.ui.main_window import MainWindow
    from src.ui.theme_engine import THEME_EFFECTS

    widget, manager = dialog
    manager.set_theme(dict(PRESET_THEMES["Bat Cave"], name=name))
    manager.set("use_theme_effect", use_theme)
    widget._theme = manager.get_theme()
    widget._load_values()
    host = Mock()
    host._settings = manager
    host._click_effects = Mock()
    MainWindow._apply_theme_effect(host)
    host._click_effects.set_effect.assert_called_once_with(widget._effect_combo.currentData())
    expected = (THEME_EFFECTS.get(name, manager.get_theme()["_effect"])
                if use_theme else manager.get_theme()["_effect"])
    assert widget._effect_combo.currentData() == expected


def test_corrupt_persisted_theme_recovers_without_overwriting_disk_data(dialog):
    widget, manager = dialog
    raw = json.dumps({
        "name": [], "accent": None, "text": "#abcdef", "_cursor": [],
        "_effect": {}, "_trail_color": "not-a-color",
    })
    manager.set("theme_data", raw)
    recovered = manager.get_theme()
    assert recovered["name"] == "Panda Dark"
    assert recovered["accent"] == manager._DEFAULT_THEME["accent"]
    assert recovered["text"] == "#abcdef"
    assert recovered["_effect"] == "panda"
    assert "_cursor" not in recovered
    assert "_trail_color" not in recovered
    assert manager.get("theme_data") == raw


def test_malformed_saved_entries_cannot_crash_theme_picker(dialog):
    widget, manager = dialog
    manager.set("saved_themes", json.dumps({
        "broken": [], "recoverable": {"name": None, "accent": "#123456", "_cursor": []},
    }))
    saved = manager.get_saved_themes()
    assert "broken" not in saved
    assert saved["recoverable"]["name"] == "recoverable"
    assert saved["recoverable"]["accent"] == "#123456"
    widget._rebuild_theme_combo(select="★ recoverable")
    widget._on_preset_selected_live()
    assert manager.get_theme()["name"] == "recoverable"
    manager.save_named_theme("new", manager.get_theme())
    assert json.loads(manager.get("saved_themes", "{}"))["broken"] == []
    manager.delete_named_theme("new")
    assert json.loads(manager.get("saved_themes", "{}"))["broken"] == []


def test_valid_import_preserves_colors_and_theme_effect_metadata(dialog, tmp_path):
    widget, manager = dialog
    theme = dict(PRESET_THEMES["Bat Cave"], name="My 🦇 theme")
    path = tmp_path / "valid.json"
    path.write_text(json.dumps(theme, ensure_ascii=False), encoding="utf-8")
    with patch("src.ui.settings_dialog.QFileDialog.getOpenFileName",
               return_value=(str(path), "")):
        with patch("src.ui.settings_dialog.QMessageBox.warning") as warning:
            with patch("src.ui.settings_dialog.QMessageBox.information"):
                widget._import_theme()
    warning.assert_not_called()
    assert manager.get_saved_themes()[theme["name"]]["_flock"] == "bats"
    assert manager.get_theme()["accent"] == theme["accent"]
    assert manager.get_theme()["_cursor"] == theme["_cursor"]


def test_locked_active_hidden_theme_remains_selectable(dialog):
    widget, manager = dialog
    name = "Blood Moon"
    widget._theme = dict(HIDDEN_THEMES[name])
    manager.set_theme(widget._theme)
    widget._rebuild_theme_combo(select=f"🔒 {name}")
    assert widget._theme_preset_combo.currentData() == ("hidden", name)
    widget._on_preset_selected_live()
    assert manager.get_theme()["name"] == name
    assert not widget._btn_delete_theme.isEnabled()


@pytest.mark.parametrize("operation", ["save", "import"])
@pytest.mark.parametrize("accept", [False, True])
def test_saved_theme_replacement_requires_confirmation_without_mutating_on_decline(dialog, tmp_path, operation, accept):
    widget, manager = dialog
    name = "My ★ theme"
    previous = dict(manager.get_theme(), name=name, accent="#123456")
    manager.save_named_theme(name, previous)
    original = manager.get_theme()
    replacement = dict(original, name=name, accent="#abcdef")
    emitted = Mock()
    widget.theme_changed.connect(emitted)
    reply = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    with patch.object(QMessageBox, "question", return_value=reply) as question, \
            patch.object(QMessageBox, "information") as info:
        if operation == "save":
            widget._theme = dict(replacement, name=original["name"])
            with patch("src.ui.settings_dialog.QInputDialog.getText", return_value=(name, True)):
                widget._save_custom_theme()
        else:
            path = tmp_path / "theme.json"
            path.write_text(json.dumps(replacement), encoding="utf-8")
            with patch("src.ui.settings_dialog.QFileDialog.getOpenFileName", return_value=(str(path), "")):
                widget._import_theme()
    question.assert_called_once()
    assert question.call_args.args[-1] == QMessageBox.StandardButton.No
    if accept:
        assert manager.get_saved_themes()[name]["accent"] == "#abcdef"
        assert manager.get_theme()["name"] == name
        assert widget._theme_preset_combo.currentData() == ("saved", name)
        info.assert_called_once()
        emitted.assert_called_once()
    else:
        assert manager.get_saved_themes()[name] == previous
        assert manager.get_theme() == original
        assert widget._theme["name"] == original["name"]
        info.assert_not_called()
        emitted.assert_not_called()


def test_delete_theme_defaults_to_no_and_retains_active_colors(dialog):
    widget, manager = dialog
    theme = dict(manager.get_theme(), name="Delete me", accent="#123456")
    manager.save_named_theme(theme["name"], theme)
    widget._rebuild_theme_combo(select="★ Delete me")
    widget._on_preset_selected_live()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
        widget._delete_custom_theme()
    assert question.call_args.args[-1] == QMessageBox.StandardButton.No
    assert "Delete me" in manager.get_saved_themes()
    assert manager.get_theme() == theme


@pytest.mark.parametrize("accept", [False, True])
def test_theme_extensionless_export_confirms_existing_final_name(dialog, tmp_path, accept):
    widget, manager = dialog
    chosen = tmp_path / "theme"
    final = chosen.with_suffix(".json")
    final.write_bytes(b"previous export")
    reply = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    with patch("src.ui.settings_dialog.QFileDialog.getSaveFileName", return_value=(str(chosen), "")), \
            patch.object(QMessageBox, "question", return_value=reply) as question, \
            patch.object(QMessageBox, "information") as info:
        widget._export_theme()
    assert question.call_args.args[-1] == QMessageBox.StandardButton.No
    if accept:
        assert json.loads(final.read_text())["accent"] == widget._theme["accent"]
        assert str(final) in info.call_args.args[2]
    else:
        assert final.read_bytes() == b"previous export"
        info.assert_not_called()
    assert not chosen.exists()
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("stage", ["encode", "replace"])
def test_theme_export_failure_preserves_existing_file(dialog, tmp_path, stage):
    widget, manager = dialog
    final = tmp_path / "theme.json"
    final.write_bytes(b"previous export")

    def partial(data, stream, **kwargs):
        stream.write("{")
        raise OSError("write failed")

    failure = (patch("src.ui.settings_dialog.json.dump", partial) if stage == "encode"
               else patch("os.replace", side_effect=PermissionError("file locked")))
    with patch("src.ui.settings_dialog.QFileDialog.getSaveFileName", return_value=(str(final), "")), \
            failure, patch.object(QMessageBox, "warning") as warning, \
            patch.object(QMessageBox, "information") as info:
        widget._export_theme()
    warning.assert_called_once()
    info.assert_not_called()
    assert final.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


def test_new_custom_theme_save_does_not_ask_to_replace(dialog):
    widget, manager = dialog
    with patch("src.ui.settings_dialog.QInputDialog.getText", return_value=("New theme", True)), \
            patch.object(QMessageBox, "question") as question, \
            patch.object(QMessageBox, "information"):
        widget._save_custom_theme()
    question.assert_not_called()
    assert manager.get_theme()["name"] == "New theme"
    assert manager.get_saved_themes()["New theme"] == widget._theme


def test_theme_export_keeps_existing_json_suffix_and_full_metadata(dialog, tmp_path):
    widget, manager = dialog
    widget._theme = dict(PRESET_THEMES["Bat Cave"], name="My 🦇 theme")
    widget._set_effect_combo(widget._theme["_effect"])
    final = tmp_path / "theme.JSON"
    with patch("src.ui.settings_dialog.QFileDialog.getSaveFileName", return_value=(str(final), "")), \
            patch.object(QMessageBox, "question") as question, \
            patch.object(QMessageBox, "information"):
        widget._export_theme()
    question.assert_not_called()
    assert json.loads(final.read_text(encoding="utf-8")) == widget._theme
    assert not (tmp_path / "theme.JSON.json").exists()


def test_settings_effects_have_dedicated_tab_without_duplicated_controls(dialog):
    widget, manager = dialog
    tabs = widget._settings_tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "🎨 Theme", "⚙ General", "🔊 Sound", "✨ Effects",
    ]
    theme_groups = {group.title() for group in tabs.widget(0).findChildren(QGroupBox)}
    effect_groups = {group.title() for group in tabs.widget(3).findChildren(QGroupBox)}
    assert "Active Theme Preset" in theme_groups
    assert "Theme Colors" in theme_groups
    for title in ["Click Effect Style", "Hold-Click Effects", "Background Effects",
                  "Mouse Trail", "Cursor", "Button Press Animation"]:
        assert title not in theme_groups
        assert title in effect_groups
        assert sum(group.title() == title for group in widget.findChildren(QGroupBox)) == 1
    assert "Sound" not in effect_groups
    assert all(tabs.tabToolTip(i) for i in range(tabs.count()))
    assert "automatically" in widget._live_settings_note.text()
    assert "Close keeps" in widget._live_settings_note.text()


def test_settings_tab_navigation_does_not_change_preferences(dialog):
    widget, manager = dialog
    before = manager.get_theme()
    emitted = Mock()
    widget.settings_changed.connect(emitted)
    for index in [3, 0, 2, 1, 3]:
        widget._settings_tabs.setCurrentIndex(index)
    assert manager.get_theme() == before
    emitted.assert_not_called()
    widget._font_size_spin.setValue(16)
    assert manager.get("font_size") == 16


def test_settings_tabs_remain_scrollable_in_small_window(dialog, app):
    widget, manager = dialog
    widget.show()
    widget.setMinimumSize(0, 0)
    widget.resize(640, 480)
    for index in range(widget._settings_tabs.count()):
        widget._settings_tabs.setCurrentIndex(index)
        app.processEvents()
        scroll = widget._settings_tabs.widget(index)
        assert isinstance(scroll, QScrollArea)
        assert scroll.widgetResizable()
        assert scroll.widget().height() >= scroll.viewport().height()
        assert scroll.verticalScrollBar().maximum() == max(
            0, scroll.widget().height() - scroll.viewport().height(),
        )
    widget.close()


@pytest.mark.parametrize("finish", ["accept", "reject", "close"])
def test_finishing_settings_flushes_pending_live_preferences(dialog, finish):
    widget, manager = dialog
    widget.show()
    widget._theme_preset_combo.setCurrentText("Bat Cave")
    widget._tooltip_style_combo.setCurrentText("Angular")
    assert widget._theme_debounce.isActive()
    assert widget._misc_combo_debounce.isActive()
    getattr(widget, finish)()
    assert manager.get_theme()["name"] == "Bat Cave"
    assert manager.get("tooltip_style") == "Angular"
    assert not widget._theme_debounce.isActive()
    assert not widget._misc_combo_debounce.isActive()


def test_reset_cannot_be_overwritten_by_pending_debounce(dialog):
    widget, manager = dialog
    widget._theme_preset_combo.setCurrentText("Bat Cave")
    widget._tooltip_style_combo.setCurrentText("Angular")
    with patch("src.ui.settings_dialog.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes):
        with patch("src.ui.settings_dialog.QMessageBox.information"):
            widget._reset_all_settings()
    assert manager.get_theme()["name"] == "Panda Dark"
    assert manager.get("tooltip_style") == manager._DEFAULTS["tooltip_style"]
    assert not widget._theme_debounce.isActive()
    assert not widget._misc_combo_debounce.isActive()
    assert widget._misc_combo_pending == {}


def test_reset_unlocks_rearms_every_first_use_trigger_without_losing_preferences(dialog):
    widget, manager = dialog
    flags = ["theme_changed_once", "cursor_anim_used_once", "trail_enabled_once",
             "tooltip_mode_changed_once", "alpha_fix_done_once", "conversion_done_once"]
    for key in flags:
        manager.set(key, True)
    manager.set("font_size", 18)
    manager.reset_unlocks_only()
    assert all(manager.get(key) is False for key in flags)
    assert manager.get("font_size") == 18


def test_settings_backup_preserves_theme_controls_and_history_policy(dialog, tmp_path):
    widget, manager = dialog
    preferences = {
        "btn_height": "Large", "widget_spacing": "Compact", "border_radius": "Square",
        "panel_padding": "Compact", "notif_overlay_enabled": False, "use_theme_notif": False,
        "history_max_entries_video_builder": 12, "history_track_gif_builder": False,
        "hold_effects_enabled": True, "hold_effects_key": "blood",
        "use_theme_hold_effects": True, "last_trail_style_pref": "comet",
        "last_effect_key_pref": "shark", "last_cursor_key_pref": "Cross",
        "last_btn_anim_pref": "abduct", "last_sound_profile_pref": "soft",
        "custom_shortcuts": json.dumps({"converter.convert": "Ctrl+Alt+C"}),
    }
    for key, value in preferences.items():
        manager.set(key, value)
    path = tmp_path / "settings-backup.json"
    manager.export_settings(str(path))
    manager.reset_all()
    manager.import_settings(str(path))
    assert {key: manager.get(key) for key in preferences} == preferences


@pytest.mark.parametrize("name,mode", [("Shark Bait", "bite"), ("Alien", "abduct")])
def test_theme_specific_button_modes_are_represented_in_settings(dialog, name, mode):
    widget, manager = dialog
    widget._rebuild_theme_combo(select=name)
    widget._on_preset_selected_live()
    assert manager.get_theme()["_button_anim"] == mode
    assert widget._button_anim_style_combo.currentData() == mode
    assert mode.title() in widget._btn_anim_theme_info_lbl.text()


def test_legacy_flock_banner_preview_matches_runtime_bounce_mode(dialog):
    widget, manager = dialog
    widget._rebuild_theme_combo(select="Bat Cave")
    widget._on_preset_selected_live()
    widget._update_banner_theme_info()
    assert widget._banner_anim_combo.currentData() == "bounce"
    assert "Bounce" in widget._banner_theme_info_lbl.text()


def test_saving_custom_theme_updates_active_identity_and_clears_filter(dialog):
    widget, manager = dialog
    widget._theme_search.setText("Panda")
    with patch("src.ui.settings_dialog.QInputDialog.getText",
               return_value=("My custom theme", True)):
        with patch("src.ui.settings_dialog.QMessageBox.information"):
            widget._save_custom_theme()
    assert manager.get_theme()["name"] == "My custom theme"
    assert widget._theme_preset_combo.currentData() == ("saved", "My custom theme")
    assert widget._theme_search.text() == ""


def test_search_cannot_redirect_an_outstanding_theme_selection(dialog):
    widget, manager = dialog
    widget._theme_preset_combo.setCurrentText("Bat Cave")
    assert widget._theme_debounce.isActive()
    widget._theme_search.setText("Galaxy")
    assert manager.get_theme()["name"] == "Bat Cave"
    assert not widget._theme_debounce.isActive()


def test_export_flushes_pending_theme_selection(dialog, tmp_path):
    widget, manager = dialog
    widget._theme_preset_combo.setCurrentText("Bat Cave")
    path = tmp_path / "export.json"
    with patch("src.ui.settings_dialog.QFileDialog.getSaveFileName",
               return_value=(str(path), "")):
        with patch("src.ui.settings_dialog.QMessageBox.information"):
            widget._export_theme()
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "Bat Cave"


def test_banner_theme_toggle_restores_the_actual_manual_animation(dialog):
    widget, manager = dialog
    widget._banner_use_theme_anim_check.setChecked(False)
    widget._banner_anim_combo.setCurrentIndex(widget._banner_anim_combo.findData("glitch"))
    widget._banner_use_theme_anim_check.setChecked(True)
    assert widget._banner_anim_combo.currentData() == "spin"
    widget._banner_use_theme_anim_check.setChecked(False)
    assert widget._banner_anim_combo.currentData() == "glitch"
    assert manager.get("banner_anim_style") == "glitch"


def test_theme_sound_preview_does_not_overwrite_manual_profile(dialog):
    widget, manager = dialog
    profiles = [widget._sound_profile_combo.itemData(i)
                for i in range(widget._sound_profile_combo.count())]
    manual = next(profile for profile in profiles if profile != "soft")
    widget._sound_profile_combo.setCurrentIndex(widget._sound_profile_combo.findData(manual))
    widget._use_theme_sound_check.setChecked(True)
    widget._rebuild_theme_combo(select="Panda Dark")
    widget._on_preset_selected_live()
    assert manager.get("sound_manual_profile") == manual
    widget._use_theme_sound_check.setChecked(False)
    assert widget._sound_profile_combo.currentData() == manual


@pytest.mark.parametrize("kind,manual,theme", [
    ("drip", "water", "Gore"),
    ("flock", "fish", "Bat Cave"),
    ("ambient", "snow", "Galaxy"),
])
def test_background_theme_toggle_preserves_manual_style(dialog, kind, manual, theme):
    widget, manager = dialog
    enabled = getattr(widget, f"_bg_{kind}_check")
    follow = getattr(widget, f"_use_theme_{kind}_check")
    combo = getattr(widget, f"_bg_{kind}_combo")
    key = f"bg_{kind}_" + ("style" if kind == "flock" else "type")
    enabled.setChecked(True)
    combo.setCurrentIndex(combo.findData(manual))
    follow.setChecked(True)
    widget._rebuild_theme_combo(select=theme)
    widget._on_preset_selected_live()
    assert manager.get(key) == manual
    enabled.setChecked(False)
    enabled.setChecked(True)
    assert manager.get(key) == manual
    follow.setChecked(False)
    assert combo.currentData() == manual
    assert manager.get(key) == manual


def test_opening_settings_does_not_erase_saved_background_or_notification_choices(dialog, tmp_path):
    widget, manager = dialog
    background = tmp_path / "custom-background.png"
    background.write_bytes(b"background placeholder")
    preferences = {
        "custom_bg_enabled": True, "use_theme_bg": True,
        "custom_bg_path": str(background),
        "notif_overlay_enabled": True, "use_theme_notif": True,
        "bg_flock_enabled": True, "use_theme_flock": True, "bg_flock_style": "fish",
    }
    manager.set_theme(PRESET_THEMES["Bat Cave"])
    for key, value in preferences.items():
        manager.set(key, value)
    reopened = SettingsDialog(manager)
    try:
        assert {key: manager.get(key) for key in preferences} == preferences
        assert reopened._custom_bg_path_edit.text() == str(background)
        assert reopened._use_theme_bg_check.isChecked()
        assert reopened._use_theme_notif_check.isChecked()
        assert reopened._bg_flock_combo.currentData() == "bats"
    finally:
        reopened.close()
        sip.delete(reopened)
