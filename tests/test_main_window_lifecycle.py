import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget
from PIL import Image

from src.core.settings_manager import SettingsManager
from src.ui.main_window import MainWindow
from tests.video_export_helpers import wait_for_video_export


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


def test_main_window_shutdown_preserves_dirty_builder_sessions_until_approved(app, tmp_path):
    from src.ui import editor_project

    original_font, original_stylesheet = app.font(), app.styleSheet()
    source_path = tmp_path / "shutdown-source.png"
    with Image.new("RGBA", (12, 9), "purple") as source:
        source.save(source_path)

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(tmp_path / "settings.ini"),
    ), patch.object(
        editor_project, "user_data_directory", return_value=tmp_path / "user-data"
    ):
        settings = SettingsManager()
        window = MainWindow(settings)
    try:
        window.show()
        window._open_or_focus_gif_builder()
        gif = window._gif_builder_dlg
        with patch(
            "src.ui.gif_builder.QFileDialog.getOpenFileNames",
            return_value=([str(source_path)], ""),
        ):
            gif._btn_add.click()
        window._open_or_focus_video_builder()
        video = window._video_tool_dlg
        with patch(
            "src.ui.video_tool.QFileDialog.getOpenFileNames",
            return_value=([str(source_path)], ""),
        ):
            video._btn_add_img.click()
        assert gif._project.dirty and video._project.dirty
        assert gif._project.autosave()
        wait_for_project(gif)
        assert video._project.autosave()
        wait_for_project(video)
        gif_lock, video_lock = gif._project._lock, video._project._lock
        assert gif_lock is not None and video_lock is not None

        with patch(
            "src.ui.editor_project.QMessageBox.question",
            side_effect=[
                QMessageBox.StandardButton.Discard,
                QMessageBox.StandardButton.Cancel,
            ],
        ) as question:
            assert not window.close()
        assert question.call_count == 2
        assert window.isVisible()
        assert gif._project.dirty and video._project.dirty
        assert not gif._project._closing_approved
        assert not video._project._closing_approved
        assert gif_lock.isLocked() and video_lock.isLocked()

        gif_project_path = tmp_path / "shutdown.gifproject.json"
        with patch(
            "src.ui.editor_project.QMessageBox.question",
            side_effect=[
                QMessageBox.StandardButton.Save,
                QMessageBox.StandardButton.Discard,
            ],
        ) as question, patch(
            "src.ui.editor_project.QFileDialog.getSaveFileName",
            return_value=(str(gif_project_path), ""),
        ):
            assert not window.close()
            wait_for_project(gif)
        app.processEvents()

        assert question.call_count == 2
        assert gif_project_path.is_file()
        assert not window.isVisible()
        assert window._shutdown_complete
        assert not gif_lock.isLocked() and not video_lock.isLocked()
        assert len(json.loads(gif_project_path.read_text(encoding="utf-8"))["entries"]) == 1
        assert video._project.recovery_path.is_file()
    finally:
        if window.isVisible():
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


