import json
from pathlib import Path
import subprocess
from threading import Event, get_ident
import time
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PyQt6.QtWidgets import QApplication

from src.core import video_export
from src.ui import video_tool as vt
from tests.video_export_helpers import wait_for_video_export


def pump_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.fixture
def dialog(video_app):
    app = video_app
    widget = vt.VideoToolDialog()
    image = Image.new("RGBA", (18, 20), (120, 40, 20, 255))
    clip = vt._ClipEntry("source.png", 1, vt._ImageFrameGetter(image),
                         clip_type="image", frame_size=image.size)
    clip.still_duration_frames = 2
    widget._clips = [clip]
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData("gif"))
    widget._mp4_export_available = True
    widget._record_export_history = Mock()
    with patch.object(vt.QMessageBox, "information") as info, \
            patch.object(vt.QMessageBox, "critical") as error:
        widget._test_info = info
        widget._test_error = error
        yield widget
        if widget.is_exporting():
            widget.request_export_cancel()
            wait_for_video_export(widget)
        widget.close()
        sip.delete(widget)
        app.processEvents()


class Writer:
    def __init__(self, path):
        self.path = path

    def append_data(self, data):
        pass

    def close(self):
        Path(self.path).write_bytes(b"encoded MP4")


@pytest.fixture(scope="session")
def video_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("stage", ["render", "gif_save", "writer_append", "writer_close", "mux"])
@pytest.mark.parametrize("existing", [False, True])
def test_slow_pipeline_keeps_gui_responsive_and_cancel_discards_stage(dialog, tmp_path, stage, existing):
    fmt = "gif" if stage in {"render", "gif_save"} else "mp4"
    dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData(fmt))
    destination = tmp_path / f"output.{fmt}"
    if existing:
        destination.write_bytes(b"original")
    entered, release = Event(), Event()
    gui_thread = get_ident()
    worker_threads = []
    finished = []
    dialog.export_finished.connect(lambda: finished.append(not dialog.is_exporting()))
    heartbeat = []
    timer = QTimer()
    timer.timeout.connect(lambda: heartbeat.append(1))
    timer.start(5)

    def hold():
        worker_threads.append(get_ident())
        entered.set()
        assert release.wait(8)

    class SlowWriter(Writer):
        def append_data(self, data):
            if stage == "writer_append":
                hold()

        def close(self):
            if stage == "writer_close":
                hold()
            super().close()

    real_filter, real_save = vt._apply_filter, Image.Image.save

    def render(image, key):
        if stage == "render":
            hold()
        return real_filter(image, key)

    def save(image, path, *args, **kwargs):
        if stage == "gif_save":
            hold()
        return real_save(image, path, *args, **kwargs)

    def mux(*args):
        hold()
        Path(args[1]).write_bytes(b"muxed")
        args[-1]()

    try:
        with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(destination), "")), \
                patch.object(vt, "_apply_filter", render), \
                patch.object(Image.Image, "save", save), \
                patch("imageio.get_writer", side_effect=lambda path, **kw: SlowWriter(path)), \
                patch.object(dialog, "_should_mux_audio", return_value=stage == "mux"), \
                patch.object(video_export, "mux_mp4_audio", mux):
            dialog._btn_remove.setEnabled(False)
            shortcuts = dialog.findChildren(vt.QShortcut)
            shortcuts[0].setEnabled(False)
            dialog._export()
            pump_until(entered.is_set)
            assert not dialog._export_content.isEnabled()
            before = len(heartbeat)
            pump_until(lambda: len(heartbeat) >= before + 4)
            dialog._export()
            dialog._load_image_paths(["not-a-real-file.png"])
            assert len(dialog._clips) == 1
            dialog._cancel_export()
            assert dialog.is_exporting()
            release.set()
            wait_for_video_export(dialog)
            assert dialog._export_content.isEnabled()
            assert not dialog._btn_remove.isEnabled()
            assert not shortcuts[0].isEnabled()
        assert all(thread != gui_thread for thread in worker_threads)
        assert finished == [True]
        assert destination.read_bytes() == b"original" if existing else not destination.exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == ([destination.name] if existing else [])
        dialog._record_export_history.assert_not_called()
        dialog._test_info.assert_not_called()
        dialog._test_error.assert_not_called()
    finally:
        release.set()
        timer.stop()
        if dialog.is_exporting():
            wait_for_video_export(dialog)


