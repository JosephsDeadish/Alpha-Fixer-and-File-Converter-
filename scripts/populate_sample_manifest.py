#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from src.core.runtime_validation import (
    build_private_local_manifests_from_env,
    load_manifest_entries,
    materialize_manifest_entries,
    render_manifest_payload,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate a manifest by copying local samples and/or downloading URL-backed entries into a cache directory."
    )
    parser.add_argument("manifest", nargs="?", help="Path to a manifest JSON file or inline JSON payload.")
    parser.add_argument(
        "--cache-dir",
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
    parser.add_argument(
        "--discover-private-local",
        action="store_true",
        help="Discover private local disc/odd-video/DDS corpus samples from ALPHA_FIXER_REAL_* corpus environment variables.",
    )
    parser.add_argument(
        "--private-limit",
        type=int,
        default=32,
        help="Maximum entries per auto-discovered private manifest category.",
    )
    parser.add_argument(
        "--output-disc-manifest",
        help="Optional path to write the discovered private disc-video manifest JSON.",
    )
    parser.add_argument(
        "--output-odd-video-manifest",
        help="Optional path to write the discovered private odd-video manifest JSON.",
    )
    parser.add_argument(
        "--output-dds-manifest",
        help="Optional path to write the discovered private DDS manifest JSON.",
    )
    args = parser.parse_args(argv)

    if args.discover_private_local:
        discovered = build_private_local_manifests_from_env(limit=max(1, int(args.private_limit)))
        rendered = {
            "disc_video": render_manifest_payload(list(discovered.get("disc_video") or [])),
            "odd_video": render_manifest_payload(list(discovered.get("odd_video") or [])),
            "dds": render_manifest_payload(list(discovered.get("dds") or [])),
        }
        wrote_any = False
        for raw_payload, out_name in (
            (rendered["disc_video"], args.output_disc_manifest),
            (rendered["odd_video"], args.output_odd_video_manifest),
            (rendered["dds"], args.output_dds_manifest),
        ):
            if not out_name:
                continue
            out_path = Path(out_name)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(raw_payload + "\n", encoding="utf-8")
            wrote_any = True
        summary = {
            "disc_video_entries": len(json.loads(rendered["disc_video"]).get("entries", [])),
            "odd_video_entries": len(json.loads(rendered["odd_video"]).get("entries", [])),
            "dds_entries": len(json.loads(rendered["dds"]).get("entries", [])),
        }
        payload = {"summary": summary, "manifests": rendered}
        formatted = json.dumps(payload, indent=2, sort_keys=True)
        if args.output_manifest:
            out_path = Path(args.output_manifest)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(formatted + "\n", encoding="utf-8")
        elif not wrote_any:
            print(formatted)
        else:
            print(json.dumps(summary, sort_keys=True))
        return 0

    if not args.manifest:
        raise SystemExit("manifest is required unless --discover-private-local is used.")
    if not args.cache_dir:
        raise SystemExit("--cache-dir is required unless --discover-private-local is used.")

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
