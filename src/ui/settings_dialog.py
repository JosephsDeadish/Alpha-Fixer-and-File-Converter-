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
    ("sparkle",      "Sparkle — Glittering star crystals ✨❄"),
    ("panda",        "Panda — Cute panda shower 🐼"),
    ("sakura",       "Sakura — Cherry blossom petals 🌸"),
    ("fairy",        "Fairy Garden — Glitter & magic wands 🪄✨"),
    ("ocean",        "Deep Ocean — Bubbles & sea creatures 🦑🫧"),
    ("ripple",       "Ripple — Water splash & waves 💧🌊"),
    ("mermaid",      "Mermaid — Magical sea creatures 🧜🐠"),
    ("shark",        "Shark — Bite & oceanic carnage 🦈🩸"),
    ("alien",        "Alien — UFO abduction beams 🛸👽"),
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
        ps_vl = QVBoxLayout(grp_preset_select)
        ps_vl.setSpacing(6)

        # Search/filter row
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("🔍 Filter:"))
        self._theme_search = QLineEdit()
        self._theme_search.setPlaceholderText("Type to filter themes…")
        self._theme_search.setClearButtonEnabled(True)
        self._theme_search.setToolTip(
            "Type part of a theme name to filter the list below."
        )
        search_row.addWidget(self._theme_search, 1)
        ps_vl.addLayout(search_row)

        # Preset selection row
        psl = QHBoxLayout()
        psl.setSpacing(8)
        psl.addWidget(QLabel("Theme:"))
        self._theme_preset_combo = QComboBox()
        self._theme_preset_combo.setMinimumWidth(200)
        self._rebuild_theme_combo()
        psl.addWidget(self._theme_preset_combo, 1)
        self._btn_save_theme = QPushButton("Save as…")
        self._btn_delete_theme = QPushButton("Delete")
        self._btn_export_theme = QPushButton("Export…")
        self._btn_import_theme = QPushButton("Import…")
        self._btn_save_theme.setMinimumWidth(75)
        self._btn_delete_theme.setMinimumWidth(62)
        self._btn_export_theme.setMinimumWidth(70)
        self._btn_import_theme.setMinimumWidth(70)
        self._btn_export_theme.setToolTip("Export the current theme settings to a JSON file.")
        self._btn_import_theme.setToolTip("Import a theme from a JSON file.")
        psl.addWidget(self._btn_save_theme)
        psl.addWidget(self._btn_delete_theme)
        psl.addWidget(self._btn_export_theme)
        psl.addWidget(self._btn_import_theme)
        ps_vl.addLayout(psl)
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
        # On/off + use-theme row (mirrors the Mouse Trail group layout)
        self._click_effects_theme_check = QCheckBox("Enable click effects")
        effect_layout.addWidget(self._click_effects_theme_check)
        self._use_theme_effect_check = QCheckBox(
            "Use theme effect  (auto-selects the matching effect for the active theme)"
        )
        self._use_theme_effect_check.setToolTip(
            "When enabled the click effect is chosen automatically to match\n"
            "the active theme — e.g. Gore gets blood splatter, Bat Cave gets bats."
        )
        effect_layout.addWidget(self._use_theme_effect_check)
        self._use_theme_effect_check.toggled.connect(
            lambda checked: self._effect_combo.setEnabled(not checked)
        )
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

        # ---- Mouse Trail and Cursor GroupBoxes (belong with theme settings) ----
        mouse_row = QHBoxLayout()
        mouse_row.setSpacing(8)

        grp_trail = QGroupBox("Mouse Trail")
        trail_gl = QGridLayout(grp_trail)
        trail_gl.setColumnStretch(1, 1)
        trail_gl.setHorizontalSpacing(10)
        trail_gl.setVerticalSpacing(6)
        self._trail_check = QCheckBox("Enable mouse trail")
        trail_gl.addWidget(self._trail_check, 0, 0, 1, 2)
        trail_gl.addWidget(QLabel("Trail Color:"), 1, 0)
        self._trail_color_btn = ColorButton("#e94560")
        trail_gl.addWidget(self._trail_color_btn, 1, 1, Qt.AlignmentFlag.AlignLeft)
        trail_gl.addWidget(QLabel("Trail Style:"), 2, 0)
        self._trail_style_combo = QComboBox()
        self._trail_style_combo.addItems([
            "Dots (default)",
            "Ribbon / Noodle",
            "Comet tail",
            "Fairy dust ✨",
            "Wave / Ocean 🌊",
            "Sparkle / Ice ❄",
        ])
        self._trail_style_combo.setToolTip(
            "Choose the visual style of the mouse trail.\n"
            "Ribbon draws a connected smooth line, Comet draws a tapered tail,\n"
            "Fairy/Wave/Sparkle use themed emoji that float and fade."
        )
        trail_gl.addWidget(self._trail_style_combo, 2, 1)
        self._use_theme_trail_check = QCheckBox(
            "Use theme trail  (auto-color + special style per effect)"
        )
        self._use_theme_trail_check.setToolTip(
            "When enabled the trail color and style are chosen automatically to match\n"
            "the active theme effect.  Fairy Garden gets sparkle fairy dust (✨💫⭐),\n"
            "Ocean/Mermaid get wave emoji (🫧💧🌊), Ice/Sparkle get crystal emoji (✦❄✧)."
        )
        trail_gl.addWidget(self._use_theme_trail_check, 3, 0, 1, 2)
        self._use_theme_trail_check.toggled.connect(
            lambda checked: self._trail_color_btn.setEnabled(not checked)
        )
        self._use_theme_trail_check.toggled.connect(
            lambda checked: self._trail_style_combo.setEnabled(not checked)
        )
        mouse_row.addWidget(grp_trail, 1)

        grp_cursor = QGroupBox("Cursor")
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
        mouse_row.addWidget(grp_cursor, 1)

        tv.addLayout(mouse_row)

        # ---- Sound GroupBox (theme-related — click sounds follow the theme) ----
        grp_sound = QGroupBox("Sound")
        sound_gl = QGridLayout(grp_sound)
        sound_gl.setColumnStretch(1, 1)
        sound_gl.setHorizontalSpacing(10)
        sound_gl.setVerticalSpacing(6)
        self._sound_check = QCheckBox("Enable click sounds (off by default)")
        self._sound_check.setToolTip(
            "Play a sound on each click. Off by default.\n"
            "Your choice is remembered between sessions."
        )
        sound_gl.addWidget(self._sound_check, 0, 0, 1, 2)
        sound_gl.addWidget(QLabel("Custom .wav:"), 1, 0)
        sound_row = QHBoxLayout()
        self._click_sound_edit = QLineEdit()
        self._click_sound_edit.setPlaceholderText("Leave blank for built-in sound")
        self._btn_sound_browse = QPushButton("Browse…")
        sound_row.addWidget(self._click_sound_edit, 1)
        sound_row.addWidget(self._btn_sound_browse)
        sound_gl.addLayout(sound_row, 1, 1)
        tv.addWidget(grp_sound)

        tv.addStretch(1)

        # Wrap the theme tab contents in a scroll area so all controls are always
        # reachable regardless of screen/window size.
        theme_scroll = QScrollArea()
        theme_scroll.setWidget(theme_tab)
        theme_scroll.setWidgetResizable(True)
        theme_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        tabs.addTab(theme_scroll, "🎨 Theme")

        # ================================================================
        # ---- General tab ----
        # ================================================================
        gen_tab = QWidget()
        gv = QVBoxLayout(gen_tab)
        gv.setContentsMargins(8, 8, 8, 8)
        gv.setSpacing(8)

        # ---- Appearance & FX GroupBox ----
        grp_misc = QGroupBox("Appearance && Effects")
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

        # Wrap the general tab contents in a scroll area so checkboxes
        # are always reachable regardless of screen/window size.
        gen_scroll = QScrollArea()
        gen_scroll.setWidget(gen_tab)
        gen_scroll.setWidgetResizable(True)
        gen_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        tabs.addTab(gen_scroll, "⚙ General")

        layout.addWidget(tabs, 1)

        # ---- Dialog button: just "Close" (settings already saved live) ----
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_close = QPushButton("Close")
        self._btn_close.setObjectName("accent")
        self._btn_close.setMinimumWidth(100)
        btn_row.addWidget(self._btn_close)
        layout.addLayout(btn_row)

        # Connections
        # Preset combo: selecting a theme immediately applies it (no Apply button needed)
        self._theme_preset_combo.currentTextChanged.connect(self._on_preset_selected_live)
        self._theme_search.textChanged.connect(self._on_theme_search_changed)
        self._btn_save_theme.clicked.connect(self._save_custom_theme)
        self._btn_delete_theme.clicked.connect(self._delete_custom_theme)
        self._btn_export_theme.clicked.connect(self._export_theme)
        self._btn_import_theme.clicked.connect(self._import_theme)
        self._btn_close.clicked.connect(self.accept)
        self._btn_sound_browse.clicked.connect(self._browse_sound)
        self._effect_combo.currentIndexChanged.connect(self._on_effect_changed_live)
        self._btn_emoji_add.clicked.connect(self._add_emoji)
        self._btn_emoji_clear.clicked.connect(self._clear_emoji)
        # All controls save+emit live
        self._sound_check.toggled.connect(self._on_sound_changed)
        self._click_sound_edit.editingFinished.connect(self._on_sound_path_changed)
        self._trail_check.toggled.connect(self._on_trail_changed)
        self._trail_color_btn.color_changed.connect(self._on_trail_color_changed)
        self._use_theme_trail_check.toggled.connect(self._on_trail_changed)
        self._trail_style_combo.currentIndexChanged.connect(self._on_trail_style_changed)
        self._cursor_combo.currentTextChanged.connect(self._on_cursor_changed)
        self._use_theme_cursor_check.toggled.connect(self._on_cursor_changed)
        self._font_size_spin.valueChanged.connect(self._on_font_size_changed)
        self._click_effects_check.toggled.connect(self._on_effects_enabled_changed)
        self._click_effects_theme_check.toggled.connect(self._on_effects_enabled_changed)
        self._use_theme_effect_check.toggled.connect(self._on_use_theme_effect_changed)
        self._tooltip_mode_combo.currentTextChanged.connect(self._on_tooltip_mode_changed)

    # ------------------------------------------------------------------
    # Theme combo helpers
    # ------------------------------------------------------------------

    def _on_theme_search_changed(self, text: str) -> None:
        """Filter the theme combo to show only themes matching *text*."""
        current = self._theme_preset_combo.currentText()
        self._rebuild_theme_combo(select=current, filter_text=text)

    def _current_filter_text(self) -> str:
        """Return the current theme search filter text."""
        return self._theme_search.text()

    def _rebuild_theme_combo(self, select: str = "", filter_text: str = ""):
        self._theme_preset_combo.blockSignals(True)
        self._theme_preset_combo.clear()
        needle = filter_text.lower().strip()

        def _matches(name: str) -> bool:
            return not needle or needle in name.lower()

        for name in PRESET_THEMES:
            if _matches(name):
                self._theme_preset_combo.addItem(name)
        # Show hidden themes that have been unlocked
        for name, t in HIDDEN_THEMES.items():
            unlock_key = f"unlock_{t.get('_unlock', '')}"
            if self._settings.get(unlock_key, False) and _matches(name):
                self._theme_preset_combo.addItem(f"🔓 {name}")
        saved = self._settings.get_saved_themes()
        filtered_saved = [n for n in sorted(saved) if _matches(n)]
        if filtered_saved:
            self._theme_preset_combo.insertSeparator(self._theme_preset_combo.count())
            for name in filtered_saved:
                self._theme_preset_combo.addItem(f"★ {name}")
        if not needle:
            self._theme_preset_combo.addItem("— Custom —")
        if select:
            idx = self._theme_preset_combo.findText(select)
            if idx >= 0:
                self._theme_preset_combo.setCurrentIndex(idx)
        self._theme_preset_combo.blockSignals(False)
        self._update_delete_btn()

    def _update_delete_btn(self):
        if not hasattr(self, "_btn_delete_theme"):
            return
        name = self._theme_preset_combo.currentText().lstrip(_THEME_PREFIX_CHARS)
        is_user = name in self._settings.get_saved_themes()
        self._btn_delete_theme.setEnabled(is_user)

    # ------------------------------------------------------------------
    # Load persisted values into controls
    # ------------------------------------------------------------------

    def _load_values(self):
        """Populate all controls from persisted settings WITHOUT firing live-update signals."""
        t = self._theme
        # Block signals for all controls so loading initial values doesn't
        # trigger save-and-emit loops.
        controls = [
            self._theme_preset_combo, self._effect_combo, self._sound_check,
            self._click_sound_edit, self._trail_check, self._trail_color_btn,
            self._trail_style_combo, self._use_theme_trail_check, self._cursor_combo,
            self._use_theme_cursor_check, self._font_size_spin,
            self._click_effects_check, self._click_effects_theme_check,
            self._use_theme_effect_check, self._tooltip_mode_combo,
        ]
        for c in controls:
            c.blockSignals(True)

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

        self._set_effect_combo(t.get("_effect", "default"))
        self._update_emoji_display()

        self._sound_check.setChecked(self._settings.get("sound_enabled", False))
        self._click_sound_edit.setText(self._settings.get("click_sound_path", ""))
        self._trail_check.setChecked(self._settings.get("trail_enabled", False))
        self._trail_color_btn.set_color(self._settings.get("trail_color", "#e94560"))
        use_theme_trail = self._settings.get("use_theme_trail", False)
        self._use_theme_trail_check.setChecked(use_theme_trail)
        self._trail_color_btn.setEnabled(not use_theme_trail)
        self._trail_style_combo.setEnabled(not use_theme_trail)
        # Load persisted trail style into combo
        _TRAIL_STYLE_MAP = {
            "dots": 0, "ribbon": 1, "comet": 2, "fairy": 3, "wave": 4, "sparkle": 5,
        }
        saved_style = self._settings.get("trail_style", "dots")
        self._trail_style_combo.setCurrentIndex(_TRAIL_STYLE_MAP.get(saved_style, 0))
        cursor_val = self._settings.get("cursor", "Default")
        idx = self._cursor_combo.findText(cursor_val)
        self._cursor_combo.setCurrentIndex(max(idx, 0))
        use_theme_cur = self._settings.get("use_theme_cursor", False)
        self._use_theme_cursor_check.setChecked(use_theme_cur)
        self._cursor_combo.setEnabled(not use_theme_cur)
        self._font_size_spin.setValue(self._settings.get("font_size", 10))
        self._click_effects_check.setChecked(
            self._settings.get("click_effects_enabled", False)
        )
        # Sync Theme-tab on/off + use-theme checkboxes with persisted values
        self._click_effects_theme_check.setChecked(
            self._settings.get("click_effects_enabled", False)
        )
        use_theme_effect = self._settings.get("use_theme_effect", False)
        self._use_theme_effect_check.setChecked(use_theme_effect)
        self._effect_combo.setEnabled(not use_theme_effect)
        mode_val = self._settings.get("tooltip_mode") or "No Filter 🤬"
        idx_m = self._tooltip_mode_combo.findText(mode_val)
        self._tooltip_mode_combo.setCurrentIndex(max(idx_m, 0))

        for c in controls:
            c.blockSignals(False)

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr) -> None:
        """Register dialog widgets with the TooltipManager for cycling tips."""
        mgr.register(self._theme_search, "theme_search")
        mgr.register(self._theme_preset_combo, "theme_combo")
        mgr.register(self._effect_combo, "effect_combo")
        mgr.register(self._emoji_input, "custom_emoji")
        mgr.register(self._tooltip_mode_combo, "tooltip_mode_combo")
        mgr.register(self._sound_check, "sound_check")
        mgr.register(self._trail_check, "trail_check")
        mgr.register(self._trail_color_btn, "trail_color")
        mgr.register(self._trail_style_combo, "trail_style")
        mgr.register(self._use_theme_trail_check, "use_theme_trail")
        mgr.register(self._cursor_combo, "cursor_combo")
        mgr.register(self._use_theme_cursor_check, "use_theme_cursor")
        mgr.register(self._font_size_spin, "font_size")
        mgr.register(self._click_effects_check, "click_effects_check")
        mgr.register(self._click_effects_theme_check, "click_effects_check")
        mgr.register(self._use_theme_effect_check, "use_theme_effect")

    # ------------------------------------------------------------------
    # Color-button callback — live apply
    # ------------------------------------------------------------------

    def _on_color_changed(self, key: str, color: str):
        self._theme[key] = color
        self._settings.set_theme(self._theme)
        self.theme_changed.emit(self._theme)

    # ------------------------------------------------------------------
    # Preset & custom theme management
    # ------------------------------------------------------------------

    def _on_preset_selected_live(self, _text: str = "") -> None:
        """Immediately load + apply the selected preset when combo changes."""
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
                return  # "— Custom —" or separator line
        # Update color swatches to reflect the new preset
        for key, btn in self._color_buttons.items():
            btn.set_color(self._theme.get(key, "#888888"))
        self._set_effect_combo(self._theme.get("_effect", "default"))
        # Persist and broadcast immediately
        self._settings.set_theme(self._theme)
        self.theme_changed.emit(self._theme)
        self._update_delete_btn()

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
        self._rebuild_theme_combo(select=f"★ {name}", filter_text=self._current_filter_text())
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
            self._rebuild_theme_combo(filter_text=self._current_filter_text())

    def _export_theme(self):
        """Export the current theme to a JSON file chosen by the user."""
        theme_name = self._theme.get("name", "my_theme")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Theme",
            f"{theme_name}.json",
            "Theme Files (*.json);;All Files (*)",
        )
        if not path:
            return
        export_data = dict(self._theme)
        export_data["_effect"] = self._effect_combo.currentData() or "default"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "Export Theme", f"Theme exported to:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not write file:\n{exc}")

    def _import_theme(self):
        """Import a theme from a JSON file and apply it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Theme", "",
            "Theme Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Import Failed", f"Could not read theme file:\n{exc}")
            return
        _REQUIRED_KEYS = ("background", "surface", "primary", "accent", "text")
        if not isinstance(data, dict) or not all(k in data for k in _REQUIRED_KEYS):
            QMessageBox.warning(
                self, "Import Failed",
                "The selected file does not appear to be a valid theme JSON.\n"
                "A valid theme must contain at least these color keys:\n"
                + ", ".join(_REQUIRED_KEYS),
            )
            return
        # Work on a copy so the parsed data dict is never mutated
        theme_data = dict(data)
        # Use filename as display name if the JSON has no "name" key
        if "name" not in theme_data:
            theme_data["name"] = os.path.splitext(os.path.basename(path))[0]
        name = theme_data["name"]
        self._settings.save_named_theme(name, theme_data)
        self._rebuild_theme_combo(select=f"★ {name}", filter_text=self._current_filter_text())
        # Apply immediately
        self._theme = dict(theme_data)
        # Sync color buttons to the imported theme
        for key, btn in self._color_buttons.items():
            btn.set_color(self._theme.get(key, "#888888"))
        self._set_effect_combo(self._theme.get("_effect", "default"))
        self._settings.set_theme(self._theme)
        self.theme_changed.emit(self._theme)
        self.settings_changed.emit()
        QMessageBox.information(self, "Import Theme", f"Theme '{name}' imported and applied.")

    # ------------------------------------------------------------------
    # Effect combo helpers — live apply
    # ------------------------------------------------------------------

    def _set_effect_combo(self, effect_key: str) -> None:
        """Set the effect combo to the entry matching effect_key."""
        for i in range(self._effect_combo.count()):
            if self._effect_combo.itemData(i) == effect_key:
                self._effect_combo.setCurrentIndex(i)
                return
        self._effect_combo.setCurrentIndex(0)

    def _on_effect_changed_live(self) -> None:
        """Sync the effect key into the theme dict and persist immediately."""
        self._theme["_effect"] = self._effect_combo.currentData() or "default"
        self._settings.set_theme(self._theme)
        self.settings_changed.emit()

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
    # Live-update handlers for the General tab
    # ------------------------------------------------------------------

    def _on_sound_changed(self) -> None:
        self._settings.set("sound_enabled", self._sound_check.isChecked())
        self.settings_changed.emit()

    def _on_sound_path_changed(self) -> None:
        self._settings.set("click_sound_path", self._click_sound_edit.text().strip())
        self.settings_changed.emit()

    def _on_trail_changed(self) -> None:
        self._settings.set("trail_enabled", self._trail_check.isChecked())
        self._settings.set("use_theme_trail", self._use_theme_trail_check.isChecked())
        self.settings_changed.emit()

    def _on_trail_style_changed(self) -> None:
        _IDX_TO_STYLE = ["dots", "ribbon", "comet", "fairy", "wave", "sparkle"]
        idx = self._trail_style_combo.currentIndex()
        style = _IDX_TO_STYLE[idx] if 0 <= idx < len(_IDX_TO_STYLE) else "dots"
        self._settings.set("trail_style", style)
        self.settings_changed.emit()

    def _on_trail_color_changed(self, color: str) -> None:
        self._settings.set("trail_color", color)
        self.settings_changed.emit()

    def _on_cursor_changed(self) -> None:
        self._settings.set("cursor", self._cursor_combo.currentText())
        self._settings.set("use_theme_cursor", self._use_theme_cursor_check.isChecked())
        self.settings_changed.emit()

    def _on_font_size_changed(self, value: int) -> None:
        self._settings.set("font_size", value)
        self.settings_changed.emit()

    def _on_effects_enabled_changed(self) -> None:
        # Keep both copies of the on/off toggle in sync
        enabled = (self._click_effects_check.isChecked()
                   or self._click_effects_theme_check.isChecked())
        sender = self.sender()
        if sender is self._click_effects_check:
            enabled = self._click_effects_check.isChecked()
            self._click_effects_theme_check.blockSignals(True)
            self._click_effects_theme_check.setChecked(enabled)
            self._click_effects_theme_check.blockSignals(False)
        elif sender is self._click_effects_theme_check:
            enabled = self._click_effects_theme_check.isChecked()
            self._click_effects_check.blockSignals(True)
            self._click_effects_check.setChecked(enabled)
            self._click_effects_check.blockSignals(False)
        self._settings.set("click_effects_enabled", enabled)
        self.settings_changed.emit()

    def _on_use_theme_effect_changed(self) -> None:
        use_theme = self._use_theme_effect_check.isChecked()
        self._settings.set("use_theme_effect", use_theme)
        self._effect_combo.setEnabled(not use_theme)
        self.settings_changed.emit()

    def _on_tooltip_mode_changed(self) -> None:
        self._settings.set("tooltip_mode", self._tooltip_mode_combo.currentText())
        self.settings_changed.emit()
