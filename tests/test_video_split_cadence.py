from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication

from src.core.video_export import source_frame_index
from src.core import video_export
from src.ui import video_tool as vt
from tests.test_video_audio_sync import ffmpeg, run_ffmpeg, decoded_audio, tone_windows
from tests.video_export_helpers import wait_for_video_export


@pytest.fixture
def video():
    app = QApplication.instance() or QApplication([])
    widget = vt.VideoToolDialog()
    widget._record_export_history = Mock()
    with patch.object(vt.QMessageBox, "information"), patch.object(vt.QMessageBox, "critical"):
        yield widget
        if widget.is_exporting():
            widget.request_export_cancel()
            wait_for_video_export(widget)
        widget.close()
        sip.delete(widget)
        app.processEvents()


def install_clip(video, speed, start=2, end=11):
    def frame(index):
        return Image.new("RGBA", (16, 16), (index * 20, 30, 40, 255))

    def load(*args, **kwargs):
        return vt._ClipEntry("generated.video", 12, frame, frame_size=(16, 16), has_audio=True)

    clip = load()
    clip.trim_start, clip.trim_end, clip.speed_percent = start, end, speed
    video._insert_clip(clip, "generated")
    video._clip_list.setCurrentRow(0)
    video._update_scrubber()
    video._update_ui_state()
    return clip, load


def mapping(video):
    return [
        clip.trim_start + clip.output_index_to_source_offset(index)
        for clip in video._clips for index in range(clip.active_frames)
    ]


@pytest.mark.parametrize("speed,playhead", [
    (50, 0), (67, 0), (75, 1), (125, 1), (150, 1), (175, 1),
    (200, 1), (250, 0), (300, 0), (333, 0),
])
def test_split_preserves_trimmed_speed_cadence_preview_and_snapshot(video, speed, playhead):
    clip, load = install_clip(video, speed)
    expected = mapping(video)
    original = video._snapshot_clip_render_state(clip, 25)
    video._scrubber.setValue(playhead)
    with patch.object(vt, "_load_video_clip", side_effect=load):
        video._split_clip_at_playhead()
    assert len(video._clips) == 2
    assert mapping(video) == expected
    assert video._total_preview_frames() == len(expected)
    assert [c.speed_percent for c in video._clips] == [speed, speed]
    assert video._clips[0].trim_start == 2
    assert video._clips[-1].trim_end == 11
    snapshots = [video._snapshot_clip_render_state(c, 25) for c in video._clips]
    assert sum(s["timeline_seconds"] for s in snapshots) == pytest.approx(len(expected) / 25)
    assert sum(s["source_duration_seconds"] for s in snapshots) == pytest.approx(10 / 25)
    assert snapshots[0]["audio_start_seconds"] == original["audio_start_seconds"]
    assert snapshots[-1]["audio_end_seconds"] == original["audio_end_seconds"]
    assert snapshots[0]["audio_end_seconds"] == snapshots[1]["audio_start_seconds"]
    for snapshot in snapshots:
        assert snapshot["audio_tempo"] == original["audio_tempo"]
        assert snapshot["audio_end_seconds"] - snapshot["audio_start_seconds"] == pytest.approx(
            snapshot["timeline_seconds"] * original["audio_tempo"],
        )
    assert [
        source_frame_index(s, i) for s in snapshots for i in range(s["active_frames"])
    ] == expected
    for index, source in enumerate(expected):
        video._scrubber.setValue(index)
        video._update_preview()
        assert video._preview_lbl.pixmap().toImage().pixelColor(0, 0).red() == source * 20
    clip.trim_end = clip.trim_start
    clip.speed_percent = 100
    assert [
        source_frame_index(s, i) for s in snapshots for i in range(s["active_frames"])
    ] == expected


