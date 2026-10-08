#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.runtime_validation import load_manifest_entries
_PUBLIC_DISC_VIDEO_MANIFEST = _REPO_ROOT / "sample_manifests" / "public_disc_video_manifest.json"
_PUBLIC_DDS_MANIFEST = _REPO_ROOT / "sample_manifests" / "public_dds_dx10_manifest.json"
_PUBLIC_FORMAT_MATRIX_MANIFEST = _REPO_ROOT / "sample_manifests" / "public_format_matrix_manifest.json"
_PUBLIC_MANIFEST_CHECKS = (
    "external_disc_video_manifest",
    "external_dds_manifest",
    "external_format_matrix_manifest",
)
_VIDEO_SELFTEST_CHECKS = (
    "generated_mp4_load",
    "mpegts_load",
    "synthetic_bin_probe",
)
_DDS_SELFTEST_CHECKS = (
    "png_to_dds_rgba",
    "png_to_dds_dxt1",
)
_CORE_SELFTEST_CHECKS = (
    "png_to_gif",
    "png_to_dds_rgba",
    "generated_mp4_load",
    "mpegts_load",
    "synthetic_bin_probe",
)


def _run_and_echo(command: list[str], *, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    return result


def _capability_payload(output: str) -> dict[str, object]:
    return _prefixed_payload(
        output,
        prefix="ALPHA_FIXER_RUNTIME_CAPABILITIES=",
        missing_message="Packaged capability audit did not emit ALPHA_FIXER_RUNTIME_CAPABILITIES output.",
    )


def _selftest_payload(output: str) -> dict[str, object]:
    return _prefixed_payload(
        output,
        prefix="ALPHA_FIXER_RUNTIME_SELFTEST=",
        missing_message="Packaged runtime self-test did not emit ALPHA_FIXER_RUNTIME_SELFTEST output.",
    )


def _prefixed_payload(output: str, *, prefix: str, missing_message: str) -> dict[str, object]:
    payload_line = ""
    for raw_line in output.splitlines():
        if raw_line.startswith(prefix):
            payload_line = raw_line[len(prefix):]
    if not payload_line:
        raise ValueError(missing_message)
    payload = json.loads(payload_line)
    if not isinstance(payload, dict):
        raise ValueError(f"{prefix[:-1]} emitted a non-object JSON payload.")
    return payload


def _merged_manifest_arg(raw_values: list[str] | None) -> str | None:
    values = [str(value or "").strip() for value in (raw_values or []) if str(value or "").strip()]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    merged_entries: list[dict[str, object]] = []
    for raw in values:
        entries = load_manifest_entries(raw)
        if not entries:
            raise SystemExit(f"Manifest argument could not be loaded: {raw}")
        merged_entries.extend(entries)
    return json.dumps({"entries": merged_entries})


def _required_selftest_checks(args) -> list[str]:
    required = [str(name or "").strip() for name in getattr(args, "require_selftest_check", []) if str(name or "").strip()]
    for attr_name, names in (
        ("require_core_selftest_checks", _CORE_SELFTEST_CHECKS),
        ("require_video_selftest_checks", _VIDEO_SELFTEST_CHECKS),
        ("require_dds_selftest_checks", _DDS_SELFTEST_CHECKS),
    ):
        if getattr(args, attr_name, False):
            for name in names:
                if name not in required:
                    required.append(name)
    if getattr(args, "require_public_manifest_checks", False):
        for name in _PUBLIC_MANIFEST_CHECKS:
            if name not in required:
                required.append(name)
    return required


def _print_selftest_check_summary(checks: dict[str, object]) -> None:
    if not isinstance(checks, dict) or not checks:
        return
    print("Self-test checks:")
    for name in sorted(checks):
        check = checks.get(name)
        if isinstance(check, dict):
            ok = bool(check.get("ok"))
            detail = str(check.get("detail") or "").strip()
        else:
            ok = False
            detail = ""
        status = "ok" if ok else "failed"
        line = f"  - {name}: {status}"
        if detail:
            line += f" ({detail})"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-launch and audit a built Alpha Fixer package.")
    parser.add_argument("launch_target", help="Path to the packaged executable/app entrypoint.")
    parser.add_argument("--smoke-seconds", type=float, default=1.5, help="Seconds to keep each smoke-launch alive.")
    parser.add_argument("--repeat", type=int, default=1, help="How many smoke-launch cycles to run before auditing capabilities.")
    parser.add_argument("--timeout", type=int, default=25, help="Per-launch timeout in seconds.")
    parser.add_argument("--require-video-runtime", action="store_true", help="Fail if video import / MP4 export runtime bits are unavailable.")
    parser.add_argument("--require-odd-probe-ready", action="store_true", help="Fail if ffprobe-backed odd-container probing is unavailable.")
    parser.add_argument("--require-ffmpeg-selfcheck", action="store_true", help="Fail if the packaged runtime cannot execute the resolved ffmpeg binary successfully.")
    parser.add_argument("--require-ffprobe-selfcheck", action="store_true", help="Fail if the packaged runtime cannot execute the resolved ffprobe binary successfully.")
    parser.add_argument("--require-wand-runtime", action="store_true", help="Fail if the packaged runtime lacks a working ImageMagick/wand runtime.")
    parser.add_argument("--require-no-missing-libs", action="store_true", help="Fail if packaged runtime reports missing Linux shared libraries.")
    parser.add_argument("--require-bundled-ffmpeg", action="store_true", help="Fail if ffmpeg resolves outside the packaged bundle.")
    parser.add_argument("--require-bundled-ffprobe", action="store_true", help="Fail if ffprobe resolves outside the packaged bundle.")
    parser.add_argument("--require-bundled-imagemagick", action="store_true", help="Fail if ImageMagick/wand support is not bundled inside the packaged app.")
    parser.add_argument("--require-no-packaged-asset-gaps", action="store_true", help="Fail if the packaged runtime audit reports any packaged asset warnings.")
    parser.add_argument("--run-selftest", action="store_true", help="Run the packaged executable's end-to-end runtime self-test after the capability audit.")
    parser.add_argument("--selftest-iterations", type=int, default=2, help="How many self-test iterations the packaged app should run when --run-selftest is set.")
    parser.add_argument("--selftest-sample-limit", type=int, default=0, help="Optional cap for external manifest entries exercised per packaged self-test run.")
    parser.add_argument("--require-selftest-pass", action="store_true", help="Fail if the packaged runtime self-test reports passed=false.")
    parser.add_argument("--max-selftest-rss-mb", type=float, help="Optional upper bound for the packaged self-test peak RSS value when reported.")
    parser.add_argument("--require-selftest-check", action="append", default=[], help="Specific packaged self-test check key that must report ok=true. Repeat for multiple checks.")
    parser.add_argument("--require-core-selftest-checks", action="store_true", help="Fail unless the built-in PNG/GIF/DDS and generated-video self-test checks all report ok=true.")
    parser.add_argument("--require-video-selftest-checks", action="store_true", help="Fail unless generated MP4, MPEG-TS, and odd-container BIN self-test checks all report ok=true.")
    parser.add_argument("--require-dds-selftest-checks", action="store_true", help="Fail unless the built-in DDS self-test checks, including compressed DDS output when available, all report ok=true.")
    parser.add_argument("--require-public-manifest-checks", action="store_true", help="Fail unless the public disc-video, DDS/DX10, and format-matrix self-test checks all report ok=true.")
    parser.add_argument("--disc-video-manifest", action="append", default=[], help="Optional external PSP/PS1/PS2 disc-video manifest (path or inline JSON) for packaged self-test execution. Repeat to merge multiple manifests.")
    parser.add_argument("--dds-manifest", action="append", default=[], help="Optional external DDS/DX10 manifest (path or inline JSON) for packaged self-test execution. Repeat to merge multiple manifests.")
    parser.add_argument("--format-matrix-manifest", action="append", default=[], help="Optional external packaged conversion-matrix manifest (path or inline JSON) for packaged self-test execution. Repeat to merge multiple manifests.")
    parser.add_argument("--use-public-sample-manifests", action="store_true", help="Use the repository's built-in public disc-video, DDS/DX10, and format-matrix manifests for packaged self-test execution.")
    parser.add_argument("--allow-sample-downloads", action="store_true", help="Allow manifest-backed self-tests to download missing external samples when URL fields are present.")
    parser.add_argument("--sample-cache-dir", help="Optional cache directory for downloaded or materialized manifest samples.")
    parser.add_argument("--json-out", help="Optional path to write the final runtime capability payload as JSON.")
    args = parser.parse_args(argv)

    launch_target = Path(args.launch_target)
    if not launch_target.exists():
        raise SystemExit(f"Launch target not found: {launch_target}")
    if args.use_public_sample_manifests:
        if not args.disc_video_manifest:
            args.disc_video_manifest = [str(_PUBLIC_DISC_VIDEO_MANIFEST)]
        if not args.dds_manifest:
            args.dds_manifest = [str(_PUBLIC_DDS_MANIFEST)]
        if not args.format_matrix_manifest:
            args.format_matrix_manifest = [str(_PUBLIC_FORMAT_MATRIX_MANIFEST)]
    merged_disc_manifest = _merged_manifest_arg(args.disc_video_manifest)
    merged_dds_manifest = _merged_manifest_arg(args.dds_manifest)
    merged_format_manifest = _merged_manifest_arg(args.format_matrix_manifest)
    if (merged_disc_manifest or merged_dds_manifest or merged_format_manifest) and not args.run_selftest:
        raise SystemExit("External manifests require --run-selftest so the packaged app can execute them.")
    if _required_selftest_checks(args) and not args.run_selftest:
        raise SystemExit("Self-test check requirements need --run-selftest so the packaged app can execute them.")

    base_env = os.environ.copy()
    if sys.platform.startswith("linux"):
        base_env.setdefault("QT_QPA_PLATFORM", "offscreen")

    command = [str(launch_target)]
    repeats = max(1, int(args.repeat))
    for attempt in range(1, repeats + 1):
        print(f"Smoke launch {attempt}/{repeats}…")
        smoke_env = dict(base_env)
        smoke_env["ALPHA_FIXER_SMOKE_TEST"] = str(max(0.25, float(args.smoke_seconds)))
        result = _run_and_echo(command, env=smoke_env, timeout=max(1, int(args.timeout)))
        if result.returncode != 0:
            raise SystemExit(f"Packaged launch smoke test failed with exit code {result.returncode}.")

    print("Running packaged capability audit…")
    capability_env = dict(base_env)
    capability_env.pop("ALPHA_FIXER_SMOKE_TEST", None)
    capability_env["ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP"] = "1"
    capability_result = _run_and_echo(command, env=capability_env, timeout=max(1, int(args.timeout)))
    if capability_result.returncode != 0:
        raise SystemExit(f"Packaged capability audit failed with exit code {capability_result.returncode}.")

    payload = _capability_payload(capability_result.stdout or "")
    if args.require_video_runtime and not payload.get("video_runtime_ready"):
        raise SystemExit("Packaged runtime audit failed: video_runtime_ready=false")
    if args.require_odd_probe_ready and not payload.get("odd_container_probe_ready"):
        raise SystemExit("Packaged runtime audit failed: odd_container_probe_ready=false")
    if args.require_ffmpeg_selfcheck and not payload.get("ffmpeg_runtime_ready"):
        raise SystemExit(
            "Packaged runtime audit failed: ffmpeg_runtime_ready=false"
            + (f" ({payload.get('ffmpeg_runtime_detail')})" if payload.get("ffmpeg_runtime_detail") else "")
        )
    if args.require_ffprobe_selfcheck and not payload.get("ffprobe_runtime_ready"):
        raise SystemExit(
            "Packaged runtime audit failed: ffprobe_runtime_ready=false"
            + (f" ({payload.get('ffprobe_runtime_detail')})" if payload.get("ffprobe_runtime_detail") else "")
        )
    if args.require_wand_runtime and not payload.get("wand_runtime_ready"):
        raise SystemExit("Packaged runtime audit failed: wand_runtime_ready=false")
    missing_libs = payload.get("missing_linux_runtime_libs") or []
    if args.require_no_missing_libs and missing_libs:
        raise SystemExit(
            "Packaged runtime audit failed: missing_linux_runtime_libs="
            + ",".join(str(name) for name in missing_libs)
        )
    if args.require_bundled_ffmpeg and not payload.get("ffmpeg_bundled"):
        raise SystemExit("Packaged runtime audit failed: ffmpeg_bundled=false")
    if args.require_bundled_ffprobe and not payload.get("ffprobe_bundled"):
        raise SystemExit("Packaged runtime audit failed: ffprobe_bundled=false")
    if args.require_bundled_imagemagick and not payload.get("imagemagick_bundled"):
        raise SystemExit("Packaged runtime audit failed: imagemagick_bundled=false")
    packaged_asset_warnings = payload.get("packaged_asset_warnings") or []
    if args.require_no_packaged_asset_gaps and packaged_asset_warnings:
        raise SystemExit(
            "Packaged runtime audit failed: packaged_asset_warnings="
            + " | ".join(str(entry) for entry in packaged_asset_warnings)
        )
    if not payload.get("ffmpeg_runtime_ready"):
        print(
            "⚠️  Packaged runtime audit: ffmpeg failed its runtime self-check."
            + (f" {payload.get('ffmpeg_runtime_detail')}" if payload.get("ffmpeg_runtime_detail") else "")
        )
    if payload.get("ffprobe_path") and not payload.get("ffprobe_runtime_ready"):
        print(
            "⚠️  Packaged runtime audit: ffprobe failed its runtime self-check."
            + (f" {payload.get('ffprobe_runtime_detail')}" if payload.get("ffprobe_runtime_detail") else "")
        )
    elif not payload.get("odd_container_probe_ready"):
        print("⚠️  Packaged runtime audit: ffprobe unavailable, odd-container probing stays limited.")
    if not payload.get("dds_compression_available"):
        print("⚠️  Packaged runtime audit: DDS compressed variants remain unavailable without bundled ImageMagick/wand.")

    selftest_payload = None
    if args.run_selftest:
        print("Running packaged end-to-end self-test…")
        selftest_env = dict(base_env)
        selftest_env.pop("ALPHA_FIXER_SMOKE_TEST", None)
        selftest_env["ALPHA_FIXER_RUNTIME_SELFTEST"] = str(max(1, int(args.selftest_iterations)))
        if args.selftest_sample_limit:
            selftest_env["ALPHA_FIXER_RUNTIME_SAMPLE_LIMIT"] = str(max(1, int(args.selftest_sample_limit)))
        if merged_disc_manifest:
            selftest_env["ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST"] = merged_disc_manifest
        if merged_dds_manifest:
            selftest_env["ALPHA_FIXER_RUNTIME_DDS_MANIFEST"] = merged_dds_manifest
        if merged_format_manifest:
            selftest_env["ALPHA_FIXER_RUNTIME_FORMAT_MATRIX_MANIFEST"] = merged_format_manifest
        if args.allow_sample_downloads:
            selftest_env["ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS"] = "1"
        if args.sample_cache_dir:
            selftest_env["ALPHA_FIXER_RUNTIME_SAMPLE_CACHE_DIR"] = args.sample_cache_dir
        selftest_result = _run_and_echo(command, env=selftest_env, timeout=max(30, int(args.timeout)))
        if selftest_result.returncode not in (0, 1):
            raise SystemExit(f"Packaged runtime self-test failed with exit code {selftest_result.returncode}.")
        selftest_payload = _selftest_payload(selftest_result.stdout or "")
        if args.require_selftest_pass and not selftest_payload.get("passed"):
            raise SystemExit("Packaged runtime self-test reported passed=false")
        if args.max_selftest_rss_mb is not None:
            peak_rss = selftest_payload.get("peak_rss_mb")
            if peak_rss is not None and float(peak_rss) > float(args.max_selftest_rss_mb):
                raise SystemExit(
                    f"Packaged runtime self-test exceeded RSS limit: {peak_rss} MiB > {args.max_selftest_rss_mb} MiB"
                )
        checks = selftest_payload.get("checks")
        if not isinstance(checks, dict):
            checks = {}
        _print_selftest_check_summary(checks)
        for check_name in _required_selftest_checks(args):
            check = checks.get(check_name)
            if not isinstance(check, dict) or not check.get("ok"):
                raise SystemExit(f"Packaged runtime self-test check failed or missing: {check_name}")
        errors = selftest_payload.get("errors") or []
        if errors:
            print("⚠️  Packaged runtime self-test reported issues:")
            for entry in errors:
                print(f"   - {entry}")
    if args.json_out:
        json_out = Path(args.json_out)
        json_out.parent.mkdir(parents=True, exist_ok=True)
        final_payload = dict(payload)
        if selftest_payload is not None:
            final_payload["runtime_selftest"] = selftest_payload
        json_out.write_text(json.dumps(final_payload, indent=2, sort_keys=True), encoding="utf-8")
    print("✅  Packaged runtime capability audit verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
