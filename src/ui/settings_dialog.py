"""
Settings / Customization dialog.
"""
import json
import os

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTabWidget, QWidget, QGridLayout, QCheckBox,
    QLineEdit, QColorDialog, QGroupBox, QScrollArea,
    QMessageBox, QInputDialog, QSpinBox, QFileDialog,
)

from .theme_engine import PRESET_THEMES, HIDDEN_THEMES, DEFAULT_THEME, build_stylesheet
from .tooltip_manager import TOOLTIP_MODES
from ..core.settings_manager import DEFAULT_CUSTOM_EMOJI

# Prefix characters used on theme combo items (user-saved = ★, unlocked hidden = 🔓)
_THEME_PREFIX_CHARS = "★🔓 "

# Human-friendly labels for click-effect keys, in display order
_EFFECT_OPTIONS = [
    ("default",      "Default — Pink sparks ✨"),
    ("gore",         "Gore — Blood splatter 🩸"),
    ("bat",          "Bat Cave — Bats fly out 🦇"),
    ("rainbow",      "Rainbow Chaos — Unicorns & rainbows 🌈"),
    ("otter",        "Otter Cove — Cute otter burst 🦦"),
    ("galaxy",       "Galaxy — Stars & space dust ✦"),
    ("galaxy_otter", "Galaxy Otter — Space otters 🦦✦"),
    ("goth",         "Goth — Skulls & shadows 💀"),
    ("neon",         "Neon — Electric lightning bolts ⚡"),
    ("fire",         "Fire — Rising flames 🔥"),
    ("ice",          "Ice — Snowflakes & frost ❄"),
    ("panda",        "Panda — Cute panda shower 🐼"),
    ("sakura",       "Sakura — Cherry blossom petals 🌸"),
    ("fairy",        "Fairy Garden — Glitter & magic wands 🪄✨"),
    ("custom",       "Custom — Your own emoji 🎨"),
]

class ColorButton(QPushButton):
    """A button that shows a color swatch and opens a color picker."""
    color_changed = pyqtSignal(str)

    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self._color = color
        self._update_style()
        self.clicked.connect(self._pick)
        self.setFixedSize(50, 26)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = color
        self._update_style()

    def _update_style(self):
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color}; border: 1px solid #888; border-radius: 4px; }}"
        )

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Pick Color")
        if c.isValid():
            self._color = c.name()
            self._update_style()
            self.color_changed.emit(self._color)