@pytest.mark.parametrize("action", ["close", "reject", "accept"])
def test_dialog_exit_defers_until_native_thread_and_source_cleanup(dialog, tmp_path, action):
    entered, release = Event(), Event()
    real_save = Image.Image.save

    def save(image, *args, **kwargs):
        entered.set()
        assert release.wait(8)
        return real_save(image, *args, **kwargs)

    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(tmp_path / "out.gif"), "")), \
            patch.object(Image.Image, "save", save):
        dialog.show()
        dialog._export()
        pump_until(entered.is_set)
        worker = dialog._export_worker
        getter = worker.clips[0]["frame_getter"]
        getattr(dialog, action)()
        assert dialog.is_exporting()
        assert dialog.isVisible()
        assert getter._frames
        release.set()
        wait_for_video_export(dialog)
    assert not getter._frames
    assert not dialog.isVisible()
    if action == "accept":
        assert dialog.result() == vt.QDialog.DialogCode.Accepted
    assert not (tmp_path / "out.gif").exists()
    dialog._test_info.assert_not_called()


def test_shutdown_does_not_block_on_publication_or_show_modal_when_publish_wins(dialog, tmp_path):
    destination = tmp_path / "out.gif"
    entered, release = Event(), Event()
    import os
    real_replace = os.replace

    def replace(source, target):
        entered.set()
        assert release.wait(8)
        return real_replace(source, target)

    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(destination), "")), \
            patch("os.replace", replace):
        dialog._export()
        pump_until(entered.is_set)
        before = time.monotonic()
        dialog.request_export_cancel()
        assert time.monotonic() - before < 0.1
        assert dialog.is_exporting()
        release.set()
        wait_for_video_export(dialog)
    with Image.open(destination) as result:
        assert result.size == (18, 20)
    dialog._record_export_history.assert_called_once()
    dialog._test_info.assert_not_called()


@pytest.mark.parametrize("stage", ["render", "save", "publish", "snapshot"])
def test_export_error_preserves_destination_cleans_resources_and_emits_finished(dialog, tmp_path, stage):
    output = tmp_path / "out.gif"
    output.write_bytes(b"original")
    finished = Mock()
    dialog.export_finished.connect(finished)
    target = {
        "render": "src.ui.video_tool._apply_filter",
        "save": "PIL.Image.Image.save",
        "publish": "os.replace",
        "snapshot": "PIL.Image.Image.copy",
    }[stage]
    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
            patch(target, side_effect=OSError(f"{stage} failed")):
        dialog._export()
        worker = dialog._export_worker
        wait_for_video_export(dialog)
    assert output.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [output]
    assert dialog._export_content.isEnabled()
    finished.assert_called_once()
    dialog._test_error.assert_called_once()
    dialog._record_export_history.assert_not_called()
    assert not worker.clips[0]["frame_getter"]._frames


@pytest.mark.parametrize("outcome,close_fails", [
    ("cancel", False), ("cancel", True),
    ("render_error", False), ("render_error", True),
    ("close_error", True),
])
def test_mp4_writer_closes_before_windows_restricted_stage_unlink(dialog, tmp_path, outcome, close_fails):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"original")
    dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
    opened = set()
    events = []
    gui_thread = get_ident()
    original_unlink = Path.unlink

    class RestrictedWriter:
        def __init__(self, path):
            self.path = Path(path)
            opened.add(self.path)

        def append_data(self, data):
            if outcome == "cancel":
                dialog._export_worker.cancel()
            elif outcome == "render_error":
                raise OSError("original render failure")

        def close(self):
            events.append(("close", get_ident()))
            self.path.write_bytes(b"partial MP4")
            opened.remove(self.path)
            if close_fails:
                raise OSError("encoder close failure")

    def windows_unlink(path, *args, **kwargs):
        if path in opened:
            events.append(("unlink-open", get_ident()))
            raise PermissionError("Windows sharing violation")
        if path.name.startswith(".alpha_fixer_save_"):
            events.append(("unlink", get_ident()))
        return original_unlink(path, *args, **kwargs)

    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
            patch.object(dialog, "_should_mux_audio", return_value=False), \
            patch("imageio.get_writer", side_effect=lambda path, **kw: RestrictedWriter(path)), \
            patch.object(Path, "unlink", windows_unlink):
        dialog._export()
        worker = dialog._export_worker
        wait_for_video_export(dialog)
    assert [event for event, _ in events][:2] == ["close", "unlink"]
    assert all(event != "unlink-open" for event, _ in events)
    assert all(thread != gui_thread for _, thread in events)
    assert not opened
    assert list(tmp_path.iterdir()) == [output]
    assert output.read_bytes() == b"original"
    assert not worker.clips[0]["frame_getter"]._frames
    dialog._record_export_history.assert_not_called()
    dialog._test_info.assert_not_called()
    if outcome == "cancel":
        assert worker.outcome == "canceled"
        dialog._test_error.assert_not_called()
    else:
        assert worker.outcome == "error"
        assert worker.error == ("original render failure" if outcome == "render_error"
                                else "encoder close failure")
        assert worker.stage == ("frame rendering" if outcome == "render_error"
                                else "MP4 finalization")
        dialog._test_error.assert_called_once()


