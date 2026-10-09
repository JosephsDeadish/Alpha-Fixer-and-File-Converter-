from pathlib import Path
import tempfile
from contextlib import ExitStack
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QMessageBox

from src.core.settings_manager import SettingsManager
from src.ui._ui_utils import confirm_normalized_save_path, staged_output_path
from src.ui.gif_builder import GifBuilderDialog
from src.ui.selective_alpha_tool import SelectiveAlphaTool
from src.ui.video_tool import VideoToolDialog, _ClipEntry, _CLIP_ROLE


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def painter(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        widget = SelectiveAlphaTool(settings)
        source = tmp_path / "source.png"
        with Image.new("RGBA", (8, 8), (10, 20, 30, 255)) as image:
            image.save(source)
        assert widget._canvas.load_image(str(source))
        widget._src_path = str(source)
        widget._canvas.set_mask_from_array(0, np.ones((8, 8), dtype=np.uint8))
        widget._offer_delete_original = Mock()
        yield widget
        widget.close()
        sip.delete(widget)
        settings.sync()
        sip.delete(settings._qs)


def test_staged_save_failure_keeps_destination_and_removes_partial(tmp_path):
    destination = tmp_path / "output.png"
    destination.write_bytes(b"existing output")
    with pytest.raises(OSError, match="encoding failed"):
        with staged_output_path(destination) as staged:
            assert Path(staged).parent == tmp_path
            Path(staged).write_bytes(b"partial")
            raise OSError("encoding failed")
    assert destination.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


def test_staged_commit_failure_keeps_original_and_removes_partial(tmp_path):
    destination = tmp_path / "output.gif"
    destination.write_bytes(b"existing output")
    with patch("os.replace", side_effect=PermissionError("destination locked")):
        with pytest.raises(PermissionError):
            with staged_output_path(destination) as staged:
                Path(staged).write_bytes(b"complete")
    assert destination.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


def test_staged_success_replaces_destination(tmp_path):
    destination = tmp_path / "output.png"
    destination.write_bytes(b"old")
    with staged_output_path(destination) as staged:
        Path(staged).write_bytes(b"new")
        assert destination.read_bytes() == b"old"
    assert destination.read_bytes() == b"new"
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("kind", ["gif", "painter"])
def test_save_failure_never_deletes_existing_output_or_records_success(app, painter, tmp_path, kind):
    target = tmp_path / ("output.gif" if kind == "gif" else "output.png")
    target.write_bytes(b"original output")
    if kind == "gif":
        widget = GifBuilderDialog()
        widget._add_paths([painter._src_path])
        success = Mock()
        widget.exported.connect(success)
        record = patch.object(widget, "_record_export_history")
        dialog_path = "src.ui.gif_builder"
        save = widget._export
    else:
        widget = painter
        success = widget._offer_delete_original
        record = patch.object(widget._settings, "add_selective_alpha_history")
        dialog_path = "src.ui.selective_alpha_tool"
        save = widget._on_save

    def fail_encoding(image, filename, *args, **kwargs):
        Path(filename).write_bytes(b"partial")
        raise OSError("encoder failed")

    try:
        with record as history:
            with patch(f"{dialog_path}.QFileDialog.getSaveFileName",
                       return_value=(str(target), "")):
                with patch.object(Image.Image, "save", fail_encoding):
                    with patch(f"{dialog_path}.QMessageBox.critical") as error:
                        save()
            error.assert_called_once()
            history.assert_not_called()
        success.assert_not_called()
        assert target.read_bytes() == b"original output"
        assert not list(tmp_path.glob(".alpha_fixer_save_*"))
    finally:
        if kind == "gif":
            widget._clear_all()
            widget.close()
            sip.delete(widget)


@pytest.mark.parametrize("selected_filter,extension", [
    ("PNG (*.png)", ".png"), ("WebP (*.webp)", ".webp"),
    ("TIFF (*.tiff *.tif)", ".tiff"), ("TGA (*.tga)", ".tga"),
    ("All Files (*)", ".png"),
])
def test_painter_extensionless_save_uses_selected_format(painter, tmp_path, selected_filter, extension):
    output = tmp_path / "painted"
    with patch("src.ui.selective_alpha_tool.QFileDialog.getSaveFileName",
               return_value=(str(output), selected_filter)):
        painter._on_save()
    path = output.with_suffix(extension)
    assert path.is_file()
    with Image.open(path) as image:
        assert image.size == (8, 8)
    history = painter._settings.get_selective_alpha_history()
    assert history[-1]["output"] == str(path)
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("count", [1, 2])
def test_gif_success_replaces_existing_output_and_emits_final_path(app, painter, tmp_path, count):
    output = tmp_path / "animation.gif"
    output.write_bytes(b"previous output")
    second = tmp_path / "second.png"
    with Image.new("RGBA", (8, 8), (200, 30, 40, 255)) as image:
        image.save(second)
    widget = GifBuilderDialog()
    widget._add_paths([painter._src_path] + ([str(second)] if count == 2 else []))
    exported = Mock()
    widget.exported.connect(exported)
    try:
        with patch("src.ui.gif_builder.QFileDialog.getSaveFileName",
                   return_value=(str(output), "GIF Files (*.gif)")):
            with patch("src.ui.gif_builder.QMessageBox.information"):
                widget._export()
        exported.assert_called_once_with(str(output))
        with Image.open(output) as image:
            assert image.n_frames == count
        assert not list(tmp_path.glob(".alpha_fixer_save_*"))
    finally:
        widget._clear_all()
        widget.close()
        sip.delete(widget)


@pytest.mark.parametrize("fmt,mux", [("gif", False), ("mp4", False), ("mp4", True)])
def test_video_final_staging_uses_destination_filesystem(app, tmp_path, fmt, mux):
    widget = VideoToolDialog()
    clip = _ClipEntry("/tmp/source.png", 1,
                      lambda _: Image.new("RGBA", (8, 8), (10, 20, 30, 255)),
                      clip_type="image", frame_size=(8, 8))
    clip.still_duration_frames = 1
    widget._clips.append(clip)
    item = QListWidgetItem("source")
    item.setData(_CLIP_ROLE, clip)
    widget._clip_list.addItem(item)
    widget._clip_list.setCurrentRow(0)
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData(fmt))
    output = tmp_path / f"video.{fmt}"
    output.write_bytes(b"previous output")
    real_tempfile = tempfile.NamedTemporaryFile
    created = []

    def capture_tempfile(**kwargs):
        handle = real_tempfile(**kwargs)
        created.append(Path(handle.name))
        return handle

    def get_writer(path, **kwargs):
        writer = Mock()
        writer.close.side_effect = lambda: Path(path).write_bytes(b"encoded MP4")
        return writer

    try:
        widget._mp4_export_available = True
        with ExitStack() as stack:
            stack.enter_context(patch("src.ui.video_tool.QFileDialog.getSaveFileName",
                                      return_value=(str(output), "")))
            stack.enter_context(patch("src.ui.video_tool.tempfile.NamedTemporaryFile", capture_tempfile))
            stack.enter_context(patch("src.ui.video_tool.QMessageBox.information"))
            stack.enter_context(patch("src.ui.video_tool.QMessageBox.warning"))
            stack.enter_context(patch.object(widget, "_should_mux_audio", return_value=mux))
            stack.enter_context(patch.object(widget, "_mux_mp4_audio", side_effect=OSError("mux failure")))
            if fmt == "mp4":
                stack.enter_context(patch("imageio.get_writer", get_writer))
            widget._export()
        assert created
        assert all(path.parent == tmp_path for path in created)
        assert all(not path.exists() for path in created)
        if fmt == "gif":
            with Image.open(output) as image:
                assert image.format == "GIF"
        else:
            assert output.read_bytes() == b"encoded MP4"
    finally:
        widget.close()
        sip.delete(widget)


