# 🐼 Alpha Fixer & File Converter

A panda-themed desktop application with two powerful tools:

## Tools

### 🖼 Alpha Fixer
Fix, adjust, and batch-process alpha channels on image files (PNG, DDS, JPG, JPEG/JFIF/JPE, BMP, TIFF, GIF, WEBP, TGA, ICO, PBM, PGM, PNM, PPM, PCX, AVIF, QOI, SVG, JPEG2000, XNB, TIM).

SVG, XNB, and TIM can be saved after processing.

**Features:**
- Built-in presets: **PS2** (128), **N64** (255), **No Alpha**, **Max Alpha**, **Transparent**, **Half Transparent**, **Invert Alpha**, **Threshold Cut**
- Presets reflect their exact values when selected
- Save and manage your own custom presets
- Fine-tune mode: set, multiply, add, subtract, clamp (per-pixel control)
- Threshold: only affect pixels below a certain alpha value
- Batch process entire folders and subfolders
- Custom output folder / filename suffix

### 🔄 File Converter
Convert between image formats with optional resize and quality control.

**Supported formats:** PNG, JPEG (including `.jfif` / `.jpe`), BMP, TIFF, WEBP, TGA, ICO, GIF, DDS, PBM, PGM, PNM, PPM, PCX, AVIF, QOI, SVG, JPEG2000, XNB, TIM

**Features:**
- Convert any supported format to any other
- Batch convert whole folders and subfolders (preserves directory structure)
- Optional resize (width × height)
- JPEG/WEBP/AVIF/JPEG2000 quality control
- Custom output folder

## UI & Customization
- 🐼 **18 built-in themes**: Panda Dark (default), Panda Light, Neon Panda, Gore, Bat Cave, Rainbow Chaos, Otter Cove, Galaxy, Galaxy Otter, Goth, Volcano 🌋, Arctic ❄, Fairy Garden 🧚, Mermaid 🧜, Shark Bait 🦈, Alien 🛸, Noodle 🍜, Pancake 🥞
- **🔓 39 hidden unlockable themes** – earn them through use (clicks, alpha fixes, and conversions):
  - **Secret Skeleton** – unlocks at 100 total clicks
  - **Secret Sakura 🌸** – unlocks at 250 total clicks
  - Plus 37 more hidden themes that unlock progressively — keep using the app!
- Fully customizable color palette via Settings → Theme (15 editable colors)
- Save your own named themes and switch between them
- **Per-theme click particle effects**: blood splatter (Gore), bat swarms + periodic flyovers (Bat Cave), unicorn sparkles (Rainbow Chaos), otter emojis (Otter Cove), star clusters (Galaxy/Galaxy Otter), skulls (Goth), rising flames (Volcano 🔥), snowflakes (Arctic ❄), pandas (Panda Dark/Light/Secret Sakura 🐼), electric bolts (Neon Panda ⚡)
- **11 mouse trail styles**: Dots, Ribbon, Noodle 🍜 (physics), Comet, Fairy Dust ✨, Wave 🌊, Sparkle ❄, Rainbow 🌈, Distortion Wave, Fire 🔥, Lightning ⚡ — configurable color and intensity
- **Animated banner** with 10 styles: Spin, Bounce, Shake, Pendulum, Pulse, Float, Flip, Orbit, Glitch, Static (Settings → Theme)
- **Animated emoji cursors**: spin (⭐🔮💎), wobble (🦈🐉🌋), and symbol-cycling — automatically selected per theme or fully customizable (Settings → General)
- Custom cursor style (Default, Cross, Pointing Hand, Open Hand, and more)
- Click sound effects (built-in synthetic beep or point to your own .wav file)
- Font size control (8–24pt)
- **Cycling tooltips** with 4 modes (Settings → General → Tooltip Mode):
  - **Normal** – 5 helpful variants per widget, cycles on each hover
  - **Off** – tooltips disabled
  - **Dumbed Down** – simplified tips with gentle user-roasting
  - **No Filter 🤬** – extremely vulgar, profanity-filled, and *still actually helpful*
