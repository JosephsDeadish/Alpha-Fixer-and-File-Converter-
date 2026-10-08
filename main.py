#!/usr/bin/env python3
"""
FORMATOMANCER: Alpha & Media Alchemy – Entry Point.

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
import ctypes
import json
import shutil
import tempfile
import subprocess
from pathlib import Path

from src.core.runtime_validation import (
    execute_dds_manifest,
    execute_disc_video_manifest,
    execute_format_matrix_manifest,
    load_manifest_entries_from_env,
    manifest_sample_limit_from_env,
)


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
    "libxcb-cursor.so.0": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
    "libxcb-icccm.so.4": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
    "libxcb-image.so.0": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
    "libxcb-keysyms.so.1": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
    "libxcb-render-util.so.0": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
    "libxcb-util.so.1": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
    "libxcb-xkb.so.1": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
    "libxkbcommon-x11.so.0": {
        "debian":   "sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "fedora":   "sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "arch":     "sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm",
        "opensuse": "sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0",
        "generic":  "Install the Qt X11/XCB support libraries for your distribution",
    },
}

_LINUX_RUNTIME_AUDIT_LIBS = (
    "libEGL.so.1",
    "libGL.so.1",
    "libGLESv2.so.2",
    "libpulse.so.0",
    "libxcb-cursor.so.0",
    "libxcb-icccm.so.4",
    "libxcb-image.so.0",
    "libxcb-keysyms.so.1",
    "libxcb-render-util.so.0",
    "libxcb-util.so.1",
    "libxcb-xkb.so.1",
    "libxkbcommon-x11.so.0",
)

_OPTIONAL_QT_WARNING_PATTERNS = (
    ("Couldn't load pipewire-0.3 library", "PipeWire"),
    ("Couldn't resolve pipewire-0.3 symbols", "PipeWire"),
    ("Couldn't load va-x11 library", "VA-API"),
    ("Couldn't resolve va-x11 symbols", "VA-API"),
    ("Couldn't load va-drm library", "VA-API"),
    ("Couldn't resolve va-drm symbols", "VA-API"),
    ("Couldn't load va library", "VA-API"),
    ("Couldn't resolve va symbols", "VA-API"),
    ("Couldn't load va(in plugin) library", "VA-API"),
    ("Couldn't resolve va(in plugin) symbols", "VA-API"),
    ("PulseAudioService: pa_context_connect() failed", "PulseAudio server"),
)
_qt_optional_warning_hits: dict[str, int] = {}
_DDS_COMPRESSED_VARIANT_LABELS = {
    "dxt1": "BC1/DXT1",
    "dxt3": "BC2/DXT3",
    "dxt5": "BC3/DXT5",
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
    Try to import the Qt widget stack. If it fails due to a missing shared
    library, print a clear error with distro-specific install commands and
    return False so the caller can exit cleanly.
    """
    if sys.platform != "linux":
        # On Windows / macOS the required DLLs are bundled with PyQt6-Qt6
        return True

    try:
        from PyQt6.QtWidgets import QApplication  # noqa: F401 – just a probe
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


def _missing_linux_runtime_libs() -> list[str]:
    if sys.platform != "linux":
        return []
    missing = []
    for lib_name in _LINUX_RUNTIME_AUDIT_LIBS:
        try:
            ctypes.CDLL(lib_name)
        except OSError:
            missing.append(lib_name)
    return missing


def _packaged_runtime_notice(missing_libs: list[str]) -> str:
    if not missing_libs:
        return ""
    preview = ", ".join(missing_libs[:3])
    extra = len(missing_libs) - min(len(missing_libs), 3)
    if extra > 0:
        preview = f"{preview} +{extra} more"
    return (
        f"⚠ Optional Linux runtime libraries are missing: {preview}. "
        "Some video/audio or X11 features may be limited."
    )


def _smoke_test_duration_ms() -> int:
    raw = os.environ.get("ALPHA_FIXER_SMOKE_TEST", "").strip()
    if not raw:
        return 0
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 1.5
    return max(250, int(seconds * 1000))


def _classify_optional_qt_warning(message: str) -> str:
    text = str(message or "").strip()
    for needle, label in _OPTIONAL_QT_WARNING_PATTERNS:
        if needle in text:
            return label
    return ""


def _install_qt_message_filter(qInstallMessageHandler) -> None:
    _qt_optional_warning_hits.clear()

    def _handler(_mode, _context, message) -> None:
        text = str(message or "").strip()
        label = _classify_optional_qt_warning(text)
        if label:
            _qt_optional_warning_hits[label] = _qt_optional_warning_hits.get(label, 0) + 1
            if _qt_optional_warning_hits[label] == 1:
                logger.info("Optional Qt backend unavailable: %s", label)
            return
        if text:
            print(text, file=sys.stderr)

    qInstallMessageHandler(_handler)


def _optional_qt_runtime_notice() -> str:
    if not _qt_optional_warning_hits:
        return ""
    labels = sorted(_qt_optional_warning_hits)
    return (
        "⚠ Optional Linux multimedia backends unavailable: "
        + ", ".join(labels)
        + ". Playback/export should still fall back to bundled ffmpeg or software paths."
    )


def _optional_feature_readiness_notice() -> str:
    summary = _runtime_capability_summary()
    notice = summary.get("feature_readiness_notice")
    return str(notice or "")


def _normalized_existing_path(path_text: str) -> str:
    candidate = str(path_text or "").strip()
    if not candidate:
        return ""
    try:
        resolved = Path(candidate).expanduser().resolve()
    except Exception:
        resolved = Path(candidate).expanduser()
    return str(resolved) if resolved.exists() else ""


