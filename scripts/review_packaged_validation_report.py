#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def _load_report(path: str) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Validation report is not a JSON object: {path}")
    return payload


def _top_level_status(payload: dict[str, object]) -> str:
    status = str(payload.get("status") or "").strip()
    if status:
        return status
    return "completed"


def _host_platform(payload: dict[str, object]) -> str:
    for key in ("validation_host_platform",):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    preflight = payload.get("preflight")
    if isinstance(preflight, dict):
        value = str(preflight.get("host_platform") or "").strip()
        if value:
            return value
    return "unknown"


def _bundle_kind(payload: dict[str, object]) -> str:
    for key in ("validation_bundle_kind",):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    preflight = payload.get("preflight")
    if isinstance(preflight, dict):
        value = str(preflight.get("bundle_kind") or "").strip()
        if value:
            return value
    return "unspecified"


def _validation_profile(payload: dict[str, object]) -> str:
    preflight = payload.get("preflight")
    if isinstance(preflight, dict):
        value = str(preflight.get("validation_profile") or "").strip()
        if value:
            return value
    return "unspecified"


def _launch_target(payload: dict[str, object]) -> str:
    for key in ("validation_launch_target",):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    preflight = payload.get("preflight")
    if isinstance(preflight, dict):
        value = str(preflight.get("launch_target") or "").strip()
        if value:
            return value
    return ""


