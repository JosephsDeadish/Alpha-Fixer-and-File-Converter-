import json
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QGroupBox, QScrollArea, QLabel, QWidget, QVBoxLayout,
    QPushButton, QToolButton, QLineEdit, QTextEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QRadioButton, QPlainTextEdit,
)

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


def test_settings_appearance_history_labels_and_named_sliders_preserve_preferences(dialog):
    widget, manager = dialog
    before = {key: manager.get(key) for key in manager.EXPORT_KEYS}
    controls = [
        widget._font_size_spin, widget._tooltip_mode_combo, widget._tooltip_style_combo,
        widget._ui_scale_combo, widget._btn_height_combo, widget._widget_spacing_combo,
        widget._border_radius_combo, widget._panel_padding_combo,
        widget._history_max_spin, widget._history_max_conv_spin, widget._history_max_alpha_spin,
        widget._history_max_sel_spin, widget._history_max_gif_spin, widget._history_max_video_spin,
    ]
    labels = widget.findChildren(QLabel)
    names = [control.accessibleName() for control in controls]
    assert all(names) and len(set(names)) == len(names)
    for control in controls:
        assert sum(label.buddy() is control for label in labels) == 1
    for control in (widget._sound_volume_slider, widget._trail_length_slider,
                    widget._trail_fade_slider, widget._trail_intensity_slider):
        assert control.accessibleName()
    widget._load_values()
    assert {key: manager.get(key) for key in manager.EXPORT_KEYS} == before
    assert [control.accessibleName() for control in controls] == names


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


def test_settings_guidance_follows_live_theme_colors_and_scaled_fonts(dialog, app):
    from src.ui.theme_engine import build_stylesheet

    widget, manager = dialog
    hints = [label for label in widget.findChildren(QLabel) if label.property("settingsHint")]
    guides = [label for label in widget.findChildren(QLabel) if label.property("settingsGuide")]
    assert len(hints) == 17
    assert len(guides) == 2
    assert any("Effects controls optional visual effects" in label.text() for label in guides)
    widget.show()
    for name, pixels in [("Panda Light", 24), ("Panda Dark", 13), ("Panda Light", 18)]:
        theme = PRESET_THEMES[name]
        widget.setStyleSheet(build_stylesheet(theme) + f"\nQWidget {{ font-size: {pixels}px; }}")
        app.processEvents()
        for label in hints + guides:
            assert not label.styleSheet()
            assert label.wordWrap()
            assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(theme["text"])
            assert label.font().pixelSize() == pixels
        for label in guides:
            assert label.palette().color(QPalette.ColorRole.Window) == QColor(theme["surface"])
        assert not widget._btn_reset.styleSheet()
        assert widget._btn_reset.palette().color(QPalette.ColorRole.ButtonText) == QColor(theme["error"])


def test_large_font_settings_guidance_remains_reachable_in_small_window(dialog, app):
    from src.ui.theme_engine import build_stylesheet

    widget, manager = dialog
    widget.setStyleSheet(build_stylesheet(PRESET_THEMES["Panda Light"])
                        + "\nQWidget { font-size: 24px; }")
    widget.setMinimumSize(0, 0)
    widget.resize(640, 480)
    widget.show()
    for index in range(widget._settings_tabs.count()):
        widget._settings_tabs.setCurrentIndex(index)
        app.processEvents()
        scroll = widget._settings_tabs.widget(index)
        assert scroll.verticalScrollBar().maximum() == max(
            0, scroll.widget().height() - scroll.viewport().height(),
        )
        for label in scroll.findChildren(QLabel):
            if label.property("settingsHint") or label.property("settingsGuide"):
                assert label.wordWrap()
                if label.isVisible():
                    assert label.height() >= label.minimumSizeHint().height()