def test_main_window_clear_all_history_confirmation_and_relaunch(app, tmp_path):
    original_font, original_stylesheet = app.font(), app.styleSheet()
    settings_path = tmp_path / "settings.ini"
    history_tools = (
        "converter", "alpha", "selective_alpha", "gif_builder", "video_builder",
    )

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(settings_path),
    ):
        settings = SettingsManager()
        for tool in history_tools:
            settings.set(
                f"{tool}_history",
                json.dumps([{
                    "timestamp": "2026-10-17T12:00:00",
                    "format": "PNG",
                    "file_count": 1,
                    "success": 1,
                    "errors": 0,
                    "files": [f"{tool}.png"],
                }]),
            )
        settings.set("font_size", 14)
        settings.set("custom_shortcuts", json.dumps({"help": "Ctrl+Alt+H"}))
        settings.sync()
        window = MainWindow(settings)

    try:
        window.show()
        app.processEvents()
        history = window._history_tab
        trees = (
            history._conv_tree, history._alpha_tree, history._sel_tree,
            history._gif_tree, history._vid_tree,
        )
        assert all(tree.topLevelItemCount() == 1 for tree in trees)

        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.No,
        ) as confirmation:
            history._btn_clear.click()
        confirmation.assert_called_once()
        assert all(
            getattr(settings, f"get_{tool}_history")()
            for tool in history_tools
        )
        assert all(tree.topLevelItemCount() == 1 for tree in trees)

        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as confirmation:
            history._btn_clear.click()
        confirmation.assert_called_once()
        assert all(
            getattr(settings, f"get_{tool}_history")() == []
            for tool in history_tools
        )
        assert all(tree.topLevelItemCount() == 0 for tree in trees)
        assert settings.get("font_size") == 14
        assert settings.get_custom_shortcuts() == {"help": "Ctrl+Alt+H"}

        settings.sync()
        window.close()
        sip.delete(window)
        sip.delete(settings._qs)
        app.processEvents()

        with patch(
            "src.core.settings_manager._settings_ini_path",
            return_value=str(settings_path),
        ):
            settings = SettingsManager()
            window = MainWindow(settings)
        window.show()
        app.processEvents()
        assert all(
            getattr(settings, f"get_{tool}_history")() == []
            for tool in history_tools
        )
        assert all(
            tree.topLevelItemCount() == 0
            for tree in (
                window._history_tab._conv_tree,
                window._history_tab._alpha_tree,
                window._history_tab._sel_tree,
                window._history_tab._gif_tree,
                window._history_tab._vid_tree,
            )
        )
        assert settings.get("font_size") == 14
        assert settings.get_custom_shortcuts() == {"help": "Ctrl+Alt+H"}
    finally:
        if not sip.isdeleted(window):
            window.close()
            sip.delete(window)
        if not sip.isdeleted(settings._qs):
            settings.sync()
            sip.delete(settings._qs)
        app.processEvents()
        app.setFont(original_font)
        app.setStyleSheet(original_stylesheet)


