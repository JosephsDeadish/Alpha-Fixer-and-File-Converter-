"""Project persistence/recovery without a MainWindow or external media corpus."""
import json
import subprocess
import time
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import Qt, QItemSelectionModel, QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QBoxLayout

from src.core.editor_projects import read_project, validate, write_project, prepare_entries, source_signature
from src.ui.gif_builder import GifBuilderDialog
from src.ui.video_tool import VideoToolDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(app, tmp_path):
    widgets = []
    with patch("src.ui.editor_project.user_data_directory", return_value=tmp_path / "data"):
        def create(kind, **kwargs):
            widget = GifBuilderDialog(**kwargs) if kind == "gif" else VideoToolDialog(**kwargs)
            widgets.append(widget)
            return widget
        yield create
    for widget in widgets:
        wait(widget)
        widget._project._closing_approved = True
        widget._project._release_session()
        widget.close()
        sip.delete(widget)


def wait(widget):
    deadline = time.monotonic() + 15
    while widget._project._job is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert widget._project._job is None
    QApplication.processEvents()


def sources(folder):
    frames = [Image.new("RGBA", (10, 8), color) for color in ("red", "green", "blue")]
    path = folder / "source.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[80, 140, 230])
    still = folder / "still.png"
    frames[0].save(still)
    for frame in frames:
        frame.close()
    return path, still


def save(widget, path):
    with patch("src.ui.editor_project.QFileDialog.getSaveFileName", return_value=(str(path), "")):
        widget._project.save_button.click()
    wait(widget)
    assert path.is_file()


def open_project(widget, path):
    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=(str(path), "")), \
            patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
        widget._project.open_button.click()
        wait(widget)


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
@pytest.mark.parametrize("font_size", [13, 24, 32])
@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_project_header_compact_reflow_and_keyboard(dialogs, tmp_path, kind, size, font_size, theme_name):
    from src.ui.theme_engine import DEFAULT_THEME, LIGHT_THEME, build_stylesheet
    widget = dialogs(kind)
    stylesheet = build_stylesheet(DEFAULT_THEME if theme_name == "dark" else LIGHT_THEME)
    widget.setStyleSheet(stylesheet + f"\nQWidget {{ font-size: {font_size}px; }}")
    _, still = sources(tmp_path)
    if kind == "gif":
        widget._add_paths([str(still)])
    else:
        widget._load_image_paths([str(still)])
    widget.resize(*size)
    widget.show()
    for _ in range(20):
        QApplication.processEvents()
    project = widget._project
    viewport = widget._content_scroll.viewport()
    assert (widget.width(), widget.height()) == size
    assert widget.layout().count() == 1
    assert viewport.height() >= size[1] - 48
    assert widget._export_content.isAncestorOf(project.header)
    required = sum(b.minimumSizeHint().width() for b in
                   (project.save_button, project.open_button, project.recover_button))
    required += 2 * project._row.spacing()
    assert project._row.direction() == (QBoxLayout.Direction.LeftToRight if required <= viewport.width()
                                        else QBoxLayout.Direction.TopToBottom)
    for button in (project.save_button, project.open_button, project.recover_button):
        assert button.font().pixelSize() == font_size
        assert button.width() >= button.minimumSizeHint().width()
        assert button.height() >= button.minimumSizeHint().height()
        assert not button.autoDefault()
    project.status.setText("Project operation failed: " + "missing-original-media/" * 20)
    for _ in range(5):
        QApplication.processEvents()
    assert viewport.height() >= size[1] - 48
    assert project.status.width() <= viewport.width()
    widget._content_scroll.verticalScrollBar().setValue(
        widget._content_scroll.verticalScrollBar().maximum())
    project.save_button.setFocus()
    for _ in range(10):
        QApplication.processEvents()
    position = project.save_button.mapTo(viewport, project.save_button.rect().topLeft())
    assert viewport.rect().contains(position)
    assert position.y() + project.save_button.height() <= viewport.height()
    path = tmp_path / "keyboard-project.json"
    with patch("src.ui.editor_project.QFileDialog.getSaveFileName", return_value=(str(path), "")), \
            patch.object(widget, "_export") as export:
        QTest.keyClick(project.save_button, Qt.Key.Key_Space)
        wait(widget)
    export.assert_not_called()
    assert path.is_file() and not project.dirty
    project.open_button.setFocus()
    for _ in range(10):
        QApplication.processEvents()
    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=(str(path), "")) as picker:
        QTest.keyClick(project.open_button, Qt.Key.Key_Space)
        wait(widget)
    picker.assert_called_once()
    assert not project.dirty


def test_gif_owned_roundtrip_and_real_export_holds(dialogs, tmp_path):
    source, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(source), str(still)])
    widget._frames[3].delay_ms = None
    widget._delay_slider.setValue(350)
    widget._frame_list.setCurrentRow(2)
    widget._scrubber.setValue(1)
    path = tmp_path / "owned.gifproject.json"
    save(widget, path)
    assert not widget._project.dirty
    source.unlink()
    still.unlink()
    loaded = dialogs("gif")
    open_project(loaded, path)
    assert [e.delay_ms for e in loaded._frames] == [80, 140, 230, None]
    assert [e.source_delay_ms for e in loaded._frames[:3]] == [80, 140, 230]
    assert loaded._frame_list.currentRow() == 2
    assert loaded._preview_idx == 1
    assert loaded._delay_slider.value() == 350
    output = tmp_path / "export.gif"
    from tests.gif_export_helpers import wait_for_gif_export
    with patch("src.ui.gif_builder.QFileDialog.getSaveFileName", return_value=(str(output), "")), \
            patch("src.ui.gif_builder.QMessageBox.information"):
        loaded._export()
        wait_for_gif_export(loaded)
    with Image.open(output) as image:
        delays = []
        for index in range(image.n_frames):
            image.seek(index)
            delays.append(image.info["duration"])
    assert delays == [80, 140, 230, 350]


def test_video_gif_split_still_settings_roundtrip(dialogs, tmp_path):
    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source), str(still)])
    widget._clip_list.setCurrentRow(0)
    widget._clip_speed_slider.setValue(70)
    widget._scrubber.setValue(1)
    widget._split_clip_at_playhead()
    widget._clip_list.setCurrentRow(2)
    widget._still_duration_spin.setValue(91)
    widget._brightness_slider.setValue(165)
    widget._filter_combo.setCurrentIndex(widget._filter_combo.findData("sepia"))
    widget._audio_volume_slider.setValue(72)
    path = tmp_path / "timeline.videoproject.json"
    save(widget, path)
    document = read_project(path)
    assert document["entries"][0]["source"] == source.name
    assert len(document["entries"][0]["split"]) == 8
    loaded = dialogs("video")
    open_project(loaded, path)
    assert [c._split_timing for c in loaded._clips] == [c._split_timing for c in widget._clips]
    assert [c._split_audio_range() for c in loaded._clips] == [c._split_audio_range() for c in widget._clips]
    assert loaded._clips[2].still_duration_frames == 91
    assert loaded._brightness_slider.value() == 165
    assert loaded._filter_combo.currentData() == "sepia"
    assert loaded._audio_volume_slider.value() == 72


def test_video_clock_timecode_project_roundtrip_and_recovery(dialogs, tmp_path):
    from src.ui.clock_timecode import format_clock_timecode

    def enter_clock(entry, text):
        entry.setFocus()
        entry.selectAll()
        QTest.keyClicks(entry, text)
        QTest.keyClick(entry, Qt.Key.Key_Return)

    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget.show()
    QApplication.processEvents()
    widget._project.timer.stop()
    widget._load_image_paths([str(source)])
    clip = widget._clips[0]
    path = tmp_path / "clock.videoproject.json"
    save(widget, path)
    count = len(widget._undo_stack)
    enter_clock(widget._trim_start_timecode, format_clock_timecode(1, clip.fps))
    assert clip.trim_start == 1 and widget._project.dirty
    assert len(widget._undo_stack) == count + 1
    widget._undo()
    assert clip.trim_start == 0 and not widget._project.dirty
    widget._redo()
    assert clip.trim_start == 1 and widget._project.dirty
    widget._clip_speed_slider.setValue(50)
    widget._fps_slider.setValue(60)
    enter_clock(widget._playhead_timecode, "00:00:00.050")
    assert widget._scrubber.value() == 3
    save(widget, path)
    loaded = dialogs("video")
    open_project(loaded, path)
    assert loaded._clips[0].trim_start == 1
    assert loaded._clips[0].trim_end == 2
    assert loaded._trim_start_timecode.text() == format_clock_timecode(1, clip.fps)
    assert loaded._playhead_timecode.text() == "00:00:00.050"
    assert not loaded._project.dirty
    widget._project.timer.stop()
    enter_clock(widget._trim_start_timecode, "00:00:00.000")
    widget._project.autosave()
    wait(widget)
    recovery_path = widget._project.recovery_path
    widget._project._release_session()
    recovered = dialogs("video")
    assert recovered._project.load(recovery_path, recovery=True)
    wait(recovered)
    assert recovered._clips[0].trim_start == 0
    assert recovered._trim_start_timecode.text() == "00:00:00.000"
    assert recovered._playhead_timecode.text() == "00:00:00.050"
    assert recovered._project.dirty and recovered._project.path == ""


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=2),
    lambda d: d.update(version=True),
    lambda d: d.update(unexpected="exec"),
    lambda d: d["controls"].update(_delay_slider=True),
    lambda d: d.update(playhead=999),
    lambda d: d["entries"][0].update(size=[999999, 999999]),
    lambda d: d["entries"][0].update(delay=-1),
])
def test_invalid_schema_rejected(dialogs, tmp_path, mutation):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    document, images = widget._project._document(tmp_path / "p.json")
    for image in images:
        image.close()
    mutation(document)
    with pytest.raises(ValueError):
        validate(document)


