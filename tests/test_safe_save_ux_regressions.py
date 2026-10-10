from pathlib import Path
import errno
import os
import tempfile
from contextlib import ExitStack
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QMessageBox, QProgressDialog

from src.core.settings_manager import SettingsManager
from src.ui._ui_utils import confirm_normalized_save_path, staged_output_path
from src.ui.gif_builder import GifBuilderDialog
from src.ui.selective_alpha_tool import SelectiveAlphaTool
from src.ui.video_tool import VideoToolDialog, _ClipEntry, _CLIP_ROLE
from tests.gif_export_helpers import wait_for_gif_export
from tests.video_export_helpers import wait_for_video_export


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


def test_stage_close_failure_is_cleaned_before_retry(tmp_path):
    target = tmp_path / "output.png"
    target.write_bytes(b"original")
    real_temporary_file = tempfile.NamedTemporaryFile
    failure = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def fail_close(**kwargs):
        handle = real_temporary_file(**kwargs)
        close = handle.close

        def close_then_fail():
            close()
            raise failure

        handle.close = close_then_fail
        return handle

    with patch("tempfile.NamedTemporaryFile", fail_close):
        with pytest.raises(OSError) as caught:
            with staged_output_path(target):
                pytest.fail("must not encode after close failed")
    assert caught.value is failure
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))
    with staged_output_path(target) as staged:
        Path(staged).write_bytes(b"retry")
    assert target.read_bytes() == b"retry"


def test_cleanup_failure_does_not_hide_encoding_errno(tmp_path):
    target = tmp_path / "output.png"
    target.write_bytes(b"original")
    failure = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
    try:
        with patch.object(Path, "unlink", side_effect=OSError(errno.EIO, os.strerror(errno.EIO))):
            with pytest.raises(OSError) as caught:
                with staged_output_path(target) as staged:
                    Path(staged).write_bytes(b"partial")
                    raise failure
        assert caught.value is failure
        assert target.read_bytes() == b"original"
    finally:
        # A disconnected filesystem cannot guarantee cleanup; remove our injected orphan.
        for path in tmp_path.glob(".alpha_fixer_save_*"):
            path.unlink()
    with staged_output_path(target) as staged:
        Path(staged).write_bytes(b"retry")
    assert target.read_bytes() == b"retry"


def test_successful_publish_does_not_attempt_cleanup_on_missing_stage(tmp_path):
    target = tmp_path / "output.png"
    with patch.object(Path, "unlink", side_effect=OSError(errno.EIO, os.strerror(errno.EIO))):
        with staged_output_path(target) as staged:
            Path(staged).write_bytes(b"complete")
    assert target.read_bytes() == b"complete"
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


@pytest.mark.skipif(os.name != "posix", reason="Existing output mode preservation is POSIX-only")
def test_stage_mode_failure_preserves_output_and_allows_retry(tmp_path):
    target = tmp_path / "output.png"
    target.write_bytes(b"original")
    target.chmod(0o640)
    with patch.object(Path, "chmod", side_effect=OSError(errno.EACCES, os.strerror(errno.EACCES))):
        with pytest.raises(OSError) as caught:
            with staged_output_path(target, preserve_existing_mode=True) as staged:
                Path(staged).write_bytes(b"complete")
    assert caught.value.errno == errno.EACCES
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))
    with staged_output_path(target, preserve_existing_mode=True) as staged:
        Path(staged).write_bytes(b"retry")
    assert target.read_bytes() == b"retry"
    assert target.stat().st_mode & 0o777 == 0o640


