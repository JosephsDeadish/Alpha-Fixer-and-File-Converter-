#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-launch and audit a built Alpha Fixer package.")
    parser.add_argument("launch_target", help="Path to the packaged executable/app entrypoint.")
    parser.add_argument("--smoke-seconds", type=float, default=1.5, help="Seconds to keep each smoke-launch alive.")
    parser.add_argument("--repeat", type=int, default=1, help="How many smoke-launch cycles to run before auditing capabilities.")
    parser.add_argument("--timeout", type=int, default=25, help="Per-launch timeout in seconds.")
    parser.add_argument("--require-video-runtime", action="store_true", help="Fail if video import / MP4 export runtime bits are unavailable.")
    parser.add_argument("--require-odd-probe-ready", action="store_true", help="Fail if ffprobe-backed odd-container probing is unavailable.")
    parser.add_argument("--require-no-missing-libs", action="store_true", help="Fail if packaged runtime reports missing Linux shared libraries.")
    parser.add_argument("--run-selftest", action="store_true", help="Run the packaged executable's end-to-end runtime self-test after the capability audit.")
    parser.add_argument("--selftest-iterations", type=int, default=2, help="How many self-test iterations the packaged app should run when --run-selftest is set.")
    parser.add_argument("--require-selftest-pass", action="store_true", help="Fail if the packaged runtime self-test reports passed=false.")
    parser.add_argument("--max-selftest-rss-mb", type=float, help="Optional upper bound for the packaged self-test peak RSS value when reported.")
    parser.add_argument("--require-selftest-check", action="append", default=[], help="Specific packaged self-test check key that must report ok=true. Repeat for multiple checks.")
    parser.add_argument("--json-out", help="Optional path to write the final runtime capability payload as JSON.")
    args = parser.parse_args(argv)

    launch_target = Path(args.launch_target)
    if not launch_target.exists():
        raise SystemExit(f"Launch target not found: {launch_target}")

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
    missing_libs = payload.get("missing_linux_runtime_libs") or []
    if args.require_no_missing_libs and missing_libs:
        raise SystemExit(
            "Packaged runtime audit failed: missing_linux_runtime_libs="
            + ",".join(str(name) for name in missing_libs)
        )
    if not payload.get("odd_container_probe_ready"):
        print("⚠️  Packaged runtime audit: ffprobe unavailable, odd-container probing stays limited.")
    if not payload.get("dds_compression_available"):
        print("⚠️  Packaged runtime audit: DDS compressed variants remain unavailable without bundled ImageMagick/wand.")

    selftest_payload = None
    if args.run_selftest:
        print("Running packaged end-to-end self-test…")
        selftest_env = dict(base_env)
        selftest_env.pop("ALPHA_FIXER_SMOKE_TEST", None)
        selftest_env["ALPHA_FIXER_RUNTIME_SELFTEST"] = str(max(1, int(args.selftest_iterations)))
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
        for check_name in args.require_selftest_check:
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
