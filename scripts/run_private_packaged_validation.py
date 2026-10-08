#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import populate_private_manifests as populate_private_manifests_script
from scripts import verify_packaged_app as verify_packaged_app_script


def _default_launch_target() -> str:
    candidates = []
    if os.name == "nt":
        candidates.extend(
            [
                _REPO_ROOT / "dist" / "AlphaFixerConverter" / "AlphaFixerConverter.exe",
                _REPO_ROOT / "dist" / "AlphaFixerConverter.exe",
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                _REPO_ROOT / "dist" / "AlphaFixerConverter" / "AlphaFixerConverter",
                _REPO_ROOT / "dist" / "AlphaFixerConverter.app" / "Contents" / "MacOS" / "AlphaFixerConverter",
            ]
        )
    else:
        candidates.extend(
            [
                _REPO_ROOT / "dist" / "AlphaFixerConverter" / "AlphaFixerConverter",
                _REPO_ROOT / "dist" / "AlphaFixerConverter",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate private real-sample manifests and run packaged validation on a machine that has the local corpora."
    )
    parser.add_argument(
        "launch_target",
        nargs="?",
        default="",
        help="Optional packaged executable/app entrypoint. Defaults to the common dist/ target for the current platform.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "dist" / "private-runtime-validation"),
        help="Directory where generated manifests, sample cache, and JSON reports should be written.",
    )
    parser.add_argument("--manifest-limit", type=int, default=24, help="Maximum auto-discovered entries per private manifest.")
    parser.add_argument("--smoke-seconds", type=float, default=2.0, help="Seconds to keep each smoke launch alive.")
    parser.add_argument("--repeat", type=int, default=3, help="How many smoke-launch cycles to run.")
    parser.add_argument("--smoke-launch-delay-seconds", type=float, default=0.5, help="Pause between repeated smoke launches.")
    parser.add_argument("--timeout", type=int, default=45, help="Per-launch timeout in seconds.")
    parser.add_argument("--selftest-iterations", type=int, default=6, help="Packaged self-test iterations per run.")
    parser.add_argument("--selftest-sample-limit", type=int, default=16, help="Optional cap for manifest entries exercised per self-test.")
    parser.add_argument("--selftest-stress-loops", type=int, default=4, help="Generated stress-loop count per packaged self-test run.")
    parser.add_argument("--repeat-selftest-runs", type=int, default=2, help="How many separate packaged self-test launches to compare.")
    parser.add_argument("--max-selftest-rss-growth-mb", type=float, default=256.0, help="Upper bound for repeated self-test peak-RSS growth.")
    parser.add_argument("--max-selftest-rss-spread-mb", type=float, default=256.0, help="Upper bound for repeated self-test peak-RSS spread.")
    parser.add_argument("--max-smoke-elapsed-seconds", type=float, default=30.0, help="Upper bound for any single packaged smoke-launch elapsed time.")
    parser.add_argument("--max-smoke-elapsed-growth-seconds", type=float, default=10.0, help="Upper bound for repeated packaged smoke-launch elapsed-time growth.")
    parser.add_argument("--max-smoke-elapsed-spread-seconds", type=float, default=10.0, help="Upper bound for repeated packaged smoke-launch elapsed-time spread.")
    parser.add_argument("--private-sample-cache-dir", help="Optional cache directory for local/download-backed private samples.")
    parser.add_argument("--allow-sample-downloads", action="store_true", help="Allow private manifest entries with download URLs to populate missing samples.")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir).resolve()
    manifests_dir = output_dir / "manifests"
    sample_cache_dir = Path(args.private_sample_cache_dir).resolve() if args.private_sample_cache_dir else (output_dir / "sample-cache")
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_cache_dir.mkdir(parents=True, exist_ok=True)

    populate_private_manifests_script.main(
        [
            "--output-dir",
            str(manifests_dir),
            "--limit",
            str(max(1, int(args.manifest_limit))),
        ]
    )

    verify_args = [
        args.launch_target or _default_launch_target(),
        "--smoke-seconds",
        str(max(0.25, float(args.smoke_seconds))),
        "--repeat",
        str(max(1, int(args.repeat))),
        "--smoke-launch-delay-seconds",
        str(max(0.0, float(args.smoke_launch_delay_seconds))),
        "--timeout",
        str(max(1, int(args.timeout))),
        "--run-selftest",
        "--selftest-iterations",
        str(max(1, int(args.selftest_iterations))),
        "--selftest-sample-limit",
        str(max(1, int(args.selftest_sample_limit))),
        "--selftest-stress-loops",
        str(max(0, int(args.selftest_stress_loops))),
        "--repeat-selftest-runs",
        str(max(1, int(args.repeat_selftest_runs))),
        "--max-selftest-rss-growth-mb",
        str(float(args.max_selftest_rss_growth_mb)),
        "--max-selftest-rss-spread-mb",
        str(float(args.max_selftest_rss_spread_mb)),
        "--max-smoke-elapsed-seconds",
        str(float(args.max_smoke_elapsed_seconds)),
        "--max-smoke-elapsed-growth-seconds",
        str(float(args.max_smoke_elapsed_growth_seconds)),
        "--max-smoke-elapsed-spread-seconds",
        str(float(args.max_smoke_elapsed_spread_seconds)),
        "--require-selftest-pass",
        "--require-core-selftest-checks",
        "--require-video-selftest-checks",
        "--require-video-runtime",
        "--require-ffmpeg-selfcheck",
        "--require-ffprobe-selfcheck",
        "--require-bundled-ffmpeg",
        "--require-bundled-ffprobe",
        "--require-bundled-default-theme-svg",
        "--require-packaged-bundle-ready",
        "--json-out",
        str(output_dir / "private-runtime-validation.json"),
        "--sample-cache-dir",
        str(sample_cache_dir),
    ]
    if sys.platform.startswith("linux"):
        verify_args.append("--require-no-missing-libs")
    if max(0, int(args.selftest_stress_loops)) > 0:
        verify_args.append("--require-stress-selftest-checks")
    if args.allow_sample_downloads:
        verify_args.append("--allow-sample-downloads")

    disc_manifest = manifests_dir / "private_real_disc_video_manifest.json"
    odd_manifest = manifests_dir / "private_real_odd_container_manifest.json"
    odd_video_manifest = manifests_dir / "private_real_odd_container_video_manifest.json"
    dds_complex_manifest = manifests_dir / "private_real_dds_complex_manifest.json"
    dds_dx10_manifest = manifests_dir / "private_real_dds_dx10_manifest.json"

    disc_manifest_count = 0
    dds_manifest_count = 0
    for manifest_path in (disc_manifest, odd_manifest, odd_video_manifest):
        if manifest_path.exists():
            verify_args.extend(["--disc-video-manifest", str(manifest_path)])
            disc_manifest_count += 1
    for manifest_path in (dds_complex_manifest, dds_dx10_manifest):
        if manifest_path.exists():
            verify_args.extend(["--dds-manifest", str(manifest_path)])
            dds_manifest_count += 1
    if disc_manifest_count > 0:
        verify_args.append("--require-disc-manifest-group-checks")
    if dds_manifest_count > 0:
        verify_args.append("--require-dds-manifest-group-checks")

    print(f"Running private packaged validation via: {verify_args[0]}")
    print(f"Generated manifests: {manifests_dir}")
    print(f"Sample cache: {sample_cache_dir}")
    return int(verify_packaged_app_script.main(verify_args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