def _resolved_path(path_text: str) -> Path | None:
    candidate = str(path_text or "").strip()
    if not candidate:
        return None
    try:
        resolved = Path(candidate).expanduser().resolve()
    except Exception:
        resolved = Path(candidate).expanduser()
    return resolved


def _path_is_within(path_text: str, root_text: str) -> bool:
    candidate = _resolved_path(path_text)
    root = _resolved_path(root_text)
    if candidate is None or root is None:
        return False
    try:
        candidate.relative_to(root)
        return True
    except Exception:
        return False


def _qt_svg_runtime_ready() -> bool:
    try:
        from PyQt6.QtSvg import QSvgRenderer  # noqa: F401
        return True
    except Exception:
        return False


def _theme_svg_runtime_details() -> dict[str, object]:
    details: dict[str, object] = {
        "qt_svg_ready": False,
        "default_theme_svg_path": "",
        "default_theme_svg_ready": False,
        "theme_svg_missing_count": 0,
    }
    try:
        from src.ui.theme_engine import get_theme_svg_path, THEME_SVG
    except Exception:
        return details
    details["qt_svg_ready"] = _qt_svg_runtime_ready()
    default_svg_path = get_theme_svg_path("Panda Dark")
    details["default_theme_svg_path"] = default_svg_path
    details["default_theme_svg_ready"] = bool(default_svg_path)
    missing = 0
    for theme_name in THEME_SVG:
        if not get_theme_svg_path(theme_name):
            missing += 1
    details["theme_svg_missing_count"] = missing
    return details


def _imagemagick_runtime_details() -> dict[str, object]:
    details: dict[str, object] = {
        "wand_runtime_ready": False,
        "magick_home_path": "",
        "imagemagick_home_path": "",
    }
    try:
        from src.core.alpha_processor import _has_wand
    except Exception:
        return details
    details["wand_runtime_ready"] = bool(_has_wand())
    details["magick_home_path"] = _normalized_existing_path(os.environ.get("MAGICK_HOME", ""))
    details["imagemagick_home_path"] = _normalized_existing_path(os.environ.get("IMAGEMAGICK_HOME", ""))
    return details


def _executable_runtime_details(path_text: str, *, args: tuple[str, ...] = ("-version",), timeout: int = 20) -> dict[str, object]:
    details: dict[str, object] = {
        "path": str(path_text or "").strip(),
        "exists": False,
        "runtime_ready": False,
        "detail": "",
    }
    path = str(path_text or "").strip()
    if not path:
        details["detail"] = "missing"
        return details
    try:
        candidate = Path(path)
    except Exception:
        details["detail"] = "invalid path"
        return details
    exists = candidate.is_file()
    details["exists"] = bool(exists)
    if not exists:
        details["detail"] = "path missing"
        return details
    try:
        result = subprocess.run(
            [path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=max(1, int(timeout)),
        )
    except Exception as exc:
        details["detail"] = str(exc).strip() or exc.__class__.__name__
        return details
    output_lines = [line.strip() for line in str(result.stdout or "").splitlines() if line.strip()]
    details["detail"] = output_lines[0] if output_lines else (f"exit {result.returncode}" if result.returncode else "ok")
    details["runtime_ready"] = result.returncode == 0
    return details


def _dds_compression_variant_selfcheck() -> dict[str, object]:
    details: dict[str, object] = {
        "available": False,
        "ready": False,
        "detail": "",
        "variants": {},
        "failures": [],
    }
    try:
        from PIL import Image
        from src.core.alpha_processor import _load_dds
        from src.core.file_converter import convert_file, dds_compression_available
    except Exception as exc:
        details["detail"] = str(exc).strip() or exc.__class__.__name__
        return details
    if not dds_compression_available():
        details["detail"] = "skipped: ImageMagick/wand runtime unavailable"
        return details
    details["available"] = True
    failures: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="alpha_fixer_dds_runtime_") as tmpdir:
            sample_png = os.path.join(tmpdir, "sample.png")
            sample_img = Image.new("RGBA", (16, 16), (48, 160, 240, 192))
            try:
                sample_img.save(sample_png)
            finally:
                sample_img.close()
            for variant in ("dxt1", "dxt3", "dxt5"):
                out_path = os.path.join(tmpdir, f"sample_{variant}.dds")
                try:
                    convert_file(sample_png, out_path, "DDS", dds_variant=variant)
                    dds_img = _load_dds(out_path)
                    try:
                        ok = bool(os.path.isfile(out_path) and dds_img.size == (16, 16))
                        detail = f"size={dds_img.size}"
                    finally:
                        dds_img.close()
                except Exception as exc:
                    ok = False
                    detail = str(exc).strip() or exc.__class__.__name__
                variant_details = {"ok": ok, "detail": detail}
                details["variants"][variant] = variant_details
                if not ok:
                    failures.append(variant)
    except Exception as exc:
        details["detail"] = str(exc).strip() or exc.__class__.__name__
        return details
    details["failures"] = failures
    details["ready"] = not failures
    details["detail"] = (
        "ok"
        if not failures else
        "failed: " + ", ".join(_DDS_COMPRESSED_VARIANT_LABELS.get(name, name.upper()) for name in failures)
    )
    return details


