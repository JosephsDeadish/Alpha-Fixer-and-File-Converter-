import importlib.util
import os
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.core import file_converter as converter


class TestBundledSvg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not converter._has_qt_svg():
            raise unittest.SkipTest("Qt SVG stack unavailable")

    def _render(self, svg):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.svg"
            path.write_text(svg, encoding="utf-8")
            with patch.object(converter, "_has_cairosvg", return_value=False):
                with patch.object(converter, "_has_svglib", return_value=False):
                    return converter._load_svg(str(path))

    def test_rasterizes_vector_and_preserves_transparency_without_external_renderer(self):
        with self._render(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
            '<rect width="6" height="8" fill="#ff0000" fill-opacity="0.5"/></svg>'
        ) as image:
            self.assertEqual(image.size, (12, 8))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((2, 2))[:3], (255, 0, 0))
            self.assertAlmostEqual(image.getpixel((2, 2))[3], 128, delta=1)
            self.assertEqual(image.getpixel((10, 2))[3], 0)

    def test_viewbox_only_svg_uses_intrinsic_size(self):
        with self._render(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 20">'
            '<rect width="30" height="20" fill="blue"/></svg>'
        ) as image:
            self.assertEqual(image.size, (30, 20))
            self.assertEqual(image.getpixel((15, 10)), (0, 0, 255, 255))

    def test_invalid_svg_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, "Invalid or unsupported SVG"):
            self._render("<not-an-svg/>")

    def test_oversized_svg_rejected_before_pixel_allocation(self):
        with patch.object(Image, "MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(Image.DecompressionBombError):
                self._render(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="101" height="101"/>'
                )

    def test_embedded_raster_svg_roundtrips_without_tracing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "output.svg")
            with Image.new("RGBA", (8, 6), (30, 90, 150, 255)) as original:
                with patch.object(converter, "_has_vtracer", return_value=False):
                    converter._save_svg(original, path)
                with converter._load_svg(path) as loaded:
                    self.assertEqual(loaded.size, original.size)
                    self.assertEqual(loaded.getpixel((4, 3)), original.getpixel((4, 3)))

    def test_bundled_tracer_exports_real_paths(self):
        if not converter._has_vtracer():
            self.skipTest("Declared vtracer dependency unavailable")
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "traced.svg")
            with Image.new("RGBA", (12, 12), (200, 30, 80, 255)) as source:
                converter._save_svg(source, path)
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("<path", content)
            self.assertNotIn("data:image/png;base64,", content)


class TestOfflineVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "scripts" / "verify_packaged_app.py"
        spec = importlib.util.spec_from_file_location("offline_verifier", path)
        cls.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.verifier)

    def test_external_runtime_configuration_removed(self):
        external = {
            "PATH": "/external/tools", "PYTHONPATH": "/external/python",
            "MAGICK_HOME": "/external/magick", "IMAGEIO_FFMPEG_EXE": "/external/ffmpeg",
            "ALPHA_FIXER_FFPROBE_EXE": "/external/ffprobe",
            "LD_LIBRARY_PATH": "/external/libs",
            "ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS": "1",
            "ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS": "true",
        }
        with patch.dict(os.environ, external):
            env = self.verifier._verification_environment(True)
        for name in external.keys() - {
            "PATH", "ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS", "ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS",
        }:
            self.assertNotIn(name, env)
        self.assertNotIn("/external", env["PATH"])
        self.assertEqual(env["ALPHA_FIXER_RUNTIME_ALLOW_SAMPLE_DOWNLOADS"], "0")
        self.assertEqual(env["ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS"], "0")

    def test_normal_verification_retains_source_configuration(self):
        with patch.dict(os.environ, {"IMAGEIO_FFMPEG_EXE": "/test/ffmpeg"}):
            env = self.verifier._verification_environment(False)
        self.assertEqual(env["IMAGEIO_FFMPEG_EXE"], "/test/ffmpeg")

    def test_offline_mode_rejects_explicit_downloads_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "app"
            target.touch()
            with patch.object(self.verifier, "_run_and_echo") as run:
                with self.assertRaisesRegex(SystemExit, "cannot be combined"):
                    self.verifier.main([str(target), "--offline", "--allow-sample-downloads"])
                run.assert_not_called()

    def test_windowed_executable_reports_through_file_channel(self):
        payload = {"frozen": True, "packaged_bundle_ready": True}

        def run(command, **kwargs):
            output = Path(kwargs["env"]["ALPHA_FIXER_VALIDATION_JSON_OUT"])
            output.write_text(json.dumps({
                "prefix": "ALPHA_FIXER_RUNTIME_CAPABILITIES", "payload": payload,
            }), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="")

        with patch.object(self.verifier.subprocess, "run", side_effect=run):
            with patch("sys.stdout"):
                result = self.verifier._run_and_echo(["app.exe"], env={}, timeout=5)
        self.assertEqual(self.verifier._capability_payload(result.stdout), payload)


