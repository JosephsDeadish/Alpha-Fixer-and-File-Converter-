"""
Video Tool Dialog.

Provides a lightweight video editor that lets the user:
  • Add one or more video clips (via imageio + imageio-ffmpeg + ffmpeg)
  • Drag clips in the list to reorder them (no Up/Down buttons)
  • Trim clips with start/end sliders
  • Adjust white point, black point, brightness, contrast, saturation, sharpness
    – all via smooth drag-sliders with live value readouts
  • Apply visual filters: greyscale, sepia, invert, sharpen, blur, vignette, etc.
  • Preview with play/pause/rewind and a position scrubber
  • Export to MP4 (via imageio+ffmpeg) or animated GIF (via Pillow)
  • Keep or mute source audio for MP4 exports and adjust output volume

**Dependency note**: Full video I/O requires imageio, imageio-ffmpeg, and a
working ffmpeg executable, preferably bundled and otherwise from the system PATH.
If video dependencies are unavailable the dialog can still assemble still images and GIFs
into an animated GIF.

UX highlights (Round-90):
  • All numeric controls use drag-sliders – no arrow-button spinboxes.
  • Clip list is drag-to-reorder; clip data stays in sync via item UserRole.
  • Trim sliders auto-update when a clip is selected.
  • Live preview refreshes immediately on any slider change.

Opening the dialog:
  • Right-clicking anywhere on the main window → "Open Video Editor"
"""
from __future__ import annotations

import datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import Lock
from typing import Callable, Optional

from PyQt6.QtCore import (
    Qt, QTimer, QSize, pyqtSignal,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QKeySequence, QShortcut, QIcon,
    QDragEnterEvent, QDropEvent, QDragMoveEvent,
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QSlider,
    QCheckBox, QComboBox, QGroupBox, QGridLayout, QSpinBox,
    QMessageBox, QProgressDialog, QSplitter, QWidget, QApplication,
    QFrame, QPlainTextEdit, QScrollArea,
)

_VIDEO_EXTS = {
    # Common containers
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".3g2", ".ts", ".m2ts",
    ".mts", ".vob", ".ogv", ".ogg", ".rm", ".rmvb", ".divx",
    ".asf", ".f4v", ".mxf", ".dv", ".yuv",
    # PlayStation / handheld console video formats (decoded via ffmpeg)
    ".pmf",    # PSP Movie Format (MPEG-2 based)
    ".pss",    # PlayStation 2 streaming video
    ".str",    # PlayStation 1/2 streaming video
    ".xa",     # PlayStation 1 audio/video
    # Disc images / cue sheets — experimental: ffmpeg can read video streams from these
    # when the image contains a demuxable video track (e.g. PSP UMD .iso
    # with MPEG inside).  Raw sector-level disc images may not load.
    ".cue",    # Cue sheet companion for BIN-style disc images
    ".iso",    # ISO 9660 disc image (PSP UMD / PS2 DVD)
    ".umd",    # PSP UMD disc image (same structure as ISO 9660)
    ".bin",    # CD-ROM disc image (may contain MPEG video sectors)
}
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif",
    ".gif", ".dds", ".tga", ".ico", ".ppm", ".pgm", ".pbm", ".pnm",
    ".pcx", ".avif", ".qoi", ".svg", ".jp2", ".j2k", ".j2c",
    ".jfif", ".jpe", ".xnb", ".tim",
}
_KNOWN_MEDIA_SUFFIXES = _VIDEO_EXTS | _IMAGE_EXTS | {".gif", ".mp4"}
_EXPERIMENTAL_DISC_VIDEO_EXTS = {".cue", ".iso", ".umd", ".bin"}
_ODD_CONTAINER_RECOVERY_EXTS = _EXPERIMENTAL_DISC_VIDEO_EXTS | {
    ".asf", ".divx", ".dv", ".flv", ".mov", ".rm", ".rmvb", ".ts", ".vob", ".yuv",
}

_FFPROBE_DEEP_ANALYSIS_ARGS = [
    "-probesize",
    "100M",
    "-analyzeduration",
    "100M",
]

_FFMPEG_DEEP_ANALYSIS_ARGS = [
    "-probesize",
    "100M",
    "-analyzeduration",
    "100M",
]
_SEGMENTED_VIDEO_NAME_RE = re.compile(
    r"(?is)^(?P<base>.+?)(?:[._ -]?)(?P<label>part|pt|cd|disc|disk|segment|seg)(?:[._ -]?)(?P<index>\d+)$"
)
_CUE_FILE_RE = re.compile(r'^\s*FILE\s+(?:"(?P<quoted>[^"]+)"|(?P<plain>\S+))\s+\S+', re.IGNORECASE)
_PROBE_FIELD_RE = re.compile(r"(?i)(?:^|[;\\n])\s*(container|video|audio|preferred-stream|preferred-audio-stream|video-streams|audio-streams)=([^;.\n]+)")
_MAX_VIDEO_LOAD_FAILURE_DETAILS = 3

_PREVIEW_MAX_W = 420
_PREVIEW_MAX_H = 320
_CLIP_ROLE = Qt.ItemDataRole.UserRole  # stores _ClipEntry in list item
_KEEP_STREAM_SELECTION = object()


def _gif_frame_rect(gif, frame_img) -> tuple[int, int, int, int]:
    """Return the logical update rectangle for the current GIF frame."""
    rect = getattr(gif, "dispose_extent", None)
    if isinstance(rect, tuple) and len(rect) == 4:
        return rect
    tile = getattr(gif, "tile", None)
    if tile:
        candidate = tile[0][1]
        if isinstance(candidate, tuple) and len(candidate) == 4:
            return candidate
    return (0, 0, frame_img.width, frame_img.height)


@lru_cache(maxsize=1)
def _has_imageio() -> bool:
    """Return True when imageio is importable for video container I/O."""
    try:
        import imageio  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _has_imageio_ffmpeg() -> bool:
    """Return True when the imageio-ffmpeg plugin is importable."""
    try:
        import imageio_ffmpeg  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _configure_imageio_ffmpeg() -> Optional[str]:
    """Configure imageio to use the bundled/system ffmpeg executable once."""
    ffmpeg_exe = _get_ffmpeg_exe()
    if ffmpeg_exe:
        os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_exe)
    return ffmpeg_exe


@lru_cache(maxsize=1)
def _has_ffmpeg() -> bool:
    """Return True if a bundled or PATH ffmpeg executable is available."""
    return _get_ffmpeg_exe() is not None


