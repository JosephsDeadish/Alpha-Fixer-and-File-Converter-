"""Deterministic follower/clock checks and optional real Qt decoding smoke test."""
import subprocess

import pytest

from src.ui.video_preview_audio import AudioTarget, PreviewAudioController, clip_audio_target


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.device = True
        self.tracks = 1
        self.can_seek = True
        self.length = 20.0
        self.pos = 0.0
        self.event = lambda *_: None

    def has_device(self): return self.device
    def track_count(self): return self.tracks
    def seekable(self): return self.can_seek
    def duration(self): return self.length
    def position(self): return self.pos
    def load(self, path): self.calls.append(("load", path))
    def stop(self): self.calls.append(("stop",))
    def pause(self): self.calls.append(("pause",))
    def play(self): self.calls.append(("play",))
    def rate(self, rate): self.calls.append(("rate", rate))
    def volume(self, volume, muted): self.calls.append(("volume", volume, muted))
    def seek(self, seconds):
        self.pos = seconds
        self.calls.append(("seek", seconds))


@pytest.fixture
def follower():
    backend = FakeBackend()
    messages = []
    clock = [0.0]
    controller = PreviewAudioController(backend, messages.append, lambda: clock[0])
    return controller, backend, messages, clock


def ready(follower, target=AudioTarget("clip.mp4", 1.0, 1.0)):
    controller, backend, _, _ = follower
    controller.configure(True, 0.7, False)
    controller.sync(target, True)
    backend.event("ready")
    return controller, backend


def test_opt_in_async_ready_uses_latest_target(follower):
    c, b, messages, _ = follower
    c.sync(AudioTarget("clip.mp4", 1, 1), True)
    assert not b.calls
    c.configure(True, 0.4, False)
    assert ("play",) not in b.calls
    c.sync(AudioTarget("clip.mp4", 3, 2), True)
    b.event("ready")
    assert ("seek", 3) in b.calls
    assert ("rate", 2) in b.calls
    assert ("volume", 0.4, False) in b.calls
    assert messages[-1].startswith("Preview audio on")
    before = len(b.calls)
    b.event("ready")
    assert len(b.calls) == before


def test_drift_correction_is_bounded(follower):
    c, b = ready(follower)
    clock = follower[3]
    b.calls.clear()
    clock[0] = 0.1
    c.sync(AudioTarget("clip.mp4", 2, 1), True)
    assert not any(call[0] == "seek" for call in b.calls)
    clock[0] = 0.4
    c.sync(AudioTarget("clip.mp4", 2, 1), True)
    assert ("seek", 2) in b.calls
    clock[0] = 0.8
    b.calls.clear()
    c.sync(AudioTarget("clip.mp4", 2.05, 1), True)
    assert not any(call[0] == "seek" for call in b.calls)


def test_pause_mute_switch_silence_and_shutdown(follower):
    c, b = ready(follower)
    c.sync(AudioTarget("clip.mp4", 4, 1), False, force=True)
    assert b.calls[-1] == ("pause",)
    c.configure(True, 0.2, True)
    assert ("volume", 0.2, True) in b.calls
    c.sync(AudioTarget("still.png", 0, 1, False, "Silent segment."), True)
    assert c.path is None
    assert b.calls[-1] == ("load", None)
    c.configure(True, 0.2, False)
    c.sync(AudioTarget("second.mp4", 0, 1), True)
    assert not c.ready
    b.event("ready")
    c.close()
    before = list(b.calls)
    b.event("ready")
    c.sync(AudioTarget("third.mp4", 0, 1), True)
    assert b.calls == before


def test_reset_ignores_reentrant_events_while_releasing_source(follower):
    c, b = ready(follower)
    b.calls.clear()
    messages = list(follower[2])

    def stop():
        b.calls.append(("stop",))
        b.event("ready")
        b.event("ended")
        b.event("error", "clearing source")

    b.stop = stop
    c.reset()
    assert b.calls == [("stop",), ("load", None)]
    assert follower[2] == messages
    assert c.path is None and not c.ready and not c.failed and not c.ended


