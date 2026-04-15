#!/usr/bin/env python3
"""
Alpha Fixer & File Converter – Entry Point.

Includes:
  • Pre-flight system-library check (libEGL, libGL) with clear install instructions
  • Single-instance guard (QLockFile) – warns the user if the app is already open
  • Global exception handling so uncaught errors show a dialog instead of crashing
  • Crash logging with timestamped log files (logs stored next to the exe/main.py)
  • Qt environment flags for HiDPI scaling and compatibility on both good and bad hardware
"""
import sys
import os
import traceback
import logging
import datetime
import threading
import time
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
# Preserve fractional DPI scale factors (e.g. 125 %, 150 %) rather than
# rounding to the nearest integer.  This produces sharper rendering on
# HiDPI displays that report a non-integer device-pixel ratio.
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
# Software rasterizer fallback for hardware without proper OpenGL / EGL
os.environ.setdefault("QT_OPENGL", "software")


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

# Reentrancy guard: prevents infinite dialog cascades when an exception
# occurs inside a Qt event handler that fires repeatedly (e.g. changeEvent).
# Without this guard, QMessageBox.exec() starts a nested event loop which
# can re-trigger the same faulting handler, producing an endless stack of
# error dialogs that the user cannot close.
_excepthook_active = False


def _collect_sysinfo() -> str:
    """Return a compact diagnostic string with Python and key-library versions."""
    lines = [
        f"Python: {sys.version}",
        f"Platform: {sys.platform}",
    ]
    for mod_name, attr in (
        ("PyQt6.QtCore", "PYQT_VERSION_STR"),
        ("PyQt6.QtCore", "QT_VERSION_STR"),
        ("PIL", "__version__"),
        ("numpy", "__version__"),
    ):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            lines.append(f"{mod_name}.{attr}: {getattr(mod, attr, '?')}")
        except Exception as exc:
            lines.append(f"{mod_name}: MISSING ({exc})")
    return "\n".join(lines)


def _excepthook(exc_type, exc_value, exc_tb):
    """Log uncaught exceptions and show a friendly dialog instead of crashing silently."""
    global _excepthook_active

    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    sysinfo = _collect_sysinfo()
    logger.critical("Uncaught exception:\n%s\nSystem info:\n%s", msg, sysinfo)

    # If we're already inside _excepthook (i.e. an error occurred while the
    # previous error dialog was open), only log – do not open another dialog.
    if _excepthook_active:
        return

    _excepthook_active = True
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
            box.setDetailedText(f"{msg}\n\nSystem info:\n{sysinfo}")
            box.setIcon(QMessageBox.Icon.Critical)
            box.exec()
    except Exception:
        pass
    finally:
        _excepthook_active = False


sys.excepthook = _excepthook


# ---------------------------------------------------------------------------
# Single-instance guard
# ---------------------------------------------------------------------------

def _acquire_single_instance_lock():
    """Ensure only one copy of the application runs at a time.

    Uses Qt's cross-platform ``QLockFile`` which stores the PID of the owning
    process and automatically treats locks from dead processes as *stale*
    (cleaned up after ``staleLockTime`` ms — default 30 s).

    Returns the ``QLockFile`` object on success.  The caller **must** keep a
    reference to it for the entire lifetime of the process; releasing it
    (or letting it go out of scope) removes the lock and would allow a second
    instance to start.

    If the lock cannot be acquired (i.e. another live instance already holds
    it), a warning dialog is displayed and the process exits with code 0.
    """
    import tempfile
    from PyQt6.QtCore import QLockFile
    from PyQt6.QtWidgets import QMessageBox

    lock_path = os.path.join(
        tempfile.gettempdir(), "AlphaFixerConverter_instance.lock"
    )
    lock = QLockFile(lock_path)

    if lock.tryLock(500):          # 500 ms → generous for slow/busy systems
        return lock

    # Another live instance is running (or the lock file is truly stale —
    # Qt already attempted an automatic break-and-reacquire above).
    logger.warning("Another instance is already running; showing notice and exiting.")
    box = QMessageBox()
    box.setWindowTitle("Already Running  🐼")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(
        "<b>Alpha Fixer &amp; File Converter is already open.</b><br><br>"
        "Only one instance can run at a time.<br>"
        "Please check your taskbar or bring the existing window to the front."
    )
    box.exec()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Hang / UI-freeze watchdog
# ---------------------------------------------------------------------------

