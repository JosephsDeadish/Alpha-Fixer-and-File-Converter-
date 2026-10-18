"""Independent TIFF pages and atomic malformed-source imports in GIF Builder."""
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from PIL import Image, TiffImagePlugin, features
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QDialog

from src.ui.gif_builder import GifBuilderDialog, _GifStreamPickerDialog, _load_pillow_rgba


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


def make_animated_webp(folder):
    if not features.check("webp"):
        pytest.skip("Pillow WebP support unavailable")
    path = folder / "animation.webp"
    frames = [
        Image.new("RGBA", (8, 6), (255, 0, 0, 255)),
        Image.new("RGBA", (8, 6), (0, 255, 0, 128)),
        Image.new("RGBA", (8, 6), (0, 0, 255, 255)),
    ]
    frames[0].putpixel((0, 0), (10, 20, 30, 0))
    try:
        frames[0].save(
            path,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 140, 230],
            loop=0,
            lossless=True,
            exact=True,
        )
    finally:
        for frame in frames:
            frame.close()
    return path


def corrupt_last_page(path):
    with Image.open(path) as image:
        image.seek(2)
        offset = image.tag_v2[273][0]
    with path.open("r+b") as stream:
        stream.truncate(offset + 1)


def _multi_stream_probe():
    return {
        "video_stream_index": 1,
        "audio_stream_index": 2,
        "video_stream_count": 2,
        "audio_stream_count": 2,
        "video_stream_choices": [
            {"index": 1, "codec_name": "h264", "width": 640, "height": 360},
            {"index": 3, "codec_name": "hevc", "width": 1280, "height": 720},
        ],
        "audio_stream_choices": [
            {"index": 2, "codec_name": "aac", "tags": {"language": "eng"}},
            {"index": 4, "codec_name": "opus", "tags": {"language": "jpn"}},
        ],
    }


def test_gif_stream_picker_exposes_accessible_video_and_audio_choices(app, tmp_path):
    picker = _GifStreamPickerDialog(str(tmp_path / "multi.mkv"), _multi_stream_probe())
    try:
        assert picker._video_combo.count() == 3
        assert picker._audio_combo.count() == 3
        assert picker._video_combo.accessibleName() == "GIF Builder video stream"
        assert picker._audio_combo.accessibleName() == "GIF Builder audio stream"
        picker._video_combo.setCurrentIndex(2)
        picker._audio_combo.setCurrentIndex(2)
        assert picker.selected_video_stream_index == 3
        assert picker.selected_audio_stream_index == 4
        picker._video_combo.setCurrentIndex(0)
        assert picker.selected_video_stream_index is None
    finally:
        picker.close()
        sip.delete(picker)


@pytest.mark.parametrize("accepted", [True, False])
def test_gif_multistream_import_applies_manual_selection_or_skips_cleanly(
        app, builder, tmp_path, accepted):
    source = tmp_path / "multi.mkv"
    source.touch()

    class Picker:
        def __init__(self, *_args):
            self.selected_video_stream_index = 3
            self.selected_audio_stream_index = 4

        def exec(self):
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    with patch("src.ui.gif_builder._probe_media_details", return_value=_multi_stream_probe()), \
            patch("src.ui.gif_builder._GifStreamPickerDialog", Picker), \
            patch("src.ui.gif_builder._load_video_frames") as load_video:
        load_video.return_value = ([Image.new("RGBA", (4, 3), "red")], 25.0)
        builder._import_paths([str(source)])

    if accepted:
        load_video.assert_called_once_with(
            str(source),
            preferred_video_stream_index=3,
            preferred_audio_stream_index=4,
            load_note=[],
        )
        assert len(builder._frames) == 1
        assert "video stream #3" in builder._import_detail_box.toPlainText()
        assert "audio stream #4" in builder._import_detail_box.toPlainText()
    else:
        load_video.assert_not_called()
        assert not builder._frames
        assert "stream selection cancelled" in builder._import_detail_box.toPlainText()


def test_gif_import_uses_actual_stream_picker_and_reports_selected_stream_failure(
        builder, tmp_path, monkeypatch):
    selected = tmp_path / "selected.mkv"
    failed = tmp_path / "failed.mkv"
    selected.touch()
    failed.touch()
    probe = _multi_stream_probe()
    selections = []

    def choose_streams(picker):
        picker._video_combo.setCurrentIndex(2)
        picker._audio_combo.setCurrentIndex(2)
        selections.append((
            picker.selected_video_stream_index,
            picker.selected_audio_stream_index,
        ))
        picker.accept()
        return QDialog.DialogCode.Accepted

    def decode(path, **kwargs):
        if path == str(failed):
            raise ValueError("selected stream did not decode")
        return [Image.new("RGBA", (5, 4), "red")], 25.0

    monkeypatch.setattr(_GifStreamPickerDialog, "exec", choose_streams)
    with patch(
        "src.ui.gif_builder._probe_media_details", return_value=probe
    ), patch(
        "src.ui.gif_builder._load_video_frames", side_effect=decode
    ) as load_video, patch(
        "src.ui.gif_builder._video_load_failure_hint",
        return_value="Selected video stream #3 could not be decoded.",
    ), patch(
        "src.ui.gif_builder._visual_still_fallback_note",
        return_value="No still frame was recoverable.",
    ), patch(
        "src.ui.gif_builder._extract_visual_still_frame", return_value=None
    ):
        builder.add_media_paths([str(selected), str(failed)])

    assert selections == [(3, 4), (3, 4)]
    assert [call.kwargs["preferred_video_stream_index"] for call in load_video.call_args_list] == [3, 3]
    assert [call.kwargs["preferred_audio_stream_index"] for call in load_video.call_args_list] == [4, 4]
    assert len(builder._frames) == 1
    assert "1 failed" in builder._import_status_lbl.text()
    details = builder._import_detail_box.toPlainText()
    assert "selected.mkv" in details
    assert "video stream #3" in details
    assert "failed.mkv: selected stream did not decode" in details
    assert "Selected video stream #3 could not be decoded." in details


