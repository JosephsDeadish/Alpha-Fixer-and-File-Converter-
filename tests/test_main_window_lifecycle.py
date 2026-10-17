import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget
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


def test_main_window_history_refreshes_and_opens_converter_output(app, tmp_path):
    original_font, original_stylesheet = app.font(), app.styleSheet()

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(tmp_path / "settings.ini"),
    ):
        settings = SettingsManager()
        window = MainWindow(settings)
    try:
        window.show()
        app.processEvents()
        history = window._history_tab
        tree = history._conv_tree
        assert tree.topLevelItemCount() == 0
        window._tabs.setCurrentIndex(window._tabs.indexOf(window._converter_tab))
        converter = window._converter_tab
        source_path = tmp_path / "source.png"
        with Image.new("RGB", (8, 6), "navy") as source:
            source.save(source_path)
        output_dir = tmp_path / "converted"
        with patch(
            "src.ui.converter_tool.QFileDialog.getOpenFileNames",
            return_value=([str(source_path)], ""),
        ):
            converter._btn_add_files.click()
        converter._out_dir_edit.setText(str(output_dir))
        fmt_index = next(
            index for index in range(converter._fmt_combo.count())
            if converter._fmt_combo.itemData(index)[0] == "JPEG"
        )
        assert fmt_index >= 0
        converter._fmt_combo.setCurrentIndex(fmt_index)
        with patch(
            "src.ui.converter_tool.QMessageBox.exec",
            return_value=QMessageBox.DialogCode.Rejected,
        ):
            converter._btn_run.click()
            deadline = time.monotonic() + 15
            while not settings.get_converter_history() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()
        records = settings.get_converter_history()
        assert records
        result = records[0]["results"][0]
        output_path = Path(result["output"])
        assert output_path.is_file()
        assert result["status"] == "success"
        assert result["output_format"] == "jpg"

        window._tabs.setCurrentIndex(window._tabs.indexOf(history))
        app.processEvents()
        assert tree.topLevelItemCount() == 1
        item = tree.topLevelItem(0)
        assert output_path.name in item.text(6)

        row = tree.visualItemRect(item)
        assert row.isValid()
        assert tree.isVisible()
        assert tree.viewport().rect().contains(row.center())
        with patch(
            "src.ui.history_tab.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            QTest.mouseClick(
                tree.viewport(),
                Qt.MouseButton.LeftButton,
                pos=row.center(),
            )
            QTest.mouseDClick(
                tree.viewport(),
                Qt.MouseButton.LeftButton,
                pos=row.center(),
            )
        app.processEvents()
        open_url.assert_called_once()
        assert open_url.call_args.args[0].toLocalFile() == str(output_path)

        def choose_output_and_folder(menu, *_args):
            actions = {action.text(): action for action in menu.actions()}
            output_actions = actions["Open output"].menu().actions()
            folder_actions = actions["Show output folder"].menu().actions()
            assert [action.text() for action in output_actions] == [output_path.name]
            assert len(folder_actions) == 1
            output_actions[0].trigger()
            folder_actions[0].trigger()

        with patch(
            "src.ui.history_tab.QDesktopServices.openUrl", return_value=True
        ) as open_url, patch.object(QMenu, "exec", new=choose_output_and_folder):
            tree.customContextMenuRequested.emit(row.center())
        assert [
            call.args[0].toLocalFile() for call in open_url.call_args_list
        ] == [str(output_path), str(output_dir)]
    finally:
        converter = window._converter_tab
        preview_loader = converter._preview_loader
        converter._stop_preview_loader()
        if preview_loader is not None:
            assert preview_loader.wait(15_000)
        worker = converter._worker
        if worker is not None:
            assert worker.wait(15_000)
        window.close()
        sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)
        app.processEvents()
        app.setFont(original_font)
        app.setStyleSheet(original_stylesheet)