def test_real_gif_success_reopens_and_snapshots_history_preferences(dialog, tmp_path):
    output = tmp_path / "out.gif"
    dialog._fps_slider.setValue(12)
    dialog._clips[0].still_duration_frames = 1
    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")):
        dialog._export()
        worker = dialog._export_worker
        dialog._fps_slider.setValue(30)  # Programmatic changes cannot change the snapshot.
        wait_for_video_export(dialog)
    with Image.open(output) as result:
        assert result.size == (18, 20)
        assert result.info["duration"] == 80
    assert worker.settings.fps == 12
    assert dialog._record_export_history.call_args.kwargs["preferences"].fps == 12
    assert not worker.clips[0]["frame_getter"]._frames
    dialog._test_info.assert_called_once()


@pytest.mark.parametrize("start,end,speed,required", [
    (2, 2, 50, [2]), (2, 2, 100, [2]), (2, 2, 200, [2]),
    (0, 3, 200, [0, 2]), (1, 2, 50, [1, 2]), (1, 3, 150, [1, 2]),
])
def test_custom_getter_snapshots_only_unique_frozen_trim_speed_indices(
    dialog, tmp_path, start, end, speed, required,
):
    calls = []
    gui_thread = get_ident()

    def getter(index):
        assert get_ident() == gui_thread
        calls.append(index)
        if index not in required:
            raise OSError(f"discarded corrupt frame {index}")
        return Image.new("RGBA", (18, 20), (10 + index * 30, 20, 30, 255))

    clip = vt._ClipEntry("custom.video", 4, getter, frame_size=(18, 20))
    clip.trim_start, clip.trim_end, clip.speed_percent = start, end, speed
    dialog._clips[0].close()
    dialog._clips = [clip]
    output = tmp_path / "trimmed.gif"
    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")):
        dialog._export()
        worker = dialog._export_worker
        # Copy jobs and rendering must use the same frozen mapping, not mutable
        # timeline fields, even when copied source indices are sparse.
        clip.trim_start, clip.trim_end, clip.speed_percent = 0, 3, 100
        wait_for_video_export(dialog)
    assert calls == required
    with Image.open(output) as result:
        assert result.n_frames == len(required)
        for frame_index, source_index in enumerate(required):
            result.seek(frame_index)
            with result.convert("RGB") as rgb:
                assert rgb.getpixel((0, 0)) == (10 + source_index * 30, 20, 30)
    assert not worker.clips[0]["frame_getter"]._frames
    dialog._record_export_history.assert_called_once()
    dialog._test_info.assert_called_once()
    dialog._test_error.assert_not_called()


def test_sequence_getter_copies_only_needed_sparse_source_frames(dialog, tmp_path):
    frames = [Image.new("RGBA", (18, 20), (index * 60, 20, 30, 255)) for index in range(4)]
    getter = vt._SequenceFrameGetter(frames)
    clip = vt._ClipEntry("animation.gif", 4, getter, clip_type="gif", frame_size=(18, 20))
    clip.speed_percent = 200
    dialog._clips[0].close()
    dialog._clips = [clip]
    copied = []
    original_copy = Image.Image.copy

    def copy(image, *args, **kwargs):
        if any(image is frame for frame in frames):
            index = next(index for index, frame in enumerate(frames) if frame is image)
            copied.append(index)
            if index in {1, 3}:
                raise OSError("discarded sequence frame must not be copied")
        return original_copy(image, *args, **kwargs)

    output = tmp_path / "trimmed.gif"
    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
            patch.object(Image.Image, "copy", copy):
        dialog._export()
        wait_for_video_export(dialog)
    assert copied == [0, 2]
    with Image.open(output) as result:
        assert result.n_frames == 2
    dialog._test_error.assert_not_called()


def test_long_logical_snapshot_jobs_are_lazy_and_cancel_before_copy(dialog, tmp_path):
    dialog._clips[0].close()
    source = Mock(side_effect=AssertionError("Canceled source must not be read"))
    dialog._clips = [
        vt._ClipEntry(f"clip-{index}.gif", 60000, source, clip_type="gif",
                      frame_size=(18, 20))
        for index in range(128)
    ]
    with patch.object(vt.QFileDialog, "getSaveFileName",
                      return_value=(str(tmp_path / "out.gif"), "")), \
            patch.object(video_export, "source_frame_index",
                         side_effect=AssertionError("Mapping must not be expanded before copying")):
        dialog._export()
        worker = dialog._export_worker
        assert len(dialog._export_copy_jobs) == 128
        assert all(iter(job[2]) is job[2] for job in dialog._export_copy_jobs)
        dialog._cancel_export()
        wait_for_video_export(dialog)
    source.assert_not_called()
    assert worker.outcome == "canceled"
    assert all(not clip["frame_getter"]._frames for clip in worker.clips)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("speed", [10, 33, 100, 175, 400])