def _failed_checks(payload: dict[str, object]) -> list[dict[str, str]]:
    for key in ("runtime_selftest_failed_checks", "failed_checks"):
        values = payload.get(key)
        if isinstance(values, list):
            return [
                {
                    "name": str(item.get("name") or "").strip(),
                    "detail": str(item.get("detail") or "").strip(),
                }
                for item in values
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
    return []


def _unstable_checks(payload: dict[str, object]) -> list[str]:
    summary = payload.get("runtime_selftest_check_repeat_summary")
    if isinstance(summary, dict):
        values = summary.get("unstable_checks")
        if isinstance(values, list):
            return [str(value).strip() for value in values if str(value).strip()]
    return []


def _interesting_outcomes(payload: dict[str, object]) -> list[dict[str, str]]:
    for key in ("runtime_selftest_interesting_sample_outcomes", "interesting_sample_outcomes"):
        values = payload.get(key)
        if isinstance(values, list):
            return [
                {
                    "manifest": str(item.get("manifest") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "label": str(item.get("label") or "").strip(),
                    "detail": str(item.get("detail") or "").strip(),
                    "stage": str(item.get("stage") or "").strip(),
                }
                for item in values
                if isinstance(item, dict)
            ]
    return []


def _manifest_review(payload: dict[str, object]) -> dict[str, object]:
    review = payload.get("runtime_selftest_manifest_review")
    return review if isinstance(review, dict) else {}


def _preflight_coverage_gaps(payload: dict[str, object]) -> list[dict[str, object]]:
    gaps: list[dict[str, object]] = []
    preflight = payload.get("preflight")
    if not isinstance(preflight, dict):
        return gaps
    required = preflight.get("required_manifest_coverage")
    if not isinstance(required, dict):
        return gaps
    for manifest_name, axes in required.items():
        if not isinstance(axes, dict):
            continue
        for axis_name, axis_review in axes.items():
            if not isinstance(axis_review, dict):
                continue
            missing = axis_review.get("missing_labels")
            if not isinstance(missing, list):
                continue
            labels = [str(label).strip() for label in missing if str(label).strip()]
            if labels:
                gaps.append(
                    {
                        "manifest": str(manifest_name),
                        "axis": str(axis_name),
                        "missing_labels": labels,
                    }
                )
    return gaps


def _manifest_failures(review: dict[str, object]) -> list[str]:
    failures: list[str] = []
    for manifest_name, report in review.items():
        if manifest_name == "dds_policy_groups" or not isinstance(report, dict):
            continue
        if not bool(report.get("ok")):
            failures.append(str(manifest_name))
        group_review = report.get("group_review")
        if isinstance(group_review, dict) and int(group_review.get("failed") or 0) > 0:
            failures.append(f"{manifest_name}:groups")
    dds_policy_groups = review.get("dds_policy_groups")
    if isinstance(dds_policy_groups, dict):
        for axis_name, axis_review in dds_policy_groups.items():
            if isinstance(axis_review, dict) and int(axis_review.get("failed") or 0) > 0:
                failures.append(f"dds_policy_groups:{axis_name}")
    return failures


def _recommendations(payload: dict[str, object], summary: dict[str, object]) -> list[str]:
    recs: list[str] = []
    status = str(summary.get("status") or "")
    if status == "preflight_failed":
        recs.append("Populate the private corpus environment variables or generated manifests before rerunning packaged validation.")
    elif status == "verifier_failed":
        recs.append("Review the packaged verifier JSON and rerun after fixing the first failing runtime/self-test requirement.")

    coverage_gaps = summary.get("preflight_coverage_gaps") or []
    if coverage_gaps:
        recs.append("Expand the private manifest coverage so the missing required platform/group labels are represented in the real corpora.")

    unstable = summary.get("unstable_checks") or []
    if unstable:
        recs.append("Investigate unstable repeated self-test checks; rerun the same bundle with a soak profile to confirm memory or timing drift.")

    failed_checks = summary.get("failed_checks") or []
    names = {str(item.get("name") or "") for item in failed_checks if isinstance(item, dict)}
    if any(name.startswith("external_disc_video_manifest") for name in names):
        recs.append("Triage the real PSP/PS1/PS2 disc-image samples called out by the failing disc-video manifest checks.")
    if any(name.startswith("external_dds_manifest") for name in names):
        recs.append("Review the failing DDS real-sample groups and compare their decode/export policy expectations against the actual runtime output.")
    if any(name.startswith("external_format_matrix_manifest") for name in names):
        recs.append("Recheck the real conversion-matrix samples and the packaged output/runtime support for the failing target formats.")

    manifest_failures = summary.get("manifest_failures") or []
    if manifest_failures:
        recs.append("Use the interesting sample outcomes to rewrite confusing error wording around the failing manifest groups before the next packaged run.")

    smoke = summary.get("smoke_repeat_summary")
    if isinstance(smoke, dict) and float(smoke.get("elapsed_seconds_growth") or 0.0) > 0.0:
        recs.append("Compare repeated smoke-launch elapsed-time growth/spread across hosts to catch relaunch regressions early.")
    selftest_repeat = summary.get("runtime_selftest_repeat_summary")
    if isinstance(selftest_repeat, dict) and float(selftest_repeat.get("peak_rss_mb_growth") or 0.0) > 0.0:
        recs.append("Track peak RSS growth from repeated self-test runs to baseline longer packaged soak behavior.")

    if not recs:
        recs.append("No immediate issues were flagged; compare this report against other host/bundle reports for cross-platform differences.")
    return recs


def _review_report(path: str, payload: dict[str, object], *, top_samples: int) -> dict[str, object]:
    failed_checks = _failed_checks(payload)
    unstable_checks = _unstable_checks(payload)
    interesting = _interesting_outcomes(payload)
    manifest_review = _manifest_review(payload)
    summary: dict[str, object] = {
        "report_path": str(Path(path).resolve()),
        "report_name": Path(path).name,
        "status": _top_level_status(payload),
        "host_platform": _host_platform(payload),
        "bundle_kind": _bundle_kind(payload),
        "validation_profile": _validation_profile(payload),
        "launch_target": _launch_target(payload),
        "smoke_repeat_summary": payload.get("smoke_repeat_summary") or {},
        "runtime_selftest_repeat_summary": payload.get("runtime_selftest_repeat_summary") or {},
        "failed_checks": failed_checks,
        "failed_check_count": len(failed_checks),
        "unstable_checks": unstable_checks,
        "unstable_check_count": len(unstable_checks),
        "manifest_review": manifest_review,
        "manifest_failures": _manifest_failures(manifest_review),
        "interesting_sample_outcomes": interesting[: max(0, int(top_samples))] if top_samples > 0 else interesting,
        "interesting_sample_outcome_count": len(interesting),
        "preflight_coverage_gaps": _preflight_coverage_gaps(payload),
    }
    summary["recommendations"] = _recommendations(payload, summary)
    return summary


def _aggregate_reviews(reviews: list[dict[str, object]]) -> dict[str, object]:
    aggregate: dict[str, object] = {
        "report_count": len(reviews),
        "status_counts": {},
        "platform_counts": {},
        "bundle_kind_counts": {},
        "failed_report_count": 0,
        "reports_with_failed_checks": 0,
        "reports_with_unstable_checks": 0,
    }
    status_counts: dict[str, int] = {}
    platform_counts: dict[str, int] = {}
    bundle_counts: dict[str, int] = {}
    for review in reviews:
        status = str(review.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        platform = str(review.get("host_platform") or "unknown")
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        bundle = str(review.get("bundle_kind") or "unspecified")
        bundle_counts[bundle] = bundle_counts.get(bundle, 0) + 1
        if status != "completed":
            aggregate["failed_report_count"] = int(aggregate["failed_report_count"]) + 1
        if int(review.get("failed_check_count") or 0) > 0:
            aggregate["reports_with_failed_checks"] = int(aggregate["reports_with_failed_checks"]) + 1
        if int(review.get("unstable_check_count") or 0) > 0:
            aggregate["reports_with_unstable_checks"] = int(aggregate["reports_with_unstable_checks"]) + 1
    aggregate["status_counts"] = status_counts
    aggregate["platform_counts"] = platform_counts
    aggregate["bundle_kind_counts"] = bundle_counts
    return aggregate


def _render_report_text(review: dict[str, object]) -> list[str]:
    lines = [
        f"Report: {review['report_name']}",
        f"Status: {review['status']}",
        f"Host: {review['host_platform']}  •  Bundle: {review['bundle_kind']}  •  Profile: {review['validation_profile']}",
    ]
    launch_target = str(review.get("launch_target") or "").strip()
    if launch_target:
        lines.append(f"Launch target: {launch_target}")
    lines.append(
        "Checks: "
        + f"{review.get('failed_check_count', 0)} failed  •  "
        + f"{review.get('unstable_check_count', 0)} unstable  •  "
        + f"{review.get('interesting_sample_outcome_count', 0)} interesting sample outcomes"
    )
    coverage_gaps = review.get("preflight_coverage_gaps") or []
    if coverage_gaps:
        lines.append("Preflight coverage gaps:")
        for gap in coverage_gaps:
            lines.append(
                f"- {gap['manifest']} / {gap['axis']}: "
                + ", ".join(str(label) for label in gap.get("missing_labels") or [])
            )
    failed_checks = review.get("failed_checks") or []
    if failed_checks:
        lines.append("Failed checks:")
        for item in failed_checks[:8]:
            line = f"- {item.get('name')}"
            detail = str(item.get("detail") or "").strip()
            if detail:
                line += f": {detail}"
            lines.append(line)
    unstable_checks = review.get("unstable_checks") or []
    if unstable_checks:
        lines.append("Unstable repeated checks:")
        for name in unstable_checks[:8]:
            lines.append(f"- {name}")
    interesting = review.get("interesting_sample_outcomes") or []
    if interesting:
        lines.append("Interesting sample outcomes:")
        for item in interesting[:8]:
            label = str(item.get("label") or "sample")
            status = str(item.get("status") or "unknown")
            manifest = str(item.get("manifest") or "manifest")
            detail = str(item.get("detail") or "").strip()
            lines.append(f"- {manifest} [{status}] {label}" + (f": {detail}" if detail else ""))
    lines.append("Recommendations:")
    for item in review.get("recommendations") or []:
        lines.append(f"- {item}")
    return lines


def _render_text(reviews: list[dict[str, object]], aggregate: dict[str, object]) -> str:
    lines = [
        "# Packaged Validation Review",
        f"Reports: {aggregate.get('report_count', 0)}",
        "Status counts: "
        + ", ".join(f"{name}={count}" for name, count in sorted((aggregate.get("status_counts") or {}).items())),
        "Platforms: "
        + ", ".join(f"{name}={count}" for name, count in sorted((aggregate.get("platform_counts") or {}).items())),
        "Bundle kinds: "
        + ", ".join(f"{name}={count}" for name, count in sorted((aggregate.get("bundle_kind_counts") or {}).items())),
        "",
    ]
    for index, review in enumerate(reviews):
        if index:
            lines.append("")
        lines.extend(_render_report_text(review))
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review packaged validation JSON output and summarize the failures, unstable checks, and real-sample triage points.")
    parser.add_argument("reports", nargs="+", help="One or more JSON report paths from verify_packaged_app.py or run_private_packaged_validation.py")
    parser.add_argument("--top-samples", type=int, default=12, help="Maximum interesting sample outcomes to keep per report (0 = keep all).")
    parser.add_argument("--json-out", help="Optional path to write the normalized review payload as JSON.")
    parser.add_argument("--markdown-out", help="Optional path to write the rendered review summary as Markdown/text.")
    args = parser.parse_args(argv)

    reviews = [_review_report(path, _load_report(path), top_samples=max(0, int(args.top_samples))) for path in args.reports]
    aggregate = _aggregate_reviews(reviews)
    payload = {
        "reports": reviews,
        "aggregate": aggregate,
    }
    rendered = _render_text(reviews, aggregate)
    print(rendered, end="")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_out:
        out_path = Path(args.markdown_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