def test_main_window_converter_fallback_batch_failure_retry_and_history_export(
        app, tmp_path):
    original_font, original_stylesheet = app.font(), app.styleSheet()
    settings_path = tmp_path / "settings.ini"
    valid_path = tmp_path / "valid.png"
    broken_path = tmp_path / "broken.png"
    with Image.new("RGBA", (9, 7), (30, 80, 140, 180)) as source:
        source.save(valid_path)
    broken_path.write_bytes(b"not a PNG")
    output_dir = tmp_path / "converted"
    output_path = output_dir / "valid.png"

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(settings_path),
    ):
        settings = SettingsManager()
        window = MainWindow(settings)
    converter = window._converter_tab

    def wait_for_history(count):
        deadline = time.monotonic() + 15
        while len(settings.get_converter_history()) < count and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert len(settings.get_converter_history()) >= count

    try:
        window.show()
        with patch(
            "src.ui.converter_tool.QFileDialog.getOpenFileNames",
            return_value=([str(valid_path), str(broken_path)], ""),
        ):
            converter._btn_add_files.click()
        converter._out_dir_edit.setText(str(output_dir))
        jpeg_index = next(
            index for index in range(converter._fmt_combo.count())
            if converter._fmt_combo.itemData(index)[0] == "JPEG"
        )
        converter._fmt_combo.setCurrentIndex(jpeg_index)
        converter._quality_spin.setValue(73)

        unavailable = lambda fmt: "Simulated runtime capability gap" if fmt == "JPEG" else None
        with patch(
            "src.ui.converter_tool.output_format_unavailable_reason",
            side_effect=unavailable,
        ), patch("src.ui.converter_tool.QMessageBox.information"), patch(
            "src.ui.converter_tool.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            converter._btn_run.click()
            wait_for_history(1)

        first = settings.get_converter_history()[0]
        assert first["format"] == "PNG"
        assert first["options"]["requested_format"] == "JPEG"
        assert first["options"]["actual_batch_format"] == "PNG"
        assert first["options"]["quality"] == 73
        assert first["options"]["output_dir"] == str(output_dir)
        assert first["success"] == 1 and first["errors"] == 1
        assert first["stopped"] is False and first["not_processed"] == 0
        result_by_source = {result["source"]: result for result in first["results"]}
        assert result_by_source[str(valid_path)]["status"] == "success"
        assert result_by_source[str(valid_path)]["output"] == str(output_path)
        assert result_by_source[str(valid_path)]["output_format"] == "png"
        assert result_by_source[str(broken_path)]["status"] == "failed"
        assert result_by_source[str(broken_path)]["reason"]
        assert converter._btn_retry_failed.isEnabled()
        assert converter._btn_export_failures.isEnabled()
        assert output_path.is_file()

        failure_report = tmp_path / "converter-failures.json"
        with patch(
            "src.ui.converter_tool.QFileDialog.getSaveFileName",
            return_value=(str(failure_report), "JSON Report (*.json)"),
        ):
            converter._btn_export_failures.click()
        failure_payload = json.loads(failure_report.read_text(encoding="utf-8"))
        assert failure_payload["error_count"] == 1
        assert failure_payload["failures"][0]["source"] == str(broken_path)
        assert failure_payload["failure_groups"][0]["count"] == 1

        with Image.new("RGB", (6, 5), "orange") as repaired:
            repaired.save(broken_path)
        with patch(
            "src.ui.converter_tool.output_format_unavailable_reason",
            side_effect=unavailable,
        ), patch("src.ui.converter_tool.QMessageBox.information"), patch(
            "src.ui.converter_tool.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ), patch(
            "src.ui.converter_tool.QMessageBox.exec",
            return_value=QMessageBox.DialogCode.Rejected,
        ):
            converter._btn_retry_failed.click()
            wait_for_history(2)
        retry = settings.get_converter_history()[0]
        assert retry["success"] == 1 and retry["errors"] == 0
        assert retry["results"][0]["source"] == str(broken_path)
        assert retry["results"][0]["status"] == "success"
        assert Path(retry["results"][0]["output"]).is_file()

        history = window._history_tab
        window._tabs.setCurrentIndex(window._tabs.indexOf(history))
        history.refresh()
        export_path = tmp_path / "converter-history.json"
        with patch(
            "src.ui.history_tab.QFileDialog.getSaveFileName",
            return_value=(str(export_path), "JSON Files (*.json)"),
        ), patch("src.ui.history_tab.QMessageBox.information"):
            history._btn_export.click()
        exported = json.loads(export_path.read_text(encoding="utf-8"))
        exported_by_sources = {
            frozenset(result["source"] for result in item["Per-file outcomes"]): item
            for item in exported
        }
        exported_initial = exported_by_sources[frozenset((str(valid_path), str(broken_path)))]
        assert exported_initial["Format"] == "PNG"
        assert json.loads(exported_initial["Run options"]) == first["options"]
        assert exported_initial["Per-file outcomes"] == first["results"]
        assert str(broken_path) in exported_initial["Failure details"]

        settings.sync()
        window.close()
        sip.delete(window)
        sip.delete(settings._qs)
        app.processEvents()

        with patch(
            "src.core.settings_manager._settings_ini_path",
            return_value=str(settings_path),
        ):
            settings = SettingsManager()
            window = MainWindow(settings)
        window.show()
        history = window._history_tab
        window._tabs.setCurrentIndex(window._tabs.indexOf(history))
        history.refresh()
        app.processEvents()

        persisted = settings.get_converter_history()
        assert persisted[0] == retry
        assert persisted[1] == first
        tree = history._conv_tree
        assert tree.topLevelItemCount() == 2
        mixed_item = next(
            item for item in (
                tree.topLevelItem(index) for index in range(tree.topLevelItemCount())
            )
            if len(item.data(0, item._RESULTS_ROLE)) == 2
        )
        assert mixed_item.data(0, mixed_item._BATCH_STATUS_ROLE) == ["Issues", "0"]
        assert str(output_path) in mixed_item.toolTip(0)
        assert "actual_batch_format: PNG" in mixed_item.toolTip(0)
        assert "broken.png:" in mixed_item.toolTip(0)
        row = tree.visualItemRect(mixed_item)
        row_click = QPoint(30, row.center().y())
        assert row.isValid() and tree.viewport().rect().contains(row_click)
        with patch(
            "src.ui.history_tab.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=row_click)
            QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=row_click)
        open_url.assert_called_once()
        assert open_url.call_args.args[0].toLocalFile() == str(output_path)

        def choose_reloaded_output_and_folder(menu, *_args):
            actions = {action.text(): action for action in menu.actions()}
            output_actions = actions["Open output"].menu().actions()
            folder_actions = actions["Show output folder"].menu().actions()
            assert [action.text() for action in output_actions] == [output_path.name]
            assert [action.text() for action in folder_actions] == [output_dir.name]
            output_actions[0].trigger()
            folder_actions[0].trigger()

        with patch(
            "src.ui.history_tab.QDesktopServices.openUrl", return_value=True
        ) as open_url, patch.object(
            QMenu, "exec", new=choose_reloaded_output_and_folder
        ):
            tree.customContextMenuRequested.emit(row_click)
        assert [
            call.args[0].toLocalFile() for call in open_url.call_args_list
        ] == [str(output_path), str(output_dir)]

        relaunch_export = tmp_path / "reloaded-converter-history.json"
        with patch(
            "src.ui.history_tab.QFileDialog.getSaveFileName",
            return_value=(str(relaunch_export), "JSON Files (*.json)"),
        ), patch("src.ui.history_tab.QMessageBox.information"):
            history._btn_export.click()
        reloaded_exports = json.loads(relaunch_export.read_text(encoding="utf-8"))
        reloaded_mixed = next(
            item for item in reloaded_exports
            if len(item["Per-file outcomes"]) == 2
        )
        assert json.loads(reloaded_mixed["Run options"]) == first["options"]
        assert reloaded_mixed["Per-file outcomes"] == first["results"]
        assert str(output_path) in reloaded_mixed["Output files"]
    finally:
        if not sip.isdeleted(window):
            converter = window._converter_tab
            converter._stop_preview_loader()
            worker = converter._worker
            if worker is not None and worker.isRunning():
                worker.stop()
                assert worker.wait(15_000)
            window.close()
            sip.delete(window)
        if not sip.isdeleted(settings._qs):
            settings.sync()
            sip.delete(settings._qs)
        app.processEvents()
        app.setFont(original_font)
        app.setStyleSheet(original_stylesheet)


def test_main_window_converter_stop_records_unprocessed_inputs(app, tmp_path):
    from threading import Event
    from src.core.file_converter import convert_file as real_convert_file

    original_font, original_stylesheet = app.font(), app.styleSheet()
    settings_path = tmp_path / "settings.ini"
    source_paths = []
    for index in range(4):
        path = tmp_path / f"stop-{index}.png"
        with Image.new("RGB", (8, 6), (index * 30, 50, 120)) as source:
            source.save(path)
        source_paths.append(path)
    output_dir = tmp_path / "stopped-output"

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(settings_path),
    ):
        settings = SettingsManager()
        window = MainWindow(settings)
    converter = window._converter_tab
    entered, release = Event(), Event()

    def paused_convert(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test did not release the in-flight conversion")
        return real_convert_file(*args, **kwargs)

    try:
        window.show()
        with patch(
            "src.ui.converter_tool.QFileDialog.getOpenFileNames",
            return_value=([str(path) for path in source_paths], ""),
        ):
            converter._btn_add_files.click()
        converter._out_dir_edit.setText(str(output_dir))
        with patch(
            "src.core.worker.ConverterWorker._recommend_worker_count",
            return_value=1,
        ), patch("src.core.worker.convert_file", side_effect=paused_convert):
            converter._btn_run.click()
            assert entered.wait(10)
            converter._btn_stop.click()
            release.set()
            deadline = time.monotonic() + 15
            while not settings.get_converter_history() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()

        records = settings.get_converter_history()
        assert len(records) == 1
        stopped = records[0]
        assert stopped["stopped"] is True
        assert stopped["success"] == 1
        assert stopped["errors"] == 0
        assert stopped["not_processed"] == len(source_paths) - 1
        assert len(stopped["results"]) == len(source_paths)
        assert stopped["results"][0]["status"] == "success"
        assert Path(stopped["results"][0]["output"]).is_file()
        assert all(
            result["status"] == "not processed"
            for result in stopped["results"][1:]
        )

        worker = converter._worker
        assert worker is not None and worker.wait(15_000)
        settings.sync()
        window.close()
        sip.delete(window)
        sip.delete(settings._qs)
        app.processEvents()

        with patch(
            "src.core.settings_manager._settings_ini_path",
            return_value=str(settings_path),
        ):
            settings = SettingsManager()
            window = MainWindow(settings)
        window.show()
        history = window._history_tab
        window._tabs.setCurrentIndex(window._tabs.indexOf(history))
        history.refresh()
        app.processEvents()

        persisted = settings.get_converter_history()
        assert len(persisted) == 1
        assert persisted[0]["stopped"] is True
        assert persisted[0]["not_processed"] == len(source_paths) - 1
        assert persisted[0]["results"] == stopped["results"]
        tree = history._conv_tree
        assert tree.topLevelItemCount() == 1
        item = tree.topLevelItem(0)
        assert item.data(0, item._BATCH_STATUS_ROLE) == [
            "Stopped",
            str(len(source_paths) - 1),
        ]
        assert f"Not processed: {len(source_paths) - 1}" in item.toolTip(0)
        assert str(output_dir / "stop-0.png") in item.toolTip(0)
        history._conv_search.setText("status:stopped")
        assert not item.isHidden()

        export_path = tmp_path / "stopped-history.json"
        with patch(
            "src.ui.history_tab.QFileDialog.getSaveFileName",
            return_value=(str(export_path), "JSON Files (*.json)"),
        ), patch("src.ui.history_tab.QMessageBox.information"):
            history._btn_export.click()
        exported = json.loads(export_path.read_text(encoding="utf-8"))
        assert len(exported) == 1
        assert exported[0]["Status"] == "Stopped"
        assert exported[0]["Not processed"] == str(len(source_paths) - 1)
        assert exported[0]["Per-file outcomes"] == stopped["results"]
    finally:
        release.set()
        if not sip.isdeleted(window):
            converter = window._converter_tab
            worker = converter._worker
            if worker is not None and worker.isRunning():
                worker.stop()
                assert worker.wait(15_000)
            converter._stop_preview_loader()
            window.close()
            sip.delete(window)
        if not sip.isdeleted(settings._qs):
            settings.sync()
            sip.delete(settings._qs)
        app.processEvents()
        app.setFont(original_font)
        app.setStyleSheet(original_stylesheet)


def test_main_window_gif_media_picker_selects_stream_and_reports_decode_failure(
        app, tmp_path):
    from src.ui import editor_project
    from src.ui.gif_builder import _GifStreamPickerDialog

    original_font, original_stylesheet = app.font(), app.styleSheet()
    selected_path = tmp_path / "selected.mkv"
    failed_path = tmp_path / "failed.mkv"
    selected_path.touch()
    failed_path.touch()
    probe = {
        "video_stream_index": 1,
        "audio_stream_index": 2,
        "video_stream_count": 2,
        "audio_stream_count": 2,
        "video_stream_choices": [
            {"index": 1, "codec_name": "h264", "width": 640, "height": 360},
            {"index": 3, "codec_name": "hevc", "width": 1280, "height": 720},
        ],
        "audio_stream_choices": [
            {"index": 2, "codec_name": "aac", "tags": {"language": "eng"}},
            {"index": 4, "codec_name": "opus", "tags": {"language": "jpn"}},
        ],
    }
    selections = []

    def choose_streams():
        picker = next(
            (
                widget for widget in app.topLevelWidgets()
                if isinstance(widget, _GifStreamPickerDialog) and widget.isVisible()
            ),
            None,
        )
        if picker is None:
            if time.monotonic() < choose_streams.deadline:
                QTimer.singleShot(10, choose_streams)
            else:
                modal = app.activeModalWidget()
                if modal is not None:
                    modal.reject()
            return
        picker._video_combo.setCurrentIndex(2)
        picker._audio_combo.setCurrentIndex(2)
        selections.append((
            picker.selected_video_stream_index,
            picker.selected_audio_stream_index,
        ))
        if len(selections) < 2:
            QTimer.singleShot(0, choose_streams)
        picker.accept()
    choose_streams.deadline = time.monotonic() + 10

    def decode(path, **_kwargs):
        if path == str(failed_path):
            raise ValueError("selected stream decode failed")
        return [Image.new("RGBA", (7, 5), "teal")], 25.0

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(tmp_path / "settings.ini"),
    ):
        settings = SettingsManager()
        window = MainWindow(settings)
    try:
        window.show()
        window._open_or_focus_gif_builder()
        builder = window._gif_builder_dlg
        QTimer.singleShot(0, choose_streams)
        with patch(
            "src.ui.gif_builder.QFileDialog.getOpenFileNames",
            return_value=([str(selected_path), str(failed_path)], ""),
        ), patch(
            "src.ui.gif_builder._probe_media_details", return_value=probe
        ), patch(
            "src.ui.gif_builder._load_video_frames", side_effect=decode
        ) as load_video, patch(
            "src.ui.gif_builder._video_load_failure_hint",
            return_value="Selected video stream #3 could not be decoded.",
        ), patch(
            "src.ui.gif_builder._visual_still_fallback_note",
            return_value="No still frame was recoverable.",
        ), patch(
            "src.ui.gif_builder._extract_visual_still_frame", return_value=None
        ):
            builder._btn_add.click()

        assert selections == [(3, 4), (3, 4)]
        assert [call.kwargs["preferred_video_stream_index"] for call in load_video.call_args_list] == [3, 3]
        assert [call.kwargs["preferred_audio_stream_index"] for call in load_video.call_args_list] == [4, 4]
        assert len(builder._frames) == 1
        assert "1 failed" in builder._import_status_lbl.text()
        details = builder._import_detail_box.toPlainText()
        assert "selected.mkv" in details and "video stream #3" in details
        assert "failed.mkv: selected stream decode failed" in details
        assert "Selected video stream #3 could not be decoded." in details
    finally:
        with patch.object(
            editor_project.QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            window.close()
        sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)
        app.processEvents()
        app.setFont(original_font)
        app.setStyleSheet(original_stylesheet)


