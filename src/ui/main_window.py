"""
Main application window.
"""
import sys
import webbrowser

from PyQt6.QtCore import Qt, QSize, QRect, QTimer
from PyQt6.QtGui import QAction, QCursor, QFont, QFontMetrics, QIcon, QKeySequence, QPixmap, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QToolBar,
    QLabel, QPushButton, QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QMessageBox, QFileDialog,
)

from ..core.settings_manager import SettingsManager, DEFAULT_CUSTOM_EMOJI
from ..core.presets import PresetManager
from .alpha_tool import AlphaFixerTab
from .converter_tool import ConverterTab
from .history_tab import HistoryTab
from .settings_dialog import SettingsDialog
from .theme_engine import (
    build_stylesheet, PRESET_THEMES, HIDDEN_THEMES, THEME_EFFECTS,
    get_theme_svg_path, get_theme_banner, get_theme_status,
    get_theme_banner_frames, get_theme_tab_labels, get_theme_icon,
)
from ..version import __version__

PATREON_URL = "https://www.patreon.com/c/DeadOnTheInside"


def _apply_dwm_title_bar_color(hwnd: int, hex_color: str) -> bool:
    """Attempt to set the Windows 11+ title bar color via DWM.

    Uses DwmSetWindowAttribute (DWMWA_CAPTION_COLOR = 35) which is only
    supported on Windows 11 build 22000+.  Silently returns False on older
    Windows versions or non-Windows platforms.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import ctypes.wintypes
        # Parse "#rrggbb" → COLORREF (0x00bbggrr)
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return False
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        colorref = ctypes.c_uint32(b << 16 | g << 8 | r)
        DWMWA_CAPTION_COLOR = 35
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.c_uint32(DWMWA_CAPTION_COLOR),
            ctypes.byref(colorref),
            ctypes.c_uint32(ctypes.sizeof(colorref)),
        )
        return True
    except Exception:
        return False

_CURSOR_MAP = {
    "Default":        Qt.CursorShape.ArrowCursor,
    "Cross":          Qt.CursorShape.CrossCursor,
    "Pointing Hand":  Qt.CursorShape.PointingHandCursor,
    "Open Hand":      Qt.CursorShape.OpenHandCursor,
    "Hourglass":      Qt.CursorShape.WaitCursor,
    "Forbidden":      Qt.CursorShape.ForbiddenCursor,
    "IBeam":          Qt.CursorShape.IBeamCursor,
    "Size All":       Qt.CursorShape.SizeAllCursor,
    "Blank":          Qt.CursorShape.BlankCursor,
}


def _make_emoji_cursor(emoji: str, size: int = 40) -> QCursor:
    """Render *emoji* into a square pixmap and return a QCursor from it.

    The emoji is drawn centred in the pixmap and the hotspot is placed at
    the centre so that interactions (clicks, hover) register at the visual
    centre of the emoji character rather than at the invisible top-left
    corner of the bounding box.

    Falls back to the arrow cursor if pixmap painting is unavailable
    (e.g. running headless without a display).
    """
    try:
        # Use a slightly larger pixmap than the rendered font size to ensure
        # the full glyph is visible without clipping.
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        # Use a font stack that covers Windows (Segoe UI Emoji), macOS (Apple Color Emoji),
        # and Linux (Noto Color Emoji) so the emoji renders on every platform.
        font = QFont()
        font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"])
        font.setPointSize(max(6, size - 10))
        painter.setFont(font)
        painter.drawText(
            QRect(0, 0, size, size),
            Qt.AlignmentFlag.AlignCenter,
            emoji,
        )
        painter.end()
        # Hotspot at centre of the pixmap so the click-point matches the
        # visual centre of the emoji (avoids the top-left offset problem).
        return QCursor(pix, size // 2, size // 2)
    except Exception:
        return QCursor(Qt.CursorShape.ArrowCursor)


class _SpinningEmojiLabel(QWidget):
    """Renders a single emoji character and rotates it continuously.

    This provides the per-theme "animated banner" effect: each theme's
    representative emoji (🐼, 🩸, 🦇, etc.) appears to spin like a gear,
    giving a genuine visual animation without cycling through different emojis.
    """

    _DEGREES_PER_FRAME = 2.0   # rotation speed per ~33 ms tick ≈ 1 full turn / ~6 s
    _INTERVAL_MS = 33           # ~30 fps

    def __init__(self, emoji: str = "🐼", font_size: int = 20, parent=None):
        super().__init__(parent)
        self._emoji = emoji
        self._font_size = font_size
        self._angle = 0.0
        sz = font_size + 16
        self.setFixedSize(sz, sz)
        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_emoji(self, emoji: str) -> None:
        """Change the displayed emoji; takes effect on the next paint."""
        self._emoji = emoji
        self.update()

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        sz = size + 16
        self.setFixedSize(sz, sz)
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + self._DEGREES_PER_FRAME) % 360.0
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.width(), self.height()
        painter.translate(w / 2.0, h / 2.0)
        painter.rotate(self._angle)

        font = QFont()
        font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"])
        font.setPointSize(self._font_size)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(self._emoji)
        th = fm.height()
        painter.drawText(-tw // 2, th // 4, self._emoji)
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self, settings: SettingsManager):
        super().__init__()
        self._settings = settings
        self._preset_mgr = PresetManager(settings)
        self._trail_overlay = None
        self._click_effects = None
        self._tooltip_mgr = None
        self._sound = None
        self._svg_badge = None
        self._banner_lbl = None
        self._banner_emoji_left: "_SpinningEmojiLabel | None" = None
        self._banner_emoji_right: "_SpinningEmojiLabel | None" = None
        self._status_bar = None
        self._unlock_timer = None
        self._anim_timer = None    # kept for compatibility (no longer used for cycling)
        self._banner_frames: list[str] = []
        self._banner_frame_idx: int = 0
        self._tab_base_labels: tuple = ()   # set during first _apply_theme()
        self._setup_window()
        self._setup_ui()
        self._restore_geometry()
        self._apply_theme()
        self._setup_effects()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle(f"🐼 Alpha Fixer & File Converter  v{__version__}")
        self.setMinimumSize(1000, 780)
        # Set the panda SVG as the window / taskbar icon (initial default).
        # Prefer the pre-generated multi-size ICO (embedded by PyInstaller)
        # which contains all shell sizes (16 → 256 px) for crisp display at
        # every zoom level.  Falls back to rendering the SVG directly when the
        # ICO is not present (e.g. running from source without running
        # scripts/make_icon.py first).
        self._set_panda_window_icon()

    @staticmethod
    def _render_svg_to_icon(svg_path: str) -> "QIcon | None":
        """Render *svg_path* at multiple resolutions and return a QIcon.

        Provides 16, 24, 32, 48, 64, 128 and 256 px variants so Qt always has
        a sharp pixmap for the title bar (16 px), taskbar (32/40/48 px) and
        the jump-list thumbnail (256 px) on every platform and DPI setting.
        Returns *None* if QtSvg is not available.
        """
        try:
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(svg_path)
            icon = QIcon()
            for size in (16, 24, 32, 40, 48, 64, 128, 256):
                pix = QPixmap(size, size)
                pix.fill(Qt.GlobalColor.transparent)
                p = QPainter(pix)
                renderer.render(p)
                p.end()
                icon.addPixmap(pix)
            return icon
        except (ImportError, Exception):
            return None

    def _set_panda_window_icon(self):
        """Set the initial window / taskbar icon to the panda theme graphic."""
        import os
        assets_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "assets")
        )
        # 1. Prefer the pre-generated multi-size ICO (ships with the repo).
        ico_path = os.path.join(assets_dir, "icon.ico")
        if os.path.isfile(ico_path):
            icon = QIcon(ico_path)
            if not icon.isNull():
                self.setWindowIcon(icon)
                QApplication.setWindowIcon(icon)
                return
        # 2. Fall back to rendering the panda SVG at multiple sizes.
        svg_dir = os.path.join(assets_dir, "svg")
        for candidate in ("panda_dark.svg", "panda_light.svg"):
            svg_path = os.path.normpath(os.path.join(svg_dir, candidate))
            if os.path.isfile(svg_path):
                icon = self._render_svg_to_icon(svg_path)
                if icon is not None:
                    self.setWindowIcon(icon)
                    QApplication.setWindowIcon(icon)
                return

    def _setup_ui(self):
        # Menu bar
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        act_quit = QAction("Quit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        settings_menu = menubar.addMenu("Settings")
        act_settings = QAction("Preferences…", self)
        act_settings.setShortcut("Ctrl+,")
        act_settings.triggered.connect(self._open_settings)
        settings_menu.addAction(act_settings)

        settings_menu.addSeparator()

        act_export = QAction("Export Settings…", self)
        act_export.triggered.connect(self._export_settings)
        settings_menu.addAction(act_export)

        act_import = QAction("Import Settings…", self)
        act_import.triggered.connect(self._import_settings)
        settings_menu.addAction(act_import)

        help_menu = menubar.addMenu("Help")
        act_shortcuts = QAction("Keyboard Shortcuts", self)
        act_shortcuts.setShortcut("F1")
        act_shortcuts.triggered.connect(self._show_shortcuts)
        help_menu.addAction(act_shortcuts)
        act_about = QAction("About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)
        help_menu.addSeparator()
        act_patreon = QAction("❤  Support on Patreon…", self)
        act_patreon.triggered.connect(self._open_patreon)
        help_menu.addAction(act_patreon)

        # Central widget with tabs
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # Animated banner: a left+right spinning emoji flanks the static title text.
        # The emoji rotates continuously (like a turning gear) using _SpinningEmojiLabel.
        # The emoji changes to reflect the active theme without cycling between emojis.
        banner_container = QWidget()
        banner_container.setObjectName("header")
        banner_layout = QHBoxLayout(banner_container)
        banner_layout.setContentsMargins(8, 6, 8, 6)
        banner_layout.setSpacing(8)

        self._banner_emoji_left = _SpinningEmojiLabel("🐼", font_size=20)
        banner_layout.addWidget(self._banner_emoji_left)

        banner_text = QLabel("Alpha Fixer  &  File Converter")
        banner_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner_text.setObjectName("header")
        banner_text.setStyleSheet("padding: 0; font-size: 20px; background: transparent; border: none;")
        banner_text.setMinimumHeight(36)
        banner_layout.addWidget(banner_text, 1)

        self._banner_emoji_right = _SpinningEmojiLabel("🐼", font_size=20)
        banner_layout.addWidget(self._banner_emoji_right)

        cv.addWidget(banner_container)
        self._banner_lbl = banner_text  # kept for theme update compatibility

        self._tabs = QTabWidget()
        self._alpha_tab = AlphaFixerTab(self._preset_mgr, self._settings)
        self._converter_tab = ConverterTab(self._settings)
        self._history_tab = HistoryTab(self._settings)
        self._tabs.addTab(self._alpha_tab, "🖼  Alpha Fixer")
        self._tabs.addTab(self._converter_tab, "🔄  Converter")
        self._tabs.addTab(self._history_tab, "📋  History")
        # Refresh history whenever the user switches to it
        self._tabs.currentChanged.connect(self._on_tab_changed)
        cv.addWidget(self._tabs, 1)

        # Keyboard shortcuts for tab switching: Ctrl+1/2/3
        for idx, key in enumerate(("Ctrl+1", "Ctrl+2", "Ctrl+3")):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(lambda i=idx: self._tabs.setCurrentIndex(i))

        self.setCentralWidget(central)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready  🐼")

        # Toolbar
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)

        # Panda icon on the far left of the toolbar
        panda_lbl = self._make_toolbar_panda_icon()
        if panda_lbl is not None:
            toolbar.addWidget(panda_lbl)
            toolbar.addSeparator()

        btn_settings = QPushButton("⚙ Settings")
        btn_settings.clicked.connect(self._open_settings)
        toolbar.addWidget(btn_settings)
        self._btn_settings = btn_settings
        toolbar.addSeparator()

        self._theme_label = QLabel("  Theme: Panda Dark  ")
        self._theme_label.setObjectName("subheader")
        self._theme_label.setMinimumWidth(220)  # wide enough for long hidden theme names
        toolbar.addWidget(self._theme_label)

        toolbar.addSeparator()
        btn_patreon = QPushButton("❤ Patreon")
        btn_patreon.setToolTip(
            "Support development on Patreon!\n"
            "patreon.com/c/DeadOnTheInside"
        )
        btn_patreon.clicked.connect(self._open_patreon)
        toolbar.addWidget(btn_patreon)
        self._btn_patreon = btn_patreon

        # Unlock status label (shown when a secret theme unlocks)
        self._unlock_lbl = QLabel("")
        self._unlock_lbl.setObjectName("subheader")
        self._unlock_lbl.setStyleSheet("color: #ffcc00; padding: 0 8px;")
        toolbar.addWidget(self._unlock_lbl)

        # SVG theme badge (uses QSvgWidget when QtSvg is available, else text fallback)
        toolbar.addSeparator()
        self._svg_badge = self._make_svg_badge()
        if self._svg_badge is not None:
            toolbar.addWidget(self._svg_badge)
        self._svg_badge_toolbar = toolbar  # keep ref for badge refresh

    # ------------------------------------------------------------------
    # Visual / audio effects (trail, cursor, sound, click effects, tooltips)
    # ------------------------------------------------------------------

    def _setup_effects(self):
        # Mouse trail overlay
        from .mouse_trail import MouseTrailOverlay
        self._trail_overlay = MouseTrailOverlay(self)
        self._trail_overlay.setGeometry(self.rect())
        self._trail_overlay.raise_()
        self._apply_trail()

        # Click effects overlay
        from .click_effects import ClickEffectsOverlay
        self._click_effects = ClickEffectsOverlay(self)
        self._click_effects.setGeometry(self.rect())
        self._click_effects.raise_()
        effects_enabled = self._settings.get("click_effects_enabled", False)
        self._click_effects.set_enabled(effects_enabled)
        self._click_effects.click_registered.connect(self._check_unlocks)
        self._apply_theme_effect()

        # Connect processing-done signals so file processing can unlock themes
        self._alpha_tab.processing_done.connect(self._on_processing_done)
        self._converter_tab.processing_done.connect(self._on_processing_done)
        # First-use unlock triggers
        self._alpha_tab.first_alpha_fix.connect(self._on_first_alpha_fix)
        self._converter_tab.first_conversion.connect(self._on_first_conversion)
        # File-add sounds
        self._alpha_tab.files_added.connect(self._on_files_added)
        self._converter_tab.files_added.connect(self._on_files_added)

        # Cursor
        self._apply_cursor()

        # Sound engine
        from .sound_engine import SoundEngine
        self._sound = SoundEngine(self._settings, parent=self)
        self._sound.install_on_app(QApplication.instance())

        # Font size
        self._apply_font_size()

        # Tooltip manager
        from .tooltip_manager import TooltipManager
        self._tooltip_mgr = TooltipManager(self._settings, parent=self)
        self._tooltip_mgr.install_on_app(QApplication.instance())
        self._register_tooltips()

    def _register_tooltips(self) -> None:
        """Wire all main-window and tab widgets to the TooltipManager."""
        mgr = self._tooltip_mgr
        if mgr is None:
            return
        mgr.register(self._btn_settings, "settings_btn")
        mgr.register(self._btn_patreon, "patreon_btn")
        # Register per-tab tooltips on the QTabBar
        mgr.register_tab_bar(
            self._tabs.tabBar(),
            ["alpha_fixer_tab", "converter_tab", "history_tab"],
        )
        self._alpha_tab.register_tooltips(mgr)
        self._converter_tab.register_tooltips(mgr)
        self._history_tab.register_tooltips(mgr)

    def _apply_theme_effect(self):
        """Set the click-effects overlay to match the active theme's effect key."""
        if self._click_effects is None:
            return
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "Panda Dark")
        # If "use theme effect" is enabled, always auto-select from THEME_EFFECTS map
        if self._settings.get("use_theme_effect", False):
            effect_key = THEME_EFFECTS.get(theme_name, "default")
        else:
            # Prefer the theme dict's own _effect key (which the user may have
            # customised in the settings dialog) over the hardcoded THEME_EFFECTS
            # map.  This ensures that changing the "Click Effect Style" combo in
            # Settings → Theme is actually respected even for preset themes.
            # Fall back to THEME_EFFECTS only when no _effect key is stored.
            effect_key = theme.get("_effect") or THEME_EFFECTS.get(theme_name, "default")
        self._click_effects.set_effect(effect_key)
        # Push the user's custom emoji list to the custom spawner
        custom_raw = self._settings.get("custom_emoji", DEFAULT_CUSTOM_EMOJI)
        custom_emoji = custom_raw.split() if custom_raw.strip() else DEFAULT_CUSTOM_EMOJI.split()
        self._click_effects.set_custom_emoji(custom_emoji)

    # ------------------------------------------------------------------
    # Unlock hidden themes based on click count
    # ------------------------------------------------------------------

    def _check_unlocks(self) -> None:
        """Check whether any hidden theme should be unlocked (click path)."""
        try:
            total = self._settings.get("total_clicks", 0) + 1
            self._settings.set("total_clicks", total)
        except Exception:
            return
        self._run_unlock_checks(total)

    def _run_unlock_checks(self, total: int) -> None:
        """Evaluate the unlock table against *total* and fire any new unlocks."""
        # (threshold, settings_key, banner_message) — ordered ascending by threshold
        _UNLOCK_TABLE = [
            (100,  "unlock_skeleton",        "🔓 'Secret Skeleton' theme unlocked! (Settings → Theme)"),
            (150,  "unlock_ice_cave",         "❄ 'Ice Cave' theme unlocked! (Settings → Theme)"),
            (200,  "unlock_cyber_otter",      "🦦 'Cyber Otter' theme unlocked! (Settings → Theme)"),
            (250,  "unlock_sakura",           "🌸 'Secret Sakura' theme unlocked! (Settings → Theme)"),
            (350,  "unlock_toxic_neon",       "☢ 'Toxic Neon' theme unlocked! (Settings → Theme)"),
            (400,  "unlock_sunset_beach",     "🌅 'Sunset Beach' theme unlocked! (Settings → Theme)"),
            (500,  "unlock_ocean",            "🌊 'Deep Ocean' theme unlocked! (Settings → Theme)"),
            (600,  "unlock_lava_cave",        "🌋 'Lava Cave' theme unlocked! (Settings → Theme)"),
            (750,  "unlock_blood_moon",       "🩸 'Blood Moon' theme unlocked! (Settings → Theme)"),
            (1000, "unlock_midnight_forest",  "🌲 'Midnight Forest' theme unlocked! (Settings → Theme)"),
            (1250, "unlock_candy_land",       "🍭 'Candy Land' theme unlocked! (Settings → Theme)"),
            (1500, "unlock_zombie",           "🧟 'Zombie Apocalypse' theme unlocked! (Settings → Theme)"),
            (1750, "unlock_dragon_fire",      "🐉 'Dragon Fire' theme unlocked! (Settings → Theme)"),
            (2000, "unlock_bubblegum",        "🫧 'Bubblegum' theme unlocked! (Settings → Theme)"),
            (2250, "unlock_thunder_storm",    "⚡ 'Thunder Storm' theme unlocked! (Settings → Theme)"),
            (2500, "unlock_rose_gold",        "🌹 'Rose Gold' theme unlocked! (Settings → Theme)"),
            (2750, "unlock_space_cat",        "🐱 'Space Cat' theme unlocked! (Settings → Theme)"),
            (3000, "unlock_magic_mushroom",   "🍄 'Magic Mushroom' theme unlocked! (Settings → Theme)"),
            (3500, "unlock_abyssal_void",     "🕳 'Abyssal Void' theme unlocked! (Settings → Theme)"),
            (4000, "unlock_spring_bloom",     "🌷 'Spring Bloom' theme unlocked! (Settings → Theme)"),
            (4500, "unlock_gold_rush",        "💰 'Gold Rush' theme unlocked! (Settings → Theme)"),
            (5000, "unlock_nebula",           "🌌 'Nebula' theme unlocked! (Settings → Theme)"),
            (5500, "unlock_crystal_cave",     "💎 'Crystal Cave' theme unlocked! (Settings → Theme)"),
            (6000, "unlock_glitch",           "📡 'Glitch' theme unlocked! (Settings → Theme)"),
            (6500, "unlock_wild_west",        "🤠 'Wild West' theme unlocked! (Settings → Theme)"),
            (7000, "unlock_pirate",           "🏴‍☠️ 'Pirate' theme unlocked! (Settings → Theme)"),
            (7500, "unlock_deep_space",       "🛸 'Deep Space' theme unlocked! (Settings → Theme)"),
            (8000, "unlock_witchs_brew",      "🧙 'Witch's Brew' theme unlocked! (Settings → Theme)"),
            (8500, "unlock_lava_lamp",        "🪔 'Lava Lamp' theme unlocked! (Settings → Theme)"),
            (9000, "unlock_coral_reef",       "🪸 'Coral Reef' theme unlocked! (Settings → Theme)"),
            (9500, "unlock_storm_cloud",      "⛈ 'Storm Cloud' theme unlocked! (Settings → Theme)"),
            (10000,"unlock_golden_hour",      "🌇 'Golden Hour' theme unlocked! (Settings → Theme)"),
        ]

        newly_unlocked = False
        for threshold, key, message in _UNLOCK_TABLE:
            if not self._settings.get(key, False) and total >= threshold:
                self._settings.set(key, True)
                self._unlock_lbl.setText(message)
                # Play unlock fanfare via SoundEngine (falls back to beep)
                try:
                    self._sound.play_unlock()
                except Exception:
                    try:
                        QApplication.instance().beep()
                    except Exception:
                        pass
                newly_unlocked = True

        # Auto-clear the unlock banner after 6 seconds
        if newly_unlocked:
            self._schedule_unlock_clear()

    def _on_processing_done(self, file_count: int) -> None:
        """Called when a batch of files is processed (alpha-fix or convert).

        Each file successfully processed is counted as a 'bonus click' so
        that heavy users who batch-process files naturally unlock themes
        without having to manually click thousands of times.  Also plays
        the success chime if sound is enabled.
        """
        if file_count <= 0:
            return
        try:
            self._sound.play_success()
        except Exception:
            pass
        try:
            total = self._settings.get("total_clicks", 0) + file_count
            self._settings.set("total_clicks", total)
        except Exception:
            return
        # Re-use the click-based unlock table but driven by total_clicks
        # (which now includes processing bonuses).
        self._run_unlock_checks(total)

    def _on_files_added(self) -> None:
        """Play a soft sound when files are added to either tab's queue."""
        try:
            self._sound.play_file_add()
        except Exception:
            pass

    def _schedule_unlock_clear(self) -> None:
        """Start (or restart) a one-shot timer that clears the unlock label."""
        from PyQt6.QtCore import QTimer
        if self._unlock_timer is None:
            self._unlock_timer = QTimer(self)
            self._unlock_timer.setSingleShot(True)
            self._unlock_timer.timeout.connect(lambda: self._unlock_lbl.setText(""))
        self._unlock_timer.start(6000)

    def _apply_cursor(self):
        use_theme = self._settings.get("use_theme_cursor", False)
        if use_theme:
            # Read the active theme's preferred cursor
            theme = self._settings.get_theme()
            cursor_spec = theme.get("_cursor", "Default")
            if cursor_spec.startswith("emoji:"):
                emoji = cursor_spec[len("emoji:"):]
                self.setCursor(_make_emoji_cursor(emoji))
                return
            # Otherwise treat it as a named cursor key
            shape = _CURSOR_MAP.get(cursor_spec, Qt.CursorShape.ArrowCursor)
            self.setCursor(QCursor(shape))
        else:
            cursor_name = self._settings.get("cursor", "Default")
            # Check if it's a system cursor name
            if cursor_name in _CURSOR_MAP:
                self.setCursor(QCursor(_CURSOR_MAP[cursor_name]))
            elif cursor_name.startswith("emoji:"):
                # Stored as "emoji:<char>" from theme profiles
                self.setCursor(_make_emoji_cursor(cursor_name[len("emoji:"):]))
            else:
                # Combo items like "🐼 Panda" – extract the emoji (first char/cluster)
                # by taking everything before the first space
                parts = cursor_name.split(" ", 1)
                if parts and parts[0].strip():
                    self.setCursor(_make_emoji_cursor(parts[0]))
                else:
                    self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def _apply_font_size(self):
        size = self._settings.get("font_size", 10)
        size = max(8, min(24, int(size)))
        app = QApplication.instance()
        font = QFont(app.font())
        font.setPointSize(size)
        app.setFont(font)

    # ------------------------------------------------------------------
    # Geometry / state
    # ------------------------------------------------------------------

    def _restore_geometry(self):
        if self._settings.get("window_maximized", False):
            self.showMaximized()
            return
        x = self._settings.get("window_x")
        y = self._settings.get("window_y")
        w = self._settings.get("window_w")
        h = self._settings.get("window_h")
        self.setGeometry(x, y, w, h)

    def _save_geometry(self):
        self._settings.set("window_maximized", self.isMaximized())
        if not self.isMaximized():
            g = self.geometry()
            self._settings.set("window_x", g.x())
            self._settings.set("window_y", g.y())
            self._settings.set("window_w", g.width())
            self._settings.set("window_h", g.height())

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_theme(self):
        theme = self._settings.get_theme()
        tooltip_style = self._settings.get("tooltip_style", "Auto (follow theme)")
        QApplication.instance().setStyleSheet(build_stylesheet(theme, tooltip_style))
        theme_name = theme.get("name", "Custom")
        self._theme_label.setText(f"  Theme: {theme_name}  ")
        # Update the spinning banner emoji to the theme's representative icon.
        # The emoji rotates continuously — no cycling between different emojis.
        icon = get_theme_icon(theme_name)
        if self._banner_emoji_left is not None:
            self._banner_emoji_left.set_emoji(icon)
        if self._banner_emoji_right is not None:
            self._banner_emoji_right.set_emoji(icon)
        # Keep static text label; update it to the theme banner (without emojis)
        if self._banner_lbl is not None:
            self._banner_lbl.setText("Alpha Fixer  &  File Converter")
        # Stop any legacy animation timer (banner no longer cycles emojis)
        if self._anim_timer is not None:
            self._anim_timer.stop()
        # Store theme-specific tab labels; update tab text directly (no spinner).
        self._tab_base_labels = get_theme_tab_labels(theme_name)
        self._update_tab_labels()
        # Update inner tab headers to also reflect the active theme
        self._alpha_tab.update_theme(theme_name)
        self._converter_tab.update_theme(theme_name)
        # Update status bar with per-theme flavor message
        if self._status_bar is not None:
            self._status_bar.showMessage(get_theme_status(theme_name))
        # Re-apply cursor so theme-cursor mode updates immediately on theme change
        self._apply_cursor()
        # Update window icon and taskbar icon to match the current theme SVG
        self._refresh_window_icon(theme_name)
        # Refresh SVG badge to match new theme
        self._refresh_svg_badge()
        # Keep trail and click-effects in sync with the active theme.
        # These overlays are created in _setup_effects() which runs after the
        # first _apply_theme() call, so guard with None checks.
        if self._trail_overlay is not None:
            self._apply_trail()
        if self._click_effects is not None:
            self._apply_theme_effect()
        # On Windows 11+, colour the native title bar to match the theme's
        # primary/surface colour so the window chrome integrates with the theme.
        try:
            hwnd = int(self.winId())
            # Use the theme's 'primary' colour for the title bar background.
            # Fallback to surface, then a dark default.
            title_color = (
                theme.get("primary")
                or theme.get("surface")
                or "#1a1a2e"
            )
            _apply_dwm_title_bar_color(hwnd, title_color)
        except Exception:
            pass

    def _update_tab_labels(self):
        """Write the theme-specific label to every tab (no animation prefix)."""
        for i, base in enumerate(self._tab_base_labels):
            self._tabs.setTabText(i, base)

    def _make_toolbar_panda_icon(self):
        """Render the panda SVG to a 28×28 QLabel for the toolbar. Returns None on failure."""
        import os
        svg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "svg")
        for candidate in ("panda_dark.svg", "panda_light.svg"):
            svg_path = os.path.normpath(os.path.join(svg_dir, candidate))
            if os.path.isfile(svg_path):
                try:
                    from PyQt6.QtSvg import QSvgRenderer
                    renderer = QSvgRenderer(svg_path)
                    pix = QPixmap(28, 28)
                    pix.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(pix)
                    renderer.render(painter)
                    painter.end()
                    lbl = QLabel()
                    lbl.setPixmap(pix)
                    lbl.setToolTip("Alpha Fixer && File Converter 🐼")
                    lbl.setContentsMargins(4, 0, 4, 0)
                    return lbl
                except Exception:
                    pass
                break
        # Fallback: plain text panda emoji
        lbl = QLabel("🐼")
        lbl.setToolTip("Alpha Fixer && File Converter 🐼")
        lbl.setContentsMargins(4, 0, 4, 0)
        return lbl

    def _make_svg_badge(self):
        """Create a small SVG theme badge widget.  Returns None if QtSvg unavailable."""
        try:
            from PyQt6.QtSvgWidgets import QSvgWidget
            badge = QSvgWidget()
            badge.setFixedSize(48, 48)
            badge.setToolTip("Theme decoration")
            return badge
        except ImportError:
            return None

    def _refresh_svg_badge(self):
        """Update the SVG badge to show the decoration for the current theme."""
        if self._svg_badge is None:
            return
        try:
            from PyQt6.QtSvgWidgets import QSvgWidget
        except ImportError:
            return
        theme = self._settings.get_theme()
        svg_path = get_theme_svg_path(theme.get("name", ""))
        if svg_path:
            self._svg_badge.load(svg_path)
            self._svg_badge.setToolTip(f"{theme.get('name','?')} theme")
            self._svg_badge.show()
        else:
            self._svg_badge.hide()

    def _refresh_window_icon(self, theme_name: str):
        """Update the window / taskbar icon to the theme-specific SVG.

        Renders the theme SVG at multiple resolutions (16 → 256 px) so Qt can
        pick the sharpest pixmap for each use-case (title bar, taskbar, etc.).
        Falls back to the panda default when no theme SVG is available.
        """
        import os
        svg_path = get_theme_svg_path(theme_name)
        if not svg_path:
            # No theme SVG – use the panda default
            svg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "svg")
            for candidate in ("panda_dark.svg", "panda_light.svg"):
                candidate_path = os.path.normpath(os.path.join(svg_dir, candidate))
                if os.path.isfile(candidate_path):
                    svg_path = candidate_path
                    break
        if not svg_path:
            return
        try:
            icon = self._render_svg_to_icon(svg_path)
            if icon is None:
                return
            self.setWindowIcon(icon)
            QApplication.setWindowIcon(icon)
        except RuntimeError:
            # Widget destroyed – silently skip.
            pass

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int):
        if self._tabs.widget(index) is self._history_tab:
            self._history_tab.refresh()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self, tooltip_mgr=self._tooltip_mgr)
        dlg.theme_changed.connect(lambda t: self._apply_theme())
        dlg.settings_changed.connect(self._on_settings_changed)
        # First tooltip mode change unlocks Secret Skeleton (independent of click count)
        dlg.first_tooltip_mode_change.connect(self._on_first_tooltip_mode_change)

        # Attach a click-effects overlay to the dialog so particle effects are
        # visible while the settings window is open (the main overlay is behind
        # the modal and therefore not visible).
        dlg_overlay = None
        if (self._click_effects is not None
                and self._settings.get("click_effects_enabled", False)):
            from .click_effects import ClickEffectsOverlay
            dlg_overlay = ClickEffectsOverlay(dlg)
            # Mirror the current effect setting but don't count clicks toward
            # secret-theme unlocks (leave click_registered unconnected).
            theme = self._settings.get_theme()
            effect_key = (theme.get("_effect")
                          or THEME_EFFECTS.get(theme.get("name", ""), "default"))
            dlg_overlay.set_effect(effect_key)
            custom_emoji = self._settings.get("custom_emoji", DEFAULT_CUSTOM_EMOJI)
            dlg_overlay.set_custom_emoji(custom_emoji.split() if custom_emoji.strip() else [])
            dlg_overlay.set_enabled(True)

        dlg.exec()

        if dlg_overlay is not None:
            dlg_overlay.set_enabled(False)

    def _on_settings_changed(self):
        """Re-apply all effect-related settings after the dialog closes."""
        self._apply_theme()
        self._apply_cursor()
        self._apply_font_size()
        self._apply_theme_effect()
        self._apply_trail()
        if self._click_effects is not None:
            self._click_effects.set_enabled(
                self._settings.get("click_effects_enabled", False)
            )

    def _on_first_tooltip_mode_change(self) -> None:
        """Unlock Secret Skeleton the first time the user changes the tooltip mode."""
        if not self._settings.get("unlock_skeleton", False):
            self._settings.set("unlock_skeleton", True)
            self._unlock_lbl.setText("🔓 'Secret Skeleton' theme unlocked! (Settings → Theme)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _on_first_alpha_fix(self) -> None:
        """Unlock Secret Sakura the very first time the user runs an alpha fix."""
        if not self._settings.get("unlock_sakura", False):
            self._settings.set("unlock_sakura", True)
            self._unlock_lbl.setText("🌸 'Secret Sakura' theme unlocked! (first alpha fix!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _on_first_conversion(self) -> None:
        """Unlock Sunset Beach the very first time the user converts files."""
        if not self._settings.get("unlock_sunset_beach", False):
            self._settings.set("unlock_sunset_beach", True)
            self._unlock_lbl.setText("🌅 'Sunset Beach' theme unlocked! (first conversion!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _apply_trail(self):
        """Apply trail color, style, length, fade speed, intensity and enabled state."""
        if self._trail_overlay is None:
            return
        use_theme = self._settings.get("use_theme_trail", False)
        if use_theme:
            theme = self._settings.get_theme()
            color = theme.get("_trail_color", "#e94560")
            effect = theme.get("_effect", "default")
            # Map effect → trail style
            if effect == "fairy":
                style = "fairy"
            elif effect in ("ocean", "mermaid", "ripple"):
                style = "wave"
            elif effect in ("sparkle", "ice"):
                style = "sparkle"
            else:
                style = "dots"
        else:
            color = self._settings.get("trail_color", "#e94560")
            style = self._settings.get("trail_style", "dots")
        self._trail_overlay.set_color(color)
        self._trail_overlay.set_style(style)
        # Apply length/fade/intensity — always from user settings regardless of theme trail
        self._trail_overlay.set_length(int(self._settings.get("trail_length", 50)))
        self._trail_overlay.set_fade_speed(int(self._settings.get("trail_fade_speed", 5)))
        self._trail_overlay.set_intensity(int(self._settings.get("trail_intensity", 100)))
        self._trail_overlay.set_enabled(
            self._settings.get("trail_enabled", False)
        )

    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings", "alpha_fixer_settings.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self._settings.export_settings(path)
            QMessageBox.information(self, "Export Settings",
                                    f"Settings exported to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _import_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Settings", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            keys = self._settings.import_settings(path)
            self._on_settings_changed()
            QMessageBox.information(
                self, "Import Settings",
                f"Imported {len(keys)} settings from:\n{path}\n\n"
                "Restart the app to fully apply all changes.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", str(exc))

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def _show_shortcuts(self):
        QMessageBox.information(
            self,
            "Keyboard Shortcuts",
            "<table>"
            "<tr><td><b>F5</b></td><td>Run / Process / Convert</td></tr>"
            "<tr><td><b>Esc</b></td><td>Stop current operation</td></tr>"
            "<tr><td><b>Ctrl+O</b></td><td>Add files</td></tr>"
            "<tr><td><b>Ctrl+Shift+O</b></td><td>Add folder</td></tr>"
            "<tr><td><b>Delete</b></td><td>Remove selected files from list</td></tr>"
            "<tr><td><b>Ctrl+,</b></td><td>Open Settings</td></tr>"
            "<tr><td><b>Ctrl+Q</b></td><td>Quit</td></tr>"
            "<tr><td><b>F1</b></td><td>This help</td></tr>"
            "</table>",
        )

    def _show_about(self):
        QMessageBox.about(
            self,
            "About 🐼 Alpha Fixer & File Converter",
            f"<h2>🐼 Alpha Fixer & File Converter  v{__version__}</h2>"
            "<p>A panda-themed tool for fixing alpha channels and converting image files.</p>"
            "<ul>"
            "<li><b>Alpha Fixer:</b> PS2, N64, No Alpha, Max Alpha presets + custom</li>"
            "<li><b>Converter:</b> PNG, DDS, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF, AVIF, QOI and more</li>"
            "<li>Drag-and-drop + batch folder/subfolder processing</li>"
            "<li>Before/after comparison slider preview with live RGB/alpha stats</li>"
            "<li>Image preview, conversion history + CSV export, export/import settings</li>"
            "<li>18 preset themes + 32 hidden unlockables (keep clicking to find them!)</li>"
            "<li>21 click effects: Gore 🩸, Bat Cave 🦇, Rainbow 🌈, Galaxy ✦, Neon ⚡, Fire 🔥,"
            " Ice ❄, Panda 🐼, Sakura 🌸, Ocean 🌊, Mermaid 🧜, Alien 🛸, Shark 🦈, and more…</li>"
            "<li>Per-channel RGBA delta adjustments (R/G/B/A ±255) for colour-correcting game textures</li>"
            "<li>Theme cursor: automatically applies a matching cursor per theme (Otter Cove → 🤘)</li>"
            "<li>Unique per-theme banner, shapes, and visual style — each theme has its own look</li>"
            "<li>Cycling tooltips with Normal, Dumbed Down, and No Filter 🤬 modes</li>"
            "<li>Keyboard shortcuts: F5 run · Esc stop · Ctrl+O add files · Ctrl+1/2/3 switch tabs · F1 help</li>"
            "</ul>"
            "<p>Built with Python + PyQt6 + Pillow.</p>"
            f'<p><a href="{PATREON_URL}">❤ Support on Patreon</a></p>',
        )

    def _open_patreon(self):
        webbrowser.open(PATREON_URL)

    # ------------------------------------------------------------------
    # Resize – keep the trail overlay covering the whole window
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._trail_overlay is not None:
            self._trail_overlay.setGeometry(self.rect())
            self._trail_overlay.raise_()
        if self._click_effects is not None:
            self._click_effects.setGeometry(self.rect())
            self._click_effects.raise_()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        # Stop any running workers gracefully
        for tab in (self._alpha_tab, self._converter_tab):
            if hasattr(tab, "_worker") and tab._worker and tab._worker.isRunning():
                tab._worker.stop()
                tab._worker.wait(3000)
        # Clean up temp sound file
        if self._sound is not None:
            self._sound.cleanup()
        self._save_geometry()
        super().closeEvent(event)

