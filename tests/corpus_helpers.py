import os

from src.core.runtime_validation import (
    load_manifest_entries,
    manifest_cache_dir_from_env,
    manifest_downloads_enabled,
    materialize_manifest_entries,
)


def _optional_corpus_roots(*env_names: str) -> list[str]:
    roots: list[str] = []
    for env_name in env_names:
        raw = os.environ.get(env_name, "")
        if not raw:
            continue
        for part in raw.split(os.pathsep):
            candidate = part.strip()
            if candidate and os.path.isdir(candidate):
                roots.append(candidate)
    return roots


def _iter_corpus_files(roots: list[str], suffixes: tuple[str, ...], *, limit: int = 32) -> list[str]:
    matches: list[str] = []
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in sorted(filenames):
                if name.lower().endswith(suffixes):
                    matches.append(os.path.join(dirpath, name))
                    if len(matches) >= limit:
                        return matches
    return matches


def _optional_manifest_entries(env_name: str) -> list[dict[str, object]]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return []
    entries = load_manifest_entries(raw)
    if not entries:
        return []
    try:
        return materialize_manifest_entries(
            entries,
            cache_dir=manifest_cache_dir_from_env(),
            allow_download=manifest_downloads_enabled(),
        )
    except Exception:
        return []
