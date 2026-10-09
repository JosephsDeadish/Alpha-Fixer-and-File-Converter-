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
import shutil
import sys
from pathlib import Path

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

ffprobe_source = ""
for env_name in ("ALPHA_FIXER_FFPROBE_EXE", "IMAGEIO_FFPROBE_EXE", "FFPROBE_EXE"):
    configured = os.environ.get(env_name, "").strip()
    if configured and Path(configured).is_file():
        ffprobe_source = configured
        break
if not ffprobe_source:
    configured_ffmpeg = os.environ.get("IMAGEIO_FFMPEG_EXE", "").strip()
    if configured_ffmpeg:
        for candidate in (Path(configured_ffmpeg).with_name("ffprobe"), Path(configured_ffmpeg).with_name("ffprobe.exe")):
            if candidate.is_file():
                ffprobe_source = str(candidate)
                break
if not ffprobe_source:
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = ""
    if ffmpeg_exe:
        for candidate in (Path(ffmpeg_exe).with_name("ffprobe"), Path(ffmpeg_exe).with_name("ffprobe.exe")):
            if candidate.is_file():
                ffprobe_source = str(candidate)
                break
if not ffprobe_source:
    ffprobe_source = shutil.which("ffprobe") or shutil.which("ffprobe.exe") or ""

print("Build capability audit:")
print("  - imageio/imageio-ffmpeg runtime support will be bundled by the PyInstaller spec.")
if ffprobe_source:
    print(f"  - ffprobe bundling source ready: {ffprobe_source}")
else:
    print("  - WARNING: No ffprobe source found for packaging, so packaged odd-container probing will fail strict validation.")
    if sys.platform == "darwin":
        print("    Install it first with: brew install ffmpeg")
    else:
        print("    Install it first with: bash scripts/install_linux_deps.sh")
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
    bundle_kind="onefile"