def test_nested_split_selection_reorder_and_timing_edits(video):
    clip, load = install_clip(video, 125)
    expected = mapping(video)
    with patch.object(vt, "_load_video_clip", side_effect=load):
        video._scrubber.setValue(1)
        video._split_clip_at_playhead()
        video._scrubber.setValue(4)
        video._split_clip_at_playhead()
    assert mapping(video) == expected
    states = [(c.trim_start, c.trim_end, c.speed_percent, c.active_frames) for c in video._clips]
    for row in (2, 0, 1, 2):
        video._clip_list.setCurrentRow(row)
        assert video._trim_start_slider.value() == video._clips[row].trim_start
    assert [(c.trim_start, c.trim_end, c.speed_percent, c.active_frames) for c in video._clips] == states
    last = video._clips[-1]
    last_indices = [last.trim_start + last.output_index_to_source_offset(i) for i in range(last.active_frames)]
    item = video._clip_list.takeItem(2)
    video._clip_list.insertItem(0, item)
    video._sync_clips_from_list()
    video._clip_list.setCurrentRow(0)
    assert video._clips[0] is last
    assert mapping(video)[:last.active_frames] == last_indices
    video._clip_speed_slider.setValue(200)
    assert last.active_frames == max(1, round(last.base_active_frames / 2))
    assert last.output_index_to_source_offset(0) == 0
    video._trim_start_slider.setValue(last.trim_start + 1)
    assert last.output_index_to_source_offset(0) == 0
    snapshot = video._snapshot_clip_render_state(last, 25)
    assert snapshot["active_frames"] == last.active_frames
    assert source_frame_index(snapshot, 0) == last.trim_start
    assert snapshot["audio_start_seconds"] == last.trim_start / 25
    assert snapshot["audio_end_seconds"] == (last.trim_end + 1) / 25
    video._trim_end_slider.setValue(last.trim_start)
    assert last.active_frames == 1
    assert video._snapshot_clip_render_state(last, 25)["active_frames"] == 1


@pytest.mark.parametrize("speed", [50, 125, 200])
def test_split_at_last_sample_leaves_timeline_unchanged(video, speed):
    clip, load = install_clip(video, speed)
    expected = mapping(video)
    video._scrubber.setValue(clip.active_frames - 1)
    with patch.object(vt, "_load_video_clip", side_effect=load) as reload:
        video._split_clip_at_playhead()
    reload.assert_not_called()
    assert video._clips == [clip]
    assert mapping(video) == expected


def test_failed_split_reload_leaves_trim_and_cadence_unchanged(video):
    clip, load = install_clip(video, 125)
    expected = mapping(video)
    with patch.object(vt, "_load_video_clip", return_value=None), \
            patch.object(vt.QMessageBox, "warning") as warning:
        video._split_clip_at_playhead()
    warning.assert_called_once()
    assert video._clips == [clip]
    assert (clip.trim_start, clip.trim_end) == (2, 11)
    assert mapping(video) == expected


def test_reloading_split_clip_preserves_sampling_phase(video):
    clip, load = install_clip(video, 125)
    video._scrubber.setValue(1)
    with patch.object(vt, "_load_video_clip", side_effect=load):
        video._split_clip_at_playhead()
        second = video._clips[1]
        reloaded = video._reload_clip(second)
    assert reloaded.active_frames == second.active_frames
    assert [
        reloaded.trim_start + reloaded.output_index_to_source_offset(i)
        for i in range(reloaded.active_frames)
    ] == [
        second.trim_start + second.output_index_to_source_offset(i)
        for i in range(second.active_frames)
    ]
    reloaded.close()


@pytest.mark.parametrize("output_fps", [20, 25, 30])
def test_nine_frame_nested_splits_preserve_original_audio_mapping(video, output_fps):
    clip, load = install_clip(video, 125, end=10)
    clip.has_audio = True
    original = video._snapshot_clip_render_state(clip, output_fps)
    with patch.object(vt, "_load_video_clip", side_effect=load):
        video._scrubber.setValue(1)
        video._split_clip_at_playhead()
        assert [c.active_frames for c in video._clips] == [2, 5]
        video._scrubber.setValue(4)
        video._split_clip_at_playhead()
    snapshots = [video._snapshot_clip_render_state(c, output_fps) for c in video._clips]
    assert [s["active_frames"] for s in snapshots] == [2, 3, 2]
    boundaries = [2 / 25 + position * 9 / (7 * 25) for position in (0, 2, 5, 7)]
    for index, snapshot in enumerate(snapshots):
        assert snapshot["audio_start_seconds"] == pytest.approx(boundaries[index])
        assert snapshot["audio_end_seconds"] == pytest.approx(boundaries[index + 1])
        assert snapshot["audio_tempo"] == original["audio_tempo"]
    with patch.object(vt, "_get_ffmpeg_exe", return_value="ffmpeg"), \
            patch.object(video_export, "run_mux_process") as mux:
        mux.return_value.returncode = 0
        video_export.mux_mp4_audio("silent.mp4", "output.mp4", snapshots, output_fps)
    graph = mux.call_args.args[0][mux.call_args.args[0].index("-filter_complex") + 1]
    for snapshot in snapshots:
        assert f"atrim=start={snapshot['audio_start_seconds']:.6f}:end={snapshot['audio_end_seconds']:.6f}" in graph
    assert graph.count(vt._build_atempo_filters(original["audio_tempo"])[0]) == 3
    video._clip_list.setCurrentRow(1)
    with patch.object(vt, "_load_video_clip", side_effect=load):
        reloaded = video._reload_clip(video._clips[1])
    assert video._snapshot_clip_render_state(reloaded, output_fps)["audio_tempo"] == original["audio_tempo"]
    reloaded.close()
    video._clip_speed_slider.setValue(200)
    edited = video._snapshot_clip_render_state(video._clips[1], output_fps)
    assert edited["audio_start_seconds"] == edited["trim_start"] / 25
    assert edited["audio_end_seconds"] == (edited["trim_end"] + 1) / 25
    assert snapshots[1]["audio_start_seconds"] == pytest.approx(boundaries[1])