def test_long_logical_timeline_preview_and_snapshot_mapping_match(dialog, speed):
    dialog._clips[0].close()
    requested = []
    getter = Mock(side_effect=lambda index: requested.append(index))
    dialog._clips = []
    for index in range(128):
        clip = vt._ClipEntry(f"clip-{index}.mp4", 60000, getter,
                             fps=29.97, frame_size=(18, 20))
        clip.trim_start, clip.trim_end, clip.speed_percent = 123, 59123, speed
        dialog._clips.append(clip)
    count = dialog._clips[0].active_frames
    assert dialog._total_preview_frames() == 128 * count
    for row in (0, 63, 127):
        clip = dialog._clips[row]
        snapshot = dialog._snapshot_clip_render_state(clip, 25)
        for offset in (0, 1, count // 2, count - 1):
            assert dialog._global_frame_to_clip(row * count + offset) == (row, offset)
            clip.get_frame(offset)
            assert requested[-1] == video_export.source_frame_index(snapshot, offset)
        clip.trim_start, clip.trim_end, clip.speed_percent = 0, 1, 100
        assert video_export.source_frame_index(snapshot, count - 1) == (
            123 + min(59000, int((count - 1) * max(0.1, speed / 100)))
        )
        clip.trim_start, clip.trim_end, clip.speed_percent = 123, 59123, speed


def test_many_clip_export_closes_completed_readers_before_next_source(dialog, tmp_path):
    import numpy as np
    dialog._clips[0].close()
    dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
    opened, readers = [], []

    def open_reader(path):
        assert all(reader.close.called for reader in readers)
        opened.append(path)
        reader = Mock()
        reader.get_data.return_value = np.zeros((20, 18, 3), dtype=np.uint8)
        readers.append(reader)
        return reader

    dialog._clips = [
        vt._ClipEntry(f"clip-{index}.mp4", 60000,
                      vt._VideoFrameGetter(f"clip-{index}.mp4", 60000),
                      frame_size=(18, 20))
        for index in range(128)
    ]
    for clip in dialog._clips:
        clip.trim_start = clip.trim_end = 59999
    with patch.object(vt.QFileDialog, "getSaveFileName",
                      return_value=(str(tmp_path / "out.mp4"), "")), \
            patch.object(vt, "_open_video_reader", side_effect=open_reader), \
            patch("imageio.get_writer", side_effect=lambda path, **kw: Writer(path)):
        dialog._export()
        worker = dialog._export_worker
        wait_for_video_export(dialog)
    assert worker.outcome == "success"
    assert len(opened) == 128
    assert all(reader.get_data.call_args.args == (59999,) for reader in readers)
    assert all(reader.close.call_count == 1 for reader in readers)
    assert all(clip["frame_getter"]._reader is None for clip in worker.clips)
    assert all(clip._get_frame._reader is None for clip in dialog._clips)


def test_long_lazy_export_cancel_releases_current_reader_and_preserves_output(dialog, tmp_path):
    import numpy as np
    dialog._clips[0].close()
    getter = vt._VideoFrameGetter("logical.mp4", 60000)
    clip = vt._ClipEntry("logical.mp4", 60000, getter, frame_size=(18, 20))
    clip.trim_start, clip.trim_end, clip.speed_percent = 100, 59100, 10
    dialog._clips = [clip]
    dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
    output = tmp_path / "out.mp4"
    output.write_bytes(b"original")
    reader = Mock()
    reader.get_data.return_value = np.zeros((20, 18, 3), dtype=np.uint8)
    reader.get_next_data.return_value = reader.get_data.return_value
    rendered = []

    class CancelWriter(Writer):
        def append_data(self, data):
            rendered.append(1)
            if len(rendered) == 200:
                dialog._export_worker.cancel()

    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
            patch.object(vt, "_open_video_reader", return_value=reader), \
            patch("imageio.get_writer", side_effect=lambda path, **kw: CancelWriter(path)):
        dialog._export()
        worker = dialog._export_worker
        wait_for_video_export(dialog)
    assert worker.outcome == "canceled"
    assert len(rendered) == 200
    assert reader.get_data.call_args.args == (100,)
    assert reader.get_next_data.call_count == 19
    reader.close.assert_called_once()
    owned = worker.clips[0]["frame_getter"]
    assert owned._reader is owned._last_frame is None
    assert getter._reader is None
    assert output.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [output]
    dialog._record_export_history.assert_not_called()


def test_lazy_reader_prefetch_cache_random_seeks_and_release():
    import numpy as np
    reader = Mock()
    reader.get_data.side_effect = lambda index: np.full((2, 2, 3), index % 256, dtype=np.uint8)
    reader.get_next_data.return_value = np.full((2, 2, 3), 1, dtype=np.uint8)
    prefetched = np.zeros((2, 2, 3), dtype=np.uint8)
    getter = vt._VideoFrameGetter("logical.mp4", 60000, prefetched)
    with patch.object(vt, "_open_video_reader", return_value=reader) as opened:
        for index in (0, 0, 59999, 0, 1, 1):
            with getter(index) as frame:
                assert frame.getpixel((0, 0))[0] == index % 256
        opened.assert_called_once()
        assert reader.get_data.call_count == 2
        reader.get_next_data.assert_called_once()
        getter._close_reader()
    assert getter._reader is getter._last_frame is getter._prefetched_frame is None
    unused = vt._VideoFrameGetter("logical.mp4", 60000, prefetched)
    unused._release_resources()
    assert unused._prefetched_frame is None


def test_long_preview_cross_clip_scrubs_keep_only_current_reader(dialog):
    import numpy as np
    dialog._clips[0].close()
    readers = []

    def open_reader(path):
        assert all(reader.close.called for reader in readers)
        reader = Mock()
        reader.get_data.return_value = np.zeros((20, 18, 3), dtype=np.uint8)
        readers.append(reader)
        return reader

    dialog._clips = [
        vt._ClipEntry(f"clip-{index}.mp4", 60000,
                      vt._VideoFrameGetter(f"clip-{index}.mp4", 60000),
                      frame_size=(18, 20))
        for index in range(128)
    ]
    dialog._update_scrubber()
    with patch.object(vt, "_open_video_reader", side_effect=open_reader):
        for row in (0, 127, 63, 0, 127):
            dialog._scrubber.setValue(row * 60000 + 59999)
            dialog._update_preview()
            assert sum(clip._get_frame._reader is not None for clip in dialog._clips) == 1
            assert dialog._preview_video_getter is dialog._clips[row]._get_frame
    dialog._release_clips()
    assert dialog._preview_video_getter is None
    assert all(reader.close.call_count == 1 for reader in readers)


def test_actual_medium_video_lazy_random_seeks_keep_one_reader(tmp_path):
    import numpy as np
    ffmpeg = vt._get_ffmpeg_exe()
    if not ffmpeg:
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "medium.mp4"
    # 120 seconds / 3,000 real frames, without an in-memory frame collection.
    subprocess.run([
        ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=32x24:rate=25:duration=120",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, timeout=60)
    fps, count, size, first = vt._probe_video_clip(str(source))
    assert (fps, count, size) == (25, 3000, (32, 24))
    getter = vt._VideoFrameGetter(str(source), count, first)
    reference = vt._open_video_reader(str(source))
    try:
        with patch.object(vt, "_open_video_reader", wraps=vt._open_video_reader) as opened:
            for index in (2999, 0, 1500, 1500, 1499, 1500, 2998, 2999, 0):
                with getter(index) as frame:
                    assert np.array_equal(np.asarray(frame)[:, :, :3], reference.get_data(index))
            opened.assert_called_once()
        assert getter._last_frame.shape == (24, 32, 3)
    finally:
        getter._close_reader()
        reference.close()
    assert getter._reader is getter._last_frame is getter._prefetched_frame is None


def test_cancellable_mux_subprocess_terminates_and_drains_without_fallback():
    checkpoints = []
    process = Mock()
    process.poll.return_value = None
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("ffmpeg", 0.1),
        ("", "terminated"),
    ]

    def checkpoint():
        checkpoints.append(1)
        if len(checkpoints) == 3:
            raise video_export.ExportCanceled()

    with patch.object(video_export.subprocess, "Popen", return_value=process):
        with pytest.raises(video_export.ExportCanceled):
            video_export.run_mux_process(["ffmpeg"], checkpoint)
    process.terminate.assert_called_once()
    assert process.communicate.call_count == 2


def test_cancel_button_interrupts_slow_mux_process_without_silent_fallback(dialog, tmp_path):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"original")
    dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
    dialog._clips[0].clip_type = "video"
    dialog._clips[0].has_audio = True
    entered = Event()
    terminated = Event()
    heartbeat = []
    timer = QTimer()
    timer.timeout.connect(lambda: heartbeat.append(1))
    timer.start(5)

    class Process:
        returncode = None

        def communicate(self, timeout=None):
            if terminated.is_set():
                self.returncode = -15
                return "", "terminated"
            entered.set()
            time.sleep(0.02)
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            terminated.set()

        def kill(self):
            terminated.set()

    process = Process()
    try:
        with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
                patch.object(vt, "_get_ffmpeg_exe", return_value="ffmpeg"), \
                patch.object(dialog, "_should_mux_audio", return_value=True), \
                patch("imageio.get_writer", side_effect=lambda path, **kw: Writer(path)), \
                patch.object(video_export.subprocess, "Popen", return_value=process):
            dialog._export()
            pump_until(entered.is_set)
            before = len(heartbeat)
            pump_until(lambda: len(heartbeat) >= before + 4)
            dialog._export_progress.cancel()
            wait_for_video_export(dialog)
        assert terminated.is_set()
        assert output.read_bytes() == b"original"
        assert list(tmp_path.iterdir()) == [output]
        dialog._record_export_history.assert_not_called()
        dialog._test_info.assert_not_called()
        dialog._test_error.assert_not_called()
    finally:
        timer.stop()


def test_real_mp4_with_audio_reopens_and_uses_independent_video_reader(dialog, tmp_path):
    ffmpeg = vt._get_ffmpeg_exe()
    ffprobe = vt._get_ffprobe_exe()
    if not (ffmpeg and ffprobe and vt._has_imageio() and vt._has_imageio_ffmpeg()):
        pytest.skip("Real MP4/audio export requires imageio, imageio-ffmpeg, ffmpeg and ffprobe")
    source = tmp_path / "source.mp4"
    subprocess.run([
        ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=18x20:r=10:d=0.5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ], check=True, timeout=30)
    clip = vt._load_video_clip(str(source))
    assert clip is not None and clip.has_audio
    dialog._clips[0].close()
    dialog._clips = [clip]
    dialog._fps_slider.setValue(10)
    dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData("mp4"))
    dialog._audio_enable_check.setChecked(True)
    dialog._update_audio_controls()
    preview = clip.get_frame(0)
    preview.close()
    output = tmp_path / "out.mp4"
    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")):
        dialog._export()
        worker = dialog._export_worker
        assert worker.clips[0]["frame_getter"] is not clip._get_frame
        assert worker.clips[0]["frame_getter"]._reader is None
        wait_for_video_export(dialog)
    import imageio
    reader = imageio.get_reader(str(output), format="FFMPEG")
    try:
        assert reader.get_data(0).shape[:2] == (20, 18)
    finally:
        reader.close()
    probe = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(output)],
                           capture_output=True, text=True, check=True, timeout=30)
    assert {s["codec_type"] for s in json.loads(probe.stdout)["streams"]} == {"video", "audio"}
    assert worker.clips[0]["frame_getter"]._reader is None
    assert dialog._record_export_history.call_args.kwargs["audio_mode_override"] == "kept"
    dialog._test_error.assert_not_called()


