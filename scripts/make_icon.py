#!/usr/bin/env python3
"""
make_icon.py – Generate src/assets/icon.ico from panda_dark.svg.

Run this script once before building the application with PyInstaller so that
the EXE gets a proper multi-resolution icon embedded in it:

    python scripts/make_icon.py

Requirements:  pip install cairosvg Pillow

The output ICO contains PNG-compressed frames at 8 sizes (16 … 256 px) in
32-bit RGBA so the icon looks crisp at all Windows shell / taskbar zoom levels.
"""

import io
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT   = os.path.dirname(SCRIPT_DIR)
SVG_PATH    = os.path.join(REPO_ROOT, "src", "assets", "svg", "panda_dark.svg")
OUT_PATH    = os.path.join(REPO_ROOT, "src", "assets", "icon.ico")
SIZES       = [16, 24, 32, 40, 48, 64, 128, 256]


def _render_pngs(svg_path: str, sizes: list) -> list:
    """Render *svg_path* at each size and return a list of raw PNG bytes."""
    try:
        import cairosvg
        from PIL import Image
    except ImportError:
        sys.exit(
            "ERROR: cairosvg and Pillow are required.\n"
            "       Install them with:  pip install cairosvg Pillow"
        )

    pngs = []
    for size in sizes:
        try:
            png_data = cairosvg.svg2png(url=svg_path, output_width=size, output_height=size)
            img = Image.open(io.BytesIO(png_data)).convert("RGBA")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            pngs.append(buf.getvalue())
            print(f"  rendered {size}x{size}  ({len(pngs[-1])} bytes)")
        except Exception as exc:
            sys.exit(f"ERROR: failed to render {size}x{size} from {svg_path}: {exc}")
    return pngs


def _write_ico(pngs: list, sizes: list, out_path: str) -> None:
    """Write a standard ICO file containing PNG-compressed frames."""
    count = len(pngs)
    # ICO header: RESERVED(2) TYPE(2=ICO) COUNT(2)
    header = struct.pack("<HHH", 0, 1, count)

    dir_size   = count * 16
    data_start = 6 + dir_size

    entries    = b""
    image_data = b""
    cur_offset = data_start

    for size, png in zip(sizes, pngs):
        # Width/height: 0 means 256 in the ICO spec
        w = size if size < 256 else 0
        h = size if size < 256 else 0
        entries    += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), cur_offset)
        image_data += png
        cur_offset += len(png)

    with open(out_path, "wb") as f:
        f.write(header)
        f.write(entries)
        f.write(image_data)


def main():
    if not os.path.isfile(SVG_PATH):
        sys.exit(f"ERROR: SVG not found: {SVG_PATH}")

    print(f"Source SVG : {SVG_PATH}")
    print(f"Output ICO : {OUT_PATH}")
    print()
    pngs = _render_pngs(SVG_PATH, SIZES)
    _write_ico(pngs, SIZES, OUT_PATH)
    print(f"\nDone – {os.path.getsize(OUT_PATH):,} bytes written to {OUT_PATH}")


if __name__ == "__main__":
    main()