def test_generated_nested_split_audio_keeps_tone_timing(video, ffmpeg, tmp_path):
    import numpy as np

    source, silent, output = [tmp_path / name for name in ("source.mkv", "silent.mp4", "split.mp4")]
    run_ffmpeg(
        ffmpeg, "-f", "lavfi", "-i", "color=s=16x16:r=25:d=9.16",
        "-f", "lavfi", "-i",
        "aevalsrc=0.12*sin(2*PI*if(lt(t\\,3)\\,440\\,880)*t):s=48000:d=9.16",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", source,
    )

    def load(*args, **kwargs):
        return vt._ClipEntry(str(source), 229, lambda index: Image.new("RGBA", (16, 16)),
                             fps=25, frame_size=(16, 16), has_audio=True)

    clip = load()
    clip.speed_percent = 125
    video._insert_clip(clip, "generated audio")
    video._clip_list.setCurrentRow(0)
    video._update_scrubber()
    with patch.object(vt, "_load_video_clip", side_effect=load):
        video._scrubber.setValue(29)
        video._split_clip_at_playhead()
        video._scrubber.setValue(99)
        video._split_clip_at_playhead()
    snapshots = [video._snapshot_clip_render_state(c, 25) for c in video._clips]
    assert [s["active_frames"] for s in snapshots] == [30, 70, 83]
    run_ffmpeg(ffmpeg, "-f", "lavfi", "-i", "color=s=16x16:r=25:d=7.32",
               "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", silent)
    with patch.object(vt, "_get_ffmpeg_exe", return_value=ffmpeg):
        assert video_export.mux_mp4_audio(str(silent), str(output), snapshots, 25) == []
    samples = decoded_audio(ffmpeg, output)
    assert abs(len(samples) / 48000 - 7.32) <= 1024 / 48000 + 0.001
    audible = np.flatnonzero(tone_windows(samples, 880) > 0.06)
    assert audible[0] * 0.01 == pytest.approx(3 * 183 / 229, abs=0.04)
    assert np.median(tone_windows(samples[:48000], 440)) > 0.06


@pytest.mark.parametrize("speed,playhead", [(125, 1), (200, 0)])
def test_generated_split_gif_export_preserves_pixels_and_duration(video, tmp_path, speed, playhead):
    clip, load = install_clip(video, speed, end=10)
    expected = mapping(video)
    video._fps_slider.setValue(25)
    video._scrubber.setValue(playhead)
    with patch.object(vt, "_load_video_clip", side_effect=load):
        video._split_clip_at_playhead()
    output = tmp_path / "split.gif"
    video._export_fmt_combo.setCurrentIndex(video._export_fmt_combo.findData("gif"))
    with patch.object(vt.QFileDialog, "getSaveFileName", return_value=(str(output), "")):
        video._export()
        wait_for_video_export(video)
    with Image.open(output) as result:
        assert result.n_frames == len(expected)
        duration = 0
        for index, source in enumerate(expected):
            result.seek(index)
            duration += result.info["duration"]
            assert result.convert("RGB").getpixel((0, 0)) == (source * 20, 30, 40)
        assert duration == len(expected) * 40
    video._record_export_history.assert_called_once()