def test_corrupt_load_preserves_live_session(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    path = tmp_path / "good.json"
    save(widget, path)
    document = read_project(path)
    document["entries"][0]["png"] = "not-base64!"
    path.write_text(json.dumps(document))
    original = widget._frames[0]
    with patch("src.ui.editor_project.QMessageBox.critical") as error:
        open_project(widget, path)
    error.assert_called_once()
    assert widget._frames[0] is original
    assert original._pil.getpixel((0, 0)) == (255, 0, 0, 255)


def test_missing_video_media_preserves_live_session(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = widget._clips[0]
    still.unlink()
    with patch("src.ui.editor_project.QMessageBox.critical") as error, \
            patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=("", "")) as picker:
        assert widget._project.load(path)
        wait(widget)
    error.assert_not_called()
    picker.assert_called_once()
    assert str(still) in picker.call_args.args[1]
    assert "cancelled" in widget._project.status.text()
    assert widget._clips[0] is original
    assert original.get_frame(0).getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("fps", [float("nan"), float("inf"), -float("inf")])
def test_prepare_rejects_nonfinite_decoded_fps_and_releases_media(dialogs, tmp_path, fps):
    from src.ui import video_tool
    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source), str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    prepared = []
    loader = video_tool._load_image_as_clip

    def load(source_path):
        clip = loader(source_path)
        if source_path == str(still):
            clip.fps = fps
        clip.close = Mock(wraps=clip.close)
        prepared.append(clip)
        return clip

    with patch.object(video_tool, "_load_image_as_clip", side_effect=load):
        with pytest.raises(ValueError, match="source FPS"):
            prepare_entries(read_project(path), path)
    assert len(prepared) == 2
    for clip in prepared:
        clip.close.assert_called_once()


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_project_fps_rejected_before_decode(dialogs, tmp_path, token):
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    document = read_project(path)
    document["entries"][0]["fps"] = "nonfinite-fps"
    path.write_text(json.dumps(document).replace('"nonfinite-fps"', token))
    widget._brightness_slider.setValue(177)
    project = widget._project
    original = tuple(project.entries)
    fingerprint = project.fingerprint()
    state = project.path, project._saved, project._autosaved
    with pytest.raises(ValueError, match="Non-finite number|source FPS"):
        read_project(path)
    with patch("src.ui.editor_project.prepare_entries") as prepare, \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        assert project.load(path)
        wait(widget)
    error.assert_called_once()
    prepare.assert_not_called()
    assert tuple(project.entries) == original and project.fingerprint() == fingerprint
    assert (project.path, project._saved, project._autosaved) == state and project.dirty
    with original[0].get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("recovery", [False, True])
def test_nonfinite_decoded_fps_keeps_unsaved_video_session(dialogs, tmp_path, recovery):
    from src.ui import video_tool
    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source), str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    project = widget._project
    widget._clip_list.setCurrentRow(0)
    widget._clip_speed_slider.setValue(70)
    widget._brightness_slider.setValue(177)
    widget._scrubber.setValue(1)
    widget._btn_play.setChecked(True)
    original = tuple(project.entries)
    fingerprint = project.fingerprint()
    state = project.path, project._saved, project._autosaved
    history = list(widget._undo_stack), list(widget._redo_stack)
    before = path.read_bytes()
    loader = video_tool._load_image_as_clip

    def load(source_path):
        clip = loader(source_path)
        clip.fps = float("nan")
        return clip

    with patch.object(video_tool, "_load_image_as_clip", side_effect=load), \
            patch("src.ui.editor_project.QMessageBox.critical") as error, \
            patch("src.ui.editor_project.QMessageBox.question",
                  return_value=QMessageBox.StandardButton.Discard) as replace:
        assert project.load(path, recovery=recovery)
        wait(widget)
    error.assert_called_once()
    replace.assert_not_called()
    assert tuple(project.entries) == original and project.fingerprint() == fingerprint
    assert (project.path, project._saved, project._autosaved) == state and project.dirty
    assert (widget._undo_stack, widget._redo_stack) == history
    assert widget._clip_list.currentRow() == 0 and widget._scrubber.value() == 1
    assert widget._btn_play.isChecked() and widget._preview_timer.isActive()
    assert widget._content_scroll.isEnabled() and not project._loading
    assert path.read_bytes() == before
    for clip in original:
        with clip.get_frame(0) as frame:
            assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


def test_missing_media_relink_repeated_entries_save_and_export(dialogs, tmp_path):
    import threading
    from src.ui import video_tool
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still), str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    before = path.read_bytes()
    original_hash = read_project(path)["entries"][0]["source_sha256"]
    moved = tmp_path / "moved.png"
    still.rename(moved)
    threads = []

    def signature(source):
        threads.append(threading.get_ident())
        return source_signature(source)

    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=(str(moved), "")) as picker, \
            patch("src.core.editor_projects.source_signature", side_effect=signature) as hashing:
        widget._project.load(path)
        wait(widget)
    picker.assert_called_once()
    hashing.assert_called_once()
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    assert all(c.source_path == str(moved) and c._source_sha256 == original_hash for c in widget._clips)
    assert widget._project.dirty
    assert path.read_bytes() == before
    assert "Save Project" in widget._project.status.text()
    output = tmp_path / "export.gif"
    widget._still_duration_spin.setValue(2)
    with patch.object(video_tool.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
            patch.object(video_tool.QMessageBox, "information"):
        widget._export()
        deadline = time.monotonic() + 15
        while widget.is_exporting() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)
        assert not widget.is_exporting()
    with Image.open(output) as image:
        assert image.getpixel((0, 0)) is not None
    destination = tmp_path / "saved" / "relinked.json"
    destination.parent.mkdir()
    save(widget, destination)
    assert not widget._project.dirty
    document = read_project(destination)
    assert all(e["source"] == "../moved.png" and e["source_sha256"] == original_hash
               for e in document["entries"])
    assert path.read_bytes() == before
    loaded = dialogs("video")
    open_project(loaded, destination)
    assert all(c.source_path == str(moved) for c in loaded._clips)
    assert not loaded._project.dirty


@pytest.mark.parametrize("cancel_after_first", [False, True])
def test_relink_failure_or_later_cancel_keeps_all_live_state(dialogs, tmp_path, cancel_after_first):
    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source), str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    before = path.read_bytes()
    source.rename(tmp_path / "moved.gif")
    still.rename(tmp_path / "moved.png")
    widget._brightness_slider.setValue(177)
    clips = list(widget._clips)
    fingerprint = widget._project.fingerprint()
    saved, autosaved = widget._project._saved, widget._project._autosaved
    choices = [(str(tmp_path / "moved.gif"), ""), ("", "")] if cancel_after_first else [
        (str(tmp_path / "moved.png"), ""), (str(tmp_path / "moved.png"), "")]
    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", side_effect=choices) as picker, \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        widget._project.load(path)
        wait(widget)
    assert picker.call_count == 2
    assert widget._clips == clips
    assert widget._project.fingerprint() == fingerprint
    assert (widget._project._saved, widget._project._autosaved) == (saved, autosaved)
    assert widget._project.path == str(path) and widget._project.dirty
    assert path.read_bytes() == before
    assert widget._project.open_button.isEnabled() and widget._content_scroll.isEnabled()
    if cancel_after_first:
        error.assert_not_called()
    else:
        error.assert_called_once()
        message = error.call_args.args[2]
        assert str(path) in message and str(tmp_path / "moved.png") in message
        assert "changed since project save" in message and "original, unchanged" in message


def test_relink_recovery_stays_dirty_without_rewriting_snapshot(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    assert widget._project.autosave()
    wait(widget)
    path = widget._project.recovery_path
    before = path.read_bytes()
    widget._project._release_session()
    moved = tmp_path / "moved.png"
    still.rename(moved)
    recovered = dialogs("video")
    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=(str(moved), "")):
        assert recovered._project.load(path, recovery=True)
        wait(recovered)
    assert recovered._clips[0].source_path == str(moved)
    assert recovered._project.dirty and recovered._project.path == ""
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_recovery_locked_after_discovery_keeps_current_session(dialogs, tmp_path, kind):
    from PyQt6.QtCore import QLockFile
    _, still = sources(tmp_path)
    writer, reader = dialogs(kind), dialogs(kind)
    for widget in (writer, reader):
        if kind == "gif":
            widget._add_paths([str(still)])
        else:
            widget._load_image_paths([str(still)])
        assert widget._project.autosave()
        wait(widget)
    writer._project._release_session()
    if kind == "gif":
        reader._delay_slider.setValue(420)
    else:
        reader._brightness_slider.setValue(150)
    project = reader._project
    entries = tuple(project.entries)
    state = project.path, project._saved, project._autosaved, project._lock
    snapshot = writer._project.recovery_path
    before = snapshot.read_bytes()
    lock = QLockFile(str(snapshot) + ".lock")
    lock.setStaleLockTime(0)

    def choose(_parent, _title, _prompt, labels, *_args):
        assert lock.tryLock(0)
        return labels[0], True

    try:
        with patch("src.ui.editor_project.QInputDialog.getItem", side_effect=choose), \
                patch("src.ui.editor_project.QMessageBox.critical") as error, \
                patch("src.ui.editor_project.QMessageBox.question") as question:
            project.recover_project()
            wait(reader)
        error.assert_called_once()
        question.assert_not_called()
        assert "active" in project.status.text().lower()
        assert tuple(project.entries) == entries and project.dirty
        assert (project.path, project._saved, project._autosaved, project._lock) == state
        assert project._lock.isLocked() and lock.isLocked()
        assert snapshot.read_bytes() == before
        assert not project._loading and reader._content_scroll.isEnabled()
    finally:
        lock.unlock()
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
        assert project.load(snapshot, recovery=True)
        wait(reader)
    assert project.dirty and project.path == ""
    assert snapshot.read_bytes() == before


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("accepted", [False, True])
def test_recovery_picker_keeps_candidate_snapshot_and_blocks_reentry(dialogs, tmp_path, kind, accepted):
    _, still = sources(tmp_path)
    writer, reader = dialogs(kind), dialogs(kind)
    for widget in (writer, reader):
        if kind == "gif":
            widget._add_paths([str(still)])
        else:
            widget._load_image_paths([str(still)])
        assert widget._project.autosave()
        wait(widget)
    writer._project._release_session()
    project = reader._project
    entries = tuple(project.entries)
    snapshot = writer._project.recovery_path
    before = snapshot.read_bytes()
    resumes = []

    def choose(_parent, _title, _prompt, labels, *_args):
        assert not project.autosave()
        assert not project.load(snapshot)
        assert not project.allow_parent_close(lambda: resumes.append(True))
        project._recoveries = []
        return labels[0], accepted

    with patch("src.ui.editor_project.QInputDialog.getItem", side_effect=choose), \
            patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard), \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        project.recover_project()
        wait(reader)
    error.assert_not_called()
    assert not resumes and not project._loading
    assert reader._content_scroll.isEnabled() and project._lock.isLocked()
    assert snapshot.read_bytes() == before
    if accepted:
        assert project.dirty and project.path == ""
        assert tuple(project.entries) != entries
    else:
        assert tuple(project.entries) == entries


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("malformed", [False, True])
def test_recovery_read_claims_lock_and_releases_on_failure(dialogs, tmp_path, kind, malformed):
    from PyQt6.QtCore import QLockFile, QThread
    _, still = sources(tmp_path)
    writer, reader = dialogs(kind), dialogs(kind)
    if kind == "gif":
        writer._add_paths([str(still)])
    else:
        writer._load_image_paths([str(still)])
    assert writer._project.autosave()
    wait(writer)
    snapshot = writer._project.recovery_path
    writer._project._release_session()
    if malformed:
        snapshot.write_text('{"bad":', encoding="utf-8")
    before = snapshot.read_bytes()
    probe = QLockFile(str(snapshot) + ".lock")
    probe.setStaleLockTime(0)

    def read(path, project_kind):
        assert QThread.currentThread() != reader.thread()
        assert not probe.tryLock(0)
        return read_project(path, project_kind)

    with patch("src.ui.editor_project.read_project", side_effect=read) as worker_read, \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        assert reader._project.load(snapshot, recovery=True)
        wait(reader)
    worker_read.assert_called_once()
    assert error.call_count == int(malformed)
    assert len(reader._project.entries) == (0 if malformed else 1)
    assert not reader._project._loading and reader._content_scroll.isEnabled()
    assert snapshot.read_bytes() == before
    assert probe.tryLock(0)
    probe.unlock()


