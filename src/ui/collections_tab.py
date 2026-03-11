"""
Collections tab – shows all unlockable content (themes, effects, cursors)
and their unlock status.  Unlocked items are shown with their details;
locked items show a hint about how to unlock them.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QGroupBox, QFrame,
    QTabWidget,
)
from PyQt6.QtGui import QFont


# ---------------------------------------------------------------------------
# Unlock milestone data
# ---------------------------------------------------------------------------

# (settings_key, display_name, unlock_hint, emoji)
_HIDDEN_THEME_DATA = [
    ("unlock_skeleton",     "Secret Skeleton 💀",     "Change the tooltip mode for the first time",     "💀"),
    ("unlock_sakura",       "Secret Sakura 🌸",        "Run your first alpha fix",                       "🌸"),
    ("unlock_ocean",        "Deep Ocean 🌊",           "Reach 500 total clicks",                         "🌊"),
    ("unlock_ice_cave",     "Ice Cave ❄",              "Reach 150 total clicks",                         "❄"),
    ("unlock_cyber_otter",  "Cyber Otter 🦦",          "Reach 200 total clicks",                         "🦦"),
    ("unlock_blood_moon",   "Blood Moon 🩸",           "Reach 750 total clicks",                         "🩸"),
    ("unlock_toxic_neon",   "Toxic Neon ☢",            "Reach 350 total clicks",                         "☢"),
    ("unlock_lava_cave",    "Lava Cave 🌋",             "Reach 600 total clicks",                         "🌋"),
    ("unlock_sunset_beach", "Sunset Beach 🌅",         "Convert your first file",                        "🌅"),
    ("unlock_midnight_forest", "Midnight Forest 🌲",  "Reach 1000 total clicks",                        "🌲"),
    ("unlock_candy_land",   "Candy Land 🍭",           "Reach 1250 total clicks",                        "🍭"),
    ("unlock_zombie",       "Zombie Apocalypse 🧟",    "Reach 1500 total clicks",                        "🧟"),
    ("unlock_dragon_fire",  "Dragon Fire 🐉",          "Reach 1750 total clicks",                        "🐉"),
    ("unlock_bubblegum",    "Bubblegum 🫧",            "Reach 2000 total clicks",                        "🫧"),
    ("unlock_thunder_storm","Thunder Storm ⚡",        "Reach 2250 total clicks",                        "⚡"),
    ("unlock_rose_gold",    "Rose Gold 🌹",            "Reach 2500 total clicks",                        "🌹"),
    ("unlock_space_cat",    "Space Cat 🐱",            "Reach 2750 total clicks",                        "🐱"),
    ("unlock_magic_mushroom","Magic Mushroom 🍄",      "Reach 3000 total clicks",                        "🍄"),
    ("unlock_abyssal_void", "Abyssal Void 🕳",         "Reach 3500 total clicks",                        "🕳"),
    ("unlock_spring_bloom", "Spring Bloom 🌷",         "Reach 4000 total clicks",                        "🌷"),
    ("unlock_gold_rush",    "Gold Rush 💰",            "Reach 4500 total clicks",                        "💰"),
    ("unlock_nebula",       "Nebula 🌌",               "Reach 5000 total clicks",                        "🌌"),
]

# Preset themes (always unlocked)
_PRESET_THEME_NAMES = [
    "Panda Dark 🐼", "Panda Light 🐼", "Neon Panda 🐼",
    "Galaxy 🌌", "Galaxy Otter 🦦", "Otter Cove 🦦",
    "Rainbow Chaos 🌈", "Goth ⚰️", "Gore 💀",
    "Bat Cave 🦇", "Fairy Garden 🧚", "Noodle 🍜",
    "Volcano 🌋", "Nebula 🪐", "Mermaid 🧜",
    "Shark Bait 🦈", "Alien 🛸",
]

# Click effects (listed with their unlock conditions)
_EFFECT_DATA = [
    ("default",      "Default ✦",     True,    ""),
    ("panda",        "Panda 🐼",      True,    ""),
    ("gore",         "Gore 💀",       True,    ""),
    ("bat",          "Bat 🦇",        True,    ""),
    ("rainbow",      "Rainbow 🌈",    True,    ""),
    ("otter",        "Otter 🦦",      True,    ""),
    ("galaxy",       "Galaxy 🌌",     True,    ""),
    ("galaxy_otter", "Galaxy Otter 🦦", True,  ""),
    ("goth",         "Goth ⚰️",       True,    ""),
    ("neon",         "Neon ⚡",       True,    ""),
    ("fire",         "Fire 🔥",       True,    ""),
    ("ice",          "Ice ❄",         "unlock_ice_cave",  "Unlock Ice Cave theme"),
    ("sakura",       "Sakura 🌸",     "unlock_sakura",    "Unlock Secret Sakura theme"),
    ("fairy",        "Fairy ✨",      True,    ""),
    ("ocean",        "Ocean 🌊",      "unlock_ocean",     "Unlock Deep Ocean theme"),
    ("sparkle",      "Sparkle ✦",     True,    ""),
    ("ripple",       "Ripple 🫧",     "unlock_ocean",     "Unlock Deep Ocean theme"),
    ("mermaid",      "Mermaid 🧜",    True,    ""),
    ("alien",        "Alien 🛸",      True,    ""),
    ("shark",        "Shark 🦈",      True,    ""),
    ("custom",       "Custom Emoji ✨", True,  ""),
]

# Trail styles
_TRAIL_DATA = [
    ("dots",    "Dots ·",     True,  ""),
    ("fairy",   "Fairy ✨",   True,  ""),
    ("wave",    "Wave 🌊",    "unlock_ocean",     "Unlock Deep Ocean theme"),
    ("sparkle", "Sparkle ✦",  "unlock_ice_cave",  "Unlock Ice Cave theme"),
]


# ---------------------------------------------------------------------------
# CollectionsTab
# ---------------------------------------------------------------------------

class CollectionsTab(QWidget):
    """Tab displaying all collectible content and their unlock status."""

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        hdr = QLabel("🏆  Collections & Unlockables")
        hdr.setObjectName("header")
        outer.addWidget(hdr)

        # Progress summary
        self._progress_lbl = QLabel()
        self._progress_lbl.setObjectName("subheader")
        outer.addWidget(self._progress_lbl)

        # Refresh button
        btn_row = QHBoxLayout()
        self._btn_refresh = QPushButton("🔄  Refresh")
        self._btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(self._btn_refresh)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        # Sub-tabs: Themes | Effects | Trails | Cursors
        self._sub_tabs = QTabWidget()

        # Themes sub-tab
        self._themes_scroll = self._make_scroll()
        self._themes_grid = QGridLayout(self._themes_scroll.widget())
        self._themes_grid.setSpacing(8)
        self._sub_tabs.addTab(self._themes_scroll, "🎨  Themes")

        # Effects sub-tab
        self._effects_scroll = self._make_scroll()
        self._effects_grid = QGridLayout(self._effects_scroll.widget())
        self._effects_grid.setSpacing(8)
        self._sub_tabs.addTab(self._effects_scroll, "✨  Effects")

        # Trails sub-tab
        self._trails_scroll = self._make_scroll()
        self._trails_grid = QGridLayout(self._trails_scroll.widget())
        self._trails_grid.setSpacing(8)
        self._sub_tabs.addTab(self._trails_scroll, "💫  Trails")

        outer.addWidget(self._sub_tabs, 1)

    def _make_scroll(self) -> QScrollArea:
        """Return a scroll area with a plain QWidget interior."""
        inner = QWidget()
        sa = QScrollArea()
        sa.setWidget(inner)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.Shape.NoFrame)
        return sa

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self):
        """Reload unlock state and rebuild all panels."""
        self._build_themes()
        self._build_effects()
        self._build_trails()
        self._update_progress()

    def _update_progress(self):
        total_hidden = len(_HIDDEN_THEME_DATA)
        unlocked = sum(
            1 for key, _, _, _ in _HIDDEN_THEME_DATA
            if self._settings.get(key, False)
        )
        total_clicks = self._settings.get("total_clicks", 0)
        self._progress_lbl.setText(
            f"Hidden themes: {unlocked}/{total_hidden} unlocked  ·  "
            f"Total clicks: {total_clicks:,}"
        )

    def _build_themes(self):
        self._clear_layout(self._themes_grid)
        cols = 3
        row = 0
        col = 0

        # ── Preset themes (always unlocked) ─────────────────────────
        sect_lbl = QLabel("✅  Preset Themes (Always Available)")
        sect_lbl.setObjectName("section")
        self._themes_grid.addWidget(sect_lbl, row, 0, 1, cols)
        row += 1

        for name in _PRESET_THEME_NAMES:
            card = self._make_card(name, True, "")
            self._themes_grid.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        if col != 0:
            row += 1
            col = 0

        # ── Hidden themes ────────────────────────────────────────────
        sect_lbl2 = QLabel("🔒  Unlockable Hidden Themes")
        sect_lbl2.setObjectName("section")
        self._themes_grid.addWidget(sect_lbl2, row, 0, 1, cols)
        row += 1

        for settings_key, display_name, hint, emoji in _HIDDEN_THEME_DATA:
            is_unlocked = self._settings.get(settings_key, False)
            card = self._make_card(
                display_name,
                is_unlocked,
                hint if not is_unlocked else "",
            )
            self._themes_grid.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

        # Spacer at the bottom so cards don't stretch to fill
        self._themes_grid.setRowStretch(row + 1, 1)

    def _build_effects(self):
        self._clear_layout(self._effects_grid)
        seen = set()
        cols = 3
        row = 0
        col = 0
        for key, display_name, unlock_cond, hint in _EFFECT_DATA:
            if display_name in seen:
                continue
            seen.add(display_name)
            if unlock_cond is True:
                unlocked = True
                hint_text = ""
            else:
                unlocked = self._settings.get(unlock_cond, False)
                hint_text = hint if not unlocked else ""
            card = self._make_card(display_name, unlocked, hint_text)
            self._effects_grid.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        self._effects_grid.setRowStretch(row + 1, 1)

    def _build_trails(self):
        self._clear_layout(self._trails_grid)
        cols = 3
        row = 0
        col = 0
        for key, display_name, unlock_cond, hint in _TRAIL_DATA:
            if unlock_cond is True:
                unlocked = True
                hint_text = ""
            else:
                unlocked = self._settings.get(unlock_cond, False)
                hint_text = hint if not unlocked else ""
            card = self._make_card(display_name, unlocked, hint_text)
            self._trails_grid.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        self._trails_grid.setRowStretch(row + 1, 1)

    # ------------------------------------------------------------------
    # Card builder
    # ------------------------------------------------------------------

    def _make_card(self, title: str, unlocked: bool, hint: str) -> QFrame:
        """Create a styled card widget for one collectible item."""
        card = QFrame()
        card.setObjectName("collection_card_unlocked" if unlocked else "collection_card_locked")
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setMinimumHeight(72)
        card.setMaximumHeight(90)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        name_lbl = QLabel(title)
        name_font = QFont()
        name_font.setPointSize(10)
        name_font.setBold(True)
        name_lbl.setFont(name_font)
        name_lbl.setWordWrap(True)
        layout.addWidget(name_lbl)

        if unlocked:
            status_lbl = QLabel("✅  Unlocked")
            status_lbl.setObjectName("collection_status_ok")
        else:
            status_lbl = QLabel(f"🔒  {hint}" if hint else "🔒  Locked")
            status_lbl.setObjectName("collection_status_locked")
            status_lbl.setWordWrap(True)

        small_font = QFont()
        small_font.setPointSize(8)
        status_lbl.setFont(small_font)
        layout.addWidget(status_lbl)

        return card

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clear_layout(layout):
        """Remove all widgets from a QGridLayout."""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ------------------------------------------------------------------
    # Tooltip registration
    # ------------------------------------------------------------------

    def register_tooltips(self, mgr) -> None:
        """Register Collections tab widgets with the TooltipManager."""
        mgr.register(self._btn_refresh, "collections_refresh_btn")
        mgr.register(self._sub_tabs.widget(0), "collections_themes_sub")
        mgr.register(self._sub_tabs.widget(1), "collections_effects_sub")
        mgr.register(self._sub_tabs.widget(2), "collections_trails_sub")
