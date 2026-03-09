"""
Main application window.
"""
import webbrowser

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QCursor, QFont, QIcon
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
from .theme_engine import build_stylesheet, PRESET_THEMES, HIDDEN_THEMES, THEME_EFFECTS
from ..version import __version__

PATREON_URL = "https://www.patreon.com/c/DeadOnTheInside"

_CURSOR_MAP = {
    "Default":       Qt.CursorShape.ArrowCursor,
    "Cross":         Qt.CursorShape.CrossCursor,
    "Pointing Hand": Qt.CursorShape.PointingHandCursor,
    "Open Hand":     Qt.CursorShape.OpenHandCursor,
}


class MainWindow(QMainWindow):
    def __init__(self, settings: SettingsManager):
        super().__init__()
        self._settings = settings
        self._preset_mgr = PresetManager(settings)
        self._trail_overlay = None
        self._click_effects = None
        self._tooltip_mgr = None
        self._sound = None
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
        self.setMinimumSize(950, 700)

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

        # Panda banner
        banner = QLabel("🐼  Alpha Fixer  &  File Converter")
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.setObjectName("header")
        banner.setStyleSheet("padding: 10px; font-size: 20px;")
        cv.addWidget(banner)

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
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(toolbar)

        btn_settings = QPushButton("⚙ Settings")
        btn_settings.clicked.connect(self._open_settings)
        toolbar.addWidget(btn_settings)
        self._btn_settings = btn_settings
        toolbar.addSeparator()

        self._theme_label = QLabel("  Theme: Panda Dark  ")
        self._theme_label.setObjectName("subheader")
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

    # ------------------------------------------------------------------
    # Visual / audio effects (trail, cursor, sound, click effects, tooltips)
    # ------------------------------------------------------------------

    def _setup_effects(self):
        # Mouse trail overlay
        from .mouse_trail import MouseTrailOverlay
        self._trail_overlay = MouseTrailOverlay(self)
        self._trail_overlay.setGeometry(self.rect())
        self._trail_overlay.raise_()

        trail_enabled = self._settings.get("trail_enabled", False)
        trail_color = self._settings.get("trail_color", "#e94560")
        self._trail_overlay.set_color(trail_color)
        self._trail_overlay.set_enabled(trail_enabled)

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
        # THEME_EFFECTS covers all preset themes; fall back to theme's own _effect
        # key for user-saved custom themes (not in the preset registry).
        effect_key = THEME_EFFECTS.get(theme_name) or theme.get("_effect", "default")
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
        total = self._settings.get("total_clicks", 0) + 1
        self._settings.set("total_clicks", total)

        # Secret Skeleton unlocks at 100 total clicks
        already_unlocked = self._settings.get("unlock_skeleton", False)
        if not already_unlocked and total >= 100:
            self._settings.set("unlock_skeleton", True)
            self._unlock_lbl.setText("🔓 'Secret Skeleton' theme unlocked! (Settings → Theme)")
            QApplication.instance().beep()

        # Secret Sakura unlocks at 250 total clicks
        already_sakura = self._settings.get("unlock_sakura", False)
        if not already_sakura and total >= 250:
            self._settings.set("unlock_sakura", True)
            self._unlock_lbl.setText("🌸 'Secret Sakura' theme unlocked! (Settings → Theme)")
            QApplication.instance().beep()

    def _apply_cursor(self):
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
        self._theme_label.setText(f"  Theme: {theme.get('name', 'Custom')}  ")

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
        dlg.exec()

    def _on_settings_changed(self):
        """Re-apply all effect-related settings after the dialog closes."""
        self._apply_theme()
        self._apply_cursor()
        self._apply_font_size()
        self._apply_theme_effect()
        if self._trail_overlay is not None:
            self._trail_overlay.set_color(
                self._settings.get("trail_color", "#e94560")
            )
            self._trail_overlay.set_enabled(
                self._settings.get("trail_enabled", False)
            )
        if self._click_effects is not None:
            self._click_effects.set_enabled(
                self._settings.get("click_effects_enabled", True)
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
            "<li>13 click effects: Gore 🩸, Bat Cave 🦇, Rainbow 🌈, Galaxy ✦, Neon ⚡, Fire 🔥, Ice ❄, Panda 🐼, and more…</li>"
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