def test_destination_directory_removed_during_staged_save_can_be_recreated(tmp_path):
    directory = tmp_path / "destination"
    directory.mkdir()
    target = directory / "output.png"
    with pytest.raises(OSError) as caught:
        with staged_output_path(target) as staged:
            Path(staged).write_bytes(b"complete")
            # Model an external process removing this small disposable destination.
            Path(staged).unlink()
            directory.rmdir()
    assert caught.value.errno == errno.ENOENT
    assert not directory.exists()
    directory.mkdir()
    with staged_output_path(target) as staged:
        Path(staged).write_bytes(b"retry")
    assert target.read_bytes() == b"retry"
    assert not list(directory.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("kind", ["gif", "painter"])
@pytest.mark.parametrize("phase,code", [
    ("create", errno.ENOSPC), ("create", errno.EACCES),
    ("encode", errno.ENOSPC), ("encode", errno.EIO),
    ("replace", errno.EACCES), ("replace", errno.EIO),
    ("missing_parent", errno.ENOENT), ("disappear", errno.ENOENT),
    ("encode_cleanup", errno.ENOSPC),
])
def test_ui_storage_failure_preserves_state_and_retries(painter, tmp_path, kind, phase, code):
    directory = tmp_path / "output"
    if phase != "missing_parent":
        directory.mkdir()
    target = directory / ("result.gif" if kind == "gif" else "result.png")
    if directory.exists() and phase != "disappear":
        target.write_bytes(b"previous output")
    if kind == "gif":
        widget = GifBuilderDialog()
        widget._add_paths([painter._src_path])
        success = Mock()
        widget.exported.connect(success)
        history_patch = patch.object(widget, "_record_export_history")
        module = "src.ui.gif_builder"
        save = widget._export
    else:
        widget = painter
        success = painter._offer_delete_original
        history_patch = patch.object(painter._settings, "add_selective_alpha_history")
        module = "src.ui.selective_alpha_tool"
        save = widget._on_save
    failure = OSError(code, os.strerror(code))
    masks = painter._canvas.get_masks_as_bool()
    source = Path(painter._src_path)
    original_source = source.read_bytes()
    real_save = Image.Image.save

    def fail_encode(_image, path, *_args, **_kwargs):
        Path(path).write_bytes(b"partial")
        raise failure

    def remove_destination(image, path, *args, **kwargs):
        real_save(image, path, *args, **kwargs)
        Path(path).unlink()
        directory.rmdir()

    def run_save():
        save()
        if kind == "gif":
            wait_for_gif_export(widget)
            assert widget._export_worker is None
            assert widget._export_progress is None
            assert widget._export_content.isEnabled()
            assert len(widget._frames) == 1

    try:
        with history_patch as history, \
                patch(f"{module}.QFileDialog.getSaveFileName", return_value=(str(target), "")), \
                patch(f"{module}.QMessageBox.critical") as error, \
                patch(f"{module}.QMessageBox.information"):
            with ExitStack() as stack:
                if phase == "create":
                    stack.enter_context(patch("tempfile.NamedTemporaryFile", side_effect=failure))
                elif phase.startswith("encode"):
                    stack.enter_context(patch.object(Image.Image, "save", fail_encode))
                    if phase == "encode_cleanup":
                        stack.enter_context(patch.object(
                            Path, "unlink", side_effect=OSError(errno.EIO, os.strerror(errno.EIO))))
                elif phase == "replace":
                    stack.enter_context(patch("os.replace", side_effect=failure))
                elif phase == "disappear":
                    stack.enter_context(patch.object(Image.Image, "save", remove_destination))
                run_save()
            error.assert_called_once()
            assert f"[Errno {code}]" in error.call_args.args[2]
            history.assert_not_called()
            success.assert_not_called()
            assert source.read_bytes() == original_source
            assert all(np.array_equal(before, after) for before, after in
                       zip(masks, painter._canvas.get_masks_as_bool()))
            if phase in ("missing_parent", "disappear"):
                assert not target.exists()
                directory.mkdir()
            else:
                assert target.read_bytes() == b"previous output"
            if phase == "encode_cleanup":
                assert len(list(directory.glob(".alpha_fixer_save_*"))) == 1
                for path in directory.glob(".alpha_fixer_save_*"):
                    path.unlink()
            assert not list(directory.glob(".alpha_fixer_save_*"))
            run_save()
            history.assert_called_once()
            success.assert_called_once()
            error.assert_called_once()
        with Image.open(target) as image:
            assert image.size == (8, 8)
        assert source.read_bytes() == original_source
        assert not list(directory.glob(".alpha_fixer_save_*"))
    finally:
        for path in directory.glob(".alpha_fixer_save_*"):
            path.unlink()
        if kind == "gif":
            widget._clear_all()
            widget.close()
            sip.delete(widget)


@pytest.mark.parametrize("kind", ["gif", "painter"])
def test_cached_ui_image_can_save_after_source_disappears(painter, tmp_path, kind):
    source = Path(painter._src_path)
    target = tmp_path / ("cached.gif" if kind == "gif" else "cached.png")
    widget = GifBuilderDialog() if kind == "gif" else painter
    module = "src.ui.gif_builder" if kind == "gif" else "src.ui.selective_alpha_tool"
    if kind == "gif":
        widget._add_paths([str(source)])
    source.unlink()
    try:
        with patch(f"{module}.QFileDialog.getSaveFileName", return_value=(str(target), "")), \
                patch(f"{module}.QMessageBox.information"), \
                patch(f"{module}.QMessageBox.critical") as error:
            if kind == "gif":
                widget._export()
                wait_for_gif_export(widget)
            else:
                painter._on_save()
        error.assert_not_called()
        with Image.open(target) as image:
            assert image.size == (8, 8)
        assert not source.exists()
        assert not list(tmp_path.glob(".alpha_fixer_save_*"))
    finally:
        if kind == "gif":
            widget._clear_all()
            widget.close()
            sip.delete(widget)


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
                        if kind == "gif":
                            wait_for_gif_export(widget)
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
                wait_for_gif_export(widget)
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
            stack.enter_context(patch("src.core.video_export.mux_mp4_audio", side_effect=OSError("mux failure")))
            if fmt == "mp4":
                stack.enter_context(patch("imageio.get_writer", get_writer))
            widget._export()
            wait_for_video_export(widget)
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
            if kind == "gif_builder":
                wait_for_gif_export(widget)
            else:
                wait_for_video_export(widget)
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


@pytest.fixture(params=["gif", "video"])
def playing_builder(request, app, painter):
    if request.param == "gif":
        widget = GifBuilderDialog()
        second = Path(painter._src_path).with_name("second.png")
        with Image.new("RGBA", (8, 8), (50, 60, 70, 255)) as image:
            image.save(second)
        widget._add_paths([painter._src_path, str(second)])
        module = "src.ui.gif_builder"
    else:
        widget = VideoToolDialog()
        clip = _ClipEntry(painter._src_path, 2,
                          lambda _: Image.new("RGBA", (8, 8), (10, 20, 30, 255)),
                          clip_type="video", frame_size=(8, 8))
        widget._clips.append(clip)
        item = QListWidgetItem("source")
        item.setData(_CLIP_ROLE, clip)
        widget._clip_list.addItem(item)
        widget._clip_list.setCurrentRow(0)
        widget._update_scrubber()
        widget._update_ui_state()
        widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData("gif"))
        module = "src.ui.video_tool"
    widget._btn_play.setChecked(True)
    assert widget._preview_timer.isActive()
    yield widget, module
    widget._preview_timer.stop()
    if request.param == "gif":
        widget._clear_all()
    widget.close()
    sip.delete(widget)


