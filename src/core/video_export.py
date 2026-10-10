"""Widget-free Video Builder rendering and transactional publication."""
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import time
from threading import Event, Lock

from PyQt6.QtCore import QThread, pyqtSignal


class ExportCanceled(Exception):
    pass


def source_frame_index(clip, output_index):
    start = int(clip["trim_start"])
    base = int(clip["base_active_frames"])
    if clip["clip_type"] == "image" or base <= 0:
        return start
    speed = max(0.1, int(clip["speed_percent"]) / 100.0)
    return start + max(0, min(base - 1, int(output_index * speed)))


def source_frame_indices(clip):
    """Yield only distinct source indices used by the frozen render mapping."""
    if clip["clip_type"] == "image":
        if int(clip["active_frames"]) > 0:
            yield int(clip["trim_start"])
        return
    previous = None
    for output_index in range(int(clip["active_frames"])):
        index = source_frame_index(clip, output_index)
        if index != previous:
            yield index
            previous = index


def run_mux_process(command, checkpoint):
    """Drain FFmpeg's pipes while allowing cancellation and bounded teardown."""
    checkpoint()
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 180
    try:
        while True:
            checkpoint()
            if time.monotonic() >= deadline:
                raise TimeoutError("FFmpeg audio mux timed out.")
            try:
                _, stderr = process.communicate(timeout=0.1)
                return subprocess.CompletedProcess(command, process.returncode, "", stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def mux_mp4_audio(silent_video_path, out_path, clips, output_fps, volume=1.0,
                  checkpoint=lambda: None):
    from src.ui.video_tool import _get_ffmpeg_exe, _build_atempo_filters

    executable = _get_ffmpeg_exe()
    if not executable:
        raise RuntimeError("FFmpeg is unavailable for MP4 audio export.")
    needs_silence = any(int(c["active_frames"]) > 0 and not (
        c["clip_type"] == "video" and c["has_audio"]) for c in clips)

    def build_command(normalize_audio):
        command = [executable, "-y", "-v", "error", "-i", silent_video_path]
        filters, inputs = [], []
        input_index = 1
        silence_index = None
        if needs_silence:
            command.extend(["-f", "lavfi", "-i",
                            "anullsrc=channel_layout=stereo:sample_rate=48000"])
            silence_index = input_index
            input_index += 1
        normalize = [
            "aresample=async=1:first_pts=0:min_hard_comp=0.100",
        ] if normalize_audio else []
        normalize.append(
            "aformat=sample_rates=48000:channel_layouts=stereo:sample_fmts=fltp"
        )
        for index, clip in enumerate(clips):
            if int(clip["active_frames"]) <= 0:
                continue
            duration = float(clip["timeline_seconds"])
            label = f"a{index}"
            if clip["clip_type"] == "video" and clip["has_audio"]:
                command.extend(["-i", str(clip["path"])])
                start = int(clip["trim_start"]) / max(0.1, float(clip["clip_fps"]))
                end = (int(clip["trim_end"]) + 1) / max(0.1, float(clip["clip_fps"]))
                tempo = max(0.01, max(0.001, end - start) / max(0.001, duration))
                chain = [
                    f"[{input_index}:a]apad=whole_dur={end:.6f}",
                    f"atrim=start={start:.6f}:end={end:.6f}",
                    "asetpts=PTS-STARTPTS", *_build_atempo_filters(tempo), *normalize,
                ]
                input_index += 1
            else:
                chain = [f"[{silence_index}:a]atrim=start=0:end={duration:.6f}",
                         "asetpts=PTS-STARTPTS", *normalize]
            # Audio can end before its video trim. Pad each segment, not just
            # the final mix, so concat cannot pull subsequent clips forward.
            chain.extend([f"apad=whole_dur={duration:.6f}",
                          f"atrim=end={duration:.6f}", "asetpts=PTS-STARTPTS"])
            filters.append(",".join(chain) + f"[{label}]")
            inputs.append(f"[{label}]")
        if not inputs:
            raise RuntimeError("No audio segments were available for MP4 export.")
        filters.append("".join(inputs) + f"concat=n={len(inputs)}:v=0:a=1[a_concat]")
        label = "[a_concat]"
        if abs(volume - 1.0) > 0.0001:
            filters.append(f"{label}volume={max(0.0, volume):.3f}[a_out]")
            label = "[a_out]"
        if normalize_audio:
            filters.append(
                f"{label}aresample=async=1:first_pts=0:min_hard_comp=0.100,"
                "aformat=sample_rates=48000:channel_layouts=stereo:sample_fmts=fltp[a_norm]"
            )
            label = "[a_norm]"
        duration = sum(max(0.0, float(c["timeline_seconds"])) for c in clips
                       if int(c["active_frames"]) > 0)
        if duration > 0:
            filters.append(f"{label}apad=whole_dur={duration:.6f},atrim=end={duration:.6f}[a_final]")
            label = "[a_final]"
        command.extend(["-filter_complex", ";".join(filters), "-map", "0:v:0",
                        "-map", label, "-c:v", "copy", "-c:a", "aac", out_path])
        return command

    first = run_mux_process(build_command(False), checkpoint)
    checkpoint()
    if first.returncode == 0:
        return []
    first_error = first.stderr.strip() or "FFmpeg audio mux failed."
    second = run_mux_process(build_command(True), checkpoint)
    checkpoint()
    if second.returncode == 0:
        return ["audio-mux-retry=normalized", f"audio-mux-first-error={first_error}"]
    error = second.stderr.strip() or "FFmpeg normalized audio mux failed."
    raise RuntimeError(f"{first_error} | normalized retry failed: {error}")


@dataclass(frozen=True)
class VideoExportSettings:
    output: str
    format: str
    fps: float
    canvas_size: tuple[int, int]
    filter: str
    adjustments: dict
    audio: bool
    volume: float


class VideoExportWorker(QThread):
    progress = pyqtSignal(int, str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.clips = []
        self.outcome = ""
        self.error = ""
        self.stage = "source snapshot"
        self.notes = []
        self.issue_count = 0
        self.audio_mode = "kept" if settings.audio else "off"
        self.completion_note = ""
        self._cancel = Event()
        self._publish_lock = Lock()
        self._published = False

    def cancel(self):
        if not self._publish_lock.acquire(blocking=False):
            return False
        try:
            if self._published:
                return False
            self._cancel.set()
            return True
        finally:
            self._publish_lock.release()

    def checkpoint(self):
        if self._cancel.is_set():
            raise ExportCanceled()

    def run(self):
        from src.ui.video_tool import (
            _apply_adjustments, _apply_filter, _fit_frame_to_canvas,
            _audio_source_plan, _audio_source_plan_history_notes, _VideoFrameGetter,
        )
        from src.ui._ui_utils import staged_output_path

        settings = self.settings
        gif_frames = []
        writer = None
        silent_path = None
        staged_path = None
        locked = False
        try:
            self.checkpoint()
            if self.error:
                raise RuntimeError(self.error)
            if settings.format == "mp4":
                self.notes.extend(_audio_source_plan_history_notes(_audio_source_plan(self.clips)))
            with staged_output_path(settings.output) as staged_path:
                render_complete = False
                try:
                    render_path = staged_path
                    if settings.format == "mp4":
                        import imageio
                        import numpy as np
                        if settings.audio:
                            with tempfile.NamedTemporaryFile(
                                prefix="alpha_fixer_video_", suffix=".mp4",
                                dir=str(Path(settings.output).absolute().parent), delete=False,
                            ) as file:
                                silent_path = file.name
                            render_path = silent_path
                        self.stage = "MP4 writer setup"
                        writer = imageio.get_writer(
                            render_path, format="FFMPEG", fps=settings.fps,
                            codec="libx264", pixelformat="yuv420p", macro_block_size=1,
                        )
                    total = sum(int(c["active_frames"]) for c in self.clips)
                    index = 0
                    self.stage = "frame rendering"
                    for clip in self.clips:
                        for offset in range(int(clip["active_frames"])):
                            self.checkpoint()
                            self.progress.emit(index, f"Rendering… frame {index + 1} / {total}")
                            source = adjusted = filtered = framed = rgb = None
                            try:
                                source = clip["frame_getter"](source_frame_index(clip, offset))
                                adjusted = _apply_adjustments(source, **settings.adjustments)
                                filtered = _apply_filter(adjusted, settings.filter)
                                framed = _fit_frame_to_canvas(filtered, settings.canvas_size, settings.format)
                                self.checkpoint()
                                if settings.format == "gif":
                                    gif_frames.append(framed)
                                    framed = None
                                else:
                                    rgb = framed.convert("RGB")
                                    writer.append_data(np.array(rgb))
                                self.checkpoint()
                            finally:
                                seen = set()
                                for image in (rgb, framed, filtered, adjusted, source):
                                    if image is not None and id(image) not in seen:
                                        seen.add(id(image))
                                        image.close()
                            index += 1
                        # Do not retain one decoder and frame cache per finished
                        # clip throughout a long multi-clip export.
                        if isinstance(clip["frame_getter"], _VideoFrameGetter):
                            clip["frame_getter"]._close_reader()
                    render_complete = True
                finally:
                    # Windows cannot remove an FFmpeg stage while its writer
                    # holds the output open. Close before the staging context
                    # unwinds, without replacing an existing render failure.
                    if writer is not None:
                        closing_writer, writer = writer, None
                        if render_complete:
                            self.stage = "MP4 finalization"
                            self.progress.emit(
                                total, "Finalizing MP4… Cancel will discard the encoded result.",
                            )
                        try:
                            closing_writer.close()
                        except Exception:
                            if render_complete:
                                raise
                self.checkpoint()
                if settings.format == "gif":
                    self.stage = "GIF assembly"
                    self.progress.emit(total, "Saving GIF… Cancel will discard the encoded result.")
                    gif_frames[0].save(
                        staged_path, format="GIF", save_all=True, append_images=gif_frames[1:],
                        duration=max(1, int(round(1000.0 / settings.fps))), loop=0, disposal=2,
                    )
                elif settings.audio:
                    self.stage = "audio muxing"
                    self.progress.emit(total, "Mixing source audio into MP4…")
                    try:
                        self.notes.extend(mux_mp4_audio(
                            silent_path, staged_path, self.clips, settings.fps,
                            settings.volume, self.checkpoint,
                        ) or [])
                        if "audio-mux-retry=normalized" in self.notes:
                            self.completion_note = "Saved after retrying MP4 audio muxing with normalized stereo/48 kHz audio."
                    except ExportCanceled:
                        raise
                    except Exception as exc:
                        self.checkpoint()
                        if not Path(silent_path).is_file() or Path(silent_path).stat().st_size <= 0:
                            raise
                        Path(silent_path).replace(staged_path)
                        self.issue_count = 1
                        self.audio_mode = "off (mux failed)"
                        self.notes.extend(["audio-mux-fallback=silent", f"audio-mux-error={exc}"])
                        self.completion_note = "Saved as a silent MP4 because source-audio muxing failed after video rendering."
                self.checkpoint()
                self.stage = "publication"
                self._publish_lock.acquire()
                locked = True
                self.checkpoint()
            self._published = True
            self.outcome = "success"
        except ExportCanceled:
            self.outcome = "canceled"
        except Exception as exc:
            self.outcome = "canceled" if self._cancel.is_set() else "error"
            self.error = str(exc)
        finally:
            if locked:
                self._publish_lock.release()
            if staged_path is not None:
                try:
                    Path(staged_path).unlink(missing_ok=True)
                except OSError:
                    pass
            if silent_path is not None:
                try:
                    Path(silent_path).unlink(missing_ok=True)
                except OSError:
                    pass
            for frame in gif_frames:
                frame.close()
            for clip in self.clips:
                try:
                    clip["frame_getter"]._close_reader()
                except Exception:
                    pass
