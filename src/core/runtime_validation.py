import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def load_manifest_entries(raw_or_path: str) -> list[dict[str, object]]:
    raw = str(raw_or_path or "").strip()
    if not raw:
        return []
    payload = None
    manifest_dir = ""
    try:
        if os.path.isfile(raw):
            manifest_dir = os.path.dirname(os.path.abspath(raw))
            with open(raw, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = json.loads(raw)
    except Exception:
        return []
    base_dir = ""
    if isinstance(payload, dict):
        base_dir = str(payload.get("base_dir") or "").strip()
        payload = payload.get("entries", payload.get("samples"))
    if manifest_dir and base_dir and not os.path.isabs(base_dir):
        base_dir = os.path.normpath(os.path.join(manifest_dir, base_dir))
    if not isinstance(payload, list):
        return []
    def _resolve_manifest_path(raw_path: object) -> str:
        resolved = str(raw_path or "").strip()
        if not resolved:
            return ""
        if not os.path.isabs(resolved):
            if base_dir:
                resolved = os.path.normpath(os.path.join(base_dir, resolved))
            elif manifest_dir:
                resolved = os.path.normpath(os.path.join(manifest_dir, resolved))
        return resolved

    def _normalize_manifest_entry(entry: dict[str, object]) -> dict[str, object]:
        normalized = dict(entry)
        for key in ("path", "input", "input_path", "source", "source_path"):
            resolved = _resolve_manifest_path(entry.get(key))
            if resolved:
                normalized[key] = resolved
        for key in ("companions", "sidecars"):
            raw_related = entry.get(key)
            if not isinstance(raw_related, list):
                continue
            normalized_related: list[dict[str, object]] = []
            for item in raw_related:
                if isinstance(item, str):
                    item = {"path": item}
                if isinstance(item, dict):
                    normalized_related.append(_normalize_manifest_entry(item))
            normalized[key] = normalized_related
        return normalized

    entries: list[dict[str, object]] = []
    for entry in payload:
        if isinstance(entry, dict):
            entries.append(_normalize_manifest_entry(entry))
    return entries


def manifest_downloads_enabled() -> bool:
    for env_name in (
        "ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS",
        "ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS",
    ):
        raw = os.environ.get(env_name, "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
    return False


def manifest_cache_dir_from_env() -> str:
    for env_name in (
        "ALPHA_FIXER_RUNTIME_SAMPLE_CACHE_DIR",
        "ALPHA_FIXER_SAMPLE_CACHE_DIR",
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return os.path.join(tempfile.gettempdir(), "alpha_fixer_manifest_cache")


def load_manifest_entries_from_env(env_name: str) -> list[dict[str, object]]:
    return load_manifest_entries(os.environ.get(env_name, ""))


def manifest_sample_limit_from_env(
    env_name: str = "ALPHA_FIXER_RUNTIME_SAMPLE_LIMIT",
    *,
    default: int = 0,
    maximum: int = 256,
) -> int:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return max(0, int(default))
    try:
        limit = int(raw)
    except ValueError:
        limit = default
    return max(0, min(int(maximum), int(limit)))


def _limited_entries(entries: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    if limit <= 0:
        return list(entries)
    return list(entries[:limit])


def _entry_source_path(entry: dict[str, object]) -> str:
    for key in ("path", "input", "input_path", "source", "source_path"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return ""


def _entry_source_key(entry: dict[str, object]) -> str:
    for key in ("path", "input", "input_path", "source", "source_path"):
        value = str(entry.get(key) or "").strip()
        if value:
            return key
    return "path"


def _entry_tokens(entry: dict[str, object], key: str) -> list[str]:
    tokens = entry.get(key) or []
    if isinstance(tokens, str):
        tokens = [tokens]
    if not isinstance(tokens, list):
        return []
    return [str(token) for token in tokens if str(token).strip()]


def _entry_bool(entry: dict[str, object], key: str, default: bool = False) -> bool:
    value = entry.get(key, default)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _entry_int(entry: dict[str, object], key: str) -> int | None:
    try:
        return int(entry.get(key))
    except Exception:
        return None


def _entry_float(entry: dict[str, object], key: str) -> float | None:
    try:
        return float(entry.get(key))
    except Exception:
        return None


def _entry_size(entry: dict[str, object], key: str) -> tuple[int, int] | None:
    value = entry.get(key)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (int(value[0]), int(value[1]))
        except Exception:
            return None
    text = str(value or "").strip().lower()
    if "x" in text:
        left, right = text.split("x", 1)
        try:
            return (int(left.strip()), int(right.strip()))
        except Exception:
            return None
    return None


def _entry_label(entry: dict[str, object], sample_path: str) -> str:
    parts = [
        str(entry.get("platform") or "").strip(),
        str(entry.get("sample_id") or entry.get("label") or entry.get("name") or "").strip(),
        Path(sample_path).name,
    ]
    return " / ".join(part for part in parts if part)


def _entry_download_url(entry: dict[str, object]) -> str:
    return str(entry.get("url") or entry.get("download_url") or "").strip()


def _entry_sha256(entry: dict[str, object]) -> str:
    return str(entry.get("sha256") or entry.get("checksum") or "").strip().lower()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_entry_checksum(path: str, entry: dict[str, object]) -> None:
    expected = _entry_sha256(entry)
    if not expected:
        return
    actual = _sha256_file(path)
    if actual.lower() != expected:
        raise ValueError(
            f"{Path(path).name}: sha256 mismatch (expected {expected}, got {actual})"
        )


def _materialized_target_path(
    entry: dict[str, object],
    *,
    cache_dir: str,
) -> str:
    source_path = _entry_source_path(entry)
    explicit_name = str(entry.get("download_name") or "").strip()
    explicit_subdir = str(entry.get("cache_subdir") or "").strip()
    if explicit_name:
        rel_name = explicit_name
    elif source_path:
        rel_name = os.path.basename(source_path)
    else:
        parsed = urlparse(_entry_download_url(entry))
        rel_name = os.path.basename(parsed.path) or "sample.bin"
    target_dir = cache_dir
    if explicit_subdir:
        target_dir = os.path.join(target_dir, explicit_subdir)
    return os.path.join(target_dir, rel_name)


def materialize_manifest_entry(
    entry: dict[str, object],
    *,
    cache_dir: str = "",
    allow_download: bool = False,
    copy_local: bool = False,
    timeout: int = 120,
) -> dict[str, object]:
    normalized = dict(entry)
    source_key = _entry_source_key(normalized)
    source_path = _entry_source_path(normalized)

    def _materialize_related_entries(parent_target_dir: str = "") -> None:
        for key in ("companions", "sidecars"):
            raw_related = normalized.get(key)
            if not isinstance(raw_related, list):
                continue
            materialized_related: list[dict[str, object]] = []
            for item in raw_related:
                if isinstance(item, str):
                    item = {"path": item}
                if not isinstance(item, dict):
                    continue
                related_cache_dir = parent_target_dir or cache_dir or manifest_cache_dir_from_env()
                materialized_related.append(
                    materialize_manifest_entry(
                        item,
                        cache_dir=related_cache_dir,
                        allow_download=allow_download,
                        copy_local=copy_local,
                        timeout=timeout,
                    )
                )
            normalized[key] = materialized_related

    if source_path and os.path.isfile(source_path):
        _verify_entry_checksum(source_path, normalized)
        if not copy_local:
            _materialize_related_entries()
            return normalized
        target_path = _materialized_target_path(
            normalized,
            cache_dir=cache_dir or manifest_cache_dir_from_env(),
        )
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if os.path.abspath(source_path) != os.path.abspath(target_path):
            shutil.copy2(source_path, target_path)
        normalized[source_key] = target_path
        _materialize_related_entries(os.path.dirname(target_path))
        return normalized
    download_url = _entry_download_url(normalized)
    if not (allow_download and download_url):
        _materialize_related_entries()
        return normalized
    target_path = _materialized_target_path(
        normalized,
        cache_dir=cache_dir or manifest_cache_dir_from_env(),
    )
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.isfile(target_path):
        try:
            _verify_entry_checksum(target_path, normalized)
        except Exception:
            try:
                os.remove(target_path)
            except OSError:
                pass
    if not os.path.isfile(target_path):
        request = Request(
            download_url,
            headers={"User-Agent": "AlphaFixerConverter/manifest-fetch"},
        )
        with urlopen(request, timeout=max(5, int(timeout))) as response, open(target_path, "wb") as handle:
            shutil.copyfileobj(response, handle)
    _verify_entry_checksum(target_path, normalized)
    normalized[source_key] = target_path
    _materialize_related_entries(os.path.dirname(target_path))
    return normalized


def materialize_manifest_entries(
    entries: list[dict[str, object]],
    *,
    cache_dir: str = "",
    allow_download: bool = False,
    copy_local: bool = False,
    timeout: int = 120,
) -> list[dict[str, object]]:
    return [
        materialize_manifest_entry(
            entry,
            cache_dir=cache_dir,
            allow_download=allow_download,
            copy_local=copy_local,
            timeout=timeout,
        )
        for entry in entries
    ]


def execute_disc_video_manifest(
    entries: list[dict[str, object]],
    video_tool,
    *,
    limit: int = 0,
) -> tuple[bool, str]:
    exercised = loaded = explained = unavailable = 0
    platform_counts: dict[str, int] = {}
    for raw_entry in _limited_entries(entries, limit):
        try:
            entry = materialize_manifest_entry(
                raw_entry,
                cache_dir=manifest_cache_dir_from_env(),
                allow_download=manifest_downloads_enabled(),
            )
        except Exception as exc:
            sample_label = Path(_entry_source_path(raw_entry) or _entry_download_url(raw_entry) or "sample").name
            return False, f"{sample_label}: manifest materialization failed: {exc}"
        sample_path = _entry_source_path(entry)
        if not sample_path or not os.path.isfile(sample_path):
            if _entry_bool(entry, "required"):
                return False, f"{Path(sample_path or _entry_source_path(raw_entry) or 'sample').name}: required sample missing"
            unavailable += 1
            continue
        preferred_video = entry.get("preferred_video_stream_index")
        preferred_audio = entry.get("preferred_audio_stream_index")
        expected = str(entry.get("expect") or "load_or_explain").strip().lower()
        sample_label = _entry_label(entry, sample_path)
        probe = None
        probe_loader = getattr(video_tool, "_probe_media_details", None)
        if callable(probe_loader):
            probe = probe_loader(
                sample_path,
                preferred_video_stream_index=preferred_video,
                preferred_audio_stream_index=preferred_audio,
            )
        clip = video_tool._load_video_clip(
            sample_path,
            preferred_video_stream_index=preferred_video,
            preferred_audio_stream_index=preferred_audio,
        )
        hint = video_tool._video_load_failure_hint(
            sample_path,
            preferred_video_stream_index=preferred_video,
            preferred_audio_stream_index=preferred_audio,
        )
        exercised += 1
        platform = str(entry.get("platform") or entry.get("system") or "").strip()
        if platform:
            platform_counts[platform] = platform_counts.get(platform, 0) + 1
        if expected == "load":
            if clip is None:
                return False, f"{sample_label}: expected load, got failure hint: {hint}"
        elif expected == "fail":
            if clip is not None:
                try:
                    clip.close()
                finally:
                    return False, f"{sample_label}: expected failure, but clip loaded"
        elif clip is None and not str(hint).strip():
            return False, f"{sample_label}: expected load_or_explain, but no clip or hint was produced"
        for token in _entry_tokens(entry, "hint_contains"):
            if token not in hint:
                return False, f"{sample_label}: missing required hint token: {token}"
        if probe is not None:
            expected_probe_size = _entry_size(entry, "expect_probe_frame_size")
            if expected_probe_size is not None:
                actual_probe_size = (
                    max(0, int(probe.get("width") or 0)),
                    max(0, int(probe.get("height") or 0)),
                )
                if actual_probe_size != expected_probe_size:
                    return False, (
                        f"{sample_label}: expected probed size {expected_probe_size}, got {actual_probe_size}"
                    )
            for key, label in (
                ("expect_video_stream_count", "video stream count"),
                ("expect_audio_stream_count", "audio stream count"),
                ("expect_video_stream_index", "selected video stream"),
                ("expect_audio_stream_index", "selected audio stream"),
            ):
                expected_value = _entry_int(entry, key)
                if expected_value is None:
                    continue
                probe_key = {
                    "expect_video_stream_count": "video_stream_count",
                    "expect_audio_stream_count": "audio_stream_count",
                    "expect_video_stream_index": "video_stream_index",
                    "expect_audio_stream_index": "audio_stream_index",
                }[key]
                actual_value = _entry_int(probe, probe_key)
                if actual_value != expected_value:
                    return False, f"{sample_label}: expected {label} {expected_value}, got {actual_value}"
            for key, probe_key, label in (
                ("expect_probe_has_video", "has_video", "probe video"),
                ("expect_probe_has_audio", "has_audio", "probe audio"),
            ):
                if key not in entry:
                    continue
                expected_value = _entry_bool(entry, key)
                actual_value = bool(probe.get(probe_key))
                if actual_value != expected_value:
                    return False, f"{sample_label}: expected {label}={expected_value}, got {actual_value}"
            for key, probe_key, label in (
                ("expect_format_name_contains", "format_name", "format"),
                ("expect_video_codec_contains", "video_codec", "video codec"),
                ("expect_audio_codec_contains", "audio_codec", "audio codec"),
            ):
                for token in _entry_tokens(entry, key):
                    if token.lower() not in str(probe.get(probe_key) or "").lower():
                        return False, f"{sample_label}: missing expected {label} token: {token}"
        if clip is not None:
            try:
                if clip.source_path != sample_path:
                    return False, f"{sample_label}: loaded clip lost original source path"
                min_frames = _entry_int(entry, "min_frames")
                if min_frames is not None and int(getattr(clip, "total_frames", 0) or 0) < min_frames:
                    return False, (
                        f"{sample_label}: expected at least {min_frames} frames, got {getattr(clip, 'total_frames', 0)}"
                    )
                min_duration = _entry_float(entry, "min_duration_seconds")
                if min_duration is not None:
                    fps = max(0.0, float(getattr(clip, "fps", 0.0) or 0.0))
                    duration = (float(getattr(clip, "total_frames", 0) or 0) / fps) if fps > 0 else 0.0
                    if duration + 1e-9 < min_duration:
                        return False, (
                            f"{sample_label}: expected duration >= {min_duration}s, got {duration:.3f}s"
                        )
                if "expect_has_audio" in entry:
                    expected_audio = _entry_bool(entry, "expect_has_audio")
                    if bool(getattr(clip, "has_audio", False)) != expected_audio:
                        return False, (
                            f"{sample_label}: expected has_audio={expected_audio}, got {bool(getattr(clip, 'has_audio', False))}"
                        )
                expected_size = _entry_size(entry, "expect_frame_size")
                if expected_size is not None and tuple(getattr(clip, "frame_size", ()) or ()) != expected_size:
                    return False, (
                        f"{sample_label}: expected frame size {expected_size}, got {getattr(clip, 'frame_size', None)}"
                    )
                if _entry_bool(entry, "require_recovery") and not str(getattr(clip, "load_note", "") or "").strip():
                    return False, f"{sample_label}: expected a recovery path, but clip loaded directly"
                for key, source_attr, label in (
                    ("expect_load_note_contains", "load_note", "load note"),
                    ("expect_load_strategy_contains", "load_strategy", "load strategy"),
                ):
                    for token in _entry_tokens(entry, key):
                        if token.lower() not in str(getattr(clip, source_attr, "") or "").lower():
                            return False, f"{sample_label}: missing expected {label} token: {token}"
                for key, attr_name, label in (
                    ("expect_preferred_video_stream_index", "preferred_video_stream_index", "preferred video stream"),
                    ("expect_preferred_audio_stream_index", "preferred_audio_stream_index", "preferred audio stream"),
                ):
                    expected_value = _entry_int(entry, key)
                    if expected_value is None:
                        continue
                    actual_value = _entry_int({key: getattr(clip, attr_name, None)}, key)
                    if actual_value != expected_value:
                        return False, f"{sample_label}: expected {label} {expected_value}, got {actual_value}"
                loaded += 1
            finally:
                clip.close()
        else:
            explained += 1
    if exercised == 0:
        return False, f"no available samples matched manifest (missing={unavailable})"
    platform_summary = ""
    if platform_counts:
        platform_summary = " platforms=" + ",".join(
            f"{name}:{count}" for name, count in sorted(platform_counts.items())
        )
    return True, (
        f"entries={exercised} loaded={loaded} explained={explained} missing={unavailable}{platform_summary}"
    )


def execute_dds_manifest(
    entries: list[dict[str, object]],
    load_dds_raw,
    *,
    limit: int = 0,
) -> tuple[bool, str]:
    exercised = decoded = failed_as_expected = unavailable = 0
    sample_groups: dict[str, int] = {}
    for raw_entry in _limited_entries(entries, limit):
        try:
            entry = materialize_manifest_entry(
                raw_entry,
                cache_dir=manifest_cache_dir_from_env(),
                allow_download=manifest_downloads_enabled(),
            )
        except Exception as exc:
            sample_label = Path(_entry_source_path(raw_entry) or _entry_download_url(raw_entry) or "sample").name
            return False, f"{sample_label}: manifest materialization failed: {exc}"
        sample_path = _entry_source_path(entry)
        if not sample_path or not os.path.isfile(sample_path):
            if _entry_bool(entry, "required"):
                return False, f"{Path(sample_path or _entry_source_path(raw_entry) or 'sample').name}: required sample missing"
            unavailable += 1
            continue
        expected = str(entry.get("expect") or "load_or_fail_clearly").strip().lower()
        sample_label = _entry_label(entry, sample_path)
        exercised += 1
        group = str(entry.get("group") or entry.get("platform") or entry.get("family") or "").strip()
        if group:
            sample_groups[group] = sample_groups.get(group, 0) + 1
        try:
            img = load_dds_raw(sample_path)
        except Exception as exc:
            detail = str(exc)
            if expected == "load":
                return False, f"{sample_label}: expected decode, got: {detail}"
            for token in _entry_tokens(entry, "detail_contains"):
                if token.lower() not in detail.lower():
                    return False, f"{sample_label}: missing DDS failure token: {token}"
            failed_as_expected += 1
            continue
        try:
            if img.size[0] <= 0 or img.size[1] <= 0:
                return False, f"{sample_label}: decoded image had invalid size {img.size}"
            if expected == "fail":
                return False, f"{sample_label}: expected failure, but DDS decoded"
            expected_size = _entry_size(entry, "expect_size")
            if expected_size is not None and tuple(img.size) != expected_size:
                return False, f"{sample_label}: expected decoded size {expected_size}, got {img.size}"
            decoded += 1
        finally:
            img.close()
    if exercised == 0:
        return False, f"no available samples matched manifest (missing={unavailable})"
    group_summary = ""
    if sample_groups:
        group_summary = " groups=" + ",".join(
            f"{name}:{count}" for name, count in sorted(sample_groups.items())
        )
    return True, (
        f"entries={exercised} decoded={decoded} expected_failures={failed_as_expected} missing={unavailable}{group_summary}"
    )


def execute_format_matrix_manifest(
    entries: list[dict[str, object]],
    *,
    convert_file,
    load_dds,
    image_module,
    output_formats: dict[str, str],
    tmpdir: str,
    limit: int = 0,
) -> tuple[bool, str]:
    exercised = converted = expected_failures = unavailable = 0
    for index, raw_entry in enumerate(_limited_entries(entries, limit), start=1):
        try:
            entry = materialize_manifest_entry(
                raw_entry,
                cache_dir=manifest_cache_dir_from_env(),
                allow_download=manifest_downloads_enabled(),
            )
        except Exception as exc:
            sample_label = Path(_entry_source_path(raw_entry) or _entry_download_url(raw_entry) or "sample").name
            return False, f"{sample_label}: manifest materialization failed: {exc}"
        input_path = _entry_source_path(entry)
        if not input_path or not os.path.isfile(input_path):
            unavailable += 1
            continue
        target_format = str(entry.get("target_format") or entry.get("output_format") or entry.get("format") or "").strip().upper()
        if not target_format:
            return False, f"{Path(input_path).name}: manifest entry missing target_format/output_format"
        output_ext = str(entry.get("output_ext") or output_formats.get(target_format) or "").strip()
        if not output_ext:
            return False, f"{Path(input_path).name}: unsupported target format {target_format}"
        quality = int(entry.get("quality") or 90)
        keep_metadata = bool(entry.get("keep_metadata"))
        dds_variant = str(entry.get("dds_variant") or "auto")
        resize = entry.get("resize")
        if isinstance(resize, list):
            resize = tuple(resize)
        if resize is not None and not isinstance(resize, tuple):
            return False, f"{Path(input_path).name}: invalid resize value {resize!r}"
        expected = str(entry.get("expect") or "convert").strip().lower()
        output_path = os.path.join(tmpdir, f"matrix_{index}{output_ext}")
        exercised += 1
        try:
            result_path = convert_file(
                input_path,
                output_path,
                target_format,
                quality=quality,
                resize=resize,
                keep_metadata=keep_metadata,
                dds_variant=dds_variant,
            )
        except Exception as exc:
            detail = str(exc)
            if expected == "fail":
                for token in _entry_tokens(entry, "detail_contains"):
                    if token.lower() not in detail.lower():
                        return False, f"{Path(input_path).name}: missing conversion failure token: {token}"
                expected_failures += 1
                continue
            return False, f"{Path(input_path).name}: conversion to {target_format} failed: {detail}"
        if expected == "fail":
            return False, f"{Path(input_path).name}: expected failure, but conversion to {target_format} succeeded"
        result_text = str(result_path)
        if not os.path.isfile(result_text) or os.path.getsize(result_text) <= 0:
            return False, f"{Path(input_path).name}: conversion to {target_format} produced no output file"
        if output_ext.lower() == ".dds":
            img = load_dds(result_text)
            try:
                if img.size[0] <= 0 or img.size[1] <= 0:
                    return False, f"{Path(input_path).name}: DDS output had invalid size {img.size}"
            finally:
                img.close()
        elif output_ext.lower() not in {".xnb", ".tim", ".svg"}:
            with image_module.open(result_text) as img:
                img.load()
                if img.size[0] <= 0 or img.size[1] <= 0:
                    return False, f"{Path(input_path).name}: converted image had invalid size {img.size}"
        converted += 1
    if exercised == 0:
        return False, f"no available samples matched manifest (missing={unavailable})"
    return True, f"entries={exercised} converted={converted} expected_failures={expected_failures} missing={unavailable}"