else
    echo "Building one-folder application…"
    pyinstaller alpha_fixer.spec
    artifact="dist/AlphaFixerConverter"
    artifact_kind="dir"
    launch_target="$artifact/AlphaFixerConverter"
    bundle_kind="folder"
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
        --max-smoke-elapsed-seconds 20
        --max-smoke-elapsed-growth-seconds 8
        --max-smoke-elapsed-spread-seconds 8
        --require-video-runtime
        --require-ffmpeg-selfcheck
        --require-ffprobe-selfcheck
        --require-bundled-ffmpeg
        --require-bundled-ffprobe
        --require-bundled-default-theme-svg
        --require-packaged-bundle-ready
        --require-no-missing-libs
        --bundle-kind "$bundle_kind"
        --json-out "dist/validation-reports/packaged-runtime-audit-$bundle_kind.json"
    )
    if [[ "${ALPHA_FIXER_VERIFY_PRIVATE_SAMPLE_MANIFESTS:-0}" == "1" ]]; then
        private_sample_limit="${ALPHA_FIXER_PRIVATE_SAMPLE_LIMIT:-${ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT:-12}}"
        private_cache_dir="${ALPHA_FIXER_PRIVATE_SAMPLE_CACHE_DIR:-${ALPHA_FIXER_SAMPLE_CACHE_DIR:-$REPO_ROOT/.sample-cache-private}}"
        private_selftest_iterations="${ALPHA_FIXER_PRIVATE_SELFTEST_ITERATIONS:-4}"
        private_selftest_repeat_runs="${ALPHA_FIXER_PRIVATE_SELFTEST_REPEAT_RUNS:-1}"
        private_stress_loops="${ALPHA_FIXER_PRIVATE_STRESS_LOOPS:-0}"
        private_rss_growth_limit="${ALPHA_FIXER_PRIVATE_MAX_SELFTEST_RSS_GROWTH_MB:-}"
        private_rss_spread_limit="${ALPHA_FIXER_PRIVATE_MAX_SELFTEST_RSS_SPREAD_MB:-}"
        private_smoke_growth_limit="${ALPHA_FIXER_PRIVATE_MAX_SMOKE_ELAPSED_GROWTH_SECONDS:-}"
        private_smoke_spread_limit="${ALPHA_FIXER_PRIVATE_MAX_SMOKE_ELAPSED_SPREAD_SECONDS:-}"
        mkdir -p "$private_cache_dir"
        verify_args+=(
            --run-selftest
            --selftest-iterations "$private_selftest_iterations"
            --selftest-sample-limit "$private_sample_limit"
            --require-selftest-pass
            --require-core-selftest-checks
            --require-video-selftest-checks
            --use-private-local-manifests
            --require-disc-manifest-group-checks
            --require-dds-manifest-group-checks
            --allow-sample-downloads
            --sample-cache-dir "$private_cache_dir"
        )
        if [[ "$private_selftest_repeat_runs" =~ ^[0-9]+$ ]] && (( private_selftest_repeat_runs > 1 )); then
            verify_args+=(
                --repeat-selftest-runs "$private_selftest_repeat_runs"
            )
        fi
        if [[ "$private_stress_loops" =~ ^[0-9]+$ ]] && (( private_stress_loops > 0 )); then
            verify_args+=(
                --selftest-stress-loops "$private_stress_loops"
                --require-stress-selftest-checks
            )
        fi
        if [[ -n "$private_rss_growth_limit" ]]; then
            verify_args+=(--max-selftest-rss-growth-mb "$private_rss_growth_limit")
        fi
        if [[ -n "$private_rss_spread_limit" ]]; then
            verify_args+=(--max-selftest-rss-spread-mb "$private_rss_spread_limit")
        fi
        if [[ -n "$private_smoke_growth_limit" ]]; then
            verify_args+=(--max-smoke-elapsed-growth-seconds "$private_smoke_growth_limit")
        fi
        if [[ -n "$private_smoke_spread_limit" ]]; then
            verify_args+=(--max-smoke-elapsed-spread-seconds "$private_smoke_spread_limit")
        fi
    fi
    if [[ "${ALPHA_FIXER_VERIFY_PUBLIC_SAMPLE_MANIFESTS:-0}" == "1" ]]; then
        sample_limit="${ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT:-4}"
        sample_cache_dir="${ALPHA_FIXER_SAMPLE_CACHE_DIR:-$REPO_ROOT/.sample-cache}"
        public_selftest_repeat_runs="${ALPHA_FIXER_PUBLIC_SELFTEST_REPEAT_RUNS:-1}"
        public_rss_growth_limit="${ALPHA_FIXER_PUBLIC_MAX_SELFTEST_RSS_GROWTH_MB:-}"
        public_rss_spread_limit="${ALPHA_FIXER_PUBLIC_MAX_SELFTEST_RSS_SPREAD_MB:-}"
        public_smoke_growth_limit="${ALPHA_FIXER_PUBLIC_MAX_SMOKE_ELAPSED_GROWTH_SECONDS:-}"
        public_smoke_spread_limit="${ALPHA_FIXER_PUBLIC_MAX_SMOKE_ELAPSED_SPREAD_SECONDS:-}"
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
        if [[ "$public_selftest_repeat_runs" =~ ^[0-9]+$ ]] && (( public_selftest_repeat_runs > 1 )); then
            verify_args+=(
                --repeat-selftest-runs "$public_selftest_repeat_runs"
            )
        fi
        if [[ -n "$public_rss_growth_limit" ]]; then
            verify_args+=(--max-selftest-rss-growth-mb "$public_rss_growth_limit")
        fi
        if [[ -n "$public_rss_spread_limit" ]]; then
            verify_args+=(--max-selftest-rss-spread-mb "$public_rss_spread_limit")
        fi
        if [[ -n "$public_smoke_growth_limit" ]]; then
            verify_args+=(--max-smoke-elapsed-growth-seconds "$public_smoke_growth_limit")
        fi
        if [[ -n "$public_smoke_spread_limit" ]]; then
            verify_args+=(--max-smoke-elapsed-spread-seconds "$public_smoke_spread_limit")
        fi
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
