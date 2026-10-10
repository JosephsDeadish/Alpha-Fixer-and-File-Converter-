#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.runtime_validation import (
    build_private_local_manifests_from_env,
    load_manifest_entries,
    manifest_coverage_review,
    manifest_grouped_entries,
    render_manifest_payload,
)
from src.core.file_converter import output_codec_selftest_checks
_PUBLIC_DISC_VIDEO_MANIFEST = _REPO_ROOT / "sample_manifests" / "public_disc_video_manifest.json"
_PUBLIC_DDS_MANIFEST = _REPO_ROOT / "sample_manifests" / "public_dds_dx10_manifest.json"
_PUBLIC_FORMAT_MATRIX_MANIFEST = _REPO_ROOT / "sample_manifests" / "public_format_matrix_manifest.json"
_PRIVATE_DISC_MANIFEST_ENV_NAMES = (
    "ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST",
    "ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST",
    "ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST",
)
_PRIVATE_DDS_MANIFEST_ENV_NAMES = (
    "ALPHA_FIXER_REAL_DDS_DX10_MANIFEST",
    "ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST",
)
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
_STRESS_SELFTEST_CHECKS = (
    "stress_image_session_batch",
    "stress_video_session_batch",
    "stress_builder_dialog_cycles",
    "stress_history_roundtrip",
    "stress_peak_rss_growth",
)
_DDS_SELFTEST_CHECKS = (
    "png_to_dds_rgba",
    "png_to_dds_dxt1",
    "png_to_dds_dxt3",
    "png_to_dds_dxt5",
)
_CORE_SELFTEST_CHECKS = (
    "png_to_gif",
    "png_to_dds_rgba",
    "generated_mp4_load",
    "mpegts_load",
    "synthetic_bin_probe",
    "svg_rasterization",
    "svg_vectorization",
)


def _record_launch_output(env: dict[str, str], stdout: str, status: object) -> None:
    log_path = env.get("ALPHA_FIXER_VALIDATION_LOG_OUT")
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write(f"\n--- Packaged launch: {status} ---\n")
            log.write(stdout)
            if not stdout.endswith("\n"):
                log.write("\n")


