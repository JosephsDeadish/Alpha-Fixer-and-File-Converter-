#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from src.core.runtime_validation import (
    load_manifest_entries,
    materialize_manifest_entries,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate a manifest by copying local samples and/or downloading URL-backed entries into a cache directory."
    )
    parser.add_argument("manifest", help="Path to a manifest JSON file or inline JSON payload.")
    parser.add_argument(
        "--cache-dir",
        required=True,
        help="Directory where materialized sample files should be written.",
    )
    parser.add_argument(
        "--output-manifest",
        help="Optional path to write the materialized manifest JSON. Defaults to stdout.",
    )
    parser.add_argument(
        "--allow-downloads",
        action="store_true",
        help="Allow downloading entries that define url/download_url fields.",
    )
    parser.add_argument(
        "--copy-local",
        action="store_true",
        help="Copy existing local files into the cache directory as well.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-download timeout in seconds.",
    )
    args = parser.parse_args(argv)

    entries = load_manifest_entries(args.manifest)
    if not entries:
        raise SystemExit("No manifest entries were loaded.")
    materialized = materialize_manifest_entries(
        entries,
        cache_dir=args.cache_dir,
        allow_download=args.allow_downloads,
        copy_local=args.copy_local,
        timeout=max(5, int(args.timeout)),
    )
    payload = {"base_dir": str(Path(args.cache_dir).resolve()), "entries": materialized}
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output_manifest:
        out_path = Path(args.output_manifest)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
