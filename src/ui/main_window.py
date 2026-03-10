"""
Main application window.
"""
import webbrowser

from PyQt6.QtCore import Qt, QSize, QRect, QTimer
from PyQt6.QtGui import QAction, QCursor, QFont, QIcon, QPixmap, QPainter
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QToolBar,
    QLabel, QPushButton, QWidget, QVBoxLayout, QApplication,
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
    get_theme_banner_frames,
)
from ..version import __version__

PATREON_URL = "https://www.patreon.com/c/DeadOnTheInside"

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


def _make_emoji_cursor(emoji: str, size: int = 32) -> QCursor:
    """Render *emoji* into a square pixmap and return a QCursor from it.

    The hotspot is placed at the top-left corner so the cursor tip lines up
    with the pointer position.  Falls back to the arrow cursor if pixmap
    painting is unavailable (e.g. running headless without a display).
    """
    try:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        # Use a font stack that covers Windows (Segoe UI Emoji), macOS (Apple Color Emoji),
        # and Linux (Noto Color Emoji) so the emoji renders on every platform.
        font = QFont("Apple Color Emoji, Segoe UI Emoji, Noto Color Emoji", max(6, size - 6))
        painter.setFont(font)
        painter.drawText(
            QRect(0, 0, size, size),
            Qt.AlignmentFlag.AlignCenter,
            emoji,
        )
        painter.end()
        return QCursor(pix, 0, 0)
    except Exception:
        return QCursor(Qt.CursorShape.ArrowCursor)


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
        self._status_bar = None
        self._unlock_timer = None
        self._anim_timer = None    # drives banner emoji cycling
        self._banner_frames: list[str] = []
        self._banner_frame_idx: int = 0
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
        # Set the panda SVG as the window icon (used in title bar + taskbar)
        self._set_panda_window_icon()

    def _set_panda_window_icon(self):
        """Render panda_dark.svg to a QPixmap and use it as the window/app icon."""
        import os
        svg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "svg")
        for candidate in ("panda_dark.svg", "panda_light.svg"):
            svg_path = os.path.normpath(os.path.join(svg_dir, candidate))
            if os.path.isfile(svg_path):
                try:
                    from PyQt6.QtSvg import QSvgRenderer
                    renderer = QSvgRenderer(svg_path)
                    pix = QPixmap(64, 64)
                    pix.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(pix)
                    renderer.render(painter)
                    painter.end()
                    self.setWindowIcon(QIcon(pix))
                    QApplication.setWindowIcon(QIcon(pix))
                    return
                except Exception:
                    pass
                break

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

        # Panda banner — enable word-wrap so long animated theme banners wrap
        # cleanly rather than being clipped on smaller windows.
        banner = QLabel("🐼  Alpha Fixer  &  File Converter")
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.setObjectName("header")
        banner.setStyleSheet("padding: 10px; font-size: 20px;")
        banner.setWordWrap(True)
        banner.setMinimumHeight(44)
        cv.addWidget(banner)
        self._banner_lbl = banner

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
        self._theme_label.setMinimumWidth(160)  # prevent squishing on long theme names
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
        effects_enabled = self._settings.get("click_effects_enabled", True)
        self._click_effects.set_enabled(effects_enabled)
        self._click_effects.click_registered.connect(self._check_unlocks)
        self._apply_theme_effect()

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
        self._alpha_tab.register_tooltips(mgr)
        self._converter_tab.register_tooltips(mgr)

    def _apply_theme_effect(self):
        """Set the click-effects overlay to match the active theme's effect key."""
        if self._click_effects is None:
            return
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "Panda Dark")
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
        """Check whether any hidden theme should be unlocked."""
        try:
            total = self._settings.get("total_clicks", 0) + 1
            self._settings.set("total_clicks", total)
        except Exception:
            return

        newly_unlocked = False

        # Secret Skeleton unlocks at 100 total clicks
        already_unlocked = self._settings.get("unlock_skeleton", False)
        if not already_unlocked and total >= 100:
            self._settings.set("unlock_skeleton", True)
            self._unlock_lbl.setText("🔓 'Secret Skeleton' theme unlocked! (Settings → Theme)")
            try:
                QApplication.instance().beep()
            except Exception:
                pass
            newly_unlocked = True

        # Secret Sakura unlocks at 250 total clicks
        already_sakura = self._settings.get("unlock_sakura", False)
        if not already_sakura and total >= 250:
            self._settings.set("unlock_sakura", True)
            self._unlock_lbl.setText("🌸 'Secret Sakura' theme unlocked! (Settings → Theme)")
            try:
                QApplication.instance().beep()
            except Exception:
                pass
            newly_unlocked = True

        # Deep Ocean unlocks at 500 total clicks
        already_ocean = self._settings.get("unlock_ocean", False)
        if not already_ocean and total >= 500:
            self._settings.set("unlock_ocean", True)
            self._unlock_lbl.setText("🌊 'Deep Ocean' theme unlocked! (Settings → Theme)")
            try:
                QApplication.instance().beep()
            except Exception:
                pass
            newly_unlocked = True

        # Auto-clear the unlock banner after 6 seconds
        if newly_unlocked:
            self._schedule_unlock_clear()

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
            shape = _CURSOR_MAP.get(cursor_name, Qt.CursorShape.ArrowCursor)
            self.setCursor(QCursor(shape))

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
        QApplication.instance().setStyleSheet(build_stylesheet(theme))
        theme_name = theme.get("name", "Custom")
        self._theme_label.setText(f"  Theme: {theme_name}  ")
        # Set up animated banner frames and restart animation timer
        self._banner_frames = get_theme_banner_frames(theme_name)
        self._banner_frame_idx = 0
        if self._banner_lbl is not None:
            self._banner_lbl.setText(self._banner_frames[0])
        self._restart_banner_anim()
        # Update status bar with per-theme flavor message
        if self._status_bar is not None:
            self._status_bar.showMessage(get_theme_status(theme_name))
        # Re-apply cursor so theme-cursor mode updates immediately on theme change
        self._apply_cursor()
        # Refresh SVG badge to match new theme
        self._refresh_svg_badge()
        # Keep trail and click-effects in sync with the active theme.
        # These overlays are created in _setup_effects() which runs after the
        # first _apply_theme() call, so guard with None checks.
        if self._trail_overlay is not None:
            self._apply_trail()
        if self._click_effects is not None:
            self._apply_theme_effect()

    def _restart_banner_anim(self) -> None:
        """Start (or restart) the banner animation timer based on the current theme's frames."""
        # Stop any previous timer
        if self._anim_timer is not None:
            self._anim_timer.stop()
        # Only animate when there are multiple frames
        if len(self._banner_frames) <= 1:
            return
        if self._anim_timer is None:
            self._anim_timer = QTimer(self)
            self._anim_timer.timeout.connect(self._tick_banner_anim)
        self._anim_timer.start(800)  # advance frame every 800 ms

    def _tick_banner_anim(self) -> None:
        """Advance to the next banner frame for the current theme."""
        if not self._banner_frames or self._banner_lbl is None:
            return
        self._banner_frame_idx = (self._banner_frame_idx + 1) % len(self._banner_frames)
        self._banner_lbl.setText(self._banner_frames[self._banner_frame_idx])

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
            badge.setFixedSize(32, 32)
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

        # Attach a click-effects overlay to the dialog so particle effects are
        # visible while the settings window is open (the main overlay is behind
        # the modal and therefore not visible).
        dlg_overlay = None
        if (self._click_effects is not None
                and self._settings.get("click_effects_enabled", True)):
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
                self._settings.get("click_effects_enabled", True)
            )

    def _apply_trail(self):
        """Apply trail color, style and enabled state, honouring use_theme_trail."""
        if self._trail_overlay is None:
            return
        use_theme = self._settings.get("use_theme_trail", False)
        if use_theme:
            theme = self._settings.get_theme()
            color = theme.get("_trail_color", "#e94560")
            # Fairy Garden gets fairy dust emoji trail style
            style = "fairy" if theme.get("_effect") == "fairy" else "dots"
        else:
            color = self._settings.get("trail_color", "#e94560")
            style = "dots"
        self._trail_overlay.set_color(color)
        self._trail_overlay.set_style(style)
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
            "<li><b>Converter:</b> PNG, DDS, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF</li>"
            "<li>Drag-and-drop + batch folder/subfolder processing</li>"
            "<li>Before/after comparison slider preview</li>"
            "<li>Image preview, conversion history, export/import settings</li>"
            "<li>12 preset themes + 2 hidden unlockables (keep clicking to find them!)</li>"
            "<li>14 click effects: Gore 🩸, Bat Cave 🦇, Rainbow 🌈, Galaxy ✦, Neon ⚡, Fire 🔥, Ice ❄, Panda 🐼, Sakura 🌸, and more…</li>"
            "<li>Theme cursor: automatically applies a matching cursor per theme (Otter Cove → 🤘)</li>"
            "<li>Cycling tooltips with Normal, Dumbed Down, and No Filter 🤬 modes</li>"
            "<li>Keyboard shortcuts: F5 run · Esc stop · Ctrl+O add files · F1 help</li>"
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