def test_painter_original_deletion_defaults_to_keep(painter):
    source = Path(painter._src_path)
    observed = []

    def inspect(dialog):
        observed.append(dialog.defaultButton().text())
        assert dialog.escapeButton() is dialog.defaultButton()
        return 0

    with patch.object(QMessageBox, "exec", inspect):
        SelectiveAlphaTool._offer_delete_original(painter, str(source))
    assert observed == ["Keep Original"]
    assert source.exists()


@pytest.mark.parametrize("typed_suffix", ["", ".jpg"])
@pytest.mark.parametrize("accept", [False, True])
def test_painter_normalized_destination_requires_overwrite_confirmation(painter, tmp_path, typed_suffix, accept):
    output = tmp_path / "existing.png"
    output.write_bytes(b"original")
    chosen = tmp_path / ("existing" + typed_suffix)
    reply = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    painter._settings.add_selective_alpha_history = Mock()
    with patch("src.ui.selective_alpha_tool.QFileDialog.getSaveFileName",
               return_value=(str(chosen), "All Files (*)")), \
            patch.object(QMessageBox, "warning"), \
            patch.object(QMessageBox, "question", return_value=reply) as question:
        painter._on_save()
    assert question.call_args.args[-1] == QMessageBox.StandardButton.No
    if accept:
        with Image.open(output) as image:
            assert image.format == "PNG"
        painter._settings.add_selective_alpha_history.assert_called_once()
    else:
        assert output.read_bytes() == b"original"
        painter._settings.add_selective_alpha_history.assert_not_called()
        painter._offer_delete_original.assert_not_called()
    assert not list(tmp_path.glob(".existing.*"))