def test_main_window_gif_export_is_recorded_and_actionable_in_history(app, tmp_path):
    from src.ui import editor_project

    original_font, original_stylesheet = app.font(), app.styleSheet()
    frame_paths = []
    for index, color in enumerate(("red", "blue")):
        path = tmp_path / f"frame-{index}.png"
        with Image.new("RGB", (10, 8), color) as frame:
            frame.save(path)
        frame_paths.append(str(path))

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(tmp_path / "settings.ini"),
    ):
        settings = SettingsManager()
        window = MainWindow(settings)
    builder = None
    try:
        window.show()
        window._open_or_focus_gif_builder()
        builder = window._gif_builder_dlg
        with patch(
            "src.ui.gif_builder.QFileDialog.getOpenFileNames",
            return_value=(frame_paths, ""),
        ):
            builder._btn_add.click()
        assert len(builder._frames) == 2

        output_path = tmp_path / "animation.gif"
        with patch(
            "src.ui.gif_builder.QFileDialog.getSaveFileName",
            return_value=(str(output_path), "GIF Files (*.gif)"),
        ), patch("src.ui.gif_builder.QMessageBox.information"):
            builder._btn_export.click()
            deadline = time.monotonic() + 15
            while builder._export_worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()
        assert builder._export_worker is None
        assert output_path.is_file()
        with Image.open(output_path) as exported:
            assert exported.n_frames == 2

        records = settings.get_gif_builder_history()
        assert len(records) == 1
        assert records[0]["output"] == str(output_path)
        assert records[0]["frame_count"] == 2

        history = window._history_tab
        window._tabs.setCurrentIndex(window._tabs.indexOf(history))
        history._sub_tabs.setCurrentIndex(3)
        app.processEvents()
        tree = history._gif_tree
        assert tree.topLevelItemCount() == 1
        item = tree.topLevelItem(0)
        assert item.text(1) == output_path.name
        assert item.text(2) == "2"
        row = tree.visualItemRect(item)
        row_click = QPoint(30, row.center().y())
        assert row.isValid() and tree.viewport().rect().contains(row_click)
        with patch(
            "src.ui.history_tab.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=row_click)
            QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=row_click)
        open_url.assert_called_once()
        assert open_url.call_args.args[0].toLocalFile() == str(output_path)
    finally:
        if builder is not None and builder._export_worker is not None:
            builder._export_worker.wait(15_000)
        with patch.object(
            editor_project.QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            window.close()
        sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)
        app.processEvents()
        app.setFont(original_font)
        app.setStyleSheet(original_stylesheet)


