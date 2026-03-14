# 🐼 Alpha Fixer & File Converter

[![Build Windows EXE](https://github.com/JosephsDeadish/Alpha-Fixer-and-File-Converter-/actions/workflows/build.yml/badge.svg)](https://github.com/JosephsDeadish/Alpha-Fixer-and-File-Converter-/actions/workflows/build.yml)

A panda-themed desktop application with two powerful tools:

## Tools

### 🖼 Alpha Fixer
Fix, adjust, and batch-process alpha channels on image files (PNG, DDS, JPG, BMP, TIFF, WEBP, TGA, ICO, GIF).

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

**Supported formats:** PNG, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF, DDS

**Features:**
- Convert any supported format to any other
- Batch convert whole folders and subfolders (preserves directory structure)
- Optional resize (width × height)
- JPEG/WEBP quality control
- Custom output folder

## UI & Customization
- 🐼 **18 built-in themes**: Panda Dark (default), Panda Light, Neon Panda, Gore, Bat Cave, Rainbow Chaos, Otter Cove, Galaxy, Galaxy Otter, Goth, Volcano 🌋, Arctic ❄, Fairy Garden 🧚, Mermaid 🧜, Shark Bait 🦈, Alien 🛸, Noodle 🍜, Pancake 🥞
- **🔓 32 hidden unlockable themes** – earn them through use (clicks, alpha fixes, and conversions):
  - **Secret Skeleton** – unlocks at 100 total clicks
  - **Secret Sakura 🌸** – unlocks at 250 total clicks
  - Plus 30 more hidden themes that unlock progressively — keep using the app!
- Fully customizable color palette via Settings → Theme (15 editable colors)
- Save your own named themes and switch between them
- **Per-theme click particle effects**: blood splatter (Gore), bat swarms + periodic flyovers (Bat Cave), unicorn sparkles (Rainbow Chaos), otter emojis (Otter Cove), star clusters (Galaxy/Galaxy Otter), skulls (Goth), rising flames (Volcano 🔥), snowflakes (Arctic ❄), pandas (Panda Dark/Light/Secret Sakura 🐼), electric bolts (Neon Panda ⚡)
- Mouse trail effect with configurable color
- Custom cursor style (Default, Cross, Pointing Hand, Open Hand)
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
- **Single-instance protection** – if you try to open the app a second time while it is already running, a friendly warning is shown instead of launching a duplicate window
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

PyQt6 requires several system-level shared libraries.  Use the one-shot installer:

```bash
bash scripts/install_linux_deps.sh
```

Or install manually by distribution:

| Library | Ubuntu / Debian | Fedora / RHEL | Arch | openSUSE |
|---|---|---|---|---|
| libEGL (`libegl1`) | `sudo apt-get install -y libegl1` | `sudo dnf install -y mesa-libEGL` | `sudo pacman -S mesa` | `sudo zypper install -y libEGL1` |
| libGL (`libgl1`) | `sudo apt-get install -y libgl1` | `sudo dnf install -y mesa-libGL` | *(included)* | `sudo zypper install -y libGL1` |
| libpulse (`libpulse0`) | `sudo apt-get install -y libpulse0` | `sudo dnf install -y pulseaudio-libs` | `sudo pacman -S libpulse` | `sudo zypper install -y libpulse0` |

If any of these are missing, `main.py` will detect the problem at startup and print the exact install command for your distribution before exiting cleanly — no cryptic crashes.

For DDS support also install [ImageMagick](https://imagemagick.org/).

## Running

```bash
python main.py
```

## Building a Standalone Executable

### ⬇️ Download a pre-built Windows release (easiest)

Every push to `main` automatically builds the Windows exe via GitHub Actions.

1. Go to the **[Actions tab](../../actions/workflows/build.yml)**
2. Click the latest successful run (green ✅)
3. Scroll to **Artifacts** at the bottom of the page
4. Download **`AlphaFixerConverter-Windows-v1.0.0`**
5. Extract the zip → run **`AlphaFixerConverter.exe`** — no Python needed!

You can also trigger a build manually: Actions → "Build Windows Executable" → **Run workflow**.

### Build it yourself

You can also package the app locally using [PyInstaller](https://pyinstaller.org/).

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

### Single-file build (slower startup)

```bash
bash scripts/build_exe.sh --onefile      # Linux / macOS
scripts\build_exe.bat --onefile          # Windows
```

### Manual PyInstaller invocation

The repo ships with a fully-configured spec file:

```bash
pyinstaller alpha_fixer.spec
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Architecture

```
src/
  version.py           - App version constant (1.0.0)
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
    settings_dialog.py   - Settings dialog (themes, effects, tooltip mode, unlock display)
    theme_engine.py      - Qt stylesheet generator + 50 theme palettes (18 preset + 32 hidden) + THEME_EFFECTS map
    click_effects.py     - Per-theme click particle overlay (blood, bats, stars, skulls, otters)
    tooltip_manager.py   - Cycling tooltip engine: Normal / Off / Dumbed Down / No Filter
    drop_list.py         - DropFileList: drag-and-drop, Delete key, right-click remove
    mouse_trail.py       - Mouse trail particle overlay
    sound_engine.py      - Click sound engine (QSoundEffect + fallback)
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
