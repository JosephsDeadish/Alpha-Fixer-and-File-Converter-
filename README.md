# 🐼 Alpha Fixer & File Converter

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
- 🐼 Panda Dark theme (default), Panda Light, Neon Panda
- Fully customizable color palette via Settings → Theme
- Settings are persisted across sessions

## Requirements

- Python 3.10+
- PyQt6 ≥ 6.4.0
- Pillow ≥ 10.0.0
- numpy ≥ 1.24.0
- imageio ≥ 2.33.0
- wand ≥ 0.6.13 (for DDS via ImageMagick — optional but recommended)

Install dependencies:
```bash
pip install -r requirements.txt
```

For DDS support also install [ImageMagick](https://imagemagick.org/).

## Running

```bash
python main.py
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Architecture

```
src/
  core/
    alpha_processor.py   – Alpha channel processing logic
    file_converter.py    – Image format conversion
    presets.py           – Built-in and custom preset definitions
    worker.py            – Background QThread workers (non-blocking)
    settings_manager.py  – Persistent settings (QSettings)
  ui/
    main_window.py       – Main application window
    alpha_tool.py        – Alpha Fixer tab
    converter_tool.py    – File Converter tab
    settings_dialog.py   – Settings & theme customization dialog
    theme_engine.py      – Qt stylesheet generator
tests/
  test_core.py           – Unit tests for alpha processing & presets
  test_converter.py      – Unit tests for file conversion
main.py                  – Entry point with crash prevention & logging
```
