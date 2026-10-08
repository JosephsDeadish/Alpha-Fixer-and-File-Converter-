#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import populate_private_manifests as populate_private_manifests_script
from scripts import verify_packaged_app as verify_packaged_app_script
from src.core.runtime_validation import (
    build_private_local_manifests_from_env,
    corpus_roots_from_env,
    iter_corpus_files,
)

_VIDEO_CORPUS_ENV_NAMES = (
    "ALPHA_FIXER_REAL_DISC_VIDEO_CORPUS",
    "ALPHA_FIXER_REAL_VIDEO_CORPUS",
    "ALPHA_FIXER_VIDEO_CORPUS_DIR",
)
_DDS_CORPUS_ENV_NAMES = (
    "ALPHA_FIXER_REAL_DDS_DX10_CORPUS",
    "ALPHA_FIXER_REAL_DDS_CORPUS",
    "ALPHA_FIXER_DDS_CORPUS_DIR",
)
_PRIVATE_MANIFEST_ENV_NAMES = (
    "ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST",
    "ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST",
    "ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST",
    "ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST",
    "ALPHA_FIXER_REAL_DDS_DX10_MANIFEST",
)


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


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _preflight_summary(
    *,
    launch_target: str,
    output_dir: Path,
    manifests_dir: Path,
    sample_cache_dir: Path,
    manifest_limit: int,
) -> dict[str, object]:
    video_roots = corpus_roots_from_env(*_VIDEO_CORPUS_ENV_NAMES)
    dds_roots = corpus_roots_from_env(*_DDS_CORPUS_ENV_NAMES)
    discovered = build_private_local_manifests_from_env(limit=max(1, int(manifest_limit)))
    disc_entries = list(discovered.get("disc_video") or [])
    odd_entries = list(discovered.get("odd_video") or [])
    dds_entries = list(discovered.get("dds") or [])
    return {
        "launch_target": launch_target,
        "launch_target_exists": Path(launch_target).exists(),
        "output_dir": str(output_dir),
        "manifests_dir": str(manifests_dir),
        "sample_cache_dir": str(sample_cache_dir),
        "private_manifest_env": {name: str(os.environ.get(name, "") or "") for name in _PRIVATE_MANIFEST_ENV_NAMES},
        "corpus_env": {
            **{name: str(os.environ.get(name, "") or "") for name in _VIDEO_CORPUS_ENV_NAMES},
            **{name: str(os.environ.get(name, "") or "") for name in _DDS_CORPUS_ENV_NAMES},
        },
        "corpus_roots": {
            "video": video_roots,
            "dds": dds_roots,
        },
        "corpus_file_counts": {
            "disc_like": len(iter_corpus_files(video_roots, (".iso", ".umd", ".bin", ".cue", ".pmf", ".pss", ".str"), limit=0)),
            "odd_video": len(iter_corpus_files(video_roots, (".pmf", ".pss", ".str", ".vob", ".wmv", ".asf", ".mxf", ".mov", ".avi", ".dat", ".mkv", ".wma"), limit=0)),
            "dds": len(iter_corpus_files(dds_roots, (".dds",), limit=0)),
        },
        "discovered_manifest_entry_counts": {
            "disc_video": len(disc_entries),
            "odd_video": len(odd_entries),
            "dds": len(dds_entries),
        },
        "discovered_manifest_preview": {
            "disc_video": disc_entries[:3],
            "odd_video": odd_entries[:3],
            "dds": dds_entries[:3],
        },
        "total_discovered_entries": len(disc_entries) + len(odd_entries) + len(dds_entries),
    }