def test_main_window_video_builder_gif_export_is_actionable_in_history(app, tmp_path):
    from src.ui import editor_project

    original_font, original_stylesheet = app.font(), app.styleSheet()
    source_path = tmp_path / "still.png"
    with Image.new("RGBA", (18, 20), "green") as source:
        source.save(source_path)

    with patch(
        "src.core.settings_manager._settings_ini_path",
        return_value=str(tmp_path / "settings.ini"),
    ):
        settings = SettingsManager()
        window = MainWindow(settings)
    builder = None
    try:
        window.show()
        window._open_or_focus_video_builder()
        builder = window._video_tool_dlg
        with patch(
            "src.ui.video_tool.QFileDialog.getOpenFileNames",
            return_value=([str(source_path)], ""),
        ):
            builder._btn_add_img.click()
        assert len(builder._clips) == 1

        gif_index = builder._export_fmt_combo.findData("gif")
        assert gif_index >= 0
        builder._export_fmt_combo.setCurrentIndex(gif_index)
        output_path = tmp_path / "video-builder.gif"
        with patch(
            "src.ui.video_tool.QFileDialog.getSaveFileName",
            return_value=(str(output_path), "GIF Files (*.gif)"),
        ), patch("src.ui.video_tool.QMessageBox.information"):
            builder._btn_export.click()
            wait_for_video_export(builder)
        assert output_path.is_file()
        with Image.open(output_path) as exported:
            assert exported.n_frames >= 1

        records = settings.get_video_builder_history()
        assert len(records) == 1
        assert records[0]["output"] == str(output_path)
        assert records[0]["format"] == "GIF"
        assert records[0]["clip_count"] == 1

        history = window._history_tab
        window._tabs.setCurrentIndex(window._tabs.indexOf(history))
        history._sub_tabs.setCurrentIndex(4)
        app.processEvents()
        tree = history._vid_tree
        assert tree.topLevelItemCount() == 1
        item = tree.topLevelItem(0)
        assert item.text(1) == output_path.name
        assert item.text(2) == "GIF"
        assert item.text(3) == "1"
        assert item.text(13) == "OK"
        row = tree.visualItemRect(item)
        row_click = QPoint(30, row.center().y())
        assert row.isValid() and tree.viewport().rect().contains(row_click)
        with patch(
            "src.ui.history_tab.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=row_click)
            QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=row_click)
        open_url.assert_called_once()
        assert open_url.call_args.args[0].toLocalFile() == str(output_path)
    finally:
        if builder is not None and builder.is_exporting():
            builder.request_export_cancel()
            wait_for_video_export(builder)
        with patch.object(
            editor_project.QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            window.close()
        sip.delete(window)
        settings.sync()
        sip.delete(settings._qs)
        app.processEvents()
        app.setFont(original_font)
        app.setStyleSheet(original_stylesheet)
