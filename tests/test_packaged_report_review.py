import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


class TestPackagedReportReview(unittest.TestCase):
    def _load_script(self):
        module_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "scripts",
            "review_packaged_validation_report.py",
        )
        spec = importlib.util.spec_from_file_location("review_packaged_validation_report", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)
        return script

    def test_review_script_summarizes_runner_report(self):
        script = self._load_script()
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "runner-summary.json"
            json_out = Path(tmpdir) / "review.json"
            markdown_out = Path(tmpdir) / "review.md"
            report_path.write_text(
                json.dumps(
                    {
                        "status": "verifier_failed",
                        "preflight": {
                            "host_platform": "win32",
                            "validation_profile": "onefile-soak",
                            "bundle_kind": "onefile",
                            "launch_target": "C:/dist/AlphaFixerConverter.exe",
                            "required_manifest_coverage": {
                                "disc_video": {
                                    "platform": {"missing_labels": ["PS1"]},
                                },
                                "dds": {
                                    "group": {"missing_labels": ["cubemap", "volume"]},
                                },
                            },
                        },
                        "runtime_selftest_check_repeat_summary": {
                            "unstable_checks": ["stress_peak_rss_growth"],
                        },
                        "runtime_selftest_failed_checks": [
                            {"name": "external_dds_manifest_array", "detail": "array failed"},
                        ],
                        "runtime_selftest_manifest_review": {
                            "dds": {
                                "ok": False,
                                "detail": "array failed",
                                "sample_count": 2,
                                "sample_status_counts": {"failed": 1},
                                "group_review": {"failed": 1, "ok": 1, "total": 2},
                            }
                        },
                        "runtime_selftest_interesting_sample_outcomes": [
                            {
                                "manifest": "dds",
                                "status": "failed",
                                "label": "texture_array.dds",
                                "detail": "unsupported array",
                                "stage": "decode",
                            }
                        ],
                        "smoke_repeat_summary": {"elapsed_seconds_growth": 1.2},
                        "runtime_selftest_repeat_summary": {"peak_rss_mb_growth": 32.5},
                    }
                ),
                encoding="utf-8",
            )
            rc = script.main(
                [
                    str(report_path),
                    "--json-out",
                    str(json_out),
                    "--markdown-out",
                    str(markdown_out),
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertEqual(payload["aggregate"]["report_count"], 1)
            review = payload["reports"][0]
            self.assertEqual(review["host_platform"], "win32")
            self.assertEqual(review["bundle_kind"], "onefile")
            self.assertEqual(review["validation_profile"], "onefile-soak")
            self.assertEqual(review["failed_check_count"], 1)
            self.assertEqual(review["unstable_check_count"], 1)
            self.assertEqual(review["interesting_sample_outcome_count"], 1)
            self.assertEqual(review["preflight_coverage_gaps"][0]["missing_labels"], ["PS1"])
            self.assertIn("Expand the private manifest coverage", "\n".join(review["recommendations"]))
            rendered = markdown_out.read_text(encoding="utf-8")
            self.assertIn("texture_array.dds", rendered)
            self.assertIn("stress_peak_rss_growth", rendered)

    def test_review_script_aggregates_multiple_reports(self):
        script = self._load_script()
        with tempfile.TemporaryDirectory() as tmpdir:
            report_a = Path(tmpdir) / "linux.json"
            report_b = Path(tmpdir) / "mac.json"
            out_json = Path(tmpdir) / "aggregate.json"
            report_a.write_text(
                json.dumps(
                    {
                        "validation_host_platform": "linux",
                        "validation_bundle_kind": "folder",
                        "validation_launch_target": "/tmp/dist/AlphaFixerConverter",
                        "runtime_selftest_failed_checks": [],
                        "runtime_selftest_check_repeat_summary": {"unstable_checks": []},
                        "runtime_selftest_interesting_sample_outcomes": [],
                    }
                ),
                encoding="utf-8",
            )
            report_b.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "preflight": {
                            "host_platform": "darwin",
                            "bundle_kind": "onefile",
                            "validation_profile": "soak",
                        },
                        "failed_checks": [{"name": "external_disc_video_manifest_ps2", "detail": "ps2 failed"}],
                        "interesting_sample_outcomes": [{"manifest": "disc_video", "status": "failed", "label": "movie.iso", "detail": "triage"}],
                    }
                ),
                encoding="utf-8",
            )
            rc = script.main([str(report_a), str(report_b), "--json-out", str(out_json)])
            self.assertEqual(rc, 0)
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["aggregate"]["report_count"], 2)
            self.assertEqual(payload["aggregate"]["platform_counts"]["linux"], 1)
            self.assertEqual(payload["aggregate"]["platform_counts"]["darwin"], 1)
            self.assertEqual(payload["aggregate"]["bundle_kind_counts"]["folder"], 1)
            self.assertEqual(payload["aggregate"]["bundle_kind_counts"]["onefile"], 1)
            self.assertEqual(payload["aggregate"]["reports_with_failed_checks"], 1)
            self.assertFalse(payload["coverage_matrix"]["complete"])
            self.assertIn(
                {"platform": "win32", "bundle_kind": "folder"},
                payload["coverage_matrix"]["missing"],
            )
            self.assertIn(
                {"platform": "win32", "bundle_kind": "onefile"},
                payload["coverage_matrix"]["missing"],
            )

    def test_review_script_can_discover_reports_from_directory(self):
        script = self._load_script()
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            nested_dir = reports_dir / "nested"
            nested_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "linux-folder.json").write_text(
                json.dumps(
                    {
                        "validation_host_platform": "linux",
                        "validation_bundle_kind": "folder",
                    }
                ),
                encoding="utf-8",
            )
            (nested_dir / "win-onefile.json").write_text(
                json.dumps(
                    {
                        "validation_host_platform": "win32",
                        "validation_bundle_kind": "onefile",
                    }
                ),
                encoding="utf-8",
            )
            out_json = Path(tmpdir) / "review.json"
            rc = script.main(
                [
                    str(reports_dir),
                    "--json-out",
                    str(out_json),
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["aggregate"]["report_count"], 2)
            self.assertEqual(payload["aggregate"]["platform_counts"]["linux"], 1)
            self.assertEqual(payload["aggregate"]["platform_counts"]["win32"], 1)
            self.assertEqual(payload["aggregate"]["platform_bundle_counts"]["linux:folder"], 1)
            self.assertEqual(payload["aggregate"]["platform_bundle_counts"]["win32:onefile"], 1)
            self.assertIn(
                {"platform": "darwin", "bundle_kind": "folder"},
                payload["coverage_matrix"]["missing"],
            )


if __name__ == "__main__":
    unittest.main()
