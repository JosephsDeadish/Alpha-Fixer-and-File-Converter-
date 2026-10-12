"""Independent TIFF pages and atomic malformed-source imports in GIF Builder."""
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from PIL import Image, TiffImagePlugin
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication

from src.ui.gif_builder import GifBuilderDialog, _load_pillow_rgba


@pytest.fixture
def folder():
    path = Path("build/gif-tiff-import-tests")
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def builder(app, folder):
    with patch("src.ui.editor_project.user_data_directory", return_value=folder / "data"):
        widget = GifBuilderDialog()
    widget._project.timer.stop()
    try:
        yield widget
    finally:
        widget._project._closing_approved = True
        widget._project._release_session()
        widget.close()
        sip.delete(widget)
        app.processEvents()


def make_tiff(folder, compression="raw"):
    path = folder / "pages.tiff"
    pages = [
        Image.new("RGB", (7, 5), (220, 30, 10)),
        Image.new("RGBA", (3, 9), (10, 70, 230, 91)),
        Image.new("L", (11, 4), 117),
    ]
    pages[1].putpixel((0, 0), (33, 44, 55, 0))
    try:
        pages[0].save(path, save_all=True, append_images=pages[1:],
                      compression=compression)
    finally:
        for page in pages:
            page.close()
    return path


def corrupt_last_page(path):
    with Image.open(path) as image:
        image.seek(2)
        offset = image.tag_v2[273][0]
    with path.open("r+b") as stream:
        stream.truncate(offset + 1)


def make_palette_tiff(folder, compression):
    path = folder / "palette-pages.tiff"
    specs = [
        ("P", (7, 5), 1),
        ("L", (3, 9), 117),
        ("1", (11, 4), 255),
        ("P", (4, 6), 2),
        ("LA", (5, 3), (80, 91)),
    ]
    with TiffImagePlugin.AppendingTiffWriter(str(path), True) as writer:
        for mode, size, color in specs:
            with Image.new(mode, size, color) as image:
                if mode == "P":
                    image.putpalette([component for value in range(256)
                                      for component in (value, 255 - value, (value * 3) % 256)])
                image.save(writer, format="TIFF", compression=compression)
            writer.newFrame()
    return path


@pytest.mark.parametrize("compression", ["raw", "tiff_lzw"])
def test_palette_does_not_colorize_following_grayscale_pages(folder, compression):
    path = make_palette_tiff(folder, compression)
    frames = _load_pillow_rgba(str(path))
    try:
        assert [frame.size for frame in frames] == [(7, 5), (3, 9), (11, 4), (4, 6), (5, 3)]
        colors = [(1, 254, 3, 255), (117, 117, 117, 255), (255, 255, 255, 255),
                  (2, 253, 6, 255), (80, 80, 80, 91)]
        path.unlink()
        for frame, color in zip(frames, colors):
            assert frame.mode == "RGBA"
            assert frame.tobytes() == bytes(color) * (frame.width * frame.height)
    finally:
        for frame in frames:
            frame.close()


@pytest.mark.parametrize("compression", ["raw", "tiff_lzw"])
def test_independent_pages_preserve_dimensions_colors_alpha_and_order(folder, compression):
    path = make_tiff(folder, compression)
    frames = _load_pillow_rgba(str(path))
    try:
        assert [frame.size for frame in frames] == [(7, 5), (3, 9), (11, 4)]
        assert [frame.mode for frame in frames] == ["RGBA"] * 3
        assert [frame.getpixel((1, 1)) for frame in frames] == [
            (220, 30, 10, 255), (10, 70, 230, 91), (117, 117, 117, 255),
        ]
        assert frames[1].getpixel((0, 0)) == (33, 44, 55, 0)
        # No decoded frame retains a dependency on the source file.
        path.unlink()
        assert frames[2].getpixel((10, 3)) == (117, 117, 117, 255)
    finally:
        for frame in frames:
            frame.close()