@pytest.fixture
def parented_video(tmp_path, video_app):
    from src.core.settings_manager import SettingsManager
    from src.ui.main_window import MainWindow

    app = video_app
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        window = MainWindow(settings)
    window.show()
    window._open_or_focus_video_builder()
    widget = window._video_tool_dlg
    image = Image.new("RGBA", (18, 20), "red")
    clip = vt._ClipEntry("source.png", 1, vt._ImageFrameGetter(image),
                         clip_type="image", frame_size=image.size)
    clip.still_duration_frames = 2
    widget._clips = [clip]
    widget._update_ui_state()
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData("gif"))
    assert widget.parentWidget() is window
    with patch.object(vt.QMessageBox, "information") as info, \
            patch.object(vt.QMessageBox, "critical") as error:
        try:
            yield window, widget, settings, info, error
        finally:
            if widget.is_exporting():
                widget.request_export_cancel()
                wait_for_video_export(widget)
            window.close()
            widget.close()
            sip.delete(window)
            settings.sync()
            sip.delete(settings._qs)
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("stage", ["gif_save", "mp4_mux"])
def test_real_main_window_shutdown_defers_parented_video_encoder_cleanup(parented_video, tmp_path, stage):
    window, widget, settings, info, error = parented_video
    fmt = "gif" if stage == "gif_save" else "mp4"
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData(fmt))
    widget._mp4_export_available = True
    output = tmp_path / f"parented.{fmt}"
    output.write_bytes(b"original")
    entered, release = Event(), Event()
    real_save = Image.Image.save

    def save(image, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_save(image, *args, **kwargs)

    def mux(*args):
        entered.set()
        assert release.wait(10)
        args[-1]()
        Path(args[1]).write_bytes(b"muxed")

    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
            patch.object(Image.Image, "save", save), \
            patch("imageio.get_writer", side_effect=lambda path, **kw: Writer(path)), \
            patch.object(video_export, "mux_mp4_audio", mux), \
            patch.object(widget, "_should_mux_audio", return_value=stage == "mp4_mux"), \
            patch.object(widget, "request_export_cancel", wraps=widget.request_export_cancel) as cancel, \
            patch.object(settings, "sync", wraps=settings.sync) as sync:
        try:
            widget._export()
            worker = widget._export_worker
            getter = worker.clips[0]["frame_getter"]
            completion = []
            widget.export_finished.connect(lambda: completion.append(
                (widget.is_exporting(), worker.isRunning(), bool(getter._frames),
                 sip.isdeleted(window), getattr(window, "_shutdown_complete", False))
            ))
            pump_until(entered.is_set)
            assert not window.close()
            assert not window.close()
            cancel.assert_called_once()
            assert window._builder_shutdown_pending == [widget]
            assert window.isVisible()
            assert widget._close_after_export and widget._export_canceling
            assert worker.isRunning()
            assert getter._frames
            assert not sip.isdeleted(window)
            assert output.read_bytes() == b"original"
            sync.assert_not_called()
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(1))
            pump_until(lambda: heartbeat)
        finally:
            release.set()
            wait_for_video_export(widget)
        pump_until(lambda: getattr(window, "_shutdown_complete", False))
        assert completion == [(False, False, False, False, False)]
        assert not window._builder_shutdown_pending
        assert not window.isVisible() and not widget.isVisible()
        assert widget._clips == []
        assert output.read_bytes() == b"original"
        assert not list(tmp_path.glob("*alpha_fixer*"))
        assert settings.get_video_builder_history() == []
        info.assert_not_called()
        error.assert_not_called()
        sync.assert_called_once()
        pump_until(lambda: sip.isdeleted(worker))