def _get_ffmpeg_exe() -> Optional[str]:
    """Return the path to the ffmpeg executable, or None."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    try:
        import shutil
        return shutil.which("ffmpeg")
    except Exception:
        return None


def _get_ffprobe_exe() -> Optional[str]:
    """Return the path to ffprobe when available alongside ffmpeg or in PATH."""
    try:
        ffmpeg_exe = _get_ffmpeg_exe()
        if ffmpeg_exe:
            ffmpeg_path = Path(ffmpeg_exe)
            for candidate in (
                ffmpeg_path.with_name("ffprobe"),
                ffmpeg_path.with_name("ffprobe.exe"),
            ):
                if candidate.is_file():
                    return str(candidate)
    except Exception:
        pass
    try:
        import shutil
        return shutil.which("ffprobe")
    except Exception:
        return None


def _unlink_file_safely(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _segmented_video_group(path: str) -> Optional[tuple[str, int]]:
    candidate = Path(path)
    suffixes = [suffix.lower() for suffix in candidate.suffixes]
    if len(suffixes) >= 2 and suffixes[-1].startswith(".") and suffixes[-1][1:].isdigit():
        preceding_ext = suffixes[-2]
        if preceding_ext in _VIDEO_EXTS or preceding_ext in _ODD_CONTAINER_RECOVERY_EXTS:
            return str(candidate.with_suffix("")).lower(), int(suffixes[-1][1:])
    if candidate.suffix.lower() in _VIDEO_EXTS | _ODD_CONTAINER_RECOVERY_EXTS:
        match = _SEGMENTED_VIDEO_NAME_RE.match(candidate.stem)
        if match:
            base_name = match.group("base").rstrip(" ._-")
            if base_name:
                return str(candidate.with_name(base_name + candidate.suffix)).lower(), int(match.group("index"))
    return None


def _segmented_video_sources(path: str) -> list[str]:
    group = _segmented_video_group(path)
    candidate = Path(path)
    if group is None or not candidate.parent.is_dir():
        return []
    group_key, _ = group
    matches: list[tuple[int, str]] = []
    try:
        for sibling in candidate.parent.iterdir():
            if not sibling.is_file():
                continue
            sibling_group = _segmented_video_group(str(sibling))
            if sibling_group is None:
                continue
            sibling_key, order = sibling_group
            if sibling_key != group_key:
                continue
            matches.append((order, str(sibling)))
    except Exception:
        return []
    if len(matches) <= 1:
        return []
    matches.sort(key=lambda item: (item[0], item[1].lower()))
    deduped: list[str] = []
    seen_orders: set[int] = set()
    for order, sibling_path in matches:
        if order in seen_orders:
            continue
        seen_orders.add(order)
        deduped.append(sibling_path)
    if str(candidate) not in deduped:
        return []
    return deduped


def _is_segmented_video_source(path: str) -> bool:
    return len(_segmented_video_sources(path)) > 1


def _ffconcat_line(path: str) -> str:
    return "file '" + path.replace("\\", "/").replace("'", "'\\''") + "'\n"


def _concat_segmented_video_source(
    segment_paths: list[str],
    details: Optional[dict[str, object]] = None,
    *,
    include_audio: bool = True,
    transcode: bool = False,
) -> Optional[str]:
    ffmpeg_exe = _get_ffmpeg_exe()
    if not ffmpeg_exe or len(segment_paths) <= 1:
        return None
    manifest_file = tempfile.NamedTemporaryFile(
        prefix="alpha_fixer_video_concat_",
        suffix=".ffconcat",
        mode="w",
        encoding="utf-8",
        delete=False,
    )
    output_suffix = ".mp4" if transcode else ".mkv"
    output_file = tempfile.NamedTemporaryFile(
        prefix="alpha_fixer_video_joined_",
        suffix=output_suffix,
        delete=False,
    )
    manifest_path = manifest_file.name
    output_path = output_file.name
    output_file.close()
    try:
        manifest_file.write("ffconcat version 1.0\n")
        for segment_path in segment_paths:
            manifest_file.write(_ffconcat_line(segment_path))
        manifest_file.close()
        command = [
            ffmpeg_exe,
            "-y",
            "-v",
            "error",
            "-fflags",
            "+discardcorrupt",
            "-err_detect",
            "ignore_err",
            *_FFMPEG_DEEP_ANALYSIS_ARGS,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            manifest_path,
            *_ffmpeg_stream_maps_with_audio(details, include_audio=include_audio),
            "-dn",
            "-sn",
        ]
        if transcode:
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                ]
            )
            command.extend(["-an"] if not include_audio else ["-c:a", "aac", "-b:a", "160k"])
        else:
            command.extend(["-c", "copy"])
        command.append(output_path)
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=180,
        )
        if result.returncode == 0 and Path(output_path).is_file() and Path(output_path).stat().st_size > 0:
            return output_path
    except Exception:
        pass
    finally:
        try:
            manifest_file.close()
        except Exception:
            pass
        _unlink_file_safely(manifest_path)
    _unlink_file_safely(output_path)
    return None


def _parse_ffprobe_rate(value) -> float:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return 0.0
    if "/" in text:
        num_text, den_text = text.split("/", 1)
        try:
            num = float(num_text)
            den = float(den_text)
        except Exception:
            return 0.0
        return num / den if den > 0 else 0.0
    try:
        parsed = float(text)
    except Exception:
        return 0.0
    return parsed if parsed > 0 else 0.0


def _coerce_optional_stream_index(value) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _join_note_parts(*parts: str) -> str:
    cleaned = [str(part).strip() for part in parts if str(part or "").strip()]
    return "; ".join(cleaned)


def _cue_referenced_files(path: str) -> list[str]:
    cue_path = Path(path)
    results: list[str] = []
    seen: set[str] = set()
    try:
        with cue_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                match = _CUE_FILE_RE.match(raw_line)
                if not match:
                    continue
                rel_name = match.group("quoted") or match.group("plain") or ""
                if not rel_name:
                    continue
                candidate = cue_path.parent / rel_name
                try:
                    resolved = str(candidate.resolve())
                except Exception:
                    resolved = str(candidate)
                if resolved in seen or not Path(resolved).is_file():
                    continue
                seen.add(resolved)
                results.append(resolved)
    except Exception:
        return []
    return results


def _disc_sidecar_sources(path: str) -> list[str]:
    source = Path(path)
    ext = source.suffix.lower()
    candidates: list[str] = []
    seen: set[str] = {str(source)}

    def _add(candidate_path: Path) -> None:
        raw_candidate = str(candidate_path)
        if raw_candidate in seen or not candidate_path.is_file():
            return
        seen.add(raw_candidate)
        candidates.append(raw_candidate)

    if ext == ".cue":
        for referenced in _cue_referenced_files(path):
            _add(Path(referenced))
        for suffix in (".bin", ".img", ".iso", ".umd"):
            _add(source.with_suffix(suffix))
    elif ext in {".bin", ".img", ".iso", ".umd"}:
        _add(source.with_suffix(".cue"))
    return candidates


def _disc_sidecar_retry_note(original_path: str, alternate_path: str) -> str:
    alt_name = Path(alternate_path).name
    alt_ext = Path(alternate_path).suffix.lower()
    if alt_ext == ".cue":
        return f"cue sidecar retry active ({alt_name})"
    if Path(original_path).suffix.lower() == ".cue":
        return f"referenced disc data retry active ({alt_name})"
    return f"alternate disc source retry active ({alt_name})"


def _stream_flag(stream: dict[str, object], key: str) -> int:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return 0
    try:
        return 1 if int(disposition.get(key) or 0) else 0
    except Exception:
        return 0


def _stream_detail_payload(stream: dict[str, object]) -> dict[str, object]:
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        tags = {}
    try:
        width = max(0, int(stream.get("width") or 0))
        height = max(0, int(stream.get("height") or 0))
    except Exception:
        width = height = 0
    try:
        bit_rate = max(0.0, float(stream.get("bit_rate") or 0.0))
    except Exception:
        bit_rate = 0.0
    try:
        duration = max(0.0, float(stream.get("duration") or 0.0))
    except Exception:
        duration = 0.0
    return {
        "index": _coerce_optional_stream_index(stream.get("index")),
        "codec_type": str(stream.get("codec_type") or "").strip(),
        "codec_name": str(stream.get("codec_name") or "").strip(),
        "width": width,
        "height": height,
        "fps": max(
            _parse_ffprobe_rate(stream.get("avg_frame_rate")),
            _parse_ffprobe_rate(stream.get("r_frame_rate")),
        ),
        "bit_rate": bit_rate,
        "duration": duration,
        "attached_pic": bool(_stream_flag(stream, "attached_pic")),
        "language": str(tags.get("language") or "").strip(),
        "title": str(tags.get("title") or "").strip(),
    }


def _describe_stream_choice(stream_info: Optional[dict[str, object]]) -> str:
    if not isinstance(stream_info, dict):
        return ""
    parts = []
    index = _coerce_optional_stream_index(stream_info.get("index"))
    if index is not None:
        parts.append(f"stream #{index}")
    codec = str(stream_info.get("codec_name") or "").strip()
    if codec:
        parts.append(codec)
    width = max(0, int(stream_info.get("width") or 0))
    height = max(0, int(stream_info.get("height") or 0))
    if width > 0 and height > 0:
        parts.append(f"{width}×{height}")
    fps = max(0.0, float(stream_info.get("fps") or 0.0))
    if fps > 0:
        parts.append(f"{fps:.3f}".rstrip("0").rstrip(".") + " fps")
    language = str(stream_info.get("language") or "").strip()
    if language:
        parts.append(language)
    title = str(stream_info.get("title") or "").strip()
    if title:
        parts.append(title)
    if bool(stream_info.get("attached_pic")):
        parts.append("cover art")
    return " • ".join(parts)


def _stream_selection_note(
    details: Optional[dict[str, object]],
    *,
    manual: bool = False,
) -> str:
    if not details:
        return ""
    video_index = _coerce_optional_stream_index(details.get("video_stream_index"))
    video_count = max(0, int(details.get("video_stream_count") or 0))
    if video_index is None or (not manual and video_count <= 1):
        return ""
    choices = details.get("video_stream_choices")
    stream_info = None
    if isinstance(choices, list):
        stream_info = next(
            (
                choice for choice in choices
                if isinstance(choice, dict) and _coerce_optional_stream_index(choice.get("index")) == video_index
            ),
            None,
        )
    prefix = "manual stream" if manual else "preferred stream"
    summary = _describe_stream_choice(stream_info) or f"stream #{video_index}"
    if summary.startswith("stream #"):
        return f"{prefix} {summary[len('stream '):]}"
    return f"{prefix} #{video_index} • {summary}"


def _audio_stream_selection_note(
    details: Optional[dict[str, object]],
    *,
    manual: bool = False,
) -> str:
    if not details:
        return ""
    audio_index = _coerce_optional_stream_index(details.get("audio_stream_index"))
    audio_count = max(0, int(details.get("audio_stream_count") or 0))
    if audio_index is None or (not manual and audio_count <= 1):
        return ""
    choices = details.get("audio_stream_choices")
    stream_info = None
    if isinstance(choices, list):
        stream_info = next(
            (
                choice for choice in choices
                if isinstance(choice, dict) and _coerce_optional_stream_index(choice.get("index")) == audio_index
            ),
            None,
        )
    prefix = "manual audio" if manual else "preferred audio"
    summary = _describe_stream_choice(stream_info) or f"stream #{audio_index}"
    if summary.startswith("stream #"):
        return f"{prefix} {summary[len('stream '):]}"
    return f"{prefix} #{audio_index} • {summary}"


def _probe_media_details(
    path: str,
    preferred_video_stream_index: Optional[int] = None,
    preferred_audio_stream_index: Optional[int] = None,
) -> Optional[dict[str, object]]:
    ffprobe_exe = _get_ffprobe_exe()
    if not ffprobe_exe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe_exe,
                "-v", "error",
                *_FFPROBE_DEEP_ANALYSIS_ARGS,
                "-print_format", "json",
                "-show_entries",
                "format=format_name,duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate,duration,disposition=attached_pic:stream_tags=language,title",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except Exception:
        return None
    streams = payload.get("streams")
    if not isinstance(streams, list):
        streams = []
    format_info = payload.get("format")
    if not isinstance(format_info, dict):
        format_info = {}
    video_streams = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"]

    def _stream_index(stream: dict[str, object]) -> int:
        try:
            return int(stream.get("index"))
        except Exception:
            return 999999

    def _video_rank(stream: dict[str, object]) -> tuple[int, int, float, float, float, int]:
        try:
            width = max(0, int(stream.get("width") or 0))
            height = max(0, int(stream.get("height") or 0))
        except Exception:
            width = height = 0
        area = width * height
        fps = max(
            _parse_ffprobe_rate(stream.get("avg_frame_rate")),
            _parse_ffprobe_rate(stream.get("r_frame_rate")),
        )
        try:
            bit_rate = max(0.0, float(stream.get("bit_rate") or 0.0))
        except Exception:
            bit_rate = 0.0
        try:
            duration = max(0.0, float(stream.get("duration") or 0.0))
        except Exception:
            duration = 0.0
        live_video = 1 if area > 0 or fps > 0.5 else 0
        attached_pic = _stream_flag(stream, "attached_pic")
        return 1 - attached_pic, live_video, area, fps, bit_rate, duration - (_stream_index(stream) / 1_000_000.0)

    selected_video_index = _coerce_optional_stream_index(preferred_video_stream_index)
    selected_audio_index = _coerce_optional_stream_index(preferred_audio_stream_index)
    video_stream = next(
        (
            stream for stream in video_streams
            if _coerce_optional_stream_index(stream.get("index")) == selected_video_index
        ),
        None,
    ) if selected_video_index is not None else None
    if video_stream is None:
        video_stream = max(video_streams, key=_video_rank, default=None)
    audio_stream = next(
        (
            stream for stream in audio_streams
            if _coerce_optional_stream_index(stream.get("index")) == selected_audio_index
        ),
        None,
    ) if selected_audio_index is not None else None
    if audio_stream is None:
        audio_stream = audio_streams[0] if audio_streams else None
    fps = 0.0
    width = height = 0
    video_codec = ""
    video_stream_index: Optional[int] = None
    if isinstance(video_stream, dict):
        fps = max(
            _parse_ffprobe_rate(video_stream.get("avg_frame_rate")),
            _parse_ffprobe_rate(video_stream.get("r_frame_rate")),
        )
        try:
            width = max(0, int(video_stream.get("width") or 0))
            height = max(0, int(video_stream.get("height") or 0))
        except Exception:
            width = height = 0
        video_codec = str(video_stream.get("codec_name") or "").strip()
        try:
            video_stream_index = int(video_stream.get("index"))
        except Exception:
            video_stream_index = None
    video_attached_pic_count = sum(_stream_flag(stream, "attached_pic") for stream in video_streams)
    selected_video_attached_pic = _stream_flag(video_stream, "attached_pic") if isinstance(video_stream, dict) else 0
    stream_tags = video_stream.get("tags") if isinstance(video_stream, dict) else {}
    if not isinstance(stream_tags, dict):
        stream_tags = {}
    selected_video_language = str(stream_tags.get("language") or "").strip()
    selected_video_title = str(stream_tags.get("title") or "").strip()
    audio_codec = ""
    audio_stream_index: Optional[int] = None
    if isinstance(audio_stream, dict):
        audio_codec = str(audio_stream.get("codec_name") or "").strip()
        try:
            audio_stream_index = int(audio_stream.get("index"))
        except Exception:
            audio_stream_index = None
    try:
        duration = float(format_info.get("duration") or 0.0)
    except Exception:
        duration = 0.0
    video_stream_choices = [_stream_detail_payload(stream) for stream in video_streams]
    audio_stream_choices = [_stream_detail_payload(stream) for stream in audio_streams]
    return {
        "format_name": str(format_info.get("format_name") or "").strip(),
        "duration": duration if duration > 0 else 0.0,
        "has_video": video_stream is not None,
        "has_audio": audio_stream is not None,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "width": width,
        "height": height,
        "fps": fps,
        "video_stream_index": video_stream_index,
        "audio_stream_index": audio_stream_index,
        "video_stream_count": len(video_streams),
        "audio_stream_count": len(audio_streams),
        "video_attached_pic_count": video_attached_pic_count,
        "selected_video_attached_pic": bool(selected_video_attached_pic),
        "selected_video_language": selected_video_language,
        "selected_video_title": selected_video_title,
        "video_stream_choices": video_stream_choices,
        "audio_stream_choices": audio_stream_choices,
    }


def _format_media_probe_summary(details: Optional[dict[str, object]]) -> str:
    if not details:
        return ""
    format_name = str(details.get("format_name") or "unknown")
    video_codec = str(details.get("video_codec") or "none")
    audio_codec = str(details.get("audio_codec") or "none")
    width = max(0, int(details.get("width") or 0))
    height = max(0, int(details.get("height") or 0))
    fps = max(0.0, float(details.get("fps") or 0.0))
    video_stream_count = max(0, int(details.get("video_stream_count") or 0))
    audio_stream_count = max(0, int(details.get("audio_stream_count") or 0))
    video_stream_index = details.get("video_stream_index")
    attached_pic_count = max(0, int(details.get("video_attached_pic_count") or 0))
    selected_language = str(details.get("selected_video_language") or "").strip()
    selected_title = str(details.get("selected_video_title") or "").strip()
    parts = [f"Probe: container={format_name}", f"video={video_codec}"]
    if width > 0 and height > 0:
        parts.append(f"size={width}x{height}")
    if fps > 0:
        parts.append(f"fps={fps:.3f}".rstrip("0").rstrip("."))
    parts.append(f"audio={audio_codec}")
    if video_stream_count > 1:
        choice = f" preferred-stream={video_stream_index}" if video_stream_index is not None else ""
        parts.append(f"video-streams={video_stream_count}{choice}")
    if audio_stream_count > 1:
        choice = f" preferred-audio-stream={audio_stream_index}" if audio_stream_index is not None else ""
        parts.append(f"audio-streams={audio_stream_count}{choice}")
    if attached_pic_count > 0:
        parts.append(f"attached-pic-streams={attached_pic_count}")
    if selected_language:
        parts.append(f"lang={selected_language}")
    if selected_title:
        parts.append(f"title={selected_title}")
    return "; ".join(parts) + "."


def _frame_dimensions(frame) -> Optional[tuple[int, int]]:
    """Best-effort width/height extraction for imageio/numpy/PIL-like frames."""
    shape = getattr(frame, "shape", None)
    if isinstance(shape, (tuple, list)) and len(shape) >= 2:
        try:
            height = int(shape[0])
            width = int(shape[1])
        except Exception:
            width = height = 0
        if width > 0 and height > 0:
            return width, height
    size = getattr(frame, "size", None)
    if isinstance(size, (tuple, list)) and len(size) >= 2:
        try:
            width = int(size[0])
            height = int(size[1])
        except Exception:
            width = height = 0
        if width > 0 and height > 0:
            return width, height
    if isinstance(frame, (list, tuple)) and frame:
        try:
            height = len(frame)
            first_row = frame[0]
            width = len(first_row) if isinstance(first_row, (list, tuple)) else 0
        except Exception:
            width = height = 0
        if width > 0 and height > 0:
            return width, height
    return None


def _video_io_diagnostics() -> str:
    """Return a human-readable summary of missing video I/O dependencies."""
    missing: list[str] = []
    if not _has_imageio():
        missing.append("imageio")
    if not _has_imageio_ffmpeg():
        missing.append("imageio-ffmpeg")
    ffmpeg_exe = _get_ffmpeg_exe()
    if not ffmpeg_exe:
        missing.append("ffmpeg executable")
    ffprobe_exe = _get_ffprobe_exe()
    summary = "All video dependencies are available."
    if missing:
        summary = "Missing: " + ", ".join(missing) + "."
    ffmpeg_text = ffmpeg_exe or "not found"
    ffprobe_text = ffprobe_exe or "not found"
    return f"{summary} ffmpeg: {ffmpeg_text}. ffprobe: {ffprobe_text}."


def _video_load_failure_hint(
    path: str,
    preferred_video_stream_index: Optional[int] = None,
    preferred_audio_stream_index: Optional[int] = None,
) -> str:
    ext = Path(path).suffix.lower()
    probe = _probe_media_details(
        path,
        preferred_video_stream_index=preferred_video_stream_index,
        preferred_audio_stream_index=preferred_audio_stream_index,
    )
    base = (
        "Ensure imageio-ffmpeg or a bundled/system ffmpeg binary is available,\n"
        "and check that the file is a supported, non-corrupt video."
    )
    lines = [base]
    if _is_segmented_video_source(path):
        segment_count = len(_segmented_video_sources(path))
        lines.append(
            f"This source looks like a segmented / multipart video set ({segment_count} parts detected); the app will also try an ffmpeg concat repair fallback before giving up."
        )
    disc_sidecars = _disc_sidecar_sources(path) if ext in _EXPERIMENTAL_DISC_VIDEO_EXTS else []
    if ext in _EXPERIMENTAL_DISC_VIDEO_EXTS:
        lines.append(
            "Disc-image video inputs are experimental and only work when ffmpeg can demux a playable stream from the image."
        )
        if disc_sidecars:
            preview = ", ".join(Path(candidate).name for candidate in disc_sidecars[:2])
            extra = len(disc_sidecars) - min(len(disc_sidecars), 2)
            if extra > 0:
                preview = f"{preview} +{extra} more"
            lines.append(
                f"Companion disc sidecar files were detected ({preview}); the app will also retry them when track-layout metadata may help expose a playable stream."
            )
        elif ext in {".bin", ".cue"}:
            lines.append(
                "Raw BIN/CUE disc images often need their matching cue/bin companion files to expose tracks correctly."
            )
        if probe and not bool(probe.get("has_video")):
            lines.append("ffprobe did not detect a playable video stream in this disc image.")
        elif probe and bool(probe.get("has_video")):
            lines.append(
                "Direct loading and ffmpeg recovery fallbacks still could not produce a playable clip from this disc image."
            )
        lines.append(
            "If direct loading fails, the app also tries temporary ffmpeg concat repair, remux, transcode, alternate-audio, audio-drop, and still-frame recovery fallbacks for compatible streams."
        )
    elif ext in _ODD_CONTAINER_RECOVERY_EXTS and probe and bool(probe.get("has_video")):
        lines.append(
            "This odd container reports a video stream, but direct loading and ffmpeg recovery fallbacks still could not produce a playable clip."
        )
        if int(probe.get("video_stream_count") or 0) > 1:
            lines.append(
                "Multiple video streams were detected; recovery will prefer the largest non-cover-art probe-detected video stream and retry alternate non-cover-art streams when needed."
            )
        if int(probe.get("audio_stream_count") or 0) > 1:
            lines.append(
                "Multiple audio streams were detected; recovery will also retry alternate audio tracks and audio-drop mode when broken source audio blocks import."
            )
        if bool(probe.get("selected_video_attached_pic")):
            lines.append("The currently selected stream looks like attached cover art instead of continuous video frames.")
    elif probe and bool(probe.get("has_audio")) and not bool(probe.get("has_video")):
        lines.append(
            "ffprobe detected audio but no playable video stream, so this file cannot be added to the Video Builder as a video clip."
        )
    elif probe and bool(probe.get("selected_video_attached_pic")):
        lines.append(
            "ffprobe only exposed an attached-picture/cover-art stream, so this source can only be imported when still-frame extraction succeeds."
        )
    elif probe and str(probe.get("format_name") or "").strip() and not bool(probe.get("has_video")):
        lines.append(
            "ffprobe recognized the container but did not expose a playable video stream for the Video Builder."
        )
    elif probe and bool(probe.get("has_video")) and not (
        int(probe.get("width") or 0) > 0 and int(probe.get("height") or 0) > 0
    ):
        lines.append(
            "ffprobe found a video stream but could not resolve stable frame dimensions; the container may be partial, malformed, or use an unsupported stream layout."
        )
    if preferred_video_stream_index is not None:
        selection_note = _stream_selection_note(probe, manual=True)
        if selection_note:
            lines.append(f"Manual selection active: {selection_note}.")
    if preferred_audio_stream_index is not None:
        audio_selection_note = _audio_stream_selection_note(probe, manual=True)
        if audio_selection_note:
            lines.append(f"Manual selection active: {audio_selection_note}.")
    lines.extend(_video_container_guidance(path, probe))
    lines.extend(_video_codec_guidance(path, probe))
    probe_summary = _format_media_probe_summary(probe)
    if probe_summary:
        lines.append(probe_summary)
    lines.append(_video_io_diagnostics())
    return "\n".join(lines)


def _video_container_guidance(path: str, details: Optional[dict[str, object]]) -> list[str]:
    if not details:
        return []
    ext = Path(path).suffix.lower()
    format_name = str(details.get("format_name") or "").lower()
    video_codec = str(details.get("video_codec") or "").lower()
    guidance: list[str] = []
    if ext == ".vob" or ("mpeg" in format_name and ext in {".vob", ".pss", ".str"}):
        guidance.append(
            "DVD/console-program streams like VOB/PSS/STR may carry multiple program streams or broken navigation/index data; remux or transcode recovery may still be required."
        )
    elif "mpegts" in format_name or ext in {".ts", ".m2ts", ".mts"}:
        guidance.append(
            "Transport-stream sources often contain discontinuities or missing timestamps; recovery may rebuild timing, but severe capture gaps can still prevent loading."
        )
    elif "asf" in format_name or ext in {".asf", ".wmv"}:
        guidance.append(
            "ASF/WMV containers rely heavily on index metadata; damaged indexes often need a full transcode instead of direct loading."
        )
    elif "matroska" in format_name or "webm" in format_name or ext in {".mkv", ".webm"}:
        guidance.append(
            "Matroska/WebM files can carry multiple alternate video/audio programs; if one stream fails, the builder will prefer the strongest detected video stream but manual stream reloads may still help."
        )
    elif "mov" in format_name or ext in {".mov", ".qt", ".m4v"}:
        guidance.append(
            "QuickTime/MOV-family files may depend on edit lists, timecode, or ProRes-style metadata; remux/transcode recovery is often needed when direct indexing is incomplete."
        )
    elif "avi" in format_name or ext in {".avi", ".divx"}:
        guidance.append(
            "AVI/DivX files often depend on legacy indexes; broken or missing index chunks can require a clean remux or transcode before timeline playback is reliable."
        )
    elif "mxf" in format_name or ext == ".mxf":
        guidance.append(
            "MXF containers can expose multiple essence streams and metadata tracks; alternate-stream retries or a clean editorial transcode may be required."
        )
    elif ext == ".dv" or video_codec in {"dvvideo", "dnxhd"}:
        guidance.append(
            "Broadcast/intermediate codecs like DV or DNxHD may arrive in wrappers with unusual fielding/timing metadata; remux or transcode recovery is often safer than direct decode."
        )
    elif ext in {".rm", ".rmvb"} or "rm" in format_name or "realmedia" in format_name:
        guidance.append(
            "RealMedia / RMVB support is best-effort; older RealMedia files often require a clean remux or transcode before frame-accurate loading will work."
        )
    if bool(details.get("selected_video_attached_pic")):
        guidance.append(
            "ffprobe selected an attached-picture/cover-art stream instead of a live video stream; this source may be audio-only metadata, or the builder may only be able to salvage it as a single still frame."
        )
    elif int(details.get("video_attached_pic_count") or 0) > 0:
        guidance.append(
            "Attached-picture/cover-art streams were also detected; recovery prefers a live video stream when possible, but some containers still need a manual remux to drop cover-art tracks."
        )
    if video_codec in {"mjpeg", "jpeg2000", "png"}:
        guidance.append(
            "Still-image or intra-frame-only video codecs can behave like cover-art or slideshow streams; a full transcode or single-frame fallback may be required before timeline playback is reliable."
        )
    elif video_codec in {"hevc", "h265", "av1"}:
        guidance.append(
            "Modern high-efficiency codecs may decode inconsistently in odd containers; if direct loading fails, a clean MP4 remux or H.264 transcode is usually the safest fallback."
        )
    return guidance


def _video_codec_guidance(path: str, details: Optional[dict[str, object]]) -> list[str]:
    if not details:
        return []
    ext = Path(path).suffix.lower()
    format_name = str(details.get("format_name") or "").lower()
    video_codec = str(details.get("video_codec") or "").lower()
    audio_codec = str(details.get("audio_codec") or "").lower()
    audio_stream_count = max(0, int(details.get("audio_stream_count") or 0))
    guidance: list[str] = []
    odd_container = ext in _ODD_CONTAINER_RECOVERY_EXTS or any(
        token in format_name for token in ("mpegts", "matroska", "asf", "realmedia", "avi", "mpeg")
    )
    if video_codec in {"hevc", "h265", "av1"}:
        guidance.append(
            "Detected HEVC/H.265 or AV1 video; when these codecs arrive in AVI/WMV/TS/odd wrappers, remuxing to MP4 or transcoding to H.264/AVC is usually the most reliable import path."
        )
    elif video_codec in {"prores", "dnxhd", "dvvideo"}:
        guidance.append(
            "Detected an intermediate/broadcast codec (ProRes/DNxHD/DV-style); odd wrappers or damaged timing metadata often need a clean editorial transcode before timeline playback is stable."
        )
    elif video_codec in {"mjpeg", "jpeg2000", "png"} and odd_container:
        guidance.append(
            "Detected a still-image style video codec inside a nonstandard container; the builder may only recover this as a slideshow/still-frame source unless ffmpeg can transcode it cleanly."
        )
    elif video_codec in {"mpeg1video", "mpeg2video"} and (ext in _EXPERIMENTAL_DISC_VIDEO_EXTS or ext in {".pss", ".str", ".vob"}):
        guidance.append(
            "Detected legacy MPEG program-stream video commonly used in PSP/PS1/PS2-era assets; alternate tracks, cue/bin metadata, or audio-drop recovery may be needed before the clip becomes playable."
        )
    elif video_codec in {"rv10", "rv20", "rv30", "rv40"}:
        guidance.append(
            "Detected a legacy RealVideo codec; these files often need a full ffmpeg transcode because direct indexing and partial-stream recovery are unreliable."
        )
    if audio_codec in {"ac3", "eac3", "dts", "truehd"}:
        if audio_stream_count > 1:
            guidance.append(
                "Selected audio uses AC3/DTS-style compressed audio and multiple audio tracks are present; if import or export fails, try another audio stream or reload with source audio dropped."
            )
        elif odd_container:
            guidance.append(
                "Source audio uses AC3/DTS-style compressed audio; if the video stream looks valid but recovery still fails, retrying without source audio is often the safest fallback."
            )
    elif audio_codec in {"mp2", "adx", "xa", "adpcm_xa", "adpcm_ima_wav"} and ext in _EXPERIMENTAL_DISC_VIDEO_EXTS | {".pss", ".str", ".vob"}:
        guidance.append(
            "Detected legacy disc-style audio alongside the video stream; if direct loading fails, recovery may need to drop or replace the original audio before the clip can be imported."
        )
    return guidance


def _ffmpeg_stream_maps(details: Optional[dict[str, object]]) -> list[str]:
    return _ffmpeg_stream_maps_with_audio(details, include_audio=True)


def _ffmpeg_stream_maps_with_audio(
    details: Optional[dict[str, object]],
    *,
    include_audio: bool,
) -> list[str]:
    maps: list[str] = []
    video_index = details.get("video_stream_index") if details else None
    audio_index = details.get("audio_stream_index") if details else None
    if video_index is None:
        maps.extend(["-map", "0:v:0"])
    else:
        maps.extend(["-map", f"0:{int(video_index)}"])
    if include_audio:
        if audio_index is None:
            maps.extend(["-map", "0:a?"])
        else:
            maps.extend(["-map", f"0:{int(audio_index)}?"])
    return maps


def _video_stream_choice_rank(stream_info: dict[str, object]) -> tuple[int, int, float, float, float, float]:
    width = max(0, int(stream_info.get("width") or 0))
    height = max(0, int(stream_info.get("height") or 0))
    fps = max(0.0, float(stream_info.get("fps") or 0.0))
    bit_rate = max(0.0, float(stream_info.get("bit_rate") or 0.0))
    duration = max(0.0, float(stream_info.get("duration") or 0.0))
    area = width * height
    attached_pic = 0 if bool(stream_info.get("attached_pic")) else 1
    live_video = 1 if area > 0 or fps > 0.5 else 0
    return attached_pic, live_video, area, fps, bit_rate, duration


def _audio_stream_choice_rank(stream_info: dict[str, object]) -> tuple[float, float, int]:
    bit_rate = max(0.0, float(stream_info.get("bit_rate") or 0.0))
    duration = max(0.0, float(stream_info.get("duration") or 0.0))
    index = _coerce_optional_stream_index(stream_info.get("index"))
    normalized_index = -(index if index is not None else 999999)
    return bit_rate, duration, normalized_index


def _recovery_probe_candidates(
    path: str,
    details: Optional[dict[str, object]],
    *,
    allow_alternate_streams: bool = True,
    allow_alternate_audio_streams: bool = True,
) -> list[dict[str, object]]:
    if not details:
        return []
    candidates = [details]
    selected_video_index = _coerce_optional_stream_index(details.get("video_stream_index"))
    selected_audio_index = _coerce_optional_stream_index(details.get("audio_stream_index"))
    seen_pairs = {(selected_video_index, selected_audio_index)}

    def _append_candidate(video_index: Optional[int], audio_index: Optional[int]) -> None:
        pair = (video_index, audio_index)
        if pair in seen_pairs:
            return
        seen_pairs.add(pair)
        candidate = _probe_media_details(
            path,
            preferred_video_stream_index=video_index,
            preferred_audio_stream_index=audio_index,
        )
        if candidate and bool(candidate.get("has_video")):
            candidates.append(candidate)

    if allow_alternate_audio_streams:
        audio_choices = details.get("audio_stream_choices")
        if isinstance(audio_choices, list) and len(audio_choices) > 1:
            alternates = [
                choice
                for choice in audio_choices
                if isinstance(choice, dict)
                and _coerce_optional_stream_index(choice.get("index")) is not None
                and _coerce_optional_stream_index(choice.get("index")) != selected_audio_index
            ]
            alternates.sort(key=_audio_stream_choice_rank, reverse=True)
            for choice in alternates:
                _append_candidate(selected_video_index, _coerce_optional_stream_index(choice.get("index")))

    if not allow_alternate_streams:
        return candidates
    choices = details.get("video_stream_choices")
    if not isinstance(choices, list) or len(choices) <= 1:
        return candidates
    alternates = [
        choice
        for choice in choices
        if isinstance(choice, dict)
        and _coerce_optional_stream_index(choice.get("index")) is not None
        and _coerce_optional_stream_index(choice.get("index")) != selected_video_index
        and not bool(choice.get("attached_pic"))
    ]
    alternates.sort(key=_video_stream_choice_rank, reverse=True)
    for choice in alternates:
        stream_index = _coerce_optional_stream_index(choice.get("index"))
        if stream_index is None:
            continue
        _append_candidate(stream_index, selected_audio_index)
        if allow_alternate_audio_streams:
            audio_choices = details.get("audio_stream_choices")
            if isinstance(audio_choices, list) and len(audio_choices) > 1:
                alternates = [
                    audio_choice
                    for audio_choice in audio_choices
                    if isinstance(audio_choice, dict)
                    and _coerce_optional_stream_index(audio_choice.get("index")) is not None
                    and _coerce_optional_stream_index(audio_choice.get("index")) != selected_audio_index
                ]
                alternates.sort(key=_audio_stream_choice_rank, reverse=True)
                for audio_choice in alternates:
                    _append_candidate(
                        stream_index,
                        _coerce_optional_stream_index(audio_choice.get("index")),
                    )
    return candidates


def _recovery_selection_note(
    details: Optional[dict[str, object]],
    *,
    primary_video_index: Optional[int] = None,
    primary_audio_index: Optional[int] = None,
) -> str:
    if not details:
        return ""
    note_parts: list[str] = []
    selected_video_index = _coerce_optional_stream_index(details.get("video_stream_index"))
    if selected_video_index is not None:
        video_stream_count = max(0, int(details.get("video_stream_count") or 0))
        if primary_video_index is not None and selected_video_index != primary_video_index:
            note_parts.append(f"alternate stream #{selected_video_index}")
        elif video_stream_count > 1:
            note_parts.append(f"preferred stream #{selected_video_index}")
    selected_audio_index = _coerce_optional_stream_index(details.get("audio_stream_index"))
    if selected_audio_index is not None:
        audio_stream_count = max(0, int(details.get("audio_stream_count") or 0))
        if primary_audio_index is not None and selected_audio_index != primary_audio_index:
            audio_note = _audio_stream_selection_note(details, manual=True)
            if audio_note.startswith("manual "):
                audio_note = "alternate " + audio_note[len("manual "):]
            note_parts.append(audio_note or f"alternate audio #{selected_audio_index}")
        elif audio_stream_count > 1:
            note_parts.append(_audio_stream_selection_note(details, manual=False) or f"preferred audio #{selected_audio_index}")
    return "; ".join(part for part in note_parts if part)


def _remux_video_source(
    path: str,
    details: Optional[dict[str, object]] = None,
    *,
    include_audio: bool = True,
) -> Optional[str]:
    ffmpeg_exe = _get_ffmpeg_exe()
    if not ffmpeg_exe:
        return None
    temp_file = tempfile.NamedTemporaryFile(
        prefix="alpha_fixer_video_src_",
        suffix=".mkv",
        delete=False,
    )
    remux_path = temp_file.name
    temp_file.close()
    try:
        result = subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-v",
                "error",
                "-fflags",
                "+discardcorrupt",
                "-err_detect",
                "ignore_err",
                *_FFMPEG_DEEP_ANALYSIS_ARGS,
                "-i",
                path,
                *_ffmpeg_stream_maps_with_audio(details, include_audio=include_audio),
                "-dn",
                "-sn",
                "-c",
                "copy",
                remux_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=180,
        )
    except Exception:
        _unlink_file_safely(remux_path)
        return None
    try:
        if result.returncode == 0 and Path(remux_path).is_file() and Path(remux_path).stat().st_size > 0:
            return remux_path
    except Exception:
        pass
    _unlink_file_safely(remux_path)
    return None


def _visual_still_fallback_note(details: Optional[dict[str, object]]) -> str:
    if details and bool(details.get("selected_video_attached_pic")):
        return "temporary cover-art still-frame fallback active"
    if details and int(details.get("video_attached_pic_count") or 0) > 0:
        return "temporary visual still-frame fallback active"
    video_codec = str(details.get("video_codec") or "").lower() if details else ""
    if video_codec in {"mjpeg", "jpeg2000", "png"}:
        return "temporary slideshow still-frame fallback active"
    return "temporary single-frame salvage fallback active"


def _extract_visual_still_frame(path: str, details: Optional[dict[str, object]] = None):
    ffmpeg_exe = _get_ffmpeg_exe()
    if not ffmpeg_exe:
        return None
    if details is not None and not bool(details.get("has_video")) and not bool(details.get("selected_video_attached_pic")):
        return None
    temp_file = tempfile.NamedTemporaryFile(
        prefix="alpha_fixer_video_still_",
        suffix=".png",
        delete=False,
    )
    still_path = temp_file.name
    temp_file.close()
    try:
        result = subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-v",
                "error",
                "-fflags",
                "+discardcorrupt",
                "-err_detect",
                "ignore_err",
                *_FFMPEG_DEEP_ANALYSIS_ARGS,
                "-i",
                path,
                *(_ffmpeg_stream_maps(details)[:2] if details else ["-map", "0:v:0"]),
                "-an",
                "-dn",
                "-sn",
                "-frames:v",
                "1",
                still_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=180,
        )
        if result.returncode != 0 or not Path(still_path).is_file() or Path(still_path).stat().st_size <= 0:
            return None
        from PIL import Image

        with Image.open(still_path) as still:
            return still.convert("RGBA")
    except Exception:
        return None
    finally:
        _unlink_file_safely(still_path)


def _transcode_video_source(
    path: str,
    details: Optional[dict[str, object]] = None,
    *,
    include_audio: bool = True,
) -> Optional[str]:
    ffmpeg_exe = _get_ffmpeg_exe()
    if not ffmpeg_exe:
        return None
    temp_file = tempfile.NamedTemporaryFile(
        prefix="alpha_fixer_video_recode_",
        suffix=".mp4",
        delete=False,
    )
    transcode_path = temp_file.name
    temp_file.close()
    try:
        result = subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-v",
                "error",
                "-fflags",
                "+discardcorrupt",
                "-err_detect",
                "ignore_err",
                *_FFMPEG_DEEP_ANALYSIS_ARGS,
                "-i",
                path,
                *_ffmpeg_stream_maps_with_audio(details, include_audio=include_audio),
                "-dn",
                "-sn",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                *(["-an"] if not include_audio else ["-c:a", "aac", "-b:a", "160k"]),
                transcode_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=180,
        )
    except Exception:
        _unlink_file_safely(transcode_path)
        return None
    try:
        if result.returncode == 0 and Path(transcode_path).is_file() and Path(transcode_path).stat().st_size > 0:
            return transcode_path
    except Exception:
        pass
    _unlink_file_safely(transcode_path)
    return None


def _attempt_video_recovery(
    path: str,
    probe: Optional[dict[str, object]] = None,
    *,
    force_recovery: bool = False,
    allow_alternate_streams: bool = True,
) -> tuple[Optional[str], str, Optional[dict[str, object]]]:
    ext = Path(path).suffix.lower()
    details = probe if probe is not None else _probe_media_details(path)
    segment_paths = _segmented_video_sources(path)
    if not force_recovery and not segment_paths and ext not in _ODD_CONTAINER_RECOVERY_EXTS and not bool(details and details.get("has_video")):
        return None, "", details
    primary_video_index = _coerce_optional_stream_index(details.get("video_stream_index")) if details else None
    primary_audio_index = _coerce_optional_stream_index(details.get("audio_stream_index")) if details else None
    for candidate in _recovery_probe_candidates(
        path,
        details,
        allow_alternate_streams=allow_alternate_streams,
        allow_alternate_audio_streams=True,
    ) or [details]:
        if candidate and bool(candidate.get("selected_video_attached_pic")) and int(candidate.get("video_attached_pic_count") or 0) >= int(candidate.get("video_stream_count") or 0):
            continue
        has_audio = bool(candidate and candidate.get("has_audio"))
        for include_audio, mode_label in ((True, ""), (False, "source audio dropped")):
            if not include_audio and not has_audio:
                continue
            if len(segment_paths) > 1:
                concat_remux_path = _concat_segmented_video_source(
                    segment_paths,
                    candidate,
                    include_audio=include_audio,
                    transcode=False,
                )
                if concat_remux_path:
                    strategy = "temporary segmented concat remux fallback active"
                    note_parts = [f"{len(segment_paths)} joined parts"]
                    selection_note = _recovery_selection_note(
                        candidate,
                        primary_video_index=primary_video_index,
                        primary_audio_index=primary_audio_index,
                    )
                    if selection_note:
                        note_parts.append(selection_note)
                    if mode_label:
                        note_parts.append(mode_label)
                    strategy += f" ({'; '.join(note_parts)})"
                    return concat_remux_path, strategy, candidate
                if candidate is None or bool(candidate.get("has_video")):
                    concat_transcode_path = _concat_segmented_video_source(
                        segment_paths,
                        candidate,
                        include_audio=include_audio,
                        transcode=True,
                    )
                    if concat_transcode_path:
                        strategy = "temporary segmented concat transcode fallback active"
                        note_parts = [f"{len(segment_paths)} joined parts"]
                        selection_note = _recovery_selection_note(
                            candidate,
                            primary_video_index=primary_video_index,
                            primary_audio_index=primary_audio_index,
                        )
                        if selection_note:
                            note_parts.append(selection_note)
                        if mode_label:
                            note_parts.append(mode_label)
                        strategy += f" ({'; '.join(note_parts)})"
                        return concat_transcode_path, strategy, candidate
            remux_path = _remux_video_source(path, candidate, include_audio=include_audio)
            if remux_path:
                strategy = "temporary ffmpeg remux fallback active"
                note_parts = []
                selection_note = _recovery_selection_note(
                    candidate,
                    primary_video_index=primary_video_index,
                    primary_audio_index=primary_audio_index,
                )
                if selection_note:
                    note_parts.append(selection_note)
                if mode_label:
                    note_parts.append(mode_label)
                if note_parts:
                    strategy += f" ({'; '.join(note_parts)})"
                return remux_path, strategy, candidate
            if candidate and bool(candidate.get("has_video")):
                transcode_path = _transcode_video_source(path, candidate, include_audio=include_audio)
                if transcode_path:
                    strategy = "temporary ffmpeg transcode fallback active"
                    note_parts = []
                    selection_note = _recovery_selection_note(
                        candidate,
                        primary_video_index=primary_video_index,
                        primary_audio_index=primary_audio_index,
                    )
                    if selection_note:
                        note_parts.append(selection_note)
                    if mode_label:
                        note_parts.append(mode_label)
                    if note_parts:
                        strategy += f" ({'; '.join(note_parts)})"
                    return transcode_path, strategy, candidate
    return None, "", details


def _video_capability_summary() -> str:
    deps_ok = _has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg()
    ffprobe_ok = _get_ffprobe_exe() is not None
    if deps_ok:
        return (
            "Ready now: standard video import, MP4 export, and image/GIF clip assembly are available. "
            + (
                "Best-effort odd-container and disc-image probing/recovery is also available through ffprobe + ffmpeg, with automatic preferred-stream selection, cue/bin sidecar retries for disc layouts, audio-drop retries for broken source audio, and manual video/audio stream pickers for multi-stream containers. "
                "Recovery also retries alternate audio tracks when multi-audio containers expose a bad default program. "
                if ffprobe_ok else
                "Odd-container recovery is partially available, but probing/detail messages stay limited until ffprobe is available. "
            )
            + "Audio-only containers still cannot be added as video clips, but cover-art/slideshow-only sources may still import as single-frame fallbacks when extraction succeeds. segmented/multipart sets can also be concat-repaired automatically when the parts live together, while partial/corrupt containers may still need manual repair."
        )
    return (
        "Limited mode: images and animated GIFs still work, but video import/MP4 export need imageio, imageio-ffmpeg, and ffmpeg. "
        "Odd-container/disc-image recovery and detailed probing stay unavailable until those dependencies are present."
    )


def _video_capability_has_limits() -> bool:
    return not (_has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg() and _get_ffprobe_exe())


def _video_capability_details() -> str:
    deps_ok = _has_ffmpeg() and _has_imageio() and _has_imageio_ffmpeg()
    ffprobe_exe = _get_ffprobe_exe()
    lines = [_video_capability_summary(), "", _video_io_diagnostics()]
    if deps_ok:
        lines.extend([
            "",
            "Current behavior:",
            "• Standard video import and MP4 export are available.",
            "• Odd-container/disc-image recovery can remux, transcode, retry without source audio, retry matching cue/bin sidecars, or salvage a still frame when ffmpeg can expose usable video data.",
            "• Multipart/segmented sources like clip.part1.vob + clip.part2.vob or movie.vob.001 + movie.vob.002 can also be concat-repaired automatically when all parts are present together.",
            "• Automatic preferred-stream selection is used for multi-stream containers when ffprobe is available, and the Selected Stream panel can reload a clip from manually chosen video and audio streams.",
            "• Audio-only containers still cannot be added as timeline video clips.",
        ])
        if ffprobe_exe:
            lines.append(f"• ffprobe detail/probing ready: {ffprobe_exe}")
        else:
            lines.append("• ffprobe detail/probing limited: recovery still works, but stream diagnostics stay less specific.")
    else:
        lines.extend([
            "",
            "Limited mode details:",
            "• Images and animated GIFs can still be added to the timeline.",
            "• MP4 export, video-source import, odd-container probing, and recovery fallbacks need imageio, imageio-ffmpeg, and ffmpeg.",
            "• Manual multi-stream selection appears only after ffprobe can inspect a loaded multi-stream clip.",
        ])
    return "\n".join(lines)


def _classify_video_import_failure(name: str, detail: str) -> str:
    ext = Path(name).suffix.lower()
    lower = detail.lower()
    if "segmented / multipart video set" in lower or "joined parts" in lower or "concat repair fallback" in lower:
        return "segmented container"
    if "dvd/console-program streams like vob/pss/str" in lower or "legacy mpeg program-stream video" in lower:
        return "program stream layout"
    if "transport-stream sources often contain discontinuities or missing timestamps" in lower:
        return "transport stream timing"
    if "matroska/webm files can carry multiple alternate video/audio programs" in lower:
        return "matroska/webm program"
    if "quicktime/mov-family files may depend on edit lists" in lower:
        return "quicktime metadata"
    if "asf/wmv containers rely heavily on index metadata" in lower or "avi/divx files often depend on legacy indexes" in lower:
        return "legacy index container"
    if "realmedia / rmvb support is best-effort" in lower:
        return "realmedia container"
    if "multiple video streams were detected" in lower or "preferred-stream=" in lower:
        return "multi-stream container"
    if "cue sidecar" in lower or "cue/bin companion" in lower or "disc sidecar files were detected" in lower:
        return "disc sidecar"
    if "attached-picture/cover-art stream" in lower or "attached cover art" in lower:
        return "cover-art stream"
    if "still-image or intra-frame-only video codecs can behave like cover-art or slideshow streams" in lower or "still-image style video codec inside a nonstandard container" in lower:
        return "still-image video"
    if "hevc/h.265 or av1" in lower or "transcoding to h.264/avc" in lower:
        return "high-efficiency codec"
    if "intermediate/broadcast codec" in lower or "editorial transcode" in lower:
        return "broadcast codec"
    if "legacy realvideo codec" in lower:
        return "legacy codec"
    if "ac3/dts-style compressed audio" in lower or "retrying without source audio" in lower:
        return "source audio track"
    if "legacy disc-style audio" in lower:
        return "legacy audio track"
    if ("codec" in lower and "unsupported" in lower) or "unsupported pixel format" in lower or "could not determine codec parameters" in lower:
        return "video codec"
    if "invalid data found when processing input" in lower or "container may be partial, malformed" in lower:
        return "container codec mismatch"
    if "audio but no playable video stream" in lower:
        return "audio-only container"
    if "did not detect a playable video stream" in lower or "did not expose a playable video stream" in lower:
        return "no playable video stream"
    if "could not resolve stable frame dimensions" in lower or "partial, malformed" in lower:
        return "partial / malformed video"
    if "recovery fallbacks still could not produce a playable clip" in lower:
        return "recovery exhausted"
    if "missing:" in lower or "imageio" in lower or "ffmpeg executable" in lower or "ffprobe" in lower:
        return "video dependency"
    if ext in _VIDEO_EXTS or "supported, non-corrupt video" in lower:
        return "video decode"
    return "video import"


def _video_failure_guidance(category: str) -> str:
    guidance = {
        "video dependency": "Install or bundle imageio, imageio-ffmpeg, ffmpeg, and ffprobe for full video probing and import.",
        "segmented container": "Multipart or segmented sources need every part present together; if automatic concat repair still fails, try a manual ffmpeg concat/remux first.",
        "program stream layout": "DVD/PSP/PS1/PS2-era program streams often carry broken navigation/index data, alternate tracks, or nontrivial stream layouts; remux, transcode, cue/bin sidecars, or audio-drop retries may be required.",
        "transport stream timing": "Transport streams often fail because of discontinuities, missing timestamps, or damaged capture timing; a clean remux/transcode usually works better than direct indexing.",
        "matroska/webm program": "Matroska/WebM containers may carry alternate video/audio programs or attachments; if the preferred stream still fails, retry a different stream selection or remux only the needed streams.",
        "quicktime metadata": "QuickTime/MOV-family files may depend on edit lists, timecode, or ProRes-style metadata that direct indexing can miss; a clean remux or transcode is often the safest fallback.",
        "legacy index container": "Legacy AVI/DivX/ASF/WMV indexes are brittle; when the index is damaged or incomplete, rebuilding the file with ffmpeg is usually more reliable than direct loading.",
        "realmedia container": "Older RealMedia/RMVB files are best handled with a full ffmpeg transcode because direct indexing and partial-stream recovery are often unreliable.",
        "disc sidecar": "BIN/CUE-style disc images often need their matching companion metadata files kept together so the track layout can be recovered correctly.",
        "multi-stream container": "This container exposes multiple video streams; the app already prefers the largest detected stream and the Selected Stream panel can retry a manual override, but a manual ffmpeg remux may still be needed.",
        "cover-art stream": "This source exposed only cover-art style video metadata instead of continuous frames; dropping attached-picture streams with ffmpeg may help.",
        "still-image video": "This source looks more like a slideshow/cover-art style video stream than continuous motion; a transcode or single-frame fallback may be the only reliable import path.",
        "high-efficiency codec": "HEVC/H.265 or AV1 streams in awkward wrappers often import more reliably after a clean MP4 remux or H.264/AVC transcode.",
        "broadcast codec": "Intermediate/editing codecs in damaged or unusual wrappers often need a clean editorial transcode before timeline playback stays stable.",
        "legacy codec": "Legacy RealVideo-style codecs are best handled with a full ffmpeg transcode because partial recovery and direct indexing are often unreliable.",
        "source audio track": "If the video stream appears valid, retrying another audio stream or dropping the original compressed audio track can unblock import/export.",
        "legacy audio track": "Legacy disc-era audio tracks often need to be dropped or replaced before the video can be recovered cleanly.",
        "video codec": "The container exposed a video stream, but the codec or pixel format still could not be decoded reliably in this runtime. Remuxing or transcoding to H.264/AVC is usually the safest fallback.",
        "container codec mismatch": "The container metadata and embedded stream data do not line up cleanly. A full ffmpeg remux/transcode often fixes these damaged index/timestamp mismatches.",
        "audio-only container": "The Video Builder only accepts clips with playable video frames; audio-only files cannot be added to the timeline.",
        "no playable video stream": "The container was recognized, but ffprobe could not expose a playable video stream for the builder.",
        "partial / malformed video": "The file appears incomplete or malformed; re-copying, remuxing, or re-encoding the source may be required.",
        "recovery exhausted": "Direct load plus ffmpeg remux/transcode/still-frame recovery could not produce a usable visual clip from this source.",
        "video decode": "The source looks like a video, but the current decode path still could not open it reliably.",
        "video import": "The source could not be imported as a supported video clip.",
    }
    return guidance.get(category, "The source could not be imported as a supported video clip.")


def _video_recovery_bucket(note: str) -> str:
    lower = note.lower()
    if "still-frame" in lower or "single-frame" in lower:
        return "still-frame"
    if "transcode" in lower:
        return "transcode"
    if "remux" in lower:
        return "remux"
    return "recovery"


def _summarize_recovery_notes(recovered: list[tuple[str, str]]) -> str:
    recovery_counts: dict[str, int] = {}
    for _name, note in recovered:
        bucket = _video_recovery_bucket(note)
        recovery_counts[bucket] = recovery_counts.get(bucket, 0) + 1
    if not recovery_counts:
        return "direct only"
    return ", ".join(f"{bucket} ×{count}" for bucket, count in sorted(recovery_counts.items()))


def _summarize_count_buckets(counts: dict[str, int], limit: int = 3) -> str:
    if not counts:
        return ""
    parts = [f"{label} ×{count}" for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    if len(parts) <= limit:
        return ", ".join(parts)
    remaining = len(parts) - limit
    return ", ".join(parts[:limit]) + f", +{remaining} more"


def _probe_fields_from_detail(detail: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _PROBE_FIELD_RE.finditer(str(detail or "")):
        key = str(match.group(1) or "").strip().lower()
        value = str(match.group(2) or "").strip()
        if key and value:
            fields[key] = value
    return fields


def _summarize_failure_probe_fields(failures: list[tuple[str, str]]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    containers: dict[str, int] = {}
    video_codecs: dict[str, int] = {}
    audio_codecs: dict[str, int] = {}
    for _name, detail in failures:
        fields = _probe_fields_from_detail(detail)
        container = str(fields.get("container") or "").strip().lower()
        video_codec = str(fields.get("video") or "").strip().lower()
        audio_codec = str(fields.get("audio") or "").strip().lower()
        if container and container != "unknown":
            containers[container] = containers.get(container, 0) + 1
        if video_codec and video_codec != "none":
            video_codecs[video_codec] = video_codecs.get(video_codec, 0) + 1
        if audio_codec and audio_codec != "none":
            audio_codecs[audio_codec] = audio_codecs.get(audio_codec, 0) + 1
    return containers, video_codecs, audio_codecs


def _summarize_clip_types(clip_snapshot: list[dict[str, object]]) -> str:
    counts: dict[str, int] = {}
    for clip in clip_snapshot:
        clip_type = str(clip.get("clip_type") or "unknown").strip().lower() or "unknown"
        counts[clip_type] = counts.get(clip_type, 0) + 1
    if not counts:
        return ""
    return ", ".join(f"{kind} ×{count}" for kind, count in sorted(counts.items()))


def _clip_stream_history_detail(clip: dict[str, object]) -> str:
    source_name = os.path.basename(str(clip.get("source_path") or clip.get("path") or "")).strip()
    if not source_name:
        return ""
    probe = clip.get("source_probe")
    probe = probe if isinstance(probe, dict) else {}
    preferred_video_index = _coerce_optional_stream_index(clip.get("preferred_video_stream_index"))
    preferred_audio_index = _coerce_optional_stream_index(clip.get("preferred_audio_stream_index"))
    selected_video_index = preferred_video_index
    if selected_video_index is None:
        selected_video_index = _coerce_optional_stream_index(probe.get("video_stream_index"))
    selected_audio_index = preferred_audio_index
    if selected_audio_index is None:
        selected_audio_index = _coerce_optional_stream_index(probe.get("audio_stream_index"))
    video_count = max(0, int(probe.get("video_stream_count") or 0))
    audio_count = max(0, int(probe.get("audio_stream_count") or 0))
    if preferred_video_index is None and preferred_audio_index is None and video_count <= 1 and audio_count <= 1:
        return ""
    parts = []
    if selected_video_index is not None:
        prefix = "manual" if preferred_video_index is not None else "auto"
        parts.append(f"{prefix} video #{selected_video_index}")
    elif video_count > 1:
        parts.append(f"auto video ({video_count} streams)")
    if selected_audio_index is not None and audio_count > 1:
        prefix = "manual" if preferred_audio_index is not None else "auto"
        parts.append(f"{prefix} audio #{selected_audio_index}")
    elif audio_count > 1:
        parts.append(f"auto audio ({audio_count} streams)")
    return f"{source_name}: " + ", ".join(parts) if parts else ""


def _clip_history_detail(clip: dict[str, object]) -> str:
    source_name = os.path.basename(str(clip.get("source_path") or clip.get("path") or "")).strip()
    if not source_name:
        return ""
    parts: list[str] = []
    load_note = str(clip.get("load_note") or "").strip()
    if load_note:
        parts.append(load_note)
    stream_detail = _clip_stream_history_detail(clip)
    if stream_detail:
        stream_text = stream_detail.split(": ", 1)[1] if ": " in stream_detail else stream_detail
        parts.append(f"streams={stream_text}")
    if not parts:
        return ""
    return f"{source_name}: " + " | ".join(parts)


def _clip_export_value(clip: object, key: str, default=None):
    if isinstance(clip, dict):
        return clip.get(key, default)
    return getattr(clip, key, default)


def _audio_source_plan(clips: list[object]) -> dict[str, int | str]:
    video_clips = 0
    audio_source_clips = 0
    silent_video_clips = 0
    silent_still_sections = 0
    dropped_audio_recovery_clips = 0
    manual_audio_override_clips = 0
    for clip in clips:
        try:
            active_frames = max(0, int(_clip_export_value(clip, "active_frames") or 0))
        except Exception:
            active_frames = 0
        if active_frames <= 0:
            continue
        clip_type = str(_clip_export_value(clip, "clip_type") or "").strip().lower()
        has_audio = bool(_clip_export_value(clip, "has_audio"))
        load_note = str(_clip_export_value(clip, "load_note") or "").strip().lower()
        preferred_audio_index = _coerce_optional_stream_index(_clip_export_value(clip, "preferred_audio_stream_index"))
        if clip_type == "video":
            video_clips += 1
            if has_audio:
                audio_source_clips += 1
            else:
                silent_video_clips += 1
            if "audio dropped" in load_note:
                dropped_audio_recovery_clips += 1
            if preferred_audio_index is not None:
                manual_audio_override_clips += 1
        else:
            silent_still_sections += 1
    if audio_source_clips <= 0:
        mode = "silent"
    elif silent_video_clips > 0 or silent_still_sections > 0:
        mode = "mixed-source+silence"
    else:
        mode = "all-source-audio"
    return {
        "mode": mode,
        "video_clips": video_clips,
        "audio_source_clips": audio_source_clips,
        "silent_video_clips": silent_video_clips,
        "silent_still_sections": silent_still_sections,
        "dropped_audio_recovery_clips": dropped_audio_recovery_clips,
        "manual_audio_override_clips": manual_audio_override_clips,
    }


def _audio_source_plan_hint(plan: dict[str, int | str]) -> str:
    parts: list[str] = []
    audio_source_clips = max(0, int(plan.get("audio_source_clips") or 0))
    video_clips = max(0, int(plan.get("video_clips") or 0))
    silent_video_clips = max(0, int(plan.get("silent_video_clips") or 0))
    silent_still_sections = max(0, int(plan.get("silent_still_sections") or 0))
    dropped_audio_recovery_clips = max(0, int(plan.get("dropped_audio_recovery_clips") or 0))
    manual_audio_override_clips = max(0, int(plan.get("manual_audio_override_clips") or 0))
    if video_clips > 1:
        parts.append(f"{audio_source_clips}/{video_clips} video clips provide source audio.")
    if silent_video_clips > 0:
        parts.append(
            f"{silent_video_clips} video clip{'s' if silent_video_clips != 1 else ''} without usable source audio will stay silent."
        )
    if silent_still_sections > 0:
        parts.append(
            f"{silent_still_sections} still-image/GIF section{'s' if silent_still_sections != 1 else ''} will be filled with silence."
        )
    if dropped_audio_recovery_clips > 0:
        parts.append(
            f"{dropped_audio_recovery_clips} recovered clip{'s' if dropped_audio_recovery_clips != 1 else ''} already dropped source audio during import."
        )
    if manual_audio_override_clips > 0:
        parts.append(
            f"{manual_audio_override_clips} clip{'s' if manual_audio_override_clips != 1 else ''} use manual audio stream override{'s' if manual_audio_override_clips != 1 else ''}."
        )
    return " ".join(parts)


def _audio_source_plan_history_notes(plan: dict[str, int | str]) -> list[str]:
    notes = [f"audio-source-plan={str(plan.get('mode') or 'silent')}"]
    video_clips = max(0, int(plan.get("video_clips") or 0))
    audio_source_clips = max(0, int(plan.get("audio_source_clips") or 0))
    notes.append(f"audio-source-clips={audio_source_clips}/{video_clips}")
    silent_video_clips = max(0, int(plan.get("silent_video_clips") or 0))
    silent_still_sections = max(0, int(plan.get("silent_still_sections") or 0))
    dropped_audio_recovery_clips = max(0, int(plan.get("dropped_audio_recovery_clips") or 0))
    manual_audio_override_clips = max(0, int(plan.get("manual_audio_override_clips") or 0))
    if silent_video_clips > 0:
        notes.append(f"audio-silent-video-clips={silent_video_clips}")
    if silent_still_sections > 0:
        notes.append(f"audio-silent-still-sections={silent_still_sections}")
    if dropped_audio_recovery_clips > 0:
        notes.append(f"audio-dropped-recovery-clips={dropped_audio_recovery_clips}")
    if manual_audio_override_clips > 0:
        notes.append(f"audio-manual-stream-overrides={manual_audio_override_clips}")
    return notes


def _open_video_reader(path: str):
    """Open an imageio ffmpeg reader, preferring the bundled ffmpeg binary."""
    import imageio

    _configure_imageio_ffmpeg()
    return imageio.get_reader(path, format="FFMPEG")


def _coerce_frame_count(value) -> int:
    """Return a positive integer frame count, or 0 when unavailable."""
    try:
        count = int(value)
    except Exception:
        return 0
    return count if count > 0 else 0


def _coerce_frame_size(value) -> Optional[tuple[int, int]]:
    """Return a positive (width, height) tuple when metadata provides one."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            width = int(value[0])
            height = int(value[1])
        except Exception:
            return None
        if width > 0 and height > 0:
            return width, height
    return None


