"""Retained ownership checks for failed image imports and GIF extraction."""
import io
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QDialog

from src.ui.converter_tool import ConverterTab
from src.ui.gif_builder import _load_pillow_rgba
from src.ui.gif_frame_picker import GifFramePickerDialog
from src.ui.selective_alpha_tool import NUM_ZONES, SelectiveAlphaCanvas
from src.ui.preview_pane import _ConvertedThumbLoader, _ConverterPreviewLoader, _pil_to_qimage
from src.ui.video_tool import _load_image_as_clip


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folder():
    root = Path("build/malformed-image-resource-tests")
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


@pytest.fixture
def gif_source(folder):
    path = folder / "frames.gif"
    frames = [Image.new("RGBA", (12, 10), (index * 70, 20, 100, 255))
              for index in range(3)]
    try:
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=[40, 80, 120], optimize=False)
    finally:
        for frame in frames:
            frame.close()
    valid = path.read_bytes()
    with Image.open(path) as image:
        image.seek(2)
        cutoff = image.tile[0][2] + 2
    return path, valid, valid[:cutoff]


@pytest.fixture
def owned_images(monkeypatch):
    images, handles = [], []
    real_open, real_new = Image.open, Image.new

    def opened(*args, **kwargs):
        image = real_open(*args, **kwargs)
        images.append(image)
        if image.fp is not None:
            handles.append(image.fp)
        return image

    def created(*args, **kwargs):
        image = real_new(*args, **kwargs)
        if "src" in Path(sys._getframe(1).f_code.co_filename).parts:
            images.append(image)
        return image

    monkeypatch.setattr(Image, "open", opened)
    monkeypatch.setattr(Image, "new", created)
    for method in ("copy", "convert", "crop"):
        original = getattr(Image.Image, method)

        def derived(self, *args, _original=original, **kwargs):
            image = _original(self, *args, **kwargs)
            # Only retain application-owned results, not Pillow's internal
            # decoder scratch images whose lifetime is Pillow's responsibility.
            if "src" in Path(sys._getframe(1).f_code.co_filename).parts:
                images.append(image)
            return image

        monkeypatch.setattr(Image.Image, method, derived)
    yield images, handles
    for image in images:
        image.close()


def assert_released(resources):
    images, handles = resources
    assert images
    assert all(handle.closed for handle in handles)
    live = []
    for image in images:
        try:
            image.getpixel((0, 0))
        except (ValueError, AssertionError):
            continue
        live.append(image)
    assert not live, f"{len(live)} retained application images still readable: {live}"


@pytest.mark.parametrize("importer", ["picker", "builder", "video"])
def test_late_gif_failure_releases_pixels_and_handles_before_retry(
    app, gif_source, owned_images, importer,
):
    path, valid, truncated = gif_source
    for _ in range(3):
        path.write_bytes(truncated)
        if importer == "picker":
            dialog = GifFramePickerDialog(str(path))
            try:
                assert dialog._frame_count == 0
                assert dialog.selected_indices() == []
                assert "Error reading frames:" in dialog._info_lbl.text()
            finally:
                dialog.close()
                sip.delete(dialog)
        elif importer == "builder":
            with pytest.raises(ValueError, match="page/frame 3"):
                _load_pillow_rgba(str(path))
        else:
            assert _load_image_as_clip(str(path)) is None
        assert_released(owned_images)
        path.write_bytes(valid)
        if importer == "picker":
            dialog = GifFramePickerDialog(str(path))
            try:
                assert dialog.selected_indices() == [0, 1, 2]
            finally:
                dialog.reject()
                sip.delete(dialog)
        elif importer == "builder":
            frames = _load_pillow_rgba(str(path))
            try:
                assert [frame.getpixel((0, 0)) for frame in frames] == [
                    (index * 70, 20, 100, 255) for index in range(3)]
            finally:
                for frame in frames:
                    frame.close()
        else:
            clip = _load_image_as_clip(str(path))
            assert clip is not None
            clip.close()
        assert_released(owned_images)


