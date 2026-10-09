from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.presets import PresetManager
from src.core.settings_manager import SettingsManager
from src.core.worker import AlphaWorker, ConverterWorker
from src.ui._ui_utils import batch_completion_summary, verified_originals
from src.ui.alpha_tool import AlphaFixerTab
from src.ui.converter_tool import ConverterTab
from src.ui.gif_builder import GifBuilderDialog
from src.ui.history_tab import HistoryTab
from src.version import APP_NAME


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture(params=["alpha", "converter"])
def tool(request, app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        manager = SettingsManager()
        if request.param == "alpha":
            widget = AlphaFixerTab(PresetManager(manager), manager)
        else:
            widget = ConverterTab(manager)
        widget._last_run_files = []
        yield widget
        widget._worker = None
        widget.close()
        sip.delete(widget)
        manager.sync()
        sip.delete(manager._qs)


@pytest.mark.parametrize("success,errors,total,stopped,pct,prefix,remaining", [
    (2, 1, 5, True, 60, "Stopped.", "2 not processed"),
    (0, 0, 5, True, 0, "Stopped.", "5 not processed"),
    (4, 1, 5, False, 100, "Done.", ""),
    (5, 0, 5, True, 100, "Stopped.", ""),
    (1, 0, 5, False, 20, "Stopped.", "4 not processed"),
    (999, 0, 1000, True, 99, "Stopped.", "1 not processed"),
])
def test_completion_summary(success, errors, total, stopped, pct, prefix, remaining):
    progress, text = batch_completion_summary(success, errors, total, stopped)
    assert progress == pct
    assert text.startswith(prefix)
    assert f"{success} succeeded" in text
    assert f"{errors} failed" in text
    if remaining:
        assert remaining in text


@pytest.mark.parametrize("stopped,success,errors,total,pct,prefix", [
    (True, 2, 1, 5, 60, "Stopped."),
    (True, 0, 0, 5, 0, "Stopped."),
    (False, 4, 1, 5, 100, "Done."),
])
def test_real_completion_feedback(tool, stopped, success, errors, total, pct, prefix):
    tool._stop_requested = stopped
    tool._batch_total = total
    tool._on_finished(success, errors)
    assert tool._progress.value() == pct
    assert tool._status_lbl.text().startswith(prefix)
    assert tool._btn_run.isEnabled()
    assert not tool._btn_stop.isEnabled()
    assert APP_NAME in tool.windowTitle()
    assert tool._log.toPlainText().count(f"─── {prefix}") == 1


def test_stop_is_idempotent_and_late_progress_keeps_stopping_status(tool):
    worker = Mock()
    tool._worker = worker
    tool._btn_run.setEnabled(False)
    tool._btn_stop.setEnabled(True)
    tool._stop()
    tool._stop()
    worker.stop.assert_called_once()
    assert not tool._btn_stop.isEnabled()
    tool._on_progress(1, 5, "/tmp/frame.png")
    assert tool._status_lbl.text().startswith("Stopping…")
    assert "Stopping…" in tool.windowTitle()
    assert not tool._btn_run.isEnabled()


def test_verified_originals_require_a_distinct_existing_output(tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"source")
    output = tmp_path / "converted.png"
    output.write_bytes(b"output")
    assert verified_originals({str(source): str(output)}) == [str(source)]
    assert verified_originals({str(source): str(source)}) == []
    assert verified_originals({str(source): str(tmp_path / "missing.png")}) == []
    assert verified_originals({str(source): str(output), str(output): str(source)}) == []
    hardlink = tmp_path / "linked.png"
    hardlink.hardlink_to(source)
    assert verified_originals({str(source): str(hardlink)}) == []
    second = tmp_path / "second.png"
    second.write_bytes(b"second")
    assert verified_originals({str(source): str(output), str(second): str(output)}) == []
    output_link = tmp_path / "output_link.png"
    output_link.hardlink_to(output)
    assert verified_originals({str(source): str(output), str(second): str(output_link)}) == []


@pytest.mark.parametrize("success,errors,total", [(1, 1, 2), (1, 0, 2)])
def test_partial_batches_never_offer_original_deletion(tool, tmp_path, success, errors, total):
    source = tmp_path / "original.png"
    output = tmp_path / "converted.png"
    source.write_bytes(b"source")
    output.write_bytes(b"output")
    tool._batch_outputs = {str(source): str(output)}
    tool._batch_total = total
    tool._last_run_files = [str(source)]
    with patch.object(tool, "_offer_delete_originals") as offer:
        tool._on_finished(success, errors)
    offer.assert_not_called()


def test_completed_batch_offers_only_verified_copies_even_if_controls_change(tool, tmp_path):
    source = tmp_path / "original.png"
    output = tmp_path / "converted.png"
    inplace = tmp_path / "inplace.png"
    for path in (source, output, inplace):
        path.write_bytes(b"image")
    tool._batch_outputs = {str(source): str(output), str(inplace): str(inplace)}
    tool._batch_total = 2
    tool._last_run_files = [str(source), str(inplace)]
    tool._suffix_edit.setText("")
    tool._out_dir_edit.setText("")
    with patch.object(tool, "_offer_delete_originals") as offer:
        tool._on_finished(2, 0)
    offer.assert_called_once_with([str(source)], 1)


def test_manifest_records_actual_batch_outputs(tool, tmp_path):
    source = str(tmp_path / "source.png")
    dest = str(tmp_path / "source_fixed.png")
    tool._on_output_manifest({source: dest})
    assert tool._batch_outputs == {source: dest}


@pytest.mark.parametrize("kind", ["alpha", "converter"])
def test_large_batch_manifest_is_emitted_before_completion_with_success_logs_suppressed(app, tmp_path, kind):
    files = []
    for index in range(2):
        path = tmp_path / f"source{index}.png"
        with Image.new("RGBA", (8, 8), (30, 40, 50, 255)) as image:
            image.save(path)
        files.append(str(path))
    if kind == "alpha":
        worker = AlphaWorker(files, suffix="_fixed")
    else:
        worker = ConverterWorker(files, "PNG", ".png", suffix="_fixed")
    events = []
    done = Mock()
    worker.file_done.connect(done)
    worker.output_manifest.connect(lambda outputs: events.append(("outputs", outputs)))
    worker.finished.connect(lambda success, errors: events.append(("finished", success, errors)))
    with patch("src.core.worker._LARGE_BATCH_THRESHOLD", 1):
        worker.run()
    done.assert_not_called()
    assert events[1] == ("finished", 2, 0)
    assert events[0] == ("outputs", {
        path: str(tmp_path / f"source{index}_fixed.png") for index, path in enumerate(files)
    })
    assert all(Path(dest).is_file() for dest in events[0][1].values())


def test_next_run_clears_stop_request_and_previous_output_records(tool, tmp_path):
    source = tmp_path / "new.png"
    with Image.new("RGBA", (8, 8)) as image:
        image.save(source)
    tool._stop_requested = True
    tool._batch_outputs = {"previous.png": "previous_fixed.png"}
    tool._file_list.blockSignals(True)
    tool._file_list.addItem(str(source))
    tool._file_list.blockSignals(False)
    worker_class = "AlphaWorker" if isinstance(tool, AlphaFixerTab) else "ConverterWorker"
    module = "src.ui.alpha_tool" if isinstance(tool, AlphaFixerTab) else "src.ui.converter_tool"
    tool._suffix_edit.setText("_fixed")
    with patch(f"{module}.{worker_class}") as worker:
        tool._run()
        worker.return_value.start.assert_called_once()
    assert not tool._stop_requested
    assert tool._batch_outputs == {}
    assert tool._btn_stop.isEnabled()
    assert not tool._btn_run.isEnabled()


def test_stopped_status_is_retained_and_searchable_in_history(tool):
    tool._batch_total = 5
    tool._stop_requested = True
    tool._on_finished(2, 0)
    history = HistoryTab(tool._settings)
    try:
        if isinstance(tool, AlphaFixerTab):
            tree = history._alpha_tree
            entries = tool._settings.get_alpha_history()
        else:
            tree = history._conv_tree
            entries = tool._settings.get_converter_history()
        assert entries[-1]["stopped"] is True
        assert entries[-1]["not_processed"] == 3
        item = tree.topLevelItem(0)
        assert "Status: Stopped" in item.toolTip(0)
        assert "Not processed: 3" in item.toolTip(0)
        HistoryTab._apply_filter(tree, "status:stopped")
        assert not item.isHidden()
        HistoryTab._apply_filter(tree, "status:ok")
        assert item.isHidden()
    finally:
        history.close()
        sip.delete(history)


def test_original_deletion_defaults_to_keep_and_escape(tool, tmp_path):
    source = tmp_path / "original.png"
    source.write_bytes(b"original")
    observed = []

    def inspect_dialog(dialog):
        observed.append(dialog.defaultButton().text())
        assert dialog.escapeButton() is dialog.defaultButton()
        return 0

    with patch.object(QMessageBox, "exec", inspect_dialog):
        tool._offer_delete_originals([str(source)], 1)
    assert observed == ["Keep Originals"]
    assert source.exists()


@pytest.fixture
def builder(app):
    widget = GifBuilderDialog()
    yield widget
    widget._clear_all()
    widget.close()
    sip.delete(widget)


def add_frames(builder, tmp_path):
    paths = []
    for index in range(2):
        path = tmp_path / f"frame{index}.png"
        with Image.new("RGBA", (8, 8), (index * 100, 0, 0, 255)) as image:
            image.save(path)
        paths.append(str(path))
    builder._add_paths(paths)
    builder._frame_list.setCurrentRow(0)


def test_empty_gif_transport_and_delay_controls_are_disabled(builder):
    assert not builder._btn_play.isEnabled()
    assert not builder._btn_rewind.isEnabled()
    assert not builder._scrubber.isEnabled()
    assert not builder._pf_check.isEnabled()
    assert not builder._pf_slider.isEnabled()


def test_removing_to_one_frame_stops_gif_playback_immediately(builder, tmp_path):
    add_frames(builder, tmp_path)
    builder._btn_play.setChecked(True)
    assert builder._preview_timer.isActive()
    builder._remove_selected()
    assert len(builder._frames) == 1
    assert not builder._preview_timer.isActive()
    assert not builder._btn_play.isChecked()
    assert not builder._btn_play.isEnabled()
    assert builder._btn_rewind.isEnabled()
    assert builder._frame_list.currentRow() == 0
    assert builder._pf_check.isEnabled()
    builder._pf_check.setChecked(True)
    builder._pf_slider.setValue(800)
    assert builder._frames[0].delay_ms == 800
    builder._clear_all()
    assert not builder._pf_check.isEnabled()
    assert builder._preview_frame_lbl.text() == "0 / 0"


def test_gif_preview_honours_override_from_first_tick_and_live_edits(builder, tmp_path):
    add_frames(builder, tmp_path)
    builder._pf_check.setChecked(True)
    builder._pf_slider.setValue(700)
    builder._btn_play.setChecked(True)
    assert builder._preview_timer.interval() == 700
    builder._delay_slider.setValue(250)
    assert builder._preview_timer.interval() == 700
    builder._pf_slider.setValue(900)
    assert builder._preview_timer.interval() == 900
    builder._pf_check.setChecked(False)
    assert builder._preview_timer.interval() == 250
    builder._advance_preview()
    assert builder._preview_timer.interval() == 250


@pytest.mark.parametrize("playing", [False, True])
def test_global_delay_updates_inherited_control_without_changing_playhead(builder, tmp_path, playing):
    add_frames(builder, tmp_path)
    builder._btn_play.setChecked(playing)
    builder._scrubber.setValue(1)
    builder._delay_slider.setValue(450)
    assert builder._preview_idx == 1
    assert builder._pf_slider.value() == 450
    assert builder._pf_val_lbl.text() == "450 ms"
    assert not builder._pf_slider.isEnabled()
    assert builder._frames[0].delay_ms is None
    builder._pf_check.setChecked(True)
    assert builder._frames[0].delay_ms == 450


def test_disabling_override_displays_global_delay_without_mutating_other_frames(builder, tmp_path):
    add_frames(builder, tmp_path)
    builder._pf_check.setChecked(True)
    builder._pf_slider.setValue(700)
    builder._delay_slider.setValue(300)
    assert builder._pf_slider.value() == 700
    assert builder._frames[0].delay_ms == 700
    builder._pf_check.setChecked(False)
    assert builder._pf_slider.value() == 300
    assert builder._pf_val_lbl.text() == "300 ms"
    assert builder._frames[0].delay_ms is None
    assert builder._frames[1].delay_ms is None
    builder._pf_check.setChecked(True)
    assert builder._frames[0].delay_ms == 300


@pytest.mark.parametrize("playing", [False, True])
@pytest.mark.parametrize("scrubbed", [False, True])
def test_real_model_reorder_preserves_frame_and_refreshes_edit_controls(builder, tmp_path, playing, scrubbed):
    from PyQt6.QtCore import QModelIndex

    add_frames(builder, tmp_path)
    builder._frames[0].delay_ms = 650
    builder._frames[1].delay_ms = 200
    builder._on_selection_changed(0)
    selected = builder._frames[0]
    builder._btn_play.setChecked(playing)
    if scrubbed:
        builder._scrubber.setValue(1)
    preview = builder._frames[builder._preview_idx]
    assert builder._frame_list.model().moveRows(QModelIndex(), 0, 1, QModelIndex(), 2)
    assert builder._frames[1] is selected
    assert builder._frame_list.currentRow() == 1
    assert builder._frames[builder._preview_idx] is preview
    assert builder._scrubber.value() == builder._preview_idx
    assert builder._preview_frame_lbl.text() == f"{builder._preview_idx + 1} / 2"
    assert builder._pf_check.isChecked()
    assert builder._pf_slider.value() == 650
    assert builder._pf_val_lbl.text() == "650 ms"
    assert builder._preview_timer.isActive() == playing
    if playing:
        assert builder._preview_timer.interval() == (200 if scrubbed else 650)
    builder._pf_slider.setValue(850)
    assert selected.delay_ms == 850
    assert builder._frames[0].delay_ms == 200
