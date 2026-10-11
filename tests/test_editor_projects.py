"""Project persistence/recovery without a MainWindow or external media corpus."""
import json
import subprocess
import time
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.editor_projects import read_project, validate, write_project, prepare_entries, source_signature
from src.ui.gif_builder import GifBuilderDialog
from src.ui.video_tool import VideoToolDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(app, tmp_path):
    widgets = []
    with patch("src.ui.editor_project.user_data_directory", return_value=tmp_path / "data"):
        def create(kind, **kwargs):
            widget = GifBuilderDialog(**kwargs) if kind == "gif" else VideoToolDialog(**kwargs)
            widgets.append(widget)
            return widget
        yield create
    for widget in widgets:
        wait(widget)
        widget._project._closing_approved = True
        widget._project._release_session()
        widget.close()
        sip.delete(widget)


def wait(widget):
    deadline = time.monotonic() + 15
    while widget._project._job is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert widget._project._job is None
    QApplication.processEvents()


def sources(folder):
    frames = [Image.new("RGBA", (10, 8), color) for color in ("red", "green", "blue")]
    path = folder / "source.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[80, 140, 230])
    still = folder / "still.png"
    frames[0].save(still)
    for frame in frames:
        frame.close()
    return path, still


def save(widget, path):
    with patch("src.ui.editor_project.QFileDialog.getSaveFileName", return_value=(str(path), "")):
        widget._project.save_button.click()
    wait(widget)
    assert path.is_file()


def open_project(widget, path):
    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=(str(path), "")), \
            patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
        widget._project.open_button.click()
        wait(widget)


def test_gif_owned_roundtrip_and_real_export_holds(dialogs, tmp_path):
    source, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(source), str(still)])
    widget._frames[3].delay_ms = None
    widget._delay_slider.setValue(350)
    widget._frame_list.setCurrentRow(2)
    widget._scrubber.setValue(1)
    path = tmp_path / "owned.gifproject.json"
    save(widget, path)
    assert not widget._project.dirty
    source.unlink()
    still.unlink()
    loaded = dialogs("gif")
    open_project(loaded, path)
    assert [e.delay_ms for e in loaded._frames] == [80, 140, 230, None]
    assert [e.source_delay_ms for e in loaded._frames[:3]] == [80, 140, 230]
    assert loaded._frame_list.currentRow() == 2
    assert loaded._preview_idx == 1
    assert loaded._delay_slider.value() == 350
    output = tmp_path / "export.gif"
    from tests.gif_export_helpers import wait_for_gif_export
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName", return_value=(str(output), "")), \
            patch("src.ui.gif_builder.QMessageBox.information"):
        loaded._export()
        wait_for_gif_export(loaded)
    with Image.open(output) as image:
        delays = []
        for index in range(image.n_frames):
            image.seek(index)
            delays.append(image.info["duration"])
    assert delays == [80, 140, 230, 350]


def test_video_gif_split_still_settings_roundtrip(dialogs, tmp_path):
    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source), str(still)])
    widget._clip_list.setCurrentRow(0)
    widget._clip_speed_slider.setValue(70)
    widget._scrubber.setValue(1)
    widget._split_clip_at_playhead()
    widget._clip_list.setCurrentRow(2)
    widget._still_duration_spin.setValue(91)
    widget._brightness_slider.setValue(165)
    widget._filter_combo.setCurrentIndex(widget._filter_combo.findData("sepia"))
    widget._audio_volume_slider.setValue(72)
    path = tmp_path / "timeline.videoproject.json"
    save(widget, path)
    document = read_project(path)
    assert document["entries"][0]["source"] == source.name
    assert len(document["entries"][0]["split"]) == 8
    loaded = dialogs("video")
    open_project(loaded, path)
    assert [c._split_timing for c in loaded._clips] == [c._split_timing for c in widget._clips]
    assert [c._split_audio_range() for c in loaded._clips] == [c._split_audio_range() for c in widget._clips]
    assert loaded._clips[2].still_duration_frames == 91
    assert loaded._brightness_slider.value() == 165
    assert loaded._filter_combo.currentData() == "sepia"
    assert loaded._audio_volume_slider.value() == 72


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=2),
    lambda d: d.update(version=True),
    lambda d: d.update(unexpected="exec"),
    lambda d: d["controls"].update(_delay_slider=True),
    lambda d: d.update(playhead=999),
    lambda d: d["entries"][0].update(size=[999999, 999999]),
    lambda d: d["entries"][0].update(delay=-1),
])
def test_invalid_schema_rejected(dialogs, tmp_path, mutation):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    document, images = widget._project._document(tmp_path / "p.json")
    for image in images:
        image.close()
    mutation(document)
    with pytest.raises(ValueError):
        validate(document)