@pytest.mark.parametrize("action", ["close", "reject", "accept"])
@pytest.mark.parametrize("deferred", [False, True])
def test_reused_video_dialog_reopens_without_released_clip_rows(parented_video, tmp_path, action, deferred):
    window, widget, settings, info, error = parented_video
    original_clip = widget._clips[0]
    original_image = original_clip._get_frame._img
    item = vt.QListWidgetItem("source")
    item.setData(vt._CLIP_ROLE, original_clip)
    widget._clip_list.addItem(item)
    widget._clip_list.setCurrentRow(0)
    widget._update_scrubber()
    widget._scrubber.setValue(1)
    widget._btn_play.setChecked(True)
    assert widget._preview_timer.isActive()
    assert widget._preview_lbl.pixmap() is not None

    if deferred:
        entered, release = Event(), Event()
        original_save = Image.Image.save

        def save(image, *args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original_save(image, *args, **kwargs)

        with patch.object(vt.QFileDialog, "getSaveFileName",
                          return_value=(str(tmp_path / "canceled.gif"), "")), \
                patch.object(Image.Image, "save", save):
            try:
                widget._export()
                pump_until(entered.is_set)
                getattr(widget, action)()
                assert widget.is_exporting()
                assert widget._clip_list.count() == 1
                assert original_image.getpixel((0, 0)) == (255, 0, 0, 255)
            finally:
                release.set()
                wait_for_video_export(widget)
        assert not (tmp_path / "canceled.gif").exists()
        info.assert_not_called()
    else:
        getattr(widget, action)()

    assert not widget.isVisible()
    assert window.isVisible()
    assert not widget._clips
    assert widget._clip_list.count() == 0
    assert widget._clip_list.currentRow() == -1
    assert widget._clip_list.currentItem() is None
    assert widget._scrubber.value() == widget._scrubber.maximum() == 0
    assert widget._trim_start_slider.maximum() == widget._trim_end_slider.maximum() == 0
    assert widget._pos_lbl.text() == "0 / 0"
    assert widget._preview_lbl.text() == "Add clips to preview and export."
    assert not widget._btn_play.isChecked() and not widget._is_playing
    assert not widget._preview_timer.isActive()
    assert all(not control.isEnabled() for control in (
        widget._btn_export, widget._btn_remove, widget._btn_play, widget._scrubber,
        widget._trim_start_slider, widget._trim_end_slider,
    ))
    with pytest.raises(ValueError):
        original_image.getpixel((0, 0))

    window._open_or_focus_video_builder()
    assert window._video_tool_dlg is widget
    assert widget.isVisible() and widget._clip_list.count() == 0
    source = tmp_path / "replacement.png"
    with Image.new("RGBA", (18, 20), "blue") as image:
        image.save(source)
    widget._load_image_paths([str(source)])
    assert len(widget._clips) == widget._clip_list.count() == 1
    assert widget._clip_list.currentRow() == 0
    assert widget._btn_export.isEnabled() and widget._btn_remove.isEnabled()
    output = tmp_path / "reopened.gif"
    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")):
        widget._export()
        wait_for_video_export(widget)
    with Image.open(output) as result:
        with result.convert("RGB") as rgb:
            assert rgb.getpixel((0, 0)) == (0, 0, 255)
    assert len(settings.get_video_builder_history()) == 1
    widget._remove_selected()
    assert not widget._clips and widget._clip_list.count() == 0
    error.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "error"])
