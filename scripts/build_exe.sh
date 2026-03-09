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
    pip install pyinstaller
fi

# ── 2. Clean previous build artefacts ────────────────────────────────────────
rm -rf build dist __pycache__

# ── 3. Run PyInstaller ────────────────────────────────────────────────────────
if [[ "$1" == "--onefile" ]]; then
    echo "Building single-file executable…"
    pyinstaller --onefile --windowed --name AlphaFixerConverter main.py
else
    echo "Building one-folder application…"
    pyinstaller alpha_fixer.spec
fi

echo ""
echo "✅  Build complete!"
echo "   Output: $(pwd)/dist/AlphaFixerConverter"
