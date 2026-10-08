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
    if source_path and os.path.isfile(source_path):
        _verify_entry_checksum(source_path, normalized)
        if not copy_local:
            return normalized
        target_path = _materialized_target_path(
            normalized,
            cache_dir=cache_dir or manifest_cache_dir_from_env(),
        )
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if os.path.abspath(source_path) != os.path.abspath(target_path):
            shutil.copy2(source_path, target_path)
        normalized[source_key] = target_path
        return normalized
    download_url = _entry_download_url(normalized)
    if not (allow_download and download_url):
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