@pytest.mark.parametrize("name", ["Panda Dark", "Panda Light"])
def test_reset_hover_and_pressed_use_readable_theme_surface(dialog, app, name):
    from src.ui.theme_engine import build_stylesheet

    widget, manager = dialog
    theme = PRESET_THEMES[name]
    widget.setStyleSheet(build_stylesheet(theme))
    widget.show()
    app.processEvents()
    button = widget._btn_reset
    QTest.mouseMove(widget, widget.rect().topRight())
    app.processEvents()
    QTest.mouseMove(button, button.rect().center())
    app.sendEvent(button, QEvent(QEvent.Type.Enter))
    app.processEvents()
    assert button.grab().toImage().pixelColor(4, button.height() // 2) == QColor(theme["surface"])
    QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    app.processEvents()
    assert button.grab().toImage().pixelColor(4, button.height() // 2) == QColor(theme["surface"])
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=button.rect().center())


@pytest.mark.parametrize("theme", [*PRESET_THEMES.values(), *HIDDEN_THEMES.values()],
                         ids=lambda theme: theme["name"])
def test_keyboard_focus_is_visible_without_layout_shifts(app, theme):
    from src.ui.theme_engine import build_stylesheet

    window = QWidget()
    window.setStyleSheet(build_stylesheet(theme))
    layout = QVBoxLayout(window)
    controls = [QPushButton("Action"), QPushButton("Export"), QPushButton("Reset"),
                QLineEdit(), QTextEdit(), QPlainTextEdit(), QComboBox(),
                QSpinBox(), QDoubleSpinBox(), QCheckBox("Option"), QRadioButton("Choice")]
    controls[1].setObjectName("accent")
    controls[2].setObjectName("resetBtn")
    controls[6].addItem("Value")
    for control in controls:
        layout.addWidget(control)
    try:
        window.show()
        window.activateWindow()
        app.processEvents()
        QTest.mouseMove(window, window.rect().bottomRight())
        for index, control in enumerate(controls):
            controls[(index + 1) % len(controls)].setFocus()
            app.processEvents()
            before_size = control.sizeHint()
            before_geometry = control.geometry()
            before_image = control.grab().toImage()
            control.setFocus(Qt.FocusReason.TabFocusReason)
            app.processEvents()
            assert control.hasFocus(), (theme["name"], index)
            assert control.sizeHint() == before_size, (theme["name"], index)
            assert control.geometry() == before_geometry, (theme["name"], index)
            after_image = control.grab().toImage()
            # Inputs have a caret and native focus effects; sample the top
            # border so the assertion specifically checks the shared focus cue.
            if index < 9:
                y = 0
                x_range = range(12, min(control.width() - 12, 60))
            else:
                # Checkbox/radio indicators are centered vertically.
                y = control.height() // 2
                x_range = range(0, 4)
            assert any(before_image.pixelColor(x, y) != after_image.pixelColor(x, y)
                       for x in x_range), (theme["name"], index)
        # Ordinary tab traversal skips disabled actions.
        controls[1].setEnabled(False)
        controls[0].setFocus()
        QTest.keyClick(controls[0], Qt.Key.Key_Tab)
        assert controls[2].hasFocus()
        QTest.keyClick(controls[2], Qt.Key.Key_Backtab)
        assert controls[0].hasFocus()
        clicked = Mock()
        controls[0].clicked.connect(clicked)
        QTest.keyClick(controls[0], Qt.Key.Key_Space)
        clicked.assert_called_once()
    finally:
        window.close()
        sip.delete(window)


def test_disabled_controls_keep_readable_theme_colors_and_do_not_activate(app):
    from src.ui.theme_engine import build_stylesheet, _readable_disabled_color

    host = QWidget()
    layout = QVBoxLayout(host)
    controls = [QPushButton("Standard"), QPushButton("Accent"), QPushButton("Reset"),
                QToolButton(), QLineEdit("Value"), QTextEdit("Value"), QComboBox(),
                QSpinBox(), QDoubleSpinBox(), QCheckBox("Choice"), QRadioButton("Choice")]
    controls[1].setObjectName("accent")
    controls[2].setObjectName("resetBtn")
    controls[3].setText("Tool")
    controls[6].addItem("Selected")
    activated = Mock()
    controls[1].clicked.connect(activated)
    for control in controls:
        layout.addWidget(control)
        control.setEnabled(False)
    host.show()
    try:
        for theme in list(PRESET_THEMES.values()) + list(HIDDEN_THEMES.values()):
            host.setStyleSheet(build_stylesheet(theme))
            app.processEvents()
            expected = QColor(_readable_disabled_color(theme))
            for control in controls:
                role = (QPalette.ColorRole.ButtonText if isinstance(control, (QPushButton, QToolButton))
                        else QPalette.ColorRole.WindowText if isinstance(control, (QCheckBox, QRadioButton))
                        else QPalette.ColorRole.Text)
                assert control.palette().color(QPalette.ColorGroup.Disabled, role) == expected
            assert controls[1].palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button) == QColor(theme["surface"])
        QTest.mouseClick(controls[1], Qt.MouseButton.LeftButton)
        activated.assert_not_called()
        controls[1].setEnabled(True)
        QTest.mouseClick(controls[1], Qt.MouseButton.LeftButton)
        activated.assert_called_once()
    finally:
        host.close()
        sip.delete(host)


@pytest.mark.parametrize("theme", list(PRESET_THEMES.values()) + list(HIDDEN_THEMES.values()),
                         ids=lambda theme: theme["name"])
def test_disabled_text_contrast_across_builtin_themes(theme):
    from src.ui.theme_engine import _readable_disabled_color

    def luminance(value):
        color = QColor(value)
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    foreground = luminance(_readable_disabled_color(theme))
    for key in ("surface", "background"):
        background = luminance(theme[key])
        contrast = (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)
        assert contrast >= 4.5, (theme["name"], key, contrast)


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


def test_loading_scale_sound_path_and_history_does_not_apply_live_changes(dialog, app):
    widget, manager = dialog
    preferences = {
        "font_size": 12, "ui_scale": "Huge", "history_max_entries": 25,
        "click_sound_path": "/example/custom-click.wav", "sound_enabled": True,
        "use_theme_sound": True,
    }
    for key, value in preferences.items():
        manager.set(key, value)
    before_font = app.font()
    changed = Mock()
    widget.settings_changed.connect(changed)
    widget._load_values()
    changed.assert_not_called()
    assert app.font() == before_font
    assert {key: manager.get(key) for key in preferences} == preferences
    assert widget._ui_scale_combo.currentText().startswith("Huge")
    assert widget._history_max_spin.value() == 25
    assert widget._click_sound_path_edit.text() == preferences["click_sound_path"]


