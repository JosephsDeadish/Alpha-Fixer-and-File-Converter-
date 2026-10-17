#!/usr/bin/env python3
import argparse
from pathlib import Path

from src.core.runtime_validation import (
    build_private_local_manifests_from_env,
    render_manifest_payload,
)


def _write_manifest(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_manifest_payload(entries) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auto-populate private real-sample manifests from configured local corpus roots."
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where populated private manifests should be written.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=32,
        help="Maximum auto-discovered entries per manifest.",
    )
    args = parser.parse_args(argv)

    manifests = build_private_local_manifests_from_env(limit=max(1, int(args.limit)))
    output_dir = Path(args.output_dir).resolve()
    written: list[tuple[str, Path, int]] = []

    disc_entries = list(manifests.get("disc_video") or [])
    odd_entries = list(manifests.get("odd_video") or [])
    dds_entries = list(manifests.get("dds") or [])

    if disc_entries:
        path = output_dir / "private_real_disc_video_manifest.json"
        _write_manifest(path, disc_entries)
        written.append(("ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST", path, len(disc_entries)))
    if odd_entries:
        for env_name, file_name in (
            ("ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST", "private_real_odd_container_manifest.json"),
            ("ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST", "private_real_odd_container_video_manifest.json"),
        ):
            path = output_dir / file_name
            _write_manifest(path, odd_entries)
            written.append((env_name, path, len(odd_entries)))
    if dds_entries:
        for env_name, file_name in (
            ("ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST", "private_real_dds_complex_manifest.json"),
            ("ALPHA_FIXER_REAL_DDS_DX10_MANIFEST", "private_real_dds_dx10_manifest.json"),
        ):
            path = output_dir / file_name
            _write_manifest(path, dds_entries)
            written.append((env_name, path, len(dds_entries)))

    if not written:
        raise SystemExit(
            "No eligible private corpus samples were discovered. Configure the ALPHA_FIXER_REAL_* corpus directories first."
        )

    print(f"Wrote populated private manifests to: {output_dir}")
    for env_name, path, count in written:
        print(f"{env_name}={path}  ({count} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