@pytest.mark.parametrize("failure_at", [0, 2, NUM_ZONES - 1])
def test_painter_mask_allocation_failure_preserves_existing_edit(
    app, folder, owned_images, failure_at,
):
    old_path, new_path = folder / "old.png", folder / "new.tiff"
    with Image.new("RGBA", (4, 3), (10, 20, 30, 255)) as image:
        image.save(old_path)
    with Image.new("RGBA", (9, 7), (40, 50, 60, 128)) as image:
        image.save(new_path, save_all=True, append_images=[image])
    valid = new_path.read_bytes()
    probe = Image.open(new_path)
    try:
        cutoff = probe.tag_v2[273][0] + probe.tag_v2[279][0] // 2
    finally:
        probe.close()
    canvas = SelectiveAlphaCanvas()
    try:
        assert canvas.load_image(str(old_path))
        canvas._masks[0][1, 1] = 255
        canvas._push_history()
        old_image, old_array = canvas._src_img, canvas._src_arr
        old_masks, old_qimage = canvas._masks, canvas._src_qimage
        old_history = list(canvas._undo_stack)
        for _ in range(3):
            first_failed = len(owned_images[0])
            new_path.write_bytes(valid[:cutoff])
            assert not canvas.load_image(str(new_path))
            assert canvas._src_img is old_image
            assert canvas._masks is old_masks
            assert canvas._src_qimage is old_qimage
            assert canvas._undo_stack == old_history
            assert_released((owned_images[0][first_failed:], owned_images[1]))
        new_path.write_bytes(valid)
        first_new = len(owned_images[0])
        real_zeros = np.zeros
        count = 0

        def fail_mask(*args, **kwargs):
            nonlocal count
            index = count
            count += 1
            if index == failure_at:
                raise MemoryError("controlled zone-mask allocation failure")
            return real_zeros(*args, **kwargs)

        with patch("src.ui.selective_alpha_tool.np.zeros", side_effect=fail_mask):
            with pytest.raises(MemoryError, match="zone-mask allocation"):
                canvas.load_image(str(new_path))
        assert canvas._src_img is old_image
        assert old_image.getpixel((0, 0)) == (10, 20, 30, 255)
        assert canvas._src_arr is old_array
        assert canvas._masks is old_masks
        assert canvas._masks[0][1, 1] == 255
        assert canvas._src_qimage is old_qimage
        assert canvas._undo_stack == old_history
        assert_released((owned_images[0][first_new:], owned_images[1]))
        assert canvas.load_image(str(new_path))
        assert canvas._src_img.getpixel((0, 0)) == (40, 50, 60, 128)
        assert len(canvas._masks) == NUM_ZONES
        assert all(mask.shape == (7, 9) and not mask.any() for mask in canvas._masks)
        assert not canvas._undo_stack
    finally:
        canvas.unload_image()
        sip.delete(canvas)
    assert_released(owned_images)


