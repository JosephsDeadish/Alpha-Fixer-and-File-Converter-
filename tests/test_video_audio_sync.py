"""Decoded timing checks using generated media, not external sample assets."""
import subprocess
import shutil
from unittest.mock import patch

import numpy as np
import pytest

from src.core import video_export
from src.ui import video_tool as vt


@pytest.fixture(params=["imageio", "native"])
def ffmpeg(request):
    executable = vt._get_ffmpeg_exe() if request.param == "imageio" else shutil.which("ffmpeg")
    if not executable:
        pytest.skip("Generated audio-sync media requires FFmpeg")
    return executable


def run_ffmpeg(executable, *arguments):
    return subprocess.run(
        [executable, "-y", "-v", "error", *map(str, arguments)],
        capture_output=True, check=True, timeout=30,
    )


def segment(path, duration, start=0, end=19, kind="video", audio=True):
    return {
        "path": str(path), "clip_type": kind, "has_audio": audio,
        "active_frames": round(duration * 20), "timeline_seconds": duration,
        "trim_start": start, "trim_end": end, "clip_fps": 20.0,
    }


def decoded_audio(executable, path):
    result = run_ffmpeg(executable, "-i", path, "-map", "0:a:0",
                        "-f", "f32le", "-ac", 1, "-ar", 48000, "-")
    return np.frombuffer(result.stdout, dtype="<f4")


def tone_windows(samples, frequency):
    # Ten-millisecond windows distinguish the two tones from padding/silence.
    windows = samples[:len(samples) // 480 * 480].reshape(-1, 480)
    wave = np.exp(-2j * np.pi * frequency * np.arange(480) / 48000)
    return np.abs(windows @ wave) * 2 / 480


@pytest.mark.parametrize("start,end,duration,tone_end", [
    (5, 34, 3.0, 1.0),       # slow: 1.5 source seconds occupy 3 timeline seconds
    (5, 34, 1.5, 0.5),
    (5, 34, 0.75, 0.25),    # fast
    (20, 39, 1.0, 0.0),     # trim begins after the short audio stream ends
])
@pytest.mark.parametrize("normalized_retry", [False, True])
def test_generated_short_audio_does_not_pull_later_tone_forward(
        ffmpeg, tmp_path, start, end, duration, tone_end, normalized_retry):
    first = tmp_path / "short-mono-32k.mkv"
    second = tmp_path / "long-stereo-48k.mkv"
    silent = tmp_path / "silent.mp4"
    output = tmp_path / "aligned.mp4"
    run_ffmpeg(
        ffmpeg, "-f", "lavfi", "-i", "color=s=16x16:r=20:d=2",
        "-f", "lavfi", "-i",
        "aevalsrc=if(lt(t\\,0.25)\\,0\\,0.12*sin(2*PI*440*t)):s=32000:d=0.75",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", first,
    )
    run_ffmpeg(
        ffmpeg, "-f", "lavfi", "-i", "color=s=16x16:r=20:d=1",
        "-f", "lavfi", "-i",
        "aevalsrc=if(lt(t\\,0.3)\\,0\\,0.12*sin(2*PI*880*t)):s=48000:d=1",
        "-ac", 2, "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le", second,
    )
    clips = [
        segment(first, duration, start, end),
        segment("still.png", 0.4, kind="image", audio=False),
        segment("muted.mp4", 0.2, audio=False),
        segment(second, 0.4, 2, 17),  # 0.8 source seconds at 2x speed
    ]
    inactive = segment("inactive-missing.mp4", 9)
    inactive["active_frames"] = 0
    clips.insert(1, inactive)
    total = duration + 1.0
    run_ffmpeg(ffmpeg, "-f", "lavfi", "-i", f"color=s=16x16:r=20:d={total}",
               "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", silent)
    actual_run = video_export.run_mux_process
    commands = []

    def mux(command, checkpoint):
        commands.append(command)
        if normalized_retry and len(commands) == 1:
            return subprocess.CompletedProcess(command, 1, "", "forced timestamp retry")
        return actual_run(command, checkpoint)

    with patch.object(vt, "_get_ffmpeg_exe", return_value=ffmpeg), \
            patch.object(video_export, "run_mux_process", side_effect=mux):
        notes = video_export.mux_mp4_audio(str(silent), str(output), clips, 20, volume=0.5)
    assert len(commands) == (2 if normalized_retry else 1)
    assert bool(notes) == normalized_retry
    samples = decoded_audio(ffmpeg, output)
    # AAC may decode a final partial frame as a whole 1024-sample frame.
    assert abs(len(samples) / 48000 - total) <= 1024 / 48000 + 0.001
    later_tone = tone_windows(samples, 880)
    audible = np.flatnonzero(later_tone > 0.025)
    assert len(audible) > 10
    assert audible[0] * 0.01 == pytest.approx(duration + 0.7, abs=0.04)
    assert audible[-1] * 0.01 == pytest.approx(total - 0.01, abs=0.04)
    # Silence must fill the early stream's missing tail and both nonaudio clips.
    quiet_start = tone_end + 0.08
    quiet_end = duration + 0.6 - 0.04
    quiet = samples[round(quiet_start * 48000):round(quiet_end * 48000)]
    assert np.sqrt(np.mean(quiet ** 2)) < 0.002
    if tone_end:
        early_tone = tone_windows(samples, 440)
        early_audible = np.flatnonzero(early_tone > 0.025)
        assert early_audible[0] * 0.01 == pytest.approx(0.0, abs=0.04)
        assert early_audible[-1] * 0.01 == pytest.approx(tone_end - 0.01, abs=0.04)
        assert early_tone[5] > 0.025
        # Volume is applied once, independent of segment format and retry.
        assert 0.035 < np.median(early_tone[5:round(tone_end * 100) - 5]) < 0.07
    else:
        assert np.max(tone_windows(samples[:round(duration * 48000)], 440)) < 0.002