@pytest.mark.parametrize("failure", ["device", "error", "tracks", "seekable", "timeout"])
def test_unavailable_backend_is_inline_and_silent(follower, failure):
    c, b, messages, clock = follower
    c.configure(True, 0.7, False)
    if failure == "device": b.device = False
    if failure == "tracks": b.tracks = 2
    if failure == "seekable": b.can_seek = False
    target = AudioTarget("clip.mp4", 0, 1)
    c.sync(target, True)
    if failure == "error": b.event("error", "decoder failed")
    elif failure == "timeout":
        clock[0] = 9
        c.sync(target, True)
    elif failure != "device": b.event("ready")
    assert ("play",) not in b.calls
    assert "unavailable" in messages[-1]


def test_device_return_and_short_audio_do_not_advance_video(follower):
    c, b = ready(follower)
    b.device = False
    b.event("device")
    assert b.calls[-1] == ("pause",)
    b.device = True
    b.event("device")
    assert b.calls[-1] == ("play",)
    b.length = 1.5
    c.sync(AudioTarget("clip.mp4", 2, 1), True)
    assert b.calls[-1] == ("pause",)
    b.event("ended")
    b.calls.clear()
    c.sync(AudioTarget("clip.mp4", 2.5, 1), True)
    assert ("play",) not in b.calls
    c.sync(AudioTarget("clip.mp4", 0, 1), True, force=True)
    assert b.calls[-1] == ("play",)


def test_device_appears_after_initial_unavailability(follower):
    c, b, _, _ = follower
    b.device = False
    c.configure(True, 0.7, False)
    c.sync(AudioTarget("clip.mp4", 2, 1), True)
    assert c.path is None
    b.device = True
    b.event("device")
    assert ("load", "clip.mp4") in b.calls
    b.event("ready")
    assert b.calls[-1] == ("play",)


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_failed_load_retries_only_on_explicit_sync(follower, failure):
    c, b, _, clock = follower
    target = AudioTarget("clip.mp4", 2, 1)
    c.configure(True, 0.7, False)
    c.sync(target, True)
    if failure == "error":
        b.event("error", "transient decoder failure")
    else:
        clock[0] = 9
        c.sync(target, True)
    assert c.failed
    b.calls.clear()
    c.sync(target, True)
    assert not b.calls
    c.sync(target, True, force=True)
    assert not b.calls
    c.sync(target, True, force=True, retry=True)
    assert b.calls.count(("load", "clip.mp4")) == 1
    assert not c.failed and not c.ready
    c.sync(AudioTarget("clip.mp4", 4, 1), True)
    b.event("ready")
    assert ("seek", 4) in b.calls
    assert b.calls[-1] == ("play",)


def test_device_return_retries_failed_load(follower):
    c, b = ready(follower)
    b.device = False
    b.event("device")
    b.event("error", "output disconnected")
    assert c.failed
    b.calls.clear()
    b.device = True
    b.event("device")
    assert ("load", "clip.mp4") in b.calls
    b.event("ready")
    assert b.calls[-1] == ("play",)


def test_no_device_time_does_not_exhaust_loading_deadline(follower):
    c, b, messages, clock = follower
    c.configure(True, 0.7, False)
    c.sync(AudioTarget("clip.mp4", 2, 1), True)
    clock[0] = 1
    b.device = False
    b.event("device")
    clock[0] = 20
    c.sync(AudioTarget("clip.mp4", 4, 1), True)
    clock[0] = 30
    b.device = True
    b.event("device")
    assert not c.failed
    assert messages[-1].startswith("Preview audio: loading")
    b.event("ready")
    assert ("seek", 4) in b.calls
    assert b.calls[-1] == ("play",)