@pytest.mark.parametrize("cancel", ["dialog", "overwrite"])
def test_canceling_save_dialog_keeps_preview_playing(playing_builder, tmp_path, cancel):
    widget, module = playing_builder
    chosen = tmp_path / "existing"
    chosen.with_suffix(".gif").write_bytes(b"original")
    path = "" if cancel == "dialog" else str(chosen)
    with patch(f"{module}.QFileDialog.getSaveFileName", return_value=(path, "")), \
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        widget._export()
    assert widget._preview_timer.isActive()
    assert widget._btn_play.isChecked()
    assert chosen.with_suffix(".gif").read_bytes() == b"original"


def test_confirmed_export_pauses_preview(playing_builder, tmp_path):
    widget, module = playing_builder
    output = tmp_path / "result.gif"

    def progress(*args, **kwargs):
        assert not widget._preview_timer.isActive()
        assert not widget._btn_play.isChecked()
        assert "Play" in widget._btn_play.text()
        return QProgressDialog(*args, **kwargs)

    with patch(f"{module}.QFileDialog.getSaveFileName", return_value=(str(output), "")), \
            patch(f"{module}.QProgressDialog", progress), \
            patch.object(QMessageBox, "information"):
        widget._export()
        if module.endswith("gif_builder"):
            wait_for_gif_export(widget)
        else:
            wait_for_video_export(widget)
    with Image.open(output) as image:
        assert image.format == "GIF"
    assert not widget._preview_timer.isActive()