def test_gif_selected_stream_still_fallback_keeps_manual_selection(builder, tmp_path):
    source = tmp_path / "multi.mkv"
    source.touch()
    probe = _multi_stream_probe()
    selected_probe = {**probe, "video_stream_index": 3, "audio_stream_index": 4}

    class Picker:
        def __init__(self, *_args):
            self.selected_video_stream_index = 3
            self.selected_audio_stream_index = 4

        def exec(self):
            return QDialog.DialogCode.Accepted

    still = Image.new("RGBA", (4, 3), "green")
    with patch(
        "src.ui.gif_builder._probe_media_details",
        side_effect=[probe, selected_probe],
    ) as probe_media, patch(
        "src.ui.gif_builder._GifStreamPickerDialog", Picker
    ), patch(
        "src.ui.gif_builder._load_video_frames",
        side_effect=ValueError("selected stream decode failed"),
    ) as load_video, patch(
        "src.ui.gif_builder._visual_still_fallback_note",
        return_value="still-frame recovery used",
    ), patch(
        "src.ui.gif_builder._extract_visual_still_frame",
        return_value=still,
    ) as extract_still:
        builder._import_paths([str(source)])

    assert probe_media.call_args.kwargs == {
        "preferred_video_stream_index": 3,
        "preferred_audio_stream_index": 4,
    }
    assert load_video.call_args.kwargs == {
        "preferred_video_stream_index": 3,
        "preferred_audio_stream_index": 4,
        "load_note": [],
    }
    extract_still.assert_called_once_with(str(source), selected_probe)
    assert len(builder._frames) == 1
    assert "still-frame recovery used" in builder._import_detail_box.toPlainText()


def test_selected_video_stream_decoder_uses_video_recovery_path_and_releases_clip(tmp_path):
    from src.ui.video_tool import _load_video_frames

    class Clip:
        total_frames = 2
        fps = 24.0
        load_note = "temporary remux used"
        closed = False

        @staticmethod
        def get_frame(index):
            return Image.new("RGB", (3, 2), "red" if index == 0 else "blue")

        def close(self):
            self.closed = True

    clip = Clip()
    notes = []
    with patch("src.ui.video_tool._load_video_clip", return_value=clip) as load_clip:
        frames, fps = _load_video_frames(
            str(tmp_path / "selected.mkv"),
            preferred_video_stream_index=7,
            preferred_audio_stream_index=9,
            load_note=notes,
        )
    try:
        load_clip.assert_called_once_with(
            str(tmp_path / "selected.mkv"),
            preferred_video_stream_index=7,
            preferred_audio_stream_index=9,
        )
        assert fps == 24.0
        assert len(frames) == 2
        assert notes == ["temporary remux used"]
        assert clip.closed
    finally:
        for frame in frames:
            frame.close()


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


def test_animated_webp_frames_preserve_order_composited_alpha_and_timing(folder):
    path = make_animated_webp(folder)
    frames = _load_pillow_rgba(str(path))
    try:
        assert [frame.size for frame in frames] == [(8, 6)] * 3
        assert [frame.mode for frame in frames] == ["RGBA"] * 3
        assert [frame.info["duration"] for frame in frames] == [80, 140, 230]
        assert [frame.getpixel((1, 1)) for frame in frames] == [
            (255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 255),
        ]
        assert frames[0].getpixel((0, 0)) == (0, 0, 0, 0)
        path.unlink()
        assert frames[2].getpixel((1, 1)) == (0, 0, 255, 255)
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


def test_animated_webp_enters_editor_with_source_timing_and_undo_redo(builder, folder):
    path = make_animated_webp(folder)
    still_path = folder / "still.webp"
    with Image.new("RGBA", (3, 2), (40, 50, 60, 255)) as still:
        still.save(still_path, format="WEBP", lossless=True)
    builder.add_media_paths([str(path), str(still_path)])
    entries = tuple(builder._frames)
    assert len(entries) == builder._frame_list.count() == 4
    assert [entry.frame_index for entry in entries] == [0, 1, 2, 0]
    assert [entry.delay_ms for entry in entries] == [80, 140, 230, None]
    assert [entry.source_delay_ms for entry in entries] == [80, 140, 230, None]
    assert [entry.source_kind for entry in entries] == ["animated WebP"] * 3 + ["image"]
    assert [entry._pil.getpixel((1, 1)) for entry in entries] == [
        (255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 255), (40, 50, 60, 255),
    ]
    builder._undo()
    assert builder._frames == []
    builder._redo()
    assert tuple(builder._frames) == entries


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