- All settings are persisted across sessions (last-used preset, format, quality, window geometry, etc.)
- **Export / Import all settings** to a portable JSON file (Settings → Export / Import)
- Drag-and-drop files from Explorer/Finder directly onto the file lists
- Right-click or Delete key to remove items from file lists
- **Image preview pane** – select any file in the Converter list to see a live thumbnail + dimensions + size
- **Before/After comparison slider** (Alpha Fixer) – select a file to see the original and processed result side by side, separated by a draggable red handle; drag left/right to reveal more of either side; auto-updates when preset or fine-tune settings change
- **Processing history tab** – all past sessions (Converter **and** Alpha Fixer) recorded with timestamp, preset/format, and file count; split into two sub-tabs
- **Selective Alpha Tool** – paint alpha zones directly on an image with up to 40 color-coded zones, brush/eraser tools, transform (move/rotate/scale), zone masks, and clipboard slots
- **Single-instance protection** – if you try to open the app a second time while it is already running, a friendly warning is shown instead of launching a duplicate window
- **HiDPI & multi-monitor aware** – fractional DPI scaling (125 %, 150 %, 200 %) and multiple displays are fully supported; window position is automatically corrected if a monitor is disconnected
- **Small-screen dialog access** – GIF/Video Builder windows fit the current screen, with scrollable controls when space or larger fonts require it. Tutorial, GIF frame selection, and shortcut dialogs also fit the available screen area.
- **GIF frame selection safeguards** – exporting requires at least one selected frame; unreadable GIFs cannot be accepted as empty exports.
- **❤ Patreon button** – support development at [patreon.com/c/DeadOnTheInside](https://www.patreon.com/c/DeadOnTheInside)
- **Keyboard shortcuts** (F1 for full list):
  - `F5` – Run / Process / Convert
  - `Esc` – Stop current operation
  - `Ctrl+O` – Add files
  - `Ctrl+Shift+O` – Add folder
  - `Delete` – Remove selected files from list
  - `Ctrl+,` – Open Settings
  - `Ctrl+Q` – Quit

## Requirements

- Python 3.10+
- PyQt6 ≥ 6.4.0
- Pillow ≥ 10.0.0
- numpy ≥ 1.24.0
- imageio ≥ 2.33.0
- wand ≥ 0.6.13 (for DDS via ImageMagick — optional but recommended)

Install Python dependencies:
```bash
pip install -r requirements.txt
```

### Linux system libraries

PyQt6 requires several system-level shared libraries, including the extra X11/XCB packages needed by the packaged Linux app. Use the one-shot installer:

```bash
bash scripts/install_linux_deps.sh
```

Or install manually by distribution:

| Library | Ubuntu / Debian | Fedora / RHEL | Arch | openSUSE |
|---|---|---|---|---|
| libEGL (`libegl1`) | `sudo apt-get install -y libegl1` | `sudo dnf install -y mesa-libEGL` | `sudo pacman -S mesa` | `sudo zypper install -y libEGL1` |
| libGL (`libgl1`) | `sudo apt-get install -y libgl1` | `sudo dnf install -y mesa-libGL` | *(included)* | `sudo zypper install -y libGL1` |
| libpulse (`libpulse0`) | `sudo apt-get install -y libpulse0` | `sudo dnf install -y pulseaudio-libs` | `sudo pacman -S libpulse` | `sudo zypper install -y libpulse0` |
| Qt X11/XCB extras (`libxcb-cursor.so.0`, `libxkbcommon-x11.so.0`, etc.) | `sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0` | `sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm` | `sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm` | `sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0` |

If any of these are missing, `main.py` will detect the problem at startup and print the exact install command for your distribution before exiting cleanly — no cryptic crashes.

For DDS support also install [ImageMagick](https://imagemagick.org/).
BC6H / BC7 / other advanced DX10 DDS variants are currently only supported when Pillow or ImageMagick/wand can decode them directly; the pure-Python fallback now fails clearly instead of fabricating placeholder pixels.

## Running

```bash
python main.py
```

## Building a Standalone Executable

You can package the app locally using [PyInstaller](https://pyinstaller.org/).

### Quick build

```bash
# Install build tools
pip install -r requirements-dev.txt

# Linux / macOS
bash scripts/build_exe.sh

# Windows
scripts\build_exe.bat
```

The finished application is placed in `dist/AlphaFixerConverter/`.
Run `AlphaFixerConverter` (Linux/macOS) or `AlphaFixerConverter.exe` (Windows) from that folder.
Both build scripts now reuse `scripts/verify_packaged_app.py` to smoke-launch the packaged app and dump runtime capabilities, so the same verification step can be re-run manually on a fresh machine later.

### Single-file build (slower startup)

```bash
bash scripts/build_exe.sh --onefile      # Linux / macOS
scripts\build_exe.bat --onefile          # Windows
```

### Manual PyInstaller invocation

The app ships with a fully-configured spec file:

```bash
pyinstaller alpha_fixer.spec
```

## Running Tests

```bash
python -m pytest tests/ -v
```

### Real corpus validation

Optional real-world corpus tests already exist for odd video containers / disc images and DDS samples. Because many of the most relevant PSP / PS1 / PS2 corpora are large, private, or copyrighted, they are **not** bundled in this repository.

This repo now also ships small **public-download manifest examples** under `sample_manifests/` so fresh machines can exercise real external samples without needing private corpora:

- `sample_manifests/public_disc_video_manifest.json`
- `sample_manifests/public_dds_dx10_manifest.json`
- `sample_manifests/public_format_matrix_manifest.json`

Those built-in manifests intentionally use public sample files that are legally redistributable or publicly downloadable, but they are **not a replacement** for true copyrighted PSP / PS1 / PS2 validation corpora. They are meant to provide a reproducible baseline for packaged runtime checks and fresh-machine smoke coverage.

There are also starter **private manifest templates** for local corpora you cannot redistribute publicly:

- `sample_manifests/private_psp_ps1_ps2_disc_manifest_template.json`
- `sample_manifests/private_odd_container_manifest_template.json`
- `sample_manifests/private_odd_container_video_manifest_template.json`
- `sample_manifests/private_dds_complex_manifest_template.json`

Suggested split:

- `private_psp_ps1_ps2_disc_manifest_template.json` – disc-image / cue-sheet / UMD / PMF / PSS / STR style console media
- `private_odd_container_manifest_template.json` – console and legacy odd-container clips like `.pss`, `.str`, `.vob`, `.asf`, `.wmv`, `.mxf`, and odd-extension samples
- `private_odd_container_video_manifest_template.json` – broader non-console edge cases such as multi-stream MKV, cover-art containers, odd MOV/AVI metadata, and audio-only misdrops
- `private_dds_complex_manifest_template.json` – local cubemap / array / volume / mipmap / BC6H DDS corpora

You can point the tests at external corpora with either directories or JSON manifest files:

```bash
# Directory-based corpora
export ALPHA_FIXER_REAL_VIDEO_CORPUS="/path/to/video-corpus"
export ALPHA_FIXER_REAL_DISC_VIDEO_CORPUS="/path/to/psp-ps1-ps2-disc-samples"
export ALPHA_FIXER_REAL_DDS_CORPUS="/path/to/dds-corpus"
export ALPHA_FIXER_REAL_DDS_DX10_CORPUS="/path/to/dds-dx10-corpus"

# Manifest-file based corpora (preferred for curated PSP / PS1 / PS2 / DDS sample sets)
export ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST="/path/to/disc_video_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST="/path/to/odd_container_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST="/path/to/odd_container_video_manifest.json"
export ALPHA_FIXER_REAL_DDS_DX10_MANIFEST="/path/to/dds_dx10_manifest.json"
export ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST="/path/to/dds_complex_manifest.json"
```

Manifest environment variables may contain either:
- a JSON array directly, or
- a path to a JSON file on disk

Manifest files can use relative sample paths, which resolve relative to the manifest file itself. They may also wrap entries in a top-level object with `samples` or `entries`, and can provide a `base_dir`.
Entries may also include `url` / `download_url`, optional `sha256`, `download_name`, and `cache_subdir` fields when you want the same manifest to populate samples onto a fresh machine.

Example manifest:

```json
{
  "base_dir": "/mnt/corpora/psp-disc-video",
  "samples": [
    {
      "path": "sample01.iso",
      "expect": "load_or_explain",
      "hint_contains": ["Disc-image video inputs are experimental"]
    }
  ]
}
```

To populate a manifest into a local cache directory before running tests or packaged self-tests:

```bash
python scripts/populate_sample_manifest.py /path/to/disc_video_manifest.json \
  --cache-dir /tmp/alpha_fixer_corpus_cache \
  --copy-local \
  --allow-downloads \
  --output-manifest /tmp/materialized_disc_video_manifest.json
```

That `--copy-local` step is the easiest way to freeze a private local corpus into a clean test cache before packaging/fresh-machine runs. It also keeps sidecars (for example cue/bin companions) beside the copied primary sample when the manifest declares them.

You can also opt into download-backed manifest entries directly during test/self-test runs:

```bash
export ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS=1
export ALPHA_FIXER_SAMPLE_CACHE_DIR=/tmp/alpha_fixer_corpus_cache
```

For the built-in public manifests:

```bash
python scripts/populate_sample_manifest.py sample_manifests/public_disc_video_manifest.json \
  --cache-dir /tmp/alpha_fixer_public_cache \
  --allow-downloads \
  --output-manifest /tmp/public_disc_video_manifest.materialized.json

python scripts/populate_sample_manifest.py sample_manifests/public_format_matrix_manifest.json \
  --cache-dir /tmp/alpha_fixer_public_cache \
  --allow-downloads \
  --output-manifest /tmp/public_format_matrix_manifest.materialized.json
```

Packaged runtime self-tests can also consume external manifests directly on fresh machines:

```bash
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --run-selftest \
  --selftest-iterations 8 \
  --selftest-sample-limit 12 \
  --selftest-stress-loops 3 \
  --disc-video-manifest sample_manifests/public_disc_video_manifest.json \
  --format-matrix-manifest sample_manifests/public_format_matrix_manifest.json \
  --allow-sample-downloads \
  --sample-cache-dir /tmp/alpha_fixer_packaged_cache \
  --require-selftest-pass \
  --require-stress-selftest-checks \
  --require-selftest-check external_disc_video_manifest \
  --require-selftest-check external_format_matrix_manifest
```

For private local corpora, you can repeat manifest flags to merge multiple edge-case sets into one packaged self-test run:

```bash
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --run-selftest \
  --selftest-iterations 6 \
  --selftest-sample-limit 16 \
  --selftest-stress-loops 3 \
  --disc-video-manifest /path/to/private_psp_ps1_ps2_disc_manifest.json \
  --disc-video-manifest /path/to/private_odd_container_manifest.json \
  --disc-video-manifest /path/to/private_odd_container_video_manifest.json \
  --dds-manifest /path/to/private_dds_complex_manifest.json \
  --allow-sample-downloads \
  --sample-cache-dir /tmp/alpha_fixer_private_cache \
  --require-selftest-pass \
  --require-video-selftest-checks \
  --require-stress-selftest-checks \
  --require-disc-manifest-group-checks \
  --require-dds-manifest-group-checks
```

That grouped-manifest mode is useful when you want separate pass/fail reporting for PSP vs PS1 vs PS2 clips, or for DDS groups like `cubemap`, `array`, and `volume`.

The packaged build scripts can also pick those private manifests up automatically from the same environment variables:

```bash
export ALPHA_FIXER_VERIFY_PRIVATE_SAMPLE_MANIFESTS=1
export ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST="/path/to/private_psp_ps1_ps2_disc_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST="/path/to/private_odd_container_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST="/path/to/private_odd_container_video_manifest.json"
export ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST="/path/to/private_dds_complex_manifest.json"
export ALPHA_FIXER_PRIVATE_SAMPLE_LIMIT=16
export ALPHA_FIXER_PRIVATE_SELFTEST_ITERATIONS=4
export ALPHA_FIXER_PRIVATE_STRESS_LOOPS=3
bash scripts/build_exe.sh
```

Or call the verifier directly and let it collect the manifests from the environment:

```bash
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --run-selftest \
  --use-private-local-manifests \
  --require-selftest-pass \
  --require-video-selftest-checks \
  --require-disc-manifest-group-checks \
  --require-dds-manifest-group-checks
```

If you are on the machine that already has the private corpora, you can do the manifest-population plus packaged-validation flow in one step:

```bash
python scripts/run_private_packaged_validation.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --output-dir dist/private-runtime-validation \
  --manifest-limit 24 \
  --repeat 3 \
  --repeat-selftest-runs 2 \
  --selftest-stress-loops 4
```

That helper auto-populates private manifests from the configured `ALPHA_FIXER_REAL_*` corpus roots, writes them into the output directory, runs repeated smoke launches plus packaged self-tests, and records JSON timing / RSS summaries for relaunch and memory-trend baselining.

BC6H / BC7 note: the public manifests above improve real external validation coverage, but they do **not** add pure in-repo BC6H / BC7 software decoding. Advanced BC6H / BC7 DDS inspection still depends on Pillow support or optional ImageMagick/wand decoding when available.

Example packaged format-matrix manifest:

```json
{
  "base_dir": "/mnt/corpora/conversion-samples",
  "entries": [
    {
      "input": "transparent_ui.png",
      "target_format": "DDS",
      "dds_variant": "rgba"
    },
    {
      "input": "ps2_texture.bmp",
      "target_format": "PNG"
    },
    {
      "input": "hdr_texture.png",
      "target_format": "DDS",
      "dds_variant": "dxt1",
      "expect": "fail",
      "detail_contains": ["wand", "ImageMagick", "DDS"]
    }
  ]
}
```

To run only the optional real-corpus validations:

```bash
python -m pytest \
  tests/test_ui_components.py -k "optional_real_video_corpus or optional_real_disc_video" \
  tests/test_converter.py -k "optional_real_dds"
```

### Re-running packaged verification on a fresh machine

After copying a built app to another machine, you can rerun the same packaged smoke/capability checks without rebuilding:

```bash
# Linux / macOS example
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --smoke-seconds 2 \
  --repeat 3 \
  --run-selftest \
  --selftest-iterations 4 \
  --selftest-sample-limit 8 \
  --selftest-stress-loops 2 \
  --require-selftest-pass \
  --require-selftest-check generated_mp4_load \
  --require-selftest-check mpegts_load \
  --require-stress-selftest-checks \
  --require-video-runtime \
  --require-no-missing-libs
```

The verifier smoke-launches the packaged app, performs the runtime capability dump, and can be repeated multiple times to catch packaging regressions that only appear after several launches. With `--run-selftest`, the packaged executable also generates a tiny built-in media/format matrix (GIF, DDS, MP4, MPEG-TS, and a synthetic odd-extension probe) so fresh-machine checks can validate more than just startup. When you provide `--disc-video-manifest`, `--dds-manifest`, or `--format-matrix-manifest`, the packaged app also executes those external real-sample sets in-process and reports them as `external_disc_video_manifest`, `external_dds_manifest`, and `external_format_matrix_manifest` self-test checks.

Windows packaged example:

```powershell
python scripts/verify_packaged_app.py dist\AlphaFixerConverter\AlphaFixerConverter.exe `
  --smoke-seconds 2 `
  --repeat 3 `
  --timeout 45 `
  --run-selftest `
  --selftest-iterations 6 `
  --selftest-stress-loops 3 `
  --use-public-sample-manifests `
  --allow-sample-downloads `
  --sample-cache-dir artifacts\sample-cache `
  --require-selftest-pass `
  --require-video-selftest-checks `
  --require-stress-selftest-checks `
  --require-public-manifest-group-checks `
  --require-ffmpeg-selfcheck `
  --require-ffprobe-selfcheck `
  --require-bundled-ffmpeg `
  --require-bundled-ffprobe
```

The repository also includes a dedicated GitHub Actions workflow, `.github/workflows/fresh-machine-runtime.yml`, which runs the packaged verifier on hosted Ubuntu and Windows machines with repeated smoke launches and runtime self-tests.

## Architecture

```
src/
  version.py           - App version constant (2.0.0)
  core/
    alpha_processor.py   - Alpha channel processing logic
    file_converter.py    - Image format conversion
    presets.py           - Built-in and custom preset definitions
    worker.py            - Background QThread workers (non-blocking)
    settings_manager.py  - Persistent settings (QSettings) + export/import
  ui/
    main_window.py       - Main window + menu + Patreon link + unlock system
    alpha_tool.py        - Alpha Fixer tab (comparison slider, keyboard shortcuts)
    converter_tool.py    - File Converter tab (image preview, shortcuts, history recording)
    history_tab.py       - Conversion History tab (timestamped, colour-coded)
    preview_pane.py      - ImagePreviewPane thumbnail + BeforeAfterWidget comparison slider
    selective_alpha_tool.py - Selective Alpha Tool (zone painting, transform, mask slots)
    settings_dialog.py   - Settings dialog (themes, effects, tooltip mode, unlock display)
    theme_engine.py      - Qt stylesheet generator + 57 theme palettes (18 preset + 39 hidden) + THEME_EFFECTS map
    click_effects.py     - Per-theme click particle overlay (blood, bats, stars, skulls, otters)
    tooltip_manager.py   - Cycling tooltip engine: Normal / Off / Dumbed Down / No Filter
    drop_list.py         - DropFileList: drag-and-drop, Delete key, right-click remove
    mouse_trail.py       - Mouse trail particle overlay (11 styles)
    sound_engine.py      - Click sound engine (QSoundEffect + fallback)
    splash_screen.py     - Animated themed startup splash screen
tests/
  test_core.py           - Unit tests for alpha processing & presets
  test_converter.py      - Unit tests for file conversion
  test_ui_components.py  - Unit tests for all UI components
main.py                  - Entry point with crash prevention, logging, libEGL check
alpha_fixer.spec         - PyInstaller build spec
scripts/
  install_linux_deps.sh  - One-shot system-library installer (libegl1, libpulse0, ...)
  build_exe.sh           - Linux / macOS standalone build script
  build_exe.bat          - Windows standalone build script
```