def _runtime_capability_summary() -> dict[str, object]:
    frozen = bool(getattr(sys, "frozen", False))
    bundle_dir = ""
    if frozen:
        try:
            bundle_dir = str(Path(sys.executable).resolve().parent)
        except Exception:
            bundle_dir = str(Path(sys.executable).parent)
    summary: dict[str, object] = {
        "frozen": frozen,
        "platform": sys.platform,
        "bundle_dir": bundle_dir,
        "missing_linux_runtime_libs": _missing_linux_runtime_libs() if sys.platform == "linux" else [],
    }
    summary["packaged_runtime_notice"] = _packaged_runtime_notice(
        list(summary["missing_linux_runtime_libs"])
    )
    try:
        from src.core.file_converter import dds_compression_available, optional_pillow_output_limits
        from src.ui.video_tool import _get_ffmpeg_exe, _get_ffprobe_exe, _has_imageio, _has_imageio_ffmpeg
    except Exception as exc:
        summary["runtime_audit_error"] = str(exc)
        summary["video_runtime_ready"] = False
        summary["dds_compression_available"] = False
        summary["optional_output_limits"] = []
        summary["feature_readiness_notice"] = ""
        return summary

    has_imageio = bool(_has_imageio())
    has_imageio_ffmpeg = bool(_has_imageio_ffmpeg())
    ffmpeg_path = _get_ffmpeg_exe() or ""
    ffprobe_path = _get_ffprobe_exe() or ""
    ffmpeg_runtime = _executable_runtime_details(ffmpeg_path)
    ffprobe_runtime = _executable_runtime_details(ffprobe_path)
    ffmpeg_path_exists = bool(ffmpeg_runtime.get("exists"))
    ffprobe_path_exists = bool(ffprobe_runtime.get("exists"))
    ffmpeg_runtime_ready = bool(ffmpeg_runtime.get("runtime_ready"))
    ffprobe_runtime_ready = bool(ffprobe_runtime.get("runtime_ready"))
    ffmpeg_runtime_detail = str(ffmpeg_runtime.get("detail") or "")
    ffprobe_runtime_detail = str(ffprobe_runtime.get("detail") or "")
    ffmpeg_on_path = bool(shutil.which("ffmpeg"))
    ffprobe_on_path = bool(shutil.which("ffprobe"))
    unavailable_outputs = optional_pillow_output_limits()
    svg_details = _theme_svg_runtime_details()
    imagemagick_details = _imagemagick_runtime_details()
    wand_runtime_ready = bool(imagemagick_details.get("wand_runtime_ready"))
    dds_variant_selfcheck = _dds_compression_variant_selfcheck()
    dds_variant_failures = list(dds_variant_selfcheck.get("failures") or [])
    dds_variant_ready = bool(dds_variant_selfcheck.get("ready"))
    ffmpeg_bundled = bool(frozen and ffmpeg_path_exists and bundle_dir and _path_is_within(ffmpeg_path, bundle_dir))
    ffprobe_bundled = bool(frozen and ffprobe_path_exists and bundle_dir and _path_is_within(ffprobe_path, bundle_dir))
    default_theme_svg_bundled = bool(
        frozen
        and svg_details.get("default_theme_svg_ready")
        and bundle_dir
        and _path_is_within(str(svg_details.get("default_theme_svg_path") or ""), bundle_dir)
    )
    magick_home_path = str(imagemagick_details.get("magick_home_path") or "")
    imagemagick_home_path = str(imagemagick_details.get("imagemagick_home_path") or "")
    imagemagick_bundled = bool(
        frozen and bundle_dir and (
            _path_is_within(magick_home_path, bundle_dir)
            or _path_is_within(imagemagick_home_path, bundle_dir)
        )
    )
    imagemagick_configured = bool(magick_home_path or imagemagick_home_path or imagemagick_bundled)
    missing_video_bits: list[str] = []
    if not has_imageio:
        missing_video_bits.append("imageio")
    if not has_imageio_ffmpeg:
        missing_video_bits.append("imageio-ffmpeg")
    if not ffmpeg_path:
        missing_video_bits.append("ffmpeg")
    elif not ffmpeg_runtime_ready:
        missing_video_bits.append("ffmpeg runtime")
    readiness_limits: list[str] = []
    if missing_video_bits:
        readiness_limits.append(
            "video import/MP4 export unavailable: missing " + ", ".join(missing_video_bits)
        )
    elif not ffprobe_path:
        readiness_limits.append(
            "odd-container probing/detail guidance limited: ffprobe unavailable"
        )
    elif not ffprobe_runtime_ready:
        readiness_limits.append(
            "odd-container probing/detail guidance limited: ffprobe self-check failed"
            + (f" ({ffprobe_runtime_detail})" if ffprobe_runtime_detail else "")
        )
    if ffmpeg_path and not ffmpeg_path_exists:
        readiness_limits.append("ffmpeg path invalid")
    elif ffmpeg_path and not ffmpeg_runtime_ready:
        readiness_limits.append(
            "ffmpeg self-check failed"
            + (f" ({ffmpeg_runtime_detail})" if ffmpeg_runtime_detail else "")
        )
    if ffprobe_path and not ffprobe_path_exists:
        readiness_limits.append("ffprobe path invalid")
    elif ffprobe_path and not ffprobe_runtime_ready:
        readiness_limits.append(
            "ffprobe self-check failed"
            + (f" ({ffprobe_runtime_detail})" if ffprobe_runtime_detail else "")
        )
    if not dds_compression_available():
        readiness_limits.append(
            "DDS compressed variants unavailable: ImageMagick/wand runtime incomplete"
            if imagemagick_configured else
            "DDS compressed variants unavailable: ImageMagick/wand runtime missing"
        )
    elif dds_variant_selfcheck.get("available") and dds_variant_failures:
        readiness_limits.append(
            "DDS compressed output self-check failed: "
            + ", ".join(_DDS_COMPRESSED_VARIANT_LABELS.get(name, name.upper()) for name in dds_variant_failures)
        )
    elif not dds_variant_selfcheck.get("available") and str(dds_variant_selfcheck.get("detail") or "").strip():
        readiness_limits.append(
            "DDS compressed output self-check unavailable: "
            + str(dds_variant_selfcheck.get("detail") or "").strip()
        )
    if not bool(svg_details.get("qt_svg_ready")):
        readiness_limits.append("Qt SVG renderer unavailable")
    if not bool(svg_details.get("default_theme_svg_ready")):
        readiness_limits.append("default theme SVG asset missing")
    missing_svg_count = int(svg_details.get("theme_svg_missing_count") or 0)
    if missing_svg_count > 0:
        readiness_limits.append(f"{missing_svg_count} theme SVG asset(s) missing")
    if unavailable_outputs:
        preview = ", ".join(name for name, _reason in unavailable_outputs[:3])
        extra = len(unavailable_outputs) - min(len(unavailable_outputs), 3)
        if extra > 0:
            preview = f"{preview} +{extra} more"
        readiness_limits.append(f"optional image exports unavailable: {preview}")
    packaged_asset_warnings: list[str] = []
    if frozen:
        if not ffmpeg_path:
            packaged_asset_warnings.append("packaged ffmpeg binary missing")
        elif not ffmpeg_path_exists:
            packaged_asset_warnings.append("packaged ffmpeg path invalid")
        elif not ffmpeg_runtime_ready:
            packaged_asset_warnings.append("packaged ffmpeg binary failed self-check")
        elif not ffmpeg_bundled:
            packaged_asset_warnings.append("ffmpeg resolves outside the packaged app")
        if not ffprobe_path:
            packaged_asset_warnings.append("packaged ffprobe binary missing")
        elif not ffprobe_path_exists:
            packaged_asset_warnings.append("packaged ffprobe path invalid")
        elif not ffprobe_runtime_ready:
            packaged_asset_warnings.append("packaged ffprobe binary failed self-check")
        elif not ffprobe_bundled:
            packaged_asset_warnings.append("ffprobe resolves outside the packaged app")
        if not bool(svg_details.get("default_theme_svg_ready")):
            packaged_asset_warnings.append("default theme SVG asset missing from package")
        elif not default_theme_svg_bundled:
            packaged_asset_warnings.append("default theme SVG resolves outside the packaged app")
        if missing_svg_count > 0:
            packaged_asset_warnings.append(f"{missing_svg_count} theme SVG asset(s) missing from package")
        if not wand_runtime_ready:
            packaged_asset_warnings.append(
                "bundled ImageMagick/wand runtime incomplete"
                if imagemagick_bundled else
                "packaged ImageMagick/wand runtime unavailable for DDS compressed output"
            )
        elif dds_variant_selfcheck.get("available") and dds_variant_failures:
            packaged_asset_warnings.append(
                "packaged DDS compressed output self-check failed: "
                + ", ".join(_DDS_COMPRESSED_VARIANT_LABELS.get(name, name.upper()) for name in dds_variant_failures)
            )
    packaged_bundle_ready = bool(frozen and not packaged_asset_warnings)
    if packaged_asset_warnings:
        readiness_limits.append("packaged asset gaps: " + "; ".join(packaged_asset_warnings))
    feature_readiness_notice = ""
    if readiness_limits:
        feature_readiness_notice = (
            "⚠ Optional feature limits detected: "
            + "; ".join(readiness_limits)
            + ". See tool banners for details."
        )

    summary.update({
        "has_imageio": has_imageio,
        "has_imageio_ffmpeg": has_imageio_ffmpeg,
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
        "ffmpeg_path_exists": ffmpeg_path_exists,
        "ffprobe_path_exists": ffprobe_path_exists,
        "ffmpeg_runtime_ready": ffmpeg_runtime_ready,
        "ffprobe_runtime_ready": ffprobe_runtime_ready,
        "ffmpeg_runtime_detail": ffmpeg_runtime_detail,
        "ffprobe_runtime_detail": ffprobe_runtime_detail,
        "ffmpeg_on_path": ffmpeg_on_path,
        "ffprobe_on_path": ffprobe_on_path,
        "ffmpeg_bundled": ffmpeg_bundled,
        "ffprobe_bundled": ffprobe_bundled,
        "video_runtime_ready": bool(has_imageio and has_imageio_ffmpeg and ffmpeg_runtime_ready),
        "odd_container_probe_ready": bool(has_imageio and has_imageio_ffmpeg and ffmpeg_runtime_ready and ffprobe_runtime_ready),
        "missing_video_bits": missing_video_bits,
        "dds_compression_available": bool(dds_compression_available()),
        "dds_compression_variant_selfcheck_available": bool(dds_variant_selfcheck.get("available")),
        "dds_compression_variant_selfcheck_ready": dds_variant_ready,
        "dds_compression_variant_selfcheck_detail": str(dds_variant_selfcheck.get("detail") or ""),
        "dds_compression_variant_failures": dds_variant_failures,
        "dds_compression_variant_checks": dict(dds_variant_selfcheck.get("variants") or {}),
        "optional_output_limits": unavailable_outputs,
        "default_theme_svg_bundled": default_theme_svg_bundled,
        "imagemagick_bundled": imagemagick_bundled,
        "imagemagick_configured": imagemagick_configured,
        "packaged_bundle_ready": packaged_bundle_ready,
        "packaged_asset_warnings": packaged_asset_warnings,
        "feature_readiness_notice": feature_readiness_notice,
        **svg_details,
        **imagemagick_details,
    })
    return summary


