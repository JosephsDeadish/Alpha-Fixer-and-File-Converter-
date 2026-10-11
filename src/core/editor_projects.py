"""Bounded, data-only editor projects. No pickle, executable objects or archives."""
from __future__ import annotations

import base64
import io
import hashlib
import json
import math
import os
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 8 * 1024 * 1024
MAX_TOTAL_PIXELS = 8 * 1024 * 1024
MAX_ENTRIES = 512
MAX_TIMELINE_FRAMES = 10_000_000

GIF_CONTROLS = {
    "_delay_slider": (10, 3000), "_loop_slider": (0, 20),
    "_width_slider": (0, 3840), "_height_slider": (0, 2160),
    "_optimize_check": bool,
}
VIDEO_CONTROLS = {
    "_fps_slider": (1, 60), "_brightness_slider": (10, 400),
    "_contrast_slider": (10, 400), "_saturation_slider": (0, 400),
    "_sharpness_slider": (0, 400), "_black_slider": (0, 254),
    "_white_slider": (1, 255), "_audio_enable_check": bool,
    "_audio_mute_check": bool, "_audio_volume_slider": (0, 200),
    "_filter_combo": ("none", "greyscale", "sepia", "invert", "sharpen",
                      "blur", "edge_enhance", "emboss", "vignette"),
    "_export_fmt_combo": ("gif", "mp4"),
    "_insert_mode_combo": ("end", "before", "after"),
}


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("Invalid project fields")


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Project integer outside supported bounds")


def _text(value):
    if type(value) is not str or len(value) > 4096 or "\0" in value:
        raise ValueError("Invalid project text/path")


def validate(document, kind=None):
    _keys(document, ("format", "version", "kind", "controls", "entries", "selected", "playhead"))
    if document["format"] != "alpha-editor-project" or type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("Unsupported project format/version")
    if document["kind"] not in ("gif", "video") or (kind and document["kind"] != kind):
        raise ValueError("Project belongs to a different editor")
    controls = GIF_CONTROLS if document["kind"] == "gif" else VIDEO_CONTROLS
    _keys(document["controls"], controls)
    for name, rule in controls.items():
        value = document["controls"][name]
        if rule is bool:
            if type(value) is not bool:
                raise ValueError("Invalid project boolean")
        elif isinstance(rule[0], int):
            _integer(value, *rule)
        elif type(value) is not str or value not in rule:
            raise ValueError("Unsupported project setting")
    entries = document["entries"]
    if type(entries) is not list or len(entries) > MAX_ENTRIES:
        raise ValueError(f"Projects support at most {MAX_ENTRIES} entries")
    _integer(document["selected"], -1, len(entries) - 1)
    _integer(document["playhead"], 0, MAX_TIMELINE_FRAMES - 1)
    pixels = 0
    timeline = 0
    for entry in entries:
        if document["kind"] == "gif":
            _keys(entry, ("source", "source_kind", "index", "delay", "source_delay", "png", "size"))
            for key in ("source", "source_kind"):
                _text(entry[key])
            _integer(entry["index"], 0, MAX_TIMELINE_FRAMES)
            for key in ("delay", "source_delay"):
                if entry[key] is not None:
                    _integer(entry[key], 1, 3_600_000)
            size = entry["size"]
            if type(size) is not list or len(size) != 2:
                raise ValueError("Invalid frame dimensions")
            for dimension in size:
                _integer(dimension, 1, 8192)
            pixels += size[0] * size[1]
            if size[0] * size[1] > MAX_PIXELS or pixels > MAX_TOTAL_PIXELS:
                raise ValueError("GIF project exceeds 8,388,608 total pixels (32 MiB RGBA)")
            if type(entry["png"]) is not str or len(entry["png"]) > (MAX_FRAME_BYTES + 2) // 3 * 4:
                raise ValueError("PNG frame exceeds 8 MiB")
            timeline += 1
        else:
            _keys(entry, ("source", "source_sha256", "loader", "type", "frames", "fps", "trim_start", "trim_end",
                          "speed", "still_duration", "video_stream", "audio_stream", "split"))
            _text(entry["source"])
            signature = entry["source_sha256"]
            if (type(signature) is not str or len(signature) != 64
                    or any(c not in "0123456789abcdef" for c in signature)):
                raise ValueError("Invalid media content signature")
            if (not entry["source"] or type(entry["loader"]) is not str
                    or type(entry["type"]) is not str or entry["loader"] not in ("image", "video")
                    or entry["type"] not in ("image", "gif", "video")):
                raise ValueError("Invalid clip source/type")
            _integer(entry["frames"], 1, MAX_TIMELINE_FRAMES)
            if type(entry["fps"]) not in (int, float) or not math.isfinite(entry["fps"]) or not 0.1 <= entry["fps"] <= 1000:
                raise ValueError("Invalid source FPS")
            _integer(entry["trim_start"], 0, entry["frames"] - 1)
            _integer(entry["trim_end"], entry["trim_start"], entry["frames"] - 1)
            _integer(entry["speed"], 10, 400)
            _integer(entry["still_duration"], 1, 3600)
            for key in ("video_stream", "audio_stream"):
                if entry[key] is not None:
                    _integer(entry[key], 0, 65535)
            split = entry["split"]
            if split is not None:
                if type(split) is not list or len(split) != 8:
                    raise ValueError("Invalid split mapping")
                for number in split:
                    _integer(number, 0, MAX_TIMELINE_FRAMES)
                if split[3:6] != [entry["trim_start"], entry["trim_end"], entry["speed"]]:
                    raise ValueError("Split mapping does not match clip timing")
                if not (split[0] <= entry["trim_start"] <= entry["trim_end"] <= split[6] < entry["frames"]
                        and 0 < split[2] <= split[7] and split[1] + split[2] <= split[7]):
                    raise ValueError("Invalid split audio/video span")
            timeline += (entry["still_duration"] if entry["type"] == "image" else
                         split[2] if split else max(1, round((entry["trim_end"] - entry["trim_start"] + 1) * 100 / entry["speed"])))
    if timeline > MAX_TIMELINE_FRAMES or document["playhead"] >= max(1, timeline):
        raise ValueError("Invalid or oversized timeline/playhead")
    return document


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate project field")
        result[key] = value
    return result