@pytest.mark.parametrize("outcome", ["truncated", "save_failure", "cancel"])
def test_converter_gif_expansion_abandonment_releases_all_staging(
    app, folder, gif_source, owned_images, outcome,
):
    path, valid, truncated = gif_source
    path.write_bytes(truncated if outcome == "truncated" else valid)
    other = folder / "other.gif"
    other.write_bytes(valid)
    queue = [str(path), str(other)]
    original_queue = list(queue)
    directories = []
    real_temporary_directory = tempfile.TemporaryDirectory
    active_outcome = outcome

    def temporary_directory(**kwargs):
        directory = real_temporary_directory(dir=folder, **kwargs)
        directories.append(Path(directory.name))
        return directory

    class Picker:
        DialogCode = QDialog.DialogCode

        def __init__(self, source, **kwargs):
            self.source = source

        def exec(self):
            return (self.DialogCode.Rejected
                    if active_outcome == "cancel" and self.source == str(other)
                    else self.DialogCode.Accepted)

        def selected_indices(self):
            return [0, 1, 2]

    harness = SimpleNamespace(_gif_temp_dir=None)
    harness._cleanup_gif_temp_dir = lambda: ConverterTab._cleanup_gif_temp_dir(harness)
    real_save = Image.Image.save

    def save(image, destination, *args, **kwargs):
        if active_outcome == "save_failure":
            Path(destination).write_bytes(b"partial output")
            raise OSError("controlled PNG encoder failure")
        return real_save(image, destination, *args, **kwargs)

    try:
        with patch("src.ui.converter_tool.GifFramePickerDialog", Picker), \
                patch("src.ui.converter_tool.tempfile.TemporaryDirectory", temporary_directory), \
                patch("src.ui.converter_tool.QMessageBox.warning"), \
                patch.object(Image.Image, "save", save):
            for _ in range(3):
                assert ConverterTab._expand_gif_frames(harness, queue) == (None, {})
                assert queue == original_queue
                assert harness._gif_temp_dir is None
                assert all(not directory.exists() for directory in directories)
                assert_released(owned_images)
                active_outcome = "success"
                path.write_bytes(valid)
                expanded, logical_sources = ConverterTab._expand_gif_frames(harness, queue)
                assert queue == original_queue
                assert len(expanded) == len(logical_sources) == 6
                assert harness._gif_temp_dir is not None
                assert directories[-1].is_dir()
                assert_released(owned_images)
                for index, frame_path in enumerate(expanded):
                    source = Path(queue[index // 3])
                    assert logical_sources[frame_path] == str(
                        source.parent / f"{source.stem}_frame{index % 3 + 1:04d}.png")
                    image = Image.open(frame_path)
                    try:
                        assert image.getpixel((0, 0)) == (index % 3 * 70, 20, 100, 255)
                    finally:
                        image.close()
                harness._cleanup_gif_temp_dir()
                assert all(not directory.exists() for directory in directories)
                assert_released(owned_images)
                active_outcome = outcome
                path.write_bytes(truncated if outcome == "truncated" else valid)
    finally:
        harness._cleanup_gif_temp_dir()


@pytest.mark.parametrize("loader_type", [_ConvertedThumbLoader, _ConverterPreviewLoader])
@pytest.mark.parametrize("failure", ["prepare", "display", "cancel"])
def test_converted_preview_exception_and_cancellation_release_resources(
    app, folder, owned_images, monkeypatch, loader_type, failure,
):
    path = folder / "preview.png"
    with Image.new("RGBA", (8, 6), (20, 40, 60, 128)) as image:
        image.save(path)
    buffers = []
    real_bytes_io, real_convert = io.BytesIO, Image.Image.convert

    def tracked_buffer(*args, **kwargs):
        buffer = real_bytes_io(*args, **kwargs)
        if "src" in Path(sys._getframe(1).f_code.co_filename).parts:
            buffers.append(buffer)
        return buffer

    monkeypatch.setattr(io, "BytesIO", tracked_buffer)
    try:
        for _ in range(3):
            loader = loader_type(str(path), "JPEG", 75)
            results, failures = [], []
            signal = loader.loaded if loader_type is _ConvertedThumbLoader else loader.ready
            signal.connect(lambda *args: results.append(args))
            loader.failed.connect(failures.append)

            def convert(image, mode=None, *args, **kwargs):
                if failure == "prepare" and mode == "RGB":
                    raise MemoryError("controlled preview preparation failure")
                return real_convert(image, mode, *args, **kwargs)

            def display(image):
                if image.mode == "RGB":
                    if failure == "display":
                        raise MemoryError("controlled preview display failure")
                    if failure == "cancel":
                        loader.stop()
                return _pil_to_qimage(image)

            with patch.object(Image.Image, "convert", convert), \
                    patch("src.ui.preview_pane._pil_to_qimage", display):
                loader.run()
            sip.delete(loader)
            assert not results
            assert len(failures) == (0 if failure == "cancel" else 1)
            assert all(buffer.closed for buffer in buffers)
            assert_released(owned_images)

            retry = loader_type(str(path), "JPEG", 75)
            signal = retry.loaded if loader_type is _ConvertedThumbLoader else retry.ready
            signal.connect(lambda *args: results.append(args))
            retry.failed.connect(failures.append)
            retry.run()
            sip.delete(retry)
            assert len(results) == 1
            assert all(buffer.closed for buffer in buffers)
            assert_released(owned_images)
    finally:
        for buffer in buffers:
            buffer.close()