def test_relink_same_shape_wrong_identity_rejected_before_decode(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    before = path.read_bytes()
    original = widget._clips[0]
    still.unlink()
    wrong = tmp_path / "wrong.png"
    with Image.new("RGBA", (10, 8), "blue") as image:
        image.save(wrong)
    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=(str(wrong), "")), \
            patch("src.ui.video_tool._load_image_as_clip") as decode, \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        widget._project.load(path)
        wait(widget)
    decode.assert_not_called()
    error.assert_called_once()
    assert "changed since project save" in error.call_args.args[2]
    assert widget._clips == [original] and path.read_bytes() == before
    assert not widget._project.dirty


def test_relink_media_changed_during_decode_releases_clip(dialogs, tmp_path):
    from src.ui import video_tool
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = widget._clips[0]
    moved = tmp_path / "moved.png"
    still.rename(moved)
    decoded = []
    loader = video_tool._load_image_as_clip

    def load(source):
        clip = loader(source)
        clip.close = Mock(wraps=clip.close)
        decoded.append(clip)
        with Image.new("RGBA", (10, 8), "blue") as image:
            image.save(moved)
        return clip

    with patch("src.ui.editor_project.QFileDialog.getOpenFileName", return_value=(str(moved), "")), \
            patch.object(video_tool, "_load_image_as_clip", side_effect=load), \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        widget._project.load(path)
        wait(widget)
    error.assert_called_once()
    assert "changed during project I/O" in error.call_args.args[2]
    assert widget._clips == [original]
    assert len(decoded) == 1
    decoded[0].close.assert_called_once()


def test_atomic_save_failure_preserves_file_and_dirty_state(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = path.read_bytes()
    widget._delay_slider.setValue(777)
    entry = widget._frames[0]
    with patch("os.replace", side_effect=OSError("disk disconnected")), \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        widget._project._save(path)
        wait(widget)
    error.assert_called_once()
    assert path.read_bytes() == original
    assert widget._project.dirty
    assert widget._frames[0] is entry
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


def test_recovery_multi_instance_and_failed_autosave(dialogs, tmp_path):
    _, still = sources(tmp_path)
    first, second = dialogs("gif"), dialogs("gif")
    for widget in (first, second):
        assert widget._project.recover_button.shortcut() == QKeySequence("Alt+R")
        assert widget._project.recover_button.accessibleName() == "Recover… (0)"
        widget._add_paths([str(still)])
        widget._project.autosave()
        wait(widget)
    one, two = first._project.recovery_path, second._project.recovery_path
    assert one != two and one.is_file() and two.is_file()
    first._project.refresh_recoveries()
    assert two not in first._project._recoveries
    before = one.read_bytes()
    first._delay_slider.setValue(400)
    with patch("os.replace", side_effect=OSError("full disk")), \
            patch("src.ui.editor_project.QMessageBox.critical") as modal:
        first._project.autosave()
        wait(first)
    modal.assert_not_called()
    assert one.read_bytes() == before
    assert "full disk" in first._project.status.text()
    first._project._release_session()
    second._project.refresh_recoveries()
    assert one in second._project._recoveries
    assert second._project.recover_button.isEnabled()
    assert second._project.recover_button.shortcut() == QKeySequence("Alt+R")
    assert second._project.recover_button.accessibleName() == "Recover… (1)"
    with patch("src.ui.editor_project.QInputDialog.getItem",
               side_effect=lambda parent, title, prompt, labels, *args: (labels[0], True)), \
            patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
        second._project.recover_button.click()
        wait(second)
    assert second._project.dirty
    assert one.is_file()
    assert len(second._frames) == 1


@pytest.mark.parametrize("action", ["poll", "autosave", "save", "load"])
def test_gif_import_event_loop_blocks_partial_project_io(dialogs, tmp_path, action):
    from src.ui import gif_builder

    source, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    project = widget._project
    project.timer.stop()
    path = tmp_path / "complete.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    before = path.read_bytes(), project.recovery_path.read_bytes()
    widget._delay_slider.setValue(777)
    project._last_autosave -= 31
    loader = gif_builder._load_pillow_rgba
    observed = []

    def attempt_project_io():
        result = (project.poll() if action == "poll" else
                  project.autosave() if action == "autosave" else
                  project._save(path) if action == "save" else project.load(path))
        observed.append((widget._importing, len(widget._frames), result, project._job is None))

    def load_with_nested_events(media_path):
        if media_path == str(source):
            # Import progress dialogs pump timers after earlier files were appended.
            QTimer.singleShot(0, attempt_project_io)
            QApplication.processEvents()
        return loader(media_path)

    with patch.object(gif_builder, "_load_pillow_rgba", side_effect=load_with_nested_events), \
            patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard), \
            patch("src.ui.editor_project.QMessageBox.critical"):
        widget._add_paths([str(still), str(source)])
        wait(widget)
    assert observed == [(True, 2, None if action == "poll" else False, True)]
    assert (path.read_bytes(), project.recovery_path.read_bytes()) == before
    assert len(widget._frames) == 5 and widget._delay_slider.value() == 777
    assert project.dirty and not widget._importing
    assert project.autosave()
    wait(widget)
    document = read_project(project.recovery_path)
    assert len(document["entries"]) == 5
    assert document["controls"]["_delay_slider"] == 777
    assert [entry["delay"] for entry in document["entries"][-3:]] == [80, 140, 230]


def test_debounce_no_slider_tick_writes(dialogs):
    widget = dialogs("gif")
    with patch.object(widget._project, "autosave") as autosave:
        for value in range(100, 200):
            widget._delay_slider.setValue(value)
            widget._project.poll()
        autosave.assert_not_called()
        widget._project._changed_at -= 6
        widget._project.poll()
        autosave.assert_called_once()


def test_close_cancel_save_and_export_guard(dialogs, tmp_path):
    widget = dialogs("gif")
    with patch("src.ui.editor_project.QMessageBox.question") as question:
        assert widget._project.allow_close(lambda: None)
        question.assert_not_called()
    widget._project._closing_approved = False
    widget._delay_slider.setValue(300)
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        assert not widget._project.allow_close(lambda: None)
    resumed = []
    path = tmp_path / "close.json"
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Save), \
            patch("src.ui.editor_project.QFileDialog.getSaveFileName", return_value=(str(path), "")):
        assert not widget._project.allow_close(lambda: resumed.append(True))
        wait(widget)
    assert resumed == [True] and path.is_file()
    with patch.object(widget, "is_exporting", return_value=True), \
            patch("src.ui.editor_project.QFileDialog.getOpenFileName") as picker:
        widget._project.open_project()
        picker.assert_not_called()


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("provisional", [False, True])
@pytest.mark.parametrize("answer", ["Cancel", "Discard", "Save", "SaveCancelled"])
def test_close_prompt_blocks_project_io_and_failed_save_keeps_recovery(
        dialogs, tmp_path, kind, provisional, answer):
    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "project.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    disk = path.read_bytes(), project.recovery_path.read_bytes()
    saved, autosaved, lock = project._saved, project._autosaved, project._lock
    entries = tuple(project.entries)
    control.setValue(control.value() + 10)
    project.poll()
    project._changed_at -= 6
    resumed = Mock()

    def assert_modal_guard():
        project.poll()
        assert project._job is None
        assert project._loading
        assert not project.autosave()
        assert not project._save(tmp_path / "unexpected.json")
        assert not project.allow_close(resumed)
        assert project._lock is lock and lock.isLocked()
        assert project.timer.isActive()
        assert tuple(project.entries) == entries

    def question(*args):
        assert_modal_guard()
        return getattr(QMessageBox.StandardButton, "Save" if answer == "SaveCancelled" else answer)

    def picker(*args):
        assert_modal_guard()
        return ("" if answer == "SaveCancelled" else str(path)), ""

    with patch("src.ui.editor_project.QMessageBox.question", side_effect=question), \
            patch("src.ui.editor_project.QFileDialog.getSaveFileName", side_effect=picker), \
            patch("os.replace", side_effect=OSError("destination unavailable")), \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        assert project.allow_close(resumed, provisional=provisional) is (answer == "Discard")
        wait(widget)
    resumed.assert_not_called()
    assert error.call_count == (1 if answer == "Save" else 0)
    assert not project._loading and project._job is None
    assert project._pending_close is None
    assert project.dirty and project.path == str(path)
    assert (project._saved, project._autosaved) == (saved, autosaved)
    assert (path.read_bytes(), project.recovery_path.read_bytes()) == disk
    assert tuple(project.entries) == entries
    assert not (tmp_path / "unexpected.json").exists()
    assert not list(tmp_path.rglob(".alpha_fixer_save_*"))
    if answer == "Discard" and not provisional:
        assert project._closing_approved and not project.timer.isActive()
        assert project._lock is None and not lock.isLocked()
    else:
        assert project._closing_approved is (answer == "Discard")
        assert project.timer.isActive() and project._lock is lock and lock.isLocked()
        if answer != "Discard":
            project._retry_after = 0
            project.poll()
            wait(widget)
            assert project._autosaved == project.fingerprint()
            assert project.dirty and project._lock is lock and lock.isLocked()


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("action", ["open", "save"])
@pytest.mark.parametrize("accepted", [False, True])
def test_project_file_picker_blocks_autosave_and_close(dialogs, tmp_path, kind, action, accepted):
    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "project.json"
    save(widget, path)
    control.setValue(control.value() + 10)
    project.poll()
    project._changed_at -= 6
    saved = project._saved
    entries = tuple(project.entries)

    def picker(*args):
        project.poll()
        assert project._job is None and project._loading
        assert not project.autosave()
        assert not project.load(path)
        assert not project.allow_parent_close(lambda: None)
        return (str(path) if accepted else ""), ""

    method = "getOpenFileName" if action == "open" else "getSaveFileName"
    with patch(f"src.ui.editor_project.QFileDialog.{method}", side_effect=picker) as choose, \
            patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
        getattr(project, f"{action}_project")()
        wait(widget)
    choose.assert_called_once()
    assert not project._loading and project._job is None
    assert project.timer.isActive() and not project._closing_approved
    assert project.path == str(path) and project.dirty is not accepted
    if not accepted:
        assert tuple(project.entries) == entries and project._saved == saved


