"""Opt-in checks on the normally launched application window.

The in-process report cannot certify teardown: overall acceptance requires an
external supervisor to observe exit code zero AND gui_assertions_passed=true.
A signal, timeout, nonzero exit, or missing report is an acceptance failure.
"""

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback


_settings_directory = None


def acceptance_settings_ini_path(output_directory: str) -> str:
    """Allocate private settings before QSettings can read or write user data."""
    global _settings_directory
    if _settings_directory is None:
        output = Path(output_directory).resolve()
        output.mkdir(parents=True, exist_ok=True)
        _settings_directory = tempfile.TemporaryDirectory(
            prefix="acceptance-settings-", dir=output,
        )
    return str(Path(_settings_directory.name) / "AlphaFixerConverter.ini")


def _write_acceptance_report(output, report, started):
    report["overall_acceptance"] = (
        "pending_external_clean_exit" if report["gui_assertions_passed"]
        else "failed_gui_assertions"
    )
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (output / "gui-acceptance.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8",
    )


def run_gui_acceptance(app, window, output_directory: str) -> None:
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from src.ui.theme_engine import DEFAULT_THEME, LIGHT_THEME

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "platform": app.platformName(),
        "device_pixel_ratio": window.devicePixelRatioF(),
        "window_id": int(window.winId()),
        "cases": [],
        "gui_assertions_passed": False,
        "overall_acceptance": "pending_external_clean_exit",
    }
    started = time.monotonic()
    original_theme = window._settings.get_theme()

    def settle():
        QTest.qWait(100)

    def settings_snapshot():
        settings = window._settings._qs
        return {key: settings.value(key) for key in settings.allKeys()}

    def focus_visible(tab, control):
        control.setFocus()
        settle()
        assert app.focusWidget() is control, control.accessibleName()
        viewport = tab._content_scroll.viewport()
        assert viewport.rect().contains(control.mapTo(viewport, QPoint()))
        assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight()))
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()

    def snapshot(tab):
        if tab is window._alpha_tab:
            return (tab._build_manual_params(), tab._preview_request_id)
        return (tab._fmt_combo.currentData(), tab._quality_spin.value(),
                tab._dds_variant_combo.currentData(), tab._keep_metadata_check.isChecked(),
                tab._resize_check.isChecked(), tab._width_spin.value(),
                tab._height_spin.value(), tab._lock_aspect_check.isChecked(),
                tab._preview_request_id)

    try:
        assert window.isVisible() and window.windowHandle().isExposed()
        window.activateWindow()
        assert QTest.qWaitForWindowActive(window)
        for theme_name, theme in (("dark", DEFAULT_THEME), ("light", LIGHT_THEME)):
            window._settings.set_theme(theme)
            window._apply_theme()
            for size_name, size in (("compact", (900, 700)), ("normal", (1200, 800))):
                window.resize(*size)
                settle()
                assert (window.width(), window.height()) == size
                for tool_name, tab in (("alpha", window._alpha_tab),
                                       ("converter", window._converter_tab)):
                    window._tabs.setCurrentWidget(tab)
                    tab._advanced_toggle.setChecked(False)
                    settle()
                    before, preferences = snapshot(tab), settings_snapshot()
                    case = {"theme": theme_name, "size": list(size), "tool": tool_name,
                            "passed": False, "screenshots": []}
                    report["cases"].append(case)
                    focus_visible(tab, tab._advanced_toggle)
                    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                        for expanded in (True, False):
                            QTest.keyClick(tab._advanced_toggle, key)
                            settle()
                            assert tab._advanced_toggle.isChecked() is expanded
                            assert app.focusWidget() is tab._advanced_toggle
                            assert all(control.isHidden() is not expanded
                                       for control in tab._advanced_controls)
                            assert snapshot(tab) == before
                            assert settings_snapshot() == preferences
                            assert not tab._preview_debounce.isActive()
                    for expanded in (False, True):
                        if expanded:
                            QTest.keyClick(tab._advanced_toggle, Qt.Key.Key_Space)
                            settle()
                            QTest.keyClick(tab._advanced_toggle, Qt.Key.Key_Tab)
                            first = (tab._binary_cut_check if tool_name == "alpha"
                                     else tab._quality_spin if tab._quality_spin.isEnabled()
                                     else tab._dds_variant_combo if tab._dds_variant_combo.isVisible()
                                     else tab._keep_metadata_check)
                            assert app.focusWidget() is first
                            focus_visible(tab, first)
                            QTest.keyClick(first, Qt.Key.Key_Backtab)
                            assert app.focusWidget() is tab._advanced_toggle
                        for scroll in (tab._content_scroll, tab._left_scroll, tab._right_scroll):
                            assert scroll.horizontalScrollBar().maximum() == 0
                        for label in (tab._advanced_hint, tab._advanced_status):
                            assert label.height() >= label.heightForWidth(label.width()), label.text()
                        focus_visible(tab, tab._advanced_toggle)
                        image_name = f"{theme_name}-{size_name}-{tool_name}-{'advanced' if expanded else 'basic'}.png"
                        assert window.grab().save(str(output / image_name))
                        case["screenshots"].append(image_name)
                    QTest.keyClick(tab._advanced_toggle, Qt.Key.Key_Space)
                    settle()
                    focus_visible(tab, tab._btn_add_files)
                    focus_visible(tab, tab._fmt_combo if tool_name == "converter"
                                  else tab._clamp_min_spin)
                    focus_visible(tab, tab._advanced_toggle)
                    assert snapshot(tab) == before
                    assert settings_snapshot() == preferences
                    case["summary"] = tab._advanced_status.text()
                    case["passed"] = True
        report["gui_assertions_passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        try:
            window._settings.set_theme(original_theme)
            window._apply_theme()
        except Exception:
            report["gui_assertions_passed"] = False
            report["restoration_error"] = traceback.format_exc()
        _write_acceptance_report(output, report, started)
        window.close()
        app.exit(0 if report["gui_assertions_passed"] else 1)
