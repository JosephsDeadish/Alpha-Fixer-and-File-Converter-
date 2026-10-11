"""Acceptance reporting and private settings, without packaged GUI teardown."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace

import pytest
from PyQt6 import sip

from src.core import gui_acceptance, settings_manager


@pytest.fixture
def directory():
    root = Path("build/gui-acceptance-tests")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as path:
        yield Path(path)
    root.rmdir()


@pytest.mark.parametrize("assertions_passed", [False, True])
def test_report_never_certifies_process_exit(directory, assertions_passed):
    report = {"gui_assertions_passed": assertions_passed}
    gui_acceptance._write_acceptance_report(directory, report, time.monotonic())
    saved = json.loads((directory / "gui-acceptance.json").read_text())
    assert "passed" not in saved
    assert saved["gui_assertions_passed"] is assertions_passed
    assert saved["overall_acceptance"] == (
        "pending_external_clean_exit" if assertions_passed else "failed_gui_assertions"
    )


def test_failed_gui_assertion_reports_before_close(directory):
    exits = []
    settings = SimpleNamespace(get_theme=lambda: {}, set_theme=lambda _: None)
    window = SimpleNamespace(
        devicePixelRatioF=lambda: 2, winId=lambda: 1, isVisible=lambda: False,
        _settings=settings, _apply_theme=lambda: None,
    )

    def close():
        saved = json.loads((directory / "gui-acceptance.json").read_text())
        assert saved["gui_assertions_passed"] is False
        assert saved["overall_acceptance"] == "failed_gui_assertions"
        assert "AssertionError" in saved["error"]
        assert "passed" not in saved

    window.close = close
    app = SimpleNamespace(platformName=lambda: "test", exit=exits.append)
    gui_acceptance.run_gui_acceptance(app, window, str(directory))
    assert exits == [1]


def test_acceptance_settings_isolated_before_construction_and_through_close(
        directory, monkeypatch):
    monkeypatch.delenv("ALPHA_FIXER_GUI_ACCEPTANCE_OUT", raising=False)
    monkeypatch.setattr(settings_manager, "writable_app_directory", lambda _: directory)
    monkeypatch.setattr(gui_acceptance, "_settings_directory", None)
    normal_path = Path(settings_manager._settings_ini_path())
    normal = settings_manager.SettingsManager()
    normal.set("window_geometry", "user geometry")
    normal.set("theme", "user theme")
    normal.sync()
    original = normal_path.read_bytes()
    sip.delete(normal._qs)

    output = directory / "report"
    monkeypatch.setenv("ALPHA_FIXER_GUI_ACCEPTANCE_OUT", str(output))
    acceptance = settings_manager.SettingsManager()
    private_path = Path(acceptance._qs.fileName())
    try:
        assert private_path.is_relative_to(output.resolve())
        assert private_path != normal_path.resolve()
        assert acceptance._qs.allKeys() == []
        assert Path(settings_manager._settings_ini_path()) == private_path
        acceptance.set("theme", "acceptance theme")
        acceptance.sync()
        # Removing the environment does not redirect an existing QSettings:
        # late close/destructor writes, or a crash before cleanup, remain private.
        monkeypatch.delenv("ALPHA_FIXER_GUI_ACCEPTANCE_OUT")
        acceptance.set("window_geometry", "acceptance close geometry")
        acceptance.sync()
        assert normal_path.read_bytes() == original
        assert Path(settings_manager._settings_ini_path()) == normal_path
        reopened = settings_manager.SettingsManager()
        try:
            assert reopened.get("theme") == "user theme"
            assert reopened.get("window_geometry") == "user geometry"
        finally:
            sip.delete(reopened._qs)
    finally:
        sip.delete(acceptance._qs)
        gui_acceptance._settings_directory.cleanup()
        gui_acceptance._settings_directory = None