@pytest.mark.parametrize("kind", ["gif_builder", "video_gif", "video_mp4"])
@pytest.mark.parametrize("typed_suffix", ["", ".png"])
@pytest.mark.parametrize("accept", [False, True])
def test_builder_normalized_destination_confirms_before_export(app, painter, tmp_path, kind, typed_suffix, accept):
    suffix = ".mp4" if kind == "video_mp4" else ".gif"
    chosen = tmp_path / ("existing" + typed_suffix)
    # GIF Builder appends; Video Builder replaces known media suffixes.
    final = Path(str(chosen) + suffix) if kind == "gif_builder" else chosen.with_suffix(suffix)
    final.write_bytes(b"original output")
    if kind == "gif_builder":
        widget = GifBuilderDialog()
        widget._add_paths([painter._src_path])
        module = "src.ui.gif_builder"
    else:
        widget = VideoToolDialog()
        clip = _ClipEntry(painter._src_path, 1,
                          lambda _: Image.new("RGBA", (8, 8), (10, 20, 30, 255)),
                          clip_type="image", frame_size=(8, 8))
        clip.still_duration_frames = 1
        widget._clips.append(clip)
        item = QListWidgetItem("source")
        item.setData(_CLIP_ROLE, clip)
        widget._clip_list.addItem(item)
        widget._clip_list.setCurrentRow(0)
        widget._export_fmt_combo.setCurrentIndex(
            widget._export_fmt_combo.findData("mp4" if suffix == ".mp4" else "gif"))
        widget._mp4_export_available = True
        module = "src.ui.video_tool"

    def get_writer(path, **kwargs):
        writer = Mock()
        writer.close.side_effect = lambda: Path(path).write_bytes(b"encoded MP4")
        return writer

    reply = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    try:
        with ExitStack() as stack:
            stack.enter_context(patch(f"{module}.QFileDialog.getSaveFileName",
                                      return_value=(str(chosen), "All Files (*)")))
            question = stack.enter_context(patch.object(QMessageBox, "question", return_value=reply))
            info = stack.enter_context(patch.object(QMessageBox, "information"))
            error = stack.enter_context(patch.object(QMessageBox, "critical"))
            history = stack.enter_context(patch.object(widget, "_record_export_history"))
            temps = stack.enter_context(patch("tempfile.NamedTemporaryFile",
                                              wraps=tempfile.NamedTemporaryFile))
            if kind != "gif_builder":
                stack.enter_context(patch.object(widget, "_should_mux_audio", return_value=False))
            if suffix == ".mp4":
                stack.enter_context(patch("imageio.get_writer", get_writer))
            widget._export()
        question.assert_called_once()
        assert str(final) in question.call_args.args[2]
        assert question.call_args.args[-1] == QMessageBox.StandardButton.No
        error.assert_not_called()
        if accept:
            assert final.read_bytes() != b"original output"
            history.assert_called_once()
            info.assert_called_once()
            if suffix == ".gif":
                with Image.open(final) as image:
                    assert image.format == "GIF"
        else:
            assert final.read_bytes() == b"original output"
            history.assert_not_called()
            temps.assert_not_called()
            info.assert_not_called()
        assert not chosen.exists()
        assert not list(tmp_path.glob(".alpha_fixer_save_*"))
        assert not list(tmp_path.glob("alpha_fixer_export_*"))
    finally:
        if kind == "gif_builder":
            widget._clear_all()
        widget.close()
        sip.delete(widget)


@pytest.mark.parametrize("changed,exists", [(False, True), (False, False), (True, False)])
def test_normalized_save_skips_unnecessary_prompt(app, tmp_path, changed, exists):
    final = tmp_path / "output.gif"
    if exists:
        final.write_bytes(b"original")
    chosen = str(tmp_path / "output") if changed else str(final)
    with patch.object(QMessageBox, "question") as question:
        assert confirm_normalized_save_path(None, chosen, str(final))
    question.assert_not_called()