class SettingsDialog(QDialog):
    theme_changed = pyqtSignal(dict)
    settings_changed = pyqtSignal()

    def __init__(self, settings_manager, parent=None, tooltip_mgr=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._theme = settings_manager.get_theme()
        self._color_buttons: dict[str, ColorButton] = {}
        self.setWindowTitle("Settings & Customization 🐼")
        self.setMinimumSize(800, 660)
        self._setup_ui()
        self._load_values()
        if tooltip_mgr is not None:
            self.register_tooltips(tooltip_mgr)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        tabs = QTabWidget()

        # ================================================================
        # ---- Theme tab ----
        # ================================================================
        theme_tab = QWidget()
        tv = QVBoxLayout(theme_tab)
        tv.setContentsMargins(8, 8, 8, 8)
        tv.setSpacing(8)

        # ---- Preset GroupBox ----
        grp_preset_select = QGroupBox("Active Theme Preset")
        psl = QHBoxLayout(grp_preset_select)
        psl.setSpacing(8)
        psl.addWidget(QLabel("Theme:"))
        self._theme_preset_combo = QComboBox()
        self._theme_preset_combo.setMinimumWidth(200)
        self._rebuild_theme_combo()
        psl.addWidget(self._theme_preset_combo, 1)
        self._btn_apply_preset = QPushButton("Apply")
        self._btn_save_theme = QPushButton("Save as…")
        self._btn_delete_theme = QPushButton("Delete")
        self._btn_apply_preset.setMinimumWidth(68)
        self._btn_save_theme.setMinimumWidth(75)
        self._btn_delete_theme.setMinimumWidth(62)
        psl.addWidget(self._btn_apply_preset)
        psl.addWidget(self._btn_save_theme)
        psl.addWidget(self._btn_delete_theme)
        tv.addWidget(grp_preset_select)

        # Color grid
        grp_colors = QGroupBox("Theme Colors")
        color_grid = QGridLayout(grp_colors)
        color_grid.setHorizontalSpacing(10)
        color_grid.setVerticalSpacing(6)
        color_keys = [
            ("background", "Background"),
            ("surface", "Surface"),
            ("primary", "Primary"),
            ("accent", "Accent"),
            ("text", "Text"),
            ("text_secondary", "Text Secondary"),
            ("border", "Border"),
            ("success", "Success"),
            ("warning", "Warning"),
            ("error", "Error"),
            ("button_bg", "Button BG"),
            ("button_hover", "Button Hover"),
            ("progress_bar", "Progress Bar"),
            ("input_bg", "Input BG"),
            ("scrollbar_handle", "Scrollbar Handle"),
        ]
        for i, (key, label) in enumerate(color_keys):
            row, col = divmod(i, 2)
            color_grid.addWidget(QLabel(label + ":"), row, col * 3)
            btn = ColorButton(self._theme.get(key, "#888888"))
            btn.color_changed.connect(lambda c, k=key: self._on_color_changed(k, c))
            self._color_buttons[key] = btn
            color_grid.addWidget(btn, row, col * 3 + 1)
            color_grid.setColumnStretch(col * 3 + 2, 1)

        scroll = QScrollArea()
        scroll.setWidget(grp_colors)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(220)
        scroll.setMaximumHeight(320)
        tv.addWidget(scroll)

        # ---- Effect + Emoji in a single row of GroupBoxes ----
        effect_emoji_row = QHBoxLayout()
        effect_emoji_row.setSpacing(8)

        grp_effect = QGroupBox("Click Effect Style")
        effect_layout = QVBoxLayout(grp_effect)
        effect_layout.setSpacing(6)
        effect_inner = QHBoxLayout()
        effect_inner.addWidget(QLabel("Effect:"))
        self._effect_combo = QComboBox()
        self._effect_combo.setMinimumWidth(220)
        for key, label in _EFFECT_OPTIONS:
            self._effect_combo.addItem(label, userData=key)
        self._effect_combo.setToolTip(
            "Choose the click particle effect for this theme.\n"
            "Select 'Custom' to use your own emoji as particles."
        )
        effect_inner.addWidget(self._effect_combo, 1)
        effect_layout.addLayout(effect_inner)
        effect_emoji_row.addWidget(grp_effect, 3)

        grp_emoji = QGroupBox("Custom Emoji  (with 'Custom' effect)")
        emoji_v = QVBoxLayout(grp_emoji)
        emoji_v.setSpacing(6)
        emoji_row = QHBoxLayout()
        self._emoji_input = QLineEdit()
        self._emoji_input.setPlaceholderText("e.g.  🐼 🎉 💥  (space-separated)")
        self._btn_emoji_add = QPushButton("Add")
        self._btn_emoji_clear = QPushButton("Clear")
        self._btn_emoji_add.setFixedWidth(50)
        self._btn_emoji_clear.setFixedWidth(52)
        emoji_row.addWidget(self._emoji_input, 1)
        emoji_row.addWidget(self._btn_emoji_add)
        emoji_row.addWidget(self._btn_emoji_clear)
        emoji_v.addLayout(emoji_row)
        self._emoji_display = QLabel("")
        self._emoji_display.setWordWrap(True)
        self._emoji_display.setObjectName("subheader")
        emoji_v.addWidget(self._emoji_display)
        effect_emoji_row.addWidget(grp_emoji, 2)

        tv.addLayout(effect_emoji_row)
        tv.addStretch(1)
        tabs.addTab(theme_tab, "🎨 Theme")

        # ================================================================
        # ---- General tab ----
        # ================================================================
        gen_tab = QWidget()
        gv = QVBoxLayout(gen_tab)
        gv.setContentsMargins(8, 8, 8, 8)
        gv.setSpacing(8)

        # ---- Sound GroupBox ----
        grp_sound = QGroupBox("🔊  Sound")
        sound_gl = QGridLayout(grp_sound)
        sound_gl.setColumnStretch(1, 1)
        sound_gl.setHorizontalSpacing(10)
        sound_gl.setVerticalSpacing(6)
        self._sound_check = QCheckBox("Enable click sounds")
        sound_gl.addWidget(self._sound_check, 0, 0, 1, 2)
        sound_gl.addWidget(QLabel("Custom .wav:"), 1, 0)
        sound_row = QHBoxLayout()
        self._click_sound_edit = QLineEdit()
        self._click_sound_edit.setPlaceholderText("Leave blank for built-in sound")
        self._btn_sound_browse = QPushButton("Browse…")
        sound_row.addWidget(self._click_sound_edit, 1)
        sound_row.addWidget(self._btn_sound_browse)
        sound_gl.addLayout(sound_row, 1, 1)
        gv.addWidget(grp_sound)

        # ---- Mouse Trail GroupBox ----
        grp_trail = QGroupBox("🖱  Mouse Trail")
        trail_gl = QGridLayout(grp_trail)
        trail_gl.setColumnStretch(1, 1)
        trail_gl.setHorizontalSpacing(10)
        trail_gl.setVerticalSpacing(6)
        self._trail_check = QCheckBox("Enable mouse trail")
        trail_gl.addWidget(self._trail_check, 0, 0, 1, 2)
        trail_gl.addWidget(QLabel("Trail Color:"), 1, 0)
        self._trail_color_btn = ColorButton("#e94560")
        trail_gl.addWidget(self._trail_color_btn, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self._use_theme_trail_check = QCheckBox(
            "Use theme trail  (auto-color + fairy dust on Fairy Garden)"
        )
        self._use_theme_trail_check.setToolTip(
            "When enabled the trail color is chosen automatically to match\n"
            "the active theme.  Fairy Garden gets a sparkling emoji trail."
        )
        trail_gl.addWidget(self._use_theme_trail_check, 2, 0, 1, 2)
        self._use_theme_trail_check.toggled.connect(
            lambda checked: self._trail_color_btn.setEnabled(not checked)
        )
        gv.addWidget(grp_trail)

        # ---- Cursor GroupBox ----
        grp_cursor = QGroupBox("🖱  Cursor")
        cursor_gl = QGridLayout(grp_cursor)
        cursor_gl.setColumnStretch(1, 1)
        cursor_gl.setHorizontalSpacing(10)
        cursor_gl.setVerticalSpacing(6)
        cursor_gl.addWidget(QLabel("Cursor Style:"), 0, 0)
        self._cursor_combo = QComboBox()
        self._cursor_combo.addItems([
            "Default", "Cross", "Pointing Hand", "Open Hand",
            "Hourglass", "Forbidden", "IBeam", "Size All", "Blank",
        ])
        cursor_gl.addWidget(self._cursor_combo, 0, 1)
        self._use_theme_cursor_check = QCheckBox(
            "Use theme cursor  (overrides the style above)"
        )
        self._use_theme_cursor_check.setToolTip(
            "When enabled the cursor shape is chosen automatically to match the\n"
            "active theme — e.g. Otter Cove gets the 🤘 rock-on emoji cursor."
        )
        cursor_gl.addWidget(self._use_theme_cursor_check, 1, 0, 1, 2)
        self._use_theme_cursor_check.toggled.connect(
            lambda checked: self._cursor_combo.setEnabled(not checked)
        )
        gv.addWidget(grp_cursor)

        # ---- Appearance & FX GroupBox ----
        grp_misc = QGroupBox("🎨  Appearance & Effects")
        misc_gl = QGridLayout(grp_misc)
        misc_gl.setColumnStretch(1, 1)
        misc_gl.setHorizontalSpacing(10)
        misc_gl.setVerticalSpacing(6)
        misc_gl.addWidget(QLabel("Font Size (pt):"), 0, 0)
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 24)
        self._font_size_spin.setValue(10)
        self._font_size_spin.setMaximumWidth(80)
        misc_gl.addWidget(self._font_size_spin, 0, 1, Qt.AlignmentFlag.AlignLeft)
        self._click_effects_check = QCheckBox("Enable per-theme click particle effects")
        misc_gl.addWidget(self._click_effects_check, 1, 0, 1, 2)
        misc_gl.addWidget(QLabel("Tooltip Mode:"), 2, 0)
        self._tooltip_mode_combo = QComboBox()
        self._tooltip_mode_combo.addItems(TOOLTIP_MODES)
        self._tooltip_mode_combo.setToolTip(
            "Controls how tooltips appear throughout the app.\n"
            "No Filter 🤬 is the best mode – trust us."
        )
        self._tooltip_mode_combo.setMaximumWidth(220)
        misc_gl.addWidget(self._tooltip_mode_combo, 2, 1, Qt.AlignmentFlag.AlignLeft)
        gv.addWidget(grp_misc)

        gv.addStretch(1)
        tabs.addTab(gen_tab, "⚙ General")

        layout.addWidget(tabs, 1)

        # ---- Dialog buttons ----
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_ok = QPushButton("Apply & Close")
        self._btn_ok.setObjectName("accent")
        self._btn_ok.setMinimumWidth(120)
        self._btn_cancel = QPushButton("Cancel")
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_ok)
        layout.addLayout(btn_row)

        # Connections
        self._btn_apply_preset.clicked.connect(self._apply_preset)
        self._btn_save_theme.clicked.connect(self._save_custom_theme)
        self._btn_delete_theme.clicked.connect(self._delete_custom_theme)
        self._btn_ok.clicked.connect(self._apply_and_close)
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_sound_browse.clicked.connect(self._browse_sound)
        self._theme_preset_combo.currentTextChanged.connect(self._update_delete_btn)
        self._effect_combo.currentIndexChanged.connect(self._on_effect_changed)
        self._btn_emoji_add.clicked.connect(self._add_emoji)
        self._btn_emoji_clear.clicked.connect(self._clear_emoji)

    # ------------------------------------------------------------------
    # Theme combo helpers
    # ------------------------------------------------------------------

    def _rebuild_theme_combo(self, select: str = ""):
        self._theme_preset_combo.blockSignals(True)
        self._theme_preset_combo.clear()
        for name in PRESET_THEMES:
            self._theme_preset_combo.addItem(name)
        # Show hidden themes that have been unlocked
        for name, t in HIDDEN_THEMES.items():
            unlock_key = f"unlock_{t.get('_unlock', '')}"
            if self._settings.get(unlock_key, False):
                self._theme_preset_combo.addItem(f"🔓 {name}")
        saved = self._settings.get_saved_themes()
        if saved:
            self._theme_preset_combo.insertSeparator(self._theme_preset_combo.count())
            for name in sorted(saved):
                self._theme_preset_combo.addItem(f"★ {name}")
        self._theme_preset_combo.addItem("— Custom —")
        if select:
            idx = self._theme_preset_combo.findText(select)
            if idx >= 0:
                self._theme_preset_combo.setCurrentIndex(idx)
        self._theme_preset_combo.blockSignals(False)
        self._update_delete_btn()

    def _update_delete_btn(self):
        # _rebuild_theme_combo() is called during _setup_ui() before
        # _btn_delete_theme is created; guard against that ordering.
        if not hasattr(self, "_btn_delete_theme"):
            return
        name = self._theme_preset_combo.currentText().lstrip(_THEME_PREFIX_CHARS)
        is_user = name in self._settings.get_saved_themes()
        self._btn_delete_theme.setEnabled(is_user)

    # ------------------------------------------------------------------
    # Load persisted values into controls
    # ------------------------------------------------------------------

    def _load_values(self):
        t = self._theme
        for key, btn in self._color_buttons.items():
            btn.set_color(t.get(key, "#888888"))

        theme_name = t.get("name", "")
        idx = self._theme_preset_combo.findText(theme_name)
        if idx < 0:
            idx = self._theme_preset_combo.findText(f"★ {theme_name}")
        if idx < 0:
            idx = self._theme_preset_combo.findText(f"🔓 {theme_name}")
        self._theme_preset_combo.setCurrentIndex(
            idx if idx >= 0 else self._theme_preset_combo.count() - 1
        )

        # Effect combo
        self._set_effect_combo(t.get("_effect", "default"))

        # Custom emoji
        self._update_emoji_display()

        self._sound_check.setChecked(self._settings.get("sound_enabled", True))
        self._click_sound_edit.setText(self._settings.get("click_sound_path", ""))
        self._trail_check.setChecked(self._settings.get("trail_enabled", False))
        self._trail_color_btn.set_color(self._settings.get("trail_color", "#e94560"))
        use_theme_trail = self._settings.get("use_theme_trail", False)
        self._use_theme_trail_check.setChecked(use_theme_trail)
        self._trail_color_btn.setEnabled(not use_theme_trail)
        cursor_val = self._settings.get("cursor", "Default")
        idx = self._cursor_combo.findText(cursor_val)
        self._cursor_combo.setCurrentIndex(max(idx, 0))
        use_theme_cur = self._settings.get("use_theme_cursor", False)
        self._use_theme_cursor_check.setChecked(use_theme_cur)
        self._cursor_combo.setEnabled(not use_theme_cur)
        self._font_size_spin.setValue(self._settings.get("font_size", 10))
        self._click_effects_check.setChecked(
            self._settings.get("click_effects_enabled", True)
        )
        mode_val = self._settings.get("tooltip_mode", "Normal")
        idx_m = self._tooltip_mode_combo.findText(mode_val)
        self._tooltip_mode_combo.setCurrentIndex(max(idx_m, 0))

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr) -> None:
        """Register dialog widgets with the TooltipManager for cycling tips."""
        mgr.register(self._theme_preset_combo, "theme_combo")
        mgr.register(self._effect_combo, "effect_combo")
        mgr.register(self._emoji_input, "custom_emoji")
        mgr.register(self._tooltip_mode_combo, "tooltip_mode_combo")
        mgr.register(self._sound_check, "sound_check")
        mgr.register(self._trail_check, "trail_check")
        mgr.register(self._trail_color_btn, "trail_color")
        mgr.register(self._use_theme_trail_check, "use_theme_trail")
        mgr.register(self._cursor_combo, "cursor_combo")
        mgr.register(self._use_theme_cursor_check, "use_theme_cursor")
        mgr.register(self._font_size_spin, "font_size")
        mgr.register(self._click_effects_check, "click_effects_check")

    # ------------------------------------------------------------------
    # Color-button callback
    # ------------------------------------------------------------------

    def _on_color_changed(self, key: str, color: str):
        self._theme[key] = color

    # ------------------------------------------------------------------
    # Preset & custom theme management
    # ------------------------------------------------------------------

    def _apply_preset(self):
        raw_name = self._theme_preset_combo.currentText()
        name = raw_name.lstrip(_THEME_PREFIX_CHARS)
        if name in PRESET_THEMES:
            self._theme = dict(PRESET_THEMES[name])
        elif name in HIDDEN_THEMES:
            self._theme = dict(HIDDEN_THEMES[name])
        else:
            saved = self._settings.get_saved_themes()
            if name in saved:
                self._theme = dict(saved[name])
            else:
                return  # "— Custom —" or separator
        for key, btn in self._color_buttons.items():
            btn.set_color(self._theme.get(key, "#888888"))
        self._set_effect_combo(self._theme.get("_effect", "default"))

    def _save_custom_theme(self):
        name, ok = QInputDialog.getText(self, "Save Theme", "Theme name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in PRESET_THEMES:
            QMessageBox.warning(self, "Save Theme",
                                f"'{name}' is a built-in theme name. Choose a different name.")
            return
        # Ensure the stored dict has the correct display name and the current
        # effect key (the latter may not have been written if the user never
        # changed the effect combo away from its pre-selected value).
        self._theme["name"] = name
        self._theme["_effect"] = self._effect_combo.currentData() or "default"
        self._settings.save_named_theme(name, dict(self._theme))
        self._rebuild_theme_combo(select=f"★ {name}")
        QMessageBox.information(self, "Save Theme", f"Theme '{name}' saved.")

    def _delete_custom_theme(self):
        raw_name = self._theme_preset_combo.currentText().lstrip(_THEME_PREFIX_CHARS)
        reply = QMessageBox.question(
            self, "Delete Theme",
            f"Delete saved theme '{raw_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._settings.delete_named_theme(raw_name)
            self._rebuild_theme_combo()

    # ------------------------------------------------------------------
    # Effect combo helpers
    # ------------------------------------------------------------------

    def _set_effect_combo(self, effect_key: str) -> None:
        """Set the effect combo to the entry matching effect_key."""
        for i in range(self._effect_combo.count()):
            if self._effect_combo.itemData(i) == effect_key:
                self._effect_combo.setCurrentIndex(i)
                return
        self._effect_combo.setCurrentIndex(0)  # fallback to Default

    def _on_effect_changed(self) -> None:
        """Sync the selected effect key back into the working theme dict."""
        self._theme["_effect"] = self._effect_combo.currentData() or "default"

    # ------------------------------------------------------------------
    # Custom emoji helpers
    # ------------------------------------------------------------------

    def _get_emoji_list(self) -> list[str]:
        raw = self._settings.get("custom_emoji", DEFAULT_CUSTOM_EMOJI)
        return raw.split() if raw.strip() else []

    def _update_emoji_display(self) -> None:
        items = self._get_emoji_list()
        self._emoji_display.setText(
            "  ".join(items) if items else "(none — add some emoji above)"
        )

    def _add_emoji(self) -> None:
        text = self._emoji_input.text().strip()
        if not text:
            return
        current = self._get_emoji_list()
        for item in text.split():
            if item and item not in current:
                current.append(item)
        self._settings.set("custom_emoji", " ".join(current))
        self._update_emoji_display()
        self._emoji_input.clear()

    def _clear_emoji(self) -> None:
        self._settings.set("custom_emoji", "")
        self._update_emoji_display()

    # ------------------------------------------------------------------
    # Sound file browser
    # ------------------------------------------------------------------

    def _browse_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Click Sound", "",
            "WAV Files (*.wav);;All Files (*)",
        )
        if path:
            self._click_sound_edit.setText(path)

    # ------------------------------------------------------------------
    # Apply & close
    # ------------------------------------------------------------------

    def _apply_and_close(self):
        # Ensure the chosen effect is written into the theme dict
        self._theme["_effect"] = self._effect_combo.currentData() or "default"
        self._settings.set_theme(self._theme)
        self._settings.set("sound_enabled", self._sound_check.isChecked())
        self._settings.set("click_sound_path", self._click_sound_edit.text().strip())
        self._settings.set("trail_enabled", self._trail_check.isChecked())
        self._settings.set("trail_color", self._trail_color_btn.color())
        self._settings.set("use_theme_trail", self._use_theme_trail_check.isChecked())
        self._settings.set("cursor", self._cursor_combo.currentText())
        self._settings.set("use_theme_cursor", self._use_theme_cursor_check.isChecked())
        self._settings.set("font_size", self._font_size_spin.value())
        self._settings.set("click_effects_enabled", self._click_effects_check.isChecked())
        self._settings.set("tooltip_mode", self._tooltip_mode_combo.currentText())
        self.theme_changed.emit(self._theme)
        self.settings_changed.emit()
        self.accept()