def test_cancel_on_last_gif_frame_preserves_destination(playing_builder, tmp_path):
    widget, module = playing_builder
    output = tmp_path / "result.gif"
    output.write_bytes(b"existing")
    dialogs = []

    def progress(*args, **kwargs):
        dialog = QProgressDialog(*args, **kwargs)
        dialogs.append(dialog)
        return dialog

    if module.endswith("gif_builder"):
        original = Image.Image.quantize
        calls = []
        frame_count = len(widget._frames)

        def frame(image, *args, **kwargs):
            result = original(image, *args, **kwargs)
            calls.append(1)
            if len(calls) == frame_count:
                widget._export_worker.cancel()
            return result

        cancel_hook = patch.object(Image.Image, "quantize", frame)
    else:
        from src.ui import video_tool
        original = video_tool._apply_filter
        calls = []

        def frame(image, key):
            result = original(image, key)
            calls.append(1)
            if len(calls) == 2:
                widget._export_worker.cancel()
            return result

        cancel_hook = patch.object(video_tool, "_apply_filter", frame)

    notice = Mock()
    widget.status_notice.connect(notice)
    with patch(f"{module}.QFileDialog.getSaveFileName", return_value=(str(output), "")), \
            patch(f"{module}.QProgressDialog", progress), cancel_hook, \
            patch.object(widget, "_record_export_history") as history, \
            patch.object(QMessageBox, "information") as info:
        widget._export()
        if module.endswith("gif_builder"):
            wait_for_gif_export(widget)
        else:
            wait_for_video_export(widget)
    assert output.read_bytes() == b"existing"
    history.assert_not_called()
    info.assert_not_called()
    notice.assert_not_called()
    assert sip.isdeleted(dialogs[0]) or not dialogs[0].isVisible()
    assert not list(tmp_path.glob("*alpha_fixer*"))


@pytest.mark.parametrize("playing_builder", ["video"], indirect=True)
@pytest.mark.parametrize("stage", ["writer_close", "mux_start"])
def test_mp4_late_cancellation_skips_mux_and_commit(playing_builder, tmp_path, stage):
    widget, module = playing_builder
    widget._mp4_export_available = True
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData("mp4"))
    output = tmp_path / "result.mp4"
    output.write_bytes(b"existing")
    dialogs = []

    def progress(*args, **kwargs):
        dialog = QProgressDialog(*args, **kwargs)
        label = dialog.setLabelText

        def set_label(text):
            label(text)
            if stage == "mux_start" and text.startswith("Mixing source audio"):
                dialog.cancel()

        dialog.setLabelText = set_label
        dialogs.append(dialog)
        return dialog

    def get_writer(path, **kwargs):
        writer = Mock()

        def close():
            Path(path).write_bytes(b"encoded")
            if stage == "writer_close":
                widget._export_worker.cancel()

        writer.close.side_effect = close
        return writer

    def cancel_mux(*args):
        widget._export_worker.cancel()
        args[-1]()

    with patch(f"{module}.QFileDialog.getSaveFileName", return_value=(str(output), "")), \
            patch(f"{module}.QProgressDialog", progress), \
            patch("imageio.get_writer", get_writer), \
            patch.object(widget, "_should_mux_audio", return_value=True), \
            patch("src.core.video_export.mux_mp4_audio", side_effect=cancel_mux) as mux, \
            patch.object(widget, "_record_export_history") as history, \
            patch.object(QMessageBox, "information") as info:
        widget._export()
        wait_for_video_export(widget)
    assert output.read_bytes() == b"existing"
    if stage == "writer_close":
        mux.assert_not_called()
    history.assert_not_called()
    info.assert_not_called()
    assert sip.isdeleted(dialogs[0]) or not dialogs[0].isVisible()
    assert not list(tmp_path.glob("*alpha_fixer*"))
