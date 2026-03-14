"""
Main application window.
"""
import math
import sys
import webbrowser

from PyQt6.QtCore import Qt, QRect, QTimer
from PyQt6.QtGui import QCursor, QFont, QFontMetrics, QIcon, QKeySequence, QPixmap, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QMenu,
    QLabel, QPushButton, QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QMessageBox, QFileDialog,
)

from ..core.settings_manager import SettingsManager, DEFAULT_CUSTOM_EMOJI
from ..core.presets import PresetManager
from .alpha_tool import AlphaFixerTab
from .converter_tool import ConverterTab
from .history_tab import HistoryTab
from .selective_alpha_tool import SelectiveAlphaTool
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
    """Renders a single emoji with one of several animation modes.

    Modes
    -----
    "spin"      Continuous 360° rotation (original behaviour).
    "bounce"    Vertical bobbing using a sine wave.
    "shake"     Rapid horizontal quiver.
    "pendulum"  Oscillating swing (±30°) like a metronome.
    "static"    No motion; used when an external flock effect is active.

    The active mode is set via ``set_mode()``.  The animation is toggled
    via ``set_animated()`` exactly as before so all callers stay compatible.
    """

    _INTERVAL_MS = 33  # ~30 fps

    # Per-mode speed constants
    _SPIN_DEG_PER_FRAME = 2.0   # full rotation ≈ 6 s
    _BOUNCE_STEP        = 0.12  # rad/tick ≈ full cycle / ~4 s
    _SHAKE_STEP         = 0.40  # rad/tick ≈ full cycle / ~0.5 s
    _PENDULUM_STEP      = 0.06  # rad/tick ≈ full cycle / ~10 s

    _BOUNCE_AMPLITUDE   = 6     # pixels
    _SHAKE_AMPLITUDE    = 5     # pixels
    _PENDULUM_MAX_ANGLE = 30.0  # degrees

    _VALID_MODES = frozenset({"spin", "bounce", "shake", "pendulum", "static"})

    def __init__(self, emoji: str = "🐼", font_size: int = 20, parent=None):
        super().__init__(parent)
        self._emoji = emoji
        self._font_size = font_size
        self._mode = "spin"
        self._angle = 0.0     # degrees – used by spin / pendulum
        self._phase = 0.0     # radians – used by bounce / shake / pendulum
        self._offset_x = 0.0  # pixel offset for bounce / shake
        self._offset_y = 0.0
        self._update_size()
        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        # Timer is NOT started by default; set_animated(True) starts it.

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Change the animation mode and reset all motion state."""
        if mode not in self._VALID_MODES:
            mode = "spin"
        self._mode = mode
        self._angle = 0.0
        self._phase = 0.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._update_size()
        self.update()

    def set_emoji(self, emoji: str) -> None:
        """Change the displayed emoji; takes effect on the next paint."""
        self._emoji = emoji
        self.update()

    def set_animated(self, enabled: bool) -> None:
        """Start or stop the animation timer.

        In "static" mode the timer is never started even when *enabled* is
        True — the emoji is always rendered without motion (the caller may
        activate an external flock effect instead).
        """
        if enabled and self._mode != "static":
            if not self._timer.isActive():
                self._timer.start()
        else:
            if self._timer.isActive():
                self._timer.stop()
            self._angle = 0.0
            self._phase = 0.0
            self._offset_x = 0.0
            self._offset_y = 0.0
            self.update()

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        self._update_size()
        self.update()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_size(self) -> None:
        """Resize the widget to fit the emoji plus any animation headroom."""
        base = self._font_size + 16
        if self._mode == "bounce":
            self.setFixedSize(base, base + self._BOUNCE_AMPLITUDE * 2)
        elif self._mode == "shake":
            self.setFixedSize(base + self._SHAKE_AMPLITUDE * 2, base)
        else:
            self.setFixedSize(base, base)

    def _tick(self) -> None:
        mode = self._mode
        if mode == "spin":
            self._angle = (self._angle + self._SPIN_DEG_PER_FRAME) % 360.0
        elif mode == "bounce":
            self._phase = (self._phase + self._BOUNCE_STEP) % (2 * math.pi)
            self._offset_y = math.sin(self._phase) * self._BOUNCE_AMPLITUDE
        elif mode == "shake":
            self._phase = (self._phase + self._SHAKE_STEP) % (2 * math.pi)
            self._offset_x = math.sin(self._phase) * self._SHAKE_AMPLITUDE
        elif mode == "pendulum":
            self._phase = (self._phase + self._PENDULUM_STEP) % (2 * math.pi)
            self._angle = math.sin(self._phase) * self._PENDULUM_MAX_ANGLE
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.width(), self.height()
        # Translate to widget centre plus any per-mode positional offset.
        painter.translate(w / 2.0 + self._offset_x, h / 2.0 + self._offset_y)
        # Apply rotation for spin / pendulum modes.
        if self._angle != 0.0:
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
    # Unlock table: (click_threshold, settings_key, banner_message).
    # Stored at class level so it is built once, not rebuilt on every click.
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
        (10000, "unlock_golden_hour",     "🌇 'Golden Hour' theme unlocked! (Settings → Theme)"),
    ]

    def __init__(self, settings: SettingsManager):
        super().__init__()
        self._settings = settings
        self._preset_mgr = PresetManager(settings)
        self._trail_overlay = None
        self._click_effects = None
        self._button_anim = None
        self._tooltip_mgr = None
        self._sound = None
        self._svg_badge = None
        self._banner_lbl = None
        self._banner_emoji_left: "_SpinningEmojiLabel | None" = None
        self._banner_emoji_right: "_SpinningEmojiLabel | None" = None
        self._toolbar_panda_lbl: "QLabel | None" = None
        self._status_bar = None
        self._unlock_timer = None
        self._anim_timer = None    # kept for compatibility (no longer used for cycling)
        self._banner_frames: list[str] = []
        self._banner_frame_idx: int = 0
        self._tab_base_labels: tuple = ()   # set during first _apply_theme()
        # Debounce timer: collapses rapid settings_changed signals into a
        # single re-apply call so slider drags / spinbox scrolling don't
        # trigger dozens of expensive setStyleSheet() calls per second.
        self._settings_apply_timer = QTimer(self)
        self._settings_apply_timer.setSingleShot(True)
        self._settings_apply_timer.setInterval(200)
        self._settings_apply_timer.timeout.connect(self._apply_settings_now)
        # Resize debounce timer: window resize fires very rapidly during an
        # interactive drag.  Repositioning the overlays on every pixel update
        # is wasteful; coalesce them into a single update 50ms after the last
        # resize event to keep the UI responsive during dragging.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._reposition_overlays)
        self._setup_window()
        self._setup_ui()
        self._restore_geometry()
        self._apply_theme()
        self._setup_effects()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle(f"🐼 Alpha & RGBA Adjuster  |  File Converter  v{__version__}")
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
                if p.isActive():
                    renderer.render(p)
                    p.end()
                    icon.addPixmap(pix)
            return icon if not icon.isNull() else None
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
        # Keep keyboard shortcuts that were previously tied to menu actions
        from PyQt6.QtGui import QShortcut
        sc_quit = QShortcut(QKeySequence("Ctrl+Q"), self)
        sc_quit.activated.connect(self.close)
        sc_settings = QShortcut(QKeySequence("Ctrl+,"), self)
        sc_settings.activated.connect(self._open_settings)
        sc_help = QShortcut(QKeySequence("F1"), self)
        sc_help.activated.connect(self._show_shortcuts)

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

        banner_text = QLabel("Alpha & RGBA Adjuster  |  File Converter")
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
        self._selective_alpha_tab = SelectiveAlphaTool(self._settings)
        self._tabs.addTab(self._alpha_tab, "🖼  Alpha & RGBA Adjuster")
        self._tabs.addTab(self._converter_tab, "🔄  Converter")
        self._tabs.addTab(self._history_tab, "📋  History")
        self._tabs.addTab(self._selective_alpha_tab, "🎨  Selective Alpha")
        # Refresh history whenever the user switches to it
        self._tabs.currentChanged.connect(self._on_tab_changed)
        cv.addWidget(self._tabs, 1)

        # Keyboard shortcuts for tab switching: Ctrl+1/2/3/4
        for idx, key in enumerate(("Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4")):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(lambda i=idx: self._tabs.setCurrentIndex(i))

        # Corner widget: Settings / Help / Patreon buttons on the right of the tab bar.
        # This puts all tool controls in one row, freeing vertical space for content.
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(2, 2, 6, 2)
        corner_layout.setSpacing(4)

        # Unlock status label (shown briefly when a secret theme unlocks)
        self._unlock_lbl = QLabel("")
        self._unlock_lbl.setObjectName("subheader")
        self._unlock_lbl.setStyleSheet("color: #ffcc00; padding: 0 6px;")
        corner_layout.addWidget(self._unlock_lbl)

        # Current theme label
        self._theme_label = QLabel("  Theme: Panda Dark  ")
        self._theme_label.setObjectName("subheader")
        corner_layout.addWidget(self._theme_label)

        # SVG theme badge (decorative – shows animated SVG for the active theme)
        self._svg_badge = self._make_svg_badge()
        if self._svg_badge is not None:
            corner_layout.addWidget(self._svg_badge)

        # ⚙ Settings button
        btn_settings = QPushButton("⚙ Settings")
        btn_settings.setToolTip("Open Settings (Ctrl+,)")
        btn_settings.clicked.connect(self._open_settings)
        corner_layout.addWidget(btn_settings)
        self._btn_settings = btn_settings

        # ❓ Help button – opens a dropdown with shortcuts/about/export/import
        btn_help = QPushButton("❓ Help")
        btn_help.setToolTip("Keyboard shortcuts, About, Export/Import settings")
        btn_help.clicked.connect(self._show_help_menu)
        corner_layout.addWidget(btn_help)
        self._btn_help = btn_help

        # ❤ Patreon button
        btn_patreon = QPushButton("❤ Patreon")
        btn_patreon.setToolTip(
            "Support development on Patreon!\n"
            "patreon.com/c/DeadOnTheInside"
        )
        btn_patreon.clicked.connect(self._open_patreon)
        corner_layout.addWidget(btn_patreon)
        self._btn_patreon = btn_patreon

        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        self.setCentralWidget(central)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready  🐼")

        # Toolbar panda label no longer used (toolbar removed); keep None so
        # _refresh_toolbar_icon() early-returns without errors.
        self._toolbar_panda_lbl = None

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

        # Button press animator
        from .click_effects import ButtonPressAnimator
        self._button_anim = ButtonPressAnimator(self, self._click_effects)
        self._apply_button_anim()

        # Connect processing-done signals so file processing can unlock themes
        self._alpha_tab.processing_done.connect(self._on_processing_done)
        self._converter_tab.processing_done.connect(self._on_processing_done)
        # Processing-error sounds
        self._alpha_tab.processing_error.connect(self._on_processing_error)
        self._converter_tab.processing_error.connect(self._on_processing_error)
        # Processing-started sounds
        self._alpha_tab.processing_started.connect(self._on_processing_started)
        self._converter_tab.processing_started.connect(self._on_processing_started)
        # First-use unlock triggers
        self._alpha_tab.first_alpha_fix.connect(self._on_first_alpha_fix)
        self._converter_tab.first_conversion.connect(self._on_first_conversion)
        # File-add sounds
        self._alpha_tab.files_added.connect(self._on_files_added)
        self._converter_tab.files_added.connect(self._on_files_added)
        # File-remove sounds
        self._alpha_tab.files_removed.connect(self._on_files_removed)
        self._converter_tab.files_removed.connect(self._on_files_removed)
        # Preview-refresh sounds
        self._alpha_tab.preview_refreshed.connect(self._on_preview_refreshed)

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
        mgr.register(self._btn_help, "help_btn")
        mgr.register(self._btn_patreon, "patreon_btn")
        # Register per-tab tooltips on the QTabBar
        mgr.register_tab_bar(
            self._tabs.tabBar(),
            ["alpha_fixer_tab", "converter_tab", "history_tab", "selective_alpha_tab"],
        )
        self._alpha_tab.register_tooltips(mgr)
        self._converter_tab.register_tooltips(mgr)
        self._history_tab.register_tooltips(mgr)
        self._selective_alpha_tab.register_tooltips(mgr)

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

    def _apply_button_anim(self) -> None:
        """Enable or disable button press animations to match the active settings."""
        if self._button_anim is None:
            return
        enabled = self._settings.get("button_anim_enabled", False)
        if not enabled:
            self._button_anim.set_enabled(False)
            return
        theme = self._settings.get_theme()
        if self._settings.get("use_theme_button_anim", True):
            mode = theme.get("_button_anim", "press")
        else:
            mode = self._settings.get("button_anim_style", "press")
        self._button_anim.set_enabled(True, mode)

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
        # _UNLOCK_TABLE is a class constant (sorted ascending by threshold).
        # The break condition `threshold > total` is equivalent to the original
        # `total >= threshold` guard, with the addition that we skip the rest of
        # the table once no further entry can fire — avoiding iterating all 33
        # entries on every click for users who have not yet reached many thresholds.
        newly_unlocked = False
        for threshold, key, message in self._UNLOCK_TABLE:
            if threshold > total:
                # Remaining entries all have higher thresholds; none can fire.
                break
            if not self._settings.get(key, False):
                # Threshold reached and not yet unlocked.
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

    def _on_files_removed(self) -> None:
        """Play a short pop when files are removed from either tab's queue."""
        try:
            self._sound.play_file_remove()
        except Exception:
            pass

    def _on_processing_started(self) -> None:
        """Play an ascending two-tone cue when a batch starts processing."""
        try:
            self._sound.play_process_start()
        except Exception:
            pass

    def _on_processing_error(self, error_count: int) -> None:
        """Play an error buzz when a batch finishes with failures."""
        try:
            self._sound.play_error()
        except Exception:
            pass

    def _on_preview_refreshed(self) -> None:
        """Play a subtle ping when the live preview refreshes (opt-in, off by default)."""
        try:
            self._sound.play_preview()
        except Exception:
            pass

    def _schedule_unlock_clear(self) -> None:
        """Start (or restart) a one-shot timer that clears the unlock label."""
        from PyQt6.QtCore import QTimer
        if self._unlock_timer is None:
            self._unlock_timer = QTimer(self)
            self._unlock_timer.setSingleShot(True)
            # Use a named method instead of a lambda so the callback is safe
            # even if the window starts closing before the 6-second timeout fires.
            self._unlock_timer.timeout.connect(self._clear_unlock_label)
        self._unlock_timer.start(6000)

    def _clear_unlock_label(self) -> None:
        """Clear the unlock notification label.  Guards against the label being
        None (destroyed) if the timer fires during window teardown."""
        if self._unlock_lbl is not None:
            self._unlock_lbl.setText("")

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
        # Update the banner emoji widget to the theme's representative icon.
        icon = get_theme_icon(theme_name)
        animated = self._settings.get("animated_banner_enabled", False)
        # Determine the animation mode: use theme's _banner_anim if the
        # "Use theme animation" setting is on, otherwise the manual setting.
        if self._settings.get("banner_use_theme_anim", True):
            anim_mode = theme.get("_banner_anim", "spin")
        else:
            anim_mode = self._settings.get("banner_anim_style", "spin")
        # "flock" mode: keep emoji widgets static; activate a banner flock on
        # the click-effects overlay so themed emoji fly across the top of the
        # window periodically (independent of click-effect enable state).
        if anim_mode == "flock":
            if self._banner_emoji_left is not None:
                self._banner_emoji_left.set_emoji(icon)
                self._banner_emoji_left.set_mode("static")
                self._banner_emoji_left.set_animated(False)
            if self._banner_emoji_right is not None:
                self._banner_emoji_right.set_emoji(icon)
                self._banner_emoji_right.set_mode("static")
                self._banner_emoji_right.set_animated(False)
            if self._click_effects is not None:
                trail_color = theme.get("_trail_color", "#e94560")
                self._click_effects.set_banner_flock(animated, icon, trail_color)
        else:
            if self._banner_emoji_left is not None:
                self._banner_emoji_left.set_emoji(icon)
                self._banner_emoji_left.set_mode(anim_mode)
                self._banner_emoji_left.set_animated(animated)
            if self._banner_emoji_right is not None:
                self._banner_emoji_right.set_emoji(icon)
                self._banner_emoji_right.set_mode(anim_mode)
                self._banner_emoji_right.set_animated(animated)
            if self._click_effects is not None:
                self._click_effects.set_banner_flock(False, icon, "#e94560")
        # Keep static text label; update it to the theme banner (without emojis)
        if self._banner_lbl is not None:
            self._banner_lbl.setText("Alpha & RGBA Adjuster  |  File Converter")
        # Stop any legacy animation timer (banner no longer cycles emojis)
        if self._anim_timer is not None:
            self._anim_timer.stop()
        # Store theme-specific tab labels; update tab text directly (no spinner).
        self._tab_base_labels = get_theme_tab_labels(theme_name)
        self._update_tab_labels()
        # Update inner tab headers to also reflect the active theme
        self._alpha_tab.update_theme(theme_name)
        self._converter_tab.update_theme(theme_name)
        self._history_tab.update_theme(theme_name)
        # Update status bar with per-theme flavor message
        if self._status_bar is not None:
            self._status_bar.showMessage(get_theme_status(theme_name))
        # Re-apply cursor so theme-cursor mode updates immediately on theme change
        self._apply_cursor()
        # Update window icon and taskbar icon to match the current theme SVG
        self._refresh_window_icon(theme_name)
        # Update toolbar icon to match the current theme
        self._refresh_toolbar_icon(theme_name)
        # Refresh SVG badge to match new theme
        self._refresh_svg_badge()
        # Keep trail and click-effects in sync with the active theme.
        # These overlays are created in _setup_effects() which runs after the
        # first _apply_theme() call, so guard with None checks.
        if self._trail_overlay is not None:
            self._apply_trail()
        if self._click_effects is not None:
            self._apply_theme_effect()
        if self._button_anim is not None:
            self._apply_button_anim()
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
                    lbl.setToolTip("Alpha && RGBA Adjuster  |  File Converter 🐼")
                    lbl.setContentsMargins(4, 0, 4, 0)
                    return lbl
                except Exception:
                    pass
                break
        # Fallback: plain text panda emoji
        lbl = QLabel("🐼")
        lbl.setToolTip("Alpha && RGBA Adjuster  |  File Converter 🐼")
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
        """Update the SVG badge to show the decoration for the current theme.

        The badge is hidden when animated_banner_enabled is False because the
        SVG files themselves contain <animate> elements – showing them while
        animations are disabled would be misleading.
        """
        if self._svg_badge is None:
            return
        # Hide badge entirely when animations are disabled
        if not self._settings.get("animated_banner_enabled", False):
            self._svg_badge.hide()
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

    def _refresh_toolbar_icon(self, theme_name: str) -> None:
        """Update the toolbar icon label to show the active theme's graphic.

        Tries to render the theme's SVG at 28×28 first; falls back to the
        theme's representative emoji when SVG rendering is unavailable.
        """
        if self._toolbar_panda_lbl is None:
            return
        svg_path = get_theme_svg_path(theme_name)
        if svg_path:
            try:
                from PyQt6.QtSvg import QSvgRenderer
                renderer = QSvgRenderer(svg_path)
                pix = QPixmap(28, 28)
                pix.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pix)
                if painter.isActive():
                    renderer.render(painter)
                    painter.end()
                    self._toolbar_panda_lbl.setPixmap(pix)
                    self._toolbar_panda_lbl.setText("")
                    self._toolbar_panda_lbl.setToolTip(f"{theme_name} theme")
                    return
            except Exception:
                pass
        # Fallback: plain emoji text
        icon = get_theme_icon(theme_name)
        self._toolbar_panda_lbl.setPixmap(QPixmap())
        self._toolbar_panda_lbl.setText(icon)
        self._toolbar_panda_lbl.setToolTip(f"{theme_name} theme")

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
        dlg.theme_changed.connect(lambda t: self._on_settings_changed())
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
        """Schedule a deferred re-apply of all effect-related settings.

        The signal can fire very rapidly (e.g. every step of a spinbox or
        slider drag).  Restarting a 200 ms single-shot timer each time
        collapses bursts of signals into a single apply call, eliminating
        the per-change setStyleSheet / icon-refresh lag.
        """
        self._settings_apply_timer.start()

    def _apply_settings_now(self):
        """Re-apply all effect-related settings (called via debounce timer)."""
        self._apply_theme()
        self._apply_cursor()
        self._apply_font_size()
        self._apply_theme_effect()
        self._apply_trail()
        self._apply_button_anim()
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
            # Use the explicit _trail key added to every theme dict.
            # Fall back to the legacy _effect → style mapping for any custom
            # themes that were saved before the _trail key was introduced.
            if "_trail" in theme:
                style = theme["_trail"]
            else:
                effect = theme.get("_effect", "default")
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
            "About 🐼 Alpha & RGBA Adjuster  |  File Converter",
            f"<h2>🐼 Alpha & RGBA Adjuster  |  File Converter  v{__version__}</h2>"
            "<p>A panda-themed tool for fixing alpha channels and converting image files.</p>"
            "<ul>"
            "<li><b>Alpha &amp; RGBA Adjuster:</b> PS2, N64, No Alpha, Max Alpha presets + custom</li>"
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

    def _show_help_menu(self):
        """Show a popup menu from the Help button with shortcuts, about, and I/O options."""
        menu = QMenu(self)
        act_shortcuts = menu.addAction("⌨  Keyboard Shortcuts  (F1)")
        act_shortcuts.triggered.connect(self._show_shortcuts)
        act_about = menu.addAction("ℹ  About")
        act_about.triggered.connect(self._show_about)
        menu.addSeparator()
        act_patreon = menu.addAction("❤  Support on Patreon…")
        act_patreon.triggered.connect(self._open_patreon)
        menu.addSeparator()
        act_export = menu.addAction("📤  Export Settings…")
        act_export.triggered.connect(self._export_settings)
        act_import = menu.addAction("📥  Import Settings…")
        act_import.triggered.connect(self._import_settings)
        # Show the menu just below the Help button
        btn = self._btn_help
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        menu.exec(pos)



    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Debounce overlay repositioning: during an interactive window drag,
        # Qt fires resizeEvent on every pixel of movement.  Repositioning
        # overlays immediately each time spends unnecessary GPU/CPU on geometry
        # recalculations.  Schedule a single coalesced update 50ms after the
        # last resize event instead.  The overlays also self-correct via their
        # own eventFilter (QEvent.Type.Resize on the main window), which provides
        # the immediate fine-grained correction; the timer fires for any cases
        # where the eventFilter is not installed (e.g., effects disabled).
        self._resize_timer.start()

    def _reposition_overlays(self) -> None:
        """Reposition both overlays to fill the window after a resize burst."""
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
        # Disable overlays first so their event filters are unregistered and
        # their internal timers (animation, bat/fairy flock, etc.) are stopped
        # before any Qt objects start being torn down.
        if self._click_effects is not None:
            self._click_effects.set_enabled(False)
        if self._trail_overlay is not None:
            self._trail_overlay.set_enabled(False)
        if self._button_anim is not None:
            self._button_anim.set_enabled(False)
        # Stop any running workers gracefully
        for tab in (self._alpha_tab, self._converter_tab):
            if hasattr(tab, "_worker") and tab._worker and tab._worker.isRunning():
                tab._worker.stop()
                tab._worker.wait(3000)
            # Cancel any in-flight preview loaders so their threads don't
            # try to emit signals into already-destroyed Qt objects.
            if hasattr(tab, "_preview_loader") and tab._preview_loader is not None:
                tab._preview_loader.stop()
            # Stop preview debounce timers so pending timeouts don't fire
            # after the tab widgets have been torn down.
            if hasattr(tab, "_preview_debounce") and tab._preview_debounce is not None:
                tab._preview_debounce.stop()
        # Stop main-window timers before the window is destroyed
        for timer in (
            self._settings_apply_timer,
            self._resize_timer,
            self._unlock_timer,
            self._anim_timer,
        ):
            if timer is not None:
                timer.stop()
        # Clean up temp sound file
        if self._sound is not None:
            self._sound.cleanup()
        self._save_geometry()
        # Flush any buffered QSettings writes to disk before closing.
        # This is the one place we explicitly sync since set() no longer
        # calls sync() after every write (which caused per-click disk I/O).
        # Save Selective Alpha Tool state first so it is included in the sync.
        try:
            self._selective_alpha_tab._save_settings()
        except Exception:
            pass
        try:
            self._settings.sync()
        except Exception:
            pass
        super().closeEvent(event)

