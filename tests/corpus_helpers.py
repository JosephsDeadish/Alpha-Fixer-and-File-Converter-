import json
import os


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
        path_text = str(entry.get("path") or "").strip()
        if not path_text:
            continue
        resolved = path_text
        if not os.path.isabs(resolved):
            if base_dir:
                resolved = os.path.normpath(os.path.join(base_dir, resolved))
            elif manifest_dir:
                resolved = os.path.normpath(os.path.join(manifest_dir, resolved))
        normalized = dict(entry)
        normalized["path"] = resolved
        entries.append(normalized)
    return entries