def _is_probably_video_source(path: str, probe: Optional[dict[str, object]] = None) -> bool:
    ext = Path(path).suffix.lower()
    if ext in _VIDEO_EXTS:
        return True
    if _is_segmented_video_source(path):
        return True
    details = probe if probe is not None else _probe_media_details(path)
    if not details or not details.get("has_video"):
        return False
    if bool(details.get("selected_video_attached_pic")) and int(details.get("video_attached_pic_count") or 0) >= int(details.get("video_stream_count") or 0):
        return False
    return True


def _probe_video_clip(path: str) -> tuple[float, int, Optional[tuple[int, int]], object | None]:
    """Return (fps, frame_count, frame_size, first_frame) for a video."""
    reader = _open_video_reader(path)
    first_frame = None
    try:
        meta = reader.get_meta_data()
        try:
            fps = float(meta.get("fps") or 25.0)
        except Exception:
            fps = 25.0
        fps = fps if fps > 0 else 25.0
        frame_size = (
            _coerce_frame_size(meta.get("source_size"))
            or _coerce_frame_size(meta.get("size"))
        )
        frame_count = _coerce_frame_count(meta.get("nframes"))
        if frame_count <= 0:
            try:
                frame_count = _coerce_frame_count(reader.count_frames())
            except Exception:
                pass
        if frame_count <= 0:
            try:
                import imageio_ffmpeg

                counted, secs = imageio_ffmpeg.count_frames_and_secs(path)
                frame_count = _coerce_frame_count(counted)
                if frame_count <= 0 and secs > 0 and fps > 0:
                    frame_count = max(1, int(round(secs * fps)))
            except Exception:
                pass
        if frame_count <= 0:
            try:
                duration = float(meta.get("duration") or 0.0)
            except Exception:
                duration = 0.0
            if duration > 0 and fps > 0:
                frame_count = max(1, int(round(duration * fps)))
        if frame_count <= 0:
            try:
                first_frame = reader.get_data(0)
            except Exception:
                first_frame = None
            if first_frame is not None:
                frame_size = _frame_dimensions(first_frame)
                frame_count = 1
        elif frame_size is None:
            try:
                first_frame = reader.get_data(0)
            except Exception:
                first_frame = None
            if first_frame is not None:
                frame_size = _frame_dimensions(first_frame)
        return fps, frame_count, frame_size, first_frame
    finally:
        reader.close()