def test_loading_timeout_still_applies_after_device_return(follower):
    c, b, messages, clock = follower
    c.configure(True, 0.7, False)
    target = AudioTarget("clip.mp4", 2, 1)
    c.sync(target, True)
    clock[0] = 1
    b.device = False
    b.event("device")
    clock[0] = 30
    b.device = True
    b.event("device")
    clock[0] = 38
    c.sync(target, True)
    assert c.failed
    assert "loading timed out" in messages[-1]
    assert ("play",) not in b.calls


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def video(app, monkeypatch):
    from PyQt6 import sip
    from src.ui import video_tool as vt
    backend = FakeBackend()
    monkeypatch.setattr(vt, "QtAudioBackend", lambda _parent: backend)
    widget = vt.VideoToolDialog()
    yield widget, backend
    widget._release_clips()
    if hasattr(widget, "_project"):
        widget._project.allow_close = lambda *_: True
    widget.close()
    sip.delete(widget)


def add_clip(widget, path="clip.mp4", count=100, kind="video", probe=None):
    from PIL import Image
    from PyQt6.QtWidgets import QListWidgetItem
    from src.ui.video_tool import _ClipEntry, _CLIP_ROLE
    clip = _ClipEntry(path, count, lambda _index: Image.new("RGBA", (8, 8)), fps=25,
                      frame_size=(8, 8), clip_type=kind, has_audio=kind == "video",
                      source_probe=probe or {"audio_stream_count": 1, "audio_stream_index": 1})
    widget._clips.append(clip)
    item = QListWidgetItem(path)
    item.setData(_CLIP_ROLE, clip)
    widget._clip_list.addItem(item)
    widget._clip_list.setCurrentRow(0)
    widget._update_scrubber()
    widget._update_preview()
    widget._update_ui_state()
    return clip


@pytest.mark.parametrize("speed", [50, 100, 175, 400])
def test_mapping_matches_export_continuous_split(video, speed):
    widget, _ = video
    clip = add_clip(widget)
    clip.trim_start, clip.trim_end, clip.speed_percent = 7, 86, speed
    count = clip.active_frames
    clip._set_split_render_range(7, 3, count - 3, 86, count)
    for fps in (10, 25, 60):
        snapshot = widget._snapshot_clip_render_state(clip, fps)
        target = clip_audio_target(clip, 4, fps)
        assert target.seconds == pytest.approx(snapshot["audio_start_seconds"] + 4 / fps * snapshot["audio_tempo"])
        assert target.rate == pytest.approx(snapshot["audio_tempo"])


def test_ui_transport_clock_controls_independent_of_export(video):
    widget, backend = video
    clip = add_clip(widget)
    now = [10.0]
    widget._preview_clock = lambda: now[0]
    export = (widget._audio_enable_check.isChecked(), widget._audio_volume_slider.value())
    widget._preview_audio_check.setChecked(True)
    widget._btn_play.setChecked(True)
    backend.event("ready")
    now[0] += 0.24
    widget._advance_preview()
    assert widget._scrubber.value() == 6
    widget._scrubber.setValue(30)
    assert backend.pos == pytest.approx(1.2)
    widget._fps_slider.setValue(50)
    assert ("rate", 2.0) in backend.calls
    widget._preview_volume_slider.setValue(21)
    widget._preview_mute_check.setChecked(True)
    assert ("volume", 0.21, True) in backend.calls
    assert export == (widget._audio_enable_check.isChecked(), widget._audio_volume_slider.value())
    widget._btn_play.setChecked(False)
    widget._rewind()
    assert backend.pos == 0
    clip.trim_start = 10
    widget._update_preview()
    assert backend.pos == pytest.approx(0.4)


def test_ui_still_switch_loop_and_disabled_audio(video):
    widget, backend = video
    add_clip(widget, count=10)
    add_clip(widget, "still.png", kind="image")
    now = [0.0]
    widget._preview_clock = lambda: now[0]
    widget._preview_audio_check.setChecked(True)
    widget._btn_play.setChecked(True)
    backend.event("ready")
    widget._scrubber.setValue(10)
    assert backend.calls[-1] == ("load", None)
    assert "Silent" in widget._preview_audio_status.text()
    now[0] = 1.04
    widget._advance_preview()
    assert widget._scrubber.value() == 1
    assert ("load", "clip.mp4") in backend.calls
    widget._preview_audio_check.setChecked(False)
    assert backend.calls[-1] == ("load", None)
    assert "off" in widget._preview_audio_status.text()