class TestUnattendedValidation(unittest.TestCase):
    def test_validation_payload_can_be_written_without_console(self):
        import main

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with patch.dict(os.environ, {"ALPHA_FIXER_VALIDATION_JSON_OUT": str(output)}):
                with patch("sys.stdout", None):
                    main._emit_validation_payload("ALPHA_FIXER_RUNTIME_SELFTEST", {"passed": True})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {
                "prefix": "ALPHA_FIXER_RUNTIME_SELFTEST", "payload": {"passed": True},
            })

    def test_validation_errors_do_not_open_modal_crash_dialog(self):
        import main
        from PyQt6.QtWidgets import QDialog

        for env in (
            {"ALPHA_FIXER_RUNTIME_SELFTEST": "1"},
            {"ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP": "1"},
            {"ALPHA_FIXER_SMOKE_TEST": "0.5"},
        ):
            with self.subTest(mode=env):
                with patch.dict(os.environ, env), patch.object(QDialog, "exec") as execute:
                    with patch("sys.stderr"):
                        main._show_crash_dialog("Validation failure", "Test", "traceback", "")
                    execute.assert_not_called()

    def test_uncaught_validation_error_exits_event_loop_with_failure(self):
        import main
        from unittest.mock import MagicMock

        app = MagicMock()
        with patch.object(main, "_unattended_validation_errors", []):
            with patch.dict(os.environ, {"ALPHA_FIXER_RUNTIME_SELFTEST": "1"}):
                with patch("PyQt6.QtCore.QCoreApplication.instance", return_value=app):
                    with patch("sys.stderr"), patch.object(main, "_show_crash_dialog") as dialog:
                        main._excepthook(ValueError, ValueError("Validation failed"), None)
                    app.exit.assert_called_once_with(1)
                    dialog.assert_not_called()
                    self.assertIn("Validation failed", main._unattended_validation_errors[0])

    def test_capability_callback_failure_cannot_report_ready(self):
        import main

        with patch.object(main, "_unattended_validation_errors", ["callback failed"]):
            with patch.object(main, "_runtime_capability_summary", return_value={"packaged_bundle_ready": True}):
                with patch.object(main, "_emit_validation_payload") as emit:
                    self.assertEqual(main._emit_runtime_capability_dump(), 1)
                self.assertFalse(emit.call_args.args[1]["packaged_bundle_ready"])
                self.assertEqual(emit.call_args.args[1]["runtime_audit_errors"], ["callback failed"])


class TestSettingsScreenFitting(unittest.TestCase):
    def test_settings_fit_decorated_window_on_small_secondary_screen(self):
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QApplication, QScrollArea
        from unittest.mock import MagicMock
        from src.core.settings_manager import SettingsManager
        from src.ui.settings_dialog import SettingsDialog

        app = QApplication.instance() or QApplication(["test"])
        original_font = app.font()
        screen = MagicMock()
        available = QRect(-640, 30, 640, 480)
        screen.availableGeometry.return_value = available
        with tempfile.TemporaryDirectory() as directory:
            with patch("src.core.settings_manager._settings_ini_path",
                       return_value=str(Path(directory) / "settings.ini")):
                manager = SettingsManager()
                try:
                    for point_size in (8, 24):
                        with self.subTest(font=point_size):
                            font = QFont(original_font)
                            font.setPointSize(point_size)
                            app.setFont(font)
                            dialog = SettingsDialog(manager)
                            try:
                                with patch.object(dialog, "screen", return_value=screen):
                                    dialog.show()
                                    app.processEvents()
                                    app.processEvents()
                                    self.assertTrue(available.contains(dialog.frameGeometry()))
                                    self.assertTrue(dialog.findChildren(QScrollArea))
                            finally:
                                dialog.close()
                                dialog.deleteLater()
                                app.processEvents()
                finally:
                    app.setFont(original_font)


class TestReadOnlyInstallation(unittest.TestCase):
    def test_writable_portable_directory_is_preserved(self):
        from src.core.app_paths import writable_app_directory

        with tempfile.TemporaryDirectory() as directory:
            preferred = Path(directory)
            self.assertEqual(writable_app_directory(preferred), preferred)

    def test_settings_and_logs_fall_back_to_user_storage(self):
        import main
        from src.core.settings_manager import SettingsManager, _settings_ini_path
        from src.core import app_paths

        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "installation"
            install.mkdir()
            user_data = Path(directory) / "user-data"
            with patch.object(main.sys, "frozen", True, create=True):
                with patch.object(main.sys, "executable", str(install / "app.exe")):
                    with patch.object(app_paths, "user_data_directory", return_value=user_data):
                        with patch.object(app_paths.tempfile, "TemporaryFile", side_effect=PermissionError):
                            self.assertEqual(Path(_settings_ini_path()).parent, user_data)
                            self.assertEqual(main._log_dir(), user_data / "logs")
                            manager = SettingsManager()
                            manager.set("font_size", 18)
                            manager.sync()
            self.assertTrue((user_data / "AlphaFixerConverter.ini").is_file())
            self.assertEqual(manager.get("font_size"), 18)


if __name__ == "__main__":
    unittest.main()
