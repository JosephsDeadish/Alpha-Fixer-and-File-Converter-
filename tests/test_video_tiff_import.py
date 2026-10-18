"""Multipage TIFF decoding in Video Builder."""
from pathlib import Path

import pytest
from PIL import Image, TiffImagePlugin

from src.ui.video_tool import _load_image_as_clip


def make_tiff(folder: Path, compression: str) -> Path:
    path = folder / "pages.tiff"
    pages = [
        Image.new("P", (7, 5), 1),
        Image.new("L", (3, 9), 117),
        Image.new("RGBA", (11, 4), (10, 70, 230, 91)),
    ]
    palette = [0] * 768
    palette[3:6] = [200, 30, 10]
    pages[0].putpalette(palette)
    pages[2].putpixel((0, 0), (33, 44, 55, 0))
    try:
        with TiffImagePlugin.AppendingTiffWriter(str(path), True) as writer:
            for page in pages:
                page.save(writer, format="TIFF", compression=compression)
                writer.newFrame()
    finally:
        for page in pages:
            page.close()
    return path


@pytest.mark.parametrize("compression", ["raw", "tiff_lzw"])
def test_video_builder_loads_independent_tiff_pages_as_sequence(tmp_path, compression):
    clip = _load_image_as_clip(str(make_tiff(tmp_path, compression)))
    assert clip is not None
    try:
        assert clip.clip_type == "gif"
        assert clip.total_frames == 3
        assert clip.fps == 25.0
        assert clip.frame_size == (11, 9)
        assert clip.decoded_pixel_count == 106
        expected = [
            ((7, 5), (200, 30, 10, 255)),
            ((3, 9), (117, 117, 117, 255)),
            ((11, 4), (33, 44, 55, 0)),
        ]
        for index, (size, pixel) in enumerate(expected):
            with clip.get_frame(index) as frame:
                assert frame.size == size
                assert frame.getpixel((0, 0)) == pixel
    finally:
        clip.close()


def test_video_builder_rejects_tiff_with_malformed_later_page(tmp_path):
    path = make_tiff(tmp_path, "raw")
    with Image.open(path) as image:
        image.seek(2)
        page_offset = image.tag_v2[273][0]
    with path.open("r+b") as stream:
        stream.truncate(page_offset + 1)

    assert _load_image_as_clip(str(path)) is None