@pytest.mark.parametrize("count,start,ticks", [
    (2, 0, [0.016 * i for i in range(1, 101)]),
    (7, 5, [0.016, 0.048, 0.321, 1.987, 2.003]),
])
def test_preview_clock_preserves_fractional_phase_and_multiple_loops(video, count, start, ticks):
    widget, _ = video
    add_clip(widget, count=count)
    now = [0.0]
    widget._preview_clock = lambda: now[0]
    widget._fps_slider.setValue(60)
    widget._scrubber.setValue(start)
    widget._btn_play.setChecked(True)
    widget._preview_timer.stop()
    loops = []
    widget._sync_preview_audio = lambda force=False, retry=False: loops.append((force, retry))
    previous_loop = 0
    for tick in ticks:
        now[0] = tick
        loops.clear()
        widget._advance_preview()
        absolute = start + int(tick * 60)
        assert widget._scrubber.value() == absolute % count
        assert loops.count((True, False)) == int(absolute // count != previous_loop)
        assert not any(retry for _, retry in loops)
        assert widget._preview_anchor == 0.0
        previous_loop = absolute // count


@pytest.mark.parametrize("split", [False, True], ids=["wrap", "clip-boundary-and-wrap"])
@pytest.mark.parametrize("recovery", ["scrub", "play", "fps", "configure", "device"])
def test_automatic_wraps_do_not_retry_invalid_media_but_recovery_does(video, split, recovery):
    widget, backend = video
    add_clip(widget, count=2)
    if split:
        add_clip(widget, count=2)
    now = [0.0]
    widget._preview_clock = lambda: now[0]
    widget._preview_audio_check.setChecked(True)
    widget._btn_play.setChecked(True)
    widget._preview_timer.stop()

    def invalid_load(path):
        backend.calls.append(("load", path))
        backend.event("error", "invalid media")

    backend.load = invalid_load
    backend.event("error", "invalid media")
    backend.calls.clear()
    for tick in range(1, 25):
        now[0] = tick * 0.041
        widget._advance_preview()
    assert widget._preview_loop >= 6
    assert widget._preview_audio.failed
    assert not any(call[0] == "load" for call in backend.calls)

    if recovery == "scrub":
        widget._scrubber.setValue(1 - widget._scrubber.value())
    elif recovery == "play":
        widget._btn_play.setChecked(False)
    elif recovery == "fps":
        widget._fps_slider.setValue(50)
    elif recovery == "configure":
        widget._preview_volume_slider.setValue(21)
    else:
        backend.event("device")
    assert backend.calls.count(("load", "clip.mp4")) == 1
    for tick in range(25, 49):
        now[0] = tick * 0.041
        widget._advance_preview()
    assert backend.calls.count(("load", "clip.mp4")) == 1


@pytest.mark.parametrize("pixels", [13, 24, 32])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
@pytest.mark.parametrize("light", [False, True], ids=["dark", "light"])
def test_new_preview_controls_compact_font_geometry(video, app, pixels, size, light):
    from PyQt6.QtCore import QPoint, QTimer
    from src.ui.theme_engine import DEFAULT_THEME, LIGHT_THEME, build_stylesheet
    widget, _ = video
    widget.setStyleSheet(build_stylesheet(LIGHT_THEME if light else DEFAULT_THEME)
                        + f"\nQWidget {{ font-size: {pixels}px; }}")
    widget.resize(*size)
    widget.show()
    callbacks = []
    # Never assert in a Qt callback: exceptions there can abort the interpreter.
    QTimer.singleShot(0, lambda: callbacks.append("settled"))
    for _ in range(30):
        app.processEvents()
    assert callbacks == ["settled"]
    assert (widget.width(), widget.height()) == size
    viewport = widget._content_scroll.viewport()
    # At 480px/32px the pre-existing import toolbar overflows by 103px.
    # The preview pane (including the new controls) must fit independently.
    assert widget._preview_audio_check.parentWidget().minimumSizeHint().width() <= viewport.width()
    if size != (480, 400) or pixels != 32:
        assert widget._content_scroll.horizontalScrollBar().maximum() == 0
    for control in (widget._preview_audio_check, widget._preview_mute_check,
                    widget._preview_volume_slider, widget._preview_volume_lbl,
                    widget._preview_audio_status):
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()
        if control in (widget._preview_audio_check, widget._preview_mute_check,
                       widget._preview_volume_slider):
            control.setFocus()
            for _ in range(30):
                app.processEvents()
            viewport = widget._content_scroll.viewport()
            assert app.focusWidget() is control
            assert viewport.rect().contains(control.mapTo(viewport, QPoint()))
            assert viewport.rect().contains(control.mapTo(viewport, control.rect().bottomRight()))
    for control in (widget._preview_audio_check, widget._preview_volume_slider):
        assert any(any(row.itemAt(i).widget() is control for i in range(row.count()))
                   for row in widget._responsive_rows)


@pytest.fixture
def qt_backend():
    from types import SimpleNamespace
    from src.ui.video_preview_audio import QtAudioBackend
    calls = []

    class Signal:
        def connect(self, callback):
            self.callback = callback
        def disconnect(self, callback):
            self.callback = lambda *_: None

    class Player:
        MediaStatus = SimpleNamespace(LoadedMedia=1, BufferedMedia=2, EndOfMedia=3, InvalidMedia=4)

        def __init__(self, parent):
            self.mediaStatusChanged, self.errorOccurred = Signal(), Signal()
            self.destroyed = Signal()
            self.source = ""
            self.status = self.MediaStatus.LoadedMedia
        def setAudioOutput(self, output):
            calls.append((self, "output", output))
        def setSource(self, source):
            self.source = source
            calls.append((self, "source", source))
            if not source:
                self.mediaStatusChanged.callback(4)
                self.errorOccurred.callback(None, "source cleared")
        def stop(self):
            calls.append((self, "stop"))
            self.mediaStatusChanged.callback(3)
        def deleteLater(self):
            calls.append((self, "delete"))
        def errorString(self): return "old decoder"
        def mediaStatus(self): return self.status

    class Url:
        def __new__(cls):
            return ""
        fromLocalFile = staticmethod(lambda path: path)

    backend = QtAudioBackend.__new__(QtAudioBackend)
    backend.QMediaPlayer = Player
    backend.QUrl = Url
    backend.parent, backend.output, backend.player = None, None, None
    backend._closed = False
    backend._retired_players = {}
    backend.devices = SimpleNamespace(audioOutputsChanged=Signal(),
                                      deleteLater=lambda: calls.append(("devices", "delete")))
    backend.event = lambda *_: None
    backend.has_device = lambda: True
    backend._replace_player()
    return backend, calls


def test_qt_source_switch_discards_delayed_prior_player_events(follower, qt_backend):
    c, fake, messages, _ = follower
    backend, calls = qt_backend
    backend.event = c.media_event
    fake.load = backend.load
    c.configure(True, 0.7, False)
    c.sync(AudioTarget("a.mp4", 0, 1), True)
    first = backend.player
    c.sync(AudioTarget("b.mp4", 1, 1), True)
    second = backend.player
    c.sync(AudioTarget("a.mp4", 2, 1), True)
    current = backend.player
    before = list(fake.calls), list(messages)
    for old in (first, second):
        assert old.source == ""
        assert calls.index((old, "source", "")) < calls.index((old, "delete"))
        assert id(old) in backend._retired_players
        for status in (1, 2, 3, 4):
            old.mediaStatusChanged.callback(status)
        old.errorOccurred.callback(None, "delayed old error")
        old.destroyed.callback()
        assert id(old) not in backend._retired_players
    assert (fake.calls, messages) == before
    assert not c.ready and not c.failed and not c.ended
    current.mediaStatusChanged.callback(1)
    assert c.ready and fake.calls[-1] == ("play",)
    assert ("seek", 2) in fake.calls


def test_qt_seek_after_eof_discards_queued_same_player_end(follower, qt_backend):
    c, fake, messages, _ = follower
    backend, _ = qt_backend
    backend.event = c.media_event
    fake.load = backend.load
    c, fake = ready(follower)
    player = backend.player
    player.status = player.MediaStatus.EndOfMedia
    player.mediaStatusChanged.callback(player.status)
    assert c.ended
    c.sync(AudioTarget("clip.mp4", 0, 1), True, force=True)
    assert not c.ended and fake.calls[-1] == ("play",)
    player.status = player.MediaStatus.LoadedMedia
    before = list(fake.calls), list(messages)
    player.mediaStatusChanged.callback(player.MediaStatus.EndOfMedia)
    assert (fake.calls, messages) == before
    assert not c.ended


@pytest.mark.parametrize("operation,playing,force", [
    ("pause", True, True), ("volume", True, True), ("rate", True, True),
    ("seek", True, True), ("play", True, True), ("pause", False, False),
])
def test_qt_reentrant_error_aborts_transport_sync(follower, qt_backend, operation, playing, force):
    c, fake, messages, _ = follower
    backend, _ = qt_backend
    backend.event = c.media_event
    fake.load = backend.load
    c, fake = ready(follower)
    player = backend.player
    fake.calls.clear()
    messages.clear()
    original = getattr(fake, operation)

    def fail(*args):
        fake.calls.append((operation, *args))
        if playing or fake.calls.count(("pause",)) == 2:
            player.errorOccurred.callback(None, f"{operation} failed")

    setattr(fake, operation, fail)
    c.sync(AudioTarget("clip.mp4", 4, 1), playing, force=force)

    assert c.failed
    assert fake.calls[-1] == ("stop",)
    assert messages == [
        f"Preview audio unavailable: {operation} failed. Video continues silently."
    ]
    if operation != "play":
        assert ("play",) not in fake.calls
    if operation == "rate":
        assert not any(call[0] == "seek" for call in fake.calls)

    setattr(fake, operation, original)
    c.sync(AudioTarget("clip.mp4", 6, 1), playing, force=True, retry=True)
    backend.player.mediaStatusChanged.callback(backend.player.MediaStatus.LoadedMedia)
    assert not c.failed
    assert ("seek", 6) in fake.calls
    assert fake.calls[-1] == (("play",) if playing else ("pause",))


def test_release_clips_clears_all_player_sources_before_temp_cleanup(video, qt_backend):
    from types import SimpleNamespace
    widget, _ = video
    backend, calls = qt_backend
    backend.output = SimpleNamespace(deleteLater=lambda: calls.append(("output", "delete")))
    clip = add_clip(widget, "recovered.mp4")
    controller = PreviewAudioController(backend, widget._preview_audio_status.setText)
    widget._preview_audio = controller
    controller.configure(True, 0.7, False)
    controller.sync(AudioTarget(clip.path, 0, 1), False)
    first = backend.player
    backend.load("other-recovered.mp4")
    current = backend.player

    def cleanup():
        calls.append(("temp", "cleanup"))
        assert controller.closed and controller.path is None
        assert backend.player is None
        for player in (first, current):
            assert player.source == ""
            assert calls.index((player, "stop")) < calls.index((player, "source", ""))
            assert calls.index((player, "source", "")) < calls.index((player, "output", None))
            assert calls.index((player, "output", None)) < calls.index((player, "delete"))
            assert calls.index((player, "delete")) < calls.index(("temp", "cleanup"))

    widget._reset_video_history = cleanup
    widget._release_clips()
    before = list(calls), widget._preview_audio_status.text()
    for player in (first, current):
        for status in (1, 2, 3, 4):
            player.mediaStatusChanged.callback(status)
        player.errorOccurred.callback(None, "late error")
    backend._devices_changed()
    backend.load("must-not-reopen.mp4")
    controller.close()
    backend.close()
    assert (calls, widget._preview_audio_status.text()) == before
    for player in list(backend._retired_players.values()):
        player.destroyed.callback()
    assert not backend._retired_players


def test_single_frame_edit_pauses_audio_with_video(video):
    widget, backend = video
    add_clip(widget)
    widget._preview_audio_check.setChecked(True)
    widget._btn_play.setChecked(True)
    backend.event("ready")
    widget._trim_end_slider.setValue(0)
    assert not widget._is_playing
    assert backend.calls[-1] == ("pause",)


@pytest.mark.parametrize("probe,selected", [({}, None), ({"audio_stream_count": 2}, 2),
    ({"audio_stream_count": 1, "audio_stream_index": 1}, 3)])
def test_unverified_or_wrong_stream_never_loads(video, probe, selected):
    widget, backend = video
    clip = add_clip(widget)
    clip.preview_audio_probe = probe
    clip.preferred_audio_stream_index = selected
    widget._preview_audio_check.setChecked(True)
    widget._btn_play.setChecked(True)
    assert not any(call[0] == "load" and call[1] for call in backend.calls)
    assert "unavailable" in widget._preview_audio_status.text()


def test_recovered_media_uses_effective_path_and_probe(video):
    widget, backend = video
    clip = add_clip(widget, "recovered.mp4")
    clip.source_path = "original.mkv"
    clip.source_probe = {"audio_stream_count": 3, "audio_stream_index": 8}
    clip.preferred_audio_stream_index = 8
    clip.preview_audio_probe = {"audio_stream_count": 1, "audio_stream_index": 1}
    widget._preview_audio_check.setChecked(True)
    widget._btn_play.setChecked(True)
    backend.event("ready")
    assert ("load", "recovered.mp4") in backend.calls
    assert ("load", "original.mkv") not in backend.calls


def test_unsupported_rate_and_failed_recovery_stream_are_explicit(video):
    widget, _ = video
    clip = add_clip(widget)
    clip.fps = 1
    assert not clip_audio_target(clip, 0, 60).available
    clip.fps = 25
    clip.preview_audio_selection_valid = False
    target = clip_audio_target(clip, 0, 25)
    assert not target.available
    assert "selected audio stream" in target.reason


def test_generated_media_qt_async_load_seek_and_shutdown(app, tmp_path):
    from src.ui import video_tool as vt
    from src.ui.video_preview_audio import QtAudioBackend
    from PyQt6.QtCore import QEventLoop, QTimer
    executable = vt._get_ffmpeg_exe()
    if not executable:
        pytest.skip("FFmpeg unavailable")
    media = tmp_path / "preview.mp4"
    subprocess.run([executable, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=s=16x16:r=25:d=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(media)],
                   check=True, capture_output=True, timeout=20)
    from PyQt6.QtCore import QObject
    owner = QObject()
    try:
        backend = QtAudioBackend(owner)
    except ImportError as exc:
        pytest.skip(f"QtMultimedia runtime unavailable: {exc}")
    # Decode/load even on headless runners; no claim of audible hardware output.
    loop = QEventLoop()
    events = []
    backend.event = lambda event, detail="": (events.append((event, detail)), loop.quit())
    backend.load(str(media))
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    assert events, "Qt media loading did not respond asynchronously"
    assert events[0][0] == "ready", events
    assert backend.track_count() == 1
    assert backend.seekable()
    backend.seek(0.2)
    assert backend.position() == pytest.approx(0.2, abs=0.02)
    backend.volume(0.0, True)
    backend.stop()
    backend.load(None)
    backend.close()
