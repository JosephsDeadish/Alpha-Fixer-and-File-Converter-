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
    mkdir -p dist/validation-reports
    verify_args=(
        "$launch_target"
        --smoke-seconds 1.5
        --timeout 25
        --require-video-runtime
        --require-ffmpeg-selfcheck
        --require-ffprobe-selfcheck
        --require-bundled-ffmpeg
        --require-bundled-ffprobe
        --require-no-missing-libs
        --json-out "dist/validation-reports/packaged-runtime-audit.json"
    )
    if [[ "${ALPHA_FIXER_VERIFY_PUBLIC_SAMPLE_MANIFESTS:-0}" == "1" ]]; then
        sample_limit="${ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT:-4}"
        sample_cache_dir="${ALPHA_FIXER_SAMPLE_CACHE_DIR:-$REPO_ROOT/.sample-cache}"
        mkdir -p "$sample_cache_dir"
        verify_args+=(
            --run-selftest
            --selftest-iterations 2
            --selftest-sample-limit "$sample_limit"
            --require-selftest-pass
            --require-core-selftest-checks
            --use-public-sample-manifests
            --require-video-selftest-checks
            --require-public-manifest-checks
            --require-public-manifest-group-checks
            --allow-sample-downloads
            --sample-cache-dir "$sample_cache_dir"
        )
        if [[ "${ALPHA_FIXER_REQUIRE_DDS_SELFTEST:-0}" == "1" ]]; then
            verify_args+=(
                --require-dds-selftest-checks
                --require-wand-runtime
                --require-bundled-imagemagick
                --require-no-packaged-asset-gaps
            )
        fi
    fi
    python scripts/verify_packaged_app.py "${verify_args[@]}"
else
    echo "⚠️  Skipping packaged launch smoke test because executable was not found at $launch_target"
fi

echo ""
echo "✅  Build complete!"
echo "   Output: $(pwd)/dist/AlphaFixerConverter"
