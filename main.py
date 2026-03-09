#!/usr/bin/env python3
"""
Alpha Fixer & File Converter – Entry Point.

Includes global exception handling, crash logging, and Qt-specific
workarounds to ensure the application never silently freezes or crashes.
"""
import sys
import os
import traceback
import logging
import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path.home() / ".alpha_fixer_converter" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_file = LOG_DIR / f"app_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(log_file), encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# Keep only the last 10 log files
existing_logs = sorted(LOG_DIR.glob("app_*.log"))
for old in existing_logs[:-10]:
    try:
        old.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Qt environment setup (must be before QApplication)
# ---------------------------------------------------------------------------

os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
# Software rasterizer fallback for hardware without proper OpenGL
os.environ.setdefault("QT_OPENGL", "software")


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

def _excepthook(exc_type, exc_value, exc_tb):
    """Log uncaught exceptions and show a friendly dialog instead of crashing silently."""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical("Uncaught exception:\n%s", msg)

    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app:
            box = QMessageBox()
            box.setWindowTitle("Unexpected Error 🐼")
            box.setText(
                "An unexpected error occurred. The application will try to continue.\n\n"
                f"Details logged to:\n{log_file}"
            )
            box.setDetailedText(msg)
            box.setIcon(QMessageBox.Icon.Critical)
            box.exec()
    except Exception:
        pass


sys.excepthook = _excepthook


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Add src to path so relative imports work when run directly
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    parent_dir = os.path.dirname(src_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt, QCoreApplication

    QCoreApplication.setApplicationName("AlphaFixerConverter")
    QCoreApplication.setOrganizationName("PandaTools")
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Consistent baseline across all platforms

    # Enable smooth font rendering
    from PyQt6.QtGui import QFont
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    app.setFont(font)

    logger.info("Starting Alpha Fixer & File Converter")

    from src.core.settings_manager import SettingsManager
    from src.ui.main_window import MainWindow

    settings = SettingsManager()
    window = MainWindow(settings)
    window.show()

    logger.info("Main window shown.")
    exit_code = app.exec()
    logger.info("Application exited with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