def test_replacement_cancel_keeps_edits_and_prepared_resources_clean(dialogs, tmp_path):
    _, still = sources(tmp_path)
    widget = dialogs("gif")
    widget._add_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    widget._delay_slider.setValue(250)
    original = widget._frames[0]
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        widget._project.load(path)
        wait(widget)
    assert widget._frames[0] is original and widget._delay_slider.value() == 250


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("accepted", [False, True])
def test_replace_prompt_blocks_reentrant_project_io(dialogs, tmp_path, kind, accepted):
    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "p.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    recovery_before = project.recovery_path.read_bytes()
    lock = project._lock
    old_entries = tuple(project.entries)
    control.setValue(control.value() + 10)
    saved, autosaved = project._saved, project._autosaved
    attempts = []

    def prompt(*args):
        # A modal question pumps timers and can dispatch project actions.
        assert project._loading and widget._editing_busy()
        widget._update_history_controls()
        assert not widget._btn_undo.isEnabled()
        if kind == "video":
            assert not widget._btn_clear.isEnabled()
        attempts.append(project.autosave())
        wait(widget)
        attempts.append(project._save(tmp_path / "unexpected.json"))
        wait(widget)
        return (QMessageBox.StandardButton.Discard if accepted
                else QMessageBox.StandardButton.Cancel)

    with patch("src.ui.editor_project.QMessageBox.question", side_effect=prompt):
        assert project.load(path)
        wait(widget)
    assert attempts == [False, False]
    assert not (tmp_path / "unexpected.json").exists()
    assert project.recovery_path.read_bytes() == recovery_before
    assert project._lock is lock and lock.isLocked()
    assert project.path == str(path)
    assert not project._loading
    assert not widget._editing_busy()
    assert project.entries
    if kind == "video":
        assert widget._btn_clear.isEnabled()
    assert widget._btn_undo.isEnabled() == bool(widget._undo_stack)
    assert project.dirty is not accepted
    if not accepted:
        assert tuple(project.entries) == old_entries
        assert (project._saved, project._autosaved) == (saved, autosaved)
    else:
        assert all(new is not old for new, old in zip(project.entries, old_entries))


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_failed_install_restores_history_controls(dialogs, tmp_path, kind):
    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
    else:
        widget._load_image_paths([str(still)])
    project = widget._project
    path = tmp_path / "p.json"
    save(widget, path)
    entries = tuple(project.entries)
    assert widget._btn_undo.isEnabled()

    def failed_install(document, prepared):
        assert project._loading and widget._editing_busy()
        widget._update_history_controls()
        assert not widget._btn_undo.isEnabled()
        for entry in prepared:
            entry.close()
        raise ValueError("installation failed")

    with patch.object(project, "_install", side_effect=failed_install), \
            patch("src.ui.editor_project.QMessageBox.critical") as error:
        assert project.load(path)
        wait(widget)
    error.assert_called_once()
    assert tuple(project.entries) == entries
    assert not project._loading and not widget._editing_busy()
    assert widget._btn_undo.isEnabled()
    if kind == "video":
        assert widget._btn_clear.isEnabled()


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_replace_prompt_late_edit_keeps_live_session(dialogs, tmp_path, kind):
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "p.json"
    save(widget, path)
    old_entries = tuple(project.entries)
    control.setValue(control.value() + 10)
    prepared = []
    prepare = editor_project.prepare_entries

    def tracked_prepare(*args):
        entries = prepare(*args)
        for entry in entries:
            entry.close = Mock(wraps=entry.close)
        prepared.extend(entries)
        return entries

    def prompt(*args):
        control.setValue(control.value() + 10)
        return QMessageBox.StandardButton.Discard

    with patch.object(editor_project, "prepare_entries", side_effect=tracked_prepare), \
            patch.object(editor_project.QMessageBox, "question", side_effect=prompt):
        assert project.load(path)
        wait(widget)
    assert tuple(project.entries) == old_entries
    assert project.dirty and project.path == str(path)
    assert prepared
    for entry in prepared:
        entry.close.assert_called_once()


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("failure", ["ENOSPC", "EACCES", "EIO"])
def test_storage_failure_preserves_project_recovery_and_retries(dialogs, tmp_path, kind, automatic, failure):
    import errno
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "p.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    before = path.read_bytes(), project.recovery_path.read_bytes()
    state = project._saved, project._autosaved, project._lock
    old_entries = tuple(project.entries)
    control.setValue(control.value() + 10)
    destination = project.recovery_path if automatic else path
    target = {"ENOSPC": "os.fsync", "EACCES": "tempfile.NamedTemporaryFile",
              "EIO": "os.replace"}[failure]
    error = OSError(getattr(errno, failure), failure)
    with patch(target, side_effect=error), patch.object(editor_project.QMessageBox, "critical") as modal:
        assert project._save(destination, automatic=automatic)
        wait(widget)
    assert modal.call_count == (0 if automatic else 1)
    assert failure in project.status.text()
    assert project.path == str(path) and project.dirty
    assert tuple(project.entries) == old_entries
    assert (project._saved, project._autosaved, project._lock) == state
    assert project._lock.isLocked() and project.timer.isActive()
    assert (path.read_bytes(), project.recovery_path.read_bytes()) == before
    assert not list(tmp_path.rglob(".alpha_fixer_save_*"))
    assert project._save(destination, automatic=automatic)
    wait(widget)
    assert project.dirty is automatic
    assert destination.read_bytes() != before[1 if automatic else 0]
    assert project._lock is state[2] and project._lock.isLocked()


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("automatic", [False, True])
def test_destination_directory_disappears_before_commit_and_retries(dialogs, tmp_path, kind, automatic):
    import os
    from src.ui import editor_project

    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    folder = tmp_path / "projects"
    folder.mkdir()
    path = folder / "saved.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    before = path.read_bytes(), project.recovery_path.read_bytes()
    state = project.path, project._saved, project._autosaved, project._lock
    control.setValue(control.value() + 10)
    captured = project.fingerprint()
    entries = tuple(project.entries)
    destination = project.recovery_path if automatic else path
    displaced = destination.parent.with_name(destination.parent.name + "-disconnected")
    replace = os.replace

    def disconnect(staged, target):
        destination.parent.rename(displaced)
        return replace(staged, target)

    try:
        with patch("os.replace", side_effect=disconnect), \
                patch.object(editor_project.QMessageBox, "critical") as modal:
            assert project._save(destination, automatic=automatic)
            wait(widget)
        assert modal.call_count == (0 if automatic else 1)
        assert not destination.parent.exists()
        assert (displaced / destination.name).read_bytes() == before[1 if automatic else 0]
        assert project.dirty and project.fingerprint() == captured
        assert tuple(project.entries) == entries
        assert (project.path, project._saved, project._autosaved, project._lock) == state
        assert project.timer.isActive() and project._lock.isLocked()
        assert not project._closing_approved and project._pending_close is None
    finally:
        if displaced.exists():
            displaced.rename(destination.parent)

    assert (path.read_bytes(), project.recovery_path.read_bytes()) == before
    # Cleanup cannot reach a staged file while its directory is disconnected.
    # Remove only this test's orphan once the directory becomes available again.
    for orphan in destination.parent.glob(".alpha_fixer_save_*"):
        orphan.unlink()
    assert project._save(destination, automatic=automatic)
    wait(widget)
    assert project.dirty is automatic
    assert (project._autosaved if automatic else project._saved) == captured
    assert project.path == str(path) and project._lock is state[3]
    assert project._lock.isLocked() and project.timer.isActive()
    assert not list(tmp_path.rglob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("failure", ["EACCES", "EIO", "ENOENT"])
def test_project_read_storage_failure_preserves_live_session_and_retries(
        dialogs, tmp_path, kind, recovery, failure):
    import errno
    from PyQt6.QtCore import QLockFile
    from src.ui import editor_project

    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "saved.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(path.read_bytes())
    before = path.read_bytes(), project.recovery_path.read_bytes(), candidate.read_bytes()
    control.setValue(control.value() + 10)
    fingerprint = project.fingerprint()
    state = project.path, project._saved, project._autosaved, project._lock
    entries = tuple(project.entries)
    history = tuple(widget._undo_stack), tuple(widget._redo_stack)
    error = OSError(getattr(errno, failure), failure)
    with patch("src.core.editor_projects.open", create=True, side_effect=error), \
            patch.object(editor_project.QMessageBox, "critical") as modal:
        assert project.load(candidate, recovery=recovery)
        wait(widget)
    modal.assert_called_once()
    assert failure in project.status.text()
    assert project.fingerprint() == fingerprint and project.dirty
    assert tuple(project.entries) == entries
    assert (tuple(widget._undo_stack), tuple(widget._redo_stack)) == history
    assert (project.path, project._saved, project._autosaved, project._lock) == state
    assert project._lock.isLocked() and project.timer.isActive()
    assert widget._content_scroll.isEnabled() and not project._loading
    assert (path.read_bytes(), project.recovery_path.read_bytes(), candidate.read_bytes()) == before
    probe = QLockFile(str(candidate) + ".lock")
    probe.setStaleLockTime(0)
    try:
        assert probe.tryLock(0)
    finally:
        probe.unlock()
    with patch.object(editor_project.QMessageBox, "question",
                      return_value=QMessageBox.StandardButton.Discard):
        assert project.load(candidate, recovery=recovery)
        wait(widget)
    assert project.dirty is recovery
    assert project.path == ("" if recovery else str(candidate))
    assert project._lock is state[3] and project._lock.isLocked()
    assert candidate.read_bytes() == before[2]


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("automatic", [False, True])
def test_save_worker_owns_snapshot_and_late_edits_cancel_pending_close(dialogs, tmp_path, kind, automatic):
    from threading import Event, get_ident
    from src.core.editor_projects import decode_png
    from src.ui import editor_project

    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "saved.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    state = project._saved, project._autosaved, project._lock
    control.setValue(control.value() + 10)
    captured = project.fingerprint()
    controls = project.controls()
    original = project.entries[0]
    destination = project.recovery_path if automatic else path
    entered, release = Event(), Event()
    gui_thread = get_ident()
    snapshots = []
    function = "encode_png" if kind == "gif" else "source_signature"
    operation = getattr(editor_project, function)
    resumed = Mock()

    def blocked(value):
        assert get_ident() != gui_thread
        if kind == "gif":
            assert value is not original._pil
            snapshots.append(value)
        entered.set()
        assert release.wait(5)
        return operation(value)

    with patch.object(editor_project, function, side_effect=blocked), \
            patch.object(editor_project.QMessageBox, "question",
                         return_value=QMessageBox.StandardButton.Save), \
            patch.object(editor_project.QFileDialog, "getSaveFileName",
                         return_value=(str(path), "")):
        try:
            if automatic:
                assert project.autosave()
            else:
                assert not project.allow_close(resumed)
                assert project._pending_close is resumed
            assert entered.wait(5)
            control.setValue(control.value() + 10)
            widget._clear_all()
            assert not project.entries
            assert project.fingerprint() != captured
            assert not project.autosave()
            assert not project._save(tmp_path / "unexpected.json")
        finally:
            release.set()
            wait(widget)

    document = read_project(destination)
    assert document["controls"] == controls and len(document["entries"]) == 1
    if kind == "gif":
        with decode_png(document["entries"][0]) as image:
            assert image.getpixel((0, 0)) == (255, 0, 0, 255)
        assert snapshots
        for image in snapshots:
            with pytest.raises(ValueError, match="closed"):
                image.getpixel((0, 0))
    else:
        assert document["entries"][0]["source_sha256"] == source_signature(still)
    resumed.assert_not_called()
    assert project._pending_close is None and not project._closing_approved
    assert project.dirty and project.timer.isActive()
    assert project._lock is state[2] and project._lock.isLocked()
    assert (project._autosaved if automatic else project._saved) == captured
    assert (project._saved if automatic else project._autosaved) == state[0 if automatic else 1]
    assert project.path == str(path) and not (tmp_path / "unexpected.json").exists()
    assert project._save(destination, automatic=automatic)
    wait(widget)
    assert read_project(destination)["entries"] == []
    assert (project._autosaved if automatic else project._saved) == project.fingerprint()
    assert project.dirty is automatic
    assert not list(tmp_path.rglob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("failure", ["missing_directory", "full_write", "empty_error",
                                    "truncated_project", "truncated_recovery"])
def test_project_error_modal_blocks_reentry_and_preserves_retry(dialogs, tmp_path, kind, failure):
    import errno
    from contextlib import nullcontext
    from PyQt6.QtCore import QLockFile
    from src.ui import editor_project

    _, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(still)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(still)])
        control = widget._brightness_slider
    project = widget._project
    path = tmp_path / "saved.json"
    save(widget, path)
    assert project.autosave()
    wait(widget)
    control.setValue(control.value() + 10)
    project.poll()
    entries = tuple(project.entries)
    history = tuple(widget._undo_stack), tuple(widget._redo_stack)
    state = project.path, project._saved, project._autosaved, project._lock
    before = path.read_bytes(), project.recovery_path.read_bytes()
    resumed = Mock()
    project._pending_close = resumed
    destination = path
    injection = nullcontext()
    if failure == "missing_directory":
        destination = tmp_path / "missing" / "project.json"
    elif failure in ("full_write", "empty_error"):
        error = OSError(errno.ENOSPC, "injected full write") if failure == "full_write" else OSError()
        injection = patch("os.fsync", side_effect=error)
    else:
        destination = tmp_path / "truncated.json"
        destination.write_bytes(b'{"format":')
        project._pending_close = None

    modal_state = {}

    def error_dialog(*args):
        modal_state["guarded"] = project._loading and project._job is None
        if not modal_state["guarded"]:
            return
        modal_state["actions"] = [
            project.autosave(), project._save(tmp_path / "unexpected.json"),
            project.load(path), project.allow_close(resumed), project.allow_parent_close(resumed),
        ]
        # A timer tick must not undo the I/O error by starting another save.
        project._changed_at -= 6
        project._retry_after = 0
        project.poll()
        modal_state["idle_after_poll"] = project._job is None
        if failure == "truncated_recovery":
            probe = QLockFile(str(destination) + ".lock")
            probe.setStaleLockTime(0)
            modal_state["recovery_unlocked"] = probe.tryLock(0)
            probe.unlock()

    with injection, patch.object(editor_project.QMessageBox, "critical", side_effect=error_dialog) as modal:
        if failure.startswith("truncated"):
            assert project.load(destination, recovery=failure == "truncated_recovery")
        else:
            assert project._save(destination)
        wait(widget)
    modal.assert_called_once()
    assert modal_state["guarded"]
    assert modal_state["actions"] == [False] * 5
    assert modal_state["idle_after_poll"]
    if failure == "truncated_recovery":
        assert modal_state["recovery_unlocked"]
    if failure == "empty_error":
        assert modal.call_args.args[2] == "OSError"
    resumed.assert_not_called()
    assert project._pending_close is None
    assert not project._loading and project.timer.isActive() and project.dirty
    assert (project.path, project._saved, project._autosaved, project._lock) == state
    assert tuple(project.entries) == entries
    assert (tuple(widget._undo_stack), tuple(widget._redo_stack)) == history
    assert (path.read_bytes(), project.recovery_path.read_bytes()) == before
    assert project._lock.isLocked()
    assert not (tmp_path / "unexpected.json").exists()
    assert not list(tmp_path.rglob(".alpha_fixer_save_*"))
    assert project._save(path)
    wait(widget)
    assert not project.dirty
    assert tuple(project.entries) == entries
    assert (tuple(widget._undo_stack), tuple(widget._redo_stack)) == history


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_malformed_recovery_media_keeps_live_unsaved_session(dialogs, tmp_path, kind):
    source, still = sources(tmp_path)
    writer, reader = dialogs(kind), dialogs(kind)
    for widget in (writer, reader):
        if kind == "gif":
            widget._add_paths([str(source), str(still)])
        else:
            widget._load_image_paths([str(source), str(still)])
        assert widget._project.autosave()
        wait(widget)
    corrupt = writer._project.recovery_path
    writer._project._release_session()
    document = read_project(corrupt)
    if kind == "gif":
        document["entries"][-1]["png"] = "not-base64!"
    else:
        malformed = tmp_path / "malformed.png"
        malformed.write_bytes(b"not an image")
        document["entries"][-1].update(source=str(malformed), source_sha256=source_signature(malformed))
    write_project(corrupt, document)
    before = corrupt.read_bytes(), reader._project.recovery_path.read_bytes()
    project = reader._project
    state = project.path, project._saved, project._autosaved, project._lock
    entries = tuple(project.entries)
    project.refresh_recoveries()
    assert corrupt in project._recoveries
    with patch("src.ui.editor_project.QMessageBox.critical") as error:
        assert project.load(corrupt, recovery=True)
        wait(reader)
    error.assert_called_once()
    assert "kept intact" in project.status.text()
    assert project.dirty and not project._loading
    assert tuple(project.entries) == entries
    assert (project.path, project._saved, project._autosaved, project._lock) == state
    assert project._lock.isLocked() and project.timer.isActive()
    assert (corrupt.read_bytes(), project.recovery_path.read_bytes()) == before
    colors = [(255, 0, 0, 255), (0, 128, 0, 255), (0, 0, 255, 255), (255, 0, 0, 255)] if kind == "gif" else [
        (255, 0, 0, 255), (255, 0, 0, 255)]
    for entry, color in zip(entries, colors):
        image = entry._pil.copy() if kind == "gif" else entry.get_frame(0)
        with image:
            assert image.getpixel((0, 0)) == color