def test_main_window_shutdown_after_video_native_finish_suppresses_queued_modal(parented_video, tmp_path, outcome):
    window, widget, settings, info, error = parented_video
    output = tmp_path / "parented.gif"
    output.write_bytes(b"original")
    entered, release, native_finished = Event(), Event(), Event()
    original = vt._apply_filter

    def render(image, key):
        entered.set()
        assert release.wait(10)
        if outcome == "error":
            raise OSError("queued video failure")
        return original(image, key)

    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
            patch.object(vt, "_apply_filter", render), \
            patch.object(widget, "request_export_cancel", wraps=widget.request_export_cancel) as cancel, \
            patch.object(settings, "sync", wraps=settings.sync) as sync:
        try:
            widget._export()
            worker = widget._export_worker
            getter = worker.clips[0]["frame_getter"]
            worker.finished.connect(native_finished.set, Qt.ConnectionType.DirectConnection)
            completion = []
            widget.export_finished.connect(lambda: completion.append(
                (widget.is_exporting(), worker.isRunning(), bool(getter._frames),
                 getattr(window, "_shutdown_complete", False))
            ))
            pump_until(entered.is_set)
            release.set()
            # Do not process GUI events: native encoding/publication and owned
            # source cleanup have won, but the GUI completion slot is queued.
            assert native_finished.wait(10)
            assert worker.outcome == outcome
            assert not getter._frames
            assert widget.is_exporting()
            assert not window.close()
            assert not window.close()
            cancel.assert_called_once()
            assert widget._close_after_export
            sync.assert_not_called()
            wait_for_video_export(widget)
        finally:
            release.set()
            if widget.is_exporting():
                widget.request_export_cancel()
                wait_for_video_export(widget)
        pump_until(lambda: getattr(window, "_shutdown_complete", False))
        assert completion == [(False, False, False, False)]
        assert not window.isVisible() and not widget.isVisible()
        info.assert_not_called()
        error.assert_not_called()
        sync.assert_called_once()
    assert not list(tmp_path.glob("*alpha_fixer*"))
    history = settings.get_video_builder_history()
    if outcome == "success":
        assert len(history) == 1 and history[0]["output"] == str(output)
        with Image.open(output) as result:
            assert result.size == (18, 20)
    else:
        assert history == []
        assert output.read_bytes() == b"original"


