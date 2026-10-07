#!/usr/bin/env bash
# build_exe.sh – Build a standalone executable for Linux / macOS
#
# Usage:
#   bash scripts/build_exe.sh            # one-folder build (default)
#   bash scripts/build_exe.sh --onefile  # single-file build
#
# The finished app lands in  dist/AlphaFixerConverter/  (or dist/AlphaFixerConverter).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

# ── 1. Check / install PyInstaller ───────────────────────────────────────────
if ! python -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller not found – installing…"
    python -m pip install pyinstaller
fi

# ── 2. Sync runtime dependencies ─────────────────────────────────────────────
echo "Installing runtime dependencies from requirements.txt…"
python -m pip install -r requirements.txt

python - <<'PY'
import importlib.util
import os
import ctypes

has_wand = importlib.util.find_spec("wand") is not None
magick_home = os.environ.get("MAGICK_HOME") or os.environ.get("IMAGEMAGICK_HOME")
runtime_libs = [
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
]
missing_runtime_libs = []
for name in runtime_libs:
    try:
        ctypes.CDLL(name)
    except OSError:
        missing_runtime_libs.append(name)
print("Build capability audit:")
print("  - imageio/imageio-ffmpeg runtime support will be bundled by the PyInstaller spec.")
if has_wand and magick_home:
    print(f"  - DDS compressed variants can be bundled for out-of-box builds (wand + MAGICK_HOME={magick_home}).")
else:
    print("  - NOTE: Full bundled DDS compression support needs wand plus MAGICK_HOME/IMAGEMAGICK_HOME set at build time.")
if missing_runtime_libs:
    print("  - WARNING: Packaging host is missing Linux runtime libs needed for a fully launchable Qt build:")
    for name in missing_runtime_libs:
        print(f"      * {name}")
    print("    Install them first with: bash scripts/install_linux_deps.sh")
PY

# ── 3. Clean previous build artefacts ────────────────────────────────────────
rm -rf build dist __pycache__

# ── 4. Run PyInstaller ────────────────────────────────────────────────────────
if [[ "$1" == "--onefile" ]]; then
    echo "Building single-file executable…"
    pyinstaller alpha_fixer_onefile.spec
    artifact="dist/AlphaFixerConverter"
    artifact_kind="file"
    launch_target="$artifact"
else
    echo "Building one-folder application…"
    pyinstaller alpha_fixer.spec
    artifact="dist/AlphaFixerConverter"
    artifact_kind="dir"
    launch_target="$artifact/AlphaFixerConverter"
fi

echo "Verifying build artifacts…"
if [[ "$artifact_kind" == "file" ]]; then
    [[ -f "$artifact" ]] || { echo "❌  ERROR: Expected executable not found at $artifact"; exit 1; }
    echo "✅  Executable verified: $(du -h "$artifact" | cut -f1)"
else
    [[ -d "$artifact" ]] || { echo "❌  ERROR: Expected app folder not found at $artifact"; exit 1; }
    echo "✅  App folder verified: $(du -sh "$artifact" | cut -f1)"
fi

if [[ -x "$launch_target" ]]; then
    echo "Running packaged launch smoke test…"
    set +e
    QT_QPA_PLATFORM=offscreen ALPHA_FIXER_SMOKE_TEST=1.5 timeout 25s "$launch_target"
    smoke_rc=$?
    set -e
    if [[ $smoke_rc -eq 0 ]]; then
        echo "✅  Packaged app launch verified."
    elif [[ $smoke_rc -eq 124 ]]; then
        echo "❌  ERROR: Packaged launch smoke test timed out after 25 seconds."
        exit 1
    else
        echo "❌  ERROR: Packaged launch smoke test failed with exit code $smoke_rc."
        exit "$smoke_rc"
    fi

    echo "Running packaged capability audit…"
    set +e
    capability_output="$(ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP=1 "$launch_target" 2>&1)"
    capability_rc=$?
    set -e
    printf '%s\n' "$capability_output"
    if [[ $capability_rc -ne 0 ]]; then
        echo "❌  ERROR: Packaged capability audit failed with exit code $capability_rc."
        exit "$capability_rc"
    fi
    capability_json="$(printf '%s\n' "$capability_output" | sed -n 's/^ALPHA_FIXER_RUNTIME_CAPABILITIES=//p' | tail -n 1)"
    if [[ -z "$capability_json" ]]; then
        echo "❌  ERROR: Packaged capability audit did not emit ALPHA_FIXER_RUNTIME_CAPABILITIES output."
        exit 1
    fi
    python - "$capability_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if not payload.get("video_runtime_ready"):
    raise SystemExit("Packaged runtime audit failed: video_runtime_ready=false")
missing_libs = payload.get("missing_linux_runtime_libs") or []
if missing_libs:
    raise SystemExit(
        "Packaged runtime audit failed: missing_linux_runtime_libs="
        + ",".join(str(name) for name in missing_libs)
    )
if not payload.get("odd_container_probe_ready"):
    print("⚠️  Packaged runtime audit: ffprobe unavailable, odd-container probing stays limited.")
if not payload.get("dds_compression_available"):
    print("⚠️  Packaged runtime audit: DDS compressed variants remain unavailable without bundled ImageMagick/wand.")
print("✅  Packaged runtime capability audit verified.")
PY
else
    echo "⚠️  Skipping packaged launch smoke test because executable was not found at $launch_target"
fi

echo ""
echo "✅  Build complete!"
echo "   Output: $(pwd)/dist/AlphaFixerConverter"