def _runtime_capability_dump_requested() -> bool:
    return os.environ.get("ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _emit_runtime_capability_dump() -> int:
    summary = _runtime_capability_summary()
    print("ALPHA_FIXER_RUNTIME_CAPABILITIES=" + json.dumps(summary, sort_keys=True))
    return 0


def _runtime_selftest_iterations() -> int:
    raw = os.environ.get("ALPHA_FIXER_RUNTIME_SELFTEST", "").strip()
    if not raw:
        return 0
    try:
        iterations = int(raw)
    except ValueError:
        iterations = 1
    return max(1, min(64, iterations))


def _runtime_selftest_peak_rss_mb() -> float | None:
    try:
        import resource
    except Exception:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    if usage <= 0:
        return None
    if sys.platform == "darwin":
        return round(float(usage) / (1024.0 * 1024.0), 2)
    return round(float(usage) / 1024.0, 2)


def _emit_runtime_selftest_dump() -> int:
    from PIL import Image
    from src.core.alpha_processor import _load_dds
    from src.core.file_converter import SUPPORTED_OUTPUT_FORMATS, convert_file, dds_compression_available
    from src.ui import video_tool as vt

    iterations = _runtime_selftest_iterations()
    manifest_limit = manifest_sample_limit_from_env()
    summary: dict[str, object] = {
        "iterations": iterations,
        "passed": True,
        "checks": {},
        "errors": [],
    }

    def _record_check(name: str, ok: bool, detail: str) -> None:
        checks = summary.setdefault("checks", {})
        if isinstance(checks, dict):
            checks[name] = {"ok": bool(ok), "detail": str(detail)}
        if not ok:
            summary["passed"] = False
            errors = summary.setdefault("errors", [])
            if isinstance(errors, list):
                errors.append(f"{name}: {detail}")

    def _copy_file(src: str, dst: str) -> None:
        with open(src, "rb") as src_handle, open(dst, "wb") as dst_handle:
            shutil.copyfileobj(src_handle, dst_handle)

    with tempfile.TemporaryDirectory(prefix="alpha_fixer_runtime_selftest_") as tmpdir:
        sample_png = os.path.join(tmpdir, "sample.png")
        sample_gif = os.path.join(tmpdir, "sample.gif")
        sample_dds = os.path.join(tmpdir, "sample.dds")
        sample_dxt1 = os.path.join(tmpdir, "sample_dxt1.dds")
        sample_mp4 = os.path.join(tmpdir, "sample.mp4")
        sample_ts = os.path.join(tmpdir, "sample.ts")
        sample_bin = os.path.join(tmpdir, "sample.bin")

        Image.new("RGBA", (32, 24), (32, 160, 255, 192)).save(sample_png)

        for _idx in range(iterations):
            convert_file(sample_png, sample_gif, "GIF")
            with Image.open(sample_gif) as gif_img:
                gif_img.load()
                _record_check("png_to_gif", gif_img.size == (32, 24), f"size={gif_img.size}")

            convert_file(sample_png, sample_dds, "DDS", dds_variant="rgba")
            dds_img = _load_dds(sample_dds)
            try:
                _record_check("png_to_dds_rgba", dds_img.size == (32, 24), f"size={dds_img.size}")
            finally:
                dds_img.close()

            if dds_compression_available():
                dds_variant_checks = _dds_compression_variant_selfcheck()
                for variant in ("dxt1", "dxt3", "dxt5"):
                    result = dict((dds_variant_checks.get("variants") or {}).get(variant) or {})
                    detail = str(result.get("detail") or dds_variant_checks.get("detail") or "DDS compressed variant self-check unavailable")
                    if dds_variant_checks.get("available"):
                        _record_check(f"png_to_dds_{variant}", bool(result.get("ok")), detail)
                    else:
                        _record_check(f"png_to_dds_{variant}", detail.startswith("skipped:"), detail)
            else:
                for variant in ("dxt1", "dxt3", "dxt5"):
                    _record_check(f"png_to_dds_{variant}", True, "skipped: ImageMagick/wand runtime unavailable")

            ffmpeg_exe = vt._get_ffmpeg_exe()
            if ffmpeg_exe:
                mp4_result = subprocess.run(
                    [
                        ffmpeg_exe,
                        "-y",
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc=size=160x90:rate=12",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=440:sample_rate=44100",
                        "-shortest",
                        "-t",
                        "1.2",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        sample_mp4,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    text=True,
                    timeout=180,
                )
                if mp4_result.returncode == 0 and os.path.isfile(sample_mp4):
                    mp4_clip = vt._load_video_clip(sample_mp4)
                    if mp4_clip is None:
                        _record_check("generated_mp4_load", False, vt._video_load_failure_hint(sample_mp4))
                    else:
                        try:
                            _record_check(
                                "generated_mp4_load",
                                mp4_clip.total_frames > 0,
                                f"frames={mp4_clip.total_frames} audio={mp4_clip.has_audio}",
                            )
                        finally:
                            mp4_clip.close()

                    ts_result = subprocess.run(
                        [
                            ffmpeg_exe,
                            "-y",
                            "-v",
                            "error",
                            "-i",
                            sample_mp4,
                            "-c",
                            "copy",
                            sample_ts,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        text=True,
                        timeout=180,
                    )
                    if ts_result.returncode == 0 and os.path.isfile(sample_ts):
                        ts_clip = vt._load_video_clip(sample_ts)
                        if ts_clip is None:
                            _record_check("mpegts_load", False, vt._video_load_failure_hint(sample_ts))
                        else:
                            try:
                                _record_check(
                                    "mpegts_load",
                                    ts_clip.total_frames > 0,
                                    f"frames={ts_clip.total_frames} note={ts_clip.load_note or 'direct'}",
                                )
                            finally:
                                ts_clip.close()
                    else:
                        _record_check(
                            "mpegts_load",
                            False,
                            f"ffmpeg copy failed: {ts_result.stderr.strip() or ts_result.stdout.strip() or 'unknown error'}",
                        )

                    _copy_file(sample_mp4, sample_bin)
                    odd_clip = vt._load_video_clip(sample_bin)
                    if odd_clip is None:
                        hint = vt._video_load_failure_hint(sample_bin)
                        _record_check("synthetic_bin_probe", bool(hint), hint or "missing odd-container hint")
                    else:
                        try:
                            _record_check(
                                "synthetic_bin_probe",
                                odd_clip.total_frames > 0,
                                f"frames={odd_clip.total_frames} note={odd_clip.load_note or 'direct'}",
                            )
                        finally:
                            odd_clip.close()
                else:
                    detail = mp4_result.stderr.strip() or mp4_result.stdout.strip() or "ffmpeg sample generation failed"
                    _record_check("generated_mp4_load", False, detail)
                    _record_check("mpegts_load", False, "skipped: generated MP4 unavailable")
                    _record_check("synthetic_bin_probe", False, "skipped: generated MP4 unavailable")
            else:
                _record_check("generated_mp4_load", False, "ffmpeg executable unavailable")
                _record_check("mpegts_load", False, "ffmpeg executable unavailable")
                _record_check("synthetic_bin_probe", False, "ffmpeg executable unavailable")

        disc_manifest = load_manifest_entries_from_env("ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST")
        if disc_manifest:
            ok, detail = execute_disc_video_manifest(disc_manifest, vt, limit=manifest_limit)
            _record_check("external_disc_video_manifest", ok, detail)

        dds_manifest = load_manifest_entries_from_env("ALPHA_FIXER_RUNTIME_DDS_MANIFEST")
        if dds_manifest:
            ok, detail = execute_dds_manifest(dds_manifest, _load_dds, limit=manifest_limit)
            _record_check("external_dds_manifest", ok, detail)

        format_manifest = load_manifest_entries_from_env("ALPHA_FIXER_RUNTIME_FORMAT_MATRIX_MANIFEST")
        if format_manifest:
            ok, detail = execute_format_matrix_manifest(
                format_manifest,
                convert_file=convert_file,
                load_dds=_load_dds,
                image_module=Image,
                output_formats=SUPPORTED_OUTPUT_FORMATS,
                tmpdir=tmpdir,
                limit=manifest_limit,
            )
            _record_check("external_format_matrix_manifest", ok, detail)

    peak_rss_mb = _runtime_selftest_peak_rss_mb()
    if peak_rss_mb is not None:
        summary["peak_rss_mb"] = peak_rss_mb
    print("ALPHA_FIXER_RUNTIME_SELFTEST=" + json.dumps(summary, sort_keys=True))
    return 0 if bool(summary.get("passed")) else 1


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


# ---------------------------------------------------------------------------
# Recent action history – circular buffer used by the crash dialog (item 41)
# ---------------------------------------------------------------------------

import collections as _collections

# Ring buffer of the most recent user-visible actions (button clicks, tool
# changes, file operations, etc.).  Kept to 30 entries; each entry is a str.
_ACTION_HISTORY: "_collections.deque[str]" = _collections.deque(maxlen=30)


def log_action(description: str) -> None:
    """Record a user action in the recent-actions ring buffer.

    Called from throughout the application (alpha_tool, converter_tool, etc.)
    so that the crash dialog can show what the user was doing before the crash.
    """
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%H:%M:%S")
    _ACTION_HISTORY.append(f"[{ts}] {description}")


# ---------------------------------------------------------------------------
# Crash dialog – human-readable, fully selectable, copyable
# ---------------------------------------------------------------------------

def _explain_error(exc_type, exc_value) -> str:
    """Return a plain-English one-liner for common exception types."""
    name = exc_type.__name__ if exc_type else "Error"
    msg  = str(exc_value) if exc_value else ""
    if name == "NameError":
        return f"A name was used before it was defined: {msg}"
    if name == "AttributeError":
        return f"An object did not have the expected attribute: {msg}"
    if name == "ImportError" or name == "ModuleNotFoundError":
        return f"A required module could not be imported: {msg}"
    if name == "TypeError":
        return f"A function was called with the wrong argument type: {msg}"
    if name == "ValueError":
        return f"A function received an invalid value: {msg}"
    if name == "FileNotFoundError":
        return f"A required file was not found: {msg}"
    if name == "PermissionError":
        return f"Permission denied when accessing a file or resource: {msg}"
    if name == "MemoryError":
        return "The application ran out of memory."
    if name == "RecursionError":
        return "Maximum recursion depth exceeded (likely an infinite loop in code)."
    if name == "KeyboardInterrupt":
        return "The application was interrupted by the user."
    return f"{name}: {msg}"


def _show_crash_dialog(
    title: str,
    summary: str,
    traceback_text: str,
    sysinfo: str,
    fatal: bool = False,
    exc_type=None,
) -> None:
    """Show an improved crash dialog with fully selectable, copyable text.

    *summary*       – a short, human-readable description of what went wrong.
    *traceback_text* – the raw Python traceback string.
    *sysinfo*       – system / library version info.
    *fatal*         – when True the application will exit after the dialog.
    *exc_type*      – the exception class for displaying the error type header.
    """
    try:
        from PyQt6.QtWidgets import (
            QApplication, QDialog, QVBoxLayout, QHBoxLayout,
            QLabel, QPlainTextEdit, QPushButton, QFrame,
        )
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont, QClipboard

        app = QApplication.instance()
        if app is None:
            return

        dlg = QDialog()
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(600, 480)
        dlg.resize(760, 560)
        dlg.setWindowFlags(
            dlg.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        # Dark-ish stylesheet that stays readable regardless of system theme.
        dlg.setStyleSheet("""
            QDialog {
                background: #1e1e2e;
                color: #cdd6f4;
            }
            QLabel#title_lbl {
                color: #f38ba8;
                font-size: 15px;
                font-weight: bold;
                padding: 4px 0;
            }
            QLabel#error_type_lbl {
                color: #fab387;
                font-size: 12px;
                font-weight: bold;
                background: #1e1e2e;
                padding: 2px 0;
            }
            QLabel#summary_lbl {
                color: #cdd6f4;
                font-size: 12px;
                background: #313244;
                border-radius: 4px;
                padding: 8px 10px;
            }
            QLabel#log_lbl {
                color: #a6adc8;
                font-size: 10px;
            }
            QPlainTextEdit {
                background: #11111b;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                font-family: Consolas, "Courier New", monospace;
                font-size: 10px;
                selection-background-color: #89b4fa;
                selection-color: #1e1e2e;
            }
            QPushButton {
                background: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 5px 14px;
                font-size: 11px;
                min-height: 26px;
            }
            QPushButton:hover { background: #45475a; }
            QPushButton:pressed { background: #585b70; }
            QPushButton#btn_close {
                background: #f38ba8;
                color: #1e1e2e;
                border: none;
                font-weight: bold;
            }
            QPushButton#btn_close:hover { background: #eba0ac; }
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        # ── Title ────────────────────────────────────────────────────────
        title_lbl = QLabel("💥  " + title)
        title_lbl.setObjectName("title_lbl")
        layout.addWidget(title_lbl)

        # ── Human-readable summary ───────────────────────────────────────
        # Show the error type as a distinct highlighted label so it is
        # immediately obvious even before reading the full traceback.
        error_type_name = exc_type.__name__ if exc_type else "Error"
        error_type_lbl = QLabel(f"⚠  Error type:  {error_type_name}")
        error_type_lbl.setObjectName("error_type_lbl")
        error_type_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(error_type_lbl)

        summary_lbl = QLabel(summary)
        summary_lbl.setObjectName("summary_lbl")
        summary_lbl.setWordWrap(True)
        summary_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(summary_lbl)

        # ── Divider ──────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #45475a;")
        layout.addWidget(line)

        # ── Full details (traceback + sysinfo) – fully selectable ────────
        details_label = QLabel("Full details  (select all with Ctrl+A, copy with Ctrl+C):")
        details_label.setObjectName("log_lbl")
        layout.addWidget(details_label)

        full_text = traceback_text.rstrip()
        if sysinfo:
            full_text += f"\n\n─── System Info ───\n{sysinfo}"
        # Include the recent action history so the reporter can see what led to the crash
        if _ACTION_HISTORY:
            full_text += "\n\n─── Recent Actions (most recent last) ───\n"
            full_text += "\n".join(_ACTION_HISTORY)
        if log_file:
            full_text += f"\n\n─── Log file ───\n{log_file}"

        details_edit = QPlainTextEdit(full_text)
        details_edit.setReadOnly(True)
        details_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(details_edit, 1)

        # ── Button row ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_copy = QPushButton("📋  Copy All")
        btn_copy.setObjectName("btn_copy")
        btn_copy.setToolTip("Copy the full crash details to the clipboard")

        def _copy_all():
            cb = QApplication.clipboard()
            cb.setText(full_text)
            btn_copy.setText("✅  Copied!")

        btn_copy.clicked.connect(_copy_all)
        btn_row.addWidget(btn_copy)
        btn_row.addStretch()

        label_action = "Exit Application" if fatal else "Close (app will try to continue)"
        btn_close = QPushButton(("🚪  " if fatal else "✖  ") + label_action)
        btn_close.setObjectName("btn_close")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

        dlg.exec()
    except Exception:
        # If the crash dialog itself fails, fall back silently – the error
        # was already written to the log file.
        pass


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
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            explanation = _explain_error(exc_type, exc_value)
            summary = (
                "An unexpected error occurred.  "
                "The application will try to continue running.\n\n"
                f"Explanation: {explanation}"
            )
            _show_crash_dialog(
                title="Unexpected Error 🐼",
                summary=summary,
                traceback_text=msg,
                sysinfo=sysinfo,
                fatal=False,
                exc_type=exc_type,
            )
    except Exception:
        pass
    finally:
        _excepthook_active = False


sys.excepthook = _excepthook


# ---------------------------------------------------------------------------
# Native-signal crash handler (items 18/32/58/63/64)
# ---------------------------------------------------------------------------
# Qt can abort the process via SIGABRT (failed assertions, double-free, etc.)
# or SIGFPE (FP exceptions) in ways that bypass Python's sys.excepthook,
# producing a completely silent crash ("no log or crash window").
# Installing a signal handler lets us at least write the crash to the log
# file before the process dies so the user has something to attach to a
# bug report.  We deliberately keep the handler minimal and
# signal-handler-safe: just write to the log, then re-raise the default
# handler so the OS can generate a core-dump / WER report as usual.

def _install_native_signal_handlers() -> None:
    """Install POSIX signal handlers for SIGABRT and SIGFPE.

    Only attempted on platforms where these signals are available
    (all POSIX systems and Windows via the C runtime).  Any
    AttributeError or ValueError is silently ignored so that
    missing signal numbers don't prevent the app from starting.
    """
    import signal as _signal

    def _make_handler(sig_name: str):
        def _handler(signum, frame):
            try:
                import traceback as _tb
                lines = [
                    f"\n{'=' * 60}",
                    f"  NATIVE CRASH — signal {sig_name} ({signum}) received",
                    f"  This is a hard crash (Qt assertion / memory fault).",
                    f"  Python stack trace at the time of the signal:",
                    "=" * 60,
                ]
                if frame is not None:
                    lines += _tb.format_stack(frame)
                logger.critical("\n".join(lines))
            except Exception:
                pass  # logging itself must not raise
            # Re-raise the default signal so the OS can produce a core dump.
            _signal.signal(signum, _signal.SIG_DFL)
            _signal.raise_signal(signum)
        return _handler

    for _sig_attr, _name in (
        ("SIGABRT", "SIGABRT"),
        ("SIGFPE",  "SIGFPE"),
    ):
        try:
            _sig = getattr(_signal, _sig_attr)
            _signal.signal(_sig, _make_handler(_name))
        except (AttributeError, ValueError, OSError):
            pass  # Signal not available on this platform


_install_native_signal_handlers()


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
    if _runtime_capability_dump_requested():
        sys.exit(_emit_runtime_capability_dump())

    # Add src to path so relative imports work when run directly
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    parent_dir = os.path.dirname(src_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    # Run the pre-flight check before anything else
    if not _check_system_libs():
        sys.exit(1)

    if _runtime_selftest_iterations() > 0:
        sys.exit(_emit_runtime_selftest_dump())

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QCoreApplication, Qt, QTimer, qInstallMessageHandler
    from PyQt6.QtGui import QFont
    from src.version import APP_INTERNAL_NAME, APP_NAME

    QCoreApplication.setApplicationName(APP_INTERNAL_NAME)
    QCoreApplication.setOrganizationName("PandaTools")
    # AA_UseHighDpiPixmaps was removed in Qt6; high-DPI pixmaps are always
    # enabled by default in Qt6/PyQt6 so no setAttribute call is needed.
    # Enable per-monitor DPI awareness so each window rescales correctly when
    # dragged between monitors with different scale factors.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    _install_qt_message_filter(qInstallMessageHandler)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Consistent baseline across all platforms

    # --- Single-instance guard -------------------------------------------------
    # Must come *after* QApplication is created so that QLockFile and the
    # fallback QMessageBox both have a running Qt event loop to work with.
    # The returned lock object MUST stay alive until the process exits.
    _instance_lock = _acquire_single_instance_lock()  # noqa: F841 – must stay alive

    logger.info("Starting %s", APP_NAME)

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
        tb_str = traceback.format_exc()
        explanation = _explain_error(type(exc), exc)
        summary = (
            f"A required library is missing and the application cannot start.\n\n"
            f"Explanation: {explanation}\n\n"
            "Install all dependencies with:\n"
            "    pip install -r requirements.txt"
        )
        _show_crash_dialog(
            title="Startup Error — Missing Library 🐼",
            summary=summary,
            traceback_text=tb_str,
            sysinfo=sysinfo,
            fatal=True,
            exc_type=type(exc),
        )
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
        explanation = _explain_error(type(exc), exc)
        summary = (
            "The main window could not be created and the application cannot start.\n\n"
            f"Explanation: {explanation}"
        )
        _show_crash_dialog(
            title="Startup Error 🐼",
            summary=summary,
            traceback_text=tb_str,
            sysinfo=sysinfo,
            fatal=True,
            exc_type=type(exc),
        )
        sys.exit(1)

    runtime_capabilities = _runtime_capability_summary()
    window.set_runtime_capability_summary(runtime_capabilities)

    # Close splash and reveal main window after the splash duration
    if splash is not None:
        QTimer.singleShot(2800, lambda: splash.finish_and_close(window))

    window.show()

    runtime_notice = str(runtime_capabilities.get("packaged_runtime_notice") or "")
    if runtime_notice:
        logger.warning(runtime_notice)
        QTimer.singleShot(900, lambda: window.statusBar().showMessage(runtime_notice, 12000))

    optional_qt_notice = _optional_qt_runtime_notice()
    if optional_qt_notice:
        runtime_capabilities["optional_qt_notice"] = optional_qt_notice
        window.set_runtime_capability_summary(runtime_capabilities)
        logger.info(optional_qt_notice)
        QTimer.singleShot(1400, lambda: window.statusBar().showMessage(optional_qt_notice, 12000))

    capability_notice = str(runtime_capabilities.get("feature_readiness_notice") or "")
    if capability_notice:
        logger.warning(capability_notice)
        QTimer.singleShot(1900, lambda: window.statusBar().showMessage(capability_notice, 12000))

    smoke_test_ms = _smoke_test_duration_ms()
    if smoke_test_ms > 0:
        logger.info("Smoke-test launch mode enabled; auto-exiting after %d ms", smoke_test_ms)
        QTimer.singleShot(smoke_test_ms, app.quit)

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