def test_corrupt_load_preserves_live_session(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    path = tmp_path / "good.json"
    save(widget, path)
    document = read_project(path)
    document["entries"][0]["png"] = "not-base64!"
    path.write_text(json.dumps(document))
    original = widget._frames[0]
    with patch("src.ui.editor_project.QMessageBox.critical") as error:
        open_project(widget, path)
    error.assert_called_once()
    assert widget._frames[0] is original
    assert original._pil.getpixel((0, 0)) == (255, 0, 0, 255)


def test_missing_video_media_preserves_live_session(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = widget._clips[0]
    still.unlink()
    with patch("src.ui.editor_project.QMessageBox.critical") as error:
        open_project(widget, path)
    error.assert_called_once()
    assert widget._clips[0] is original
    assert original.get_frame(0).getpixel((0, 0)) == (255, 0, 0, 255)


def test_atomic_save_failure_preserves_file_and_dirty_state(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = path.read_bytes()
    widget._delay_slider.setValue(777)
    entry = widget._frames[0]
    with patch("os.replace", side_effect=OSError("disk disconnected")), \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        widget._project._save(path)
        wait(widget)
    error.assert_called_once()
    assert path.read_bytes() == original
    assert widget._project.dirty
    assert widget._frames[0] is entry
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


def test_recovery_multi_instance_and_failed_autosave(dialogs, tmp_path):
    _, still = sources(tmp_path)
    first, second = dialogs("gif"), dialogs("gif")
    for widget in (first, second):
        widget._add_paths([str(still)])
        widget._project.autosave()
        wait(widget)
    one, two = first._project.recovery_path, second._project.recovery_path
    assert one != two and one.is_file() and two.is_file()
    first._project.refresh_recoveries()
    assert two not in first._project._recoveries
    before = one.read_bytes()
    first._delay_slider.setValue(400)
    with patch("os.replace", side_effect=OSError("full disk")), \
            patch("src.ui.editor_project.QMessageBox.critical") as modal:
        first._project.autosave()
        wait(first)
    modal.assert_not_called()
    assert one.read_bytes() == before
    assert "full disk" in first._project.status.text()
    first._project._release_session()
    second._project.refresh_recoveries()
    assert one in second._project._recoveries
    assert second._project.recover_button.isEnabled()
    with patch("src.ui.editor_project.QInputDialog.getItem",
               side_effect=lambda parent, title, prompt, labels, *args: (labels[0], True)), \
            patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
        second._project.recover_button.click()
        wait(second)
    assert second._project.dirty
    assert one.is_file()
    assert len(second._frames) == 1


def test_debounce_no_slider_tick_writes(dialogs):
    widget = dialogs("gif")
    with patch.object(widget._project, "autosave") as autosave:
        for value in range(100, 200):
            widget._delay_slider.setValue(value)
            widget._project.poll()
        autosave.assert_not_called()
        widget._project._changed_at -= 6
        widget._project.poll()
        autosave.assert_called_once()


def test_close_cancel_save_and_export_guard(dialogs, tmp_path):
    widget = dialogs("gif")
    with patch("src.ui.editor_project.QMessageBox.question") as question:
        assert widget._project.allow_close(lambda: None)
        question.assert_not_called()
    widget._project._closing_approved = False
    widget._delay_slider.setValue(300)
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        assert not widget._project.allow_close(lambda: None)
    resumed = []
    path = tmp_path / "close.json"
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Save), \
            patch("src.ui.editor_project.QFileDialog.getSaveFileName", return_value=(str(path), "")):
        assert not widget._project.allow_close(lambda: resumed.append(True))
        wait(widget)
    assert resumed == [True] and path.is_file()
    with patch.object(widget, "is_exporting", return_value=True), \
            patch("src.ui.editor_project.QFileDialog.getOpenFileName") as picker:
        widget._project.open_project()
        picker.assert_not_called()


def test_replacement_cancel_keeps_edits_and_prepared_resources_clean(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    widget._delay_slider.setValue(250)
    original = widget._frames[0]
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        widget._project.load(path)
        wait(widget)
    assert widget._frames[0] is original and widget._delay_slider.value() == 250


def test_duplicate_keys_and_wrong_editor(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError, match="Duplicate"):
        read_project(path)


def test_video_split_stream_mapping_data_only(dialogs, tmp_path):
    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    widget._clip_list.setCurrentRow(0)
    widget._scrubber.setValue(0)
    widget._split_clip_at_playhead()
    path = tmp_path / "p.json"
    document, _ = widget._project._document(path)
    # Simulate a decoded multi-stream video without requiring an ffmpeg corpus.
    for entry in document["entries"]:
        entry.update(loader="video", type="video", video_stream=3, audio_stream=7,
                     source_sha256=source_signature(source))
    validate(document)
    clip_template = widget._clips[0]

    def loader(path, **kwargs):
        from src.ui.video_tool import _ClipEntry, _ImageFrameGetter
        assert kwargs == dict(preferred_video_stream_index=3, preferred_audio_stream_index=7)
        return _ClipEntry(path, clip_template.total_frames, _ImageFrameGetter(Image.new("RGBA", (10, 8))),
                          clip_template.fps, clip_type="video", has_audio=True,
                          preferred_video_stream_index=3, preferred_audio_stream_index=7)

    with patch("src.ui.video_tool._load_video_clip", side_effect=loader):
        prepared = prepare_entries(document, path)
    try:
        assert [c._split_timing for c in prepared] == [tuple(e["split"]) for e in document["entries"]]
        assert all(c.has_audio and c.preferred_audio_stream_index == 7 for c in prepared)
    finally:
        for clip in prepared:
            clip.close()


def test_generated_video_audio_split_roundtrip(dialogs, tmp_path):
    from src.ui.video_tool import _get_ffmpeg_exe
    executable = _get_ffmpeg_exe()
    if not executable:
        pytest.skip("Existing FFmpeg backend unavailable")
    source = tmp_path / "tone.mp4"
    subprocess.run([executable, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=16x16:r=20:d=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                   check=True, capture_output=True, timeout=30)
    widget = dialogs("video")
    widget._load_video_paths([str(source)])
    assert widget._clips and widget._clips[0].has_audio
    widget._clip_speed_slider.setValue(70)
    widget._trim_start_slider.setValue(2)
    widget._trim_end_slider.setValue(17)
    widget._scrubber.setValue(4)
    widget._split_clip_at_playhead()
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData("mp4"))
    path = tmp_path / "tone.videoproject.json"
    save(widget, path)
    loaded = dialogs("video")
    open_project(loaded, path)
    assert len(loaded._clips) == 2
    assert [c._split_audio_range() for c in loaded._clips] == [c._split_audio_range() for c in widget._clips]
    assert [c._split_timing for c in loaded._clips] == [c._split_timing for c in widget._clips]
    assert all(c.has_audio for c in loaded._clips)
    assert loaded._export_fmt_combo.currentData() == "mp4"


def test_wrong_editor_and_bounded_images(dialogs, tmp_path):
    _, still = sources(tmp_path)
    gif = dialogs("gif")
    gif._add_paths([str(still)])
    path = tmp_path / "p.json"
    save(gif, path)
    with pytest.raises(ValueError, match="different editor"):
        read_project(path, "video")
    from src.core.editor_projects import MAX_ENTRIES, MAX_TOTAL_PIXELS
    document = read_project(path)
    document["entries"] = document["entries"] * (MAX_ENTRIES + 1)
    with pytest.raises(ValueError, match="at most"):
        validate(document)
    document = read_project(path)
    document["entries"][0]["size"] = [4096, MAX_TOTAL_PIXELS // 4096 + 1]
    with pytest.raises(ValueError, match="total pixels"):
        validate(document)


def test_parent_shutdown_waits_for_project_and_reopen_uses_new_identity(dialogs, tmp_path):
    from threading import Event
    from src.ui import editor_project
    widget = dialogs("gif")
    widget._delay_slider.setValue(345)
    entered, release = Event(), Event()
    writer = editor_project.write_project

    def blocked_writer(*args):
        entered.set()
        assert release.wait(5)
        writer(*args)

    resumed = []
    with patch.object(editor_project, "write_project", side_effect=blocked_writer):
        widget._project._save(tmp_path / "p.json")
        assert entered.wait(5)
        assert not widget._project.allow_parent_close(lambda: resumed.append(True))
        release.set()
        wait(widget)
    assert resumed == [True]
    identity = widget._project.session_id
    assert widget._project.allow_close(lambda: None)
    widget._project.resume_session()
    assert widget._project.session_id != identity
    assert widget._project.timer.isActive()


def test_multi_editor_cancelled_shutdown_keeps_recovery_live(dialogs, tmp_path):
    from src.ui.editor_project import approve_parent_projects
    _, still = sources(tmp_path)
    gif, video = dialogs("gif"), dialogs("video")
    gif._add_paths([str(still)])
    video._load_image_paths([str(still)])
    gif._project.autosave()
    wait(gif)
    lock = gif._project._lock
    with patch("src.ui.editor_project.QMessageBox.question",
               side_effect=[QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel]):
        assert not approve_parent_projects([gif, video], lambda: None)
    assert not gif._project._closing_approved
    assert gif._project.timer.isActive() and gif._project._lock is lock
    gif._delay_slider.setValue(619)
    gif._project.autosave()
    wait(gif)
    assert read_project(gif._project.recovery_path)["controls"]["_delay_slider"] == 619
    with patch("src.ui.editor_project.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Cancel) as question:
        assert not gif._project.allow_close(lambda: None)
    question.assert_called_once()


def test_multi_editor_save_then_cancel_does_not_commit_shutdown(dialogs, tmp_path):
    from src.ui.editor_project import approve_parent_projects
    gif, video = dialogs("gif"), dialogs("video")
    gif._delay_slider.setValue(444)
    video._brightness_slider.setValue(150)
    resumed = []
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Save), \
            patch("src.ui.editor_project.QFileDialog.getSaveFileName",
                  return_value=(str(tmp_path / "save.json"), "")):
        assert not approve_parent_projects([gif, video], lambda: resumed.append(True))
        wait(gif)
    assert resumed == [True]
    assert not gif._project._closing_approved and gif._project.timer.isActive()
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        assert not approve_parent_projects([gif, video], lambda: None)
    assert not gif._project._closing_approved and gif._project.timer.isActive()


@pytest.mark.parametrize("reopen", [False, True])
def test_supplied_gif_media_autosaves_without_edit(dialogs, tmp_path, reopen):
    from src.ui.main_window import MainWindow
    _, still = sources(tmp_path)
    if reopen:
        widget = dialogs("gif")
        widget.show()
        assert widget.close()
        # Exercise the production opener on a lightweight host, not MainWindow construction.
        class Host:
            _gif_builder_dlg = widget
            def _update_builder_status(self):
                pass
            def _update_current_tool_status(self):
                pass
        MainWindow._open_or_focus_gif_builder(Host(), [str(still)])
    else:
        widget = dialogs("gif", initial_files=[str(still)])
        widget.show()
    controller = widget._project
    assert controller.dirty
    assert controller.fingerprint() != controller._autosaved
    controller.poll()
    controller._changed_at -= 6
    controller.poll()
    wait(widget)
    document = read_project(controller.recovery_path)
    assert len(document["entries"]) == 1
    assert controller.dirty
    with patch("src.ui.editor_project.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Cancel) as question:
        assert not controller.allow_close(lambda: None)
    question.assert_called_once()


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("accepted", [False, True])
def test_normalized_project_destination_requires_confirmation(dialogs, tmp_path, kind, accepted):
    widget = dialogs(kind)
    widget._project.poll()
    widget._project._changed_at = time.monotonic()
    chosen = tmp_path / "existing"
    destination = tmp_path / f"existing.{kind}project.json"
    destination.write_bytes(b"keep me")
    with patch("src.ui.editor_project.QFileDialog.getSaveFileName", return_value=(str(chosen), "")), \
            patch("PyQt6.QtWidgets.QMessageBox.question",
                  return_value=QMessageBox.StandardButton.Yes if accepted else QMessageBox.StandardButton.No) as question:
        assert widget._project.save_project() == accepted
        wait(widget)
    question.assert_called_once()
    if accepted:
        assert read_project(destination)["kind"] == kind
    else:
        assert destination.read_bytes() == b"keep me"
        assert widget._project.path == ""


def test_same_shape_replaced_media_rejected_even_with_preserved_metadata(dialogs, tmp_path):
    import os
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = widget._clips[0]
    stat = still.stat()
    image = Image.new("RGBA", (10, 8), "blue")
    image.save(still)
    image.close()
    os.utime(still, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with patch("src.ui.editor_project.QMessageBox.critical") as error:
        open_project(widget, path)
    error.assert_called_once()
    assert "changed since project save" in error.call_args.args[2]
    assert widget._clips[0] is original
    assert original.get_frame(0).getpixel((0, 0)) == (255, 0, 0, 255)


def test_media_hash_runs_only_in_project_workers_and_once_per_source(dialogs, tmp_path):
    import threading
    from src.ui import editor_project
    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    widget._scrubber.setValue(1)
    widget._split_clip_at_playhead()
    path = tmp_path / "p.json"
    threads = []
    def signature(path):
        threads.append(threading.get_ident())
        return source_signature(path)
    with patch.object(editor_project, "source_signature", side_effect=signature) as hashing:
        save(widget, path)
        for _ in range(3):
            widget._project.poll()
        hashing.assert_called_once()
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    with patch("src.core.editor_projects.source_signature", side_effect=signature) as hashing:
        open_project(widget, path)
        hashing.assert_called_once()
    assert all(thread != threading.get_ident() for thread in threads)


def test_cancelled_parent_shutdown_preserves_closed_editor_reopen(dialogs, tmp_path):
    from src.ui.editor_project import approve_parent_projects
    gif, video = dialogs("gif"), dialogs("video")
    gif.show()
    assert gif.close()
    identity = gif._project.session_id
    video._brightness_slider.setValue(151)
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        assert not approve_parent_projects([gif, video], lambda: None)
    assert gif._project._closing_approved
    gif.show()
    assert gif._project.session_id != identity and gif._project.timer.isActive()


def test_identity_failure_closes_already_prepared_clips(dialogs, tmp_path):
    from src.ui import video_tool
    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source), str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    image = Image.new("RGBA", (10, 8), "blue")
    image.save(still)
    image.close()
    prepared = []
    loader = video_tool._load_image_as_clip
    def load(path):
        clip = loader(path)
        clip.close = Mock(wraps=clip.close)
        prepared.append(clip)
        return clip
    with patch.object(video_tool, "_load_image_as_clip", side_effect=load):
        with pytest.raises(ValueError, match="changed since project save"):
            prepare_entries(read_project(path), path)
    assert len(prepared) == 1
    prepared[0].close.assert_called_once()


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("identity", ["import", "saved", "loaded"])
def test_save_rejects_replaced_source_preserves_disk(dialogs, tmp_path, automatic, identity):
    import os
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    if identity != "import":
        save(widget, path)
        if identity == "loaded":
            open_project(widget, path)
    controller = widget._project
    if automatic:
        destination = controller.recovery_path
        destination.parent.mkdir(parents=True, exist_ok=True)
    else:
        destination = path
    if not destination.exists():
        destination.write_bytes(b"previous complete snapshot")
    before = destination.read_bytes()
    saved, autosaved = controller._saved, controller._autosaved
    original = widget._clips[0]
    stat = still.stat()
    with Image.new("RGBA", (10, 8), "blue") as replacement:
        replacement.save(still)
    os.utime(still, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    # Force the cached-hash branch, independently of reliable filesystem timestamps.
    if identity != "import":
        original._source_stamp = editor_project._source_stamp(still.stat())
    with patch.object(editor_project.QMessageBox, "critical") as error:
        assert controller._save(destination, automatic=automatic)
        wait(widget)
    assert destination.read_bytes() == before
    assert controller._saved == saved and controller._autosaved == autosaved
    assert "changed since" in controller.status.text()
    assert error.call_count == (0 if automatic else 1)
    assert widget._clips[0] is original
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("accepted", [False, True])
def test_parent_shutdown_releases_prepared_media_only_after_all_approve(dialogs, tmp_path, accepted):
    from src.ui.editor_project import approve_parent_projects
    from src.ui.video_tool import _ClipEntry, _VideoFrameGetter, _CLIP_ROLE
    from PyQt6.QtWidgets import QListWidgetItem
    video, gif = dialogs("video"), dialogs("gif")
    prepared = tmp_path / "prepared.mp4"
    prepared.write_bytes(b"prepared media")
    getter = _VideoFrameGetter(str(prepared), 1, cleanup_paths=[str(prepared)])
    reader = Mock()
    getter._reader = reader
    clip = _ClipEntry(str(prepared), 1, getter)
    video._clips.append(clip)
    item = QListWidgetItem("prepared")
    item.setData(_CLIP_ROLE, clip)
    video._clip_list.addItem(item)
    gif._delay_slider.setValue(444)
    video._project._saved = video._project.fingerprint()
    release = Mock(wraps=clip.close)
    clip.close = release
    with patch("src.ui.editor_project.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Discard if accepted else QMessageBox.StandardButton.Cancel):
        assert approve_parent_projects([video, gif], lambda: None) == accepted
    if accepted:
        release.assert_called_once()
        reader.close.assert_called_once()
        assert not prepared.exists() and not video._clips
        assert not video._project.timer.isActive()
    else:
        release.assert_not_called()
        reader.close.assert_not_called()
        assert prepared.exists() and video._clips == [clip]
        assert video._project.timer.isActive() and not video._project._closing_approved


@pytest.mark.parametrize("failure", ["controls", "clone", "rollback"])
def test_install_snapshot_failure_releases_prepared_entries(dialogs, tmp_path, failure):
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    document = read_project(path)
    entries = prepare_entries(document, path)
    original = widget._clips[0]
    for entry in entries:
        entry.close = Mock(wraps=entry.close)
    if failure == "controls":
        target, attribute = widget._project, "controls"
    elif failure == "clone":
        target, attribute = editor_project.QListWidgetItem, "clone"
    else:
        target, attribute = widget._project, "_apply_state"
    with patch.object(target, attribute, side_effect=MemoryError("snapshot failure")):
        with pytest.raises(MemoryError, match="snapshot failure"):
            widget._project._install(document, entries)
    for entry in entries:
        entry.close.assert_called_once()
    assert widget._clips == [original]
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


def test_first_autosave_retains_hash_and_pending_parent_close_keeps_media(dialogs, tmp_path):
    from threading import Event
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    clip = widget._clips[0]
    assert clip._source_sha256 is None
    clip.close = Mock(wraps=clip.close)
    entered, release = Event(), Event()
    writer = editor_project.write_project
    resumed = []
    def blocked_writer(*args):
        entered.set()
        assert release.wait(5)
        writer(*args)
    with patch.object(editor_project, "write_project", side_effect=blocked_writer):
        assert widget._project.autosave()
        assert entered.wait(5)
        try:
            assert not editor_project.approve_parent_projects([widget], lambda: resumed.append(True))
            clip.close.assert_not_called()
            assert widget._project.timer.isActive()
        finally:
            release.set()
            wait(widget)
    assert resumed == [True]
    assert clip._source_sha256 == source_signature(still)
    with patch.object(editor_project.QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
        assert editor_project.approve_parent_projects([widget], lambda: None)
    clip.close.assert_called_once()


def test_import_rejects_source_changed_during_decode(tmp_path):
    from src.ui.video_tool import _load_identified_clip
    _, still = sources(tmp_path)
    clip = Mock()
    def decode():
        with Image.new("RGBA", (10, 8), "blue") as replacement:
            replacement.save(still)
        return clip
    assert _load_identified_clip(str(still), decode) is None
    clip.close.assert_called_once()


@pytest.mark.parametrize("identity", ["saved", "loaded"])
@pytest.mark.parametrize("loader", ["image", "video"])
def test_reload_rejects_replaced_project_source(dialogs, tmp_path, identity, loader):
    import os
    from src.ui import video_tool
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    if identity == "loaded":
        open_project(widget, path)
    original = widget._clips[0]
    if loader == "video":
        original.clip_type = "video"
    original.close = Mock(wraps=original.close)
    retained = original._source_sha256, original._source_stamp
    stat = still.stat()
    with Image.new("RGBA", (10, 8), "blue") as replacement:
        replacement.save(still)
    os.utime(still, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    name = "_load_video_clip" if loader == "video" else "_load_image_as_clip"
    with patch.object(video_tool, name) as decode:
        assert widget._reload_clip(original, preferred_video_stream_index=7) is None
    decode.assert_not_called()
    original.close.assert_not_called()
    assert widget._clips == [original]
    assert (original._source_sha256, original._source_stamp) == retained
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("identity", ["saved", "loaded"])
def test_unchanged_reload_and_splits_retain_project_source_identity(dialogs, tmp_path, identity):
    from src.ui import editor_project
    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    path = tmp_path / "p.json"
    save(widget, path)
    if identity == "loaded":
        open_project(widget, path)
    original = widget._clips[0]
    retained = original._source_sha256, original._source_stamp
    original.speed_percent = 125
    with patch.object(editor_project, "source_signature", side_effect=AssertionError("GUI hash")), \
            patch("src.core.editor_projects.source_signature", side_effect=AssertionError("GUI hash")):
        reloaded = widget._reload_clip(original)
        assert reloaded is not None and reloaded is not original
        try:
            assert (reloaded._source_sha256, reloaded._source_stamp) == retained
            assert reloaded.speed_percent == 125
        finally:
            reloaded.close()
        widget._scrubber.setValue(0)
        widget._split_clip_at_playhead()
    assert len(widget._clips) == 2
    assert all((clip._source_sha256, clip._source_stamp) == retained for clip in widget._clips)
    save(widget, path)
    assert all(entry["source_sha256"] == retained[0] for entry in read_project(path)["entries"])


@pytest.mark.parametrize("loader", ["image", "video"])
def test_reload_rejects_source_changed_during_decode(dialogs, tmp_path, loader):
    from src.ui import video_tool
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    original = widget._clips[0]
    original.close = Mock(wraps=original.close)
    original.clip_type = loader
    retained = original._source_sha256, original._source_stamp
    decoded = video_tool._load_image_as_clip(str(still))
    decoded.close = Mock(wraps=decoded.close)
    def decode(*args, **kwargs):
        with Image.new("RGBA", (10, 8), "blue") as replacement:
            replacement.save(still)
        return decoded
    name = "_load_video_clip" if loader == "video" else "_load_image_as_clip"
    with patch.object(video_tool, name, side_effect=decode):
        assert widget._reload_clip(original) is None
    decoded.close.assert_called_once()
    original.close.assert_not_called()
    assert widget._clips == [original]
    assert (original._source_sha256, original._source_stamp) == retained
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


def test_reload_retained_hash_rejects_replacement_at_worker_save(dialogs, tmp_path):
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = widget._clips[0]
    before = path.read_bytes()
    with Image.new("RGBA", (10, 8), "blue") as replacement:
        replacement.save(still)
    # Exercise deferred content verification even when metadata comparison passes.
    original._source_stamp = editor_project._source_stamp(still.stat())
    retained = original._source_sha256, original._source_stamp
    reloaded = widget._reload_clip(original)
    assert reloaded is not None
    assert (reloaded._source_sha256, reloaded._source_stamp) == retained
    widget._clips[0] = reloaded
    original.close()
    with patch.object(editor_project.QMessageBox, "critical") as error:
        assert widget._project._save(path)
        wait(widget)
    error.assert_called_once()
    assert "changed since" in widget._project.status.text()
    assert path.read_bytes() == before