def test_tiff_pages_enter_editor_as_independently_editable_frames(builder, folder):
    path = make_tiff(folder)
    builder.add_media_paths([str(path)])
    entries = tuple(builder._frames)
    assert len(entries) == builder._frame_list.count() == 3
    assert [entry._pil.size for entry in entries] == [(7, 5), (3, 9), (11, 4)]
    assert [entry.source_delay_ms for entry in entries] == [None] * 3
    assert "3 frame sizes" in builder._import_status_lbl.text()
    assert "Largest imported frame: 11×4" in builder._import_detail_box.toPlainText()
    assert [builder._frame_list.item(i).text() for i in range(3)] == [
        "pages\n[1]", "pages\n[2]", "pages\n[3]",
    ]
    builder._undo()
    assert builder._frames == []
    builder._redo()
    assert tuple(builder._frames) == entries
    assert entries[1]._pil.getpixel((1, 1)) == (10, 70, 230, 91)


def test_truncated_later_page_closes_all_decoded_pages_and_source(folder):
    path = make_tiff(folder)
    corrupt_last_page(path)
    converted = []
    sources = []
    original_convert = Image.Image.convert
    original_open = Image.open

    def convert(image, *args, **kwargs):
        result = original_convert(image, *args, **kwargs)
        converted.append(result)
        return result

    def open_image(*args, **kwargs):
        result = original_open(*args, **kwargs)
        sources.append(result)
        return result

    with patch.object(Image.Image, "convert", convert), patch.object(Image, "open", open_image):
        with pytest.raises(ValueError, match="page/frame 3"):
            _load_pillow_rgba(str(path))
    assert len(converted) == 2
    assert sources[0].fp is None
    for frame in converted:
        with pytest.raises(ValueError, match="closed"):
            frame.getpixel((0, 0))


def test_failed_source_is_atomic_and_next_source_recovers_inline(builder, folder):
    healthy = folder / "healthy.png"
    with Image.new("RGBA", (4, 6), (9, 8, 7, 123)) as image:
        image.save(healthy)
    builder.add_media_paths([str(healthy)])
    original = builder._frames[0]
    corrupt = make_tiff(folder)
    corrupt_last_page(corrupt)
    builder.add_media_paths([str(corrupt), str(healthy)])
    assert len(builder._frames) == 2
    assert builder._frames[0] is original
    assert builder._frames[1]._pil.getpixel((0, 0)) == (9, 8, 7, 123)
    assert "page/frame 3" in builder._import_detail_box.toPlainText()
    builder._undo()
    assert builder._frames == [original]


def test_cancellation_after_decode_discards_unpublished_pages(builder, folder):
    frames = _load_pillow_rgba(str(make_tiff(folder)))
    with patch("src.ui.gif_builder._load_pillow_rgba", return_value=frames), \
            patch("src.ui.gif_builder.QProgressDialog.wasCanceled", side_effect=[False, True]):
        builder.add_media_paths([str(folder / "pages.tiff")])
    assert builder._frames == []
    assert builder._frame_list.count() == 0
    assert not builder._undo_stack
    for frame in frames:
        with pytest.raises(ValueError, match="closed"):
            frame.getpixel((0, 0))


@pytest.mark.parametrize("disposal", [1, 2, 3])
def test_gif_decoder_keeps_delta_compositing_disposal_and_duration(folder, disposal):
    path = folder / "delta.gif"
    pages = [Image.new("RGBA", (8, 6), (0, 0, 0, 0)) for _ in range(3)]
    pages[0].paste((255, 0, 0, 255), (0, 0, 2, 2))
    pages[1].paste((0, 255, 0, 255), (3, 0, 5, 2))
    pages[2].paste((0, 0, 255, 255), (6, 0, 8, 2))
    try:
        pages[0].save(path, save_all=True, append_images=pages[1:],
                      duration=[80, 140, 230], disposal=disposal, optimize=True)
    finally:
        for page in pages:
            page.close()
    frames = _load_pillow_rgba(str(path))
    try:
        assert [frame.info["duration"] for frame in frames] == [80, 140, 230]
        assert frames[0].getpixel((0, 0)) == (255, 0, 0, 255)
        assert frames[1].getpixel((3, 0)) == (0, 255, 0, 255)
        assert frames[2].getpixel((6, 0)) == (0, 0, 255, 255)
        assert frames[2].getpixel((0, 0)) == (
            (255, 0, 0, 255) if disposal == 1 else (0, 0, 0, 0)
        )
        assert frames[2].getpixel((3, 0)) == (
            (0, 255, 0, 255) if disposal == 1 else (0, 0, 0, 0)
        )
    finally:
        for frame in frames:
            frame.close()
