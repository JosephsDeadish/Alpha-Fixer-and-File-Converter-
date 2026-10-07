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

has_wand = importlib.util.find_spec("wand") is not None
magick_home = os.environ.get("MAGICK_HOME") or os.environ.get("IMAGEMAGICK_HOME")
print("Build capability audit:")
print("  - imageio/imageio-ffmpeg runtime support will be bundled by the PyInstaller spec.")
if has_wand and magick_home:
    print(f"  - DDS compressed variants can be bundled for out-of-box builds (wand + MAGICK_HOME={magick_home}).")
else:
    print("  - NOTE: Full bundled DDS compression support needs wand plus MAGICK_HOME/IMAGEMAGICK_HOME set at build time.")
PY

# ── 3. Clean previous build artefacts ────────────────────────────────────────
rm -rf build dist __pycache__

# ── 4. Run PyInstaller ────────────────────────────────────────────────────────
if [[ "$1" == "--onefile" ]]; then
    echo "Building single-file executable…"
    pyinstaller alpha_fixer_onefile.spec
else
    echo "Building one-folder application…"
    pyinstaller alpha_fixer.spec
fi

echo ""
echo "✅  Build complete!"
echo "   Output: $(pwd)/dist/AlphaFixerConverter"