class _HangWatchdog:
    """Lightweight watchdog that detects Qt event-loop freezes.

    The UI thread resets a ``_heartbeat`` flag every ``tick_ms`` milliseconds
    via a QTimer.  A background daemon thread checks the flag every
    ``check_interval`` seconds; if the flag has *not* been reset the watchdog
    concludes that the event loop is blocked and logs a warning together with
    the current stack frames of all threads so the freeze can be diagnosed from
    the crash log.

    The watchdog is intentionally non-fatal: it logs and continues rather than
    force-killing the process, because the UI may eventually unblock on its own
    (e.g. waiting for a slow disk operation) and killing would lose unsaved work.
    """

    # How often the QTimer ticks (ms) — this is the resolution of "alive" pings.
    _TICK_MS = 1_000
    # If the flag has not been refreshed within this many seconds, declare a hang.
    _HANG_THRESHOLD_S = 5.0
    # How long the monitor thread sleeps between checks.
    _CHECK_INTERVAL_S = 2.0
    # Minimum gap (s) between consecutive hang log entries so the log isn't flooded.
    _LOG_COOLDOWN_S = 15.0

    def __init__(self):
        self._heartbeat: float = time.monotonic()
        self._running = False
        self._thread: threading.Thread | None = None
        self._timer = None          # QTimer — created in start() on the UI thread
        self._last_log: float = 0.0

    def start(self) -> None:
        """Start the watchdog.  Must be called from the Qt main / UI thread."""
        from PyQt6.QtCore import QTimer
        self._heartbeat = time.monotonic()
        self._running = True

        # QTimer fires on the UI thread → proves the event loop is alive.
        self._timer = QTimer()
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        # Monitor thread is a daemon so it never prevents clean exit.
        self._thread = threading.Thread(
            target=self._monitor, name="HangWatchdog", daemon=True
        )
        self._thread.start()
        logger.info("Hang watchdog started (threshold=%.0fs).", self._HANG_THRESHOLD_S)

    def stop(self) -> None:
        """Stop the watchdog (call before the QApplication is destroyed)."""
        self._running = False
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass

    def _on_tick(self) -> None:
        """Called by QTimer on the UI thread — proof the event loop is running."""
        self._heartbeat = time.monotonic()

    def _monitor(self) -> None:
        """Background thread: periodically check whether the heartbeat is fresh."""
        while self._running:
            time.sleep(self._CHECK_INTERVAL_S)
            if not self._running:
                break
            age = time.monotonic() - self._heartbeat
            if age >= self._HANG_THRESHOLD_S:
                now = time.monotonic()
                if now - self._last_log >= self._LOG_COOLDOWN_S:
                    self._last_log = now
                    self._log_hang(age)

    def _log_hang(self, age: float) -> None:
        """Log a hang event with per-thread stack traces for diagnosis."""
        lines = [
            f"⚠  UI THREAD HANG DETECTED — event loop blocked for ≥{age:.1f}s",
            "--- Thread stack traces ---",
        ]
        frames = sys._current_frames()
        for tid, frame in frames.items():
            name = "?"
            for t in threading.enumerate():
                if t.ident == tid:
                    name = t.name
                    break
            lines.append(f"\nThread {tid} ({name}):")
            lines.extend(
                "  " + line
                for line in traceback.format_stack(frame)
            )
        lines.append("--- End of hang report ---")
        logger.warning("\n".join(lines))


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
    from PyQt6.QtCore import QCoreApplication, Qt
    from PyQt6.QtGui import QFont

    QCoreApplication.setApplicationName("AlphaFixerConverter")
    QCoreApplication.setOrganizationName("PandaTools")
    # AA_UseHighDpiPixmaps was removed in Qt6; high-DPI pixmaps are always
    # enabled by default in Qt6/PyQt6 so no setAttribute call is needed.
    # Enable per-monitor DPI awareness so each window rescales correctly when
    # dragged between monitors with different scale factors.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Consistent baseline across all platforms

    # --- Single-instance guard -------------------------------------------------
    # Must come *after* QApplication is created so that QLockFile and the
    # fallback QMessageBox both have a running Qt event loop to work with.
    # The returned lock object MUST stay alive until the process exits.
    _instance_lock = _acquire_single_instance_lock()  # noqa: F841 – must stay alive

    logger.info("Starting Alpha Fixer & File Converter")

    # Import application modules.  Any ImportError here typically means a
    # required library (numpy, Pillow, etc.) is not installed.  Log clearly.
    try:
        from src.core.settings_manager import SettingsManager
        from src.ui.main_window import MainWindow
        from src.ui.splash_screen import ThemeSplashScreen
    except ImportError as exc:
        sysinfo = _collect_sysinfo()
        logger.critical(
            "Failed to import application modules (missing library?):\n%s\n"
            "System info:\n%s",
            exc, sysinfo,
        )
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setWindowTitle("Startup Error — Missing Library 🐼")
        box.setText(
            f"A required library is missing and the application cannot start.\n\n"
            f"Missing: {exc}\n\n"
            "Install all dependencies with:\n"
            "    pip install -r requirements.txt\n\n"
            f"Details logged to:\n{log_file}"
        )
        box.setDetailedText(f"System info:\n{sysinfo}")
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()
        sys.exit(1)

    settings = SettingsManager()

    # Apply the user's saved font-size preference before the main window
    # appears so every widget (including the splash) inherits the correct size.
    _saved_font_size = settings.get("font_size", 10)
    _saved_font_size = max(8, min(24, int(_saved_font_size)))
    font = QFont("Segoe UI", _saved_font_size)
    font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    app.setFont(font)

    # Show animated themed splash screen only when enabled in settings
    splash = None
    if settings.get("show_splash_screen", False):
        splash = ThemeSplashScreen(settings)
        splash.show()
        app.processEvents()

    try:
        window = MainWindow(settings)
    except Exception as exc:
        tb_str = traceback.format_exc()
        sysinfo = _collect_sysinfo()
        logger.critical(
            "Failed to create main window:\n%s\nSystem info:\n%s",
            tb_str, sysinfo,
        )
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setWindowTitle("Startup Error 🐼")
        box.setText(
            "The main window could not be created and the application cannot start.\n\n"
            f"Error: {exc}\n\n"
            f"Details logged to:\n{log_file}"
        )
        box.setDetailedText(f"{tb_str}\n\nSystem info:\n{sysinfo}")
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()
        sys.exit(1)

    # Close splash and reveal main window after the splash duration
    from PyQt6.QtCore import QTimer
    if splash is not None:
        QTimer.singleShot(2800, lambda: splash.finish_and_close(window))

    window.show()

    # Start the hang watchdog after the window is visible so normal startup
    # I/O (settings load, theme apply, etc.) doesn't trigger false positives.
    _watchdog = _HangWatchdog()
    _watchdog.start()

    logger.info("Main window shown.")
    exit_code = app.exec()
    _watchdog.stop()
    logger.info("Application exited with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
