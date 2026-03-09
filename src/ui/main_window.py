"""
Main application window.
"""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QIcon, QFont
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QToolBar,
    QLabel, QPushButton, QWidget, QVBoxLayout, QApplication,
    QMessageBox,
)

from ..core.settings_manager import SettingsManager
from ..core.presets import PresetManager
from .alpha_tool import AlphaFixerTab
from .converter_tool import ConverterTab
from .settings_dialog import SettingsDialog
from .theme_engine import build_stylesheet


class MainWindow(QMainWindow):
    def __init__(self, settings: SettingsManager):
        super().__init__()
        self._settings = settings
        self._preset_mgr = PresetManager(settings)
        self._setup_window()
        self._setup_ui()
        self._restore_geometry()
        self._apply_theme()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle("🐼 Alpha Fixer & File Converter")
        self.setMinimumSize(800, 600)

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

        help_menu = menubar.addMenu("Help")
        act_about = QAction("About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

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
        self._tabs.addTab(self._alpha_tab, "🖼  Alpha Fixer")
        self._tabs.addTab(self._converter_tab, "🔄  Converter")
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
        toolbar.addSeparator()

        self._theme_label = QLabel("  Theme: Panda Dark  ")
        self._theme_label.setObjectName("subheader")
        toolbar.addWidget(self._theme_label)

    # ------------------------------------------------------------------
    # Geometry / state
    # ------------------------------------------------------------------

    def _restore_geometry(self):
        if self._settings.get("window_maximized", False):
            self.showMaximized()
            return
        x = self._settings.get("window_x", 100)
        y = self._settings.get("window_y", 100)
        w = self._settings.get("window_w", 1100)
        h = self._settings.get("window_h", 750)
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
    # Dialogs
    # ------------------------------------------------------------------

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self)
        dlg.theme_changed.connect(lambda t: self._apply_theme())
        dlg.exec()

    def _show_about(self):
        QMessageBox.about(
            self,
            "About 🐼 Alpha Fixer & File Converter",
            "<h2>🐼 Alpha Fixer & File Converter</h2>"
            "<p>A panda-themed tool for fixing alpha channels and converting image files.</p>"
            "<ul>"
            "<li><b>Alpha Fixer:</b> PS2, N64, No Alpha, Max Alpha presets + custom</li>"
            "<li><b>Converter:</b> PNG, DDS, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF</li>"
            "<li>Batch processing with folder/subfolder support</li>"
            "<li>Customizable panda themes</li>"
            "</ul>"
            "<p>Built with Python + PyQt6 + Pillow.</p>",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        # Stop any running workers gracefully
        for tab in (self._alpha_tab, self._converter_tab):
            if hasattr(tab, "_worker") and tab._worker and tab._worker.isRunning():
                tab._worker.stop()
                tab._worker.wait(3000)
        self._save_geometry()
        super().closeEvent(event)