def _run_and_echo(command: list[str], *, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="alpha_fixer_validation_") as directory:
        output = Path(directory) / "result.json"
        child_env = dict(env)
        child_env["ALPHA_FIXER_VALIDATION_JSON_OUT"] = str(output)
        # A regular temporary file avoids Windows pipe-reader threads that can
        # block stream closure when a detached descendant survives a timeout.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as capture:
            process = subprocess.Popen(
                command,
                stdout=capture,
                stderr=subprocess.STDOUT,
                env=child_env,
                start_new_session=os.name != "nt",
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # One-file bootloaders and FFmpeg can leave descendants holding
                # the output pipe open if only the top-level process is killed.
                if os.name == "nt":
                    taskkill = Path(child_env.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
                    try:
                        subprocess.run(
                            [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=10, check=False,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                capture.seek(0)
                stdout = capture.read()
                _record_launch_output(env, stdout, "timed out")
                if stdout:
                    print(stdout, end="" if stdout.endswith("\n") else "\n")
                raise
            capture.seek(0)
            stdout = capture.read()
            result = subprocess.CompletedProcess(command, process.returncode, stdout=stdout)
        if output.is_file():
            data = json.loads(output.read_text(encoding="utf-8"))
            prefix = data.get("prefix")
            if prefix not in ("ALPHA_FIXER_RUNTIME_CAPABILITIES", "ALPHA_FIXER_RUNTIME_SELFTEST"):
                raise ValueError("Packaged executable emitted an unknown validation payload.")
            if not isinstance(data.get("payload"), dict):
                raise ValueError("Packaged executable emitted a non-object validation payload.")
            # Use the file channel when a windowed executable has no stdout.
            if not any(line.startswith(prefix + "=") for line in (result.stdout or "").splitlines()):
                result.stdout = (result.stdout or "") + "\n" + prefix + "=" + json.dumps(data["payload"]) + "\n"
    _record_launch_output(env, result.stdout or "", f"exit code {result.returncode}")
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    return result


def _verification_environment(offline: bool) -> dict[str, str]:
    env = os.environ.copy()
    # Each launch must exercise its requested mode, not an inherited audit or
    # self-test that can return success without ever displaying the application.
    for name in (
        "ALPHA_FIXER_SMOKE_TEST", "ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP",
        "ALPHA_FIXER_RUNTIME_SELFTEST", "ALPHA_FIXER_RUNTIME_STRESS_LOOPS",
        "ALPHA_FIXER_RUNTIME_SAMPLE_LIMIT", "ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST",
        "ALPHA_FIXER_RUNTIME_DDS_MANIFEST", "ALPHA_FIXER_RUNTIME_FORMAT_MATRIX_MANIFEST",
        "ALPHA_FIXER_RUNTIME_DISC_GROUP_CHECKS", "ALPHA_FIXER_RUNTIME_DDS_GROUP_CHECKS",
        "ALPHA_FIXER_RUNTIME_FORMAT_GROUP_CHECKS", "ALPHA_FIXER_RUNTIME_SAMPLE_CACHE_DIR",
        "ALPHA_FIXER_VALIDATION_JSON_OUT", "ALPHA_FIXER_VALIDATION_LOG_OUT",
    ):
        env.pop(name, None)
    env["ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS"] = "0"
    env["ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS"] = "0"
    if offline:
        for name in (
            "PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
            "MAGICK_HOME", "IMAGEMAGICK_HOME", "MAGICK_CONFIGURE_PATH",
            "MAGICK_CODER_MODULE_PATH", "WAND_MAGICK_LIBRARY_SUFFIX",
            "IMAGEIO_FFMPEG_EXE", "IMAGEIO_FFPROBE_EXE", "FFPROBE_EXE",
            "ALPHA_FIXER_FFPROBE_EXE", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
            "ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS", "ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS",
        ):
            env.pop(name, None)
        # Keep only OS executables on Windows; no Python, ffmpeg or ImageMagick PATH.
        env["PATH"] = str(Path(env.get("SystemRoot", r"C:\Windows")) / "System32") if os.name == "nt" else ""
        env["ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS"] = "0"
        env["ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS"] = "0"
    if sys.platform.startswith("linux"):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return env


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


def _manifest_values_from_env(*env_names: str) -> list[str]:
    values: list[str] = []
    for env_name in env_names:
        raw = str(os.environ.get(env_name, "") or "").strip()
        if not raw:
            continue
        if raw.startswith("{") or raw.startswith("["):
            values.append(raw)
            continue
        for part in raw.split(os.pathsep):
            candidate = str(part or "").strip()
            if candidate:
                values.append(candidate)
    return values


def _required_selftest_checks(args) -> list[str]:
    required = [str(name or "").strip() for name in getattr(args, "require_selftest_check", []) if str(name or "").strip()]
    for attr_name, names in (
        ("require_core_selftest_checks", _CORE_SELFTEST_CHECKS),
        ("require_video_selftest_checks", _VIDEO_SELFTEST_CHECKS),
        ("require_stress_selftest_checks", _STRESS_SELFTEST_CHECKS),
        ("require_dds_selftest_checks", _DDS_SELFTEST_CHECKS),
    ):
        if getattr(args, attr_name, False):
            for name in names:
                if name not in required:
                    required.append(name)
    if getattr(args, "require_output_codec_checks", False):
        for name in output_codec_selftest_checks():
            if name not in required:
                required.append(name)
    if getattr(args, "require_public_manifest_checks", False):
        for name in _PUBLIC_MANIFEST_CHECKS:
            if name not in required:
                required.append(name)
    return required


def _manifest_group_requirement_checks(raw_manifest: str | None, base_check: str, *keys: str) -> list[str]:
    if not raw_manifest:
        return []
    entries = load_manifest_entries(raw_manifest)
    return [
        f"{base_check}_{suffix}"
        for suffix, _label, _entries in manifest_grouped_entries(entries, *keys)
    ]


def _manifest_input_summary(raw_manifest: str | None, *group_keys: str) -> dict[str, object]:
    entries = load_manifest_entries(raw_manifest or "")
    summary: dict[str, object] = {
        "provided": bool(raw_manifest),
        "entry_count": len(entries),
    }
    grouped = manifest_grouped_entries(entries, *group_keys) if entries and group_keys else []
    if grouped:
        summary["groups"] = [
            {"suffix": suffix, "label": label, "entry_count": len(group_entries)}
            for suffix, label, group_entries in grouped
        ]
    return summary


def _required_label_args(values: list[str] | None) -> list[str]:
    return [str(value or "").strip() for value in (values or []) if str(value or "").strip()]


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


def _print_manifest_result_summary(manifest_results: dict[str, object]) -> None:
    if not isinstance(manifest_results, dict) or not manifest_results:
        return
    print("Manifest result summary:")
    for base_key, group_key in (
        ("disc_video", "disc_video_groups"),
        ("dds", "dds_groups"),
        ("format_matrix", "format_matrix_groups"),
    ):
        report = manifest_results.get(base_key)
        if isinstance(report, dict):
            detail = str(report.get("detail") or "").strip()
            line = f"  - {base_key}: {'ok' if report.get('ok') else 'failed'}"
            if detail:
                line += f" ({detail})"
            print(line)
        grouped = manifest_results.get(group_key)
        if isinstance(grouped, dict):
            for suffix in sorted(grouped):
                group_report = grouped.get(suffix)
                if not isinstance(group_report, dict):
                    continue
                label = str(group_report.get("label") or suffix)
                detail = str(group_report.get("detail") or "").strip()
                line = f"    * {label}: {'ok' if group_report.get('ok') else 'failed'}"
                if detail:
                    line += f" ({detail})"
                print(line)
    dds_policy_groups = manifest_results.get("dds_policy_groups")
    if isinstance(dds_policy_groups, dict) and dds_policy_groups:
        print("  - dds_policy_groups:")
        for axis in ("surface_kind", "decode_policy", "export_policy", "policy_status"):
            grouped = dds_policy_groups.get(axis)
            if not isinstance(grouped, dict) or not grouped:
                continue
            print(f"    * {axis}:")
            for suffix in sorted(grouped):
                group_report = grouped.get(suffix)
                if not isinstance(group_report, dict):
                    continue
                label = str(group_report.get("label") or suffix)
                detail = str(group_report.get("detail") or "").strip()
                line = f"      - {label}: {'ok' if group_report.get('ok') else 'failed'}"
                if detail:
                    line += f" ({detail})"
                print(line)
    interesting: list[str] = []
    for base_key in ("disc_video", "dds", "format_matrix"):
        report = manifest_results.get(base_key)
        if not isinstance(report, dict):
            continue
        for sample in report.get("sample_results") or []:
            if not isinstance(sample, dict):
                continue
            status = str(sample.get("status") or "").strip().lower()
            if status not in {"failed", "explained", "expected_failure"}:
                continue
            label = str(sample.get("label") or sample.get("path") or "sample").strip()
            detail = str(sample.get("detail") or "").strip()
            dds_policy_bits = []
            if base_key == "dds":
                for key in ("surface_kind", "decode_policy", "export_policy", "policy_status"):
                    value = str(sample.get(key) or "").strip()
                    if value:
                        dds_policy_bits.append(f"{key}={value}")
            policy_suffix = f" ({', '.join(dds_policy_bits)})" if dds_policy_bits else ""
            interesting.append(f"  - {base_key} [{status}] {label}{policy_suffix}" + (f": {detail}" if detail else ""))
    if interesting:
        print("Manifest sample outcomes:")
        for line in interesting[:12]:
            print(line)
        if len(interesting) > 12:
            print(f"  - …and {len(interesting) - 12} more")


def _selftest_peak_rss_series(payloads: list[dict[str, object]]) -> list[float]:
    values: list[float] = []
    for payload in payloads:
        try:
            raw = payload.get("peak_rss_mb")
            if raw is None:
                continue
            values.append(float(raw))
        except Exception:
            continue
    return values


def _selftest_repeat_summary(payloads: list[dict[str, object]]) -> dict[str, object]:
    peaks = _selftest_peak_rss_series(payloads)
    summary: dict[str, object] = {
        "runs": len(payloads),
        "peak_rss_mb_values": peaks,
    }
    if peaks:
        summary["peak_rss_mb_min"] = round(min(peaks), 2)
        summary["peak_rss_mb_max"] = round(max(peaks), 2)
        summary["peak_rss_mb_spread"] = round(max(peaks) - min(peaks), 2)
        summary["peak_rss_mb_growth"] = round(peaks[-1] - peaks[0], 2)
    return summary


def _failed_selftest_checks(payload: dict[str, object] | None) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        return []
    failures: list[dict[str, str]] = []
    for name in sorted(checks):
        check = checks.get(name)
        if not isinstance(check, dict) or bool(check.get("ok")):
            continue
        failures.append(
            {
                "name": str(name),
                "detail": str(check.get("detail") or "").strip(),
            }
        )
    return failures


def _interesting_manifest_outcomes(
    manifest_results: dict[str, object] | None,
    *,
    limit: int = 0,
) -> list[dict[str, str]]:
    if not isinstance(manifest_results, dict):
        return []
    interesting: list[dict[str, str]] = []
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
            entry = {
                "manifest": str(base_key),
                "status": status,
                "label": str(sample.get("label") or sample.get("path") or "sample").strip(),
                "detail": str(sample.get("detail") or "").strip(),
                "stage": str(sample.get("stage") or "").strip(),
            }
            if base_key == "dds":
                for key in ("surface_kind", "decode_policy", "export_policy", "policy_status"):
                    value = str(sample.get(key) or "").strip()
                    if value:
                        entry[key] = value
            interesting.append(entry)
            if limit > 0 and len(interesting) >= limit:
                return interesting
    return interesting


def _group_review(grouped: object) -> dict[str, object]:
    groups = grouped if isinstance(grouped, dict) else {}
    labels: list[str] = []
    ok_count = 0
    failed_count = 0
    for suffix in sorted(groups):
        report = groups.get(suffix)
        if not isinstance(report, dict):
            continue
        labels.append(str(report.get("label") or suffix))
        if bool(report.get("ok")):
            ok_count += 1
        else:
            failed_count += 1
    return {
        "total": len(labels),
        "ok": ok_count,
        "failed": failed_count,
        "labels": labels,
    }


def _manifest_result_review(manifest_results: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(manifest_results, dict):
        return {}
    review: dict[str, object] = {}
    for base_key, group_key in (
        ("disc_video", "disc_video_groups"),
        ("dds", "dds_groups"),
        ("format_matrix", "format_matrix_groups"),
    ):
        report = manifest_results.get(base_key)
        if not isinstance(report, dict):
            continue
        sample_status_counts: dict[str, int] = {}
        for sample in report.get("sample_results") or []:
            if not isinstance(sample, dict):
                continue
            status = str(sample.get("status") or "").strip().lower() or "unknown"
            sample_status_counts[status] = sample_status_counts.get(status, 0) + 1
        review[base_key] = {
            "ok": bool(report.get("ok")),
            "detail": str(report.get("detail") or "").strip(),
            "sample_count": len(report.get("sample_results") or []),
            "sample_status_counts": sample_status_counts,
            "group_review": _group_review(manifest_results.get(group_key)),
        }
    dds_policy_groups = manifest_results.get("dds_policy_groups")
    if isinstance(dds_policy_groups, dict):
        policy_review: dict[str, object] = {}
        for axis in ("surface_kind", "decode_policy", "export_policy", "policy_status"):
            grouped = dds_policy_groups.get(axis)
            axis_review = _group_review(grouped)
            if axis_review.get("total"):
                policy_review[axis] = axis_review
        if policy_review:
            review["dds_policy_groups"] = policy_review
    return review


def _selftest_check_repeat_summary(payloads: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {
        "runs": len(payloads),
        "checks": {},
        "unstable_checks": [],
    }
    per_check: dict[str, dict[str, object]] = {}
    total_runs = len(payloads)
    for payload in payloads:
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            checks = {}
        seen_in_run: set[str] = set()
        for name, check in checks.items():
            key = str(name)
            entry = per_check.setdefault(
                key,
                {
                    "ok_runs": 0,
                    "failed_runs": 0,
                    "missing_runs": 0,
                    "details": [],
                },
            )
            seen_in_run.add(key)
            ok = isinstance(check, dict) and bool(check.get("ok"))
            if ok:
                entry["ok_runs"] = int(entry.get("ok_runs") or 0) + 1
            else:
                entry["failed_runs"] = int(entry.get("failed_runs") or 0) + 1
                detail = str(check.get("detail") or "").strip() if isinstance(check, dict) else ""
                if detail and detail not in entry["details"]:
                    entry["details"].append(detail)
        for key, entry in per_check.items():
            if key not in seen_in_run:
                entry["missing_runs"] = int(entry.get("missing_runs") or 0) + 1
    unstable: list[str] = []
    finalized: dict[str, dict[str, object]] = {}
    for key in sorted(per_check):
        entry = per_check[key]
        stable_ok = (
            int(entry.get("ok_runs") or 0) == total_runs
            and int(entry.get("failed_runs") or 0) == 0
            and int(entry.get("missing_runs") or 0) == 0
        )
        finalized[key] = {
            "ok_runs": int(entry.get("ok_runs") or 0),
            "failed_runs": int(entry.get("failed_runs") or 0),
            "missing_runs": int(entry.get("missing_runs") or 0),
            "stable_ok": stable_ok,
            "details": list(entry.get("details") or []),
        }
        if not stable_ok:
            unstable.append(key)
    summary["checks"] = finalized
    summary["unstable_checks"] = unstable
    return summary


def _smoke_repeat_summary(runs: list[dict[str, object]]) -> dict[str, object]:
    elapsed_values: list[float] = []
    exit_codes: list[int] = []
    for run in runs:
        try:
            elapsed_values.append(round(float(run.get("elapsed_seconds") or 0.0), 3))
        except Exception:
            pass
        try:
            exit_codes.append(int(run.get("returncode") or 0))
        except Exception:
            pass
    summary: dict[str, object] = {
        "runs": len(runs),
        "successful_runs": sum(1 for code in exit_codes if code == 0),
        "exit_codes": exit_codes,
        "elapsed_seconds_values": elapsed_values,
    }
    if elapsed_values:
        summary["elapsed_seconds_min"] = round(min(elapsed_values), 3)
        summary["elapsed_seconds_max"] = round(max(elapsed_values), 3)
        summary["elapsed_seconds_spread"] = round(max(elapsed_values) - min(elapsed_values), 3)
        summary["elapsed_seconds_growth"] = round(elapsed_values[-1] - elapsed_values[0], 3)
    return summary


def _validation_bundle_kind(raw_value: object) -> str:
    value = str(raw_value or "").strip().lower()
    if value in {"folder", "onefile"}:
        return value
    return "unspecified"


def main(argv: list[str] | None = None) -> int:
    # Redirected Windows consoles may use cp1252 even when reports and filenames
    # contain Unicode. Preserve that encoding, but never fail a valid audit when
    # rendering an unsupported character.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Smoke-launch and audit a built Alpha Fixer package.")
    parser.add_argument("launch_target", help="Path to the packaged executable/app entrypoint.")
    parser.add_argument("--bundle-kind", choices=("folder", "onefile"), help="Optional packaged artifact kind label to include in reports.")
    parser.add_argument("--offline", action="store_true", help="Clear external tool/runtime paths and forbid sample downloads. Does not replace clean-machine OS testing.")
    parser.add_argument("--smoke-seconds", type=float, default=1.5, help="Seconds to keep each smoke-launch alive.")
    parser.add_argument("--repeat", type=int, default=1, help="How many smoke-launch cycles to run before auditing capabilities.")
    parser.add_argument("--smoke-launch-delay-seconds", type=float, default=0.0, help="Optional pause between repeated smoke launches.")
    parser.add_argument("--timeout", type=int, default=25, help="Per-launch timeout in seconds.")
    parser.add_argument("--max-smoke-elapsed-seconds", type=float, help="Optional upper bound for any single packaged smoke-launch elapsed time.")
    parser.add_argument("--max-smoke-elapsed-growth-seconds", type=float, help="Optional upper bound for last-minus-first packaged smoke-launch elapsed time across repeated runs.")
    parser.add_argument("--max-smoke-elapsed-spread-seconds", type=float, help="Optional upper bound for max-minus-min packaged smoke-launch elapsed time across repeated runs.")
    parser.add_argument("--require-video-runtime", action="store_true", help="Fail if video import / MP4 export runtime bits are unavailable.")
    parser.add_argument("--require-odd-probe-ready", action="store_true", help="Fail if ffprobe-backed odd-container probing is unavailable.")
    parser.add_argument("--require-ffmpeg-selfcheck", action="store_true", help="Fail if the packaged runtime cannot execute the resolved ffmpeg binary successfully.")
    parser.add_argument("--require-ffprobe-selfcheck", action="store_true", help="Fail if the packaged runtime cannot execute the resolved ffprobe binary successfully.")
    parser.add_argument("--require-wand-runtime", action="store_true", help="Fail if the packaged runtime lacks a working ImageMagick/wand runtime.")
    parser.add_argument("--require-no-missing-libs", action="store_true", help="Fail if packaged runtime reports missing Linux shared libraries.")
    parser.add_argument("--require-bundled-ffmpeg", action="store_true", help="Fail if ffmpeg resolves outside the packaged bundle.")
    parser.add_argument("--require-bundled-ffprobe", action="store_true", help="Fail if ffprobe resolves outside the packaged bundle.")
    parser.add_argument("--require-bundled-default-theme-svg", action="store_true", help="Fail if the packaged default-theme SVG asset resolves outside the packaged app.")
    parser.add_argument("--require-bundled-imagemagick", action="store_true", help="Fail if ImageMagick/wand support is not bundled inside the packaged app.")
    parser.add_argument("--require-packaged-bundle-ready", action="store_true", help="Fail if the packaged runtime audit reports packaged_bundle_ready=false.")
    parser.add_argument("--require-no-packaged-asset-gaps", action="store_true", help="Fail if the packaged runtime audit reports any packaged asset warnings.")
    parser.add_argument("--run-selftest", action="store_true", help="Run the packaged executable's end-to-end runtime self-test after the capability audit.")
    parser.add_argument("--selftest-iterations", type=int, default=2, help="How many self-test iterations the packaged app should run when --run-selftest is set.")
    parser.add_argument("--selftest-sample-limit", type=int, default=0, help="Optional cap for external manifest entries exercised per packaged self-test run.")
    parser.add_argument("--selftest-stress-loops", type=int, default=0, help="Optional number of larger generated media/session stress loops to run during packaged self-test.")
    parser.add_argument("--repeat-selftest-runs", type=int, default=1, help="How many separate packaged self-test launches to run and compare.")
    parser.add_argument("--selftest-timeout", type=int, default=120, help="Per-self-test-launch timeout in seconds.")
    parser.add_argument("--require-selftest-pass", action="store_true", help="Fail if the packaged runtime self-test reports passed=false.")
    parser.add_argument("--max-selftest-rss-mb", type=float, help="Optional upper bound for the packaged self-test peak RSS value when reported.")
    parser.add_argument("--max-selftest-rss-growth-mb", type=float, help="Optional upper bound for last-minus-first peak RSS across repeated self-test runs.")
    parser.add_argument("--max-selftest-rss-spread-mb", type=float, help="Optional upper bound for max-minus-min peak RSS across repeated self-test runs.")
    parser.add_argument("--require-selftest-check", action="append", default=[], help="Specific packaged self-test check key that must report ok=true. Repeat for multiple checks.")
    parser.add_argument("--require-core-selftest-checks", action="store_true", help="Fail unless the built-in PNG/GIF/DDS and generated-video self-test checks all report ok=true.")
    parser.add_argument("--require-output-codec-checks", action="store_true", help="Fail unless every advertised output has a successful generated encode/decode check (no missing or skipped checks).")
    parser.add_argument("--require-video-selftest-checks", action="store_true", help="Fail unless generated MP4, MPEG-TS, and odd-container BIN self-test checks all report ok=true.")
    parser.add_argument("--require-stress-selftest-checks", action="store_true", help="Fail unless the packaged self-test stress batch checks report ok=true.")
    parser.add_argument("--require-dds-selftest-checks", action="store_true", help="Fail unless the built-in DDS self-test checks, including compressed DDS output when available, all report ok=true.")
    parser.add_argument("--require-public-manifest-checks", action="store_true", help="Fail unless the public disc-video, DDS/DX10, and format-matrix self-test checks all report ok=true.")
    parser.add_argument("--require-public-manifest-group-checks", action="store_true", help="Fail unless every platform/group/format subgroup represented in the loaded public manifests also reports ok=true.")
    parser.add_argument("--require-disc-manifest-group-checks", action="store_true", help="Fail unless the packaged self-test reports ok=true for every platform/group represented in the supplied disc-video manifest.")
    parser.add_argument("--require-dds-manifest-group-checks", action="store_true", help="Fail unless the packaged self-test reports ok=true for every group/family represented in the supplied DDS manifest.")
    parser.add_argument("--require-format-manifest-group-checks", action="store_true", help="Fail unless the packaged self-test reports ok=true for every target format represented in the supplied format-matrix manifest.")
    parser.add_argument("--require-disc-manifest-platform", action="append", default=[], help="Require the supplied disc-video manifest to include at least one entry for this platform/system label. Repeat for multiple labels.")
    parser.add_argument("--require-disc-manifest-group", action="append", default=[], help="Require the supplied disc-video manifest to include at least one entry for this group label. Repeat for multiple labels.")
    parser.add_argument("--require-dds-manifest-group", action="append", default=[], help="Require the supplied DDS manifest to include at least one entry for this group/family label. Repeat for multiple labels.")
    parser.add_argument("--require-format-manifest-target", action="append", default=[], help="Require the supplied format-matrix manifest to include at least one entry for this target/output format label. Repeat for multiple labels.")
    parser.add_argument("--disc-video-manifest", action="append", default=[], help="Optional external PSP/PS1/PS2 disc-video manifest (path or inline JSON) for packaged self-test execution. Repeat to merge multiple manifests.")
    parser.add_argument("--dds-manifest", action="append", default=[], help="Optional external DDS/DX10 manifest (path or inline JSON) for packaged self-test execution. Repeat to merge multiple manifests.")
    parser.add_argument("--format-matrix-manifest", action="append", default=[], help="Optional external packaged conversion-matrix manifest (path or inline JSON) for packaged self-test execution. Repeat to merge multiple manifests.")
    parser.add_argument("--use-public-sample-manifests", action="store_true", help="Use the repository's built-in public disc-video, DDS/DX10, and format-matrix manifests for packaged self-test execution.")
    parser.add_argument("--use-private-local-manifests", action="store_true", help="Load private local disc/video and DDS manifests from ALPHA_FIXER_REAL_* environment variables and merge them into the packaged self-test run.")
    parser.add_argument("--private-manifest-auto-limit", type=int, default=32, help="Maximum auto-discovered entries per private manifest when --use-private-local-manifests falls back to corpus-root scanning.")
    parser.add_argument("--allow-sample-downloads", action="store_true", help="Allow manifest-backed self-tests to download missing external samples when URL fields are present.")
    parser.add_argument("--sample-cache-dir", help="Optional cache directory for downloaded or materialized manifest samples.")
    parser.add_argument("--json-out", help="Optional path to write the final runtime capability payload as JSON.")
    args = parser.parse_args(argv)

    launch_target = Path(args.launch_target).resolve()
    if not launch_target.exists():
        raise SystemExit(f"Launch target not found: {launch_target}")
    bundle_kind = _validation_bundle_kind(getattr(args, "bundle_kind", ""))
    if args.use_public_sample_manifests:
        if not args.disc_video_manifest:
            args.disc_video_manifest = [str(_PUBLIC_DISC_VIDEO_MANIFEST)]
        if not args.dds_manifest:
            args.dds_manifest = [str(_PUBLIC_DDS_MANIFEST)]
        if not args.format_matrix_manifest:
            args.format_matrix_manifest = [str(_PUBLIC_FORMAT_MATRIX_MANIFEST)]
    if args.use_private_local_manifests:
        args.disc_video_manifest.extend(_manifest_values_from_env(*_PRIVATE_DISC_MANIFEST_ENV_NAMES))
        args.dds_manifest.extend(_manifest_values_from_env(*_PRIVATE_DDS_MANIFEST_ENV_NAMES))
        if not args.disc_video_manifest and not args.dds_manifest:
            discovered = build_private_local_manifests_from_env(limit=max(1, int(args.private_manifest_auto_limit)))
            disc_entries = list(discovered.get("disc_video") or []) + list(discovered.get("odd_video") or [])
            dds_entries = list(discovered.get("dds") or [])
            if disc_entries:
                args.disc_video_manifest.append(render_manifest_payload(disc_entries))
            if dds_entries:
                args.dds_manifest.append(render_manifest_payload(dds_entries))
            if not args.disc_video_manifest and not args.dds_manifest:
                private_envs = ", ".join((*_PRIVATE_DISC_MANIFEST_ENV_NAMES, *_PRIVATE_DDS_MANIFEST_ENV_NAMES))
                raise SystemExit(
                    "No private local manifests were found in the configured environment variables, "
                    "and no discoverable private corpus samples were found in the configured ALPHA_FIXER_REAL_* corpus directories: "
                    f"{private_envs}"
                )
    merged_disc_manifest = _merged_manifest_arg(args.disc_video_manifest)
    merged_dds_manifest = _merged_manifest_arg(args.dds_manifest)
    merged_format_manifest = _merged_manifest_arg(args.format_matrix_manifest)
    if args.require_public_manifest_group_checks and not (
        merged_disc_manifest and merged_dds_manifest and merged_format_manifest
    ):
        raise SystemExit(
            "Public manifest group checks require disc-video, DDS, and format manifests. "
            "Use --use-public-sample-manifests for the built-in set."
        )
    manifest_group_required_checks: list[str] = []
    require_disc_group_checks = args.require_disc_manifest_group_checks or args.require_public_manifest_group_checks
    require_dds_group_checks = args.require_dds_manifest_group_checks or args.require_public_manifest_group_checks
    require_format_group_checks = args.require_format_manifest_group_checks or args.require_public_manifest_group_checks
    required_disc_platforms = _required_label_args(args.require_disc_manifest_platform)
    required_disc_groups = _required_label_args(args.require_disc_manifest_group)
    required_dds_groups = _required_label_args(args.require_dds_manifest_group)
    required_format_targets = _required_label_args(args.require_format_manifest_target)
    manifest_inputs = {
        "disc_video": _manifest_input_summary(merged_disc_manifest, "platform", "system", "group"),
        "dds": _manifest_input_summary(merged_dds_manifest, "group", "platform", "family"),
        "format_matrix": _manifest_input_summary(merged_format_manifest, "target_format", "output_format", "format"),
        "used_public_sample_manifests": bool(args.use_public_sample_manifests),
        "used_private_local_manifests": bool(args.use_private_local_manifests),
    }
    disc_entries = load_manifest_entries(merged_disc_manifest or "")
    dds_entries = load_manifest_entries(merged_dds_manifest or "")
    format_entries = load_manifest_entries(merged_format_manifest or "")
    manifest_inputs["disc_video"]["coverage_review"] = {
        "platform": manifest_coverage_review(disc_entries, required_disc_platforms, "platform", "system"),
        "group": manifest_coverage_review(disc_entries, required_disc_groups, "group"),
    }
    manifest_inputs["dds"]["coverage_review"] = {
        "group": manifest_coverage_review(dds_entries, required_dds_groups, "group", "family", "platform"),
    }
    manifest_inputs["format_matrix"]["coverage_review"] = {
        "target": manifest_coverage_review(format_entries, required_format_targets, "target_format", "output_format", "format"),
    }
    coverage_errors: list[str] = []
    disc_platform_review = manifest_inputs["disc_video"]["coverage_review"]["platform"]
    disc_group_review = manifest_inputs["disc_video"]["coverage_review"]["group"]
    dds_group_review = manifest_inputs["dds"]["coverage_review"]["group"]
    format_target_review = manifest_inputs["format_matrix"]["coverage_review"]["target"]
    if required_disc_platforms and disc_platform_review.get("missing_labels"):
        coverage_errors.append(
            "Disc-video manifest missing required platform labels: "
            + ", ".join(str(label) for label in disc_platform_review["missing_labels"])
        )
    if required_disc_groups and disc_group_review.get("missing_labels"):
        coverage_errors.append(
            "Disc-video manifest missing required group labels: "
            + ", ".join(str(label) for label in disc_group_review["missing_labels"])
        )
    if required_dds_groups and dds_group_review.get("missing_labels"):
        coverage_errors.append(
            "DDS manifest missing required group labels: "
            + ", ".join(str(label) for label in dds_group_review["missing_labels"])
        )
    if required_format_targets and format_target_review.get("missing_labels"):
        coverage_errors.append(
            "Format-matrix manifest missing required target labels: "
            + ", ".join(str(label) for label in format_target_review["missing_labels"])
        )
    if coverage_errors:
        raise SystemExit(" ; ".join(coverage_errors))
    if require_disc_group_checks:
        disc_group_checks = _manifest_group_requirement_checks(
            merged_disc_manifest,
            "external_disc_video_manifest",
            "platform",
            "system",
            "group",
        )
        if not disc_group_checks:
            raise SystemExit("Disc manifest group checks require a disc-video manifest with platform/group labels.")
        manifest_group_required_checks.extend(disc_group_checks)
    if require_dds_group_checks:
        dds_group_checks = _manifest_group_requirement_checks(
            merged_dds_manifest,
            "external_dds_manifest",
            "group",
            "platform",
            "family",
        )
        if not dds_group_checks:
            raise SystemExit("DDS manifest group checks require a DDS manifest with group/family labels.")
        for check_name in dds_group_checks:
            if check_name not in manifest_group_required_checks:
                manifest_group_required_checks.append(check_name)
    if require_format_group_checks:
        format_group_checks = _manifest_group_requirement_checks(
            merged_format_manifest,
            "external_format_matrix_manifest",
            "target_format",
            "output_format",
            "format",
        )
        if not format_group_checks:
            raise SystemExit("Format-manifest group checks require a format-matrix manifest with target_format/output_format labels.")
        for check_name in format_group_checks:
            if check_name not in manifest_group_required_checks:
                manifest_group_required_checks.append(check_name)
    if (merged_disc_manifest or merged_dds_manifest or merged_format_manifest) and not args.run_selftest:
        raise SystemExit("External manifests require --run-selftest so the packaged app can execute them.")
    if (_required_selftest_checks(args) or manifest_group_required_checks) and not args.run_selftest:
        raise SystemExit("Self-test check requirements need --run-selftest so the packaged app can execute them.")

    if args.offline and args.allow_sample_downloads:
        raise SystemExit("--offline cannot be combined with --allow-sample-downloads.")
    base_env = _verification_environment(args.offline)
    if args.json_out:
        log_path = Path(args.json_out).with_suffix(".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        base_env["ALPHA_FIXER_VALIDATION_LOG_OUT"] = str(log_path.absolute())

    command = [str(launch_target)]
    repeats = max(1, int(args.repeat))
    smoke_runs: list[dict[str, object]] = []
    for attempt in range(1, repeats + 1):
        print(f"Smoke launch {attempt}/{repeats}…")
        smoke_env = dict(base_env)
        smoke_env["ALPHA_FIXER_SMOKE_TEST"] = str(max(0.25, float(args.smoke_seconds)))
        started = time.monotonic()
        result = _run_and_echo(command, env=smoke_env, timeout=max(1, int(args.timeout)))
        elapsed = round(max(0.0, time.monotonic() - started), 3)
        smoke_runs.append(
            {
                "attempt": attempt,
                "returncode": int(result.returncode),
                "elapsed_seconds": elapsed,
            }
        )
        if result.returncode != 0:
            raise SystemExit(f"Packaged launch smoke test failed with exit code {result.returncode}.")
        if args.max_smoke_elapsed_seconds is not None and elapsed > float(args.max_smoke_elapsed_seconds):
            raise SystemExit(
                f"Packaged smoke-launch elapsed time exceeded limit: {elapsed} s > {args.max_smoke_elapsed_seconds} s"
            )
        if float(args.smoke_launch_delay_seconds or 0.0) > 0 and attempt < repeats:
            time.sleep(max(0.0, float(args.smoke_launch_delay_seconds)))
    smoke_summary = _smoke_repeat_summary(smoke_runs)
    smoke_elapsed_values = smoke_summary.get("elapsed_seconds_values") or []
    if repeats > 1 and smoke_elapsed_values:
        print(
            "Repeated smoke-launch elapsed seconds: "
            + ", ".join(f"{float(value):.3f}" for value in smoke_elapsed_values)
            + f" (growth={float(smoke_summary.get('elapsed_seconds_growth') or 0.0):.3f}, "
            + f"spread={float(smoke_summary.get('elapsed_seconds_spread') or 0.0):.3f})"
        )
    if args.max_smoke_elapsed_growth_seconds is not None:
        if len(smoke_elapsed_values) < repeats:
            raise SystemExit("Smoke-launch growth check needs elapsed seconds from every repeated run.")
        growth = float(smoke_summary.get("elapsed_seconds_growth") or 0.0)
        if growth > float(args.max_smoke_elapsed_growth_seconds):
            raise SystemExit(
                "Packaged smoke-launch elapsed-time growth exceeded limit: "
                f"{growth} s > {args.max_smoke_elapsed_growth_seconds} s"
            )
    if args.max_smoke_elapsed_spread_seconds is not None:
        if len(smoke_elapsed_values) < repeats:
            raise SystemExit("Smoke-launch spread check needs elapsed seconds from every repeated run.")
        spread = float(smoke_summary.get("elapsed_seconds_spread") or 0.0)
        if spread > float(args.max_smoke_elapsed_spread_seconds):
            raise SystemExit(
                "Packaged smoke-launch elapsed-time spread exceeded limit: "
                f"{spread} s > {args.max_smoke_elapsed_spread_seconds} s"
            )

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
    if args.require_bundled_default_theme_svg and not payload.get("default_theme_svg_bundled"):
        raise SystemExit("Packaged runtime audit failed: default_theme_svg_bundled=false")
    if args.require_bundled_imagemagick and not payload.get("imagemagick_bundled"):
        raise SystemExit("Packaged runtime audit failed: imagemagick_bundled=false")
    if args.require_packaged_bundle_ready and not payload.get("packaged_bundle_ready"):
        raise SystemExit("Packaged runtime audit failed: packaged_bundle_ready=false")
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
    selftest_runs: list[dict[str, object]] = []
    if args.run_selftest:
        required_checks = _required_selftest_checks(args)
        for check_name in manifest_group_required_checks:
            if check_name not in required_checks:
                required_checks.append(check_name)
        repeated_runs = max(1, int(args.repeat_selftest_runs))
        for run_index in range(1, repeated_runs + 1):
            if repeated_runs > 1:
                print(f"Running packaged end-to-end self-test {run_index}/{repeated_runs}…")
            else:
                print("Running packaged end-to-end self-test…")
            selftest_env = dict(base_env)
            selftest_env.pop("ALPHA_FIXER_SMOKE_TEST", None)
            selftest_env["ALPHA_FIXER_RUNTIME_SELFTEST"] = str(max(1, int(args.selftest_iterations)))
            if args.selftest_sample_limit:
                selftest_env["ALPHA_FIXER_RUNTIME_SAMPLE_LIMIT"] = str(max(1, int(args.selftest_sample_limit)))
            if args.selftest_stress_loops:
                selftest_env["ALPHA_FIXER_RUNTIME_STRESS_LOOPS"] = str(max(1, int(args.selftest_stress_loops)))
            if merged_disc_manifest:
                selftest_env["ALPHA_FIXER_RUNTIME_DISC_VIDEO_MANIFEST"] = merged_disc_manifest
            if require_disc_group_checks:
                selftest_env["ALPHA_FIXER_RUNTIME_DISC_GROUP_CHECKS"] = "1"
            if merged_dds_manifest:
                selftest_env["ALPHA_FIXER_RUNTIME_DDS_MANIFEST"] = merged_dds_manifest
            if require_dds_group_checks:
                selftest_env["ALPHA_FIXER_RUNTIME_DDS_GROUP_CHECKS"] = "1"
            if merged_format_manifest:
                selftest_env["ALPHA_FIXER_RUNTIME_FORMAT_MATRIX_MANIFEST"] = merged_format_manifest
            if require_format_group_checks:
                selftest_env["ALPHA_FIXER_RUNTIME_FORMAT_GROUP_CHECKS"] = "1"
            if args.allow_sample_downloads:
                selftest_env["ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS"] = "1"
            if args.sample_cache_dir:
                selftest_env["ALPHA_FIXER_RUNTIME_SAMPLE_CACHE_DIR"] = args.sample_cache_dir
            selftest_timeout = max(30, int(args.selftest_timeout))
            try:
                selftest_result = _run_and_echo(command, env=selftest_env, timeout=selftest_timeout)
            except subprocess.TimeoutExpired:
                raise SystemExit(
                    f"Packaged runtime self-test timed out after {selftest_timeout} seconds."
                ) from None
            if selftest_result.returncode not in (0, 1):
                raise SystemExit(f"Packaged runtime self-test failed with exit code {selftest_result.returncode}.")
            selftest_payload = _selftest_payload(selftest_result.stdout or "")
            selftest_runs.append(selftest_payload)
            if args.require_selftest_pass and not selftest_payload.get("passed"):
                raise SystemExit(f"Packaged runtime self-test run {run_index} reported passed=false")
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
            manifest_results = selftest_payload.get("manifest_results")
            if isinstance(manifest_results, dict):
                _print_manifest_result_summary(manifest_results)
            for check_name in required_checks:
                check = checks.get(check_name)
                if (
                    not isinstance(check, dict)
                    or check.get("ok") is not True
                    or check.get("skipped")
                    or str(check.get("detail", "")).strip().casefold().startswith("skipped:")
                ):
                    raise SystemExit(f"Packaged runtime self-test check failed or missing: {check_name}")
            errors = selftest_payload.get("errors") or []
            if errors:
                print("⚠️  Packaged runtime self-test reported issues:")
                for entry in errors:
                    print(f"   - {entry}")
        repeat_summary = _selftest_repeat_summary(selftest_runs)
        peaks = repeat_summary.get("peak_rss_mb_values") or []
        if repeated_runs > 1 and peaks:
            print(
                "Repeated self-test peak RSS: "
                + ", ".join(f"{float(value):.2f}" for value in peaks)
                + f" MiB (growth={repeat_summary.get('peak_rss_mb_growth', 0.0):.2f}, "
                + f"spread={repeat_summary.get('peak_rss_mb_spread', 0.0):.2f})"
            )
        if args.max_selftest_rss_growth_mb is not None:
            if len(peaks) < repeated_runs:
                raise SystemExit("Packaged runtime self-test RSS growth check needs peak_rss_mb from every repeated run.")
            growth = float(repeat_summary.get("peak_rss_mb_growth") or 0.0)
            if growth > float(args.max_selftest_rss_growth_mb):
                raise SystemExit(
                    f"Packaged runtime self-test RSS growth exceeded limit: {growth} MiB > {args.max_selftest_rss_growth_mb} MiB"
                )
        if args.max_selftest_rss_spread_mb is not None:
            if len(peaks) < repeated_runs:
                raise SystemExit("Packaged runtime self-test RSS spread check needs peak_rss_mb from every repeated run.")
            spread = float(repeat_summary.get("peak_rss_mb_spread") or 0.0)
            if spread > float(args.max_selftest_rss_spread_mb):
                raise SystemExit(
                    f"Packaged runtime self-test RSS spread exceeded limit: {spread} MiB > {args.max_selftest_rss_spread_mb} MiB"
                )
    if args.json_out:
        json_out = Path(args.json_out)
        json_out.parent.mkdir(parents=True, exist_ok=True)
        final_payload = dict(payload)
        final_payload["validation_bundle_kind"] = bundle_kind
        final_payload["validation_host_platform"] = sys.platform
        final_payload["validation_offline_path_isolation"] = args.offline
        final_payload["validation_launch_target"] = str(launch_target)
        final_payload["validation_launch_target_name"] = launch_target.name
        final_payload["manifest_inputs"] = manifest_inputs
        final_payload["smoke_repeat_summary"] = smoke_summary
        final_payload["smoke_runs"] = smoke_runs
        if selftest_payload is not None:
            final_payload["runtime_selftest"] = selftest_payload
            manifest_results = selftest_payload.get("manifest_results")
            if isinstance(manifest_results, dict):
                final_payload["runtime_selftest_manifest_review"] = _manifest_result_review(manifest_results)
                final_payload["runtime_selftest_interesting_sample_outcomes"] = _interesting_manifest_outcomes(manifest_results)
            final_payload["runtime_selftest_failed_checks"] = _failed_selftest_checks(selftest_payload)
        if selftest_runs:
            final_payload["runtime_selftest_runs"] = selftest_runs
            final_payload["runtime_selftest_repeat_summary"] = _selftest_repeat_summary(selftest_runs)
            final_payload["runtime_selftest_check_repeat_summary"] = _selftest_check_repeat_summary(selftest_runs)
        json_out.write_text(json.dumps(final_payload, indent=2, sort_keys=True), encoding="utf-8")
    print("✅  Packaged runtime capability audit verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
