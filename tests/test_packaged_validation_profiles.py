import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestPackagedValidationProfiles(unittest.TestCase):
    def _load_runner_script(self):
        module_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "scripts",
            "run_private_packaged_validation.py",
        )
        spec = importlib.util.spec_from_file_location("run_private_packaged_validation", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)
        return script

    def test_effective_validation_settings_raise_minimums_for_onefile_profile(self):
        script = self._load_runner_script()

        class Args:
            validation_profile = "onefile-deep"
            bundle_kind = "onefile"
            smoke_seconds = 2.0
            repeat = 2
            smoke_launch_delay_seconds = 0.25
            timeout = 45
            selftest_iterations = 6
            selftest_sample_limit = 8
            selftest_stress_loops = 2
            repeat_selftest_runs = 2
            max_selftest_rss_growth_mb = 256.0
            max_selftest_rss_spread_mb = 256.0
            max_smoke_elapsed_seconds = 30.0
            max_smoke_elapsed_growth_seconds = 10.0
            max_smoke_elapsed_spread_seconds = 10.0
            use_public_sample_manifests = True
            allow_sample_downloads = False

        settings = script._effective_validation_settings(Args())
        self.assertEqual(settings["validation_profile"], "onefile-deep")
        self.assertEqual(settings["bundle_kind"], "onefile")
        self.assertEqual(settings["repeat"], 5)
        self.assertEqual(settings["selftest_iterations"], 8)
        self.assertEqual(settings["selftest_stress_loops"], 5)
        self.assertEqual(settings["repeat_selftest_runs"], 4)
        self.assertEqual(settings["max_selftest_rss_growth_mb"], 320.0)
        self.assertEqual(settings["max_smoke_elapsed_seconds"], 45.0)
        self.assertTrue(settings["use_public_sample_manifests"])

    def test_effective_validation_settings_raise_minimums_for_soak_profile(self):
        script = self._load_runner_script()

        class Args:
            validation_profile = "soak"
            bundle_kind = "folder"
            smoke_seconds = 2.0
            repeat = 2
            smoke_launch_delay_seconds = 0.25
            timeout = 45
            selftest_iterations = 6
            selftest_sample_limit = 8
            selftest_stress_loops = 2
            repeat_selftest_runs = 2
            max_selftest_rss_growth_mb = 256.0
            max_selftest_rss_spread_mb = 256.0
            max_smoke_elapsed_seconds = 30.0
            max_smoke_elapsed_growth_seconds = 10.0
            max_smoke_elapsed_spread_seconds = 10.0
            use_public_sample_manifests = False
            allow_sample_downloads = False

        settings = script._effective_validation_settings(Args())
        self.assertEqual(settings["validation_profile"], "soak")
        self.assertEqual(settings["bundle_kind"], "folder")
        self.assertEqual(settings["repeat"], 8)
        self.assertEqual(settings["selftest_iterations"], 10)
        self.assertEqual(settings["selftest_stress_loops"], 8)
        self.assertEqual(settings["repeat_selftest_runs"], 6)
        self.assertEqual(settings["max_selftest_rss_growth_mb"], 384.0)
        self.assertEqual(settings["max_smoke_elapsed_seconds"], 60.0)

    def test_runner_forwards_profile_and_public_manifest_requirements(self):
        script = self._load_runner_script()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_root = os.path.join(tmpdir, "video")
            dds_root = os.path.join(tmpdir, "dds")
            out_dir = os.path.join(tmpdir, "out")
            launch_target = os.path.join(tmpdir, "AlphaFixerConverter")
            os.makedirs(os.path.join(video_root, "ps2"), exist_ok=True)
            os.makedirs(dds_root, exist_ok=True)
            Path(os.path.join(video_root, "ps2", "sample.iso")).write_bytes(b"iso")
            Path(os.path.join(dds_root, "texture_array.dds")).write_bytes(b"dds")
            Path(launch_target).write_text("stub", encoding="utf-8")
            os.chmod(launch_target, 0o755)
            captured = {}

            def _fake_verify(argv):
                captured["argv"] = list(argv)
                json_out = Path(argv[argv.index("--json-out") + 1])
                json_out.parent.mkdir(parents=True, exist_ok=True)
                json_out.write_text(json.dumps({"runtime_selftest": {"checks": {}, "manifest_results": {}}}), encoding="utf-8")
                return 0

            with mock.patch.dict(
                os.environ,
                {
                    "ALPHA_FIXER_REAL_VIDEO_CORPUS": video_root,
                    "ALPHA_FIXER_REAL_DDS_CORPUS": dds_root,
                },
                clear=False,
            ), mock.patch.object(script.verify_packaged_app_script, "main", side_effect=_fake_verify):
                rc = script.main(
                    [
                        launch_target,
                        "--output-dir",
                        out_dir,
                        "--bundle-kind",
                        "onefile",
                        "--validation-profile",
                        "onefile-deep",
                        "--use-public-sample-manifests",
                    ]
                )
            self.assertEqual(rc, 0)
            argv = captured["argv"]
            self.assertIn("--bundle-kind", argv)
            self.assertIn("onefile", argv)
            self.assertIn("--require-odd-probe-ready", argv)
            self.assertIn("--require-dds-selftest-checks", argv)
            self.assertIn("--require-no-packaged-asset-gaps", argv)
            self.assertIn("--use-public-sample-manifests", argv)
            self.assertIn("--require-public-manifest-checks", argv)
            self.assertIn("--require-public-manifest-group-checks", argv)
            self.assertIn("--require-disc-manifest-platform", argv)
            self.assertIn("PSP", argv)
            self.assertIn("PS1", argv)
            self.assertIn("PS2", argv)
            self.assertIn("--require-dds-manifest-group", argv)
            self.assertIn("BC6H", argv)
            self.assertIn("BC7", argv)
            self.assertIn("mipmap", argv)
            self.assertIn("cubemap", argv)
            self.assertIn("array", argv)
            self.assertIn("volume", argv)
            self.assertEqual(argv[argv.index("--repeat") + 1], "5")
            self.assertEqual(argv[argv.index("--repeat-selftest-runs") + 1], "4")
            summary_path = os.path.join(out_dir, "private-runtime-validation-summary.json")
            summary_payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["preflight"]["validation_profile"], "onefile-deep")
            self.assertEqual(summary_payload["preflight"]["bundle_kind"], "onefile")
            self.assertEqual(summary_payload["preflight"]["requested_validation_settings"]["repeat"], 5)
            self.assertEqual(
                summary_payload["preflight"]["required_manifest_coverage"]["disc_video"]["platform"]["missing_labels"],
                ["PSP", "PS1"],
            )
            self.assertEqual(
                summary_payload["preflight"]["required_manifest_coverage"]["dds"]["group"]["missing_labels"],
                ["BC6H", "BC7", "mipmap", "cubemap", "volume"],
            )


if __name__ == "__main__":
    unittest.main()