def test_duplicate_keys_and_wrong_editor(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError, match="Duplicate"):
        read_project(path)


def test_video_split_stream_mapping_data_only(dialogs, tmp_path):
    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    widget._clip_list.setCurrentRow(0)
    widget._scrubber.setValue(0)
    widget._split_clip_at_playhead()
    path = tmp_path / "p.json"
    document, _ = widget._project._document(path)
    # Simulate a decoded multi-stream video without requiring an ffmpeg corpus.
    for entry in document["entries"]:
        entry.update(loader="video", type="video", video_stream=3, audio_stream=7,
                     source_sha256=source_signature(source))
    validate(document)
    clip_template = widget._clips[0]

    def loader(path, **kwargs):
        from src.ui.video_tool import _ClipEntry, _ImageFrameGetter
        assert kwargs == dict(preferred_video_stream_index=3, preferred_audio_stream_index=7)
        return _ClipEntry(path, clip_template.total_frames, _ImageFrameGetter(Image.new("RGBA", (10, 8))),
                          clip_template.fps, clip_type="video", has_audio=True,
                          preferred_video_stream_index=3, preferred_audio_stream_index=7)

    with patch("src.ui.video_tool._load_video_clip", side_effect=loader):
        prepared = prepare_entries(document, path)
    try:
        assert [c._split_timing for c in prepared] == [tuple(e["split"]) for e in document["entries"]]
        assert all(c.has_audio and c.preferred_audio_stream_index == 7 for c in prepared)
    finally:
        for clip in prepared:
            clip.close()


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("corruption", ["audio_count", "output_start", "trim_end"])
def test_malformed_split_project_keeps_unsaved_session(dialogs, tmp_path, recovery, corruption):
    from src.ui import editor_project

    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    widget._clip_speed_slider.setValue(70)
    widget._scrubber.setValue(0)
    widget._split_clip_at_playhead()
    path = tmp_path / "valid.json"
    save(widget, path)
    document = read_project(path)
    entry = document["entries"][0]
    if corruption == "audio_count":
        entry["split"][7] += 1
    elif corruption == "output_start":
        entry["split"][1] += 1
    else:
        entry["trim_end"] += 1
        entry["split"][4] = entry["trim_end"]
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text(json.dumps(document), encoding="utf-8")
    corrupt_bytes = corrupt.read_bytes()
    widget._brightness_slider.setValue(180)
    widget._clip_list.setCurrentRow(1)
    widget._scrubber.setValue(3)
    project = widget._project
    assert project.autosave()
    wait(widget)
    state = (project.fingerprint(), project.path, project._saved, project._autosaved,
             project._lock, widget._clip_list.currentRow(), widget._scrubber.value())
    entries = tuple(widget._clips)
    history = tuple(widget._undo_stack)
    recovery_bytes = project.recovery_path.read_bytes()
    with patch.object(editor_project.QMessageBox, "critical") as error, \
            patch.object(editor_project.QMessageBox, "question",
                         return_value=QMessageBox.StandardButton.Discard) as replace, \
            patch.object(editor_project, "prepare_entries",
                         wraps=editor_project.prepare_entries) as prepare:
        assert project.load(corrupt, recovery=recovery)
        wait(widget)
    error.assert_called_once()
    replace.assert_not_called()
    prepare.assert_not_called()
    assert "kept intact" in project.status.text() and project.dirty
    assert tuple(widget._clips) == entries and tuple(widget._undo_stack) == history
    assert (project.fingerprint(), project.path, project._saved, project._autosaved,
            project._lock, widget._clip_list.currentRow(), widget._scrubber.value()) == state
    assert project._lock.isLocked()
    assert project.recovery_path.read_bytes() == recovery_bytes
    assert corrupt.read_bytes() == corrupt_bytes
    assert not corrupt.with_suffix(".json.lock").exists()
    for clip in entries:
        with clip.get_frame(0) as frame:
            assert frame.size == (10, 8)


@pytest.mark.parametrize("speed", [10, 50, 70, 130, 200, 400])
def test_nested_split_project_restores_cadence_and_live_controls(dialogs, tmp_path, speed):
    from src.core.editor_projects import VIDEO_CONTROLS

    frames = [Image.new("RGBA", (10, 8), (index * 10, 90, 180, 255)) for index in range(24)]
    source = tmp_path / "cadence.gif"
    try:
        frames[0].save(source, save_all=True, append_images=frames[1:], duration=100)
    finally:
        for frame in frames:
            frame.close()
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    widget._trim_start_slider.setValue(2)
    widget._trim_end_slider.setValue(21)
    widget._clip_speed_slider.setValue(speed)

    def cadence(dialog):
        colors = []
        for clip in dialog._clips:
            for index in range(clip.active_frames):
                with clip.get_frame(index) as frame:
                    colors.append(frame.getpixel((0, 0)))
        return colors

    expected = cadence(widget)
    widget._scrubber.setValue(0)
    widget._split_clip_at_playhead()
    widget._scrubber.setValue(widget._clips[0].active_frames)
    widget._split_clip_at_playhead()
    assert len(widget._clips) == 3 and cadence(widget) == expected
    for name, rule in VIDEO_CONTROLS.items():
        control = getattr(widget, name)
        if rule is bool:
            control.setChecked(not control.isChecked())
        elif isinstance(rule[0], int):
            control.setValue(rule[1] - 1)
        else:
            control.setCurrentIndex(control.findData(rule[-1]))
    widget._clip_list.setCurrentRow(1)
    widget._scrubber.setValue(len(expected) - 1)
    controls = widget._project.controls()
    timing = [clip._split_timing for clip in widget._clips]
    audio = [clip._split_audio_range() for clip in widget._clips]
    path = tmp_path / "nested.json"
    save(widget, path)
    loaded = dialogs("video")
    open_project(loaded, path)
    assert loaded._project.controls() == controls
    assert [clip._split_timing for clip in loaded._clips] == timing
    assert [clip._split_audio_range() for clip in loaded._clips] == audio
    assert cadence(loaded) == expected
    assert loaded._clip_list.currentRow() == 1
    assert loaded._scrubber.value() == len(expected) - 1
    assert loaded._trim_start_slider.value() == loaded._clips[1].trim_start
    assert loaded._trim_end_slider.value() == loaded._clips[1].trim_end
    assert loaded._clip_speed_slider.value() == speed
    assert not loaded._project.dirty


