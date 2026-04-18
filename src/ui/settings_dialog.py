"""
Settings / Customization dialog.
"""
import json
import os

from PyQt6.QtCore import pyqtSignal, Qt, QRect
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTabWidget, QWidget, QGridLayout, QCheckBox,
    QLineEdit, QColorDialog, QGroupBox, QScrollArea,
    QMessageBox, QInputDialog, QSpinBox, QFileDialog, QSlider,
    QAbstractSpinBox, QFrame,
)

from .theme_engine import PRESET_THEMES, HIDDEN_THEMES, THEME_DESCRIPTIONS, THEME_EFFECTS
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


def _label_width(text_sample: str) -> int:
    """Return a minimum pixel width wide enough for *text_sample* at the
    current application font, plus a small 4-pixel margin.  This replaces
    ``setFixedWidth(N)`` hard-codes so slider value labels scale with the
    system font size and HiDPI settings.
    """
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFontMetrics
    fm = QFontMetrics(QApplication.font())
    return fm.horizontalAdvance(text_sample) + 4

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
        # Size is font-relative so it scales correctly on HiDPI / large-font
        # systems.  em ≈ point size of the application font in pixels.
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(QApplication.font())
        em = fm.height()
        self.setFixedSize(int(em * 3.2), int(em * 1.6))

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
    # Emitted the very first time the user enables cursor animation.
    # MainWindow connects this to trigger the Toxic Neon unlock.
    first_cursor_anim_enabled = pyqtSignal()
    # Emitted the very first time the user selects a different theme preset.
    # MainWindow connects this to trigger the Candy Land unlock.
    first_theme_changed = pyqtSignal()
    # Emitted the very first time the user enables the mouse trail.
    # MainWindow connects this to trigger the Midnight Forest unlock.
    first_trail_enabled = pyqtSignal()

    def __init__(self, settings_manager, parent=None, tooltip_mgr=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._theme = settings_manager.get_theme()
        self._color_buttons: dict[str, ColorButton] = {}
        # Debounce timer for theme preset combo: delay live theme apply by
        # 120 ms to prevent lag when the user scrolls quickly through themes.
        from PyQt6.QtCore import QTimer
        self._theme_debounce = QTimer(self)
        self._theme_debounce.setSingleShot(True)
        self._theme_debounce.setInterval(350)  # item 54: increased for less scrolling lag; was 200 ms
        self._theme_debounce.timeout.connect(self._on_preset_selected_live)
        # Debounce timer for small combos (tooltip mode/style, sound profile etc.)
        # so rapid scroll events don't cause repeated QSettings I/O (item 79).
        self._misc_combo_debounce = QTimer(self)
        self._misc_combo_debounce.setSingleShot(True)
        self._misc_combo_debounce.setInterval(200)
        self._misc_combo_debounce.timeout.connect(self._flush_misc_combo_changes)
        # Pending values accumulated until the debounce fires.
        self._misc_combo_pending: dict[str, str] = {}
        self.setWindowTitle("Settings & Customization 🐼")
        # Adaptive minimum size: shrink proportionally on small or low-resolution
        # screens so the dialog is never forced off the visible area.  We keep the
        # floor generous enough that all content remains usable; the scroll areas
        # inside each tab mean even a 400×320 window can reach every control.
        screen = (self.parent().screen() if self.parent() is not None
                  else None) or QApplication.primaryScreen()
        if screen is not None:
            ag = screen.availableGeometry()
            min_w = min(760, max(440, int(ag.width()  * 0.45)))
            min_h = min(520, max(340, int(ag.height() * 0.45)))
        else:
            min_w, min_h = 760, 520
            ag = None
        self.setMinimumSize(min_w, min_h)
        # Set an initial size that fits the screen; the showEvent also clamps it
        # but this avoids an initial oversized paint on some platforms.  Leave at
        # least 100 px of margin so the dialog doesn't crowd the desktop.
        init_w = min(1020, max(min_w, ag.width() - 80)) if ag is not None else min_w
        init_h = min(680, max(min_h, ag.height() - 100)) if ag is not None else min_h
        self.resize(init_w, init_h)
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
        tv.setContentsMargins(4, 4, 4, 4)  # item 50: tighter margins for more content on screen
        tv.setSpacing(4)  # item 50: reduced spacing between GroupBoxes

        # ---- Preset GroupBox ----
        grp_preset_select = QGroupBox("Active Theme Preset")
        ps_vl = QVBoxLayout(grp_preset_select)
        ps_vl.setSpacing(4)
        ps_vl.setContentsMargins(6, 4, 6, 4)

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
        self._theme_preset_combo.setToolTip(
            "Choose a visual theme for the application.\n"
            "Hover over each theme to see a description of its style."
        )
        self._rebuild_theme_combo()
        psl.addWidget(self._theme_preset_combo, 1)
        self._btn_save_theme = QPushButton("Save as…")
        self._btn_delete_theme = QPushButton("Delete")
        self._btn_export_theme = QPushButton("Export Theme…")
        self._btn_import_theme = QPushButton("Import Theme…")
        self._btn_save_theme.setMinimumWidth(75)
        self._btn_delete_theme.setMinimumWidth(62)
        self._btn_export_theme.setMinimumWidth(110)
        self._btn_import_theme.setMinimumWidth(110)
        self._btn_export_theme.setToolTip(
            "Export the current custom theme settings to a JSON file.\n"
            "The file can be shared or imported on another installation."
        )
        self._btn_import_theme.setToolTip(
            "Import a theme from a previously exported JSON file.\n"
            "This will overwrite the current custom theme colors and settings."
        )
        psl.addWidget(self._btn_save_theme)
        psl.addWidget(self._btn_delete_theme)
        psl.addWidget(self._btn_export_theme)
        psl.addWidget(self._btn_import_theme)
        ps_vl.addLayout(psl)
        tv.addWidget(grp_preset_select)

        # ---- Custom Background GroupBox (item 81) ----
        grp_custom_bg = QGroupBox("Custom Background")
        custom_bg_layout = QVBoxLayout(grp_custom_bg)
        custom_bg_layout.setSpacing(6)
        self._custom_bg_check = QCheckBox("Enable custom background (image / GIF / video)")
        self._custom_bg_check.setToolTip(
            "When enabled the application window background uses a custom image,\n"
            "animated GIF, or video file instead of the solid theme colour.\n"
            "Supported: PNG, JPG, GIF, MP4, WebM, WebP."
        )
        custom_bg_layout.addWidget(self._custom_bg_check)
        # Sub-container: hidden when custom bg is disabled
        self._custom_bg_sub = QWidget()
        _cbg_vl = QVBoxLayout(self._custom_bg_sub)
        _cbg_vl.setContentsMargins(16, 0, 0, 0)
        _cbg_vl.setSpacing(4)
        self._use_theme_bg_check = QCheckBox(
            "Use theme background  (override disabled — theme controls background)"
        )
        self._use_theme_bg_check.setToolTip(
            "When enabled the active theme's built-in background is used,\n"
            "overriding any custom file selection below.\n"
            "Uncheck to force a custom file or a plain solid colour."
        )
        self._use_theme_bg_check.setChecked(True)
        _cbg_vl.addWidget(self._use_theme_bg_check)
        # File picker row (hidden when use-theme is on)
        self._custom_bg_file_row = QWidget()
        _cbg_file_hl = QHBoxLayout(self._custom_bg_file_row)
        _cbg_file_hl.setContentsMargins(0, 0, 0, 0)
        _cbg_file_hl.addWidget(QLabel("Background file:"))
        self._custom_bg_path_edit = QLineEdit()
        self._custom_bg_path_edit.setPlaceholderText(
            "Path to PNG / GIF / MP4 / WebM…"
        )
        self._custom_bg_path_edit.setToolTip(
            "Full path to the background image or video file.\n"
            "You can also drag a file from Explorer/Finder onto this field."
        )
        self._custom_bg_path_edit.setReadOnly(False)
        _cbg_file_hl.addWidget(self._custom_bg_path_edit, 1)
        self._custom_bg_browse_btn = QPushButton("…")
        self._custom_bg_browse_btn.setFixedWidth(30)
        self._custom_bg_browse_btn.setToolTip("Browse for a background image or video file.")
        _cbg_file_hl.addWidget(self._custom_bg_browse_btn)
        _cbg_vl.addWidget(self._custom_bg_file_row)
        custom_bg_layout.addWidget(self._custom_bg_sub)

        def _update_custom_bg_state():
            enabled = self._custom_bg_check.isChecked()
            use_theme = self._use_theme_bg_check.isChecked()
            self._custom_bg_sub.setVisible(enabled)
            self._custom_bg_file_row.setVisible(not use_theme)

        self._custom_bg_check.toggled.connect(lambda _: _update_custom_bg_state())
        self._use_theme_bg_check.toggled.connect(lambda _: _update_custom_bg_state())
        self._custom_bg_sub.setVisible(False)  # hidden until enabled

        def _browse_bg_file():
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Background File", "",
                "Images & Video (*.png *.jpg *.jpeg *.gif *.mp4 *.webm *.webp *.bmp *.tiff)"
                ";;All Files (*)"
            )
            if path:
                self._custom_bg_path_edit.setText(path)
                self._on_custom_bg_changed()

        self._custom_bg_browse_btn.clicked.connect(_browse_bg_file)
        tv.addWidget(grp_custom_bg)

        grp_colors = QGroupBox("Theme Colors")
        color_grid = QGridLayout(grp_colors)
        color_grid.setHorizontalSpacing(6)
        color_grid.setVerticalSpacing(4)
        color_grid.setContentsMargins(6, 4, 6, 4)
        color_keys = [
            ("background", "Background"),
            ("surface", "Surface"),
            ("primary", "Primary"),
            ("accent", "Accent"),
            ("text", "Text"),
            ("text_secondary", "Text Sec."),
            ("border", "Border"),
            ("success", "Success"),
            ("warning", "Warning"),
            ("error", "Error"),
            ("button_bg", "Button BG"),
            ("button_hover", "Btn Hover"),
            ("progress_bar", "Progress"),
            ("input_bg", "Input BG"),
            ("scrollbar_handle", "Scrollbar"),
        ]
        # 5-column layout for compact appearance (item 70)
        _COLS = 5
        for i, (key, label) in enumerate(color_keys):
            row, col = divmod(i, _COLS)
            lbl = QLabel(label + ":")
            lbl.setToolTip(f"Custom colour for the '{key}' theme slot.")
            color_grid.addWidget(lbl, row, col * 2)
            btn = ColorButton(self._theme.get(key, "#888888"))
            btn.color_changed.connect(lambda c, k=key: self._on_color_changed(k, c))
            self._color_buttons[key] = btn
            color_grid.addWidget(btn, row, col * 2 + 1)
        # Equal stretch between column pairs
        for c in range(_COLS):
            color_grid.setColumnStretch(c * 2 + 1, 1)

        scroll = QScrollArea()
        scroll.setWidget(grp_colors)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(110)
        scroll.setMinimumHeight(80)
        tv.addWidget(scroll)

        # ---- Effect + Emoji in a single row of GroupBoxes ----
        effect_emoji_row = QHBoxLayout()
        effect_emoji_row.setSpacing(8)

        grp_effect = QGroupBox("Click Effect Style")
        effect_layout = QVBoxLayout(grp_effect)
        effect_layout.setSpacing(4)
        effect_layout.setContentsMargins(6, 4, 6, 4)
        effect_layout.setSpacing(6)
        # On/off + use-theme row (mirrors the Mouse Trail group layout)
        self._click_effects_theme_check = QCheckBox("Enable click effects")
        effect_layout.addWidget(self._click_effects_theme_check)
        # Sub-container: hidden until click effects are enabled
        self._click_effect_sub = QWidget()
        _ce_sub_vl = QVBoxLayout(self._click_effect_sub)
        _ce_sub_vl.setContentsMargins(16, 0, 0, 0)
        _ce_sub_vl.setSpacing(4)
        self._use_theme_effect_check = QCheckBox(
            "Use theme effect  (auto-selects the matching effect for the active theme)"
        )
        self._use_theme_effect_check.setToolTip(
            "When enabled the click effect is chosen automatically to match\n"
            "the active theme — e.g. Gore gets blood splatter, Bat Cave gets bats."
        )
        _ce_sub_vl.addWidget(self._use_theme_effect_check)
        # Info label shown when "Use theme effect" is ON (replaces manual combo, item 4)
        self._effect_theme_info_lbl = QLabel("Theme effect: —")
        self._effect_theme_info_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._effect_theme_info_lbl.setVisible(False)
        _ce_sub_vl.addWidget(self._effect_theme_info_lbl)
        self._effect_inner_widget = QWidget()
        effect_inner = QHBoxLayout(self._effect_inner_widget)
        effect_inner.setContentsMargins(0, 0, 0, 0)
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
        _ce_sub_vl.addWidget(self._effect_inner_widget)
        effect_layout.addWidget(self._click_effect_sub)
        # Show/hide sub-container and combo enable state
        def _update_effect_sub():
            enabled = self._click_effects_theme_check.isChecked()
            use_theme = self._use_theme_effect_check.isChecked()
            self._click_effect_sub.setVisible(enabled)
            self._effect_inner_widget.setVisible(not use_theme)
            self._effect_theme_info_lbl.setVisible(use_theme)
            if use_theme:
                self._update_effect_theme_info()
        self._click_effects_theme_check.toggled.connect(lambda _: _update_effect_sub())
        self._use_theme_effect_check.toggled.connect(lambda _: _update_effect_sub())
        self._click_effect_sub.setVisible(False)
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

        # ---- Background Effects GroupBox ----
        grp_bg_drip = QGroupBox("Background Effects")
        bg_drip_layout = QVBoxLayout(grp_bg_drip)
        bg_drip_layout.setSpacing(4)
        bg_drip_layout.setContentsMargins(6, 4, 6, 4)
        self._bg_drip_check = QCheckBox("Enable background drip effect (off by default)")
        self._bg_drip_check.setToolTip(
            "When enabled, a continuous drip animation plays in the background\n"
            "independently of click effects. Blood drip for gore/dark themes;\n"
            "water drip for ocean/aquatic themes."
        )
        bg_drip_layout.addWidget(self._bg_drip_check)
        # Container widget for "Use theme" checkbox + style combo – shown/hidden as a unit
        self._bg_drip_sub = QWidget()
        bg_drip_sub_layout = QVBoxLayout(self._bg_drip_sub)
        bg_drip_sub_layout.setContentsMargins(16, 0, 0, 0)
        bg_drip_sub_layout.setSpacing(4)
        self._use_theme_drip_check = QCheckBox(
            "Use theme drip  (auto-selects Blood or Water based on the active theme)"
        )
        self._use_theme_drip_check.setToolTip(
            "When enabled the drip type is chosen automatically:\n"
            "Gore / Shark themes → Blood Drip\n"
            "Ocean / Ripple / Mermaid themes → Water Drip\n"
            "All other themes → no drip (this theme has no drip effect)."
        )
        bg_drip_sub_layout.addWidget(self._use_theme_drip_check)
        # Info label shown when use-theme is on (replaces the manual drip combo)
        self._bg_drip_theme_lbl = QLabel("Theme drip: —")
        self._bg_drip_theme_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._bg_drip_theme_lbl.setVisible(False)
        bg_drip_sub_layout.addWidget(self._bg_drip_theme_lbl)
        bg_drip_inner = QHBoxLayout()
        bg_drip_inner.addWidget(QLabel("Drip Style:"))
        self._bg_drip_combo = QComboBox()
        self._bg_drip_combo.setMinimumWidth(180)
        self._bg_drip_combo.addItem("🩸 Blood Drip", userData="blood")
        self._bg_drip_combo.addItem("💧 Water Drip", userData="water")
        self._bg_drip_combo.setToolTip(
            "Choose the drip style when 'Use theme drip' is off.\n"
            "Blood Drip: crimson tear-shaped drops fall from the top.\n"
            "Water Drip: translucent cyan drops fall from the top.\n"
            "To disable drip, uncheck 'Enable background drip' above."
        )
        bg_drip_inner.addWidget(self._bg_drip_combo, 1)
        bg_drip_sub_layout.addLayout(bg_drip_inner)
        bg_drip_layout.addWidget(self._bg_drip_sub)
        # Hide/show sub-container based on enable checkbox; enable/disable combo on use-theme
        # (item 1/4): drip combo stays visible — disabled + shows themed value when use-theme is on
        def _update_drip_combo_state():
            enabled = self._bg_drip_check.isChecked()
            use_theme = self._use_theme_drip_check.isChecked()
            self._bg_drip_sub.setVisible(enabled)
            self._bg_drip_combo.setEnabled(enabled and not use_theme)
            self._bg_drip_theme_lbl.setVisible(use_theme)
            if use_theme and enabled:
                # Derive drip type from the current theme's effect key
                try:
                    theme = self._settings.get_theme()
                    theme_name = theme.get("name", "")
                    eff = theme.get("_effect", "default")
                    if eff in ("gore", "shark"):
                        idx = self._bg_drip_combo.findData("blood")
                        self._bg_drip_theme_lbl.setText(
                            f"🩸 Auto-set by '{theme_name}' theme  →  Blood Drip"
                        )
                    elif eff in ("ocean", "ripple", "mermaid"):
                        idx = self._bg_drip_combo.findData("water")
                        self._bg_drip_theme_lbl.setText(
                            f"💧 Auto-set by '{theme_name}' theme  →  Water Drip"
                        )
                    else:
                        idx = -1
                        self._bg_drip_theme_lbl.setText(
                            f"🚫 '{theme_name}' theme has no drip effect"
                        )
                    if idx >= 0:
                        self._bg_drip_combo.blockSignals(True)
                        self._bg_drip_combo.setCurrentIndex(idx)
                        self._bg_drip_combo.blockSignals(False)
                except Exception:
                    pass
        self._bg_drip_check.toggled.connect(lambda _: _update_drip_combo_state())
        self._use_theme_drip_check.toggled.connect(lambda _: _update_drip_combo_state())
        self._bg_drip_sub.setVisible(False)  # hidden until drip is enabled

        # Separator between drip and flock sections
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        bg_drip_layout.addWidget(sep1)

        # ---- Flock sub-section ----
        self._bg_flock_check = QCheckBox("Enable background flock (emoji fly across the window)")
        self._bg_flock_check.setToolTip(
            "When enabled, themed emoji periodically fly across the top of the window\n"
            "as a background animation, independent of click effects."
        )
        bg_drip_layout.addWidget(self._bg_flock_check)
        self._bg_flock_sub = QWidget()
        _bf_sub_vl = QVBoxLayout(self._bg_flock_sub)
        _bf_sub_vl.setContentsMargins(16, 0, 0, 0)
        _bf_sub_vl.setSpacing(4)
        self._use_theme_flock_check = QCheckBox(
            "Use theme flock  (auto-selects the active theme's flock style, if any)"
        )
        self._use_theme_flock_check.setToolTip(
            "When enabled the flock style is chosen automatically from the active theme.\n"
            "Themes without a defined flock (e.g. Panda, Goth) will disable the flock\n"
            "entirely — no random bats will appear on themes that don't call for them.\n"
            "Uncheck to manually pick an emoji style from the list below."
        )
        _bf_sub_vl.addWidget(self._use_theme_flock_check)
        # Info label shown when "Use theme flock" is ON (replaces the manual combo, item 4)
        self._bg_flock_theme_lbl = QLabel("Theme flock: —")
        self._bg_flock_theme_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._bg_flock_theme_lbl.setVisible(False)
        _bf_sub_vl.addWidget(self._bg_flock_theme_lbl)
        self._bg_flock_combo = QComboBox()
        self._bg_flock_combo.setMinimumWidth(180)
        self._bg_flock_combo.addItem("🦇 Bats", userData="bats")
        self._bg_flock_combo.addItem("🧚 Fairies", userData="fairies")
        self._bg_flock_combo.addItem("🐟 Fish", userData="fish")
        self._bg_flock_combo.addItem("🦋 Butterflies", userData="butterflies")
        self._bg_flock_combo.addItem("🐦 Birds", userData="birds")
        self._bg_flock_combo.addItem("⭐ Stars", userData="stars")
        self._bg_flock_combo.addItem("🌸 Petals", userData="petals")
        self._bg_flock_combo.addItem("🦈 Sharks", userData="sharks")
        self._bg_flock_combo.setToolTip(
            "Choose the emoji used for the background flock.\n"
            "Hidden while 'Use theme flock' is checked — the theme controls the style."
        )
        self._bg_flock_inner_widget = QWidget()
        _bfi_row = QHBoxLayout(self._bg_flock_inner_widget)
        _bfi_row.setContentsMargins(0, 0, 0, 0)
        _bfi_row.addWidget(QLabel("Flock Style:"))
        _bfi_row.addWidget(self._bg_flock_combo, 1)
        _bf_sub_vl.addWidget(self._bg_flock_inner_widget)
        bg_drip_layout.addWidget(self._bg_flock_sub)
        # Hide/show sub-container; show info label vs combo row based on use-theme toggle
        def _update_flock_combo_state():
            enabled = self._bg_flock_check.isChecked()
            use_theme = self._use_theme_flock_check.isChecked()
            self._bg_flock_sub.setVisible(enabled)
            self._bg_flock_theme_lbl.setVisible(use_theme)
            # item 1/4: inner widget stays visible — just disable combo when use-theme is on
            self._bg_flock_inner_widget.setVisible(True)
            self._bg_flock_combo.setEnabled(enabled and not use_theme)
        self._bg_flock_check.toggled.connect(lambda _: _update_flock_combo_state())
        self._use_theme_flock_check.toggled.connect(lambda _: _update_flock_combo_state())
        self._bg_flock_sub.setVisible(False)  # hidden until flock is enabled

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        bg_drip_layout.addWidget(sep2)

        # ---- Ambient Effect sub-section ----
        self._bg_ambient_check = QCheckBox("Enable background ambient effect")
        self._bg_ambient_check.setToolTip(
            "When enabled, a continuous ambient animation plays in the background\n"
            "regardless of the active theme. Pairs beautifully with any theme."
        )
        bg_drip_layout.addWidget(self._bg_ambient_check)
        self._bg_ambient_sub = QWidget()
        _ba_sub_vl = QVBoxLayout(self._bg_ambient_sub)
        _ba_sub_vl.setContentsMargins(16, 0, 0, 0)
        _ba_sub_vl.setSpacing(4)
        self._use_theme_ambient_check = QCheckBox(
            "Use theme ambient  (auto-selects the active theme's ambient style, if any)"
        )
        self._use_theme_ambient_check.setToolTip(
            "When enabled the ambient style is chosen automatically from the active theme.\n"
            "Themes without a defined ambient (e.g. Panda, Otter) will disable the effect.\n"
            "Uncheck to manually pick an ambient style from the list below."
        )
        _ba_sub_vl.addWidget(self._use_theme_ambient_check)
        # Info label shown when "Use theme ambient" is ON (replaces the manual combo, item 4)
        self._bg_ambient_theme_lbl = QLabel("Theme ambient: —")
        self._bg_ambient_theme_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._bg_ambient_theme_lbl.setVisible(False)
        _ba_sub_vl.addWidget(self._bg_ambient_theme_lbl)
        self._bg_ambient_combo = QComboBox()
        self._bg_ambient_combo.setMinimumWidth(180)
        self._bg_ambient_combo.addItem("❄️ Snow Drift", userData="snow")
        self._bg_ambient_combo.addItem("🔥 Ember Drift", userData="ember")
        self._bg_ambient_combo.addItem("🌸 Sakura Petals", userData="sakura")
        self._bg_ambient_combo.addItem("✨ Shooting Stars", userData="stars")
        self._bg_ambient_combo.addItem("🫧 Rising Bubbles", userData="bubbles")
        self._bg_ambient_combo.addItem("🌈 Neon Flicker", userData="neon")
        self._bg_ambient_combo.addItem("👻 Ghost Wisps", userData="ghost")
        self._bg_ambient_combo.addItem("🎊 Confetti Fall", userData="confetti")
        self._bg_ambient_combo.addItem("🪲 Fireflies", userData="firefly")
        self._bg_ambient_combo.addItem("💻 Matrix Rain", userData="matrix")
        self._bg_ambient_combo.addItem("🍂 Autumn Leaves", userData="leaves")
        self._bg_ambient_combo.addItem("🌈 Rainbow Sparkle", userData="rainbow")
        self._bg_ambient_combo.addItem("🎋 Bamboo Leaves", userData="bamboo")
        self._bg_ambient_combo.setToolTip(
            "Choose the ambient background animation style.\n"
            "Hidden while 'Use theme ambient' is checked — the theme controls the style."
        )
        self._bg_ambient_inner_widget = QWidget()
        _bai_row = QHBoxLayout(self._bg_ambient_inner_widget)
        _bai_row.setContentsMargins(0, 0, 0, 0)
        _bai_row.addWidget(QLabel("Ambient Style:"))
        _bai_row.addWidget(self._bg_ambient_combo, 1)
        _ba_sub_vl.addWidget(self._bg_ambient_inner_widget)
        bg_drip_layout.addWidget(self._bg_ambient_sub)

        def _update_ambient_combo_state():
            enabled = self._bg_ambient_check.isChecked()
            use_theme = self._use_theme_ambient_check.isChecked()
            self._bg_ambient_sub.setVisible(enabled)
            self._bg_ambient_theme_lbl.setVisible(use_theme)
            # item 1/4: inner widget stays visible — just disable combo when use-theme is on
            self._bg_ambient_inner_widget.setVisible(True)
            self._bg_ambient_combo.setEnabled(enabled and not use_theme)

        self._bg_ambient_check.toggled.connect(lambda _: _update_ambient_combo_state())
        self._use_theme_ambient_check.toggled.connect(lambda _: _update_ambient_combo_state())
        self._bg_ambient_sub.setVisible(False)  # hidden until ambient is enabled

        tv.addWidget(grp_bg_drip)

        # ---- Notifications & Pop-ups Overlay GroupBox (item 66) ----
        grp_notif = QGroupBox("Achievement Notifications && Pop-ups Overlay")
        notif_layout = QVBoxLayout(grp_notif)
        notif_layout.setSpacing(6)
        self._notif_overlay_check = QCheckBox(
            "Enable achievement notifications overlay (on by default)"
        )
        self._notif_overlay_check.setToolTip(
            "When enabled, unlock and achievement notifications appear as a\n"
            "transparent overlay banner.  Uncheck to disable all pop-up banners."
        )
        self._notif_overlay_check.setChecked(True)
        notif_layout.addWidget(self._notif_overlay_check)
        # Sub-container: hidden when notifications are disabled
        self._notif_overlay_sub = QWidget()
        _notif_sub_vl = QVBoxLayout(self._notif_overlay_sub)
        _notif_sub_vl.setContentsMargins(16, 0, 0, 0)
        _notif_sub_vl.setSpacing(4)
        self._use_theme_notif_check = QCheckBox(
            "Use theme notification style  (auto-style per active theme)"
        )
        self._use_theme_notif_check.setToolTip(
            "When enabled the notification banner style (colors, emoji) is chosen\n"
            "automatically to match the active theme.\n"
            "Uncheck to use the default notification style regardless of theme."
        )
        self._use_theme_notif_check.setChecked(True)
        _notif_sub_vl.addWidget(self._use_theme_notif_check)
        # Info label shown when "Use theme" is ON
        self._notif_theme_info_lbl = QLabel("Using theme notification style")
        self._notif_theme_info_lbl.setStyleSheet(
            "color: #aaa; font-size: 10px; margin-left: 4px;"
        )
        self._notif_theme_info_lbl.setVisible(True)
        _notif_sub_vl.addWidget(self._notif_theme_info_lbl)
        notif_layout.addWidget(self._notif_overlay_sub)

        def _update_notif_state():
            enabled = self._notif_overlay_check.isChecked()
            use_theme = self._use_theme_notif_check.isChecked()
            self._notif_overlay_sub.setVisible(enabled)
            self._notif_theme_info_lbl.setVisible(use_theme)

        self._notif_overlay_check.toggled.connect(lambda _: _update_notif_state())
        self._use_theme_notif_check.toggled.connect(lambda _: _update_notif_state())
        # Default: sub visible (enabled=True), theme info visible (use_theme=True)
        self._notif_overlay_sub.setVisible(True)
        tv.addWidget(grp_notif)


        mouse_row = QHBoxLayout()
        mouse_row.setSpacing(4)  # item 50: tighter horizontal spacing

        grp_trail = QGroupBox("Mouse Trail")
        trail_gl = QGridLayout(grp_trail)
        trail_gl.setSpacing(4)
        trail_gl.setContentsMargins(6, 4, 6, 4)
        trail_gl.setColumnStretch(1, 1)
        trail_gl.setHorizontalSpacing(10)
        trail_gl.setVerticalSpacing(6)
        self._trail_check = QCheckBox("Enable mouse trail")
        trail_gl.addWidget(self._trail_check, 0, 0, 1, 2)
        # Sub-container: hidden until trail is enabled
        self._trail_sub = QWidget()
        _trail_sub_vl = QVBoxLayout(self._trail_sub)
        _trail_sub_vl.setContentsMargins(0, 0, 0, 0)
        _trail_sub_vl.setSpacing(4)
        self._use_theme_trail_check = QCheckBox(
            "Use theme trail  (auto-color + special style per effect)"
        )
        self._use_theme_trail_check.setToolTip(
            "When enabled the trail color and style are chosen automatically to match\n"
            "the active theme effect.  Fairy Garden gets sparkle fairy dust (✨💫⭐),\n"
            "Ocean/Mermaid get wave emoji (🫧💧🌊), Ice/Sparkle get crystal emoji (✦❄✧)."
        )
        _trail_sub_vl.addWidget(self._use_theme_trail_check)
        # Info label shown when "Use theme trail" is ON (replaces manual color/style, item 4)
        self._trail_theme_info_lbl = QLabel("Theme trail: —")
        self._trail_theme_info_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._trail_theme_info_lbl.setVisible(False)
        _trail_sub_vl.addWidget(self._trail_theme_info_lbl)
        # Manual color + style widget (hidden when use_theme is ON)
        self._trail_manual_widget = QWidget()
        _tmw_gl = QGridLayout(self._trail_manual_widget)
        _tmw_gl.setContentsMargins(0, 0, 0, 0)
        _tmw_gl.setColumnStretch(1, 1)
        _tmw_gl.setHorizontalSpacing(10)
        _tmw_gl.setVerticalSpacing(6)
        _tmw_gl.addWidget(QLabel("Trail Color:"), 0, 0)
        self._trail_color_btn = ColorButton("#e94560")
        _tmw_gl.addWidget(self._trail_color_btn, 0, 1, Qt.AlignmentFlag.AlignLeft)
        _tmw_gl.addWidget(QLabel("Trail Style:"), 1, 0)
        self._trail_style_combo = QComboBox()
        _TRAIL_STYLE_OPTIONS = [
            ("Dots ·",             "Dots  – Small colored dots fade out behind the cursor."),
            ("Ribbon 🎀",          "Ribbon  – Smooth connected line trails the cursor like a ribbon."),
            ("Noodle 🍜",          "Noodle  – Physics-simulated dangling chain that wobbles and swings as you move the mouse."),
            ("Comet Tail ☄",       "Comet Tail  – Tapered bright streak that fades to nothing behind the cursor."),
            ("Fairy Dust ✨",       "Fairy Dust  – ✨💫⭐ emoji sparkles float and fade as you move."),
            ("Ocean Wave 🌊",      "Ocean Wave  – 🫧💧🌊🐠 emoji drift and ripple behind the cursor."),
            ("Sparkle Ice ❄",      "Sparkle Ice  – ✦❄✧💎 glittering ice crystals trail behind the cursor."),
            ("Rainbow 🌈",         "Rainbow  – Full spectrum hue cycle: trail sweeps through the entire colour wheel."),
            ("Wavy Ribbon 〰",      "Wavy Ribbon  – A sinusoidal ribbon that writhes and ripples as you move the cursor."),
            ("Fire 🔥",            "Fire  – Glowing embers drift upward behind the cursor, hot yellow to deep red."),
            ("Lightning ⚡",       "Lightning  – Brief bright bolt-flashes crackle along the trail and vanish instantly."),
            ("Plasma Arc 🔵",      "Plasma Arc  – Electric arc sparks crackle in purple and cyan, fading fast like a static discharge."),
            ("Sakura 🌸",          "Sakura  – Soft pink petals drift and spin behind the cursor, fading gently as they fall."),
            ("Smoke 💨",           "Smoke  – Soft gray puffs expand and rise behind the cursor, dissipating into nothing."),
        ]
        for label, tip in _TRAIL_STYLE_OPTIONS:
            self._trail_style_combo.addItem(label)
            idx = self._trail_style_combo.count() - 1
            self._trail_style_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._trail_style_combo.setToolTip(
            "Choose the visual style of the mouse trail.\n"
            "Ribbon – smooth line, Noodle – physics chain, Comet Tail – tapered streak,\n"
            "Fairy Dust / Ocean Wave / Sparkle Ice – themed emoji trails,\n"
            "Wavy Ribbon – sinusoidal ribbon, Fire – rising embers,\n"
            "Lightning – crackle bolts, Plasma Arc – electric arc sparks,\n"
            "Sakura – drifting petals, Smoke – rising gray puffs."
        )
        _tmw_gl.addWidget(self._trail_style_combo, 1, 1)
        _trail_sub_vl.addWidget(self._trail_manual_widget)
        def _update_trail_sub():
            use_theme = self._use_theme_trail_check.isChecked()
            self._trail_manual_widget.setVisible(not use_theme)
            self._trail_theme_info_lbl.setVisible(use_theme)
            if use_theme:
                self._update_trail_theme_info()
        self._use_theme_trail_check.toggled.connect(lambda _: _update_trail_sub())

        # Slider sub-widget for length / fade / intensity
        _trail_sliders_widget = QWidget()
        _trail_sliders_gl = QGridLayout(_trail_sliders_widget)
        _trail_sliders_gl.setContentsMargins(0, 0, 0, 0)
        _trail_sliders_gl.setColumnStretch(1, 1)
        _trail_sliders_gl.setHorizontalSpacing(10)
        _trail_sliders_gl.setVerticalSpacing(6)
        # Trail Length slider (10–200 points)
        _trail_sliders_gl.addWidget(QLabel("Trail Length:"), 0, 0)
        self._trail_length_slider = QSlider(Qt.Orientation.Horizontal)
        self._trail_length_slider.setRange(_TRAIL_LENGTH_MIN, _TRAIL_LENGTH_MAX)
        self._trail_length_slider.setValue(_TRAIL_LENGTH_DEFAULT)
        self._trail_length_slider.setToolTip(
            "Controls how many trail points are kept.\n"
            "Short = snappy; Long = lingering ghost trail."
        )
        self._trail_length_val_lbl = QLabel(str(_TRAIL_LENGTH_DEFAULT))
        self._trail_length_val_lbl.setMinimumWidth(_label_width("200"))
        length_row = QHBoxLayout()
        length_row.addWidget(self._trail_length_slider)
        length_row.addWidget(self._trail_length_val_lbl)
        _trail_sliders_gl.addLayout(length_row, 0, 1)
        self._trail_length_slider.valueChanged.connect(
            lambda v: self._trail_length_val_lbl.setText(str(v))
        )
        self._trail_length_slider.valueChanged.connect(self._on_trail_length_changed)

        # Trail Fade Speed slider (1 slow … 10 fast)
        _trail_sliders_gl.addWidget(QLabel("Fade Speed:"), 1, 0)
        self._trail_fade_slider = QSlider(Qt.Orientation.Horizontal)
        self._trail_fade_slider.setRange(_TRAIL_FADE_MIN, _TRAIL_FADE_MAX)
        self._trail_fade_slider.setValue(_TRAIL_FADE_DEFAULT)
        self._trail_fade_slider.setToolTip(
            "How quickly the trail fades out.\n"
            "1 = very slow (long ghost), 10 = very fast (sharp snap)."
        )
        self._trail_fade_val_lbl = QLabel(str(_TRAIL_FADE_DEFAULT))
        self._trail_fade_val_lbl.setMinimumWidth(_label_width("10"))
        fade_row = QHBoxLayout()
        fade_row.addWidget(self._trail_fade_slider)
        fade_row.addWidget(self._trail_fade_val_lbl)
        _trail_sliders_gl.addLayout(fade_row, 1, 1)
        self._trail_fade_slider.valueChanged.connect(
            lambda v: self._trail_fade_val_lbl.setText(str(v))
        )
        self._trail_fade_slider.valueChanged.connect(self._on_trail_fade_changed)

        # Trail Intensity slider (10–100 %)
        _trail_sliders_gl.addWidget(QLabel("Intensity:"), 2, 0)
        self._trail_intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self._trail_intensity_slider.setRange(_TRAIL_INTENSITY_MIN, _TRAIL_INTENSITY_MAX)
        self._trail_intensity_slider.setValue(_TRAIL_INTENSITY_DEFAULT)
        self._trail_intensity_slider.setToolTip(
            "Maximum opacity of the trail (10 % = very faint, 100 % = fully bright)."
        )
        self._trail_intensity_val_lbl = QLabel(f"{_TRAIL_INTENSITY_DEFAULT}%")
        self._trail_intensity_val_lbl.setMinimumWidth(_label_width("100%"))
        intensity_row = QHBoxLayout()
        intensity_row.addWidget(self._trail_intensity_slider)
        intensity_row.addWidget(self._trail_intensity_val_lbl)
        _trail_sliders_gl.addLayout(intensity_row, 2, 1)
        self._trail_intensity_slider.valueChanged.connect(
            lambda v: self._trail_intensity_val_lbl.setText(f"{v}%")
        )
        self._trail_intensity_slider.valueChanged.connect(self._on_trail_intensity_changed)
        _trail_sub_vl.addWidget(_trail_sliders_widget)
        # Add sub-container to trail_gl and connect visibility to enable checkbox.
        # Visibility is managed by _on_trail_changed (connected later at line ~1385).
        trail_gl.addWidget(self._trail_sub, 1, 0, 1, 2)
        self._trail_sub.setVisible(False)

        mouse_row.addWidget(grp_trail, 1)

        grp_cursor = QGroupBox("Cursor")
        cursor_gl = QVBoxLayout(grp_cursor)
        cursor_gl.setSpacing(4)
        cursor_gl.setContentsMargins(6, 4, 6, 4)
        self._cursor_enable_check = QCheckBox("Enable custom cursor  (off by default)")
        self._cursor_enable_check.setToolTip(
            "When off the system default cursor is always used.\n"
            "Turn on to select a custom cursor style or let the active theme choose one."
        )
        cursor_gl.addWidget(self._cursor_enable_check)
        # Sub-container: hidden until custom cursor is enabled (item 71)
        self._cursor_sub = QWidget()
        _cursor_sub_vl = QVBoxLayout(self._cursor_sub)
        _cursor_sub_vl.setContentsMargins(8, 0, 0, 0)
        _cursor_sub_vl.setSpacing(4)
        self._use_theme_cursor_check = QCheckBox(
            "Use theme cursor  (auto-selects cursor for the active theme)"
        )
        self._use_theme_cursor_check.setToolTip(
            "When enabled the cursor shape is chosen automatically to match the\n"
            "active theme — e.g. Otter Cove gets the 🦦 otter cursor."
        )
        _cursor_sub_vl.addWidget(self._use_theme_cursor_check)
        # Info label shown when "Use theme cursor" is ON (item 4)
        self._cursor_theme_info_lbl = QLabel("Theme cursor: —")
        self._cursor_theme_info_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._cursor_theme_info_lbl.setVisible(False)
        _cursor_sub_vl.addWidget(self._cursor_theme_info_lbl)
        # Manual style widget (hidden when use_theme is ON)
        self._cursor_manual_widget = QWidget()
        _cmw_gl = QGridLayout(self._cursor_manual_widget)
        _cmw_gl.setContentsMargins(0, 0, 0, 0)
        _cmw_gl.setColumnStretch(1, 1)
        _cmw_gl.setHorizontalSpacing(10)
        _cmw_gl.addWidget(QLabel("Cursor Style:"), 0, 0)
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
        self._cursor_combo.setToolTip(
            "Choose the mouse cursor shape used throughout the application.\n"
            "Emoji cursors animate when 'Animate cursor' is enabled.\n"
            "Hidden when 'Use theme cursor' is checked."
        )
        _cmw_gl.addWidget(self._cursor_combo, 0, 1)
        _cursor_sub_vl.addWidget(self._cursor_manual_widget)
        self._cursor_anim_check = QCheckBox(
            "Animate cursor  (cycles themed frames for emoji cursors)"
        )
        self._cursor_anim_check.setToolTip(
            "When enabled, emoji cursors with defined animation sequences cycle\n"
            "through frames at ~4 fps (e.g. 🦈 snapping, 🔥 flickering, ✨ sparkling).\n"
            "Disable if you prefer a static cursor or need to reduce CPU usage."
        )
        _cursor_sub_vl.addWidget(self._cursor_anim_check)
        cursor_gl.addWidget(self._cursor_sub)
        self._cursor_sub.setVisible(False)  # hidden until custom cursor is enabled
        # Wire enable check → show/hide sub and use_theme check → show/hide manual/info
        def _update_cursor_sub():
            cursor_en = self._cursor_enable_check.isChecked()
            use_theme = self._use_theme_cursor_check.isChecked()
            self._cursor_sub.setVisible(cursor_en)
            # item 1/4: keep combo visible (disabled) when use-theme is on
            self._cursor_manual_widget.setVisible(True)
            self._cursor_combo.setEnabled(cursor_en and not use_theme)
            self._cursor_theme_info_lbl.setVisible(use_theme)
            if use_theme:
                self._update_cursor_theme_info()
        self._cursor_enable_check.toggled.connect(lambda _: _update_cursor_sub())
        self._use_theme_cursor_check.toggled.connect(lambda _: _update_cursor_sub())
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

        # Sub-controls container — shown only when sounds are enabled (item 68).
        self._sound_sub_widget = QWidget()
        sub_sound_gl = QGridLayout(self._sound_sub_widget)
        sub_sound_gl.setColumnStretch(1, 1)
        sub_sound_gl.setHorizontalSpacing(10)
        sub_sound_gl.setVerticalSpacing(6)
        sub_sound_gl.setContentsMargins(0, 0, 0, 0)
        self._sound_sub_widget.setVisible(False)  # hidden until sounds enabled
        sound_gl.addWidget(self._sound_sub_widget, 1, 0, 1, 2)

        # Redirect further adds to sub_sound_gl so they appear inside the container.
        _s = sub_sound_gl
        self._use_theme_sound_check = QCheckBox("Use theme sound")
        self._use_theme_sound_check.setToolTip(
            "When enabled the click sound automatically uses the sound profile\n"
            "associated with the currently active visual theme.\n"
            "Each theme has a distinct profile (e.g. Gore = growl, Panda = soft,\n"
            "Alien = bright).  The info label below shows the current profile.\n"
            "Disable to choose a profile manually from the dropdown below."
        )
        _s.addWidget(self._use_theme_sound_check, 0, 0, 1, 2)

        # Informational tooltip still exists on the combo itself.
        # The separate info label is only shown as a fallback when theme
        # detection fails (item 1 — combo stays visible, not replaced by label).
        self._sound_theme_info_lbl = QLabel("")
        self._sound_theme_info_lbl.setWordWrap(True)
        self._sound_theme_info_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        _s.addWidget(self._sound_theme_info_lbl, 1, 0, 1, 2)
        self._sound_theme_info_lbl.setVisible(False)

        # Sound profile dropdown — stays visible in both manual and use-theme
        # modes (item 1).  When "Use theme sound" is ON the combo is disabled
        # and shows the theme's auto-selected profile; when OFF it is enabled
        # and lets the user pick manually.
        self._sound_profile_lbl = QLabel("Sound profile:")
        _s.addWidget(self._sound_profile_lbl, 2, 0)
        self._sound_profile_combo = QComboBox()
        _SOUND_PROFILE_OPTIONS = [
            ("soft",    "Soft — gentle chime 🎵"),
            ("bright",  "Bright — snappy ping ✨"),
            ("dark",    "Dark — low thud 🌑"),
            ("hard",    "Hard — punchy hit 💥"),
            ("warm",    "Warm — mellow tone 🌅"),
            ("icy",     "Icy — crystalline click ❄"),
            ("sparkle", "Sparkle — shimmery tinkle 🌟"),
            ("growl",   "Growl — gritty bass 🩸"),
            ("bubble",  "Bubble — pop 🫧"),
            ("chirp",   "Chirp — bird-like tweet 🐦"),
            ("crunch",  "Crunch — bone snap 💀"),
            ("purr",    "Purr — cat rumble 🐱"),
            ("meow",    "Meow — cat cry 🐱"),
            ("roar",    "Roar — fierce growl 🐉"),
            ("bark",    "Bark — dog bark 🐶"),
            ("howl",    "Howl — wolf howl 🐺"),
            ("hiss",    "Hiss — serpent hiss 🐍"),
            ("rock",    "Rock — cycling guitar riff 🎸"),
            ("splash",  "Splash — water drop 💧"),
            ("tweet",   "Tweet — forest bird 🌿"),
        ]
        for key, label in _SOUND_PROFILE_OPTIONS:
            self._sound_profile_combo.addItem(label, userData=key)
        self._sound_profile_combo.setToolTip(
            "Click-sound profile used when 'Use theme sound' is disabled.\n"
            "Each profile has a distinct tone that matches different moods.\n"
            "Ignored when 'Use theme sound' is enabled."
        )
        _s.addWidget(self._sound_profile_combo, 2, 1)

        # Volume slider
        _s.addWidget(QLabel("Volume:"), 3, 0)
        vol_row = QHBoxLayout()
        self._sound_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._sound_volume_slider.setRange(0, 100)
        self._sound_volume_slider.setValue(50)
        self._sound_volume_slider.setToolTip(
            "Master volume for all application sounds (0 = silent, 100 = full).\n"
            "Only affects the Qt Multimedia backend; subprocess fallback ignores this."
        )
        self._sound_volume_lbl = QLabel("50%")
        self._sound_volume_lbl.setMinimumWidth(_label_width("100%"))
        self._sound_volume_slider.valueChanged.connect(
            lambda v: self._sound_volume_lbl.setText(f"{v}%")
        )
        vol_row.addWidget(self._sound_volume_slider, 1)
        vol_row.addWidget(self._sound_volume_lbl)
        _s.addLayout(vol_row, 3, 1)

        # Sound event toggles (off by default; each controls a specific app event)
        events_lbl = QLabel("Event sounds:")
        events_lbl.setToolTip(
            "Enable or disable individual application sound events.\n"
            "All are off by default to keep the app unobtrusive."
        )
        _s.addWidget(events_lbl, 4, 0)
        self._btn_mute_all_events = QPushButton("Mute all events")
        self._btn_mute_all_events.setMinimumWidth(140)
        self._btn_mute_all_events.setToolTip(
            "Turn off all three event-sound checkboxes at once.\n"
            "Does not affect the master Enable sounds toggle."
        )
        self._btn_mute_all_events.clicked.connect(self._on_mute_all_events)
        _s.addWidget(self._btn_mute_all_events, 7, 0, 1, 2)
        self._sound_theme_change_chk = QCheckBox("Play sound when theme changes")
        self._sound_theme_change_chk.setToolTip(
            "Play a short whoosh sound whenever the active theme is switched.\n"
            "Off by default."
        )
        _s.addWidget(self._sound_theme_change_chk, 4, 1)

        self._sound_tab_switch_chk = QCheckBox("Play sound when switching tabs")
        self._sound_tab_switch_chk.setToolTip(
            "Play a soft tick when you click between the main tabs.\n"
            "Off by default."
        )
        _s.addWidget(self._sound_tab_switch_chk, 5, 1)

        self._sound_drag_enter_chk = QCheckBox("Play sound when files are dragged in")
        self._sound_drag_enter_chk.setToolTip(
            "Play a ping when files are dragged into the file list.\n"
            "Off by default."
        )
        _s.addWidget(self._sound_drag_enter_chk, 6, 1)

        # grp_sound is added to the dedicated Sound tab below; keep the reference.
        self._grp_sound = grp_sound

        # ---- Button Press Animation GroupBox ----
        grp_btn_anim = QGroupBox("Button Press Animation")
        btn_anim_gl = QGridLayout(grp_btn_anim)
        btn_anim_gl.setColumnStretch(1, 1)
        btn_anim_gl.setHorizontalSpacing(10)
        btn_anim_gl.setVerticalSpacing(6)
        self._button_anim_check = QCheckBox(
            "Enable button press animations"
        )
        self._button_anim_check.setToolTip(
            "When enabled every QPushButton in the app plays a short animation\n"
            "when clicked — a subtle slide, bounce, shake, or particle burst.\n"
            "Enabled by default. Disable to improve performance on slow systems."
        )
        btn_anim_gl.addWidget(self._button_anim_check, 0, 0, 1, 2)
        # Container for sub-options — hidden until button animation is enabled
        self._btn_anim_sub = QWidget()
        _ba_vl = QVBoxLayout(self._btn_anim_sub)
        _ba_vl.setContentsMargins(16, 0, 0, 0)
        _ba_vl.setSpacing(4)
        self._use_theme_button_anim_check = QCheckBox(
            "Use theme animation (each theme picks its own style)"
        )
        self._use_theme_button_anim_check.setToolTip(
            "When checked the animation style is chosen automatically by the\n"
            "active theme — e.g. Gore/Zombie/Dragon gets 'Shatter', Alien/Neon\n"
            "gets 'Shake', Fairy/Sakura gets 'Bounce'.\n"
            "Uncheck to force a fixed style from the dropdown below."
        )
        _ba_vl.addWidget(self._use_theme_button_anim_check)
        _ba_style_row = QHBoxLayout()
        _ba_style_row.addWidget(QLabel("Animation style:"))
        self._button_anim_style_combo = QComboBox()
        _BUTTON_ANIM_OPTIONS = [
            ("press",   "Press — 4 px downward nudge"),
            ("fall",    "Fall — 12 px drop and spring back"),
            ("bounce",  "Bounce — button leaps up and bounces back"),
            ("shake",   "Shake — rapid left/right vibration"),
            ("shatter", "Shatter — particle burst from button centre"),
            ("vanish",  "Vanish — shrinks to nothing then snaps back"),
            ("explode", "Explode — expands outward then collapses"),
        ]
        _BUTTON_ANIM_TIPS = {
            "press":   "The button shifts 4 pixels down on press then springs back.\n"
                       "Subtle and satisfying — closest to a real physical button.",
            "fall":    "The button slides 12 pixels down over ~260 ms then springs back.\n"
                       "Heavier feel, great for ocean/cave/goth themes.",
            "bounce":  "The button shoots up 6 pixels then bounces back down.\n"
                       "Playful and energetic — great for fairy/candy/sakura themes.",
            "shake":   "Rapid left/right vibration (~5 px over ~300 ms).\n"
                       "Aggressive energy — great for neon/alien/storm themes.",
            "shatter": "Spawns themed click-effect particles from the button centre.\n"
                       "Requires click effects to be enabled for best results.\n"
                       "Dramatic — great for gore/volcano/dragon themes.",
            "vanish":  "The button shrinks toward its centre then elastically snaps back.\n"
                       "Fun and punchy — great for fairy/candy/panda themes.",
            "explode": "The button rapidly expands outward then bounces back to size.\n"
                       "Big impact energy — great for volcano/storm/neon themes.",
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
            "Hidden while 'Use theme animation' is checked."
        )
        self._button_anim_style_combo.setMinimumWidth(220)
        _ba_style_row.addWidget(self._button_anim_style_combo, 1)
        # Wrap style row in a container so we can hide it as a unit (item 4)
        self._btn_anim_style_widget = QWidget()
        _bas_vl = QVBoxLayout(self._btn_anim_style_widget)
        _bas_vl.setContentsMargins(0, 0, 0, 0)
        _bas_vl.addLayout(_ba_style_row)
        _ba_vl.addWidget(self._btn_anim_style_widget)
        self._btn_anim_theme_info_lbl = QLabel("Theme animation: —")
        self._btn_anim_theme_info_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._btn_anim_theme_info_lbl.setVisible(False)
        _ba_vl.addWidget(self._btn_anim_theme_info_lbl)
        btn_anim_gl.addWidget(self._btn_anim_sub, 1, 0, 1, 2)
        # Show/hide sub-container when enable checkbox changes
        def _update_btn_anim_sub():
            enabled = self._button_anim_check.isChecked()
            use_theme = self._use_theme_button_anim_check.isChecked()
            self._btn_anim_sub.setVisible(enabled)
            self._btn_anim_style_widget.setVisible(not use_theme)
            self._btn_anim_theme_info_lbl.setVisible(use_theme)
            if use_theme and enabled:
                self._update_btn_anim_theme_info()
        self._button_anim_check.toggled.connect(lambda _: _update_btn_anim_sub())
        self._use_theme_button_anim_check.toggled.connect(lambda _: _update_btn_anim_sub())
        self._btn_anim_sub.setVisible(False)  # hidden by default

        tv.addWidget(grp_btn_anim)

        # ---- Banner & SVG Animation GroupBox (moved here from General tab) ----
        grp_banner = QGroupBox("Banner && SVG Badge Animation")
        banner_gl = QGridLayout(grp_banner)
        banner_gl.setColumnStretch(1, 1)
        banner_gl.setHorizontalSpacing(10)
        banner_gl.setVerticalSpacing(6)

        self._animated_banner_check = QCheckBox(
            "Enable animated banner emojis && SVG badge (off by default)"
        )
        self._animated_banner_check.setToolTip(
            "When enabled: the banner emoji in the header animates continuously\n"
            "and the theme SVG badge in the tab bar plays its built-in animation.\n"
            "When disabled: both are rendered statically, saving CPU/GPU resources."
        )
        banner_gl.addWidget(self._animated_banner_check, 0, 0, 1, 2)

        # Sub-container: hidden until banner animation is enabled (item 68)
        self._banner_anim_sub = QWidget()
        _ban_sub_gl = QGridLayout(self._banner_anim_sub)
        _ban_sub_gl.setContentsMargins(16, 0, 0, 0)
        _ban_sub_gl.setColumnStretch(1, 1)
        _ban_sub_gl.setHorizontalSpacing(10)
        _ban_sub_gl.setVerticalSpacing(6)
        self._banner_anim_combo = QComboBox()
        _BANNER_ANIM_OPTIONS = [
            ("spin",     "Spin – continuous 360° rotation"),
            ("bounce",   "Bounce – gentle vertical bobbing"),
            ("shake",    "Shake – rapid horizontal quiver"),
            ("pendulum", "Pendulum – swinging back and forth"),
            ("pulse",    "Pulse – rhythmic scale in/out"),
            ("float",    "Float – slow dreamy vertical drift"),
            ("flip",     "Flip – horizontal squeeze-and-flip"),
            ("orbit",    "Orbit – emoji circles around the centre point"),
            ("glitch",   "Glitch – jittery digital glitch stutter"),
            ("drip",     "Drip – slowly falls and shrinks then resets"),
        ]
        _BANNER_ANIM_TIPS = {
            "spin":     "The emoji rotates continuously like a gear (~6 s per full turn).",
            "bounce":   "The emoji bobs up and down with a smooth sine-wave motion.",
            "shake":    "The emoji vibrates rapidly side to side — great for aggressive themes.",
            "pendulum": "The emoji swings back and forth like a pendulum clock.",
            "pulse":    "The emoji slowly breathes in and out, pulsing between 75% and 125% size.",
            "float":    "The emoji drifts up and down lazily — perfect for calm or dreamy themes.",
            "flip":     "The emoji periodically squishes flat and pops back, like a coin flip.",
            "orbit":    "The emoji circles around its centre point in a small orbit — great for\n"
                        "themes like space, alien, or mermaid.",
            "glitch":   "The emoji stutters and jumps to random nearby positions each frame —\n"
                        "great for gore, alien, or cyber/glitch themes.",
            "drip":     "The emoji slowly falls downward while shrinking and wobbling, then\n"
                        "resets to the top — perfect for blood/water drip themes like Gore or Ocean.",
        }
        for key, label in _BANNER_ANIM_OPTIONS:
            self._banner_anim_combo.addItem(label, userData=key)
            idx = self._banner_anim_combo.count() - 1
            tip = _BANNER_ANIM_TIPS.get(key, "")
            if tip:
                self._banner_anim_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._banner_anim_combo.setToolTip(
            "Choose the animation style for the banner emoji when animation is enabled.\n"
            "Hidden while 'Use theme animation' is checked."
        )
        self._banner_anim_combo.setMinimumWidth(220)

        # Wrap animation label+combo in a container so we can hide it (item 4)
        self._banner_manual_widget = QWidget()
        _bman_row = QHBoxLayout(self._banner_manual_widget)
        _bman_row.setContentsMargins(0, 0, 0, 0)
        _bman_row.addWidget(QLabel("Banner animation:"))
        _bman_row.addWidget(self._banner_anim_combo, 1)
        _ban_sub_gl.addWidget(self._banner_manual_widget, 0, 0, 1, 2)

        self._banner_use_theme_anim_check = QCheckBox(
            "Use theme animation (each theme has its own style)"
        )
        self._banner_use_theme_anim_check.setToolTip(
            "When checked the animation style is chosen automatically by the active\n"
            "theme (e.g. Bat Cave uses bounce, Alien uses orbit, Goth uses pendulum).\n"
            "Uncheck to override with your own style from the dropdown above."
        )
        _ban_sub_gl.addWidget(self._banner_use_theme_anim_check, 1, 0, 1, 2)
        # Info label shown when use-theme is ON (item 4)
        self._banner_theme_info_lbl = QLabel("Theme animation: —")
        self._banner_theme_info_lbl.setStyleSheet("color: #aaa; font-size: 10px; margin-left: 4px;")
        self._banner_theme_info_lbl.setVisible(False)
        _ban_sub_gl.addWidget(self._banner_theme_info_lbl, 2, 0, 1, 2)
        # Add sub-container to banner_gl and wire visibility to enable checkbox
        banner_gl.addWidget(self._banner_anim_sub, 1, 0, 1, 2)
        def _update_banner_anim_sub(checked: bool = None):
            enabled = self._animated_banner_check.isChecked()
            use_theme = self._banner_use_theme_anim_check.isChecked()
            self._banner_anim_sub.setVisible(enabled)
            self._banner_manual_widget.setVisible(not use_theme)
            self._banner_theme_info_lbl.setVisible(use_theme)
            if use_theme and enabled:
                self._update_banner_theme_info()
        self._animated_banner_check.toggled.connect(_update_banner_anim_sub)
        self._banner_use_theme_anim_check.toggled.connect(lambda _: _update_banner_anim_sub())
        self._banner_anim_sub.setVisible(False)  # hidden until banner is enabled

        self._show_splash_check = QCheckBox(
            "Show themed splash screen on startup (off by default)"
        )
        self._show_splash_check.setToolTip(
            "When enabled: an animated themed splash screen is shown while the\n"
            "app loads on startup.  Disable to skip straight to the main window."
        )
        banner_gl.addWidget(self._show_splash_check, 2, 0, 1, 2)

        tv.addWidget(grp_banner)

        # Wrap the theme tab contents in a scroll area so all controls are always
        # reachable regardless of screen/window size.
        theme_scroll = QScrollArea()
        theme_scroll.setWidget(theme_tab)
        theme_scroll.setWidgetResizable(True)
        theme_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        theme_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tabs.addTab(theme_scroll, "🎨 Theme")

        # ================================================================
        # ---- General tab ----
        # ================================================================
        gen_tab = QWidget()
        gv = QVBoxLayout(gen_tab)
        gv.setContentsMargins(8, 8, 8, 8)
        gv.setSpacing(8)

        # ---- Tooltip Appearance GroupBox ----
        grp_misc = QGroupBox("Tooltip Appearance")
        misc_gl = QGridLayout(grp_misc)
        misc_gl.setColumnStretch(1, 1)
        misc_gl.setHorizontalSpacing(10)
        misc_gl.setVerticalSpacing(6)
        misc_gl.addWidget(QLabel("Tooltip Font Size (pt):"), 0, 0)
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._font_size_spin.setRange(8, 24)
        self._font_size_spin.setValue(10)
        self._font_size_spin.setMaximumWidth(80)
        self._font_size_spin.setToolTip(
            "Sets the tooltip font size in points.\n"
            "The UI Scale setting (General → UI Scaling) does NOT change tooltips;\n"
            "tooltips always use this exact size regardless of UI Scale."
        )
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

        gv.addWidget(grp_misc)

        # ---- UI Scaling GroupBox ----
        grp_scale = QGroupBox("UI Scaling")
        scale_gl = QGridLayout(grp_scale)
        scale_gl.setColumnStretch(1, 1)
        scale_gl.setHorizontalSpacing(10)
        scale_gl.setVerticalSpacing(6)

        scale_gl.addWidget(QLabel("UI Scale:"), 0, 0)
        self._ui_scale_combo = QComboBox()
        _SCALE_OPTIONS = [
            ("Compact  (85%)",    "Compact",    "Slightly smaller text and controls — useful on small or dense-DPI screens."),
            ("Normal  (100%)",    "Normal",     "Default size. Best for most standard 1080p and larger displays."),
            ("Large  (115%)",     "Large",      "Slightly larger — good for high-DPI displays or accessibility needs."),
            ("Extra Large  (130%)","Extra Large","Maximum size. Best for 4K monitors or low-vision users."),
        ]
        for label, _key, tip in _SCALE_OPTIONS:
            self._ui_scale_combo.addItem(label)
            idx = self._ui_scale_combo.count() - 1
            self._ui_scale_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._ui_scale_combo.setToolTip(
            "Scale the application's base font size for all UI elements.\n"
            "Changes apply immediately — no restart needed."
        )
        self._ui_scale_combo.setMaximumWidth(220)
        scale_gl.addWidget(self._ui_scale_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)

        scale_note = QLabel(
            "ℹ  Scales all text and controls. Tooltip size is controlled separately in Tooltip Appearance above."
        )
        scale_note.setWordWrap(True)
        scale_note.setStyleSheet("color: #888; font-size: 10px;")
        scale_gl.addWidget(scale_note, 1, 0, 1, 2)

        # Button / Control Height (items 6/7)
        scale_gl.addWidget(QLabel("Button Height:"), 2, 0)
        self._btn_height_combo = QComboBox()
        _BTN_HEIGHT_OPTIONS = [
            ("Compact  (22 px min)",   "Compact",     "Shorter buttons and controls — fits more on screen."),
            ("Normal   (26 px min)",   "Normal",      "Default button height. Comfortable for most displays."),
            ("Comfortable  (32 px min)", "Comfortable", "Taller buttons — easier to click, especially on touch screens."),
        ]
        for label, _key, tip in _BTN_HEIGHT_OPTIONS:
            self._btn_height_combo.addItem(label)
            idx = self._btn_height_combo.count() - 1
            self._btn_height_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._btn_height_combo.setToolTip(
            "Set the minimum height for buttons and combo boxes.\n"
            "Compact fits more controls on screen; Comfortable is easier to click."
        )
        self._btn_height_combo.setMaximumWidth(220)
        scale_gl.addWidget(self._btn_height_combo, 2, 1, Qt.AlignmentFlag.AlignLeft)

        # Widget Spacing (items 6/7)
        scale_gl.addWidget(QLabel("Widget Spacing:"), 3, 0)
        self._widget_spacing_combo = QComboBox()
        _SPACING_OPTIONS = [
            ("Tight  (2 px)",   "Tight",   "Minimal spacing between widgets — maximise screen use."),
            ("Normal  (4 px)",  "Normal",  "Default spacing. Balanced and readable."),
            ("Relaxed  (8 px)", "Relaxed", "More breathing room between controls — easier to read."),
        ]
        for label, _key, tip in _SPACING_OPTIONS:
            self._widget_spacing_combo.addItem(label)
            idx = self._widget_spacing_combo.count() - 1
            self._widget_spacing_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
        self._widget_spacing_combo.setToolTip(
            "Set the spacing between widgets in tool panels.\n"
            "Tight fits more controls; Relaxed is easier to read."
        )
        self._widget_spacing_combo.setMaximumWidth(220)
        scale_gl.addWidget(self._widget_spacing_combo, 3, 1, Qt.AlignmentFlag.AlignLeft)

        gv.addWidget(grp_scale)

        # ---- History GroupBox (items 8/9) ----
        grp_history = QGroupBox("📋  History")
        hist_gl = QGridLayout(grp_history)
        hist_gl.setColumnStretch(1, 1)
        hist_gl.setHorizontalSpacing(10)
        hist_gl.setVerticalSpacing(6)

        # Default max entries
        hist_gl.addWidget(QLabel("Default max entries:"), 0, 0)
        self._history_max_spin = QSpinBox()
        self._history_max_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._history_max_spin.setRange(10, 5000)
        self._history_max_spin.setValue(100)
        self._history_max_spin.setSingleStep(10)
        self._history_max_spin.setSuffix("  entries")
        self._history_max_spin.setMaximumWidth(130)
        self._history_max_spin.setToolTip(
            "Default maximum number of history entries kept per tool.\n"
            "Overridden by per-tool limits below when those are non-zero.\n"
            "Oldest entries are automatically removed when the limit is reached.\n"
            "Default: 100.  Maximum: 5000."
        )
        hist_gl.addWidget(self._history_max_spin, 0, 1, Qt.AlignmentFlag.AlignLeft)

        # Per-tool max entries
        per_lbl = QLabel("Per-tool limits (0 = use default above):")
        per_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        hist_gl.addWidget(per_lbl, 1, 0, 1, 2)

        hist_gl.addWidget(QLabel("  Converter:"), 2, 0)
        self._history_max_conv_spin = QSpinBox()
        self._history_max_conv_spin.setRange(0, 5000)
        self._history_max_conv_spin.setSpecialValueText("default")
        self._history_max_conv_spin.setValue(0)
        self._history_max_conv_spin.setSuffix("  entries")
        self._history_max_conv_spin.setMaximumWidth(130)
        self._history_max_conv_spin.setToolTip(
            "Max history entries for the File Converter (0 = use default above)."
        )
        hist_gl.addWidget(self._history_max_conv_spin, 2, 1, Qt.AlignmentFlag.AlignLeft)

        hist_gl.addWidget(QLabel("  Alpha & RGBA Adjuster:"), 3, 0)
        self._history_max_alpha_spin = QSpinBox()
        self._history_max_alpha_spin.setRange(0, 5000)
        self._history_max_alpha_spin.setSpecialValueText("default")
        self._history_max_alpha_spin.setValue(0)
        self._history_max_alpha_spin.setSuffix("  entries")
        self._history_max_alpha_spin.setMaximumWidth(130)
        self._history_max_alpha_spin.setToolTip(
            "Max history entries for the Alpha & RGBA Adjuster (0 = use default above)."
        )
        hist_gl.addWidget(self._history_max_alpha_spin, 3, 1, Qt.AlignmentFlag.AlignLeft)

        hist_gl.addWidget(QLabel("  Selective Alpha:"), 4, 0)
        self._history_max_sel_spin = QSpinBox()
        self._history_max_sel_spin.setRange(0, 5000)
        self._history_max_sel_spin.setSpecialValueText("default")
        self._history_max_sel_spin.setValue(0)
        self._history_max_sel_spin.setSuffix("  entries")
        self._history_max_sel_spin.setMaximumWidth(130)
        self._history_max_sel_spin.setToolTip(
            "Max history entries for the Selective Alpha Tool (0 = use default above)."
        )
        hist_gl.addWidget(self._history_max_sel_spin, 4, 1, Qt.AlignmentFlag.AlignLeft)

        # Track history checkboxes
        track_lbl = QLabel("Track history for:")
        track_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        hist_gl.addWidget(track_lbl, 5, 0, 1, 2)

        self._chk_track_converter = QCheckBox("Converter")
        self._chk_track_converter.setChecked(True)
        self._chk_track_converter.setToolTip(
            "When checked, File Converter batches are saved to history."
        )
        hist_gl.addWidget(self._chk_track_converter, 6, 0)

        self._chk_track_alpha = QCheckBox("Alpha & RGBA Adjuster")
        self._chk_track_alpha.setChecked(True)
        self._chk_track_alpha.setToolTip(
            "When checked, Alpha & RGBA Adjuster batches are saved to history."
        )
        hist_gl.addWidget(self._chk_track_alpha, 7, 0)

        self._chk_track_sel_alpha = QCheckBox("Selective Alpha")
        self._chk_track_sel_alpha.setChecked(True)
        self._chk_track_sel_alpha.setToolTip(
            "When checked, Selective Alpha Tool saves are recorded in history."
        )
        hist_gl.addWidget(self._chk_track_sel_alpha, 8, 0)

        hist_note = QLabel(
            "ℹ  Existing history entries are not trimmed immediately — limits "
            "only apply to new entries going forward."
        )
        hist_note.setWordWrap(True)
        hist_note.setStyleSheet("color: #888; font-size: 10px;")
        hist_gl.addWidget(hist_note, 9, 0, 1, 2)

        gv.addWidget(grp_history)

        # Wrap the general tab contents in a scroll area so checkboxes
        # are always reachable regardless of screen/window size.
        gen_scroll = QScrollArea()
        gen_scroll.setWidget(gen_tab)
        gen_scroll.setWidgetResizable(True)
        gen_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        gen_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tabs.addTab(gen_scroll, "⚙ General")

        # ================================================================
        # ---- Sound tab ----
        # ================================================================
        sound_tab = QWidget()
        sv = QVBoxLayout(sound_tab)
        sv.setContentsMargins(8, 8, 8, 8)
        sv.setSpacing(8)
        sv.addWidget(self._grp_sound)
        sv.addStretch(1)
        sound_scroll = QScrollArea()
        sound_scroll.setWidget(sound_tab)
        sound_scroll.setWidgetResizable(True)
        sound_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        sound_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tabs.addTab(sound_scroll, "🔊 Sound")

        layout.addWidget(tabs, 1)

        # ---- Dialog button: just "Close" (settings already saved live) ----
        btn_row = QHBoxLayout()
        self._btn_reset = QPushButton("⚠  Reset All Settings…")
        self._btn_reset.setObjectName("resetBtn")
        self._btn_reset.setToolTip(
            "⚠ DESTRUCTIVE: Reset ALL settings, unlock flags, and history to factory defaults.\n"
            "This cannot be undone. Useful for testing easter eggs and unlock events."
        )
        self._btn_reset.setStyleSheet(
            "QPushButton#resetBtn {"
            "  color: #ff8a80;"
            "  border: 1px solid #c62828;"
            "  border-radius: 4px;"
            "  padding: 4px 8px;"
            "}"
            "QPushButton#resetBtn:hover { background: rgba(198,40,40,60); }"
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
        # Preset combo: debounced live apply (120 ms) so quickly scrolling
        # through themes doesn't apply every intermediate theme and lag the UI
        self._theme_preset_combo.currentTextChanged.connect(
            lambda _: self._theme_debounce.start()
        )
        self._theme_search.textChanged.connect(self._on_theme_search_changed)
        self._btn_save_theme.clicked.connect(self._save_custom_theme)
        self._btn_delete_theme.clicked.connect(self._delete_custom_theme)
        self._btn_export_theme.clicked.connect(self._export_theme)
        self._btn_import_theme.clicked.connect(self._import_theme)
        self._btn_close.clicked.connect(self.accept)
        self._btn_reset.clicked.connect(self._reset_all_settings)
        self._btn_reset_unlocks.clicked.connect(self._reset_unlocks_only)
        self._effect_combo.currentIndexChanged.connect(self._on_effect_changed_live)
        self._btn_emoji_add.clicked.connect(self._add_emoji)
        self._btn_emoji_clear.clicked.connect(self._clear_emoji)
        # All controls save+emit live
        self._sound_check.toggled.connect(self._on_sound_changed)
        self._use_theme_sound_check.toggled.connect(self._on_use_theme_sound_changed)
        self._use_theme_sound_check.toggled.connect(self._on_use_theme_sound_toggled)
        self._sound_theme_change_chk.toggled.connect(self._on_sound_theme_change_changed)
        self._sound_tab_switch_chk.toggled.connect(self._on_sound_tab_switch_changed)
        self._sound_drag_enter_chk.toggled.connect(self._on_sound_drag_enter_changed)
        self._sound_profile_combo.currentIndexChanged.connect(self._on_sound_profile_changed)
        self._sound_volume_slider.valueChanged.connect(self._on_sound_volume_changed)
        self._trail_check.toggled.connect(self._on_trail_changed)
        self._trail_color_btn.color_changed.connect(self._on_trail_color_changed)
        self._use_theme_trail_check.toggled.connect(self._on_use_theme_trail_changed)
        self._trail_style_combo.currentIndexChanged.connect(self._on_trail_style_changed)
        self._cursor_combo.currentTextChanged.connect(self._on_cursor_changed)
        self._cursor_enable_check.toggled.connect(self._on_cursor_changed)
        self._use_theme_cursor_check.toggled.connect(self._on_cursor_changed)
        self._cursor_anim_check.toggled.connect(self._on_cursor_anim_changed)
        self._font_size_spin.valueChanged.connect(self._on_font_size_changed)
        self._ui_scale_combo.currentTextChanged.connect(self._on_ui_scale_changed)
        self._btn_height_combo.currentTextChanged.connect(self._on_btn_height_changed)
        self._widget_spacing_combo.currentTextChanged.connect(self._on_widget_spacing_changed)
        self._history_max_spin.valueChanged.connect(self._on_history_max_changed)
        self._history_max_conv_spin.valueChanged.connect(
            lambda v: self._settings.set("history_max_entries_converter", v)
        )
        self._history_max_alpha_spin.valueChanged.connect(
            lambda v: self._settings.set("history_max_entries_alpha", v)
        )
        self._history_max_sel_spin.valueChanged.connect(
            lambda v: self._settings.set("history_max_entries_selective_alpha", v)
        )
        self._chk_track_converter.toggled.connect(
            lambda v: self._settings.set("history_track_converter", v)
        )
        self._chk_track_alpha.toggled.connect(
            lambda v: self._settings.set("history_track_alpha", v)
        )
        self._chk_track_sel_alpha.toggled.connect(
            lambda v: self._settings.set("history_track_selective_alpha", v)
        )
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
        self._bg_drip_check.toggled.connect(self._on_bg_drip_changed)
        self._use_theme_drip_check.toggled.connect(self._on_bg_drip_changed)
        self._bg_drip_combo.currentIndexChanged.connect(self._on_bg_drip_changed)
        self._bg_flock_check.toggled.connect(self._on_bg_flock_changed)
        self._use_theme_flock_check.toggled.connect(self._on_bg_flock_changed)
        self._bg_flock_combo.currentIndexChanged.connect(self._on_bg_flock_changed)
        self._bg_ambient_check.toggled.connect(self._on_bg_ambient_changed)
        self._use_theme_ambient_check.toggled.connect(self._on_bg_ambient_changed)
        self._bg_ambient_combo.currentIndexChanged.connect(self._on_bg_ambient_changed)
        self._notif_overlay_check.toggled.connect(self._on_notif_overlay_changed)
        self._use_theme_notif_check.toggled.connect(self._on_notif_overlay_changed)
        self._custom_bg_check.toggled.connect(self._on_custom_bg_changed)
        self._use_theme_bg_check.toggled.connect(self._on_custom_bg_changed)
        self._custom_bg_path_edit.textChanged.connect(self._on_custom_bg_changed)

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
            self._theme_preset_combo.addItem("— Custom (unsaved) —")
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
            self._use_theme_sound_check, self._trail_check,
            self._trail_color_btn, self._trail_style_combo, self._use_theme_trail_check,
            self._cursor_enable_check, self._cursor_combo, self._use_theme_cursor_check,
            self._cursor_anim_check, self._font_size_spin,
            self._click_effects_theme_check,
            self._use_theme_effect_check, self._tooltip_mode_combo, self._tooltip_style_combo,
            self._animated_banner_check, self._banner_anim_combo,
            self._banner_use_theme_anim_check, self._show_splash_check,
            self._button_anim_check, self._button_anim_style_combo,
            self._use_theme_button_anim_check,
            self._sound_theme_change_chk, self._sound_tab_switch_chk,
            self._sound_drag_enter_chk,
            # Sliders must also be signal-blocked during load; their valueChanged
            # is connected to _on_trail_*_changed which emits settings_changed.
            self._trail_length_slider, self._trail_fade_slider, self._trail_intensity_slider,
            self._sound_volume_slider,
            # Background effects controls – their handlers write to QSettings and
            # emit settings_changed, so they must be blocked during initial load.
            self._bg_drip_check, self._use_theme_drip_check, self._bg_drip_combo,
            self._bg_flock_check, self._use_theme_flock_check, self._bg_flock_combo,
            self._bg_ambient_check, self._use_theme_ambient_check, self._bg_ambient_combo,
            # Sound profile combo also saves settings on currentIndexChanged.
            self._sound_profile_combo,
            # UI density combos
            self._btn_height_combo, self._widget_spacing_combo,
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
        use_theme_sound = self._settings.get("use_theme_sound", False)
        self._use_theme_sound_check.setChecked(use_theme_sound)
        sound_enabled = self._settings.get("sound_enabled", False)
        # Show/hide sub-controls based on enable state (item 68)
        self._sound_sub_widget.setVisible(sound_enabled)
        vol = int(self._settings.get("sound_volume", 50))
        self._sound_volume_slider.setValue(max(0, min(100, vol)))
        self._sound_volume_lbl.setText(f"{self._sound_volume_slider.value()}%")
        # Load sound profile combo
        saved_profile = str(self._settings.get("sound_manual_profile", "soft"))
        _profile_idx = self._sound_profile_combo.findData(saved_profile)
        if _profile_idx >= 0:
            self._sound_profile_combo.setCurrentIndex(_profile_idx)
        # Item 1: combo stays visible in both modes; just toggle enabled state.
        if use_theme_sound:
            self._update_sound_theme_info()
            self._sound_profile_combo.setEnabled(False)
        else:
            self._sound_theme_info_lbl.setVisible(False)
            self._sound_profile_combo.setEnabled(True)
        # Load sound event toggles
        self._sound_theme_change_chk.setChecked(
            bool(self._settings.get("sound_theme_change", False))
        )
        self._sound_tab_switch_chk.setChecked(
            bool(self._settings.get("sound_tab_switch", False))
        )
        self._sound_drag_enter_chk.setChecked(
            bool(self._settings.get("sound_drag_enter", False))
        )
        trail_enabled = self._settings.get("trail_enabled", False)
        self._trail_check.setChecked(trail_enabled)
        self._trail_color_btn.set_color(self._settings.get("trail_color", "#e94560"))
        use_theme_trail = self._settings.get("use_theme_trail", False)
        self._use_theme_trail_check.setChecked(use_theme_trail)
        self._trail_sub.setVisible(trail_enabled)
        self._trail_manual_widget.setVisible(not use_theme_trail)
        self._trail_theme_info_lbl.setVisible(use_theme_trail)
        if use_theme_trail:
            self._update_trail_theme_info()
        # Load persisted trail style into combo (or theme trail if use-theme is on)
        _TRAIL_STYLE_MAP = {
            "dots": 0, "ribbon": 1, "noodle": 2, "comet": 3,
            "fairy": 4, "wave": 5, "sparkle": 6, "rainbow": 7,
            "distortion": 8, "fire": 9, "lightning": 10,
            "plasma": 11, "sakura": 12, "smoke": 13,
        }
        if use_theme_trail:
            theme_trail = self._settings.get_theme().get("_trail", "dots")
            self._trail_style_combo.setCurrentIndex(_TRAIL_STYLE_MAP.get(theme_trail, 0))
        else:
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
        cursor_enabled = self._settings.get("cursor_enabled", False)
        self._cursor_enable_check.setChecked(cursor_enabled)
        self._cursor_sub.setVisible(cursor_enabled)
        cursor_val = self._settings.get("cursor", "Default")
        idx = self._cursor_combo.findText(cursor_val)
        self._cursor_combo.setCurrentIndex(max(idx, 0))
        use_theme_cur = self._settings.get("use_theme_cursor", False)
        self._use_theme_cursor_check.setChecked(use_theme_cur)
        # item 1/4: keep manual widget visible — just disable combo when use-theme is on
        self._cursor_manual_widget.setVisible(True)
        self._cursor_combo.setEnabled(cursor_enabled and not use_theme_cur)
        self._cursor_theme_info_lbl.setVisible(use_theme_cur)
        if use_theme_cur:
            self._update_cursor_theme_info()
        self._cursor_anim_check.setChecked(bool(self._settings.get("cursor_anim_enabled", True)))
        self._font_size_spin.setValue(self._settings.get("font_size", 10))
        # UI Scale
        scale_val = self._settings.get("ui_scale", "Normal")
        _scale_map = {"Compact": 0, "Normal": 1, "Large": 2, "Extra Large": 3}
        self._ui_scale_combo.setCurrentIndex(_scale_map.get(scale_val, 1))
        # Button height
        btn_h_val = self._settings.get("btn_height", "Normal")
        _btn_h_map = {"Compact": 0, "Normal": 1, "Comfortable": 2}
        self._btn_height_combo.setCurrentIndex(_btn_h_map.get(btn_h_val, 1))
        # Widget spacing
        ws_val = self._settings.get("widget_spacing", "Normal")
        _ws_map = {"Tight": 0, "Normal": 1, "Relaxed": 2}
        self._widget_spacing_combo.setCurrentIndex(_ws_map.get(ws_val, 1))
        # History max entries
        self._history_max_spin.setValue(self._settings.get("history_max_entries", 100))
        self._history_max_conv_spin.setValue(
            int(self._settings.get("history_max_entries_converter", 0))
        )
        self._history_max_alpha_spin.setValue(
            int(self._settings.get("history_max_entries_alpha", 0))
        )
        self._history_max_sel_spin.setValue(
            int(self._settings.get("history_max_entries_selective_alpha", 0))
        )
        self._chk_track_converter.setChecked(
            bool(self._settings.get("history_track_converter", True))
        )
        self._chk_track_alpha.setChecked(
            bool(self._settings.get("history_track_alpha", True))
        )
        self._chk_track_sel_alpha.setChecked(
            bool(self._settings.get("history_track_selective_alpha", True))
        )
        # Sync Theme-tab on/off + use-theme checkboxes with persisted values
        click_effects_enabled = self._settings.get("click_effects_enabled", False)
        self._click_effects_theme_check.setChecked(click_effects_enabled)
        use_theme_effect = self._settings.get("use_theme_effect", False)
        self._use_theme_effect_check.setChecked(use_theme_effect)
        self._click_effect_sub.setVisible(click_effects_enabled)
        if click_effects_enabled:
            self._effect_inner_widget.setVisible(not use_theme_effect)
            self._effect_theme_info_lbl.setVisible(use_theme_effect)
            if use_theme_effect:
                self._update_effect_theme_info()
        mode_val = self._settings.get("tooltip_mode") or "No Filter 🤬"
        idx_m = self._tooltip_mode_combo.findText(mode_val)
        self._tooltip_mode_combo.setCurrentIndex(max(idx_m, 0))
        style_val = self._settings.get("tooltip_style", "Auto (follow theme)")
        idx_s = self._tooltip_style_combo.findText(style_val)
        self._tooltip_style_combo.setCurrentIndex(max(idx_s, 0))
        banner_enabled = self._settings.get("animated_banner_enabled", False)
        self._animated_banner_check.setChecked(banner_enabled)
        # Load banner animation style combo (show theme anim if "use theme" is on)
        _BANNER_ANIM_IDX_MAP = {
            "spin": 0, "bounce": 1, "shake": 2, "pendulum": 3,
            "pulse": 4, "float": 5, "flip": 6, "orbit": 7, "glitch": 8, "drip": 9,
        }
        banner_use_theme = self._settings.get("banner_use_theme_anim", True)
        if banner_use_theme:
            theme_anim = self._settings.get_theme().get("_banner_anim", "spin")
            self._banner_anim_combo.setCurrentIndex(_BANNER_ANIM_IDX_MAP.get(theme_anim, 0))
        else:
            saved_banner_anim = self._settings.get("banner_anim_style", "spin")
            self._banner_anim_combo.setCurrentIndex(
                _BANNER_ANIM_IDX_MAP.get(saved_banner_anim, 0)
            )
        self._banner_use_theme_anim_check.setChecked(banner_use_theme)
        self._banner_anim_sub.setVisible(banner_enabled)
        self._banner_manual_widget.setVisible(not banner_use_theme)
        self._banner_theme_info_lbl.setVisible(banner_use_theme)
        if banner_use_theme and banner_enabled:
            self._update_banner_theme_info()
        self._show_splash_check.setChecked(
            self._settings.get("show_splash_screen", False)
        )
        # Load button animation settings
        btn_anim_enabled = self._settings.get("button_anim_enabled")
        self._button_anim_check.setChecked(btn_anim_enabled)
        use_theme_btn_anim = self._settings.get("use_theme_button_anim", True)
        self._use_theme_button_anim_check.setChecked(use_theme_btn_anim)
        _BUTTON_ANIM_IDX_MAP = {
            "press": 0, "fall": 1, "bounce": 2, "shake": 3, "shatter": 4,
            "vanish": 5, "explode": 6,
        }
        if use_theme_btn_anim:
            theme_btn_anim = self._settings.get_theme().get("_button_anim", "press")
            self._button_anim_style_combo.setCurrentIndex(
                _BUTTON_ANIM_IDX_MAP.get(theme_btn_anim, 0)
            )
        else:
            saved_btn_anim = self._settings.get("button_anim_style", "press")
            self._button_anim_style_combo.setCurrentIndex(
                _BUTTON_ANIM_IDX_MAP.get(saved_btn_anim, 0)
            )
        self._btn_anim_sub.setVisible(btn_anim_enabled)
        self._btn_anim_style_widget.setVisible(not use_theme_btn_anim)
        self._btn_anim_theme_info_lbl.setVisible(use_theme_btn_anim)
        if use_theme_btn_anim and btn_anim_enabled:
            self._update_btn_anim_theme_info()

        # Load background drip settings
        bg_drip_enabled = self._settings.get("bg_drip_enabled", False)
        self._bg_drip_check.setChecked(bg_drip_enabled)
        use_theme_drip = self._settings.get("use_theme_drip", False)
        self._use_theme_drip_check.setChecked(use_theme_drip)
        # item 1/4: combo stays visible — disabled + shows themed value when use-theme is on
        if use_theme_drip:
            eff = self._settings.get_theme().get("_effect", "default")
            theme_name = self._settings.get_theme().get("name", "")
            if eff in ("gore", "shark"):
                theme_drip = "blood"
                drip_label = f"🩸 Auto-set by '{theme_name}' theme  →  Blood Drip"
            elif eff in ("ocean", "ripple", "mermaid"):
                theme_drip = "water"
                drip_label = f"💧 Auto-set by '{theme_name}' theme  →  Water Drip"
            else:
                theme_drip = None
                drip_label = f"🚫 '{theme_name}' theme has no drip effect"
            self._bg_drip_theme_lbl.setText(drip_label)
            self._bg_drip_theme_lbl.setVisible(True)
            if theme_drip:
                for i in range(self._bg_drip_combo.count()):
                    if self._bg_drip_combo.itemData(i) == theme_drip:
                        self._bg_drip_combo.setCurrentIndex(i)
                        break
        else:
            manual_drip = self._settings.get("bg_drip_type", "blood")
            self._bg_drip_theme_lbl.setVisible(False)
            for i in range(self._bg_drip_combo.count()):
                if self._bg_drip_combo.itemData(i) == manual_drip:
                    self._bg_drip_combo.setCurrentIndex(i)
                    break
        self._bg_drip_sub.setVisible(bg_drip_enabled)
        self._bg_drip_combo.setEnabled(bg_drip_enabled and not use_theme_drip)

        # Load background flock settings
        bg_flock_enabled = self._settings.get("bg_flock_enabled", False)
        self._bg_flock_check.setChecked(bg_flock_enabled)
        use_theme_flock = self._settings.get("use_theme_flock", False)
        self._use_theme_flock_check.setChecked(use_theme_flock)
        bg_flock_style = self._settings.get("bg_flock_style", "bats")
        for i in range(self._bg_flock_combo.count()):
            if self._bg_flock_combo.itemData(i) == bg_flock_style:
                self._bg_flock_combo.setCurrentIndex(i)
                break
        self._bg_flock_sub.setVisible(bg_flock_enabled)
        # item 1/4: inner widget stays visible — disable combo when use-theme is on
        self._bg_flock_theme_lbl.setVisible(use_theme_flock)
        self._bg_flock_inner_widget.setVisible(True)
        self._bg_flock_combo.setEnabled(bg_flock_enabled and not use_theme_flock)
        if use_theme_flock:
            # Initialise info label text from the current theme
            _FLOCK_LABELS = {
                "bats": "🦇 Bats", "fairies": "🧚 Fairies", "fish": "🐟 Fish",
                "butterflies": "🦋 Butterflies", "birds": "🐦 Birds",
                "stars": "⭐ Stars", "petals": "🌸 Petals", "sharks": "🦈 Sharks",
            }
            _th = self._settings.get_theme()
            _tf = _th.get("_flock")
            if _tf:
                self._bg_flock_theme_lbl.setText(
                    f"Using theme flock: {_FLOCK_LABELS.get(_tf, _tf)}  "
                    f"(set by '{_th.get('name', '')}' theme)"
                )
            else:
                self._bg_flock_theme_lbl.setText(
                    f"🚫  '{_th.get('name', '')}' theme has no flock"
                )

        # Load background ambient settings
        bg_ambient_enabled = self._settings.get("bg_ambient_enabled", False)
        self._bg_ambient_check.setChecked(bg_ambient_enabled)
        bg_ambient_type = self._settings.get("bg_ambient_type", "none")
        for i in range(self._bg_ambient_combo.count()):
            if self._bg_ambient_combo.itemData(i) == bg_ambient_type:
                self._bg_ambient_combo.setCurrentIndex(i)
                break
        use_theme_ambient = self._settings.get("use_theme_ambient", False)
        self._use_theme_ambient_check.setChecked(use_theme_ambient)
        self._bg_ambient_sub.setVisible(bg_ambient_enabled)
        # item 1/4: inner widget stays visible — disable combo when use-theme is on
        self._bg_ambient_theme_lbl.setVisible(use_theme_ambient)
        self._bg_ambient_inner_widget.setVisible(True)
        self._bg_ambient_combo.setEnabled(bg_ambient_enabled and not use_theme_ambient)
        if use_theme_ambient:
            # Initialise info label text from the current theme
            _AMBIENT_LABELS = {
                "snow": "❄️ Snow Drift", "ember": "🔥 Ember Drift",
                "sakura": "🌸 Sakura Petals", "stars": "✨ Shooting Stars",
                "bubbles": "🫧 Rising Bubbles", "neon": "🌈 Neon Flicker",
                "ghost": "👻 Ghost Wisps", "confetti": "🎊 Confetti Fall",
                "firefly": "🪲 Fireflies", "matrix": "💻 Matrix Rain",
                "leaves": "🍂 Autumn Leaves", "rainbow": "🌈 Rainbow Sparkle",
                "bamboo": "🎋 Bamboo Leaves",
            }
            try:
                from .theme_engine import THEME_AMBIENT_MAP
                _th = self._settings.get_theme()
                _ak = THEME_AMBIENT_MAP.get(_th.get("name", ""))
                if _ak:
                    self._bg_ambient_theme_lbl.setText(
                        f"Using theme ambient: {_AMBIENT_LABELS.get(_ak, _ak)}  "
                        f"(set by '{_th.get('name', '')}' theme)"
                    )
                else:
                    self._bg_ambient_theme_lbl.setText(
                        f"🚫  '{_th.get('name', '')}' theme has no ambient"
                    )
            except Exception:
                pass

        for c in controls:
            c.blockSignals(False)

        # Load notification overlay settings (item 66)
        notif_enabled = bool(self._settings.get("notif_overlay_enabled", True))
        self._notif_overlay_check.setChecked(notif_enabled)
        use_theme_notif = bool(self._settings.get("use_theme_notif", True))
        self._use_theme_notif_check.setChecked(use_theme_notif)
        self._notif_overlay_sub.setVisible(notif_enabled)
        self._notif_theme_info_lbl.setVisible(use_theme_notif)

        # Load custom background settings (item 81)
        custom_bg_enabled = bool(self._settings.get("custom_bg_enabled", False))
        self._custom_bg_check.setChecked(custom_bg_enabled)
        use_theme_bg = bool(self._settings.get("use_theme_bg", True))
        self._use_theme_bg_check.setChecked(use_theme_bg)
        self._custom_bg_path_edit.setText(
            str(self._settings.get("custom_bg_path", ""))
        )
        self._custom_bg_sub.setVisible(custom_bg_enabled)
        self._custom_bg_file_row.setVisible(not use_theme_bg)

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr) -> None:
        """Register dialog widgets with the TooltipManager for cycling tips."""
        mgr.register(self._theme_search, "theme_search")
        mgr.register(self._theme_preset_combo, "theme_combo")
        mgr.register(self._effect_combo, "effect_combo")
        mgr.register(self._emoji_combo, "custom_emoji")
        mgr.register(self._btn_emoji_add, "emoji_add_btn")
        mgr.register(self._btn_emoji_clear, "emoji_clear_btn")
        mgr.register(self._bg_drip_check, "bg_drip_check")
        mgr.register(self._use_theme_drip_check, "use_theme_drip")
        mgr.register(self._bg_drip_combo, "bg_drip_combo")
        mgr.register(self._bg_flock_check, "bg_flock_check")
        mgr.register(self._bg_flock_combo, "bg_flock_combo")
        mgr.register(self._bg_ambient_check, "bg_ambient_check")
        mgr.register(self._use_theme_ambient_check, "use_theme_ambient")
        mgr.register(self._bg_ambient_combo, "bg_ambient_combo")
        mgr.register(self._tooltip_mode_combo, "tooltip_mode_combo")
        mgr.register(self._tooltip_style_combo, "tooltip_style_combo")
        mgr.register(self._sound_check, "sound_check")
        mgr.register(self._use_theme_sound_check, "use_theme_sound")
        mgr.register(self._sound_profile_combo, "sound_profile_combo")
        mgr.register(self._sound_volume_slider, "sound_volume_slider")
        mgr.register(self._sound_theme_change_chk, "sound_theme_change_check")
        mgr.register(self._sound_tab_switch_chk, "sound_tab_switch_check")
        mgr.register(self._sound_drag_enter_chk, "sound_drag_enter_check")
        mgr.register(self._btn_mute_all_events, "sound_mute_all_btn")
        mgr.register(self._trail_check, "trail_check")
        mgr.register(self._trail_color_btn, "trail_color")
        mgr.register(self._trail_style_combo, "trail_style")
        mgr.register(self._use_theme_trail_check, "use_theme_trail")
        mgr.register(self._trail_length_slider, "trail_length_slider")
        mgr.register(self._trail_fade_slider, "trail_fade_slider")
        mgr.register(self._trail_intensity_slider, "trail_intensity_slider")
        mgr.register(self._cursor_combo, "cursor_combo")
        mgr.register(self._use_theme_cursor_check, "use_theme_cursor")
        mgr.register(self._cursor_anim_check, "cursor_anim")
        mgr.register(self._notif_overlay_check, "notif_overlay_check")
        mgr.register(self._use_theme_notif_check, "use_theme_notif_check")
        mgr.register(self._font_size_spin, "font_size")
        mgr.register(self._ui_scale_combo, "ui_scale_combo")
        mgr.register(self._history_max_spin, "history_max_spin")
        mgr.register(self._history_max_conv_spin, "history_max_conv_spin")
        mgr.register(self._history_max_alpha_spin, "history_max_alpha_spin")
        mgr.register(self._history_max_sel_spin, "history_max_sel_spin")
        mgr.register(self._chk_track_converter, "history_track_converter")
        mgr.register(self._chk_track_alpha, "history_track_alpha")
        mgr.register(self._chk_track_sel_alpha, "history_track_sel_alpha")
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
        mgr.register(self._btn_reset, "reset_all_settings")
        mgr.register(self._btn_reset_unlocks, "reset_unlocks_btn")
        # Settings dialog own tab bar (Theme / General / Sound tabs)
        mgr.register_tab_bar(
            self._settings_tabs.tabBar(),
            ["settings_theme_tab", "settings_general_tab", "settings_sound_tab"],
        )
        # Register all color swatch buttons with the same generic key
        for btn in self._color_buttons.values():
            mgr.register(btn, "theme_color_btn")

    # ------------------------------------------------------------------
    # Window positioning
    # ------------------------------------------------------------------

    def showEvent(self, event):  # noqa: N802
        """Center the dialog on the screen it will appear on and clamp its
        size to the available geometry so no part is hidden off-screen."""
        super().showEvent(event)
        # Determine which screen we're on (use parent's screen, fall back to primary)
        screen = (self.parent().screen() if self.parent() is not None else None)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        ag = screen.availableGeometry()
        # Relax the minimum size so the dialog can shrink to fit the screen.
        # Without this, resize() cannot reduce below setMinimumSize(), leaving
        # the bottom/right edge of the dialog off-screen on small monitors.
        # Subtract 4 px to leave room for window decorations/shadows so the
        # resize lands cleanly within the available area.
        safe_w = max(320, ag.width() - 4)
        safe_h = max(240, ag.height() - 4)
        if self.minimumWidth() >= safe_w or self.minimumHeight() >= safe_h:
            self.setMinimumSize(
                min(self.minimumWidth(), safe_w),
                min(self.minimumHeight(), safe_h),
            )
        # Ensure the dialog is no larger than the available area
        w = min(self.width(), ag.width())
        h = min(self.height(), ag.height())
        if w != self.width() or h != self.height():
            self.resize(w, h)
        # Center on the available geometry
        x = ag.x() + max(0, (ag.width() - w) // 2)
        y = ag.y() + max(0, (ag.height() - h) // 2)
        self.move(x, y)

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
                return  # "— Custom (unsaved) —" or separator line
        # Update color swatches to reflect the new preset
        for key, btn in self._color_buttons.items():
            btn.set_color(self._theme.get(key, "#888888"))
        self._set_effect_combo(self._theme.get("_effect", "default"))
        # Update "use theme" combos so they preview the new theme's values immediately.
        self._sync_use_theme_combos()
        # Persist and broadcast immediately
        self._settings.set_theme(self._theme)
        # Emit first-change signal before theme_changed so unlock fires once.
        if not self._settings.get("theme_changed_once", False):
            self._settings.set("theme_changed_once", True)
            self.theme_changed.emit(self._theme)
            self.first_theme_changed.emit()
        else:
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
        self._sync_use_theme_combos()
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

    def _sync_use_theme_combos(self) -> None:
        """Refresh all 'use theme' combo selections to reflect the current theme.

        Called whenever the active theme changes (preset selection, import,
        custom-colour save) so that every 'Use theme X' combo immediately shows
        what value the NEW theme will provide, rather than the previous one.
        Only combos whose matching 'use theme' checkbox is currently checked
        are updated — unchecked combos retain the user's manual selection.
        """
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "")
        _TRAIL_STYLE_MAP = {
            "dots": 0, "ribbon": 1, "noodle": 2, "comet": 3,
            "fairy": 4, "wave": 5, "sparkle": 6, "rainbow": 7,
            "distortion": 8, "fire": 9, "lightning": 10,
            "plasma": 11, "sakura": 12, "smoke": 13,
        }
        _BANNER_ANIM_IDX_MAP = {
            "spin": 0, "bounce": 1, "shake": 2, "pendulum": 3,
            "pulse": 4, "float": 5, "flip": 6, "orbit": 7, "glitch": 8, "drip": 9,
        }
        _BUTTON_ANIM_IDX_MAP = {
            "press": 0, "fall": 1, "bounce": 2, "shake": 3, "shatter": 4,
            "vanish": 5, "explode": 6,
        }
        # cascading settings_changed emissions for each individual combo change.
        _combos = [
            self._trail_style_combo,
            self._banner_anim_combo,
            self._button_anim_style_combo,
            self._bg_drip_combo,
        ]
        for c in _combos:
            c.blockSignals(True)
        try:
            # Trail combo
            if self._use_theme_trail_check.isChecked():
                trail_key = theme.get("_trail", "dots")
                self._trail_style_combo.setCurrentIndex(_TRAIL_STYLE_MAP.get(trail_key, 0))
            # Banner animation combo
            if self._banner_use_theme_anim_check.isChecked():
                anim_key = theme.get("_banner_anim", "spin")
                self._banner_anim_combo.setCurrentIndex(_BANNER_ANIM_IDX_MAP.get(anim_key, 0))
            # Button animation combo
            if self._use_theme_button_anim_check.isChecked():
                btn_key = theme.get("_button_anim", "press")
                self._button_anim_style_combo.setCurrentIndex(
                    _BUTTON_ANIM_IDX_MAP.get(btn_key, 0)
                )
            # Click effect combo (via existing helper)
            if self._use_theme_effect_check.isChecked():
                from .theme_engine import THEME_EFFECTS
                effect_key = THEME_EFFECTS.get(theme_name, theme.get("_effect", "default"))
                self._set_effect_combo(effect_key)
            # Bg drip combo / info label
            if self._use_theme_drip_check.isChecked():
                eff = theme.get("_effect", "default")
                if eff in ("gore", "shark"):
                    drip_key = "blood"
                    drip_label = "🩸 Blood Drip  (set by theme)"
                elif eff in ("ocean", "ripple", "mermaid"):
                    drip_key = "water"
                    drip_label = "💧 Water Drip  (set by theme)"
                else:
                    # This theme has no defined drip — show info message (item 4).
                    drip_key = None
                    drip_label = f"🚫  This theme has no drip effect."
                # Update info label that replaces the combo when use-theme is on.
                if hasattr(self, "_bg_drip_theme_lbl"):
                    self._bg_drip_theme_lbl.setText(drip_label)
                # Select the matching combo item (used as fallback if label hidden)
                if drip_key:
                    for i in range(self._bg_drip_combo.count()):
                        if self._bg_drip_combo.itemData(i) == drip_key:
                            self._bg_drip_combo.setCurrentIndex(i)
                            break
            # Flock info label — update to show which flock (or none) the theme uses (item 4)
            if self._use_theme_flock_check.isChecked():
                _FLOCK_LABELS = {
                    "bats": "🦇 Bats", "fairies": "🧚 Fairies", "fish": "🐟 Fish",
                    "butterflies": "🦋 Butterflies", "birds": "🐦 Birds",
                    "stars": "⭐ Stars", "petals": "🌸 Petals", "sharks": "🦈 Sharks",
                }
                _FLOCK_IDX = {
                    "bats": 0, "fairies": 1, "fish": 2, "butterflies": 3,
                    "birds": 4, "stars": 5, "petals": 6, "sharks": 7,
                }
                theme_flock = theme.get("_flock")
                if theme_flock:
                    self._bg_flock_combo.blockSignals(True)
                    self._bg_flock_combo.setCurrentIndex(_FLOCK_IDX.get(theme_flock, 0))
                    self._bg_flock_combo.blockSignals(False)
                    flock_label = _FLOCK_LABELS.get(theme_flock, theme_flock)
                    self._bg_flock_theme_lbl.setText(
                        f"Using theme flock: {flock_label}  (set by '{theme_name}' theme)"
                    )
                else:
                    # Theme has no flock — show info label (item 4)
                    self._bg_flock_theme_lbl.setText(
                        f"🚫  '{theme_name}' theme has no flock"
                    )
            # Ambient info label — update to show which ambient (or none) the theme uses (item 4)
            if self._use_theme_ambient_check.isChecked():
                from .theme_engine import THEME_AMBIENT_MAP
                _AMBIENT_LABELS = {
                    "snow": "❄️ Snow Drift", "ember": "🔥 Ember Drift",
                    "sakura": "🌸 Sakura Petals", "stars": "✨ Shooting Stars",
                    "bubbles": "🫧 Rising Bubbles", "neon": "🌈 Neon Flicker",
                    "ghost": "👻 Ghost Wisps", "confetti": "🎊 Confetti Fall",
                    "firefly": "🪲 Fireflies", "matrix": "💻 Matrix Rain",
                    "leaves": "🍂 Autumn Leaves", "rainbow": "🌈 Rainbow Sparkle",
                    "bamboo": "🎋 Bamboo Leaves",
                }
                _AMBIENT_IDX = {
                    "snow": 0, "ember": 1, "sakura": 2, "stars": 3,
                    "bubbles": 4, "neon": 5, "ghost": 6, "confetti": 7,
                    "firefly": 8, "matrix": 9, "leaves": 10, "rainbow": 11,
                    "bamboo": 12,
                }
                ambient_key = THEME_AMBIENT_MAP.get(theme_name)
                if ambient_key:
                    self._bg_ambient_combo.blockSignals(True)
                    self._bg_ambient_combo.setCurrentIndex(_AMBIENT_IDX.get(ambient_key, 0))
                    self._bg_ambient_combo.blockSignals(False)
                    ambient_label = _AMBIENT_LABELS.get(ambient_key, ambient_key)
                    self._bg_ambient_theme_lbl.setText(
                        f"Using theme ambient: {ambient_label}  (set by '{theme_name}' theme)"
                    )
                else:
                    # Theme has no ambient — show info label (item 4)
                    self._bg_ambient_theme_lbl.setText(
                        f"🚫  '{theme_name}' theme has no ambient"
                    )
        finally:
            for c in _combos:
                c.blockSignals(False)
        # Update the sound theme info label if "Use theme sound" is currently checked
        if self._use_theme_sound_check.isChecked():
            self._update_sound_theme_info()

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
        enabled = self._sound_check.isChecked()
        self._settings.set("sound_enabled", enabled)
        # Show/hide all sub-controls depending on the enabled state (item 68).
        self._sound_sub_widget.setVisible(enabled)
        # Also keep the combo enable states in sync for when they become visible.
        if enabled:
            use_theme = self._use_theme_sound_check.isChecked()
            self._sound_theme_info_lbl.setVisible(use_theme)
            self._sound_profile_combo.setVisible(not use_theme)
            self._sound_profile_combo.setEnabled(not use_theme)
            self._sound_profile_lbl.setVisible(not use_theme)
        self.settings_changed.emit()

    def _update_sound_theme_info(self) -> None:
        """Update the sound profile combo to show the current theme's profile (item 1).

        When 'Use theme sound' is on the combo is disabled and set to the
        profile the active theme would auto-select so the user can see at a
        glance which profile is in use without any separate label.
        """
        try:
            from .sound_engine import _THEME_SOUND_PROFILES
            theme_name = self._settings.get_theme().get("name", "")
            profile = _THEME_SOUND_PROFILES.get(theme_name, "soft")
            idx = self._sound_profile_combo.findData(profile)
            if idx >= 0:
                self._sound_profile_combo.setCurrentIndex(idx)
            # Show a small hint below so the user knows this is auto-set.
            self._sound_theme_info_lbl.setText(
                f"🎵 Auto-set by '{theme_name}' theme  →  {profile}"
            )
            self._sound_theme_info_lbl.setVisible(True)
        except Exception:
            self._sound_theme_info_lbl.setVisible(False)

    def _on_use_theme_sound_changed(self) -> None:
        self._settings.set("use_theme_sound", self._use_theme_sound_check.isChecked())
        self.settings_changed.emit()

    def _on_use_theme_sound_toggled(self, checked: bool) -> None:
        """When 'Use theme sound' is toggled: disable/enable the combo (item 1).

        The sound profile combo stays visible regardless of this toggle.  When
        use-theme is ON the combo is disabled and updated to show the theme's
        auto-selected profile.  When OFF the combo is re-enabled so the user
        can pick manually.
        """
        if checked:
            self._update_sound_theme_info()
        else:
            # Restore the manually-saved profile in the combo.
            saved = str(self._settings.get("sound_manual_profile", "soft"))
            idx = self._sound_profile_combo.findData(saved)
            if idx >= 0:
                self._sound_profile_combo.setCurrentIndex(idx)
        # Always keep combo and its label visible; just toggle enabled state.
        self._sound_profile_combo.setEnabled(not checked)
        self._sound_theme_info_lbl.setVisible(False)

    def _on_sound_profile_changed(self) -> None:
        """Save the manually selected sound profile."""
        profile = self._sound_profile_combo.currentData()
        if profile:
            self._settings.set("sound_manual_profile", profile)

    def _on_sound_volume_changed(self, value: int) -> None:
        self._settings.set("sound_volume", value)
        # No settings_changed emit needed — volume is read at play time.

    def _on_sound_theme_change_changed(self) -> None:
        self._settings.set("sound_theme_change", self._sound_theme_change_chk.isChecked())

    def _on_sound_tab_switch_changed(self) -> None:
        self._settings.set("sound_tab_switch", self._sound_tab_switch_chk.isChecked())

    def _on_sound_drag_enter_changed(self) -> None:
        self._settings.set("sound_drag_enter", self._sound_drag_enter_chk.isChecked())

    def _on_mute_all_events(self) -> None:
        """Turn off all individual event-sound checkboxes at once."""
        for chk in (
            self._sound_theme_change_chk,
            self._sound_tab_switch_chk,
            self._sound_drag_enter_chk,
        ):
            chk.setChecked(False)

    def _update_trail_theme_info(self) -> None:
        """Refresh the trail theme info label."""
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "")
        trail_style = theme.get("_trail", "dots")
        trail_color = theme.get("_trail_color", "#e94560")
        if trail_style:
            self._trail_theme_info_lbl.setText(
                f"Theme trail:  '{theme_name}'  →  style: {trail_style},  color: {trail_color}"
            )
        else:
            self._trail_theme_info_lbl.setText(
                f"Theme trail:  '{theme_name}'  →  (no trail defined for this theme)"
            )

    def _update_effect_theme_info(self) -> None:
        """Refresh the click effect theme info label."""
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "")
        effect_key = theme.get("_effect", "default")
        # Look up a human-friendly label for the effect key
        _effect_labels = {k: lbl.split("—")[0].strip() for k, lbl in _EFFECT_OPTIONS}
        effect_label = _effect_labels.get(effect_key, effect_key)
        self._effect_theme_info_lbl.setText(
            f"Theme effect:  '{theme_name}'  →  {effect_label}"
        )

    def _update_cursor_theme_info(self) -> None:
        """Refresh the cursor theme info label."""
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "")
        cursor_spec = theme.get("_cursor", "Default")
        if cursor_spec.startswith("emoji:"):
            cursor_label = cursor_spec[len("emoji:"):]
        else:
            cursor_label = cursor_spec
        self._cursor_theme_info_lbl.setText(
            f"Theme cursor:  '{theme_name}'  →  {cursor_label}"
        )

    def _update_btn_anim_theme_info(self) -> None:
        """Refresh the button animation theme info label."""
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "")
        anim_key = theme.get("_button_anim", "press")
        _anim_labels = {
            "press": "Press", "fall": "Fall", "bounce": "Bounce",
            "shake": "Shake", "shatter": "Shatter", "vanish": "Vanish", "explode": "Explode",
        }
        self._btn_anim_theme_info_lbl.setText(
            f"Theme animation:  '{theme_name}'  →  {_anim_labels.get(anim_key, anim_key)}"
        )

    def _update_banner_theme_info(self) -> None:
        """Refresh the banner animation theme info label."""
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "")
        anim_key = theme.get("_banner_anim", "spin")
        _anim_labels = {
            "spin": "Spin", "bounce": "Bounce", "shake": "Shake",
            "pendulum": "Pendulum", "pulse": "Pulse", "float": "Float",
            "flip": "Flip", "orbit": "Orbit", "glitch": "Glitch", "drip": "Drip",
        }
        self._banner_theme_info_lbl.setText(
            f"Theme animation:  '{theme_name}'  →  {_anim_labels.get(anim_key, anim_key)}"
        )

    def _on_trail_changed(self) -> None:
        enabled = self._trail_check.isChecked()
        use_theme = self._use_theme_trail_check.isChecked()
        self._settings.set("trail_enabled", enabled)
        self._settings.set("use_theme_trail", use_theme)
        self._trail_sub.setVisible(enabled)
        self._trail_manual_widget.setVisible(not use_theme)
        self._trail_theme_info_lbl.setVisible(use_theme)
        if use_theme:
            self._update_trail_theme_info()
        if enabled and not self._settings.get("trail_enabled_once", False):
            self._settings.set("trail_enabled_once", True)
            self.settings_changed.emit()
            self.first_trail_enabled.emit()
        else:
            self.settings_changed.emit()

    def _on_use_theme_trail_changed(self) -> None:
        """Handle the 'use theme trail' checkbox independently."""
        enabled = self._trail_check.isChecked()
        use_theme = self._use_theme_trail_check.isChecked()
        self._settings.set("trail_enabled", enabled)
        self._settings.set("use_theme_trail", use_theme)
        self._trail_manual_widget.setVisible(not use_theme)
        self._trail_theme_info_lbl.setVisible(use_theme)
        if use_theme:
            self._update_trail_theme_info()
        self.settings_changed.emit()

    def _on_trail_style_changed(self) -> None:
        _IDX_TO_STYLE = ["dots", "ribbon", "noodle", "comet", "fairy", "wave",
                         "sparkle", "rainbow", "distortion", "fire", "lightning",
                         "plasma", "sakura", "smoke"]
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
        self._settings.set("cursor_enabled", self._cursor_enable_check.isChecked())
        self._settings.set("cursor", self._cursor_combo.currentText())
        self._settings.set("use_theme_cursor", self._use_theme_cursor_check.isChecked())
        self.settings_changed.emit()

    def _on_cursor_anim_changed(self) -> None:
        enabled = self._cursor_anim_check.isChecked()
        # Emit first-enable signal before persisting so the unlock fires once.
        if enabled and not self._settings.get("cursor_anim_used_once", False):
            self._settings.set("cursor_anim_used_once", True)
            self._settings.set("cursor_anim_enabled", enabled)
            self.settings_changed.emit()
            self.first_cursor_anim_enabled.emit()
        else:
            self._settings.set("cursor_anim_enabled", enabled)
            self.settings_changed.emit()

    def _on_font_size_changed(self, value: int) -> None:
        self._settings.set("font_size", value)
        self.settings_changed.emit()

    def _on_ui_scale_changed(self) -> None:
        _scale_keys = ["Compact", "Normal", "Large", "Extra Large"]
        idx = self._ui_scale_combo.currentIndex()
        key = _scale_keys[idx] if 0 <= idx < len(_scale_keys) else "Normal"
        self._settings.set("ui_scale", key)
        # Apply the scale immediately by adjusting the base font size
        _scale_factors = {"Compact": 0.85, "Normal": 1.0, "Large": 1.15, "Extra Large": 1.30}
        factor = _scale_factors.get(key, 1.0)
        base_pt = self._settings.get("font_size", 10)
        scaled_pt = max(7, int(round(base_pt * factor)))
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QFont
            app = QApplication.instance()
            if app is not None:
                f = app.font()
                f.setPointSize(scaled_pt)
                app.setFont(f)
                # Force all open windows to re-polish so the font change is
                # visible without a restart.
                app.setStyle(app.style())
        except Exception:
            pass
        self.settings_changed.emit()

    def _on_btn_height_changed(self) -> None:
        _keys = ["Compact", "Normal", "Comfortable"]
        idx = self._btn_height_combo.currentIndex()
        key = _keys[idx] if 0 <= idx < len(_keys) else "Normal"
        self._settings.set("btn_height", key)
        self.settings_changed.emit()

    def _on_widget_spacing_changed(self) -> None:
        _keys = ["Tight", "Normal", "Relaxed"]
        idx = self._widget_spacing_combo.currentIndex()
        key = _keys[idx] if 0 <= idx < len(_keys) else "Normal"
        self._settings.set("widget_spacing", key)
        self.settings_changed.emit()

    def _on_history_max_changed(self, value: int) -> None:
        self._settings.set("history_max_entries", value)

    def _on_effects_enabled_changed(self) -> None:
        enabled = self._click_effects_theme_check.isChecked()
        self._settings.set("click_effects_enabled", enabled)
        use_theme = self._use_theme_effect_check.isChecked()
        self._click_effect_sub.setVisible(enabled)
        self._effect_inner_widget.setVisible(not use_theme)
        self._effect_theme_info_lbl.setVisible(use_theme)
        if use_theme and enabled:
            self._update_effect_theme_info()
        self.settings_changed.emit()

    def _on_use_theme_effect_changed(self) -> None:
        use_theme = self._use_theme_effect_check.isChecked()
        enabled = self._click_effects_theme_check.isChecked()
        self._settings.set("use_theme_effect", use_theme)
        self._effect_inner_widget.setVisible(not use_theme)
        self._effect_theme_info_lbl.setVisible(use_theme)
        if use_theme:
            self._update_effect_theme_info()
        self.settings_changed.emit()

    def _on_tooltip_mode_changed(self) -> None:
        # Track first-ever tooltip mode change to trigger the Secret Skeleton unlock.
        # Both settings saves complete before the signal fires, so the unlock
        # only triggers after the flag and mode have been persisted.
        should_unlock = not self._settings.get("tooltip_mode_changed_once", False)
        if should_unlock:
            self._settings.set("tooltip_mode_changed_once", True)
        # Debounce the actual settings write to avoid per-step I/O lag (item 79)
        self._misc_combo_pending["tooltip_mode"] = self._tooltip_mode_combo.currentText()
        self._misc_combo_debounce.start()
        if should_unlock:
            self.first_tooltip_mode_change.emit()

    def _on_tooltip_style_changed(self) -> None:
        # Debounce the settings write to avoid per-step I/O lag (item 79)
        self._misc_combo_pending["tooltip_style"] = self._tooltip_style_combo.currentText()
        self._misc_combo_debounce.start()

    def _flush_misc_combo_changes(self) -> None:
        """Write debounced combo changes to settings and emit settings_changed."""
        for key, value in self._misc_combo_pending.items():
            self._settings.set(key, value)
        self._misc_combo_pending.clear()
        self.settings_changed.emit()

    def _on_animated_banner_changed(self) -> None:
        enabled = self._animated_banner_check.isChecked()
        self._settings.set("animated_banner_enabled", enabled)
        use_theme = self._banner_use_theme_anim_check.isChecked()
        self._banner_anim_sub.setVisible(enabled)
        self._banner_manual_widget.setVisible(not use_theme)
        self._banner_theme_info_lbl.setVisible(use_theme)
        if use_theme and enabled:
            self._update_banner_theme_info()
        self.settings_changed.emit()

    def _on_banner_anim_style_changed(self) -> None:
        key = self._banner_anim_combo.currentData() or "spin"
        self._settings.set("banner_anim_style", key)
        self.settings_changed.emit()

    def _on_banner_use_theme_anim_changed(self) -> None:
        use_theme = self._banner_use_theme_anim_check.isChecked()
        self._settings.set("banner_use_theme_anim", use_theme)
        self._banner_manual_widget.setVisible(not use_theme)
        self._banner_theme_info_lbl.setVisible(use_theme)
        if use_theme:
            self._update_banner_theme_info()
        self.settings_changed.emit()

    def _on_show_splash_changed(self) -> None:
        self._settings.set("show_splash_screen", self._show_splash_check.isChecked())
        self.settings_changed.emit()

    def _on_button_anim_changed(self) -> None:
        enabled = self._button_anim_check.isChecked()
        self._settings.set("button_anim_enabled", enabled)
        use_theme = self._use_theme_button_anim_check.isChecked()
        self._btn_anim_sub.setVisible(enabled)
        self._btn_anim_style_widget.setVisible(not use_theme)
        self._btn_anim_theme_info_lbl.setVisible(use_theme)
        if use_theme and enabled:
            self._update_btn_anim_theme_info()
        self.settings_changed.emit()

    def _on_button_anim_style_changed(self) -> None:
        key = self._button_anim_style_combo.currentData() or "press"
        self._settings.set("button_anim_style", key)
        self.settings_changed.emit()

    def _on_use_theme_button_anim_changed(self) -> None:
        use_theme = self._use_theme_button_anim_check.isChecked()
        self._settings.set("use_theme_button_anim", use_theme)
        enabled = self._button_anim_check.isChecked()
        self._btn_anim_style_widget.setVisible(not use_theme)
        self._btn_anim_theme_info_lbl.setVisible(use_theme)
        if use_theme:
            self._update_btn_anim_theme_info()
        self.settings_changed.emit()


    def _on_bg_drip_changed(self) -> None:
        enabled = self._bg_drip_check.isChecked()
        self._settings.set("bg_drip_enabled", enabled)
        use_theme_drip = self._use_theme_drip_check.isChecked()
        self._settings.set("use_theme_drip", use_theme_drip)
        drip_type = self._bg_drip_combo.currentData() or "blood"
        self._settings.set("bg_drip_type", drip_type)
        # Keep sub-controls in sync with the enabled/use-theme state.
        self._use_theme_drip_check.setEnabled(enabled)
        # item 1/4: combo stays visible — disabled + shows themed value when use-theme is on
        self._bg_drip_combo.setEnabled(enabled and not use_theme_drip)
        self._bg_drip_theme_lbl.setVisible(use_theme_drip)
        # Update info label and combo to reflect theme drip when "use theme" is on
        if use_theme_drip:
            theme = self._settings.get_theme()
            effect_key = theme.get("_effect", "default")
            theme_name = theme.get("name", "")
            if effect_key in ("gore", "shark"):
                theme_drip = "blood"
                drip_label = f"🩸 Auto-set by '{theme_name}' theme  →  Blood Drip"
            elif effect_key in ("ocean", "ripple", "mermaid"):
                theme_drip = "water"
                drip_label = f"💧 Auto-set by '{theme_name}' theme  →  Water Drip"
            else:
                # Theme has no drip — show info message (item 4)
                theme_drip = None
                drip_label = f"🚫 '{theme_name}' theme has no drip effect"
            self._bg_drip_theme_lbl.setText(drip_label)
            # Sync combo to theme drip value if there is one
            if theme_drip:
                for i in range(self._bg_drip_combo.count()):
                    if self._bg_drip_combo.itemData(i) == theme_drip:
                        self._bg_drip_combo.setCurrentIndex(i)
                        break
        self.settings_changed.emit()

    def _on_bg_flock_changed(self) -> None:
        enabled = self._bg_flock_check.isChecked()
        self._settings.set("bg_flock_enabled", enabled)
        use_theme_flock = self._use_theme_flock_check.isChecked()
        self._settings.set("use_theme_flock", use_theme_flock)
        flock_style = self._bg_flock_combo.currentData() or "bats"
        self._settings.set("bg_flock_style", flock_style)
        # item 1/4: inner widget stays visible — disable combo when use-theme is on
        self._use_theme_flock_check.setEnabled(enabled)
        self._bg_flock_theme_lbl.setVisible(use_theme_flock)
        self._bg_flock_inner_widget.setVisible(True)
        self._bg_flock_combo.setEnabled(enabled and not use_theme_flock)
        self.settings_changed.emit()

    def _on_bg_ambient_changed(self) -> None:
        enabled = self._bg_ambient_check.isChecked()
        self._settings.set("bg_ambient_enabled", enabled)
        use_theme = self._use_theme_ambient_check.isChecked()
        self._settings.set("use_theme_ambient", use_theme)
        if not enabled:
            self._settings.set("bg_ambient_type", "none")
        elif not use_theme:
            ambient_type = self._bg_ambient_combo.currentData() or "snow"
            self._settings.set("bg_ambient_type", ambient_type)
        # item 1/4: inner widget stays visible — disable combo when use-theme is on
        self._use_theme_ambient_check.setEnabled(enabled)
        self._bg_ambient_theme_lbl.setVisible(use_theme)
        self._bg_ambient_inner_widget.setVisible(True)
        self._bg_ambient_combo.setEnabled(enabled and not use_theme)
        self.settings_changed.emit()

    def _on_notif_overlay_changed(self) -> None:
        """Persist notification overlay settings when toggles change (item 66)."""
        enabled = self._notif_overlay_check.isChecked()
        use_theme = self._use_theme_notif_check.isChecked()
        self._settings.set("notif_overlay_enabled", enabled)
        self._settings.set("use_theme_notif", use_theme)
        # Show/hide sub-settings
        self._notif_overlay_sub.setVisible(enabled)
        self._notif_theme_info_lbl.setVisible(use_theme)
        self.settings_changed.emit()

    def _on_custom_bg_changed(self) -> None:
        """Persist custom background settings when controls change (item 81)."""
        enabled = self._custom_bg_check.isChecked()
        use_theme = self._use_theme_bg_check.isChecked()
        path = self._custom_bg_path_edit.text().strip()
        self._settings.set("custom_bg_enabled", enabled)
        self._settings.set("use_theme_bg", use_theme)
        self._settings.set("custom_bg_path", path)
        # Show/hide sub-settings
        self._custom_bg_sub.setVisible(enabled)
        self._custom_bg_file_row.setVisible(not use_theme)
        self.settings_changed.emit()