def _load_video_frames(path: str) -> tuple[list["PIL.Image.Image"], float]:
    """Decode a whole video into RGBA frames for reuse in GIF Builder."""
    from PIL import Image

    reader = _open_video_reader(path)
    frames: list[Image.Image] = []
    try:
        meta = reader.get_meta_data()
        try:
            fps = float(meta.get("fps") or 25.0)
        except Exception:
            fps = 25.0
        fps = fps if fps > 0 else 25.0
        frame_index = 0
        while True:
            try:
                frame = reader.get_data(frame_index)
            except IndexError:
                break
            except Exception:
                if frame_index == 0:
                    raise
                break
            pil = Image.fromarray(frame)
            try:
                frames.append(pil.convert("RGBA"))
            finally:
                pil.close()
            frame_index += 1
        if not frames:
            raise ValueError("No readable frames found in video.")
        return frames, fps
    finally:
        reader.close()


def _pil_to_pixmap(pil_img) -> QPixmap:
    from PIL import Image  # noqa: F401
    rgba = pil_img.convert("RGBA")
    try:
        data = rgba.tobytes("raw", "RGBA")
        qi = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qi)
    finally:
        rgba.close()


def _apply_adjustments(pil_img, brightness: float, contrast: float,
                       black_point: int, white_point: int,
                       saturation: float, sharpness: float) -> "PIL.Image.Image":
    """Apply brightness/contrast/levels/saturation/sharpness to a PIL RGBA image."""
    from PIL import Image, ImageEnhance, ImageOps
    img = pil_img.convert("RGB")
    if black_point > 0 or white_point < 255:
        def _levels(v: int) -> int:
            bp = max(0, min(254, black_point))
            wp = max(bp + 1, min(255, white_point))
            return max(0, min(255, int((v - bp) * 255 / max(1, wp - bp))))
        img = img.point(lambda v: _levels(v))
    if abs(brightness - 1.0) > 0.01:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if abs(contrast - 1.0) > 0.01:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if abs(saturation - 1.0) > 0.01:
        img = ImageEnhance.Color(img).enhance(saturation)
    if abs(sharpness - 1.0) > 0.01:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)
    return img.convert("RGBA")


@lru_cache(maxsize=16)
def _cached_vignette_mask(size: tuple[int, int]) -> "PIL.Image.Image":
    from PIL import Image
    import numpy as np

    w, h = max(1, int(size[0])), max(1, int(size[1]))
    yy, xx = np.ogrid[:h, :w]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    rx = max(w / 2.0, 1.0)
    ry = max(h / 2.0, 1.0)
    dist = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    mask_arr = np.clip(1.0 - dist, 0.0, 1.0)
    return Image.fromarray((mask_arr * 255).astype("uint8"), mode="L")


def _apply_filter(pil_img, filter_name: str) -> "PIL.Image.Image":
    """Apply a named visual filter to a PIL RGBA image."""
    from PIL import Image, ImageFilter, ImageOps
    img = pil_img.convert("RGB")
    if filter_name == "none":
        pass
    elif filter_name == "greyscale":
        img = img.convert("L").convert("RGB")
    elif filter_name == "sepia":
        img = ImageOps.colorize(img.convert("L"), black=(32, 18, 0), white=(255, 228, 185))
    elif filter_name == "invert":
        img = ImageOps.invert(img)
    elif filter_name == "sharpen":
        img = img.filter(ImageFilter.SHARPEN)
    elif filter_name == "blur":
        img = img.filter(ImageFilter.GaussianBlur(radius=2))
    elif filter_name == "edge_enhance":
        img = img.filter(ImageFilter.EDGE_ENHANCE)
    elif filter_name == "emboss":
        img = img.filter(ImageFilter.EMBOSS)
    elif filter_name == "vignette":
        mask = _cached_vignette_mask(img.size)
        dark = Image.new("RGB", img.size, (0, 0, 0))
        img = Image.composite(img, dark, mask)
    return img.convert("RGBA")


def _coerce_export_size(size: tuple[int, int], fmt: str) -> tuple[int, int]:
    """Clamp export size and round MP4 output up to even dimensions."""
    width = max(1, int(size[0]))
    height = max(1, int(size[1]))
    if fmt == "mp4":
        if width % 2:
            width += 1
        if height % 2:
            height += 1
    return width, height