def read_project(path, kind=None):
    with open(path, "rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Project exceeds 64 MiB")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite number")))
        return validate(document, kind)
    except (RecursionError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid project JSON") from error


def encode_png(image):
    if image.width > 8192 or image.height > 8192 or image.width * image.height > MAX_PIXELS:
        raise ValueError("Frame exceeds supported dimensions/pixels")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("PNG frame exceeds 8 MiB")
    return base64.b64encode(raw).decode("ascii")


def decode_png(entry):
    from PIL import Image
    try:
        raw = base64.b64decode(entry["png"], validate=True)
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("PNG frame exceeds 8 MiB")
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or list(image.size) != entry["size"] or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Invalid embedded PNG")
            image.load()
            return image.convert("RGBA")
    except Exception as error:
        raise ValueError(f"Cannot decode project frame: {error}") from error


def write_project(path, document):
    from src.ui._ui_utils import staged_output_path
    validate(document)
    raw = json.dumps(document, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Project exceeds 64 MiB")
    with staged_output_path(path, preserve_existing_mode=True) as staged:
        with open(staged, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())


def _source_stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def source_signature(path):
    """Stream content identity in project I/O workers, never during UI polling."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        before = os.fstat(handle.fileno())
        remaining = before.st_size
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError(f"Media changed during project I/O: {path}")
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(handle.fileno())
    if _source_stamp(before) != _source_stamp(after) or _source_stamp(after) != _source_stamp(os.stat(path)):
        raise ValueError(f"Media changed during project I/O: {path}")
    return digest.hexdigest()


def prepare_entries(document, project_path):
    """Resolve/decode everything before the caller touches the live session."""
    prepared = []
    try:
        if document["kind"] == "gif":
            from src.ui.gif_builder import _FrameEntry
            for entry in document["entries"]:
                prepared.append(_FrameEntry(entry["source"], entry["index"], decode_png(entry),
                                            entry["delay"], entry["source_delay"], entry["source_kind"]))
        else:
            from src.ui.video_tool import _load_image_as_clip, _load_video_clip
            decoded_pixels = 0
            signatures = {}
            source_stamps = {}
            for entry in document["entries"]:
                source = str((Path(project_path).absolute().parent / entry["source"]).resolve())
                if not Path(source).is_file():
                    raise ValueError(f"Missing project media: {source}")
                if source not in signatures:
                    source_stamps[source] = _source_stamp(os.stat(source))
                    signatures[source] = source_signature(source)
                if signatures[source] != entry["source_sha256"]:
                    raise ValueError(f"Media changed since project save: {source}")
                if entry["loader"] == "image":
                    from PIL import Image, UnidentifiedImageError
                    try:
                        with Image.open(source) as image:
                            if (image.width > 8192 or image.height > 8192
                                    or image.width * image.height * getattr(image, "n_frames", 1) > MAX_TOTAL_PIXELS
                                    or getattr(image, "n_frames", 1) > MAX_ENTRIES):
                                raise ValueError(f"Image source exceeds project decode limits: {source}")
                    except UnidentifiedImageError:
                        # App-specific formats (TIM/XNB) use the existing image loader.
                        pass
                clip = (_load_image_as_clip(source) if entry["loader"] == "image" else
                        _load_video_clip(source, preferred_video_stream_index=entry["video_stream"],
                                         preferred_audio_stream_index=entry["audio_stream"]))
                if clip is None:
                    raise ValueError(f"Cannot load project media: {source}")
                prepared.append(clip)
                if source_stamps[source] != _source_stamp(os.stat(source)):
                    raise ValueError(f"Media changed during project I/O: {source}")
                clip._source_sha256 = signatures[source]
                clip._source_stamp = source_stamps[source]
                if clip.frame_size and (max(clip.frame_size) > 8192 or clip.frame_size[0] * clip.frame_size[1] > MAX_PIXELS):
                    raise ValueError(f"Media dimensions exceed project limits: {source}")
                if clip.frame_size:
                    decoded_pixels += clip.frame_size[0] * clip.frame_size[1] * (
                        clip.total_frames if entry["loader"] == "image" else 1)
                    if decoded_pixels > MAX_TOTAL_PIXELS:
                        raise ValueError("Project initial decoded images exceed 8,388,608 pixels")
                if clip.total_frames != entry["frames"] or clip.clip_type != entry["type"] or abs(clip.fps - entry["fps"]) > 0.01:
                    raise ValueError(f"Media changed since project save: {source}")
                if (clip.preferred_video_stream_index != entry["video_stream"]
                        or clip.preferred_audio_stream_index != entry["audio_stream"]):
                    raise ValueError(f"Requested media streams unavailable: {source}")
                clip.trim_start, clip.trim_end = entry["trim_start"], entry["trim_end"]
                clip.speed_percent = entry["speed"]
                clip.still_duration_frames = entry["still_duration"]
                clip._split_timing = tuple(entry["split"]) if entry["split"] else None
        return prepared
    except BaseException:
        for entry in prepared:
            entry.close()
        raise