def _interesting_sample_outcomes(payload: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    runtime = payload.get("runtime_selftest")
    if not isinstance(runtime, dict):
        return []
    manifest_results = runtime.get("manifest_results")
    if not isinstance(manifest_results, dict):
        return []
    interesting: list[dict[str, object]] = []
    for base_key in ("disc_video", "dds", "format_matrix"):
        report = manifest_results.get(base_key)
        if not isinstance(report, dict):
            continue
        for sample in report.get("sample_results") or []:
            if not isinstance(sample, dict):
                continue
            status = str(sample.get("status") or "").strip().lower()
            if status not in {"failed", "explained", "expected_failure", "missing"}:
                continue
            interesting.append(
                {
                    "manifest": base_key,
                    "status": status,
                    "label": str(sample.get("label") or sample.get("path") or "sample").strip(),
                    "detail": str(sample.get("detail") or "").strip(),
                    "stage": str(sample.get("stage") or "").strip(),
                }
            )
    return interesting


def _failed_checks(payload: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    runtime = payload.get("runtime_selftest")
    if not isinstance(runtime, dict):
        return []
    checks = runtime.get("checks")
    if not isinstance(checks, dict):
        return []
    failures: list[dict[str, object]] = []
    for name in sorted(checks):
        check = checks.get(name)
        if not isinstance(check, dict) or bool(check.get("ok")):
            continue
        failures.append(
            {
                "name": name,
                "detail": str(check.get("detail") or "").strip(),
            }
        )
    return failures


def _write_runner_summary(
    path: Path,
    *,
    status: str,
    preflight: dict[str, object],
    verifier_report_path: Path | None = None,
    verifier_payload: dict[str, object] | None = None,
    error: str = "",
) -> None:
    payload: dict[str, object] = {
        "status": status,
        "preflight": preflight,
    }
    if error:
        payload["error"] = error
    if verifier_report_path is not None:
        payload["verifier_report_path"] = str(verifier_report_path)
    if isinstance(verifier_payload, dict):
        payload["manifest_inputs"] = verifier_payload.get("manifest_inputs") or {}
        payload["runtime_selftest_repeat_summary"] = verifier_payload.get("runtime_selftest_repeat_summary") or {}
        runtime = verifier_payload.get("runtime_selftest")
        if isinstance(runtime, dict):
            payload["runtime_selftest_checks"] = runtime.get("checks") or {}
            payload["manifest_results"] = runtime.get("manifest_results") or {}
        payload["failed_checks"] = _failed_checks(verifier_payload)
        payload["interesting_sample_outcomes"] = _interesting_sample_outcomes(verifier_payload)
    _write_json(path, payload)


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
    runner_summary_path = output_dir / "private-runtime-validation-summary.json"
    verifier_report_path = output_dir / "private-runtime-validation.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_cache_dir.mkdir(parents=True, exist_ok=True)

    launch_target = args.launch_target or _default_launch_target()
    preflight = _preflight_summary(
        launch_target=launch_target,
        output_dir=output_dir,
        manifests_dir=manifests_dir,
        sample_cache_dir=sample_cache_dir,
        manifest_limit=max(1, int(args.manifest_limit)),
    )
    if int(preflight.get("total_discovered_entries") or 0) <= 0:
        error = "No eligible private corpus samples were discovered. Configure the ALPHA_FIXER_REAL_* corpus directories first."
        _write_runner_summary(
            runner_summary_path,
            status="preflight_failed",
            preflight=preflight,
            verifier_report_path=verifier_report_path,
            error=error,
        )
        print(error)
        print(f"Wrote private validation preflight summary: {runner_summary_path}")
        return 1

    populate_private_manifests_script.main(
        [
            "--output-dir",
            str(manifests_dir),
            "--limit",
            str(max(1, int(args.manifest_limit))),
        ]
    )

    verify_args = [
        launch_target,
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
        str(verifier_report_path),
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
    try:
        rc = int(verify_packaged_app_script.main(verify_args) or 0)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        _write_runner_summary(
            runner_summary_path,
            status="verifier_failed",
            preflight=preflight,
            verifier_report_path=verifier_report_path,
            verifier_payload=_load_json(verifier_report_path),
            error=str(exc),
        )
        raise SystemExit(code)
    _write_runner_summary(
        runner_summary_path,
        status="completed" if rc == 0 else "verifier_failed",
        preflight=preflight,
        verifier_report_path=verifier_report_path,
        verifier_payload=_load_json(verifier_report_path),
    )
    print(f"Wrote private validation summary: {runner_summary_path}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
