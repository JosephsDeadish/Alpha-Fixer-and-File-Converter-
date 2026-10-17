import json
import time
from unittest.mock import patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
from PIL import Image

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for_project(dialog):
    deadline = time.monotonic() + 15
    while dialog._project._job is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert dialog._project._job is None
    QApplication.processEvents()


@pytest.mark.parametrize("iteration", range(6))
def test_real_builders_and_queued_status_survive_window_teardown(app, tmp_path, iteration):
    original_stylesheet = app.styleSheet()
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / f"settings-{iteration}.ini")):
        settings = SettingsManager()
        window = MainWindow(settings)
    try:
        window.show()
        window._open_or_focus_gif_builder()
        window._open_or_focus_video_builder()
        app.processEvents()
        for _ in range(3):
            for dialog in (window._gif_builder_dlg, window._video_tool_dlg):
                dialog.hide()
                dialog.show()
                dialog.activateWindow()
            window._apply_theme()
            app.processEvents()
        window._gif_builder_dlg.hide()
        window._video_tool_dlg.hide()
        window.close()
        sip.delete(window)
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        unrelated = QWidget()
        unrelated.show()
        app.processEvents()
        sip.delete(unrelated)
    finally:
        if not sip.isdeleted(window):
            window.close()
            sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)
        app.processEvents()
        app.setStyleSheet(original_stylesheet)


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_main_window_project_save_reopen_and_recovery_ui_workflow(app, tmp_path, kind):
    from src.ui import editor_project

    original_font, original_stylesheet = app.font(), app.styleSheet()
    windows = []
    image_path = tmp_path / "acceptance.png"
    image = Image.new("RGBA", (12, 9), (40, 110, 210, 180))
    image.save(image_path)
    image.close()

    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")), \
            patch.object(editor_project, "user_data_directory", return_value=tmp_path / "user-data"):
        settings = SettingsManager()
        try:
            first = MainWindow(settings)
            windows.append(first)
            if kind == "gif":
                first._open_or_focus_gif_builder()
                builder = first._gif_builder_dlg
                add_dialog = "src.ui.gif_builder.QFileDialog.getOpenFileNames"
                add_button = builder._btn_add
                control_name = "_delay_slider"
            else:
                first._open_or_focus_video_builder()
                builder = first._video_tool_dlg
                add_dialog = "src.ui.video_tool.QFileDialog.getOpenFileNames"
                add_button = builder._btn_add_img
                control_name = "_brightness_slider"

            with patch(add_dialog, return_value=([str(image_path)], "")):
                add_button.click()
            QApplication.processEvents()
            assert len(builder._project.entries) == 1

            project = builder._project
            control = getattr(builder, control_name)
            initial_value = control.value()
            project_path = tmp_path / f"saved-{kind}.json"
            with patch(
                "src.ui.editor_project.QFileDialog.getSaveFileName",
                return_value=(str(project_path), ""),
            ):
                project.save_button.click()
            wait_for_project(builder)
            assert project_path.is_file()
            assert not project.dirty

            control.setValue(initial_value + 11)
            assert project.dirty
            with patch(
                "src.ui.editor_project.QFileDialog.getOpenFileName",
                return_value=(str(project_path), ""),
            ), patch(
                "src.ui.editor_project.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Discard,
            ):
                project.open_button.click()
                wait_for_project(builder)
            assert control.value() == initial_value
            assert not project.dirty

            recovered_value = initial_value + 23
            control.setValue(recovered_value)
            assert project.dirty
            assert project.autosave()
            wait_for_project(builder)
            recovery_path = project.recovery_path
            assert json.loads(recovery_path.read_text(encoding="utf-8"))["controls"][
                control_name
            ] == recovered_value

            with patch(
                "src.ui.editor_project.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Discard,
            ):
                assert first.close()
            sip.delete(first)
            windows.remove(first)
            app.processEvents()

            second = MainWindow(settings)
            windows.append(second)
            if kind == "gif":
                second._open_or_focus_gif_builder()
                reopened = second._gif_builder_dlg
            else:
                second._open_or_focus_video_builder()
                reopened = second._video_tool_dlg
            assert reopened._project.recover_button.isEnabled()
            assert reopened._project.recover_button.text().endswith("(1)")
            with patch(
                "src.ui.editor_project.QInputDialog.getItem",
                side_effect=lambda _parent, _title, _label, choices, *_args:
                    (choices[0], True),
            ), patch(
                "src.ui.editor_project.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Discard,
            ):
                reopened._project.recover_button.click()
                wait_for_project(reopened)
            assert len(reopened._project.entries) == 1
            assert getattr(reopened, control_name).value() == recovered_value
            assert "Recovered edits" in reopened._project.status.text()
        finally:
            for window in reversed(windows):
                if sip.isdeleted(window):
                    continue
                for attr in ("_gif_builder_dlg", "_video_tool_dlg"):
                    dialog = getattr(window, attr, None)
                    if dialog is not None:
                        wait_for_project(dialog)
                with patch(
                    "src.ui.editor_project.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Discard,
                ):
                    window.close()
                sip.delete(window)
            settings.sync()
            sip.delete(settings._qs)
            app.processEvents()
            app.setFont(original_font)
            app.setStyleSheet(original_stylesheet)
