"""
Settings / Customization dialog.
"""
import json
import os

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCompleter, QTabWidget, QWidget, QGridLayout, QCheckBox,
    QLineEdit, QColorDialog, QGroupBox, QScrollArea,
    QMessageBox, QInputDialog, QSpinBox, QFileDialog, QSlider,
)

from .theme_engine import PRESET_THEMES, HIDDEN_THEMES, DEFAULT_THEME, build_stylesheet, THEME_DESCRIPTIONS
from .tooltip_manager import TOOLTIP_MODES
from ..core.settings_manager import DEFAULT_CUSTOM_EMOJI

# Prefix characters used on theme combo items (user-saved = ★, unlocked hidden = 🔓)
_THEME_PREFIX_CHARS = "★🔓 "

# Maximum character length accepted as a directly-typed custom emoji.
# Emoji can be multi-codepoint sequences (e.g. 🏴‍☠️ = 7 code units) but are
# never longer than ~8 chars; this guards against adding entire search strings.
_MAX_CUSTOM_EMOJI_LEN = 8

# Trail slider range constants
_TRAIL_LENGTH_MIN = 10
_TRAIL_LENGTH_MAX = 200
_TRAIL_LENGTH_DEFAULT = 50
_TRAIL_FADE_MIN = 1
_TRAIL_FADE_MAX = 10
_TRAIL_FADE_DEFAULT = 5
_TRAIL_INTENSITY_MIN = 10
_TRAIL_INTENSITY_MAX = 100
_TRAIL_INTENSITY_DEFAULT = 100

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
    # Emitted the very first time the user changes the tooltip mode.
    # MainWindow connects this to trigger the Secret Skeleton unlock.
    first_tooltip_mode_change = pyqtSignal()

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
        self._settings_tabs = QTabWidget()
        tabs = self._settings_tabs

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
        _EFFECT_TIPS = {
            "default":      "Pink sparks burst from click point. Light and fast.",
            "gore":         "Blood splatter sprays outward. Dark and dramatic.",
            "bat":          "Bats fly across the top of the window periodically.",
            "rainbow":      "Unicorns and rainbow arcs fly from click point.",
            "otter":        "Cute otter emojis burst and fall with gravity.",
            "galaxy":       "Stars and cosmic dust scatter outward.",
            "galaxy_otter": "Space otters emerge from a cosmic burst.",
            "goth":         "Skulls and shadow sparks fall from click point.",
            "neon":         "Electric lightning bolts crackle outward.",
            "fire":         "Rising flame particles shoot upward from click.",
            "ice":          "Snowflakes and frost crystals scatter outward.",
            "sparkle":      "Glittering star crystals burst and fade.",
            "panda":        "Cute panda emojis shower down from click point.",
            "sakura":       "Cherry blossom petals drift down gracefully.",
            "fairy":        "Glitter, magic wands, and fairies flutter across.",
            "ocean":        "Bubbles rise and sea creatures pop from click.",
            "ripple":       "Water droplets and wave rings spread from click.",
            "mermaid":      "Magical sea creatures and sparkles float up.",
            "shark":        "Shark bites with blood splatter effects.",
            "alien":        "UFO abduction beams and alien emojis burst out.",
            "custom":       "Use your own emoji (set in 'Custom Emoji' below).",
        }
        for key, label in _EFFECT_OPTIONS:
            self._effect_combo.addItem(label, userData=key)
            idx = self._effect_combo.count() - 1
            tip = _EFFECT_TIPS.get(key, "")
            if tip:
                self._effect_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._effect_combo.setToolTip(
            "Choose the click particle effect for this theme.\n"
            "Select 'Custom' to use your own emoji as particles."
        )
        effect_inner.addWidget(self._effect_combo, 1)
        effect_layout.addLayout(effect_inner)
        effect_emoji_row.addWidget(grp_effect, 3)

        # Curated emoji palette for the custom click-effect picker.
        # Each entry is (emoji_char, display_label).  The label is shown in the
        # dropdown so users know exactly what they're selecting without needing
        # an emoji keyboard.
        _EMOJI_PALETTE = [
            # ── Sparkles & Stars ────────────────────────────────────────────
            ("✨", "✨  Sparkle"),
            ("⭐", "⭐  Star"),
            ("💫", "💫  Dizzy Star"),
            ("🌟", "🌟  Glowing Star"),
            ("🌠", "🌠  Shooting Star"),
            # ── Fire & Elements ─────────────────────────────────────────────
            ("🔥", "🔥  Fire"),
            ("❄", "❄  Snowflake"),
            ("💧", "💧  Water Drop"),
            ("⚡", "⚡  Lightning"),
            ("💥", "💥  Explosion"),
            ("💨", "💨  Wind"),
            # ── Hearts & Gems ────────────────────────────────────────────────
            ("❤️", "❤️  Red Heart"),
            ("💜", "💜  Purple Heart"),
            ("💙", "💙  Blue Heart"),
            ("💚", "💚  Green Heart"),
            ("💛", "💛  Yellow Heart"),
            ("🧡", "🧡  Orange Heart"),
            ("🖤", "🖤  Black Heart"),
            ("💎", "💎  Diamond"),
            # ── Celebration ─────────────────────────────────────────────────
            ("🎉", "🎉  Party Popper"),
            ("🎊", "🎊  Confetti Ball"),
            ("🎈", "🎈  Balloon"),
            ("🎀", "🎀  Ribbon"),
            ("🌈", "🌈  Rainbow"),
            # ── Nature & Flowers ────────────────────────────────────────────
            ("🌸", "🌸  Cherry Blossom"),
            ("🌺", "🌺  Hibiscus"),
            ("🌼", "🌼  Blossom"),
            ("🌻", "🌻  Sunflower"),
            ("🍀", "🍀  Four Leaf Clover"),
            ("🍁", "🍁  Maple Leaf"),
            # ── Animals ──────────────────────────────────────────────────────
            ("🐼", "🐼  Panda"),
            ("🦦", "🦦  Otter"),
            ("🦋", "🦋  Butterfly"),
            ("🐱", "🐱  Cat"),
            ("🐸", "🐸  Frog"),
            ("🦊", "🦊  Fox"),
            ("🦄", "🦄  Unicorn"),
            ("🐝", "🐝  Bee"),
            # ── Sea Creatures ────────────────────────────────────────────────
            ("🐟", "🐟  Fish"),
            ("🦈", "🦈  Shark"),
            ("🐙", "🐙  Octopus"),
            ("🦑", "🦑  Squid"),
            ("🐬", "🐬  Dolphin"),
            ("🦀", "🦀  Crab"),
            # ── Space & Sci-Fi ───────────────────────────────────────────────
            ("🌙", "🌙  Crescent Moon"),
            ("🪐", "🪐  Planet"),
            ("🛸", "🛸  UFO"),
            ("👽", "👽  Alien"),
            ("🤖", "🤖  Robot"),
            # ── Spooky ───────────────────────────────────────────────────────
            ("💀", "💀  Skull"),
            ("👻", "👻  Ghost"),
            ("🦇", "🦇  Bat"),
            ("🕷️", "🕷️  Spider"),
            ("👾", "👾  Alien Monster"),
            ("😈", "😈  Smiling Devil"),
            # ── Fun & Misc ────────────────────────────────────────────────────
            ("🎮", "🎮  Game Controller"),
            ("🍕", "🍕  Pizza"),
            ("🍩", "🍩  Donut"),
            ("🍭", "🍭  Lollipop"),
            ("🩸", "🩸  Blood Drop"),
            ("💩", "💩  Poop"),
            ("🤡", "🤡  Clown"),
            ("🥳", "🥳  Partying Face"),
            ("🤓", "🤓  Nerd Face"),
        ]

        grp_emoji = QGroupBox("Custom Click Emoji  ·  used when effect = 'Custom'")
        emoji_v = QVBoxLayout(grp_emoji)
        emoji_v.setSpacing(6)
        _emoji_hint = QLabel(
            "Pick an emoji, click Add.  "
            "Set the Click Effect to 'Custom' (above) to fire these on every click."
        )
        _emoji_hint.setWordWrap(True)
        _emoji_hint.setObjectName("subheader")
        emoji_v.addWidget(_emoji_hint)
        emoji_row = QHBoxLayout()
        self._emoji_combo = QComboBox()
        self._emoji_combo.setMinimumWidth(160)
        self._emoji_combo.setEditable(True)
        self._emoji_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._emoji_combo.lineEdit().setPlaceholderText("Search emoji…")
        self._emoji_combo.setToolTip(
            "Type to search emoji by name, then click Add to include it in your "
            "custom click-effect pool.\nSet the Click Effect dropdown to "
            "'Custom' to fire these emoji as particles on every click."
        )
        for emoji_char, label in _EMOJI_PALETTE:
            self._emoji_combo.addItem(label, userData=emoji_char)
        # Configure the auto-created completer for contains-mode filtering so
        # the user can search by any part of the label (e.g. "heart", "fire").
        emoji_completer = self._emoji_combo.completer()
        if emoji_completer is not None:
            try:
                emoji_completer.setFilterMode(Qt.MatchFlag.MatchContains)
                emoji_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            except AttributeError:
                pass
        self._btn_emoji_add = QPushButton("Add")
        self._btn_emoji_clear = QPushButton("Clear All")
        self._btn_emoji_add.setMinimumWidth(60)
        self._btn_emoji_clear.setMinimumWidth(80)
        emoji_row.addWidget(self._emoji_combo, 1)
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
        _TRAIL_STYLE_OPTIONS = [
            ("Dots (default)",    "Dots  – Small colored dots fade out behind the cursor."),
            ("Ribbon / Noodle",   "Ribbon  – Smooth connected line trails the cursor like a ribbon or noodle."),
            ("Comet tail",        "Comet tail  – Tapered bright streak that fades to nothing behind the cursor."),
            ("Fairy dust ✨",     "Fairy dust  – ✨💫⭐ emoji sparkles float and fade as you move."),
            ("Wave / Ocean 🌊",   "Wave / Ocean  – 🫧💧🌊🐠 emoji drift and ripple behind the cursor."),
            ("Sparkle / Ice ❄",  "Sparkle / Ice  – ✦❄✧💎 glittering ice crystals trail behind the cursor."),
        ]
        for label, tip in _TRAIL_STYLE_OPTIONS:
            self._trail_style_combo.addItem(label)
            idx = self._trail_style_combo.count() - 1
            self._trail_style_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
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

        # Trail Length slider (10–200 points)
        trail_gl.addWidget(QLabel("Trail Length:"), 4, 0)
        self._trail_length_slider = QSlider(Qt.Orientation.Horizontal)
        self._trail_length_slider.setRange(_TRAIL_LENGTH_MIN, _TRAIL_LENGTH_MAX)
        self._trail_length_slider.setValue(_TRAIL_LENGTH_DEFAULT)
        self._trail_length_slider.setToolTip(
            "Controls how many trail points are kept.\n"
            "Short = snappy; Long = lingering ghost trail."
        )
        self._trail_length_val_lbl = QLabel(str(_TRAIL_LENGTH_DEFAULT))
        self._trail_length_val_lbl.setFixedWidth(30)
        length_row = QHBoxLayout()
        length_row.addWidget(self._trail_length_slider)
        length_row.addWidget(self._trail_length_val_lbl)
        trail_gl.addLayout(length_row, 4, 1)
        self._trail_length_slider.valueChanged.connect(
            lambda v: self._trail_length_val_lbl.setText(str(v))
        )
        self._trail_length_slider.valueChanged.connect(self._on_trail_length_changed)

        # Trail Fade Speed slider (1 slow … 10 fast)
        trail_gl.addWidget(QLabel("Fade Speed:"), 5, 0)
        self._trail_fade_slider = QSlider(Qt.Orientation.Horizontal)
        self._trail_fade_slider.setRange(_TRAIL_FADE_MIN, _TRAIL_FADE_MAX)
        self._trail_fade_slider.setValue(_TRAIL_FADE_DEFAULT)
        self._trail_fade_slider.setToolTip(
            "How quickly the trail fades out.\n"
            "1 = very slow (long ghost), 10 = very fast (sharp snap)."
        )
        self._trail_fade_val_lbl = QLabel(str(_TRAIL_FADE_DEFAULT))
        self._trail_fade_val_lbl.setFixedWidth(30)
        fade_row = QHBoxLayout()
        fade_row.addWidget(self._trail_fade_slider)
        fade_row.addWidget(self._trail_fade_val_lbl)
        trail_gl.addLayout(fade_row, 5, 1)
        self._trail_fade_slider.valueChanged.connect(
            lambda v: self._trail_fade_val_lbl.setText(str(v))
        )
        self._trail_fade_slider.valueChanged.connect(self._on_trail_fade_changed)

        # Trail Intensity slider (10–100 %)
        trail_gl.addWidget(QLabel("Intensity:"), 6, 0)
        self._trail_intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self._trail_intensity_slider.setRange(_TRAIL_INTENSITY_MIN, _TRAIL_INTENSITY_MAX)
        self._trail_intensity_slider.setValue(_TRAIL_INTENSITY_DEFAULT)
        self._trail_intensity_slider.setToolTip(
            "Maximum opacity of the trail (10 % = very faint, 100 % = fully bright)."
        )
        self._trail_intensity_val_lbl = QLabel(f"{_TRAIL_INTENSITY_DEFAULT}%")
        self._trail_intensity_val_lbl.setFixedWidth(40)
        intensity_row = QHBoxLayout()
        intensity_row.addWidget(self._trail_intensity_slider)
        intensity_row.addWidget(self._trail_intensity_val_lbl)
        trail_gl.addLayout(intensity_row, 6, 1)
        self._trail_intensity_slider.valueChanged.connect(
            lambda v: self._trail_intensity_val_lbl.setText(f"{v}%")
        )
        self._trail_intensity_slider.valueChanged.connect(self._on_trail_intensity_changed)

        mouse_row.addWidget(grp_trail, 1)

        grp_cursor = QGroupBox("Cursor")
        cursor_gl = QGridLayout(grp_cursor)
        cursor_gl.setColumnStretch(1, 1)
        cursor_gl.setHorizontalSpacing(10)
        cursor_gl.setVerticalSpacing(6)
        cursor_gl.addWidget(QLabel("Cursor Style:"), 0, 0)
        self._cursor_combo = QComboBox()
        self._cursor_combo.addItems([
            # Standard system cursors
            "Default", "Cross", "Pointing Hand", "Open Hand",
            "Hourglass", "Forbidden", "IBeam", "Size All", "Blank",
            # Emoji text cursors (rendered via _make_emoji_cursor() in main window)
            "🐼 Panda", "🦦 Otter", "🐱 Cat", "🦈 Shark",
            "🧜 Mermaid Trident", "🛸 UFO", "🦇 Bat",
            "🌊 Wave", "🔥 Fire", "❄ Snowflake", "⚡ Lightning",
            "💀 Skull", "🌸 Sakura", "✨ Sparkle",
            # Extended emoji cursors
            "🐉 Dragon", "🌈 Rainbow", "🧚 Fairy", "👽 Alien",
            "🌙 Moon", "🍭 Candy", "🌿 Leaf", "🎯 Target",
            "🔮 Crystal Ball", "🦋 Butterfly", "🐙 Octopus",
            "🪄 Magic Wand", "🌺 Flower", "💎 Diamond",
            "🍄 Mushroom", "🤠 Cowboy", "☠ Crossbones",
            "🐠 Fish", "🍀 Clover", "🌟 Star", "🦴 Bone",
            "🎃 Pumpkin", "🧿 Evil Eye", "⚗ Flask", "🪸 Coral",
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
        self._sound_check = QCheckBox("Enable sounds (off by default)")
        self._sound_check.setToolTip(
            "Master switch — enables all application sounds.\n"
            "Off by default. Individual events can still be muted below."
        )
        sound_gl.addWidget(self._sound_check, 0, 0, 1, 2)
        self._use_theme_sound_check = QCheckBox("Use theme sound (click sound follows the active theme)")
        self._use_theme_sound_check.setToolTip(
            "When enabled the click sound changes to match the active theme.\n"
            "Gore = deep thud, Panda = soft chime, Alien = bright ping, etc."
        )
        sound_gl.addWidget(self._use_theme_sound_check, 1, 0, 1, 2)

        # Volume slider
        sound_gl.addWidget(QLabel("Volume:"), 2, 0)
        vol_row = QHBoxLayout()
        self._sound_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._sound_volume_slider.setRange(0, 100)
        self._sound_volume_slider.setValue(50)
        self._sound_volume_slider.setToolTip(
            "Master volume for all application sounds (0 = silent, 100 = full).\n"
            "Only affects the Qt Multimedia backend; subprocess fallback ignores this."
        )
        self._sound_volume_lbl = QLabel("50%")
        self._sound_volume_lbl.setFixedWidth(36)
        self._sound_volume_slider.valueChanged.connect(
            lambda v: self._sound_volume_lbl.setText(f"{v}%")
        )
        vol_row.addWidget(self._sound_volume_slider, 1)
        vol_row.addWidget(self._sound_volume_lbl)
        sound_gl.addLayout(vol_row, 2, 1)

        sound_gl.addWidget(QLabel("Custom click .wav:"), 3, 0)
        sound_row = QHBoxLayout()
        self._click_sound_edit = QLineEdit()
        self._click_sound_edit.setPlaceholderText("Leave blank for built-in sound")
        self._btn_sound_browse = QPushButton("Browse…")
        sound_row.addWidget(self._click_sound_edit, 1)
        sound_row.addWidget(self._btn_sound_browse)
        sound_gl.addLayout(sound_row, 3, 1)

        # Per-event sound toggles
        sound_gl.addWidget(QLabel("Event sounds:"), 4, 0)
        events_row = QHBoxLayout()
        self._sound_success_check = QCheckBox("Completion")
        self._sound_success_check.setToolTip(
            "Play a cheerful chime when a batch finishes with no errors."
        )
        self._sound_error_check = QCheckBox("Error")
        self._sound_error_check.setToolTip(
            "Play a descending buzz when a batch finishes with file errors."
        )
        self._sound_unlock_check = QCheckBox("Unlock")
        self._sound_unlock_check.setToolTip(
            "Play an ascending fanfare when a secret theme is unlocked."
        )
        self._sound_file_add_check = QCheckBox("File added")
        self._sound_file_add_check.setToolTip(
            "Play a soft thunk when files are dropped into the queue."
        )
        self._sound_preview_check = QCheckBox("Preview")
        self._sound_preview_check.setToolTip(
            "Play a subtle ping every time the live preview image refreshes.\n"
            "Off by default — can be distracting during rapid parameter changes."
        )
        for chk in (self._sound_success_check, self._sound_error_check,
                    self._sound_unlock_check, self._sound_file_add_check,
                    self._sound_preview_check):
            events_row.addWidget(chk)
        sound_gl.addLayout(events_row, 4, 1)

        tv.addWidget(grp_sound)

        # ---- Button Press Animation GroupBox ----
        grp_btn_anim = QGroupBox("Button Press Animation")
        btn_anim_gl = QGridLayout(grp_btn_anim)
        btn_anim_gl.setColumnStretch(1, 1)
        btn_anim_gl.setHorizontalSpacing(10)
        btn_anim_gl.setVerticalSpacing(6)
        self._button_anim_check = QCheckBox(
            "Enable button press animations (off by default)"
        )
        self._button_anim_check.setToolTip(
            "When enabled every QPushButton in the app plays a short animation\n"
            "when clicked — a subtle slide, bounce, shake, or particle burst.\n"
            "Off by default for maximum performance."
        )
        btn_anim_gl.addWidget(self._button_anim_check, 0, 0, 1, 2)

        self._use_theme_button_anim_check = QCheckBox(
            "Use theme animation (each theme picks its own style)"
        )
        self._use_theme_button_anim_check.setToolTip(
            "When checked the animation style is chosen automatically by the\n"
            "active theme — e.g. Gore/Zombie/Dragon gets 'Shatter', Alien/Neon\n"
            "gets 'Shake', Fairy/Sakura gets 'Bounce'.\n"
            "Uncheck to force a fixed style from the dropdown below."
        )
        btn_anim_gl.addWidget(self._use_theme_button_anim_check, 1, 0, 1, 2)

        btn_anim_gl.addWidget(QLabel("Animation style:"), 2, 0)
        self._button_anim_style_combo = QComboBox()
        _BUTTON_ANIM_OPTIONS = [
            ("none",    "None — no animation"),
            ("press",   "Press — subtle 2 px downward nudge"),
            ("fall",    "Fall — 8 px drop and spring back"),
            ("bounce",  "Bounce — button leaps up and bounces back"),
            ("shake",   "Shake — rapid left/right vibration"),
            ("shatter", "Shatter — particle burst from button centre"),
        ]
        _BUTTON_ANIM_TIPS = {
            "none":    "No animation. Buttons respond instantly with no visual movement.",
            "press":   "The button shifts 2 pixels down on press then springs back.\n"
                       "Subtle and satisfying — closest to a real physical button.",
            "fall":    "The button slides 8 pixels down over ~220 ms then springs back.\n"
                       "Heavier feel, great for ocean/cave/goth themes.",
            "bounce":  "The button shoots up 6 pixels then bounces back down.\n"
                       "Playful and energetic — great for fairy/candy/sakura themes.",
            "shake":   "Rapid left/right vibration (~5 px over ~300 ms).\n"
                       "Aggressive energy — great for neon/alien/storm themes.",
            "shatter": "Spawns themed click-effect particles from the button centre.\n"
                       "Requires click effects to be enabled for best results.\n"
                       "Dramatic — great for gore/volcano/dragon themes.",
        }
        for key, label in _BUTTON_ANIM_OPTIONS:
            self._button_anim_style_combo.addItem(label, userData=key)
            idx = self._button_anim_style_combo.count() - 1
            tip = _BUTTON_ANIM_TIPS.get(key, "")
            if tip:
                self._button_anim_style_combo.setItemData(
                    idx, tip, Qt.ItemDataRole.ToolTipRole
                )
        self._button_anim_style_combo.setToolTip(
            "Choose the press-animation style applied to every button.\n"
            "Greyed out while 'Use theme animation' is checked."
        )
        self._button_anim_style_combo.setMaximumWidth(280)
        btn_anim_gl.addWidget(self._button_anim_style_combo, 2, 1, Qt.AlignmentFlag.AlignLeft)

        tv.addWidget(grp_btn_anim)

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
        misc_gl.addWidget(QLabel("Tooltip Font Size (pt):"), 0, 0)
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 24)
        self._font_size_spin.setValue(10)
        self._font_size_spin.setMaximumWidth(80)
        misc_gl.addWidget(self._font_size_spin, 0, 1, Qt.AlignmentFlag.AlignLeft)
        misc_gl.addWidget(QLabel("Tooltip Mode:"), 1, 0)
        self._tooltip_mode_combo = QComboBox()
        _TOOLTIP_MODE_TIPS = {
            "Normal":       "Standard helpful tooltips. Clear, informative, and professional.",
            "Off":          "Tooltips are disabled. Hover over anything: silence. Pure, blessed silence.",
            "Dumbed Down":  "Tips written as if you've never seen software before. Condescending but thorough.",
            "No Filter 🤬": "Extremely vulgar, extremely funny, very sweary — but still actually helpful. The best mode.",
        }
        for mode in TOOLTIP_MODES:
            self._tooltip_mode_combo.addItem(mode)
            idx = self._tooltip_mode_combo.count() - 1
            tip = _TOOLTIP_MODE_TIPS.get(mode, "")
            if tip:
                self._tooltip_mode_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._tooltip_mode_combo.setToolTip(
            "Controls how tooltips appear throughout the app.\n"
            "No Filter 🤬 is the best mode – trust us."
        )
        self._tooltip_mode_combo.setMaximumWidth(220)
        misc_gl.addWidget(self._tooltip_mode_combo, 1, 1, Qt.AlignmentFlag.AlignLeft)
        misc_gl.addWidget(QLabel("Tooltip Popups Style:"), 2, 0)
        self._tooltip_style_combo = QComboBox()
        _TOOLTIP_STYLE_ENTRIES = [
            ("Auto (follow theme)",  "Tooltip style follows the active theme automatically."),
            ("Angular",              "Sharp rectangular corners. Clean and minimal."),
            ("Bubbly",               "Rounded corners, bold text. Friendly and playful."),
            ("Rounded",              "Soft medium-radius corners. Works well with most themes."),
            ("Icy",                  "Frosted blue tint with subtle glow. For ice/arctic themes."),
            ("Wavy",                 "Alternating radius corners for a wavy feel. Ocean/mermaid themes."),
            ("Neon",                 "Bold glowing border that pulses with the accent color."),
            ("Classic",              "Traditional solid border. Familiar and unobtrusive."),
        ]
        for style_name, style_tip in _TOOLTIP_STYLE_ENTRIES:
            self._tooltip_style_combo.addItem(style_name)
            idx = self._tooltip_style_combo.count() - 1
            self._tooltip_style_combo.setItemData(idx, style_tip, Qt.ItemDataRole.ToolTipRole)
        self._tooltip_style_combo.setToolTip(
            "Controls the visual shape and appearance of tooltip boxes.\n"
            "Auto follows the active theme.  Other options force a fixed style."
        )
        self._tooltip_style_combo.setMaximumWidth(220)
        misc_gl.addWidget(self._tooltip_style_combo, 2, 1, Qt.AlignmentFlag.AlignLeft)

        # Animated banner emojis and SVG badge (off by default – saves CPU/GPU)
        self._animated_banner_check = QCheckBox(
            "Enable animated banner emojis && SVG badge (off by default)"
        )
        self._animated_banner_check.setToolTip(
            "When enabled: the banner emoji in the header animates continuously\n"
            "and the theme SVG badge in the tab bar plays its built-in animation.\n"
            "When disabled: both are rendered statically, saving CPU/GPU resources."
        )
        misc_gl.addWidget(self._animated_banner_check, 3, 0, 1, 2)

        # Banner animation style (spin / bounce / shake / pendulum / flock)
        misc_gl.addWidget(QLabel("Banner animation:"), 4, 0)
        self._banner_anim_combo = QComboBox()
        _BANNER_ANIM_OPTIONS = [
            ("spin",     "Spin – continuous 360° rotation"),
            ("bounce",   "Bounce – gentle vertical bobbing"),
            ("shake",    "Shake – rapid horizontal quiver"),
            ("pendulum", "Pendulum – swinging back and forth"),
            ("flock",    "Flock – emoji fly across the top of the window"),
        ]
        _BANNER_ANIM_TIPS = {
            "spin":     "The emoji rotates continuously like a gear (~6 s per full turn).",
            "bounce":   "The emoji bobs up and down with a smooth sine-wave motion.",
            "shake":    "The emoji vibrates rapidly side to side — great for aggressive themes.",
            "pendulum": "The emoji swings back and forth like a pendulum clock.",
            "flock":    "A small group of themed emoji periodically flies across the top of\n"
                        "the window (similar to the bat flock in Bat Cave theme).",
        }
        for key, label in _BANNER_ANIM_OPTIONS:
            self._banner_anim_combo.addItem(label, userData=key)
            idx = self._banner_anim_combo.count() - 1
            tip = _BANNER_ANIM_TIPS.get(key, "")
            if tip:
                self._banner_anim_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._banner_anim_combo.setToolTip(
            "Choose the animation style for the banner emoji when animation is enabled.\n"
            "Greyed out (non-interactive) while 'Use theme animation' is checked —\n"
            "uncheck that box to override with your own style."
        )
        self._banner_anim_combo.setMaximumWidth(280)
        misc_gl.addWidget(self._banner_anim_combo, 4, 1, Qt.AlignmentFlag.AlignLeft)

        # Use-theme animation checkbox
        self._banner_use_theme_anim_check = QCheckBox(
            "Use theme animation (each theme has its own style)"
        )
        self._banner_use_theme_anim_check.setToolTip(
            "When checked the animation style is chosen automatically by the active\n"
            "theme (e.g. Bat Cave uses flock, Alien uses bounce, Goth uses pendulum).\n"
            "Uncheck to override with your own style from the dropdown above."
        )
        misc_gl.addWidget(self._banner_use_theme_anim_check, 5, 0, 1, 2)

        # Splash screen on startup (off by default)
        self._show_splash_check = QCheckBox(
            "Show themed splash screen on startup (off by default)"
        )
        self._show_splash_check.setToolTip(
            "When enabled: an animated themed splash screen is shown while the\n"
            "app loads on startup.  Disable to skip straight to the main window."
        )
        misc_gl.addWidget(self._show_splash_check, 6, 0, 1, 2)

        gv.addWidget(grp_misc)

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
        self._btn_reset = QPushButton("Reset All Settings…")
        self._btn_reset.setToolTip(
            "Reset ALL settings, unlock flags, and history to factory defaults.\n"
            "Useful for testing easter eggs and unlock events."
        )
        btn_row.addWidget(self._btn_reset)
        self._btn_reset_unlocks = QPushButton("Reset Unlocks & Clicks…")
        self._btn_reset_unlocks.setToolTip(
            "Reset only the unlock flags and click counter to zero.\n"
            "All other settings (theme, sound, trail, etc.) are preserved.\n"
            "Useful for re-testing hidden theme easter eggs without losing your setup."
        )
        btn_row.addWidget(self._btn_reset_unlocks)
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
        self._btn_reset.clicked.connect(self._reset_all_settings)
        self._btn_reset_unlocks.clicked.connect(self._reset_unlocks_only)
        self._btn_sound_browse.clicked.connect(self._browse_sound)
        self._effect_combo.currentIndexChanged.connect(self._on_effect_changed_live)
        self._btn_emoji_add.clicked.connect(self._add_emoji)
        self._btn_emoji_clear.clicked.connect(self._clear_emoji)
        # All controls save+emit live
        self._sound_check.toggled.connect(self._on_sound_changed)
        self._use_theme_sound_check.toggled.connect(self._on_use_theme_sound_changed)
        self._click_sound_edit.editingFinished.connect(self._on_sound_path_changed)
        self._sound_volume_slider.valueChanged.connect(self._on_sound_volume_changed)
        self._sound_success_check.toggled.connect(self._on_sound_event_changed)
        self._sound_error_check.toggled.connect(self._on_sound_event_changed)
        self._sound_unlock_check.toggled.connect(self._on_sound_event_changed)
        self._sound_file_add_check.toggled.connect(self._on_sound_event_changed)
        self._sound_preview_check.toggled.connect(self._on_sound_event_changed)
        self._trail_check.toggled.connect(self._on_trail_changed)
        self._trail_color_btn.color_changed.connect(self._on_trail_color_changed)
        self._use_theme_trail_check.toggled.connect(self._on_trail_changed)
        self._trail_style_combo.currentIndexChanged.connect(self._on_trail_style_changed)
        self._cursor_combo.currentTextChanged.connect(self._on_cursor_changed)
        self._use_theme_cursor_check.toggled.connect(self._on_cursor_changed)
        self._font_size_spin.valueChanged.connect(self._on_font_size_changed)
        self._click_effects_theme_check.toggled.connect(self._on_effects_enabled_changed)
        self._use_theme_effect_check.toggled.connect(self._on_use_theme_effect_changed)
        self._tooltip_mode_combo.currentTextChanged.connect(self._on_tooltip_mode_changed)
        self._tooltip_style_combo.currentTextChanged.connect(self._on_tooltip_style_changed)
        self._animated_banner_check.toggled.connect(self._on_animated_banner_changed)
        self._banner_anim_combo.currentIndexChanged.connect(self._on_banner_anim_style_changed)
        self._banner_use_theme_anim_check.toggled.connect(self._on_banner_use_theme_anim_changed)
        self._show_splash_check.toggled.connect(self._on_show_splash_changed)
        self._button_anim_check.toggled.connect(self._on_button_anim_changed)
        self._button_anim_style_combo.currentIndexChanged.connect(self._on_button_anim_style_changed)
        self._use_theme_button_anim_check.toggled.connect(self._on_use_theme_button_anim_changed)

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

        def _set_tip(idx: int, name: str) -> None:
            """Set a tooltip on a just-added combo item using THEME_DESCRIPTIONS."""
            desc = THEME_DESCRIPTIONS.get(name, "")
            if desc:
                self._theme_preset_combo.setItemData(
                    idx, desc, Qt.ItemDataRole.ToolTipRole
                )

        for name in PRESET_THEMES:
            if _matches(name):
                idx = self._theme_preset_combo.count()
                self._theme_preset_combo.addItem(name)
                _set_tip(idx, name)
        # Show hidden themes that have been unlocked
        for name, t in HIDDEN_THEMES.items():
            unlock_key = f"unlock_{t.get('_unlock', '')}"
            if self._settings.get(unlock_key, False) and _matches(name):
                idx = self._theme_preset_combo.count()
                self._theme_preset_combo.addItem(f"🔓 {name}")
                _set_tip(idx, name)
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
            self._use_theme_sound_check, self._click_sound_edit, self._trail_check,
            self._trail_color_btn, self._trail_style_combo, self._use_theme_trail_check,
            self._cursor_combo, self._use_theme_cursor_check, self._font_size_spin,
            self._click_effects_theme_check,
            self._use_theme_effect_check, self._tooltip_mode_combo, self._tooltip_style_combo,
            self._animated_banner_check, self._banner_anim_combo,
            self._banner_use_theme_anim_check, self._show_splash_check,
            self._button_anim_check, self._button_anim_style_combo,
            self._use_theme_button_anim_check,
            # Sliders must also be signal-blocked during load; their valueChanged
            # is connected to _on_trail_*_changed which emits settings_changed.
            self._trail_length_slider, self._trail_fade_slider, self._trail_intensity_slider,
            self._sound_volume_slider,
            self._sound_success_check, self._sound_error_check, self._sound_unlock_check,
            self._sound_file_add_check, self._sound_preview_check,
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
        self._use_theme_sound_check.setChecked(self._settings.get("use_theme_sound", False))
        self._click_sound_edit.setText(self._settings.get("click_sound_path", ""))
        vol = int(self._settings.get("sound_volume", 50))
        self._sound_volume_slider.setValue(max(0, min(100, vol)))
        self._sound_volume_lbl.setText(f"{self._sound_volume_slider.value()}%")
        self._sound_success_check.setChecked(self._settings.get("sound_success", True))
        self._sound_error_check.setChecked(self._settings.get("sound_error", True))
        self._sound_unlock_check.setChecked(self._settings.get("sound_unlock", True))
        self._sound_file_add_check.setChecked(self._settings.get("sound_file_add", True))
        self._sound_preview_check.setChecked(self._settings.get("sound_preview", False))
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
        # Load trail sliders
        saved_length = int(self._settings.get("trail_length", _TRAIL_LENGTH_DEFAULT))
        self._trail_length_slider.setValue(max(_TRAIL_LENGTH_MIN, min(_TRAIL_LENGTH_MAX, saved_length)))
        self._trail_length_val_lbl.setText(str(self._trail_length_slider.value()))
        saved_fade = int(self._settings.get("trail_fade_speed", _TRAIL_FADE_DEFAULT))
        self._trail_fade_slider.setValue(max(_TRAIL_FADE_MIN, min(_TRAIL_FADE_MAX, saved_fade)))
        self._trail_fade_val_lbl.setText(str(self._trail_fade_slider.value()))
        saved_intensity = int(self._settings.get("trail_intensity", _TRAIL_INTENSITY_DEFAULT))
        self._trail_intensity_slider.setValue(max(_TRAIL_INTENSITY_MIN, min(_TRAIL_INTENSITY_MAX, saved_intensity)))
        self._trail_intensity_val_lbl.setText(f"{self._trail_intensity_slider.value()}%")
        cursor_val = self._settings.get("cursor", "Default")
        idx = self._cursor_combo.findText(cursor_val)
        self._cursor_combo.setCurrentIndex(max(idx, 0))
        use_theme_cur = self._settings.get("use_theme_cursor", False)
        self._use_theme_cursor_check.setChecked(use_theme_cur)
        self._cursor_combo.setEnabled(not use_theme_cur)
        self._font_size_spin.setValue(self._settings.get("font_size", 10))
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
        style_val = self._settings.get("tooltip_style", "Auto (follow theme)")
        idx_s = self._tooltip_style_combo.findText(style_val)
        self._tooltip_style_combo.setCurrentIndex(max(idx_s, 0))
        self._animated_banner_check.setChecked(
            self._settings.get("animated_banner_enabled", False)
        )
        # Load banner animation style combo
        _BANNER_ANIM_IDX_MAP = {
            "spin": 0, "bounce": 1, "shake": 2, "pendulum": 3, "flock": 4,
        }
        saved_banner_anim = self._settings.get("banner_anim_style", "spin")
        self._banner_anim_combo.setCurrentIndex(
            _BANNER_ANIM_IDX_MAP.get(saved_banner_anim, 0)
        )
        banner_use_theme = self._settings.get("banner_use_theme_anim", True)
        self._banner_use_theme_anim_check.setChecked(banner_use_theme)
        banner_enabled = self._settings.get("animated_banner_enabled", False)
        self._banner_anim_combo.setEnabled(banner_enabled and not banner_use_theme)
        self._banner_use_theme_anim_check.setEnabled(banner_enabled)
        self._show_splash_check.setChecked(
            self._settings.get("show_splash_screen", False)
        )
        # Load button animation settings
        btn_anim_enabled = self._settings.get("button_anim_enabled", False)
        self._button_anim_check.setChecked(btn_anim_enabled)
        use_theme_btn_anim = self._settings.get("use_theme_button_anim", True)
        self._use_theme_button_anim_check.setChecked(use_theme_btn_anim)
        _BUTTON_ANIM_IDX_MAP = {
            "none": 0, "press": 1, "fall": 2, "bounce": 3, "shake": 4, "shatter": 5,
        }
        saved_btn_anim = self._settings.get("button_anim_style", "press")
        self._button_anim_style_combo.setCurrentIndex(
            _BUTTON_ANIM_IDX_MAP.get(saved_btn_anim, 1)
        )
        self._button_anim_style_combo.setEnabled(
            btn_anim_enabled and not use_theme_btn_anim
        )
        self._use_theme_button_anim_check.setEnabled(btn_anim_enabled)

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
        mgr.register(self._emoji_combo, "custom_emoji")
        mgr.register(self._tooltip_mode_combo, "tooltip_mode_combo")
        mgr.register(self._tooltip_style_combo, "tooltip_style_combo")
        mgr.register(self._sound_check, "sound_check")
        mgr.register(self._use_theme_sound_check, "use_theme_sound")
        mgr.register(self._sound_volume_slider, "sound_volume_slider")
        mgr.register(self._sound_success_check, "sound_success_check")
        mgr.register(self._sound_error_check, "sound_error_check")
        mgr.register(self._sound_unlock_check, "sound_unlock_check")
        mgr.register(self._sound_file_add_check, "sound_file_add_check")
        mgr.register(self._sound_preview_check, "sound_preview_check")
        mgr.register(self._trail_check, "trail_check")
        mgr.register(self._trail_color_btn, "trail_color")
        mgr.register(self._trail_style_combo, "trail_style")
        mgr.register(self._use_theme_trail_check, "use_theme_trail")
        mgr.register(self._trail_length_slider, "trail_length_slider")
        mgr.register(self._trail_fade_slider, "trail_fade_slider")
        mgr.register(self._trail_intensity_slider, "trail_intensity_slider")
        mgr.register(self._cursor_combo, "cursor_combo")
        mgr.register(self._use_theme_cursor_check, "use_theme_cursor")
        mgr.register(self._font_size_spin, "font_size")
        mgr.register(self._click_effects_theme_check, "click_effects_check")
        mgr.register(self._use_theme_effect_check, "use_theme_effect")
        mgr.register(self._animated_banner_check, "animated_banner_check")
        mgr.register(self._banner_anim_combo, "banner_anim_combo")
        mgr.register(self._banner_use_theme_anim_check, "banner_use_theme_anim_check")
        mgr.register(self._show_splash_check, "show_splash_check")
        mgr.register(self._button_anim_check, "button_anim_check")
        mgr.register(self._button_anim_style_combo, "button_anim_style_combo")
        mgr.register(self._use_theme_button_anim_check, "use_theme_button_anim_check")
        # Additional widget registrations
        mgr.register(self._btn_save_theme, "save_custom_theme")
        mgr.register(self._btn_delete_theme, "delete_custom_theme")
        mgr.register(self._btn_export_theme, "export_custom_theme")
        mgr.register(self._btn_import_theme, "import_custom_theme")
        mgr.register(self._click_sound_edit, "sound_path")
        mgr.register(self._btn_sound_browse, "sound_browse")
        mgr.register(self._btn_reset, "reset_all_settings")
        mgr.register(self._btn_reset_unlocks, "reset_unlocks_btn")
        # Settings dialog own tab bar (Theme / General tabs)
        mgr.register_tab_bar(
            self._settings_tabs.tabBar(),
            ["settings_theme_tab", "settings_general_tab"],
        )
        # Register all color swatch buttons with the same generic key
        for btn in self._color_buttons.values():
            mgr.register(btn, "theme_color_btn")

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
        # Prefer the userData (emoji char) of the currently selected palette item.
        # Fall back to the raw text in the line edit so that the user can type a
        # custom emoji (not in the palette) directly into the search box and click
        # Add to include it.
        emoji_char = self._emoji_combo.currentData()
        if not emoji_char:
            typed = self._emoji_combo.currentText().strip()
            # Only accept the fallback text if it looks like an emoji / short symbol
            # (≤_MAX_CUSTOM_EMOJI_LEN chars) to avoid accidentally adding search
            # strings like "fire".
            if typed and len(typed) <= _MAX_CUSTOM_EMOJI_LEN:
                emoji_char = typed
        if not emoji_char:
            return
        current = self._get_emoji_list()
        current.append(emoji_char)
        self._settings.set("custom_emoji", " ".join(current))
        self._update_emoji_display()
        self.settings_changed.emit()

    def _clear_emoji(self) -> None:
        self._settings.set("custom_emoji", "")
        self._update_emoji_display()
        self.settings_changed.emit()

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

    def _reset_all_settings(self) -> None:
        """Ask the user then wipe all settings back to factory defaults."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Reset All Settings?",
            "This will erase ALL settings, unlock flags, history, and custom themes.\n\n"
            "Unlock events like easter eggs will be re-triggerable from scratch.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._settings.reset_all()
        self.settings_changed.emit()
        QMessageBox.information(
            self,
            "Settings Reset",
            "All settings have been reset to defaults.\n"
            "Restart the application to fully apply the changes.",
        )
        self.accept()

    def _reset_unlocks_only(self) -> None:
        """Ask the user then reset only unlock flags and click counter."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Reset Unlocks & Clicks?",
            "This will reset ONLY the unlock flags and click/file counter to zero.\n\n"
            "All other settings (theme, sound, trail, cursor, etc.) are kept as-is.\n\n"
            "Hidden themes and easter eggs will become re-triggerable from scratch.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._settings.reset_unlocks_only()
        self.settings_changed.emit()
        # Rebuild the theme combo so locked hidden themes are removed
        self._rebuild_theme_combo(
            select=self._theme_preset_combo.currentText(),
            filter_text=self._current_filter_text(),
        )
        QMessageBox.information(
            self,
            "Unlocks Reset",
            "Unlock flags and click counter have been reset.\n"
            "Your theme, sound, and appearance settings are unchanged.\n"
            "Start clicking or processing files to re-unlock hidden themes!",
        )

    # ------------------------------------------------------------------
    # Live-update handlers for the General tab
    # ------------------------------------------------------------------

    def _on_sound_changed(self) -> None:
        self._settings.set("sound_enabled", self._sound_check.isChecked())
        self.settings_changed.emit()

    def _on_use_theme_sound_changed(self) -> None:
        self._settings.set("use_theme_sound", self._use_theme_sound_check.isChecked())
        self.settings_changed.emit()

    def _on_sound_path_changed(self) -> None:
        self._settings.set("click_sound_path", self._click_sound_edit.text().strip())
        self.settings_changed.emit()

    def _on_sound_volume_changed(self, value: int) -> None:
        self._settings.set("sound_volume", value)
        # No settings_changed emit needed — volume is read at play time.

    def _on_sound_event_changed(self) -> None:
        """Save per-event sound toggle states."""
        self._settings.set("sound_success", self._sound_success_check.isChecked())
        self._settings.set("sound_error", self._sound_error_check.isChecked())
        self._settings.set("sound_unlock", self._sound_unlock_check.isChecked())
        self._settings.set("sound_file_add", self._sound_file_add_check.isChecked())
        self._settings.set("sound_preview", self._sound_preview_check.isChecked())

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

    def _on_trail_length_changed(self, value: int) -> None:
        self._settings.set("trail_length", value)
        self.settings_changed.emit()

    def _on_trail_fade_changed(self, value: int) -> None:
        self._settings.set("trail_fade_speed", value)
        self.settings_changed.emit()

    def _on_trail_intensity_changed(self, value: int) -> None:
        self._settings.set("trail_intensity", value)
        self.settings_changed.emit()

    def _on_cursor_changed(self) -> None:
        self._settings.set("cursor", self._cursor_combo.currentText())
        self._settings.set("use_theme_cursor", self._use_theme_cursor_check.isChecked())
        self.settings_changed.emit()

    def _on_font_size_changed(self, value: int) -> None:
        self._settings.set("font_size", value)
        self.settings_changed.emit()

    def _on_effects_enabled_changed(self) -> None:
        enabled = self._click_effects_theme_check.isChecked()
        self._settings.set("click_effects_enabled", enabled)
        self.settings_changed.emit()

    def _on_use_theme_effect_changed(self) -> None:
        use_theme = self._use_theme_effect_check.isChecked()
        self._settings.set("use_theme_effect", use_theme)
        self._effect_combo.setEnabled(not use_theme)
        self.settings_changed.emit()

    def _on_tooltip_mode_changed(self) -> None:
        # Track first-ever tooltip mode change to trigger the Secret Skeleton unlock.
        # Both settings saves complete before the signal fires, so the unlock
        # only triggers after the flag and mode have been persisted.
        should_unlock = not self._settings.get("tooltip_mode_changed_once", False)
        if should_unlock:
            self._settings.set("tooltip_mode_changed_once", True)
        self._settings.set("tooltip_mode", self._tooltip_mode_combo.currentText())
        self.settings_changed.emit()
        if should_unlock:
            self.first_tooltip_mode_change.emit()

    def _on_tooltip_style_changed(self) -> None:
        self._settings.set("tooltip_style", self._tooltip_style_combo.currentText())
        self.settings_changed.emit()

    def _on_animated_banner_changed(self) -> None:
        enabled = self._animated_banner_check.isChecked()
        self._settings.set("animated_banner_enabled", enabled)
        # Enable/disable the subordinate controls based on the new state.
        use_theme = self._banner_use_theme_anim_check.isChecked()
        self._banner_anim_combo.setEnabled(enabled and not use_theme)
        self._banner_use_theme_anim_check.setEnabled(enabled)
        self.settings_changed.emit()

    def _on_banner_anim_style_changed(self) -> None:
        key = self._banner_anim_combo.currentData() or "spin"
        self._settings.set("banner_anim_style", key)
        self.settings_changed.emit()

    def _on_banner_use_theme_anim_changed(self) -> None:
        use_theme = self._banner_use_theme_anim_check.isChecked()
        self._settings.set("banner_use_theme_anim", use_theme)
        banner_enabled = self._animated_banner_check.isChecked()
        self._banner_anim_combo.setEnabled(banner_enabled and not use_theme)
        self.settings_changed.emit()

    def _on_show_splash_changed(self) -> None:
        self._settings.set("show_splash_screen", self._show_splash_check.isChecked())
        self.settings_changed.emit()

    def _on_button_anim_changed(self) -> None:
        enabled = self._button_anim_check.isChecked()
        self._settings.set("button_anim_enabled", enabled)
        use_theme = self._use_theme_button_anim_check.isChecked()
        self._button_anim_style_combo.setEnabled(enabled and not use_theme)
        self._use_theme_button_anim_check.setEnabled(enabled)
        self.settings_changed.emit()

    def _on_button_anim_style_changed(self) -> None:
        key = self._button_anim_style_combo.currentData() or "press"
        self._settings.set("button_anim_style", key)
        self.settings_changed.emit()

    def _on_use_theme_button_anim_changed(self) -> None:
        use_theme = self._use_theme_button_anim_check.isChecked()
        self._settings.set("use_theme_button_anim", use_theme)
        enabled = self._button_anim_check.isChecked()
        self._button_anim_style_combo.setEnabled(enabled and not use_theme)
        self.settings_changed.emit()