def test_declined_reset_preserves_preferences_and_pending_changes(dialog):
    widget, manager = dialog
    widget._theme_preset_combo.setCurrentText("Bat Cave")
    widget._tooltip_style_combo.setCurrentText("Angular")
    before = manager.get_theme()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
        widget._reset_all_settings()
    assert "Restart" in question.call_args.args[2]
    assert question.call_args.args[-1] == QMessageBox.StandardButton.No
    assert manager.get_theme() == before
    assert widget._theme_debounce.isActive()
    assert widget._misc_combo_debounce.isActive()
    widget._flush_pending_preferences()
    assert manager.get_theme()["name"] == "Bat Cave"
    assert manager.get("tooltip_style") == "Angular"


def test_next_launch_and_reset_timing_are_visible_before_changes(dialog):
    widget, manager = dialog
    assert "next launch" in widget._show_splash_check.text()
    assert "next time" in widget._show_splash_check.toolTip()
    assert "Restart" in widget._btn_reset.toolTip()
    widget._show_splash_check.setChecked(True)
    assert manager.get("show_splash_screen") is True


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


@pytest.mark.parametrize("key,value", [
    ("sound_enabled", []), ("sound_enabled", "maybe"), ("sound_enabled", 2),
    ("font_size", {}), ("font_size", True), ("font_size", 12.5),
    ("font_size", "large"), ("font_size", 2**80),
    ("history_max_entries_video_builder", []),
    ("cursor", None), ("ui_scale", {}), ("click_sound_path", []),
])
def test_invalid_backup_value_rejects_import_without_partial_changes(dialog, tmp_path, key, value):
    widget, manager = dialog
    manager.set("theme", "Existing theme")
    before = {name: manager.get(name) for name in manager.EXPORT_KEYS}
    path = tmp_path / "invalid-backup.json"
    path.write_text(json.dumps({"theme": "Replacement theme", key: value}), encoding="utf-8")
    with pytest.raises(ValueError, match=key):
        manager.import_settings(str(path))
    assert {name: manager.get(name) for name in manager.EXPORT_KEYS} == before


def test_legacy_backup_scalars_survive_disk_reload_and_settings_reopening(dialog, tmp_path):
    widget, manager = dialog
    path = tmp_path / "legacy-backup.json"
    path.write_text(json.dumps({
        "sound_enabled": "true", "use_theme_sound": "no", "sound_volume": "65",
        "font_size": "12", "ui_scale": "Large",
        "hold_effects_enabled": 1, "hold_effects_key": "blood",
        "use_theme_hold_effects": "0", "cursor_enabled": "yes",
        "cursor": "emoji:🧪", "last_cursor_key_pref": "emoji:🧪",
        "history_max_entries_video_builder": "12", "unknown_future_setting": [],
    }), encoding="utf-8")
    imported = manager.import_settings(str(path))
    assert "unknown_future_setting" not in imported
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=manager._qs.fileName()):
        reloaded = SettingsManager()
    reopened = SettingsDialog(reloaded)
    try:
        assert reloaded.get("sound_volume") == 65
        assert reloaded.get("history_max_entries_video_builder", 0) == 12
        assert reopened._sound_check.isChecked()
        assert not reopened._use_theme_sound_check.isChecked()
        assert reopened._font_size_spin.value() == 12
        assert reopened._hold_key_combo.currentData() == "blood"
        assert reopened._cursor_combo.currentText() == "🧪"
    finally:
        reopened.close()
        sip.delete(reopened)
        reloaded.sync()
        sip.delete(reloaded._qs)


_BACKUP_NUMERIC_LIMITS = [
    ("sound_volume", 0, 100), ("font_size", 8, 24),
    ("trail_length", 10, 200), ("trail_fade_speed", 1, 10),
    ("trail_intensity", 10, 100), ("history_max_entries", 10, 5000),
    ("history_max_entries_converter", 0, 5000),
    ("history_max_entries_alpha", 0, 5000),
    ("history_max_entries_selective_alpha", 0, 5000),
    ("history_max_entries_gif_builder", 0, 5000),
    ("history_max_entries_video_builder", 0, 5000),
    ("last_converter_quality", 1, 100),
    ("sa_brush_size", 1, 200), ("sa_eraser_size", 1, 200),
]


@pytest.mark.parametrize("key,minimum,maximum", _BACKUP_NUMERIC_LIMITS)
@pytest.mark.parametrize("boundary", ["below", "above"])
def test_out_of_range_backup_is_atomic_on_disk(dialog, tmp_path, key, minimum, maximum, boundary):
    widget, manager = dialog
    manager.set("theme", "Existing theme")
    manager.sync()
    before_disk = Path(manager._qs.fileName()).read_bytes()
    before = {name: manager.get(name) for name in manager.EXPORT_KEYS}
    value = minimum - 1 if boundary == "below" else maximum + 1
    path = tmp_path / "out-of-range.json"
    path.write_text(json.dumps({"theme": "Replacement theme", key: str(value)}), encoding="utf-8")
    with pytest.raises(ValueError, match=key):
        manager.import_settings(str(path))
    assert {name: manager.get(name) for name in manager.EXPORT_KEYS} == before
    assert Path(manager._qs.fileName()).read_bytes() == before_disk