@pytest.mark.parametrize("frame_count,speed", [(21, 56), (145, 232)])
def test_project_last_frame_uses_editor_rounding(dialogs, tmp_path, frame_count, speed):
    source = tmp_path / "rounding.gif"
    frames = [Image.new("RGBA", (10, 8), (index, 90, 180, 255)) for index in range(frame_count)]
    try:
        frames[0].save(source, save_all=True, append_images=frames[1:], duration=100)
    finally:
        for frame in frames:
            frame.close()
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    widget._clip_speed_slider.setValue(speed)
    count = widget._clips[0].active_frames
    widget._scrubber.setValue(count - 1)
    path = tmp_path / "rounding.json"
    with patch("src.ui.editor_project.QMessageBox.critical") as error:
        save(widget, path)
    error.assert_not_called()
    loaded = dialogs("video")
    open_project(loaded, path)
    assert loaded._scrubber.maximum() == count - 1
    assert loaded._scrubber.value() == count - 1
    assert loaded._clips[0].active_frames == count
    document = read_project(path)
    document["playhead"] = count
    with pytest.raises(ValueError, match="timeline/playhead"):
        validate(document)


def test_generated_video_audio_split_roundtrip(dialogs, tmp_path):
    from src.ui.video_tool import _get_ffmpeg_exe
    from src.ui.video_preview_audio import clip_audio_target
    from src.core.video_export import source_frame_index
    executable = _get_ffmpeg_exe()
    if not executable:
        pytest.skip("Existing FFmpeg backend unavailable")
    source = tmp_path / "tone.mp4"
    subprocess.run([executable, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=16x16:r=20:d=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                   check=True, capture_output=True, timeout=30)
    widget = dialogs("video")
    widget._load_video_paths([str(source)])
    assert widget._clips and widget._clips[0].has_audio
    widget._clip_speed_slider.setValue(70)
    widget._trim_start_slider.setValue(2)
    widget._trim_end_slider.setValue(17)
    widget._scrubber.setValue(4)
    widget._split_clip_at_playhead()
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData("mp4"))
    path = tmp_path / "tone.videoproject.json"
    save(widget, path)
    loaded = dialogs("video")
    open_project(loaded, path)
    assert len(loaded._clips) == 2
    assert [c._split_audio_range() for c in loaded._clips] == [c._split_audio_range() for c in widget._clips]
    assert [c._split_timing for c in loaded._clips] == [c._split_timing for c in widget._clips]
    assert all(c.has_audio for c in loaded._clips)
    assert loaded._export_fmt_combo.currentData() == "mp4"
    expected_audio = [
        clip_audio_target(clip, frame, widget._fps_slider.value())
        for clip in widget._clips for frame in range(clip.active_frames)
    ]
    expected_snapshots = [
        widget._snapshot_clip_render_state(clip, widget._fps_slider.value())
        for clip in widget._clips
    ]
    assert widget._project.autosave()
    wait(widget)
    widget._project._release_session()
    recovered = dialogs("video")
    assert recovered._project.load(widget._project.recovery_path, recovery=True)
    wait(recovered)
    for restored in (loaded, recovered):
        assert [
            clip_audio_target(clip, frame, restored._fps_slider.value())
            for clip in restored._clips for frame in range(clip.active_frames)
        ] == expected_audio
        snapshots = [
            restored._snapshot_clip_render_state(clip, restored._fps_slider.value())
            for clip in restored._clips
        ]
        for snapshot, expected in zip(snapshots, expected_snapshots):
            for key in ("audio_start_seconds", "audio_end_seconds", "audio_tempo", "timeline_seconds"):
                assert snapshot[key] == pytest.approx(expected[key])
            assert [source_frame_index(snapshot, frame) for frame in range(snapshot["active_frames"])] == [
                source_frame_index(expected, frame) for frame in range(expected["active_frames"])
            ]
    assert recovered._project.dirty and recovered._project.path == ""


def test_repeated_truncated_gif_project_load_releases_partial_frames(dialogs, tmp_path):
    from src.ui import video_tool

    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._project.timer.stop()
    widget._load_image_paths([str(still), str(source)])
    path = tmp_path / "partial-decode.json"
    save(widget, path)
    broken = tmp_path / "broken.gif"
    broken.write_bytes(source.read_bytes()[:-3])
    document = read_project(path)
    document["entries"][1]["source"] = broken.name
    document["entries"][1]["source_sha256"] = source_signature(broken)
    write_project(path, document)
    widget._brightness_slider.setValue(177)
    project = widget._project
    entries = tuple(project.entries)
    fingerprint = project.fingerprint()
    history = list(widget._undo_stack), list(widget._redo_stack)
    saved, autosaved = project._saved, project._autosaved
    before = path.read_bytes()
    copied, prepared = [], []
    copy = Image.Image.copy
    loader = video_tool._load_image_as_clip

    def tracked_copy(image):
        result = copy(image)
        result.close = Mock(wraps=result.close)
        copied.append(result)
        return result

    def tracked_loader(source_path):
        if source_path == str(broken):
            with patch.object(Image.Image, "copy", tracked_copy):
                return loader(source_path)
        result = loader(source_path)
        result.close = Mock(wraps=result.close)
        prepared.append(result)
        return result

    with patch.object(video_tool, "_load_image_as_clip", side_effect=tracked_loader), \
            patch("src.ui.editor_project.QMessageBox.critical") as error, \
            patch("src.ui.editor_project.QMessageBox.question") as replacement:
        for iteration in range(20):
            start = len(copied)
            assert project.load(path, recovery=bool(iteration % 2))
            wait(widget)
            assert len(copied) - start == 6
            for image in copied[start:]:
                image.close.assert_called_once()
                with pytest.raises(ValueError, match="closed"):
                    image.getpixel((0, 0))
            prepared[-1].close.assert_called_once()
            assert tuple(project.entries) == entries
            assert project.fingerprint() == fingerprint and project.dirty
            assert (project._saved, project._autosaved) == (saved, autosaved)
            assert (widget._undo_stack, widget._redo_stack) == history
            assert project.path == str(path) and path.read_bytes() == before
            assert project._job is None and not project._loading
            assert project.open_button.isEnabled() and widget._content_scroll.isEnabled()
            assert not widget._preview_timer.isActive()
        assert error.call_count == 20
        replacement.assert_not_called()
    assert len(prepared) == 20 and len(copied) == 120
    with entries[1].get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))
    assert not list(tmp_path.glob("*.lock"))


