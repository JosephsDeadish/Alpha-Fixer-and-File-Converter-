import json
import os
from pathlib import Path
from typing import Optional


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
    entries: list[dict[str, object]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        normalized = dict(entry)
        for key in ("path", "input", "input_path", "source", "source_path"):
            raw_path = str(entry.get(key) or "").strip()
            if not raw_path:
                continue
            resolved = raw_path
            if not os.path.isabs(resolved):
                if base_dir:
                    resolved = os.path.normpath(os.path.join(base_dir, resolved))
                elif manifest_dir:
                    resolved = os.path.normpath(os.path.join(manifest_dir, resolved))
            normalized[key] = resolved
        entries.append(normalized)
    return entries


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


def _entry_tokens(entry: dict[str, object], key: str) -> list[str]:
    tokens = entry.get(key) or []
    if isinstance(tokens, str):
        tokens = [tokens]
    if not isinstance(tokens, list):
        return []
    return [str(token) for token in tokens if str(token).strip()]


def execute_disc_video_manifest(
    entries: list[dict[str, object]],
    video_tool,
    *,
    limit: int = 0,
) -> tuple[bool, str]:
    exercised = loaded = explained = unavailable = 0
    for entry in _limited_entries(entries, limit):
        sample_path = _entry_source_path(entry)
        if not sample_path or not os.path.isfile(sample_path):
            unavailable += 1
            continue
        preferred_video = entry.get("preferred_video_stream_index")
        preferred_audio = entry.get("preferred_audio_stream_index")
        expected = str(entry.get("expect") or "load_or_explain").strip().lower()
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
        if expected == "load":
            if clip is None:
                return False, f"{Path(sample_path).name}: expected load, got failure hint: {hint}"
        elif expected == "fail":
            if clip is not None:
                try:
                    clip.close()
                finally:
                    return False, f"{Path(sample_path).name}: expected failure, but clip loaded"
        elif clip is None and not str(hint).strip():
            return False, f"{Path(sample_path).name}: expected load_or_explain, but no clip or hint was produced"
        if clip is not None:
            try:
                if clip.source_path != sample_path:
                    return False, f"{Path(sample_path).name}: loaded clip lost original source path"
                loaded += 1
            finally:
                clip.close()
        else:
            explained += 1
        for token in _entry_tokens(entry, "hint_contains"):
            if token not in hint:
                return False, f"{Path(sample_path).name}: missing required hint token: {token}"
    if exercised == 0:
        return False, f"no available samples matched manifest (missing={unavailable})"
    return True, f"entries={exercised} loaded={loaded} explained={explained} missing={unavailable}"


def execute_dds_manifest(
    entries: list[dict[str, object]],
    load_dds_raw,
    *,
    limit: int = 0,
) -> tuple[bool, str]:
    exercised = decoded = failed_as_expected = unavailable = 0
    for entry in _limited_entries(entries, limit):
        sample_path = _entry_source_path(entry)
        if not sample_path or not os.path.isfile(sample_path):
            unavailable += 1
            continue
        expected = str(entry.get("expect") or "load_or_fail_clearly").strip().lower()
        exercised += 1
        try:
            img = load_dds_raw(sample_path)
        except Exception as exc:
            detail = str(exc)
            if expected == "load":
                return False, f"{Path(sample_path).name}: expected decode, got: {detail}"
            for token in _entry_tokens(entry, "detail_contains"):
                if token.lower() not in detail.lower():
                    return False, f"{Path(sample_path).name}: missing DDS failure token: {token}"
            failed_as_expected += 1
            continue
        try:
            if img.size[0] <= 0 or img.size[1] <= 0:
                return False, f"{Path(sample_path).name}: decoded image had invalid size {img.size}"
            if expected == "fail":
                return False, f"{Path(sample_path).name}: expected failure, but DDS decoded"
            decoded += 1
        finally:
            img.close()
    if exercised == 0:
        return False, f"no available samples matched manifest (missing={unavailable})"
    return True, f"entries={exercised} decoded={decoded} expected_failures={failed_as_expected} missing={unavailable}"


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
    for index, entry in enumerate(_limited_entries(entries, limit), start=1):
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