@pytest.mark.parametrize("upper", [False, True])
@pytest.mark.parametrize("legacy_strings", [False, True])
def test_backup_numeric_boundaries_match_reopened_widgets(dialog, tmp_path, upper, legacy_strings):
    widget, manager = dialog
    values = {key: maximum if upper else minimum for key, minimum, maximum in _BACKUP_NUMERIC_LIMITS}
    path = tmp_path / "numeric-boundaries.json"
    path.write_text(json.dumps({key: str(value) if legacy_strings else value
                                for key, value in values.items()}), encoding="utf-8")
    assert set(manager.import_settings(str(path))) == set(values)
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=manager._qs.fileName()):
        reloaded = SettingsManager()
    reopened = SettingsDialog(reloaded)
    try:
        assert {key: reloaded.get(key, 0) for key in values} == values
        controls = {
            "sound_volume": reopened._sound_volume_slider,
            "font_size": reopened._font_size_spin,
            "trail_length": reopened._trail_length_slider,
            "trail_fade_speed": reopened._trail_fade_slider,
            "trail_intensity": reopened._trail_intensity_slider,
            "history_max_entries": reopened._history_max_spin,
            "history_max_entries_converter": reopened._history_max_conv_spin,
            "history_max_entries_alpha": reopened._history_max_alpha_spin,
            "history_max_entries_selective_alpha": reopened._history_max_sel_spin,
            "history_max_entries_gif_builder": reopened._history_max_gif_spin,
            "history_max_entries_video_builder": reopened._history_max_video_spin,
        }
        for key, control in controls.items():
            assert control.value() == values[key], key
            assert (control.minimum(), control.maximum()) == SettingsManager._IMPORT_INTEGER_RANGES[key]
    finally:
        reopened.close()
        sip.delete(reopened)
        reloaded.sync()
        sip.delete(reloaded._qs)


def test_out_of_range_backup_ui_reports_failure_without_applying(dialog, tmp_path):
    from src.ui.main_window import MainWindow

    widget, manager = dialog
    manager.set("sound_volume", 35)
    widget._load_values()
    before = {key: manager.get(key) for key in manager.EXPORT_KEYS}
    path = tmp_path / "invalid-volume.json"
    path.write_text(json.dumps({"font_size": 20, "sound_volume": -1}), encoding="utf-8")
    with patch("src.ui.main_window.QFileDialog.getOpenFileName", return_value=(str(path), "")), \
         patch("src.ui.main_window.QMessageBox.critical") as error, \
         patch("src.ui.main_window.QMessageBox.information") as success, \
         patch.object(widget, "_on_settings_changed", create=True) as apply:
        MainWindow._import_settings(widget)
    error.assert_called_once()
    assert error.call_args.args[1] == "Import Failed"
    assert "sound_volume" in error.call_args.args[2]
    assert "0–100" in error.call_args.args[2]
    apply.assert_not_called()
    success.assert_not_called()
    assert {key: manager.get(key) for key in manager.EXPORT_KEYS} == before
    assert widget._sound_volume_slider.value() == 35
    assert widget._font_size_spin.value() == before["font_size"]