def test_main_window_waits_for_both_real_gif_and_video_workers(parented_video, tmp_path):
    from src.ui.gif_builder import _FrameEntry
    from tests.gif_export_helpers import wait_for_gif_export

    window, video, settings, info, error = parented_video
    window._open_or_focus_gif_builder()
    gif = window._gif_builder_dlg
    gif._frames = [_FrameEntry("blue.png", 0, Image.new("RGBA", (12, 12), "blue"), delay_ms=80)]
    gif._update_count()
    video_output, gif_output = tmp_path / "video.gif", tmp_path / "animation.gif"
    for output in (video_output, gif_output):
        output.write_bytes(b"original")
    video_entered, video_release = Event(), Event()
    gif_entered, gif_release = Event(), Event()
    original_save = Image.Image.save

    def save(image, *args, **kwargs):
        entered, release = ((video_entered, video_release) if image.size == (18, 20)
                            else (gif_entered, gif_release))
        entered.set()
        assert release.wait(10)
        return original_save(image, *args, **kwargs)

    with patch.object(vt.QFileDialog, "getSaveFileName",
                      side_effect=[(str(video_output), ""), (str(gif_output), "")]), \
            patch.object(Image.Image, "save", save), \
            patch.object(video, "request_export_cancel", wraps=video.request_export_cancel) as cancel_video, \
            patch.object(gif, "request_export_cancel", wraps=gif.request_export_cancel) as cancel_gif, \
            patch.object(settings, "sync", wraps=settings.sync) as sync:
        try:
            video._export()
            gif._export()
            video_worker, gif_worker = video._export_worker, gif._export_worker
            pump_until(lambda: video_entered.is_set() and gif_entered.is_set())
            assert not window.close()
            assert not window.close()
            assert set(window._builder_shutdown_pending) == {video, gif}
            cancel_video.assert_called_once()
            cancel_gif.assert_called_once()
            gif_release.set()
            wait_for_gif_export(gif)
            assert video.is_exporting() and video_worker.isRunning()
            assert sip.isdeleted(gif_worker) or not gif_worker.isRunning()
            assert window._builder_shutdown_pending == [video]
            assert window.isVisible()
            assert not getattr(window, "_shutdown_complete", False)
            sync.assert_not_called()
            video_release.set()
            wait_for_video_export(video)
            pump_until(lambda: getattr(window, "_shutdown_complete", False))
            assert not window.isVisible()
            assert not window._builder_shutdown_pending
            assert settings.get_video_builder_history() == []
            assert settings.get_gif_builder_history() == []
            assert video_output.read_bytes() == gif_output.read_bytes() == b"original"
            assert not list(tmp_path.glob("*alpha_fixer*"))
            info.assert_not_called()
            error.assert_not_called()
            sync.assert_called_once()
        finally:
            video_release.set()
            gif_release.set()
            if video.is_exporting():
                video.request_export_cancel()
                wait_for_video_export(video)
            if gif.is_exporting():
                gif.request_export_cancel()
                wait_for_gif_export(gif)