def _fit_frame_to_canvas(pil_img, canvas_size: tuple[int, int], fmt: str) -> "PIL.Image.Image":
    """Resize a frame to fit inside a shared export canvas with letterboxing."""
    from PIL import Image, ImageOps

    canvas_size = _coerce_export_size(canvas_size, fmt)
    if pil_img.size == canvas_size and pil_img.mode == "RGBA":
        return pil_img.copy()

    rgba = pil_img if pil_img.mode == "RGBA" else pil_img.convert("RGBA")
    fitted = rgba
    canvas = None
    try:
        if rgba.size != canvas_size:
            fitted = ImageOps.contain(rgba, canvas_size, method=Image.Resampling.LANCZOS)
        background = (0, 0, 0, 255) if fmt == "mp4" else (0, 0, 0, 0)
        canvas = Image.new("RGBA", canvas_size, background)
        offset = (
            max(0, (canvas_size[0] - fitted.width) // 2),
            max(0, (canvas_size[1] - fitted.height) // 2),
        )
        canvas.paste(fitted, offset, fitted)
        return canvas
    finally:
        if fitted is not rgba:
            fitted.close()
        if rgba is not pil_img:
            rgba.close()


def _format_extension_filter(label: str, extensions: set[str]) -> str:
    patterns = " ".join(f"*{ext}" for ext in sorted(extensions))
    return f"{label} ({patterns});;All Files (*)"


@lru_cache(maxsize=128)
def _video_has_audio_stream(path: str) -> bool:
    ffprobe_exe = _get_ffprobe_exe()
    if ffprobe_exe:
        try:
            result = subprocess.run(
                [
                    ffprobe_exe,
                    "-v", "error",
                    "-select_streams", "a:0",
                    "-show_entries", "stream=index",
                    "-of", "csv=p=0",
                    path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                text=True,
                timeout=20,
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except Exception:
            pass
    ffmpeg_exe = _get_ffmpeg_exe()
    if not ffmpeg_exe:
        return False
    try:
        result = subprocess.run(
            [ffmpeg_exe, "-v", "error", "-i", path, "-map", "0:a:0", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
        )
    except Exception:
        return False
    return result.returncode == 0


def _build_atempo_filters(speed_factor: float) -> list[str]:
    def _fmt(value: float) -> str:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    remaining = max(0.01, float(speed_factor))
    filters: list[str] = []
    while remaining < 0.5:
        filters.append(f"atempo={_fmt(0.5)}")
        remaining /= 0.5
    while remaining > 2.0:
        filters.append(f"atempo={_fmt(2.0)}")
        remaining /= 2.0
    filters.append(f"atempo={_fmt(remaining)}")
    return filters


class _ImageFrameGetter:
    """Picklable frame getter for a single-image clip.

    Using a module-level class instead of a local closure avoids the
    ``AttributeError: Can't pickle local object '_load_image_as_clip.<locals>._get'``
    that occurs when Qt serialises QListWidgetItem UserRole data during a
    drag-to-reorder operation.
    """

    def __init__(self, img: "PIL.Image.Image") -> None:
        self._img = img

    def __call__(self, idx: int) -> "PIL.Image.Image":
        return self._img.copy()

    def _close_reader(self) -> None:
        try:
            self._img.close()
        except Exception:
            pass


class _SequenceFrameGetter:
    """Picklable frame getter backed by one or more in-memory PIL frames."""

    def __init__(self, frames: list["PIL.Image.Image"]) -> None:
        self._frames = list(frames)

    def __call__(self, idx: int) -> "PIL.Image.Image":
        clamped = max(0, min(len(self._frames) - 1, int(idx)))
        return self._frames[clamped].copy()

    def _close_reader(self) -> None:
        for frame in self._frames:
            try:
                frame.close()
            except Exception:
                pass
        self._frames.clear()


class _VideoFrameGetter:
    """Picklable frame getter for a multi-frame video clip."""

    def __init__(
        self,
        path: str,
        total_frames: int,
        first_frame=None,
        cleanup_paths: Optional[list[str]] = None,
    ) -> None:
        self._path = path
        self._total_frames = max(1, int(total_frames))
        self._prefetched_frame = first_frame
        self._cleanup_paths = list(cleanup_paths or [])
        self._reader = None
        self._last_idx = -1
        self._last_frame = None
        self._lock = Lock()

    def _open_reader(self) -> None:
        self._reader = _open_video_reader(self._path)

    def _close_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None
        self._last_idx = -1
        self._last_frame = None

    def _release_resources(self) -> None:
        self._close_reader()
        for cleanup_path in self._cleanup_paths:
            _unlink_file_safely(cleanup_path)
        self._cleanup_paths.clear()

    def __call__(self, idx: int) -> "PIL.Image.Image":
        from PIL import Image

        clamped = max(0, min(self._total_frames - 1, int(idx)))
        with self._lock:
            if clamped == 0 and self._prefetched_frame is not None:
                frame = self._prefetched_frame
                self._last_idx = 0
                self._last_frame = frame
                self._prefetched_frame = None
            else:
                reopened = self._reader is None or clamped < self._last_idx
                if reopened:
                    self._close_reader()
                    self._open_reader()
                try:
                    if clamped == self._last_idx and self._last_frame is not None:
                        frame = self._last_frame
                    elif not reopened and clamped == self._last_idx + 1:
                        frame = self._reader.get_next_data()
                    else:
                        frame = self._reader.get_data(clamped)
                    self._last_idx = clamped
                    self._last_frame = frame
                except Exception:
                    self._close_reader()
                    raise
        return Image.fromarray(frame).convert("RGBA")

    def __getstate__(self) -> dict:
        return {
            "_path": self._path,
            "_total_frames": self._total_frames,
            "_cleanup_paths": list(self._cleanup_paths),
        }

    def __setstate__(self, state: dict) -> None:
        self._path = state["_path"]
        self._total_frames = max(1, int(state["_total_frames"]))
        self._prefetched_frame = None
        self._cleanup_paths = list(state.get("_cleanup_paths") or [])
        self._reader = None
        self._last_idx = -1
        self._last_frame = None
        self._lock = Lock()


class _ClipEntry:
    """One video clip or image in the video tool's timeline."""

    def __init__(self, path: str, total_frames: int,
                 get_frame_fn, fps: float = 25.0,
                 frame_size: Optional[tuple[int, int]] = None,
                 clip_type: str = "video",
                 has_audio: bool = False,
                 source_path: Optional[str] = None,
                 load_note: str = "",
                 load_strategy: str = "",
                 source_probe: Optional[dict[str, object]] = None,
                 preferred_video_stream_index: Optional[int] = None,
                 preferred_audio_stream_index: Optional[int] = None):
        self.path = path
        self.source_path = source_path or path
        self.total_frames = total_frames
        self.fps = fps
        self._get_frame = get_frame_fn   # callable(frame_idx) → PIL RGBA image
        self.frame_size = frame_size
        self.clip_type = clip_type
        self.has_audio = has_audio
        self.load_note = load_note
        self.load_strategy = load_strategy or ("direct" if not load_note else load_note)
        self.source_probe = source_probe
        self.preferred_video_stream_index = _coerce_optional_stream_index(preferred_video_stream_index)
        self.preferred_audio_stream_index = _coerce_optional_stream_index(preferred_audio_stream_index)
        self.speed_percent: int = 100
        self.still_duration_frames: int = 25 if clip_type == "image" else 1
        self.trim_start: int = 0
        self.trim_end: int = max(0, total_frames - 1)

    @property
    def base_active_frames(self) -> int:
        return max(0, self.trim_end - self.trim_start + 1)

    @property
    def active_frames(self) -> int:
        if self.clip_type == "image":
            return max(1, int(self.still_duration_frames))
        base = self.base_active_frames
        if base <= 0:
            return 0
        speed = max(0.1, self.speed_percent / 100.0)
        return max(1, int(round(base / speed)))

    def output_index_to_source_offset(self, idx: int) -> int:
        if self.clip_type == "image":
            return 0
        base = self.base_active_frames
        if base <= 0:
            return 0
        speed = max(0.1, self.speed_percent / 100.0)
        mapped = int(idx * speed)
        return max(0, min(base - 1, mapped))

    def split_second_half_offset(self, output_idx: int) -> Optional[int]:
        if self.clip_type == "image":
            return None
        base = self.base_active_frames
        if base <= 1:
            return None
        current = self.output_index_to_source_offset(output_idx)
        for next_idx in range(max(0, int(output_idx)) + 1, self.active_frames):
            candidate = self.output_index_to_source_offset(next_idx)
            if candidate > current:
                return candidate
        return None

    def get_frame(self, idx: int) -> "PIL.Image.Image":
        return self._get_frame(self.trim_start + self.output_index_to_source_offset(idx))

    def close(self) -> None:
        close_fn = getattr(self._get_frame, "_release_resources", None)
        if callable(close_fn):
            close_fn()
            return
        close_fn = getattr(self._get_frame, "_close_reader", None)
        if callable(close_fn):
            close_fn()


def _format_clip_label(clip: "_ClipEntry", path: str, icon: str) -> str:
    size = clip.frame_size
    size_text = f"{size[0]}×{size[1]}  •  " if size else ""
    if clip.clip_type == "image":
        return f"{icon}  {Path(path).name}  [{size_text}still • {clip.still_duration_frames} fr]"
    speed_suffix = "" if clip.speed_percent == 100 else f" • {clip.speed_percent}% speed"
    return f"{icon}  {Path(path).name}  [{size_text}{clip.active_frames} fr @ {clip.fps:.1f} fps{speed_suffix}]"


_ADJUSTMENT_DEFAULT_VALUES = {
    "brightness": 100,
    "contrast": 100,
    "saturation": 100,
    "sharpness": 100,
    "black_point": 0,
    "white_point": 255,
}


def _load_video_clip(
    path: str,
    *,
    preferred_video_stream_index: Optional[int] = None,
    preferred_audio_stream_index: Optional[int] = None,
) -> Optional["_ClipEntry"]:
    """Try to load a video file using imageio-ffmpeg.  Returns None on failure."""
    preferred_video_stream_index = _coerce_optional_stream_index(preferred_video_stream_index)
    preferred_audio_stream_index = _coerce_optional_stream_index(preferred_audio_stream_index)
    allow_direct_open = preferred_video_stream_index is None and preferred_audio_stream_index is None

    def _try_load_candidate(actual_path: str, *, source_retry_note: str = "") -> Optional["_ClipEntry"]:
        probe = _probe_media_details(
            actual_path,
            preferred_video_stream_index=preferred_video_stream_index,
            preferred_audio_stream_index=preferred_audio_stream_index,
        )
        if allow_direct_open:
            try:
                fps, frame_count, frame_size, first_frame = _probe_video_clip(actual_path)
                if frame_count <= 0:
                    return None
                if probe and bool(probe.get("selected_video_attached_pic")):
                    raise RuntimeError("Attached-picture stream selected")
                note = _join_note_parts(source_retry_note)
                return _ClipEntry(
                    actual_path,
                    frame_count,
                    _VideoFrameGetter(actual_path, frame_count, first_frame),
                    fps,
                    frame_size=frame_size,
                    clip_type="video",
                    has_audio=_video_has_audio_stream(actual_path),
                    source_path=path,
                    load_note=note,
                    load_strategy=note or "direct",
                    source_probe=probe,
                    preferred_video_stream_index=preferred_video_stream_index if preferred_video_stream_index is not None else _coerce_optional_stream_index(probe.get("video_stream_index") if probe else None),
                    preferred_audio_stream_index=preferred_audio_stream_index if preferred_audio_stream_index is not None else _coerce_optional_stream_index(probe.get("audio_stream_index") if probe else None),
                )
            except Exception:
                probe = _probe_media_details(
                    actual_path,
                    preferred_video_stream_index=preferred_video_stream_index,
                    preferred_audio_stream_index=preferred_audio_stream_index,
                )
        manual_stream_note = _stream_selection_note(probe, manual=preferred_video_stream_index is not None)
        recovered_path, recovery_note, recovery_probe = _attempt_video_recovery(
            actual_path,
            probe,
            force_recovery=not allow_direct_open,
            allow_alternate_streams=preferred_video_stream_index is None,
        )
        if recovered_path:
            try:
                fps, frame_count, frame_size, first_frame = _probe_video_clip(recovered_path)
                if frame_count <= 0:
                    _unlink_file_safely(recovered_path)
                    recovered_path = None
                else:
                    active_probe = recovery_probe or probe
                    note = _join_note_parts(source_retry_note, manual_stream_note, recovery_note)
                    return _ClipEntry(
                        recovered_path,
                        frame_count,
                        _VideoFrameGetter(recovered_path, frame_count, first_frame, cleanup_paths=[recovered_path]),
                        fps,
                        frame_size=frame_size,
                        clip_type="video",
                        has_audio=_video_has_audio_stream(recovered_path),
                        source_path=path,
                        load_note=note,
                        load_strategy=note,
                        source_probe=active_probe,
                        preferred_video_stream_index=preferred_video_stream_index if preferred_video_stream_index is not None else _coerce_optional_stream_index(active_probe.get("video_stream_index") if active_probe else None),
                        preferred_audio_stream_index=preferred_audio_stream_index if preferred_audio_stream_index is not None else _coerce_optional_stream_index(active_probe.get("audio_stream_index") if active_probe else None),
                    )
            except Exception:
                _unlink_file_safely(recovered_path)
                recovered_path = None
        still_frame = _extract_visual_still_frame(actual_path, probe)
        if still_frame is None:
            return None
        try:
            note = _join_note_parts(source_retry_note, manual_stream_note, _visual_still_fallback_note(probe))
            return _ClipEntry(
                actual_path,
                1,
                _ImageFrameGetter(still_frame),
                25.0,
                frame_size=still_frame.size,
                clip_type="image",
                has_audio=bool(probe.get("has_audio")) if probe else False,
                source_path=path,
                load_note=note,
                load_strategy=note,
                source_probe=probe,
                preferred_video_stream_index=preferred_video_stream_index,
                preferred_audio_stream_index=preferred_audio_stream_index,
            )
        except Exception:
            try:
                still_frame.close()
            except Exception:
                pass
            return None

    clip = _try_load_candidate(path)
    if clip is not None:
        return clip
    for alternate_path in _disc_sidecar_sources(path):
        clip = _try_load_candidate(
            alternate_path,
            source_retry_note=_disc_sidecar_retry_note(path, alternate_path),
        )
        if clip is not None:
            return clip
    return None


def _load_image_as_clip(path: str) -> Optional["_ClipEntry"]:
    """Wrap a still image or animated GIF file as a clip."""
    try:
        from PIL import Image

        ext = Path(path).suffix.lower()
        if ext == ".gif":
            gif = Image.open(path)
            frames: list[Image.Image] = []
            durations: list[int] = []
            try:
                n = getattr(gif, "n_frames", 1)
                if n <= 1:
                    frames.append(gif.convert("RGBA"))
                else:
                    canvas = Image.new("RGBA", gif.size, (0, 0, 0, 0))
                    try:
                        for i in range(n):
                            gif.seek(i)
                            curr = gif.convert("RGBA")
                            previous_canvas = canvas.copy()
                            composite = canvas.copy()
                            rect = _gif_frame_rect(gif, curr)
                            left, top, right, bottom = rect
                            rect_size = (max(0, right - left), max(0, bottom - top))
                            if curr.size == rect_size:
                                paste_img = curr
                            elif curr.width >= right and curr.height >= bottom:
                                paste_img = curr.crop(rect)
                            else:
                                paste_img = curr
                            try:
                                composite.paste(paste_img, (left, top), paste_img)
                            finally:
                                if paste_img is not curr:
                                    paste_img.close()
                                curr.close()
                            frames.append(composite.copy())
                            durations.append(max(1, int(gif.info.get("duration", 100) or 100)))
                            disposal = getattr(gif, "disposal_method", gif.info.get("disposal", 0))
                            canvas.close()
                            if disposal == 2:
                                canvas = Image.new("RGBA", gif.size, (0, 0, 0, 0))
                                composite.close()
                                previous_canvas.close()
                            elif disposal == 3:
                                canvas = previous_canvas
                                composite.close()
                            else:
                                canvas = composite
                                previous_canvas.close()
                    finally:
                        canvas.close()
            finally:
                gif.close()
            fps = 25.0
            if durations:
                avg_duration = sum(durations) / len(durations)
                if avg_duration > 0:
                    fps = max(0.1, min(60.0, 1000.0 / avg_duration))
            total_frames = max(1, len(frames))
            getter = _SequenceFrameGetter(frames)
            return _ClipEntry(
                path,
                total_frames,
                getter,
                fps,
                frame_size=frames[0].size if frames else None,
                clip_type="gif",
            )

        from ..core.alpha_processor import load_image
        img = load_image(path)
        return _ClipEntry(path, 1, _ImageFrameGetter(img), 25.0, frame_size=img.size, clip_type="image")
    except Exception:
        return None


def _make_hslider(lo: int, hi: int, val: int) -> QSlider:
    """Return a horizontal QSlider."""
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    s.setTracking(True)
    return s


class _ClipListWidget(QListWidget):
    """Drag-to-reorder clip list that also accepts dropped video/image files.

    Each item stores its ``_ClipEntry`` in ``_CLIP_ROLE``.  ``order_changed``
    fires after any internal drag so the caller can re-sync ``_clips``.
    """

    files_dropped = pyqtSignal(list, int)  # list[str], insert_row
    order_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # InternalMove keeps _ClipEntry objects intact across drags (same
        # reasoning as _FrameListWidget in gif_builder.py).
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.model().rowsMoved.connect(lambda *_: self.order_changed.emit())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()
                     if url.toLocalFile()]
            if paths:
                pos = event.position().toPoint()
                row = self.count()
                index = self.indexAt(pos)
                if index.isValid():
                    row = index.row()
                    rect = self.visualRect(index)
                    if pos.y() > rect.center().y():
                        row += 1
                self.files_dropped.emit(paths, row)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class VideoToolDialog(QDialog):
    """Lightweight video editor dialog.

    Combines multiple clips, applies visual adjustments and filters, and
    exports the result. MP4 exports can optionally carry over source audio
    from video clips while still-image/GIF sections render as silence. Video
    clips and MP4 export require imageio, imageio-ffmpeg, and a working
    ffmpeg executable. Still-image clips can still be assembled into
    animated GIF exports without those video dependencies.
    """
    status_notice = pyqtSignal(str, int)
    queue_status_changed = pyqtSignal(str)
    SHORTCUT_DEFS = (
        ("video_remove_selected", "Delete", "Remove selected clip", "Video Editor"),
        ("video_toggle_play", "Space", "Play or pause preview", "Video Editor"),
        ("video_export", "Ctrl+S", "Export video or GIF", "Video Editor"),
        ("video_split_clip", "Ctrl+E", "Split clip at playhead", "Video Editor"),
    )

    def __init__(self, parent=None, tooltip_mgr=None):
        super().__init__(parent)
        self.setWindowTitle("🎬 Video Editor")
        self.setMinimumSize(1120, 760)
        self.resize(1320, 820)
        self.setModal(False)
        self._tooltip_mgr = tooltip_mgr
        _configure_imageio_ffmpeg()
        self._clips: list[_ClipEntry] = []
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._is_playing: bool = False
        self._ffmpeg_available = _has_ffmpeg()
        self._imageio_available = _has_imageio()
        self._imageio_ffmpeg_available = _has_imageio_ffmpeg()
        self._video_io_diagnostics = _video_io_diagnostics()
        self._video_io_available = (
            self._ffmpeg_available
            and self._imageio_available
            and self._imageio_ffmpeg_available
        )
        self._mp4_export_available = self._video_io_available
        self._build_ui()
        mgr = self._resolve_tooltip_mgr()
        if mgr is not None:
            self.register_tooltips(mgr)
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("🎬  Video Editor")
        title.setObjectName("subheader")
        root.addWidget(title)

        self._capability_lbl = QLabel(_video_capability_summary())
        self._capability_lbl.setWordWrap(True)
        self._capability_lbl.setStyleSheet(
            "color: #b26a00; font-size: 11px;"
            if _video_capability_has_limits()
            else "color: #2e7d32; font-size: 11px;"
        )
        self._capability_lbl.setToolTip(_video_capability_details())
        root.addWidget(self._capability_lbl)

        if not self._video_io_available:
            warn = QLabel(
                "⚠  Video import and MP4 export need imageio, imageio-ffmpeg, and a working ffmpeg executable.  "
                "You can still add images/GIFs and export an animated GIF.\n"
                f"{self._video_io_diagnostics}"
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: orange;")
            root.addWidget(warn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: clip list ──────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        tb = QHBoxLayout()
        self._btn_add_video = QPushButton("🎞  Add Video")
        self._btn_add_video.setEnabled(self._video_io_available)
        self._btn_add_video.setToolTip("Add a video file to the timeline. (Requires ffmpeg, imageio, and imageio-ffmpeg)")
        self._btn_add_video.clicked.connect(self._add_video)
        tb.addWidget(self._btn_add_video)

        self._btn_add_img = QPushButton("🖼  Add Images / GIFs")
        self._btn_add_img.setToolTip("Add still image(s), animated GIFs, or other supported image files.")
        self._btn_add_img.clicked.connect(self._add_images)
        tb.addWidget(self._btn_add_img)

        self._btn_remove = QPushButton("🗑  Remove")
        self._btn_remove.setToolTip("Remove selected clip.  Shortcut: Delete")
        self._btn_remove.clicked.connect(self._remove_selected)
        tb.addWidget(self._btn_remove)

        self._btn_split = QPushButton("✂  Split at Playhead")
        self._btn_split.setToolTip(
            "Split the clip under the current playhead so you can insert media between the two parts.  Shortcut: Ctrl+E"
        )
        self._btn_split.clicked.connect(self._split_clip_at_playhead)
        tb.addWidget(self._btn_split)
        left_layout.addLayout(tb)

        insert_row = QHBoxLayout()
        insert_row.addWidget(QLabel("Insert new clips:"))
        self._insert_mode_combo = QComboBox()
        self._insert_mode_combo.addItem("After selected clip", userData="after")
        self._insert_mode_combo.addItem("Before selected clip", userData="before")
        self._insert_mode_combo.addItem("At end of timeline", userData="end")
        self._insert_mode_combo.setToolTip(
            "Controls where newly added media is inserted when using the Add buttons.\n"
            "Dropping files onto the timeline still inserts exactly at the drop position."
        )
        insert_row.addWidget(self._insert_mode_combo, 1)
        left_layout.addLayout(insert_row)

        hint = QLabel("💡 Drag clips to reorder  •  Drop files to add")
        hint.setStyleSheet("color: gray; font-style: italic; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(hint)

        self._import_status_lbl = QLabel(
            "Ready: add videos, images, or animated GIFs. Recovery notes, failure groups, and skipped-file details will appear here."
        )
        self._import_status_lbl.setWordWrap(True)
        self._import_status_lbl.setStyleSheet("color: gray; font-size: 11px;")
        self._import_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._import_status_lbl)
        self._import_detail_box = QPlainTextEdit()
        self._import_detail_box.setReadOnly(True)
        self._import_detail_box.setPlaceholderText("Detailed import diagnostics will appear here.")
        self._import_detail_box.setMinimumHeight(70)
        self._import_detail_box.setMaximumHeight(110)
        self._import_detail_box.setVisible(False)
        left_layout.addWidget(self._import_detail_box)

        self._timeline_summary_lbl = QLabel("Timeline: 0 clips  •  0.00 s  •  0 frames")
        self._timeline_summary_lbl.setWordWrap(True)
        self._timeline_summary_lbl.setStyleSheet("color: gray; font-size: 11px;")
        self._timeline_summary_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._timeline_summary_lbl)

        self._clip_list = _ClipListWidget()
        self._clip_list.files_dropped.connect(self._on_files_dropped)
        self._clip_list.order_changed.connect(self._sync_clips_from_list)
        self._clip_list.currentRowChanged.connect(self._on_clip_selected)
        left_layout.addWidget(self._clip_list, 1)

        # Trim sliders
        grp_trim = QGroupBox("Trim Selected Clip")
        trim_vl = QVBoxLayout(grp_trim)
        trim_vl.setSpacing(6)

        # Start trim
        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("In:"))
        self._trim_start_slider = _make_hslider(0, 0, 0)
        self._trim_start_slider.setToolTip("Drag to set the clip's start (in) point.")
        self._trim_start_slider.valueChanged.connect(self._on_trim_start_changed)
        start_row.addWidget(self._trim_start_slider, 1)
        self._trim_start_lbl = QLabel("0")
        self._trim_start_lbl.setFixedWidth(48)
        self._trim_start_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        start_row.addWidget(self._trim_start_lbl)
        trim_vl.addLayout(start_row)

        # End trim
        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("Out:"))
        self._trim_end_slider = _make_hslider(0, 0, 0)
        self._trim_end_slider.setToolTip("Drag to set the clip's end (out) point.")
        self._trim_end_slider.valueChanged.connect(self._on_trim_end_changed)
        end_row.addWidget(self._trim_end_slider, 1)
        self._trim_end_lbl = QLabel("0")
        self._trim_end_lbl.setFixedWidth(48)
        self._trim_end_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        end_row.addWidget(self._trim_end_lbl)
        trim_vl.addLayout(end_row)

        self._clip_info_lbl = QLabel("")
        self._clip_info_lbl.setStyleSheet("color: gray; font-size: 11px;")
        self._clip_info_lbl.setWordWrap(True)
        trim_vl.addWidget(self._clip_info_lbl)
        left_layout.addWidget(grp_trim)

        grp_stream = QGroupBox("Selected Stream")
        stream_vl = QVBoxLayout(grp_stream)
        stream_vl.setSpacing(6)
        self._stream_summary_lbl = QLabel("Select a loaded video clip to inspect its available video/audio streams.")
        self._stream_summary_lbl.setWordWrap(True)
        self._stream_summary_lbl.setStyleSheet("color: gray; font-size: 11px;")
        stream_vl.addWidget(self._stream_summary_lbl)
        stream_row = QGridLayout()
        stream_row.addWidget(QLabel("Video stream:"), 0, 0)
        self._stream_picker_combo = QComboBox()
        self._stream_picker_combo.setToolTip(
            "Choose which detected video stream to reload for the selected clip.\n"
            "Auto keeps the probe-preferred stream; explicit picks force a remux/transcode path when needed."
        )
        self._stream_picker_combo.currentIndexChanged.connect(self._on_stream_picker_changed)
        stream_row.addWidget(self._stream_picker_combo, 0, 1)
        stream_row.addWidget(QLabel("Audio stream:"), 1, 0)
        self._audio_stream_picker_combo = QComboBox()
        self._audio_stream_picker_combo.setToolTip(
            "Choose which detected audio stream should stay attached to the selected clip.\n"
            "Auto keeps the probe-preferred audio stream; explicit picks are preserved through reload/export when possible."
        )
        self._audio_stream_picker_combo.currentIndexChanged.connect(self._on_stream_picker_changed)
        stream_row.addWidget(self._audio_stream_picker_combo, 1, 1)
        stream_vl.addLayout(stream_row)
        self._btn_apply_stream = QPushButton("Reload Selected Streams")
        self._btn_apply_stream.setToolTip(
            "Reload the selected clip from the chosen video/audio stream combination while preserving trim and timing settings when possible."
        )
        self._btn_apply_stream.clicked.connect(self._apply_selected_stream_choice)
        stream_vl.addWidget(self._btn_apply_stream)
        self._stream_hint_lbl = QLabel("Use Auto to keep the probe-preferred video/audio streams, or choose explicit streams to preserve a manual override.")
        self._stream_hint_lbl.setWordWrap(True)
        self._stream_hint_lbl.setStyleSheet("color: gray; font-size: 11px;")
        stream_vl.addWidget(self._stream_hint_lbl)
        left_layout.addWidget(grp_stream)
        self._stream_group = grp_stream

        grp_timing = QGroupBox("Selected Clip Timing")
        timing_vl = QVBoxLayout(grp_timing)
        timing_vl.setSpacing(6)

        clip_speed_row = QHBoxLayout()
        clip_speed_row.addWidget(QLabel("Clip speed:"))
        self._clip_speed_slider = _make_hslider(10, 400, 100)
        self._clip_speed_slider.setToolTip(
            "Adjust playback speed for the selected video or animated GIF clip.\n"
            "100% = original speed, lower = slower, higher = faster."
        )
        self._clip_speed_slider.valueChanged.connect(self._on_clip_speed_changed)
        clip_speed_row.addWidget(self._clip_speed_slider, 1)
        self._clip_speed_lbl = QLabel("1.00×")
        self._clip_speed_lbl.setFixedWidth(56)
        self._clip_speed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        clip_speed_row.addWidget(self._clip_speed_lbl)
        timing_vl.addLayout(clip_speed_row)

        still_row = QHBoxLayout()
        still_row.addWidget(QLabel("Still duration:"))
        self._still_duration_spin = QSpinBox()
        self._still_duration_spin.setRange(1, 3600)
        self._still_duration_spin.setValue(25)
        self._still_duration_spin.setToolTip(
            "How many timeline frames a still image should stay on screen.\n"
            "At 25 FPS, 25 frames = about 1 second."
        )
        self._still_duration_spin.valueChanged.connect(self._on_still_duration_changed)
        still_row.addWidget(self._still_duration_spin)
        self._still_duration_lbl = QLabel("1.00 s")
        self._still_duration_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        still_row.addWidget(self._still_duration_lbl, 1)
        timing_vl.addLayout(still_row)

        self._timing_hint_lbl = QLabel("")
        self._timing_hint_lbl.setWordWrap(True)
        self._timing_hint_lbl.setStyleSheet("color: gray; font-size: 11px;")
        timing_vl.addWidget(self._timing_hint_lbl)
        left_layout.addWidget(grp_timing)

        splitter.addWidget(left)

        # ── Centre: preview ──────────────────────────────────────────────
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(6)

        grp_preview = QGroupBox("Preview")
        pv_layout = QVBoxLayout(grp_preview)
        pv_layout.setSpacing(6)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumSize(_PREVIEW_MAX_W, _PREVIEW_MAX_H)
        self._preview_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._preview_lbl.setText("Add clips to preview and export.")
        pv_layout.addWidget(self._preview_lbl, 1)

        # Scrubber
        self._scrubber = _make_hslider(0, 0, 0)
        self._scrubber.setToolTip("Drag to scrub through the timeline.")
        self._scrubber.valueChanged.connect(self._on_scrub)
        pv_layout.addWidget(self._scrubber)

        # Transport
        ctrl = QHBoxLayout()
        self._btn_rewind = QPushButton("⏮")
        self._btn_rewind.setFixedWidth(36)
        self._btn_rewind.setToolTip("Rewind to beginning")
        self._btn_rewind.clicked.connect(self._rewind)
        ctrl.addWidget(self._btn_rewind)

        self._btn_play = QPushButton("▶  Play")
        self._btn_play.setCheckable(True)
        self._btn_play.setToolTip("Play / Pause.  Space bar also works.")
        self._btn_play.toggled.connect(self._on_play_toggled)
        ctrl.addWidget(self._btn_play)

        self._pos_lbl = QLabel("0 / 0")
        self._pos_lbl.setObjectName("subheader")
        ctrl.addWidget(self._pos_lbl)
        ctrl.addStretch()
        pv_layout.addLayout(ctrl)

        # FPS row
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Preview FPS:"))
        self._fps_slider = _make_hslider(1, 60, 25)
        self._fps_slider.setToolTip("Playback speed for preview and export.")
        self._fps_val_lbl = QLabel("25 fps")
        self._fps_val_lbl.setFixedWidth(50)
        self._fps_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._fps_slider.valueChanged.connect(
            lambda v: self._fps_val_lbl.setText(f"{v} fps")
        )
        self._fps_slider.valueChanged.connect(lambda _v: self._update_timing_controls())
        fps_row.addWidget(self._fps_slider, 1)
        fps_row.addWidget(self._fps_val_lbl)
        pv_layout.addLayout(fps_row)

        centre_layout.addWidget(grp_preview, 1)
        splitter.addWidget(centre)

        # ── Right: adjustments + export ──────────────────────────────────
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_inner = QWidget()
        right_layout = QVBoxLayout(right_inner)
        right_layout.setContentsMargins(4, 0, 4, 0)
        right_layout.setSpacing(6)
        right_scroll.setWidget(right_inner)

        grp_adj = QGroupBox("Visual Adjustments")
        adj_vl = QVBoxLayout(grp_adj)
        adj_vl.setSpacing(8)

        def _adj_row(label: str, lo: int, hi: int, val: int,
                     fmt_fn=None, tooltip: str = "") -> QSlider:
            """Add a labelled slider row; return the slider."""
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(90)
            row.addWidget(lbl)
            slider = _make_hslider(lo, hi, val)
            if tooltip:
                slider.setToolTip(tooltip)
            row.addWidget(slider, 1)
            val_lbl = QLabel()
            val_lbl.setFixedWidth(52)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if fmt_fn is None:
                fmt_fn = str
            val_lbl.setText(fmt_fn(val))
            slider.valueChanged.connect(lambda v, fn=fmt_fn, lb=val_lbl: lb.setText(fn(v)))
            slider.valueChanged.connect(self._refresh_preview_adjustments)
            row.addWidget(val_lbl)
            adj_vl.addLayout(row)
            return slider

        # Float sliders use ×100 integer range, divide by 100.0 when reading
        def _f(v: int) -> str:  # format 100-scale int as 2-dp float
            return f"{v / 100:.2f}"

        self._brightness_slider = _adj_row(
            "Brightness:", 10, 400, _ADJUSTMENT_DEFAULT_VALUES["brightness"], _f,
            "1.00 = original  •  >1 = brighter  •  <1 = darker"
        )
        self._contrast_slider = _adj_row(
            "Contrast:", 10, 400, _ADJUSTMENT_DEFAULT_VALUES["contrast"], _f,
            "1.00 = original  •  >1 = more contrast"
        )
        self._saturation_slider = _adj_row(
            "Saturation:", 0, 400, _ADJUSTMENT_DEFAULT_VALUES["saturation"], _f,
            "1.00 = original  •  0.00 = greyscale  •  >1 = vivid"
        )
        self._sharpness_slider = _adj_row(
            "Sharpness:", 0, 400, _ADJUSTMENT_DEFAULT_VALUES["sharpness"], _f,
            "1.00 = original  •  >1 = sharper  •  <1 = softer"
        )
        self._black_slider = _adj_row(
            "Black point:", 0, 254, _ADJUSTMENT_DEFAULT_VALUES["black_point"],
            tooltip="Input level mapped to black — lifts shadows"
        )
        self._white_slider = _adj_row(
            "White point:", 1, 255, _ADJUSTMENT_DEFAULT_VALUES["white_point"],
            tooltip="Input level mapped to white — pulls down highlights"
        )

        btn_reset = QPushButton("↺  Reset All Adjustments")
        btn_reset.clicked.connect(self._reset_adjustments)
        adj_vl.addWidget(btn_reset)

        right_layout.addWidget(grp_adj)

        grp_filter = QGroupBox("Visual Filter")
        fl_layout = QHBoxLayout(grp_filter)
        fl_layout.addWidget(QLabel("Filter:"))
        self._filter_combo = QComboBox()
        _FILTERS = [
            ("None", "none"), ("Greyscale", "greyscale"), ("Sepia", "sepia"),
            ("Invert", "invert"), ("Sharpen", "sharpen"), ("Blur", "blur"),
            ("Edge Enhance", "edge_enhance"), ("Emboss", "emboss"),
            ("Vignette", "vignette"),
        ]
        for label, key in _FILTERS:
            self._filter_combo.addItem(label, userData=key)
        self._filter_combo.currentIndexChanged.connect(self._refresh_preview_adjustments)
        fl_layout.addWidget(self._filter_combo, 1)
        right_layout.addWidget(grp_filter)

        grp_export = QGroupBox("Export")
        ex_vl = QVBoxLayout(grp_export)
        ex_vl.setSpacing(8)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._export_fmt_combo = QComboBox()
        self._export_fmt_combo.addItem("Animated GIF (.gif)", userData="gif")
        if self._mp4_export_available:
            self._export_fmt_combo.addItem("MP4 Video (.mp4)", userData="mp4")
        self._export_fmt_combo.currentIndexChanged.connect(self._update_export_summary)
        fmt_row.addWidget(self._export_fmt_combo, 1)
        ex_vl.addLayout(fmt_row)

        self._export_size_lbl = QLabel("Canvas: auto once clips are added")
        self._export_size_lbl.setWordWrap(True)
        self._export_size_lbl.setStyleSheet("color: gray; font-size: 11px;")
        ex_vl.addWidget(self._export_size_lbl)

        self._export_pad_lbl = QLabel(
            "Mixed-size clips are resized to fit and centred automatically."
        )
        self._export_pad_lbl.setWordWrap(True)
        self._export_pad_lbl.setStyleSheet("color: gray; font-size: 11px;")
        ex_vl.addWidget(self._export_pad_lbl)

        self._audio_group = QGroupBox("Audio (MP4 export only)")
        audio_vl = QVBoxLayout(self._audio_group)
        audio_vl.setSpacing(6)

        self._audio_scope_lbl = QLabel(
            "These controls only affect exported MP4 audio. Preview playback stays silent."
        )
        self._audio_scope_lbl.setWordWrap(True)
        self._audio_scope_lbl.setStyleSheet("color: gray; font-size: 11px;")
        audio_vl.addWidget(self._audio_scope_lbl)

        self._audio_enable_check = QCheckBox("Keep source audio in MP4 export")
        self._audio_enable_check.setChecked(True)
        self._audio_enable_check.setToolTip(
            "When enabled, MP4 export keeps audio from source video clips where available.\n"
            "Still-image and GIF sections stay silent."
        )
        self._audio_enable_check.toggled.connect(self._update_audio_controls)
        audio_vl.addWidget(self._audio_enable_check)

        self._audio_mute_check = QCheckBox("Mute exported audio")
        self._audio_mute_check.setToolTip("Disable audio in the exported MP4 without changing the picture.")
        self._audio_mute_check.toggled.connect(self._update_audio_controls)
        audio_vl.addWidget(self._audio_mute_check)

        audio_volume_row = QHBoxLayout()
        audio_volume_row.addWidget(QLabel("Volume:"))
        self._audio_volume_slider = _make_hslider(0, 200, 100)
        self._audio_volume_slider.setToolTip(
            "Adjust MP4 audio loudness.\n100% = original volume, 0% = silent, 200% = twice as loud."
        )
        self._audio_volume_slider.valueChanged.connect(
            lambda v: self._audio_volume_lbl.setText(f"{v}%")
        )
        self._audio_volume_slider.valueChanged.connect(lambda _v: self._update_audio_controls())
        audio_volume_row.addWidget(self._audio_volume_slider, 1)
        self._audio_volume_lbl = QLabel("100%")
        self._audio_volume_lbl.setFixedWidth(52)
        self._audio_volume_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        audio_volume_row.addWidget(self._audio_volume_lbl)
        audio_vl.addLayout(audio_volume_row)

        self._audio_hint_lbl = QLabel("")
        self._audio_hint_lbl.setWordWrap(True)
        self._audio_hint_lbl.setStyleSheet("color: gray; font-size: 11px;")
        audio_vl.addWidget(self._audio_hint_lbl)

        ex_vl.addWidget(self._audio_group)

        self._btn_export = QPushButton("💾  Export…")
        self._btn_export.setToolTip("Render and export.  Shortcut: Ctrl+S")
        self._btn_export.setMinimumHeight(34)
        self._btn_export.clicked.connect(self._export)
        ex_vl.addWidget(self._btn_export)

        right_layout.addWidget(grp_export)
        right_layout.addStretch()

        splitter.addWidget(right_scroll)
        splitter.setSizes([240, 420, 260])
        self._update_audio_controls()
        self._refresh_stream_controls()
        self._update_ui_state()

    def _resolve_tooltip_mgr(self):
        if self._tooltip_mgr is not None:
            return self._tooltip_mgr
        parent = self.parentWidget()
        while parent is not None:
            mgr = getattr(parent, "_tooltip_mgr", None)
            if mgr is not None:
                self._tooltip_mgr = mgr
                return mgr
            parent = parent.parentWidget()
        return None

    @classmethod
    def shortcut_definitions(cls) -> tuple[tuple[str, str, str, str], ...]:
        return cls.SHORTCUT_DEFS

    def _resolve_settings(self):
        parent = self.parentWidget()
        while parent is not None:
            settings = getattr(parent, "_settings", None)
            if settings is not None:
                return settings
            parent = parent.parentWidget()
        return None

    def _setup_shortcuts(self) -> None:
        self._shortcut_objects: dict[str, QShortcut] = {}
        self._bind_shortcut("video_remove_selected", "Delete", self._remove_selected)
        self._bind_shortcut("video_toggle_play", "Space", self._toggle_play)
        self._bind_shortcut("video_export", "Ctrl+S", self._export)
        self._bind_shortcut("video_split_clip", "Ctrl+E", self._split_clip_at_playhead)

    def update_shortcut_binding(self, shortcut_id: str, key_sequence: str) -> None:
        shortcut = getattr(self, "_shortcut_objects", {}).get(shortcut_id)
        if shortcut is not None:
            shortcut.setKey(QKeySequence(key_sequence))

    def _bind_shortcut(self, shortcut_id: str, default: str, slot) -> None:
        settings = self._resolve_settings()
        key_sequence = default
        if settings is not None:
            key_sequence = settings.get_shortcut_binding(shortcut_id, default)
        shortcut = QShortcut(QKeySequence(key_sequence), self)
        shortcut.activated.connect(slot)
        self._shortcut_objects[shortcut_id] = shortcut

    def register_tooltips(self, mgr) -> None:
        """Register dialog widgets with the shared TooltipManager."""
        self._tooltip_mgr = mgr
        mgr.register(self._btn_add_video, "video_media_add")
        mgr.register(self._btn_add_img, "video_media_add")
        mgr.register(self._btn_remove, "video_timeline")
        mgr.register(self._btn_split, "video_timeline")
        mgr.register(self._insert_mode_combo, "video_timeline")
        mgr.register(self._clip_list, "video_timeline")
        mgr.register(self._trim_start_slider, "video_trim")
        mgr.register(self._trim_end_slider, "video_trim")
        mgr.register(self._clip_info_lbl, "video_trim")
        mgr.register(self._clip_speed_slider, "video_trim")
        mgr.register(self._still_duration_spin, "video_trim")
        mgr.register(self._timing_hint_lbl, "video_trim")
        mgr.register(self._preview_lbl, "video_preview")
        mgr.register(self._scrubber, "video_preview")
        mgr.register(self._btn_rewind, "video_preview")
        mgr.register(self._btn_play, "video_preview")
        mgr.register(self._fps_slider, "video_preview")
        mgr.register(self._filter_combo, "video_filter")
        mgr.register(self._export_fmt_combo, "video_export")
        mgr.register(self._audio_enable_check, "video_export")
        mgr.register(self._audio_mute_check, "video_export")
        mgr.register(self._audio_volume_slider, "video_export")
        mgr.register(self._audio_hint_lbl, "video_export")
        mgr.register(self._btn_export, "video_export")

    # ------------------------------------------------------------------
    # Clip management
    # ------------------------------------------------------------------

    def _next_insert_row(self) -> int:
        row = self._clip_list.currentRow()
        mode = self._insert_mode_combo.currentData() or "after"
        if mode == "end" or row < 0:
            return len(self._clips)
        if mode == "before":
            return max(0, row)
        return row + 1

    def _insert_clip(self, clip: "_ClipEntry", label: str, row: Optional[int] = None) -> int:
        insert_row = len(self._clips) if row is None else max(0, min(len(self._clips), row))
        self._clips.insert(insert_row, clip)
        item = QListWidgetItem(label)
        item.setData(_CLIP_ROLE, clip)
        self._clip_list.insertItem(insert_row, item)
        self._clip_list.setCurrentRow(insert_row)
        return insert_row + 1

    def _set_import_status(self, message: str, *, detail: str = "", tone: str = "neutral") -> None:
        colors = {
            "neutral": "gray",
            "success": "#2e7d32",
            "warning": "#b26a00",
            "error": "#b00020",
        }
        self._import_status_lbl.setText(message)
        self._import_status_lbl.setStyleSheet(f"color: {colors.get(tone, 'gray')}; font-size: 11px;")
        self._import_status_lbl.setToolTip(detail or message)
        self._import_detail_box.setPlainText(detail)
        self._import_detail_box.setVisible(bool(detail.strip()))
        self.queue_status_changed.emit(self.get_queue_status_text())

    def _update_import_status(
        self,
        *,
        added: int,
        attempted: int,
        recovered: list[tuple[str, str]],
        failures: list[tuple[str, str]],
        skipped: list[str],
    ) -> None:
        if attempted <= 0:
            self._set_import_status(
                "Ready: add videos, images, or animated GIFs. Recovery notes, failure groups, and skipped-file details will appear here."
            )
            return
        parts = [f"Added {added} clip{'s' if added != 1 else ''}"]
        if recovered:
            recovery_summary = _summarize_recovery_notes(recovered)
            parts.append(f"{len(recovered)} recovered")
            if recovery_summary:
                parts.append(recovery_summary)
        if failures:
            grouped: dict[str, int] = {}
            for name, detail in failures:
                category = _classify_video_import_failure(name, detail)
                grouped[category] = grouped.get(category, 0) + 1
            parts.append(f"{len(failures)} failed")
            parts.append(_summarize_count_buckets(grouped, limit=2))
        if skipped:
            parts.append(f"{len(skipped)} skipped")
        tone = "success" if added and not failures and not skipped else "warning" if added else "error"
        detail_lines = []
        if recovered:
            detail_lines.append(
                "Recovery paths: " + _summarize_recovery_notes(recovered)
            )
            detail_lines.append(
                "Recovery fallbacks used:\n  "
                + "\n  ".join(f"{name}: {note}" for name, note in recovered)
                + "\nOriginal source paths stay attached for labeling and export history."
            )
        if failures:
            failure_containers, failure_video_codecs, failure_audio_codecs = _summarize_failure_probe_fields(failures)
            detail_lines.append(
                "Failure types: "
                + ", ".join(f"{category} ×{count}" for category, count in grouped.items())
            )
            if failure_containers:
                detail_lines.append("Probe-detected containers: " + _summarize_count_buckets(failure_containers))
            if failure_video_codecs:
                detail_lines.append("Probe-detected video codecs: " + _summarize_count_buckets(failure_video_codecs))
            if failure_audio_codecs:
                detail_lines.append("Probe-detected audio codecs: " + _summarize_count_buckets(failure_audio_codecs))
            detail_lines.append(
                "Failure guidance:\n  "
                + "\n  ".join(f"{category}: {_video_failure_guidance(category)}" for category in grouped)
            )
            failure_lines = [f"{name}: {hint}" for name, hint in failures[:_MAX_VIDEO_LOAD_FAILURE_DETAILS]]
            if len(failures) > len(failure_lines):
                failure_lines.append(f"…and {len(failures) - len(failure_lines)} more file(s).")
            detail_lines.append("Load failures:\n  " + "\n  ".join(failure_lines))
        if skipped:
            detail_lines.append("Skipped unsupported files:\n  " + "\n  ".join(skipped))
        summary = "Import summary: " + "  •  ".join(parts)
        self._set_import_status(summary, detail="\n\n".join(detail_lines), tone=tone)
        self.status_notice.emit(f"Video Builder: {summary}", 7000)

    def _format_clip_info_text(self, clip: "_ClipEntry") -> str:
        text = (
            f"{clip.total_frames} total  •  {clip.active_frames} timeline  •  "
            f"{clip.fps:.1f} fps"
            + (f"  •  {clip.frame_size[0]}×{clip.frame_size[1]}" if clip.frame_size else "")
        )
        if clip.load_note:
            text += f"  •  {clip.load_note}"
        return text

    def _set_stream_controls_state(
        self,
        summary: str,
        hint: str,
        *,
        enable_picker: bool = False,
        enable_apply: bool = False,
    ) -> None:
        self._stream_picker_combo.blockSignals(True)
        self._stream_picker_combo.clear()
        self._stream_picker_combo.addItem("Auto")
        self._stream_picker_combo.setEnabled(enable_picker)
        self._stream_picker_combo.blockSignals(False)
        self._audio_stream_picker_combo.blockSignals(True)
        self._audio_stream_picker_combo.clear()
        self._audio_stream_picker_combo.addItem("Auto")
        self._audio_stream_picker_combo.setEnabled(enable_picker)
        self._audio_stream_picker_combo.blockSignals(False)
        self._btn_apply_stream.setEnabled(enable_apply)
        self._stream_summary_lbl.setText(summary)
        self._stream_hint_lbl.setText(hint)
        self._stream_group.setEnabled(enable_picker or enable_apply)

    def _refresh_stream_controls(self, row: Optional[int] = None) -> None:
        row = self._clip_list.currentRow() if row is None else row
        if row < 0 or row >= len(self._clips):
            self._set_stream_controls_state(
                "Select a loaded video clip to inspect its available video/audio streams.",
                "Audio track selection becomes selectable here only after ffprobe inspects a loaded multi-stream clip.",
            )
            return
        clip = self._clips[row]
        if clip.clip_type != "video":
            self._set_stream_controls_state(
                "Still images and GIF clips only expose a single visual source frame sequence.",
                "Manual stream selection applies only to imported video clips.",
            )
            return
        details = clip.source_probe if isinstance(clip.source_probe, dict) else None
        if details is None:
            details = _probe_media_details(
                clip.source_path,
                preferred_video_stream_index=clip.preferred_video_stream_index,
                preferred_audio_stream_index=clip.preferred_audio_stream_index,
            )
        if not details:
            self._set_stream_controls_state(
                "ffprobe details are unavailable for this clip, so manual stream selection cannot be offered here.",
                "Install or bundle ffprobe to inspect multi-stream containers inside the Video Builder.",
            )
            return
        clip.source_probe = details
        video_choices = details.get("video_stream_choices")
        if not isinstance(video_choices, list):
            video_choices = []
        video_count = max(0, int(details.get("video_stream_count") or 0))
        audio_count = max(0, int(details.get("audio_stream_count") or 0))
        auto_index = _coerce_optional_stream_index(details.get("video_stream_index"))
        auto_audio_index = _coerce_optional_stream_index(details.get("audio_stream_index"))
        auto_choice = next(
            (
                choice for choice in video_choices
                if isinstance(choice, dict) and _coerce_optional_stream_index(choice.get("index")) == auto_index
            ),
            None,
        )
        auto_label = "Auto"
        auto_desc = _describe_stream_choice(auto_choice)
        if auto_desc:
            auto_label = f"Auto — {auto_desc}"
        elif auto_index is not None:
            auto_label = f"Auto — stream #{auto_index}"
        self._stream_picker_combo.blockSignals(True)
        self._stream_picker_combo.clear()
        self._stream_picker_combo.addItem(auto_label, userData=None)
        for choice in video_choices:
            if not isinstance(choice, dict):
                continue
            index = _coerce_optional_stream_index(choice.get("index"))
            if index is None:
                continue
            label = _describe_stream_choice(choice) or f"stream #{index}"
            self._stream_picker_combo.addItem(label if label.lower().startswith("stream #") else f"Stream #{index} — {label}", userData=index)
        target_index = clip.preferred_video_stream_index
        match = 0
        for combo_index in range(self._stream_picker_combo.count()):
            if _coerce_optional_stream_index(self._stream_picker_combo.itemData(combo_index)) == target_index:
                match = combo_index
                break
        self._stream_picker_combo.setCurrentIndex(match)
        self._stream_picker_combo.blockSignals(False)
        audio_choices = details.get("audio_stream_choices")
        if not isinstance(audio_choices, list):
            audio_choices = []
        auto_audio_choice = next(
            (
                choice for choice in audio_choices
                if isinstance(choice, dict) and _coerce_optional_stream_index(choice.get("index")) == auto_audio_index
            ),
            None,
        )
        auto_audio_label = "Auto"
        auto_audio_desc = _describe_stream_choice(auto_audio_choice)
        if auto_audio_desc:
            auto_audio_label = f"Auto — {auto_audio_desc}"
        elif auto_audio_index is not None:
            auto_audio_label = f"Auto — stream #{auto_audio_index}"
        self._audio_stream_picker_combo.blockSignals(True)
        self._audio_stream_picker_combo.clear()
        self._audio_stream_picker_combo.addItem(auto_audio_label, userData=None)
        for choice in audio_choices:
            if not isinstance(choice, dict):
                continue
            index = _coerce_optional_stream_index(choice.get("index"))
            if index is None:
                continue
            label = _describe_stream_choice(choice) or f"stream #{index}"
            self._audio_stream_picker_combo.addItem(label if label.lower().startswith("stream #") else f"Stream #{index} — {label}", userData=index)
        target_audio_index = clip.preferred_audio_stream_index
        audio_match = 0
        for combo_index in range(self._audio_stream_picker_combo.count()):
            if _coerce_optional_stream_index(self._audio_stream_picker_combo.itemData(combo_index)) == target_audio_index:
                audio_match = combo_index
                break
        self._audio_stream_picker_combo.setCurrentIndex(audio_match)
        self._audio_stream_picker_combo.blockSignals(False)
        enable_video_picker = video_count > 1
        enable_audio_picker = audio_count > 1
        selected_label = _stream_selection_note(details, manual=target_index is not None)
        selected_audio_label = _audio_stream_selection_note(details, manual=target_audio_index is not None)
        summary_parts = []
        if video_count > 1:
            summary_parts.append(f"{video_count} video streams detected.")
            if selected_label:
                summary_parts.append(f"Video override: {selected_label}.")
            elif auto_desc:
                summary_parts.append(f"Auto video currently prefers {auto_desc}.")
        else:
            summary_parts.append("This clip exposes a single detected video stream.")
        if audio_count > 1:
            summary_parts.append(f"{audio_count} audio streams detected.")
            if selected_audio_label:
                summary_parts.append(f"Audio override: {selected_audio_label}.")
            elif auto_audio_desc:
                summary_parts.append(f"Auto audio currently prefers {auto_audio_desc}.")
        else:
            summary_parts.append("A single audio stream is currently selected automatically." if bool(details.get("has_audio")) else "No source audio stream was detected for this clip.")
        summary = " ".join(summary_parts)
        hint_parts = []
        if audio_count > 1:
            hint_parts.append("Audio overrides are preserved on reload and export history when possible.")
        else:
            hint_parts.append("Auto keeps the detected audio stream unless you choose an explicit override.")
        if bool(details.get("selected_video_attached_pic")):
            hint_parts.append("The currently selected stream looks like cover art, so still-frame fallback may be the only usable path.")
        elif int(details.get("video_attached_pic_count") or 0) > 0:
            hint_parts.append("Cover-art style streams were also detected alongside the playable video choices.")
        self._stream_summary_lbl.setText(summary)
        self._stream_hint_lbl.setText(" ".join(hint_parts))
        self._stream_picker_combo.setEnabled(enable_video_picker)
        self._audio_stream_picker_combo.setEnabled(enable_audio_picker)
        self._btn_apply_stream.setEnabled(
            (enable_video_picker and target_index != _coerce_optional_stream_index(self._stream_picker_combo.currentData()))
            or (enable_audio_picker and target_audio_index != _coerce_optional_stream_index(self._audio_stream_picker_combo.currentData()))
        )
        self._stream_group.setEnabled(enable_video_picker or enable_audio_picker)

    def _on_stream_picker_changed(self, _index: int) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips):
            self._btn_apply_stream.setEnabled(False)
            return
        clip = self._clips[row]
        if clip.clip_type != "video":
            self._btn_apply_stream.setEnabled(False)
            return
        self._btn_apply_stream.setEnabled(
            (
                self._stream_picker_combo.isEnabled()
                and clip.preferred_video_stream_index != _coerce_optional_stream_index(self._stream_picker_combo.currentData())
            )
            or (
                self._audio_stream_picker_combo.isEnabled()
                and clip.preferred_audio_stream_index != _coerce_optional_stream_index(self._audio_stream_picker_combo.currentData())
            )
        )

    def _apply_selected_stream_choice(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips):
            return
        clip = self._clips[row]
        if clip.clip_type != "video":
            return
        selected_index = _coerce_optional_stream_index(self._stream_picker_combo.currentData())
        selected_audio_index = _coerce_optional_stream_index(self._audio_stream_picker_combo.currentData())
        if (
            selected_index == clip.preferred_video_stream_index
            and selected_audio_index == clip.preferred_audio_stream_index
        ):
            self._btn_apply_stream.setEnabled(False)
            return
        new_clip = self._reload_clip(
            clip,
            preferred_video_stream_index=selected_index,
            preferred_audio_stream_index=selected_audio_index,
        )
        if new_clip is None:
            detail = _video_load_failure_hint(
                clip.source_path,
                preferred_video_stream_index=selected_index,
                preferred_audio_stream_index=selected_audio_index,
            )
            selected_text = "auto stream choice" if selected_index is None else f"video stream #{selected_index}"
            selected_audio_text = "auto audio choice" if selected_audio_index is None else f"audio stream #{selected_audio_index}"
            self._set_import_status(
                f"Stream reload failed for {Path(clip.source_path).name} ({selected_text}; {selected_audio_text}).",
                detail=detail,
                tone="error",
            )
            self.status_notice.emit(f"Video Builder: stream reload failed for {Path(clip.source_path).name}", 7000)
            self._refresh_stream_controls(row)
            return
        old_clip = clip
        self._clips[row] = new_clip
        item = self._clip_list.item(row)
        if item is not None:
            item.setData(_CLIP_ROLE, new_clip)
        self._refresh_clip_item(row)
        try:
            old_clip.close()
        except Exception:
            pass
        self._clip_list.setCurrentRow(row)
        self._update_scrubber()
        self._update_preview()
        self._update_ui_state()
        self._refresh_stream_controls(row)
        selected_text = _stream_selection_note(new_clip.source_probe, manual=selected_index is not None)
        if not selected_text:
            selected_text = "auto video choice restored" if selected_index is None else f"manual video #{selected_index}"
        selected_audio_text = _audio_stream_selection_note(new_clip.source_probe, manual=selected_audio_index is not None)
        if not selected_audio_text:
            selected_audio_text = "auto audio choice restored" if selected_audio_index is None else f"manual audio #{selected_audio_index}"
        detail_lines = [f"Reloaded from {selected_text}.", f"Audio selection: {selected_audio_text}."]
        probe_summary = _format_media_probe_summary(new_clip.source_probe)
        if probe_summary:
            detail_lines.append(probe_summary)
        if new_clip.load_note:
            detail_lines.append(f"Load note: {new_clip.load_note}")
        self._set_import_status(
            f"Reloaded {Path(new_clip.source_path).name} from {selected_text} with {selected_audio_text}.",
            detail="\n\n".join(detail_lines),
            tone="success",
        )
        self.status_notice.emit(f"Video Builder: reloaded {Path(new_clip.source_path).name} from {selected_text}", 7000)

    def _update_timeline_summary(self) -> None:
        total_frames = self._total_preview_frames()
        fps = max(0.1, float(self._fps_slider.value()))
        seconds = total_frames / fps if total_frames else 0.0
        recovered = sum(1 for clip in self._clips if getattr(clip, "load_note", ""))
        extra = f"  •  {recovered} recovery fallback{'s' if recovered != 1 else ''}" if recovered else ""
        self._timeline_summary_lbl.setText(
            f"Timeline: {len(self._clips)} clip{'s' if len(self._clips) != 1 else ''}  •  "
            f"{seconds:.2f} s  •  {total_frames} frames{extra}"
        )
        self.queue_status_changed.emit(self.get_queue_status_text())

    def get_queue_status_text(self) -> str:
        if not self._clips:
            return "🎬 Video Builder ready"
        summary = self._timeline_summary_lbl.text().strip()
        if summary.lower().startswith("timeline:"):
            summary = summary.split(":", 1)[1].strip()
        return "🎬 Video Builder: " + summary

    def _status_bar_import_summary(self) -> str:
        text = self._import_status_lbl.text().strip()
        if not text or text.lower().startswith("ready:"):
            return ""
        if text.startswith("Import summary: "):
            return "import " + text[len("Import summary: "):]
        return text

    def get_status_bar_text(self) -> str:
        import_summary = self._status_bar_import_summary()
        if not self._clips:
            mode = "video + MP4 ready" if self._video_io_available else "image/GIF mode"
            summary = f"🎬 Video Builder ready  •  {mode}"
            if import_summary:
                summary += f"  •  {import_summary}"
            return summary
        summary = self.get_queue_status_text()
        extras = []
        preview = self._pos_lbl.text().strip()
        if preview and preview != "0 / 0":
            extras.append(f"preview {preview}")
        if self._is_playing:
            extras.append("playing")
        if import_summary:
            extras.append(import_summary)
        if extras:
            summary += "  •  " + "  •  ".join(extras)
        return summary

    def _update_ui_state(self) -> None:
        has_clips = bool(self._clips)
        row = self._clip_list.currentRow()
        has_selection = 0 <= row < len(self._clips)

        if not has_clips:
            if self._is_playing:
                self._preview_timer.stop()
                self._is_playing = False
            self._btn_play.blockSignals(True)
            self._btn_play.setChecked(False)
            self._btn_play.blockSignals(False)
            self._btn_play.setText("▶  Play")

        self._btn_remove.setEnabled(has_selection)
        self._btn_rewind.setEnabled(has_clips)
        self._btn_play.setEnabled(has_clips)
        self._scrubber.setEnabled(has_clips)
        self._btn_export.setEnabled(has_clips)
        self._trim_start_slider.setEnabled(has_selection)
        self._trim_end_slider.setEnabled(has_selection)
        if not has_selection:
            self._trim_start_lbl.setText("0")
            self._trim_end_lbl.setText("0")
            self._clip_info_lbl.setText("Select a clip to adjust trim and timing.")

        self._update_timeline_summary()
        self._update_audio_controls()

    def _refresh_clip_item(self, row: int) -> None:
        if row < 0 or row >= len(self._clips):
            return
        clip = self._clips[row]
        item = self._clip_list.item(row)
        if item is None:
            return
        icon = "🎞" if clip.clip_type == "video" else "🖼"
        item.setText(_format_clip_label(clip, clip.source_path, icon))

    def _reload_clip(
        self,
        clip: "_ClipEntry",
        *,
        preferred_video_stream_index=_KEEP_STREAM_SELECTION,
        preferred_audio_stream_index=_KEEP_STREAM_SELECTION,
    ) -> Optional["_ClipEntry"]:
        if clip.clip_type == "video":
            video_index = (
                clip.preferred_video_stream_index
                if preferred_video_stream_index is _KEEP_STREAM_SELECTION
                else preferred_video_stream_index
            )
            audio_index = (
                clip.preferred_audio_stream_index
                if preferred_audio_stream_index is _KEEP_STREAM_SELECTION
                else preferred_audio_stream_index
            )
            new_clip = _load_video_clip(
                clip.source_path,
                preferred_video_stream_index=video_index,
                preferred_audio_stream_index=audio_index,
            )
        else:
            new_clip = _load_image_as_clip(clip.source_path)
        if new_clip is None:
            return None
        new_clip.speed_percent = clip.speed_percent
        new_clip.still_duration_frames = clip.still_duration_frames
        new_clip.trim_start = max(0, min(clip.trim_start, new_clip.total_frames - 1))
        new_clip.trim_end = max(new_clip.trim_start, min(clip.trim_end, new_clip.total_frames - 1))
        return new_clip

    def _on_files_dropped(self, paths: list[str], insert_row: int) -> None:
        skipped = []
        fallback_loaded: list[tuple[str, str]] = []
        failed_videos: list[tuple[str, str]] = []
        next_row = max(0, min(len(self._clips), insert_row))
        added = 0
        for path in paths:
            ext = Path(path).suffix.lower()
            if ext in _VIDEO_EXTS:
                clip = _load_video_clip(path)
                if clip is None:
                    failed_videos.append((Path(path).name, _video_load_failure_hint(path)))
                    continue
                label = _format_clip_label(clip, path, "🎞")
                next_row = self._insert_clip(clip, label, next_row)
                added += 1
                if clip.load_note:
                    fallback_loaded.append((Path(path).name, clip.load_note))
            elif ext in _IMAGE_EXTS:
                clip = _load_image_as_clip(path)
                if clip is None:
                    skipped.append(Path(path).name)
                    continue
                label = _format_clip_label(clip, path, "🖼")
                next_row = self._insert_clip(clip, label, next_row)
                added += 1
            else:
                probe = _probe_media_details(path)
                if _is_probably_video_source(path, probe):
                    clip = _load_video_clip(path)
                    if clip is None:
                        failed_videos.append((Path(path).name, _video_load_failure_hint(path)))
                        continue
                    label = _format_clip_label(clip, path, "🎞")
                    next_row = self._insert_clip(clip, label, next_row)
                    added += 1
                    if clip.load_note:
                        fallback_loaded.append((Path(path).name, clip.load_note))
                elif probe:
                    failed_videos.append((Path(path).name, _video_load_failure_hint(path)))
                else:
                    skipped.append(Path(path).name)
        self._update_scrubber()
        self._update_preview()
        self._update_ui_state()
        self._update_import_status(
            added=added,
            attempted=len(paths),
            recovered=fallback_loaded,
            failures=failed_videos,
            skipped=skipped,
        )

    def _add_video(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Video / Odd-Container Files", "",
            _format_extension_filter("Video Files", _VIDEO_EXTS),
        )
        self._load_video_paths(paths, insert_row=self._next_insert_row())

    def _load_video_paths(self, paths: list[str], insert_row: Optional[int] = None) -> None:
        next_row = len(self._clips) if insert_row is None else max(0, min(len(self._clips), insert_row))
        fallback_loaded: list[tuple[str, str]] = []
        failed_videos: list[tuple[str, str]] = []
        skipped: list[str] = []
        added = 0
        for path in paths:
            ext = Path(path).suffix.lower()
            if ext in _IMAGE_EXTS:
                skipped.append(Path(path).name)
                continue
            probe = None
            if ext not in _VIDEO_EXTS:
                probe = _probe_media_details(path)
                if not _is_probably_video_source(path, probe):
                    if probe:
                        failed_videos.append((Path(path).name, _video_load_failure_hint(path)))
                    else:
                        skipped.append(Path(path).name)
                    continue
            clip = _load_video_clip(path)
            if clip is None:
                failed_videos.append((Path(path).name, _video_load_failure_hint(path)))
                continue
            label = _format_clip_label(clip, path, "🎞")
            next_row = self._insert_clip(clip, label, next_row)
            added += 1
            if clip.load_note:
                fallback_loaded.append((Path(path).name, clip.load_note))
        self._update_scrubber()
        self._update_preview()
        self._update_ui_state()
        self._update_import_status(
            added=added,
            attempted=len(paths),
            recovered=fallback_loaded,
            failures=failed_videos,
            skipped=skipped,
        )

    def _record_export_history(
        self,
        out_path: str,
        fmt: str,
        clip_snapshot: list[dict[str, object]],
        success: int,
        errors: int,
        *,
        canvas_size: Optional[tuple[int, int]] = None,
        audio_mode_override: Optional[str] = None,
        extra_notes: Optional[list[str]] = None,
    ) -> None:
        settings = self._resolve_settings()
        if settings is None:
            return
        files = [os.path.basename(str(clip.get("source_path") or clip.get("path") or "")) for clip in clip_snapshot]
        entry = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "output": out_path,
            "format": fmt.upper(),
            "clip_count": len(clip_snapshot),
            "success": success,
            "errors": errors,
            "files": files,
            "first_file": str(clip_snapshot[0].get("source_path") or clip_snapshot[0].get("path") or "") if clip_snapshot else "",
            "sources": _summarize_clip_types(clip_snapshot),
            "filter": str(self._filter_combo.currentData() or "none"),
            "audio": audio_mode_override or ("kept" if self._should_mux_audio(fmt, clip_snapshot) else "off"),
            "fps": str(int(self._fps_slider.value())),
        }
        if canvas_size and len(canvas_size) == 2:
            try:
                width = max(0, int(canvas_size[0]))
                height = max(0, int(canvas_size[1]))
            except Exception:
                width = height = 0
            if width > 0 and height > 0:
                entry["canvas"] = f"{width}×{height}"
        recovered = [
            (
                os.path.basename(str(clip.get("source_path") or clip.get("path") or "")),
                str(clip.get("load_note") or "").strip(),
            )
            for clip in clip_snapshot
            if str(clip.get("load_note") or "").strip()
        ]
        entry["recovery"] = _summarize_recovery_notes(recovered)
        noted = [
            f"{os.path.basename(str(clip.get('source_path') or clip.get('path') or ''))}: {clip.get('load_note')}"
            for clip in clip_snapshot
            if str(clip.get("load_note") or "").strip()
        ]
        selected_streams = [
            detail for detail in (_clip_stream_history_detail(clip) for clip in clip_snapshot) if detail
        ]
        clip_details = [
            detail for detail in (_clip_history_detail(clip) for clip in clip_snapshot) if detail
        ]
        stream_summary = "manual" if selected_streams else ""
        if selected_streams:
            stream_summary = "; ".join(selected_streams[:3]) + (" …" if len(selected_streams) > 3 else "")
            entry["streams"] = stream_summary
        if clip_details:
            entry["clips"] = "; ".join(clip_details[:3]) + (" …" if len(clip_details) > 3 else "")
        notes = [
            f"filter={entry['filter']}",
            f"audio={entry['audio']}",
            f"fps={entry['fps']}",
        ]
        if entry.get("canvas"):
            notes.append(f"canvas={entry['canvas']}")
        if entry["sources"]:
            notes.append(f"sources={entry['sources']}")
        if entry["recovery"] and entry["recovery"] != "direct only":
            notes.append(f"recovery={entry['recovery']}")
        if clip_details:
            notes.append(f"clips={entry['clips']}")
        elif noted:
            notes.append("clips=" + ("; ".join(noted[:3]) + (" …" if len(noted) > 3 else "")))
        if stream_summary:
            notes.append("streams=" + stream_summary)
        if extra_notes:
            notes.extend(str(note).strip() for note in extra_notes if str(note).strip())
        if notes:
            entry["notes"] = " | ".join(notes)
        try:
            settings.add_video_builder_history(entry)
        except Exception:
            pass

    def _add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Images / GIFs", "",
            _format_extension_filter("Images", _IMAGE_EXTS),
        )
        self._load_image_paths(paths, insert_row=self._next_insert_row())

    def _load_image_paths(self, paths: list[str], insert_row: Optional[int] = None) -> None:
        skipped = []
        next_row = len(self._clips) if insert_row is None else max(0, min(len(self._clips), insert_row))
        added = 0
        for path in paths:
            clip = _load_image_as_clip(path)
            if clip is None:
                skipped.append(Path(path).name)
                continue
            label = _format_clip_label(clip, path, "🖼")
            next_row = self._insert_clip(clip, label, next_row)
            added += 1
        self._update_scrubber()
        self._update_preview()
        self._update_ui_state()
        self._update_import_status(
            added=added,
            attempted=len(paths),
            recovered=[],
            failures=[],
            skipped=skipped,
        )

    def _remove_selected(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips):
            return
        self._clips[row].close()
        del self._clips[row]
        self._clip_list.takeItem(row)
        if self._clip_list.count():
            self._clip_list.setCurrentRow(min(row, self._clip_list.count() - 1))
        else:
            self._clip_list.setCurrentRow(-1)
        self._update_scrubber()
        self._update_preview()
        self._update_ui_state()

    def _sync_clips_from_list(self) -> None:
        """Rebuild ``self._clips`` from current list-item order."""
        self._clips = []
        for i in range(self._clip_list.count()):
            clip = self._clip_list.item(i).data(_CLIP_ROLE)
            if clip is not None:
                self._clips.append(clip)
        self._update_scrubber()
        self._update_preview()
        self._update_ui_state()

    def _on_clip_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._clips):
            self._trim_start_slider.blockSignals(True)
            self._trim_end_slider.blockSignals(True)
            self._trim_start_slider.setRange(0, 0)
            self._trim_end_slider.setRange(0, 0)
            self._trim_start_slider.blockSignals(False)
            self._trim_end_slider.blockSignals(False)
            self._update_timing_controls()
            self._refresh_stream_controls(row)
            self._update_ui_state()
            return
        clip = self._clips[row]
        self._trim_start_slider.blockSignals(True)
        self._trim_end_slider.blockSignals(True)
        mx = max(0, clip.total_frames - 1)
        self._trim_start_slider.setRange(0, mx)
        self._trim_end_slider.setRange(0, mx)
        self._trim_start_slider.setValue(clip.trim_start)
        self._trim_end_slider.setValue(clip.trim_end)
        self._trim_start_lbl.setText(str(clip.trim_start))
        self._trim_end_lbl.setText(str(clip.trim_end))
        self._trim_start_slider.blockSignals(False)
        self._trim_end_slider.blockSignals(False)
        self._clip_info_lbl.setText(self._format_clip_info_text(clip))
        self._update_timing_controls()
        self._refresh_stream_controls(row)
        self._update_ui_state()

    def _on_trim_start_changed(self, val: int) -> None:
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_start = min(val, clip.trim_end)
            self._trim_start_slider.blockSignals(True)
            self._trim_start_slider.setValue(clip.trim_start)
            self._trim_start_slider.blockSignals(False)
            self._trim_start_lbl.setText(str(clip.trim_start))
            self._clip_info_lbl.setText(self._format_clip_info_text(clip))
            self._refresh_clip_item(row)
            self._update_timing_controls()
            self._update_scrubber()
            self._update_ui_state()
        else:
            self._trim_start_lbl.setText(str(val))

    def _on_trim_end_changed(self, val: int) -> None:
        row = self._clip_list.currentRow()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            clip.trim_end = max(val, clip.trim_start)
            self._trim_end_slider.blockSignals(True)
            self._trim_end_slider.setValue(clip.trim_end)
            self._trim_end_slider.blockSignals(False)
            self._trim_end_lbl.setText(str(clip.trim_end))
            self._clip_info_lbl.setText(self._format_clip_info_text(clip))
            self._refresh_clip_item(row)
            self._update_timing_controls()
            self._update_scrubber()
            self._update_ui_state()
        else:
            self._trim_end_lbl.setText(str(val))

    def _update_timing_controls(self) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips):
            self._clip_speed_slider.setEnabled(False)
            self._still_duration_spin.setEnabled(False)
            self._btn_split.setEnabled(False)
            self._clip_speed_lbl.setText("1.00×")
            self._still_duration_lbl.setText("0.00 s")
            self._timing_hint_lbl.setText("Select a clip to adjust its timing.")
            return
        clip = self._clips[row]
        fps = max(0.1, float(self._fps_slider.value()))
        self._clip_speed_slider.blockSignals(True)
        self._clip_speed_slider.setValue(max(10, min(400, int(clip.speed_percent))))
        self._clip_speed_slider.blockSignals(False)
        self._clip_speed_lbl.setText(f"{clip.speed_percent / 100:.2f}×")
        self._still_duration_spin.blockSignals(True)
        self._still_duration_spin.setValue(max(1, int(clip.still_duration_frames)))
        self._still_duration_spin.blockSignals(False)
        self._still_duration_lbl.setText(f"{clip.still_duration_frames / fps:.2f} s")
        is_still = clip.clip_type == "image"
        self._clip_speed_slider.setEnabled(not is_still)
        self._still_duration_spin.setEnabled(is_still)
        self._btn_split.setEnabled(not is_still and clip.base_active_frames > 1)
        if is_still:
            self._timing_hint_lbl.setText(
                "Still images stay on screen for the selected number of timeline frames."
            )
        else:
            self._timing_hint_lbl.setText(
                "Clip speed affects preview and export timing for the selected moving clip."
            )

    def _timeline_has_video_clips(self) -> bool:
        return any(clip.clip_type == "video" and clip.active_frames > 0 for clip in self._clips)

    def _timeline_has_detected_audio(self) -> bool:
        return any(
            clip.clip_type == "video"
            and clip.active_frames > 0
            and clip.has_audio
            for clip in self._clips
        )

    def _update_audio_controls(self) -> None:
        export_fmt = self._export_fmt_combo.currentData() if hasattr(self, "_export_fmt_combo") else "gif"
        is_mp4 = export_fmt == "mp4"
        has_video_clips = self._timeline_has_video_clips()
        has_audio_source = has_video_clips and self._timeline_has_detected_audio()
        audio_plan = _audio_source_plan(list(self._clips))
        audio_plan_hint = _audio_source_plan_hint(audio_plan)
        allow_audio_controls = is_mp4 and self._mp4_export_available and has_video_clips
        allow_audio_source_controls = allow_audio_controls and has_audio_source

        if hasattr(self, "_audio_group"):
            self._audio_group.setVisible(self._mp4_export_available and is_mp4)

        self._audio_enable_check.setEnabled(allow_audio_source_controls)
        audio_enabled = allow_audio_source_controls and self._audio_enable_check.isChecked()
        self._audio_mute_check.setEnabled(audio_enabled)
        volume_enabled = audio_enabled and not self._audio_mute_check.isChecked()
        self._audio_volume_slider.setEnabled(volume_enabled)
        self._audio_volume_lbl.setEnabled(volume_enabled)

        if not is_mp4:
            hint = "GIF export is always silent."
        elif not self._mp4_export_available:
            hint = "MP4 export audio controls are unavailable until the video backend is installed and working."
        elif not has_video_clips:
            hint = "Audio controls only apply when the timeline contains at least one video clip."
        elif not has_audio_source:
            hint = "No source audio stream was detected in the current video clips, so volume and mute controls stay disabled."
            if audio_plan_hint:
                hint += " " + audio_plan_hint
        elif not self._audio_enable_check.isChecked():
            hint = "MP4 export will stay silent until source audio is enabled."
            if audio_plan_hint:
                hint += " " + audio_plan_hint
        elif self._audio_mute_check.isChecked() or self._audio_volume_slider.value() <= 0:
            hint = "MP4 export will render without sound because audio is muted."
            if audio_plan_hint:
                hint += " " + audio_plan_hint
        else:
            hint = (
                "Source audio is trimmed, time-matched, and volume-adjusted per video clip. "
                "Still-image and GIF sections are filled with silence."
            )
            if audio_plan_hint:
                hint += " " + audio_plan_hint
        self._audio_hint_lbl.setText(hint)

    def _on_clip_speed_changed(self, value: int) -> None:
        row = self._clip_list.currentRow()
        if row < 0 or row >= len(self._clips):
            self._clip_speed_lbl.setText(f"{value / 100:.2f}×")
            return
        clip = self._clips[row]
        if clip.clip_type == "image":
            return
        clip.speed_percent = max(10, min(400, int(value)))
        self._clip_speed_lbl.setText(f"{clip.speed_percent / 100:.2f}×")
        self._after_selected_clip_timing_changed(row)

    def _after_selected_clip_timing_changed(self, row: int) -> None:
        self._refresh_clip_item(row)
        self._update_scrubber()
        self._update_preview()
        self._on_clip_selected(row)
        self._update_ui_state()

    def _on_still_duration_changed(self, value: int) -> None:
        row = self._clip_list.currentRow()
        fps = max(0.1, float(self._fps_slider.value()))
        self._still_duration_lbl.setText(f"{max(1, int(value)) / fps:.2f} s")
        if row < 0 or row >= len(self._clips):
            return
        clip = self._clips[row]
        if clip.clip_type != "image":
            return
        clip.still_duration_frames = max(1, int(value))
        self._after_selected_clip_timing_changed(row)

    def _split_clip_at_playhead(self) -> None:
        total = self._total_preview_frames()
        if total <= 1 or not self._clips:
            QMessageBox.information(self, "Split Clip", "Load a multi-frame clip before splitting.")
            return
        clip_row, frame_idx = self._global_frame_to_clip(max(0, min(self._scrubber.value(), total - 1)))
        clip = self._clips[clip_row]
        if clip.clip_type == "image" or clip.base_active_frames <= 1:
            QMessageBox.information(
                self,
                "Split Clip",
                "Move the playhead onto a video or animated GIF clip with at least two frames.",
            )
            return
        second_half_offset = clip.split_second_half_offset(frame_idx)
        if second_half_offset is None:
            QMessageBox.information(
                self,
                "Split Clip",
                "Move the playhead earlier in the clip so there is room to split after the current frame.",
            )
            return
        original_trim_start = clip.trim_start
        original_trim_end = clip.trim_end
        second_half = self._reload_clip(clip)
        if second_half is None:
            QMessageBox.warning(self, "Split Clip", "Could not duplicate the selected clip for splitting.")
            return
        clip.trim_end = original_trim_start + second_half_offset - 1
        second_half.trim_start = original_trim_start + second_half_offset
        second_half.trim_end = original_trim_end
        insert_row = clip_row + 1
        self._clips.insert(insert_row, second_half)
        item = QListWidgetItem(_format_clip_label(second_half, second_half.path, "🎞" if second_half.clip_type == "video" else "🖼"))
        item.setData(_CLIP_ROLE, second_half)
        self._clip_list.insertItem(insert_row, item)
        self._refresh_clip_item(clip_row)
        self._clip_list.setCurrentRow(clip_row)
        self._on_clip_selected(clip_row)
        self._update_scrubber()
        self._update_preview()
        self._update_timing_controls()
        self._update_ui_state()

    # ------------------------------------------------------------------
    # Preview / transport
    # ------------------------------------------------------------------

    def _total_preview_frames(self) -> int:
        return sum(c.active_frames for c in self._clips)

    def _update_scrubber(self) -> None:
        total_frames = self._total_preview_frames()
        total = max(0, total_frames - 1)
        current = min(self._scrubber.value(), total)
        self._scrubber.blockSignals(True)
        self._scrubber.setRange(0, total)
        self._scrubber.setValue(current)
        self._scrubber.blockSignals(False)
        self._pos_lbl.setText(f"{(current + 1) if total_frames else 0} / {total_frames}")
        self._update_export_summary()

    def _global_frame_to_clip(self, global_idx: int):
        idx = global_idx
        for ci, clip in enumerate(self._clips):
            n = clip.active_frames
            if idx < n:
                return ci, idx
            idx -= n
        return max(0, len(self._clips) - 1), 0

    def _update_preview(self) -> None:
        total = self._total_preview_frames()
        if total == 0 or not self._clips:
            self._preview_lbl.setText("Add clips to preview and export.")
            self._pos_lbl.setText("0 / 0")
            self.queue_status_changed.emit(self.get_queue_status_text())
            return
        g = max(0, min(self._scrubber.value(), total - 1))
        ci, fi = self._global_frame_to_clip(g)
        source = None
        adjusted = None
        filtered = None
        try:
            source = self._clips[ci].get_frame(fi)
            adjusted = _apply_adjustments(
                source,
                brightness=self._brightness_slider.value() / 100.0,
                contrast=self._contrast_slider.value() / 100.0,
                black_point=self._black_slider.value(),
                white_point=self._white_slider.value(),
                saturation=self._saturation_slider.value() / 100.0,
                sharpness=self._sharpness_slider.value() / 100.0,
            )
            filter_key = self._filter_combo.currentData() or "none"
            filtered = _apply_filter(adjusted, filter_key)
            pix = _pil_to_pixmap(filtered).scaled(
                _PREVIEW_MAX_W, _PREVIEW_MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._preview_lbl.setPixmap(pix)
        except Exception as exc:
            self._preview_lbl.setText(f"(preview error: {exc})")
        finally:
            if filtered is not None and filtered is not adjusted:
                filtered.close()
            if adjusted is not None and adjusted is not source:
                adjusted.close()
            if source is not None:
                source.close()

        self._pos_lbl.setText(f"{g + 1} / {total}")
        self.queue_status_changed.emit(self.get_queue_status_text())

    def _refresh_preview_adjustments(self) -> None:
        self._update_preview()

    def _on_scrub(self, _value: int) -> None:
        self._update_preview()

    def _toggle_play(self) -> None:
        self._btn_play.setChecked(not self._btn_play.isChecked())

    def _on_play_toggled(self, playing: bool) -> None:
        self._is_playing = playing
        if playing:
            if self._total_preview_frames() == 0:
                self._btn_play.setChecked(False)
                return
            fps = max(0.1, float(self._fps_slider.value()))
            self._preview_timer.start(int(1000 / fps))
            self._btn_play.setText("⏸  Pause")
        else:
            self._preview_timer.stop()
            self._btn_play.setText("▶  Play")
        self.queue_status_changed.emit(self.get_queue_status_text())

    def _advance_preview(self) -> None:
        total = self._total_preview_frames()
        if total == 0:
            return
        nxt = (self._scrubber.value() + 1) % total
        self._scrubber.setValue(nxt)
        self._update_preview()

    def _rewind(self) -> None:
        self._scrubber.setValue(0)
        self._update_preview()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _snapshot_clip_render_state(self, clip: "_ClipEntry", output_fps: float) -> dict[str, object]:
        trim_start = clip.trim_start
        trim_end = clip.trim_end
        clip_type = clip.clip_type
        speed_percent = clip.speed_percent
        still_duration_frames = clip.still_duration_frames
        frame_size = clip.frame_size
        base_active_frames = max(0, trim_end - trim_start + 1)
        if clip_type == "image":
            active_frames = max(1, int(still_duration_frames))
        else:
            speed = max(0.1, speed_percent / 100.0)
            active_frames = max(1, int(round(base_active_frames / speed))) if base_active_frames > 0 else 0
        timeline_seconds = active_frames / max(0.1, output_fps) if active_frames > 0 else 0.0
        source_duration_seconds = base_active_frames / max(0.1, clip.fps) if base_active_frames > 0 else 0.0
        return {
            "frame_getter": clip._get_frame,
            "active_frames": active_frames,
            "frame_size": frame_size,
            "clip_type": clip_type,
            "path": clip.path,
            "source_path": clip.source_path,
            "trim_start": trim_start,
            "trim_end": trim_end,
            "clip_fps": clip.fps,
            "base_active_frames": base_active_frames,
            "speed_percent": speed_percent,
            "timeline_seconds": timeline_seconds,
            "source_duration_seconds": source_duration_seconds,
            "has_audio": clip_type == "video" and clip.has_audio,
            "load_note": clip.load_note,
            "source_probe": clip.source_probe,
            "preferred_video_stream_index": clip.preferred_video_stream_index,
            "preferred_audio_stream_index": clip.preferred_audio_stream_index,
        }

    def _get_snapshot_frame(self, clip: dict[str, object], output_idx: int):
        get_source_frame = clip["frame_getter"]
        trim_start = int(clip["trim_start"])
        clip_type = str(clip["clip_type"])
        base_active_frames = int(clip["base_active_frames"])
        if clip_type == "image" or base_active_frames <= 0:
            return get_source_frame(trim_start)
        speed = max(0.1, int(clip["speed_percent"]) / 100.0)
        mapped = int(output_idx * speed)
        source_offset = max(0, min(base_active_frames - 1, mapped))
        return get_source_frame(trim_start + source_offset)

    def _should_mux_audio(self, fmt: str, clip_snapshot: list[dict[str, object]]) -> bool:
        return (
            fmt == "mp4"
            and self._audio_enable_check.isEnabled()
            and self._audio_enable_check.isChecked()
            and not self._audio_mute_check.isChecked()
            and self._audio_volume_slider.value() > 0
            and any(bool(clip["has_audio"]) for clip in clip_snapshot)
        )

    def _mux_mp4_audio(
        self,
        silent_video_path: str,
        out_path: str,
        clip_snapshot: list[dict[str, object]],
        output_fps: float,
    ) -> None:
        ffmpeg_exe = _get_ffmpeg_exe()
        if not ffmpeg_exe:
            raise RuntimeError("FFmpeg is unavailable for MP4 audio export.")

        cmd = [ffmpeg_exe, "-y", "-v", "error", "-i", silent_video_path]
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        input_index = 1
        needs_silence = any(
            max(0, int(clip["active_frames"])) > 0
            and not (clip["clip_type"] == "video" and clip["has_audio"])
            for clip in clip_snapshot
        )
        silence_input_index = None
        if needs_silence:
            cmd.extend([
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            ])
            silence_input_index = input_index
            input_index += 1
        for clip_idx, clip in enumerate(clip_snapshot):
            active_frames = max(0, int(clip["active_frames"]))
            if active_frames <= 0:
                continue
            output_duration = float(clip["timeline_seconds"])
            label = f"a{clip_idx}"
            if clip["clip_type"] == "video" and clip["has_audio"]:
                cmd.extend(["-i", str(clip["path"])])
                trim_start = int(clip["trim_start"]) / max(0.1, float(clip["clip_fps"]))
                trim_end = (int(clip["trim_end"]) + 1) / max(0.1, float(clip["clip_fps"]))
                source_duration = max(0.001, trim_end - trim_start)
                duration_ratio = max(0.001, output_duration) / source_duration
                tempo_factor = max(0.01, 1.0 / duration_ratio)
                filters = [
                    f"[{input_index}:a]atrim=start={trim_start:.6f}:end={trim_end:.6f}",
                    "asetpts=PTS-STARTPTS",
                    *_build_atempo_filters(tempo_factor),
                ]
                filter_parts.append(",".join(filters) + f"[{label}]")
            else:
                if silence_input_index is None:
                    raise RuntimeError("FFmpeg silence source is unavailable for MP4 audio export.")
                filter_parts.append(
                    f"[{silence_input_index}:a]atrim=start=0:end={output_duration:.6f},asetpts=PTS-STARTPTS[{label}]"
                )
            concat_inputs.append(f"[{label}]")
            if clip["clip_type"] == "video" and clip["has_audio"]:
                input_index += 1

        if not concat_inputs:
            raise RuntimeError("No audio segments were available for MP4 export.")

        filter_parts.append(
            "".join(concat_inputs) + f"concat=n={len(concat_inputs)}:v=0:a=1[a_concat]"
        )
        volume = max(0.0, self._audio_volume_slider.value() / 100.0)
        output_label = "[a_concat]"
        if abs(volume - 1.0) > 0.0001:
            filter_parts.append(f"[a_concat]volume={volume:.3f}[a_out]")
            output_label = "[a_out]"
        total_duration = sum(max(0.0, float(clip["timeline_seconds"])) for clip in clip_snapshot)
        if total_duration > 0:
            filter_parts.append(
                f"{output_label}apad=whole_dur={total_duration:.6f},atrim=end={total_duration:.6f}[a_final]"
            )
            output_label = "[a_final]"

        cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", "0:v:0",
            "-map", output_label,
            "-c:v", "copy",
            "-c:a", "aac",
            out_path,
        ])

        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "FFmpeg audio mux failed.")

    def _export(self) -> None:
        output_fps = max(0.1, float(self._fps_slider.value()))
        clip_snapshot = [
            self._snapshot_clip_render_state(clip, output_fps)
            for clip in self._clips
            if clip.active_frames > 0
        ]
        total = sum(int(clip["active_frames"]) for clip in clip_snapshot)
        if total == 0:
            QMessageBox.information(self, "No Clips", "Add at least one clip or image first.")
            return

        def _global_frame_to_snapshot(global_idx: int) -> tuple[int, int]:
            idx = global_idx
            for clip_idx, clip in enumerate(clip_snapshot):
                active_frames = int(clip["active_frames"])
                if idx < active_frames:
                    return clip_idx, idx
                idx -= active_frames
            return max(0, len(clip_snapshot) - 1), 0

        fmt = self._export_fmt_combo.currentData() or "gif"
        fps = output_fps
        filter_key = self._filter_combo.currentData() or "none"
        canvas_size = self._timeline_canvas_size(fmt)
        if canvas_size is None:
            QMessageBox.warning(self, "Export Error", "Could not determine an output size.")
            return
        if fmt == "mp4" and not self._mp4_export_available:
            QMessageBox.warning(
                self,
                "MP4 Export Unavailable",
                "MP4 export requires imageio, imageio-ffmpeg, and a working ffmpeg executable. Animated GIF export is still available.\n\n"
                f"{self._video_io_diagnostics}",
            )
            return
        if fmt == "gif":
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Export as GIF", "output.gif",
                "GIF Files (*.gif);;All Files (*)",
            )
        else:
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Export as MP4", "output.mp4",
                "MP4 Files (*.mp4);;All Files (*)",
            )
        if not out_path:
            return
        target_suffix = ".gif" if fmt == "gif" else ".mp4"
        current_suffix = Path(out_path).suffix.lower()
        if current_suffix != target_suffix:
            known_media_suffixes = _KNOWN_MEDIA_SUFFIXES
            if current_suffix in known_media_suffixes:
                out_path = str(Path(out_path).with_suffix(target_suffix))
            else:
                out_path = f"{out_path}{target_suffix}"
        out_path_existed = Path(out_path).exists()

        progress = QProgressDialog("Rendering and saving output…", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.show()
        QApplication.processEvents()
        brightness = self._brightness_slider.value() / 100.0
        contrast = self._contrast_slider.value() / 100.0
        black_point = self._black_slider.value()
        white_point = self._white_slider.value()
        saturation = self._saturation_slider.value() / 100.0
        sharpness = self._sharpness_slider.value() / 100.0
        writer = None
        gif_frames = []
        canceled = False
        wrote_frames = False
        render_path = out_path
        temp_mp4 = None
        temp_output_path = None
        export_stage = "render setup"
        export_issue_count = 0
        history_audio_mode_override = None
        history_extra_notes: list[str] = []
        completion_note = ""
        if fmt == "mp4":
            history_extra_notes.extend(_audio_source_plan_history_notes(_audio_source_plan(clip_snapshot)))
        try:
            if fmt in {"gif", "mp4"}:
                output_file = tempfile.NamedTemporaryFile(
                    prefix="alpha_fixer_export_",
                    suffix=target_suffix,
                    delete=False,
                )
                temp_output_path = output_file.name
                output_file.close()
            if fmt != "gif":
                if self._should_mux_audio(fmt, clip_snapshot):
                    temp_file = tempfile.NamedTemporaryFile(
                        prefix="alpha_fixer_video_",
                        suffix=".mp4",
                        delete=False,
                    )
                    temp_mp4 = temp_file.name
                    temp_file.close()
                    render_path = temp_mp4
                elif temp_output_path is not None:
                    render_path = temp_output_path
                import imageio
                import numpy as np
                writer = imageio.get_writer(
                    render_path,
                    format="FFMPEG",
                    fps=fps,
                    codec="libx264",
                    pixelformat="yuv420p",
                )
            export_stage = "frame rendering"
            for i in range(total):
                progress.setValue(i)
                QApplication.processEvents()
                if progress.wasCanceled():
                    canceled = True
                    break
                ci, fi = _global_frame_to_snapshot(i)
                source_pil = self._get_snapshot_frame(clip_snapshot[ci], fi)
                adjusted = source_pil
                filtered = None
                framed = None
                rgb = None
                try:
                    adjusted = _apply_adjustments(
                        source_pil,
                        brightness=brightness,
                        contrast=contrast,
                        black_point=black_point,
                        white_point=white_point,
                        saturation=saturation,
                        sharpness=sharpness,
                    )
                    filtered = _apply_filter(adjusted, filter_key)
                    framed = _fit_frame_to_canvas(filtered, canvas_size, fmt)
                    if fmt != "gif":
                        rgb = framed if framed.mode == "RGB" else framed.convert("RGB")
                        try:
                            writer.append_data(np.array(rgb))
                        finally:
                            if rgb is not None and rgb is not framed:
                                rgb.close()
                    if fmt == "gif":
                        gif_frames.append(framed)
                        framed = None
                    wrote_frames = True
                finally:
                    if framed is not None and framed is not filtered:
                        try:
                            framed.close()
                        except Exception:
                            pass
                    if filtered is not None and filtered is not adjusted and filtered is not source_pil:
                        try:
                            filtered.close()
                        except Exception:
                            pass
                    if adjusted is not source_pil and adjusted is not filtered:
                        try:
                            adjusted.close()
                        except Exception:
                            pass
                    try:
                        source_pil.close()
                    except Exception:
                        pass
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    if not canceled:
                        raise
                writer = None
            if fmt == "gif" and not canceled and gif_frames:
                export_stage = "GIF assembly"
                first = gif_frames[0]
                rest = gif_frames[1:]
                try:
                    if rest:
                        first.save(
                            temp_output_path or out_path,
                            format="GIF",
                            save_all=True,
                            append_images=rest,
                            duration=max(1, int(round(1000.0 / fps))),
                            loop=0,
                            disposal=2,
                        )
                    else:
                        first.save(
                            temp_output_path or out_path,
                            format="GIF",
                            duration=max(1, int(round(1000.0 / fps))),
                            loop=0,
                            disposal=2,
                        )
                finally:
                    for frame in gif_frames:
                        try:
                            frame.close()
                        except Exception:
                            pass
                    gif_frames.clear()
                if temp_output_path is not None:
                    Path(temp_output_path).replace(out_path)
                    temp_output_path = None
            elif fmt == "mp4" and not canceled and wrote_frames and temp_mp4 is not None:
                export_stage = "audio muxing"
                progress.setLabelText("Mixing source audio into MP4…")
                QApplication.processEvents()
                try:
                    self._mux_mp4_audio(render_path, temp_output_path, clip_snapshot, fps)
                    if temp_output_path is not None:
                        Path(temp_output_path).replace(out_path)
                        temp_output_path = None
                except Exception as exc:
                    silent_render = Path(render_path)
                    if not silent_render.is_file() or silent_render.stat().st_size <= 0:
                        raise
                    if temp_output_path is not None:
                        try:
                            Path(temp_output_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        temp_output_path = None
                    silent_render.replace(out_path)
                    temp_mp4 = None
                    export_issue_count += 1
                    history_audio_mode_override = "off (mux failed)"
                    history_extra_notes.extend([
                        "audio-mux-fallback=silent",
                        f"audio-mux-error={(str(exc).strip() or 'unknown mux failure')}",
                    ])
                    completion_note = (
                        "Saved as a silent MP4 because source-audio muxing failed after video rendering."
                    )
            elif fmt == "mp4" and not canceled and wrote_frames and temp_output_path is not None:
                Path(temp_output_path).replace(out_path)
                temp_output_path = None
            progress.setValue(total)
        except Exception as exc:
            if temp_mp4 is not None:
                try:
                    Path(temp_mp4).unlink(missing_ok=True)
                except Exception:
                    pass
            if temp_output_path is not None:
                try:
                    Path(temp_output_path).unlink(missing_ok=True)
                except Exception:
                    pass
            progress.close()
            QMessageBox.critical(self, "Export Error", f"Could not save output during {export_stage}:\n{exc}")
            return
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            if temp_mp4 is not None:
                try:
                    Path(temp_mp4).unlink(missing_ok=True)
                except Exception:
                    pass
            if temp_output_path is not None:
                try:
                    Path(temp_output_path).unlink(missing_ok=True)
                except Exception:
                    pass
            for frame in gif_frames:
                try:
                    frame.close()
                except Exception:
                    pass
            gif_frames.clear()

        if canceled or not wrote_frames:
            if not out_path_existed:
                try:
                    Path(out_path).unlink(missing_ok=True)
                except Exception:
                    pass
            progress.close()
            return

        progress.close()
        self._record_export_history(
            out_path,
            fmt,
            clip_snapshot,
            len(clip_snapshot),
            export_issue_count,
            canvas_size=canvas_size,
            audio_mode_override=history_audio_mode_override,
            extra_notes=history_extra_notes,
        )
        status_suffix = f" — {completion_note}" if completion_note else ""
        self.status_notice.emit(
            f"Video Builder export saved: {Path(out_path).name} ({len(clip_snapshot)} clip{'s' if len(clip_snapshot) != 1 else ''}, {fmt.upper()}){status_suffix}",
            8000,
        )
        final_message = f"Saved to:\n{out_path}"
        if completion_note:
            final_message += f"\n\n{completion_note}"
        QMessageBox.information(self, "Export Complete", final_message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.queue_status_changed.emit(self.get_queue_status_text())

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.queue_status_changed.emit("")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _reset_adjustments(self) -> None:
        for slider, val in [
            (self._brightness_slider, _ADJUSTMENT_DEFAULT_VALUES["brightness"]),
            (self._contrast_slider, _ADJUSTMENT_DEFAULT_VALUES["contrast"]),
            (self._saturation_slider, _ADJUSTMENT_DEFAULT_VALUES["saturation"]),
            (self._sharpness_slider, _ADJUSTMENT_DEFAULT_VALUES["sharpness"]),
            (self._black_slider, _ADJUSTMENT_DEFAULT_VALUES["black_point"]),
            (self._white_slider, _ADJUSTMENT_DEFAULT_VALUES["white_point"]),
        ]:
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
        self._filter_combo.blockSignals(True)
        self._filter_combo.setCurrentIndex(0)
        self._filter_combo.blockSignals(False)
        self._update_preview()

    def _clip_canvas_size(self, clip: "_ClipEntry") -> Optional[tuple[int, int]]:
        if clip.active_frames <= 0:
            return None
        if clip.frame_size and clip.frame_size[0] > 0 and clip.frame_size[1] > 0:
            return clip.frame_size
        probe = None
        try:
            probe = clip.get_frame(0)
            clip.frame_size = probe.size
            return clip.frame_size
        except Exception:
            return None
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

    def _active_clip_canvas_sizes(self) -> list[tuple[int, int]]:
        active = [clip for clip in self._clips if clip.active_frames > 0]
        sizes = [self._clip_canvas_size(clip) for clip in active]
        return [size for size in sizes if size is not None]

    def _timeline_canvas_size(self, fmt: Optional[str] = None) -> Optional[tuple[int, int]]:
        sizes = self._active_clip_canvas_sizes()
        if not sizes:
            return None
        width = max(size[0] for size in sizes)
        height = max(size[1] for size in sizes)
        export_fmt = fmt or (self._export_fmt_combo.currentData() or "gif")
        return _coerce_export_size((width, height), export_fmt)

    def _update_export_summary(self) -> None:
        self._update_audio_controls()
        natural_sizes = self._active_clip_canvas_sizes()
        if not natural_sizes:
            self._export_size_lbl.setText("Canvas: auto once clips are added")
            self._export_pad_lbl.setText("Mixed-size clips are resized to fit and centred automatically.")
            return
        natural_width = max(size[0] for size in natural_sizes)
        natural_height = max(size[1] for size in natural_sizes)
        canvas_size = _coerce_export_size(
            (natural_width, natural_height),
            self._export_fmt_combo.currentData() or "gif",
        )
        if canvas_size == (natural_width, natural_height):
            detail = "Canvas: auto from the largest clip"
        else:
            detail = "Canvas: auto from the largest clip (rounded for MP4 compatibility)"
        self._export_size_lbl.setText(f"{detail}: {canvas_size[0]} × {canvas_size[1]}")
        self._export_pad_lbl.setText(
            "Mixed-size clips are scaled to fit this canvas and letterboxed automatically."
        )

    def closeEvent(self, event) -> None:
        self._preview_timer.stop()
        for clip in self._clips:
            clip.close()
        super().closeEvent(event)