def test_backup_export_declined_normalized_overwrite_keeps_file(dialog, tmp_path):
    from src.ui.main_window import MainWindow

    widget, manager = dialog
    chosen = tmp_path / "backup"
    destination = tmp_path / "backup.json"
    destination.write_bytes(b"existing backup")
    with patch("src.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(chosen), "")), \
         patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question, \
         patch.object(manager, "export_settings") as export, \
         patch("src.ui.main_window.QMessageBox.information") as success:
        MainWindow._export_settings(widget)
    question.assert_called_once()
    assert question.call_args.args[-1] == QMessageBox.StandardButton.No
    export.assert_not_called()
    success.assert_not_called()
    assert destination.read_bytes() == b"existing backup"
    assert not chosen.exists()


def test_backup_export_failure_preserves_destination_and_cleans_stage(dialog, tmp_path):
    from src.ui.main_window import MainWindow

    widget, manager = dialog
    destination = tmp_path / "backup.json"
    destination.write_bytes(b"existing backup")
    def fail_after_partial_write(path):
        assert Path(path).parent == destination.parent
        assert Path(path) != destination
        Path(path).write_bytes(b"incomplete backup")
        raise OSError("disk write failed")

    with patch("src.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
         patch.object(manager, "export_settings", side_effect=fail_after_partial_write), \
         patch("src.ui.main_window.QMessageBox.critical") as error, \
         patch("src.ui.main_window.QMessageBox.information") as success:
        MainWindow._export_settings(widget)
    error.assert_called_once()
    success.assert_not_called()
    assert destination.read_bytes() == b"existing backup"
    assert list(tmp_path.glob(".alpha_fixer_save_*")) == []


def test_backup_export_confirmed_normalized_overwrite_replaces_file(dialog, tmp_path):
    from src.ui.main_window import MainWindow

    widget, manager = dialog
    chosen = tmp_path / "backup"
    destination = tmp_path / "backup.json"
    destination.write_bytes(b"existing backup")
    manager.set("font_size", 16)
    with patch("src.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(chosen), "")), \
         patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
         patch("src.ui.main_window.QMessageBox.information"):
        MainWindow._export_settings(widget)
    assert json.loads(destination.read_text(encoding="utf-8"))["font_size"] == 16
    assert list(tmp_path.glob(".alpha_fixer_save_*")) == []


def test_backup_export_cancel_does_not_write(dialog):
    from src.ui.main_window import MainWindow

    widget, manager = dialog
    with patch("src.ui.main_window.QFileDialog.getSaveFileName", return_value=("", "")), \
         patch.object(manager, "export_settings") as export, \
         patch("src.ui.main_window.QMessageBox.information") as success:
        MainWindow._export_settings(widget)
    export.assert_not_called()
    success.assert_not_called()


@pytest.mark.parametrize("extension", ["", ".JSON"])
def test_backup_export_normalizes_and_round_trips(dialog, tmp_path, extension):
    from src.ui.main_window import MainWindow

    widget, manager = dialog
    manager.set("font_size", 18)
    chosen = tmp_path / f"backup{extension}"
    destination = chosen if extension else chosen.with_suffix(".json")
    with patch("src.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(chosen), "")), \
         patch("src.ui.main_window.QMessageBox.information") as success, \
         patch("src.ui.main_window.QMessageBox.critical") as error:
        MainWindow._export_settings(widget)
    success.assert_called_once()
    assert str(destination) in success.call_args.args[2]
    error.assert_not_called()
    assert json.loads(destination.read_text(encoding="utf-8"))["font_size"] == 18
    manager.set("font_size", 10)
    manager.import_settings(str(destination))
    assert manager.get("font_size") == 18
    assert list(tmp_path.glob(".alpha_fixer_save_*")) == []


@pytest.mark.parametrize("key,payload", [
    ("custom_shortcuts", "{"), ("custom_shortcuts", "null"),
    ("custom_shortcuts", "[]"), ("custom_shortcuts", '{"settings": 3}'),
    ("custom_shortcuts", '{"settings": null}'), ("custom_shortcuts", '{"settings": []}'),
    ("sa_zone_alphas", ""), ("sa_zone_alphas", "{}"), ("sa_zone_alphas", "[]"),
    ("sa_zone_alphas", "[null]"), ("sa_zone_alphas", "[true]"),
    ("sa_zone_alphas", "[1.5]"), ("sa_zone_alphas", "[-1]"),
    ("sa_zone_alphas", "[256]"), ("sa_zone_alphas", '["not alpha"]'),
    ("sa_zone_alphas", "[NaN]"), ("sa_zone_alphas", json.dumps([128] * 41)),
    ("sa_zone_colors", "{"), ("sa_zone_colors", "null"), ("sa_zone_colors", "[]"),
    ("sa_zone_colors", "[128]"), ("sa_zone_colors", "[[1,2,3]]"),
    ("sa_zone_colors", "[[1,2,3,4,5]]"), ("sa_zone_colors", "[[1,2,3,false]]"),
    ("sa_zone_colors", "[[1,2,3,256]]"), ("sa_zone_colors", "[[1,2,3,-1]]"),
    ("sa_zone_colors", "[[1,2,3,1.5]]"),
    ("sa_zone_colors", json.dumps([[0, 0, 0, 128]] * 41)),
])
def test_invalid_structured_backup_preserves_preferences_and_disk(dialog, tmp_path, key, payload):
    widget, manager = dialog
    manager.set("font_size", 12)
    manager.sync()
    before = {name: manager.get(name) for name in manager.EXPORT_KEYS}
    before_disk = Path(manager._qs.fileName()).read_bytes()
    path = tmp_path / "invalid-structured.json"
    path.write_text(json.dumps({"font_size": 20, key: payload}), encoding="utf-8")
    with pytest.raises(ValueError, match=key):
        manager.import_settings(str(path))
    assert {name: manager.get(name) for name in manager.EXPORT_KEYS} == before
    assert Path(manager._qs.fileName()).read_bytes() == before_disk


@pytest.mark.parametrize("count", [1, 7, 40])
@pytest.mark.parametrize("legacy_strings", [False, True])
def test_structured_backup_boundaries_survive_disk_reload(dialog, tmp_path, count, legacy_strings):
    widget, manager = dialog
    alphas = [0, 255] * 20
    colors = [[0, 255, 0, 255]] * count
    alphas = alphas[:count]
    if legacy_strings:
        alphas = [str(value) for value in alphas]
        colors = [[str(value) for value in color] for color in colors]
    values = {
        "custom_shortcuts": json.dumps({"settings": "Ctrl+Alt+S", "future.action": ""}),
        "sa_zone_alphas": json.dumps(alphas),
        "sa_zone_colors": json.dumps(colors),
    }
    path = tmp_path / "structured.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    manager.import_settings(str(path))
    with patch("src.core.settings_manager._settings_ini_path", return_value=manager._qs.fileName()):
        reloaded = SettingsManager()
    try:
        assert {key: reloaded.get(key) for key in values} == values
        assert reloaded.get_shortcut_binding("settings", "Ctrl+,") == "Ctrl+Alt+S"
        assert reloaded.get_shortcut_binding("future.action", "F1") == "F1"
        expected = [int(value) for value in alphas[:7]]
        assert reloaded.get_sa_zone_alphas() == expected + [128] * (7 - len(expected))
        assert reloaded.get_sa_zone_colors() == [[int(value) for value in color] for color in colors]
    finally:
        reloaded.sync()
        sip.delete(reloaded._qs)


def test_empty_structured_backup_defaults_remain_supported(dialog, tmp_path):
    widget, manager = dialog
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps({"custom_shortcuts": "", "sa_zone_colors": ""}), encoding="utf-8")
    manager.import_settings(str(path))
    assert manager.get_custom_shortcuts() == {}
    assert manager.get_sa_zone_colors() is None


@pytest.mark.parametrize("key,payload", [
    ("theme_data", ""), ("theme_data", "{"), ("theme_data", "null"),
    ("theme_data", "[]"), ("theme_data", '{"name": ""}'),
    ("theme_data", '{"name": 3}'), ("theme_data", '{"accent": null}'),
    ("theme_data", '{"surface": "not-a-color"}'), ("theme_data", '{"_effect": []}'),
    ("theme_data", '{"_cursor": {}}'), ("theme_data", '{"_trail_color": "bad color"}'),
    ("saved_themes", "{"), ("saved_themes", "[]"), ("saved_themes", "null"),
    ("saved_themes", '{"": {}}'), ("saved_themes", '{"  ": {}}'),
    ("saved_themes", '{"Custom": null}'), ("saved_themes", '{"Custom": []}'),
    ("saved_themes", '{"Custom": {"text": "invalid"}}'),
    ("saved_themes", '{"Custom": {"_future_effect": false}}'),
])
def test_invalid_theme_backup_rejects_without_disk_changes(dialog, tmp_path, key, payload):
    widget, manager = dialog
    manager.set("font_size", 12)
    manager.save_named_theme("Existing", {"accent": "#123456"})
    manager.sync()
    before = {name: manager.get(name) for name in manager.EXPORT_KEYS}
    before_disk = Path(manager._qs.fileName()).read_bytes()
    path = tmp_path / "invalid-theme-backup.json"
    path.write_text(json.dumps({"font_size": 20, key: payload}), encoding="utf-8")
    with pytest.raises(ValueError, match=key):
        manager.import_settings(str(path))
    assert {name: manager.get(name) for name in manager.EXPORT_KEYS} == before
    assert Path(manager._qs.fileName()).read_bytes() == before_disk


@pytest.mark.parametrize("theme", [*PRESET_THEMES.values(), *HIDDEN_THEMES.values()],
                         ids=lambda theme: theme["name"])
def test_builtin_theme_backups_remain_valid(dialog, tmp_path, theme):
    widget, manager = dialog
    values = {"theme_data": json.dumps(theme), "saved_themes": json.dumps({theme["name"]: theme})}
    path = tmp_path / "builtin-backup.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    manager.import_settings(str(path))
    assert manager.get_theme()["accent"] == theme["accent"]
    assert manager.get_saved_themes()[theme["name"]]["accent"] == theme["accent"]
    assert {key: manager.get(key) for key in values} == values


def test_partial_legacy_theme_backup_reopens_without_losing_metadata(dialog, tmp_path):
    widget, manager = dialog
    partial = {"accent": "red", "_cursor": "emoji:🧪", "_effect": "future-effect",
               "_future_setting": "future-value", "future_extension": {"enabled": True}}
    values = {"theme_data": json.dumps(partial),
              "saved_themes": json.dumps({"★ Custom": partial})}
    path = tmp_path / "legacy-theme-backup.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    manager.import_settings(str(path))
    with patch("src.core.settings_manager._settings_ini_path", return_value=manager._qs.fileName()):
        reloaded = SettingsManager()
    reopened = SettingsDialog(reloaded)
    try:
        theme = reloaded.get_theme()
        assert theme["background"] == manager._DEFAULT_THEME["background"]
        assert theme["accent"] == "red"
        assert theme["_cursor"] == "emoji:🧪"
        assert theme["future_extension"] == {"enabled": True}
        assert reloaded.get_saved_themes()["★ Custom"]["name"] == "★ Custom"
        assert {key: reloaded.get(key) for key in values} == values
    finally:
        reopened.close()
        sip.delete(reopened)
        reloaded.sync()
        sip.delete(reloaded._qs)


@pytest.mark.parametrize("payload", ["{", "null", "{}",
    json.dumps([None]), json.dumps([[]]),
    *[json.dumps([{"name": "Custom", "description": "Description", **change}])
      for change in [
          {"name": ""}, {"name": 4}, {"description": None},
          {"invert": "false"}, {"binary_cut": 1}, {"builtin": []},
          {"clamp_min": True}, {"clamp_max": 1.5}, {"clamp_min": "invalid"},
          {"clamp_min": -1}, {"clamp_max": 256}, {"threshold": -1},
          {"threshold": 256}, {"threshold": "128"}, {"threshold": False},
          {"alpha_value": "invalid"}, {"alpha_value": 256}, {"alpha_value": 1.5},
      ]],
    json.dumps([{"name": "Missing description"}]),
])
def test_invalid_custom_preset_backup_preserves_disk_and_preferences(dialog, tmp_path, payload):
    widget, manager = dialog
    manager.set("font_size", 12)
    manager.save_custom_presets([{"name": "Existing", "description": "Keep me"}])
    before = {key: manager.get(key) for key in manager.EXPORT_KEYS}
    before_disk = Path(manager._qs.fileName()).read_bytes()
    path = tmp_path / "invalid-presets.json"
    path.write_text(json.dumps({"font_size": 20, "custom_presets": payload}), encoding="utf-8")
    with pytest.raises(ValueError, match="custom_presets"):
        manager.import_settings(str(path))
    assert {key: manager.get(key) for key in manager.EXPORT_KEYS} == before
    assert Path(manager._qs.fileName()).read_bytes() == before_disk


def test_custom_preset_backup_legacy_and_current_records_reload(dialog, tmp_path):
    from src.core.presets import PresetManager

    widget, manager = dialog
    records = [
        {"name": "Legacy fixed", "description": "Legacy", "alpha_value": "128", "mode": "set"},
        {"name": "Legacy default", "description": "Default mode", "alpha_value": 0},
        {"name": "Legacy add", "description": "Range retained", "alpha_value": 10,
         "mode": "add", "clamp_min": "32", "clamp_max": "255"},
        {"name": "Partial range", "description": "Defaults", "clamp_min": None, "clamp_max": None},
        {"name": "Current", "description": "Binary", "clamp_min": 0, "clamp_max": 255,
         "threshold": 128, "binary_cut": True, "invert": False, "builtin": False,
         "future_extension": {"enabled": True}},
    ]
    path = tmp_path / "presets.json"
    raw = json.dumps(records)
    path.write_text(json.dumps({"custom_presets": raw}), encoding="utf-8")
    manager.import_settings(str(path))
    with patch("src.core.settings_manager._settings_ini_path", return_value=manager._qs.fileName()):
        reloaded = SettingsManager()
    try:
        presets = PresetManager(reloaded).custom_presets()
        assert len(presets) == len(records)
        assert [(preset.clamp_min, preset.clamp_max) for preset in presets] == [
            (128, 128), (0, 0), (32, 255), (0, 255), (0, 255)]
        assert all(not preset.builtin for preset in presets)
        assert presets[-1].threshold == 128 and presets[-1].binary_cut
        assert reloaded.get("custom_presets") == raw
    finally:
        reloaded.sync()
        sip.delete(reloaded._qs)


@pytest.mark.parametrize("payload", ["", "[]"])
def test_empty_custom_preset_backup_remains_supported(dialog, tmp_path, payload):
    widget, manager = dialog
    path = tmp_path / "empty-presets.json"
    path.write_text(json.dumps({"custom_presets": payload}), encoding="utf-8")
    manager.import_settings(str(path))
    assert manager.get_custom_presets() == []


def test_backup_round_trip_accepts_every_exportable_default(dialog, tmp_path):
    widget, manager = dialog
    for key in manager.EXPORT_KEYS:
        value = manager._DEFAULTS.get(key, "")
        if key.startswith("history_max_entries_"):
            value = 0
        manager.set(key, value)
    before = {key: manager.get(key) for key in manager.EXPORT_KEYS}
    path = tmp_path / "complete-backup.json"
    manager.export_settings(str(path))
    manager.reset_all()
    assert manager.import_settings(str(path)) == manager.EXPORT_KEYS
    assert {key: manager.get(key) for key in manager.EXPORT_KEYS} == before


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


@pytest.mark.parametrize("enabled,follow,combo,manual,key", [
    ("_bg_flock_check", "_use_theme_flock_check", "_bg_flock_combo", "fish", "bg_flock_style"),
    ("_bg_ambient_check", "_use_theme_ambient_check", "_bg_ambient_combo", "snow", "bg_ambient_type"),
    ("_button_anim_check", "_use_theme_button_anim_check", "_button_anim_style_combo",
     "abduct", "button_anim_style"),
    ("_animated_banner_check", "_banner_use_theme_anim_check", "_banner_anim_combo",
     "glitch", "banner_anim_style"),
])
def test_theme_control_guidance_matches_visible_disabled_manual_restoration(
        dialog, app, enabled, follow, combo, manual, key):
    widget, manager = dialog
    enable_control = getattr(widget, enabled)
    follow_control = getattr(widget, follow)
    style_control = getattr(widget, combo)
    enable_control.setChecked(True)
    follow_control.setChecked(False)
    style_control.setCurrentIndex(style_control.findData(manual))
    widget.show()
    widget._settings_tabs.setCurrentIndex(
        0 if enabled == "_animated_banner_check" else 3
    )
    app.processEvents()
    follow_control.setChecked(True)
    assert style_control.isVisible()
    assert not style_control.isEnabled()
    assert "Visible but disabled" in style_control.toolTip()
    assert "restore your manual" in style_control.toolTip()
    assert manager.get(key) == manual
    follow_control.setChecked(False)
    assert style_control.isEnabled()
    assert style_control.currentData() == manual
    assert manager.get(key) == manual


@pytest.mark.parametrize("theme,expected", [
    ("Gore", "blood"), ("Bat Cave", "shake"), ("Panda Dark", "bubble"),
])
def test_hold_theme_display_matches_runtime_and_preserves_manual_choice(dialog, theme, expected):
    from src.ui.main_window import MainWindow

    widget, manager = dialog
    widget._hold_effects_check.setChecked(True)
    widget._use_theme_hold_check.setChecked(False)
    manual = "shake" if expected != "shake" else "blood"
    widget._hold_key_combo.setCurrentIndex(widget._hold_key_combo.findData(manual))
    widget._use_theme_hold_check.setChecked(True)
    widget._rebuild_theme_combo(select=theme)
    widget._on_preset_selected_live()
    assert widget._hold_key_combo.currentData() == expected
    assert not widget._hold_key_combo.isEnabled()
    assert manager.get("hold_effects_key") == manual
    host = Mock(_settings=manager, _click_effects=Mock())
    MainWindow._apply_hold_effects(host)
    host._click_effects.set_hold_effects.assert_called_once_with(True, expected)
    widget._hold_effects_check.setChecked(False)
    widget._hold_effects_check.setChecked(True)
    assert manager.get("hold_effects_key") == manual
    widget._use_theme_hold_check.setChecked(False)
    assert widget._hold_key_combo.currentData() == manual
    assert widget._hold_key_combo.isEnabled()


@pytest.mark.parametrize("use_theme", [False, True])
def test_loading_hold_preferences_does_not_emit_or_overwrite_them(dialog, use_theme):
    widget, manager = dialog
    preferences = {
        "hold_effects_enabled": True, "use_theme_hold_effects": use_theme,
        "hold_effects_key": "blood",
    }
    for key, value in preferences.items():
        manager.set(key, value)
    changed = Mock()
    widget.settings_changed.connect(changed)
    widget._load_values()
    changed.assert_not_called()
    assert {key: manager.get(key) for key in preferences} == preferences
    reopened = SettingsDialog(manager)
    try:
        assert reopened._hold_effects_check.isChecked()
        assert reopened._use_theme_hold_check.isChecked() == use_theme
        assert reopened._hold_key_combo.currentData() == ("bubble" if use_theme else "blood")
        reopened._use_theme_hold_check.setChecked(False)
        assert reopened._hold_key_combo.currentData() == "blood"
    finally:
        reopened.close()
        sip.delete(reopened)


@pytest.mark.parametrize("cursor", ["Cross", "emoji:🦇", "emoji:🧪"])
def test_cursor_theme_display_preserves_manual_choice_across_reopening(dialog, cursor):
    widget, manager = dialog
    widget._cursor_enable_check.setChecked(True)
    widget._use_theme_cursor_check.setChecked(False)
    widget._cursor_combo.setCurrentText("IBeam")
    widget._theme = dict(manager.get_theme(), _cursor=cursor)
    manager.set_theme(widget._theme)
    widget._use_theme_cursor_check.setChecked(True)
    expected = cursor.removeprefix("emoji:")
    assert widget._cursor_combo.currentText().split(" ", 1)[0] == expected
    assert not widget._cursor_combo.isEnabled()
    assert manager.get("cursor") == "IBeam"
    assert manager.get("last_cursor_key_pref") == "IBeam"
    widget._cursor_enable_check.setChecked(False)
    widget._cursor_enable_check.setChecked(True)
    assert manager.get("cursor") == "IBeam"
    reopened = SettingsDialog(manager)
    try:
        assert reopened._cursor_combo.currentText().split(" ", 1)[0] == expected
        reopened._use_theme_cursor_check.setChecked(False)
        assert reopened._cursor_combo.currentText() == "IBeam"
        assert reopened._cursor_combo.isEnabled()
        assert manager.get("cursor") == "IBeam"
    finally:
        reopened.close()
        sip.delete(reopened)


def test_legacy_manual_emoji_cursor_restores_after_theme_mode(dialog):
    widget, manager = dialog
    manager.set("cursor", "emoji:🧪")
    manager.set("last_cursor_key_pref", "emoji:🧪")
    manager.set("cursor_enabled", True)
    manager.set("use_theme_cursor", True)
    widget._load_values()
    widget._use_theme_cursor_check.setChecked(False)
    assert widget._cursor_combo.currentText() == "🧪"
    assert manager.get("cursor") == "emoji:🧪"


def test_theme_added_emoji_selected_manually_survives_reopening(dialog):
    widget, manager = dialog
    widget._cursor_enable_check.setChecked(True)
    widget._theme = dict(manager.get_theme(), _cursor="emoji:🧪")
    manager.set_theme(widget._theme)
    widget._use_theme_cursor_check.setChecked(True)
    widget._use_theme_cursor_check.setChecked(False)
    widget._cursor_combo.setCurrentText("🧪")
    assert manager.get("cursor") == "emoji:🧪"
    assert manager.get("last_cursor_key_pref") == "emoji:🧪"
    reopened = SettingsDialog(manager)
    try:
        assert reopened._cursor_combo.currentText() == "🧪"
        reopened._cursor_enable_check.setChecked(False)
        reopened._cursor_enable_check.setChecked(True)
        reopened._use_theme_cursor_check.setChecked(True)
        reopened._use_theme_cursor_check.setChecked(False)
        assert reopened._cursor_combo.currentText() == "🧪"
        assert manager.get("cursor") == "emoji:🧪"
    finally:
        reopened.close()
        sip.delete(reopened)


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
