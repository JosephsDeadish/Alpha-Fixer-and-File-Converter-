#!/usr/bin/env python3
"""
Alpha Fixer & File Converter – Entry Point.

Includes:
  • Pre-flight system-library check (libEGL, libGL) with clear install instructions
  • Global exception handling so uncaught errors show a dialog instead of crashing
  • Crash logging with automatic rotation (logs stored next to the exe/main.py)
  • Qt environment flags for compatibility on both good and bad hardware
"""
import sys
import os
import traceback
import logging
import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging configuration  (done early so even pre-Qt errors are logged)
# ---------------------------------------------------------------------------

def _log_dir() -> Path:
    """Return the directory for log files.

    Priority:
    1. Next to the frozen executable (PyInstaller .exe)  →  <exe_dir>/logs/
    2. Next to main.py when running from source          →  <project_root>/logs/

    This keeps logs alongside the settings INI file so everything the app
    writes is in one easy-to-find place next to the executable.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    d = base / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


LOG_DIR = _log_dir()

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
# Pre-flight: verify system libraries required by PyQt6
# ---------------------------------------------------------------------------

_LINUX_INSTALL = {
    "libEGL.so.1": {
        "debian":   "sudo apt-get install -y libegl1",
        "fedora":   "sudo dnf install -y mesa-libEGL",
        "arch":     "sudo pacman -S mesa",
        "opensuse": "sudo zypper install -y libEGL1",
        "generic":  "Install the Mesa EGL library for your distribution",
    },
    "libGL.so.1": {
        "debian":   "sudo apt-get install -y libgl1",
        "fedora":   "sudo dnf install -y mesa-libGL",
        "arch":     "sudo pacman -S mesa",
        "opensuse": "sudo zypper install -y libGL1",
        "generic":  "Install the Mesa GL library for your distribution",
    },
    "libGLES": {
        "debian":   "sudo apt-get install -y libgles2",
        "fedora":   "sudo dnf install -y mesa-libGLES",
        "arch":     "sudo pacman -S mesa",
        "opensuse": "sudo zypper install -y libGLESv2-2",
        "generic":  "Install the Mesa GLES library for your distribution",
    },
    "libpulse.so.0": {
        "debian":   "sudo apt-get install -y libpulse0",
        "fedora":   "sudo dnf install -y pulseaudio-libs",
        "arch":     "sudo pacman -S libpulse",
        "opensuse": "sudo zypper install -y libpulse0",
        "generic":  "Install the PulseAudio client library (libpulse) for your distribution",
    },
}


def _detect_distro() -> str:
    """Return a simple distribution key for install command lookup."""
    try:
        import distro  # optional third-party package
        name = distro.id().lower()
    except ImportError:
        # Fall back to /etc/os-release
        name = ""
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("ID="):
                        name = line.split("=", 1)[1].strip().strip('"').lower()
                        break
        except OSError:
            pass

    if name in ("ubuntu", "debian", "linuxmint", "pop", "elementary"):
        return "debian"
    if name in ("fedora", "rhel", "centos", "rocky", "alma"):
        return "fedora"
    if name in ("arch", "manjaro", "endeavouros"):
        return "arch"
    if name in ("opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles"):
        return "opensuse"
    return "generic"


def _check_system_libs() -> bool:
    """
    Try to import PyQt6's core module. If it fails due to a missing shared
    library, print a clear error with distro-specific install commands and
    return False so the caller can exit cleanly.
    """
    if sys.platform != "linux":
        # On Windows / macOS the required DLLs are bundled with PyQt6-Qt6
        return True

    try:
        from PyQt6.QtCore import QCoreApplication  # noqa: F401 – just a probe
        return True
    except ImportError as exc:
        err = str(exc)
        logger.critical("PyQt6 import failed: %s", err)

        # Match the missing library name from the error message
        matched_lib = None
        for lib_key in _LINUX_INSTALL:
            if lib_key.rstrip(".0123456789") in err:
                matched_lib = lib_key
                break

        print("\n" + "=" * 62)
        print("  ERROR: A required system library is missing.")
        print("=" * 62)
        print(f"\n  Missing: {err}")

        distro = _detect_distro()
        if matched_lib:
            cmd = _LINUX_INSTALL[matched_lib].get(distro) or _LINUX_INSTALL[matched_lib]["generic"]
            print(f"\n  Install it with:\n\n    {cmd}\n")
        else:
            print("\n  Install all required Qt system libraries by running:\n")
            print("    bash scripts/install_linux_deps.sh\n")

        print("  Then run the application again.\n")
        print(f"  Full error logged to: {log_file}")
        print("=" * 62 + "\n")
        return False


# ---------------------------------------------------------------------------
# Qt environment setup (must be before QApplication)
# ---------------------------------------------------------------------------

os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
# Software rasterizer fallback for hardware without proper OpenGL / EGL
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
    # Run the pre-flight check before anything else
    if not _check_system_libs():
        sys.exit(1)

    # Add src to path so relative imports work when run directly
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    parent_dir = os.path.dirname(src_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QCoreApplication

    QCoreApplication.setApplicationName("AlphaFixerConverter")
    QCoreApplication.setOrganizationName("PandaTools")
    # AA_UseHighDpiPixmaps was removed in Qt6; high-DPI pixmaps are always
    # enabled by default in Qt6/PyQt6 so no setAttribute call is needed.

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
    from src.ui.splash_screen import ThemeSplashScreen

    settings = SettingsManager()

    # Show animated themed splash screen
    splash = ThemeSplashScreen(settings)
    splash.show()
    app.processEvents()

    window = MainWindow(settings)

    # Close splash and reveal main window after the splash duration
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(2800, lambda: splash.finish_and_close(window))

    window.show()

    logger.info("Main window shown.")
    exit_code = app.exec()
    logger.info("Application exited with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