def test_wrong_editor_and_bounded_images(dialogs, tmp_path):
    _, still = sources(tmp_path)
    gif = dialogs("gif")
    gif._add_paths([str(still)])
    path = tmp_path / "p.json"
    save(gif, path)
    with pytest.raises(ValueError, match="different editor"):
        read_project(path, "video")
    from src.core.editor_projects import MAX_ENTRIES, MAX_TOTAL_PIXELS
    document = read_project(path)
    document["entries"] = document["entries"] * (MAX_ENTRIES + 1)
    with pytest.raises(ValueError, match="at most"):
        validate(document)
    document = read_project(path)
    document["entries"][0]["size"] = [4096, MAX_TOTAL_PIXELS // 4096 + 1]
    with pytest.raises(ValueError, match="total pixels"):
        validate(document)


def test_parent_shutdown_waits_for_project_and_reopen_uses_new_identity(dialogs, tmp_path):
    from threading import Event
    from src.ui import editor_project
    widget = dialogs("gif")
    widget._delay_slider.setValue(345)
    entered, release = Event(), Event()
    writer = editor_project.write_project

    def blocked_writer(*args):
        entered.set()
        assert release.wait(5)
        writer(*args)

    resumed = []
    with patch.object(editor_project, "write_project", side_effect=blocked_writer):
        widget._project._save(tmp_path / "p.json")
        assert entered.wait(5)
        assert not widget._project.allow_parent_close(lambda: resumed.append(True))
        release.set()
        wait(widget)
    assert resumed == [True]
    identity = widget._project.session_id
    assert widget._project.allow_close(lambda: None)
    widget._project.resume_session()
    assert widget._project.session_id != identity
    assert widget._project.timer.isActive()


def test_multi_editor_cancelled_shutdown_keeps_recovery_live(dialogs, tmp_path):
    from src.ui.editor_project import approve_parent_projects
    _, still = sources(tmp_path)
    gif, video = dialogs("gif"), dialogs("video")
    gif._add_paths([str(still)])
    video._load_image_paths([str(still)])
    gif._project.autosave()
    wait(gif)
    lock = gif._project._lock
    with patch("src.ui.editor_project.QMessageBox.question",
               side_effect=[QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel]):
        assert not approve_parent_projects([gif, video], lambda: None)
    assert not gif._project._closing_approved
    assert gif._project.timer.isActive() and gif._project._lock is lock
    gif._delay_slider.setValue(619)
    gif._project.autosave()
    wait(gif)
    assert read_project(gif._project.recovery_path)["controls"]["_delay_slider"] == 619
    with patch("src.ui.editor_project.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Cancel) as question:
        assert not gif._project.allow_close(lambda: None)
    question.assert_called_once()


def test_multi_editor_save_then_cancel_does_not_commit_shutdown(dialogs, tmp_path):
    from src.ui.editor_project import approve_parent_projects
    gif, video = dialogs("gif"), dialogs("video")
    gif._delay_slider.setValue(444)
    video._brightness_slider.setValue(150)
    resumed = []
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Save), \
            patch("src.ui.editor_project.QFileDialog.getSaveFileName",
                  return_value=(str(tmp_path / "save.json"), "")):
        assert not approve_parent_projects([gif, video], lambda: resumed.append(True))
        wait(gif)
    assert resumed == [True]
    assert not gif._project._closing_approved and gif._project.timer.isActive()
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        assert not approve_parent_projects([gif, video], lambda: None)
    assert not gif._project._closing_approved and gif._project.timer.isActive()


@pytest.mark.parametrize("reopen", [False, True])
def test_supplied_gif_media_autosaves_without_edit(dialogs, tmp_path, reopen):
    from src.ui.main_window import MainWindow
    _, still = sources(tmp_path)
    if reopen:
        widget = dialogs("gif")
        widget.show()
        assert widget.close()
        # Exercise the production opener on a lightweight host, not MainWindow construction.
        class Host:
            _gif_builder_dlg = widget
            def _update_builder_status(self):
                pass
            def _update_current_tool_status(self):
                pass
        MainWindow._open_or_focus_gif_builder(Host(), [str(still)])
    else:
        widget = dialogs("gif", initial_files=[str(still)])
        widget.show()
    controller = widget._project
    assert controller.dirty
    assert controller.fingerprint() != controller._autosaved
    controller.poll()
    controller._changed_at -= 6
    controller.poll()
    wait(widget)
    document = read_project(controller.recovery_path)
    assert len(document["entries"]) == 1
    assert controller.dirty
    with patch("src.ui.editor_project.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Cancel) as question:
        assert not controller.allow_close(lambda: None)
    question.assert_called_once()


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("accepted", [False, True])
def test_normalized_project_destination_requires_confirmation(dialogs, tmp_path, kind, accepted):
    widget = dialogs(kind)
    widget._project.poll()
    widget._project._changed_at = time.monotonic()
    chosen = tmp_path / "existing"
    destination = tmp_path / f"existing.{kind}project.json"
    destination.write_bytes(b"keep me")
    with patch("src.ui.editor_project.QFileDialog.getSaveFileName", return_value=(str(chosen), "")), \
            patch("PyQt6.QtWidgets.QMessageBox.question",
                  return_value=QMessageBox.StandardButton.Yes if accepted else QMessageBox.StandardButton.No) as question:
        assert widget._project.save_project() == accepted
        wait(widget)
    question.assert_called_once()
    if accepted:
        assert read_project(destination)["kind"] == kind
    else:
        assert destination.read_bytes() == b"keep me"
        assert widget._project.path == ""


def test_same_shape_replaced_media_rejected_even_with_preserved_metadata(dialogs, tmp_path):
    import os
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = widget._clips[0]
    stat = still.stat()
    image = Image.new("RGBA", (10, 8), "blue")
    image.save(still)
    image.close()
    os.utime(still, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with patch("src.ui.editor_project.QMessageBox.critical") as error:
        open_project(widget, path)
    error.assert_called_once()
    assert "changed since project save" in error.call_args.args[2]
    assert widget._clips[0] is original
    assert original.get_frame(0).getpixel((0, 0)) == (255, 0, 0, 255)


def test_media_hash_runs_only_in_project_workers_and_once_per_source(dialogs, tmp_path):
    import threading
    from src.ui import editor_project
    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    widget._scrubber.setValue(1)
    widget._split_clip_at_playhead()
    path = tmp_path / "p.json"
    threads = []
    def signature(path):
        threads.append(threading.get_ident())
        return source_signature(path)
    with patch.object(editor_project, "source_signature", side_effect=signature) as hashing:
        save(widget, path)
        for _ in range(3):
            widget._project.poll()
        hashing.assert_called_once()
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    with patch("src.core.editor_projects.source_signature", side_effect=signature) as hashing:
        open_project(widget, path)
        hashing.assert_called_once()
    assert all(thread != threading.get_ident() for thread in threads)


def test_cancelled_parent_shutdown_preserves_closed_editor_reopen(dialogs, tmp_path):
    from src.ui.editor_project import approve_parent_projects
    gif, video = dialogs("gif"), dialogs("video")
    gif.show()
    assert gif.close()
    identity = gif._project.session_id
    video._brightness_slider.setValue(151)
    with patch("src.ui.editor_project.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
        assert not approve_parent_projects([gif, video], lambda: None)
    assert gif._project._closing_approved
    gif.show()
    assert gif._project.session_id != identity and gif._project.timer.isActive()


@pytest.mark.parametrize("route", ["close", "reject", "parent"])
def test_repeated_gif_shutdown_releases_preview_and_recovery(dialogs, tmp_path, route):
    from src.ui.editor_project import approve_parent_projects
    source, _ = sources(tmp_path)
    widget = dialogs("gif")
    for _ in range(5):
        widget.show()
        widget._add_paths([str(source)])
        widget._scrubber.setValue(2)
        widget._btn_play.setChecked(True)
        assert widget._project.autosave()
        wait(widget)
        lock = widget._project._lock
        entries = tuple(widget._frames)
        for entry in entries:
            entry.close = Mock(wraps=entry.close)
        assert not widget._preview_lbl.pixmap().isNull()
        with patch("src.ui.editor_project.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Discard):
            if route == "parent":
                assert approve_parent_projects([widget], lambda: None)
                widget.hide()
            elif route == "reject":
                widget.reject()
            else:
                assert widget.close()
        assert not widget._frames and not widget._history_entries
        assert not widget._undo_stack and not widget._redo_stack
        for entry in entries:
            entry.close.assert_called_once()
            assert not entry._thumb_cache
            with pytest.raises(ValueError):
                entry._pil.getpixel((0, 0))
        assert widget._preview_lbl.pixmap().isNull()
        assert widget._preview_lbl.text() == "(no frames yet)"
        assert widget._preview_frame_lbl.text() == "0 / 0"
        assert widget._frame_count_lbl.text() == "0 frames"
        assert widget._scrubber.maximum() == 0
        assert not widget._scrubber.isEnabled() and not widget._btn_rewind.isEnabled()
        assert not widget._btn_play.isChecked() and not widget._preview_timer.isActive()
        assert not widget._project.timer.isActive()
        assert widget._project._lock is None and not lock.isLocked()
        assert widget._project.recovery_path.is_file()


def test_identity_failure_closes_already_prepared_clips(dialogs, tmp_path):
    from src.ui import video_tool
    source, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source), str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    image = Image.new("RGBA", (10, 8), "blue")
    image.save(still)
    image.close()
    prepared = []
    loader = video_tool._load_image_as_clip
    def load(path):
        clip = loader(path)
        clip.close = Mock(wraps=clip.close)
        prepared.append(clip)
        return clip
    with patch.object(video_tool, "_load_image_as_clip", side_effect=load):
        with pytest.raises(ValueError, match="changed since project save"):
            prepare_entries(read_project(path), path)
    assert len(prepared) == 1
    prepared[0].close.assert_called_once()


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("identity", ["import", "saved", "loaded"])
def test_save_rejects_replaced_source_preserves_disk(dialogs, tmp_path, automatic, identity):
    import os
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    if identity != "import":
        save(widget, path)
        if identity == "loaded":
            open_project(widget, path)
    controller = widget._project
    if automatic:
        destination = controller.recovery_path
        destination.parent.mkdir(parents=True, exist_ok=True)
    else:
        destination = path
    if not destination.exists():
        destination.write_bytes(b"previous complete snapshot")
    before = destination.read_bytes()
    saved, autosaved = controller._saved, controller._autosaved
    original = widget._clips[0]
    stat = still.stat()
    with Image.new("RGBA", (10, 8), "blue") as replacement:
        replacement.save(still)
    os.utime(still, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    # Force the cached-hash branch, independently of reliable filesystem timestamps.
    if identity != "import":
        original._source_stamp = editor_project._source_stamp(still.stat())
    with patch.object(editor_project.QMessageBox, "critical") as error:
        assert controller._save(destination, automatic=automatic)
        wait(widget)
    assert destination.read_bytes() == before
    assert controller._saved == saved and controller._autosaved == autosaved
    assert "changed since" in controller.status.text()
    assert error.call_count == (0 if automatic else 1)
    assert widget._clips[0] is original
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("accepted", [False, True])
def test_parent_shutdown_releases_prepared_media_only_after_all_approve(dialogs, tmp_path, accepted):
    from src.ui.editor_project import approve_parent_projects
    from src.ui.video_tool import _ClipEntry, _VideoFrameGetter, _CLIP_ROLE
    from PyQt6.QtWidgets import QListWidgetItem
    video, gif = dialogs("video"), dialogs("gif")
    prepared = tmp_path / "prepared.mp4"
    prepared.write_bytes(b"prepared media")
    getter = _VideoFrameGetter(str(prepared), 1, cleanup_paths=[str(prepared)])
    reader = Mock()
    getter._reader = reader
    clip = _ClipEntry(str(prepared), 1, getter)
    video._clips.append(clip)
    item = QListWidgetItem("prepared")
    item.setData(_CLIP_ROLE, clip)
    video._clip_list.addItem(item)
    gif._delay_slider.setValue(444)
    video._project._saved = video._project.fingerprint()
    release = Mock(wraps=clip.close)
    clip.close = release
    with patch("src.ui.editor_project.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Discard if accepted else QMessageBox.StandardButton.Cancel):
        assert approve_parent_projects([video, gif], lambda: None) == accepted
    if accepted:
        release.assert_called_once()
        reader.close.assert_called_once()
        assert not prepared.exists() and not video._clips
        assert not video._project.timer.isActive()
    else:
        release.assert_not_called()
        reader.close.assert_not_called()
        assert prepared.exists() and video._clips == [clip]
        assert video._project.timer.isActive() and not video._project._closing_approved


@pytest.mark.parametrize("failure", ["controls", "clone", "rollback"])
def test_install_snapshot_failure_releases_prepared_entries(dialogs, tmp_path, failure):
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    document = read_project(path)
    entries = prepare_entries(document, path)
    original = widget._clips[0]
    for entry in entries:
        entry.close = Mock(wraps=entry.close)
    if failure == "controls":
        target, attribute = widget._project, "controls"
    elif failure == "clone":
        target, attribute = editor_project.QListWidgetItem, "clone"
    else:
        target, attribute = widget._project, "_apply_state"
    with patch.object(target, attribute, side_effect=MemoryError("snapshot failure")):
        with pytest.raises(MemoryError, match="snapshot failure"):
            widget._project._install(document, entries)
    for entry in entries:
        entry.close.assert_called_once()
    assert widget._clips == [original]
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_failed_install_keeps_unsaved_selection_history_and_playback(dialogs, tmp_path, kind):
    from src.ui import editor_project
    source, still = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(source), str(still)])
    else:
        widget._load_image_paths([str(source), str(still), str(still)])
    path = tmp_path / "rollback.json"
    save(widget, path)
    project = widget._project
    listing = project.entry_list
    listing.setCurrentRow(2, QItemSelectionModel.SelectionFlag.NoUpdate)
    listing.clearSelection()
    selected_rows = [0, 2] if kind == "gif" else [2]
    for row in selected_rows:
        listing.item(row).setSelected(True)
    assert [listing.row(item) for item in listing.selectedItems()] == selected_rows
    if kind == "gif":
        widget._pf_check.setChecked(True)
        widget._pf_slider.setValue(333)
        widget._delay_slider.setValue(777)
    else:
        widget._brightness_slider.setValue(177)
    widget._scrubber.setValue(1)
    widget._btn_play.setChecked(True)
    entries = list(project.entries)
    fingerprint = project.fingerprint()
    saved, autosaved = project._saved, project._autosaved
    history = (list(widget._undo_stack), list(widget._redo_stack))
    before = path.read_bytes()
    prepared = []
    prepare = editor_project.prepare_entries

    def tracked_prepare(*args):
        result = prepare(*args)
        for entry in result:
            entry.close = Mock(wraps=entry.close)
        prepared.extend(result)
        return result

    apply = project._apply_state
    attempts = []

    def fail_after_apply(*args):
        apply(*args)
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("late project refresh failed")

    with patch.object(editor_project, "prepare_entries", side_effect=tracked_prepare), \
            patch.object(project, "_apply_state", side_effect=fail_after_apply), \
            patch.object(editor_project.QMessageBox, "critical") as error:
        open_project(widget, path)
    error.assert_called_once()
    assert len(attempts) == 2
    assert project.entries == entries
    assert project.fingerprint() == fingerprint
    assert (project._saved, project._autosaved) == (saved, autosaved)
    assert (widget._undo_stack, widget._redo_stack) == history
    assert listing.currentRow() == 2
    assert [listing.row(item) for item in listing.selectedItems()] == selected_rows
    assert widget._scrubber.value() == 1
    assert widget._btn_play.isChecked() and widget._preview_timer.isActive()
    widget._btn_play.setChecked(False)
    assert project.dirty and project.path == str(path)
    assert path.read_bytes() == before
    assert project.open_button.isEnabled() and widget._content_scroll.isEnabled()
    for entry in prepared:
        entry.close.assert_called_once()
    assert "current project has been kept intact" in error.call_args.args[2]


@pytest.mark.parametrize("kind", ["gif", "video"])
def test_invalid_open_keeps_unsaved_controls_and_playback(dialogs, tmp_path, kind):
    source, _ = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(source)])
    else:
        widget._load_image_paths([str(source)])
    path = tmp_path / "invalid.json"
    save(widget, path)
    document = read_project(path)
    document["version"] = True
    path.write_text(json.dumps(document))
    control = widget._delay_slider if kind == "gif" else widget._brightness_slider
    control.setValue(177)
    widget._scrubber.setValue(1)
    widget._btn_play.setChecked(True)
    project = widget._project
    entries = list(project.entries)
    fingerprint = project.fingerprint()
    saved, autosaved = project._saved, project._autosaved
    with patch("src.ui.editor_project.QMessageBox.critical") as error, \
            patch("src.ui.editor_project.prepare_entries") as prepare:
        open_project(widget, path)
    error.assert_called_once()
    prepare.assert_not_called()
    assert project.entries == entries and project.fingerprint() == fingerprint
    assert (project._saved, project._autosaved) == (saved, autosaved)
    assert control.value() == 177 and project.dirty
    assert widget._btn_play.isChecked() and widget._preview_timer.isActive()
    widget._btn_play.setChecked(False)
    assert widget._scrubber.value() == 1
    assert project.open_button.isEnabled() and widget._content_scroll.isEnabled()


@pytest.mark.parametrize("kind", ["gif", "video"])
@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("failure", ["read", "prepare"])
def test_failed_open_pauses_retained_playback_until_error_dismissed(
        dialogs, tmp_path, kind, recovery, failure):
    source, _ = sources(tmp_path)
    widget = dialogs(kind)
    if kind == "gif":
        widget._add_paths([str(source)])
        control = widget._delay_slider
    else:
        widget._load_image_paths([str(source)])
        control = widget._brightness_slider
    path = tmp_path / "failed-open.json"
    save(widget, path)
    project = widget._project
    document = read_project(path)
    if failure == "read":
        document["version"] = 2
    elif kind == "gif":
        document["entries"][0]["png"] = "not-base64!"
    else:
        document["entries"][0]["source_sha256"] = "0" * 64
    path.write_text(json.dumps(document))
    control.setValue(control.value() + 10)
    widget._scrubber.setValue(0)
    widget._btn_play.setChecked(True)
    entries = tuple(project.entries)
    fingerprint = project.fingerprint()
    saved, autosaved = project._saved, project._autosaved
    history = list(widget._undo_stack), list(widget._redo_stack)
    before = path.read_bytes()
    modal = []

    def error_dialog(*args):
        modal.append((widget._btn_play.isChecked(), widget._preview_timer.isActive()))
        # A real modal error dialog pumps the retained editor's preview timer.
        QTest.qWait(160)
        modal.append(widget._scrubber.value())

    with patch("src.ui.editor_project.QMessageBox.critical", side_effect=error_dialog) as error:
        assert project.load(path, recovery=recovery)
        wait(widget)
    error.assert_called_once()
    assert modal == [(False, False), 0]
    assert widget._btn_play.isChecked() and widget._preview_timer.isActive()
    widget._btn_play.setChecked(False)
    assert widget._scrubber.value() == 0
    assert tuple(project.entries) == entries and project.fingerprint() == fingerprint
    assert (project._saved, project._autosaved) == (saved, autosaved)
    assert (widget._undo_stack, widget._redo_stack) == history
    assert project.dirty and project.path == str(path)
    assert path.read_bytes() == before
    assert project.open_button.isEnabled() and widget._content_scroll.isEnabled()
    if kind == "gif":
        assert entries[0]._pil.getpixel((0, 0)) == (255, 0, 0, 255)
    else:
        with entries[0].get_frame(0) as frame:
            assert frame.getpixel((0, 0)) == (255, 0, 0, 255)

    project._retry_after -= 31
    project._last_autosave -= 31
    project.poll()
    wait(widget)
    retained = read_project(project.recovery_path)
    assert retained["controls"] == project.controls()
    assert retained["selected"] == project.entry_list.currentRow()
    assert retained["playhead"] == 0 and len(retained["entries"]) == len(entries)
    assert project.dirty and tuple(project.entries) == entries
    assert path.read_bytes() == before


def test_first_autosave_retains_hash_and_pending_parent_close_keeps_media(dialogs, tmp_path):
    from threading import Event
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    clip = widget._clips[0]
    assert clip._source_sha256 is None
    clip.close = Mock(wraps=clip.close)
    entered, release = Event(), Event()
    writer = editor_project.write_project
    resumed = []
    def blocked_writer(*args):
        entered.set()
        assert release.wait(5)
        writer(*args)
    with patch.object(editor_project, "write_project", side_effect=blocked_writer):
        assert widget._project.autosave()
        assert entered.wait(5)
        try:
            assert not editor_project.approve_parent_projects([widget], lambda: resumed.append(True))
            clip.close.assert_not_called()
            assert widget._project.timer.isActive()
        finally:
            release.set()
            wait(widget)
    assert resumed == [True]
    assert clip._source_sha256 == source_signature(still)
    with patch.object(editor_project.QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
        assert editor_project.approve_parent_projects([widget], lambda: None)
    clip.close.assert_called_once()


def test_import_rejects_source_changed_during_decode(tmp_path):
    from src.ui.video_tool import _load_identified_clip
    _, still = sources(tmp_path)
    clip = Mock()
    def decode():
        with Image.new("RGBA", (10, 8), "blue") as replacement:
            replacement.save(still)
        return clip
    assert _load_identified_clip(str(still), decode) is None
    clip.close.assert_called_once()


@pytest.mark.parametrize("identity", ["saved", "loaded"])
@pytest.mark.parametrize("loader", ["image", "video"])
def test_reload_rejects_replaced_project_source(dialogs, tmp_path, identity, loader):
    import os
    from src.ui import video_tool
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    if identity == "loaded":
        open_project(widget, path)
    original = widget._clips[0]
    if loader == "video":
        original.clip_type = "video"
    original.close = Mock(wraps=original.close)
    retained = original._source_sha256, original._source_stamp
    stat = still.stat()
    with Image.new("RGBA", (10, 8), "blue") as replacement:
        replacement.save(still)
    os.utime(still, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    name = "_load_video_clip" if loader == "video" else "_load_image_as_clip"
    with patch.object(video_tool, name) as decode:
        assert widget._reload_clip(original, preferred_video_stream_index=7) is None
    decode.assert_not_called()
    original.close.assert_not_called()
    assert widget._clips == [original]
    assert (original._source_sha256, original._source_stamp) == retained
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("identity", ["saved", "loaded"])
def test_unchanged_reload_and_splits_retain_project_source_identity(dialogs, tmp_path, identity):
    from src.ui import editor_project
    source, _ = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(source)])
    path = tmp_path / "p.json"
    save(widget, path)
    if identity == "loaded":
        open_project(widget, path)
    original = widget._clips[0]
    retained = original._source_sha256, original._source_stamp
    original.speed_percent = 125
    with patch.object(editor_project, "source_signature", side_effect=AssertionError("GUI hash")), \
            patch("src.core.editor_projects.source_signature", side_effect=AssertionError("GUI hash")):
        reloaded = widget._reload_clip(original)
        assert reloaded is not None and reloaded is not original
        try:
            assert (reloaded._source_sha256, reloaded._source_stamp) == retained
            assert reloaded.speed_percent == 125
        finally:
            reloaded.close()
        widget._scrubber.setValue(0)
        widget._split_clip_at_playhead()
    assert len(widget._clips) == 2
    assert all((clip._source_sha256, clip._source_stamp) == retained for clip in widget._clips)
    save(widget, path)
    assert all(entry["source_sha256"] == retained[0] for entry in read_project(path)["entries"])


@pytest.mark.parametrize("loader", ["image", "video"])
def test_reload_rejects_source_changed_during_decode(dialogs, tmp_path, loader):
    from src.ui import video_tool
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    original = widget._clips[0]
    original.close = Mock(wraps=original.close)
    original.clip_type = loader
    retained = original._source_sha256, original._source_stamp
    decoded = video_tool._load_image_as_clip(str(still))
    decoded.close = Mock(wraps=decoded.close)
    def decode(*args, **kwargs):
        with Image.new("RGBA", (10, 8), "blue") as replacement:
            replacement.save(still)
        return decoded
    name = "_load_video_clip" if loader == "video" else "_load_image_as_clip"
    with patch.object(video_tool, name, side_effect=decode):
        assert widget._reload_clip(original) is None
    decoded.close.assert_called_once()
    original.close.assert_not_called()
    assert widget._clips == [original]
    assert (original._source_sha256, original._source_stamp) == retained
    with original.get_frame(0) as frame:
        assert frame.getpixel((0, 0)) == (255, 0, 0, 255)


def test_reload_retained_hash_rejects_replacement_at_worker_save(dialogs, tmp_path):
    from src.ui import editor_project
    _, still = sources(tmp_path)
    widget = dialogs("video")
    widget._load_image_paths([str(still)])
    path = tmp_path / "p.json"
    save(widget, path)
    original = widget._clips[0]
    before = path.read_bytes()
    with Image.new("RGBA", (10, 8), "blue") as replacement:
        replacement.save(still)
    # Exercise deferred content verification even when metadata comparison passes.
    original._source_stamp = editor_project._source_stamp(still.stat())
    retained = original._source_sha256, original._source_stamp
    reloaded = widget._reload_clip(original)
    assert reloaded is not None
    assert (reloaded._source_sha256, reloaded._source_stamp) == retained
    widget._clips[0] = reloaded
    original.close()
    with patch.object(editor_project.QMessageBox, "critical") as error:
        assert widget._project._save(path)
        wait(widget)
    error.assert_called_once()
    assert "changed since" in widget._project.status.text()
    assert path.read_bytes() == before
