"""Bounded, asynchronous audio follower for the Video Builder's visual clock."""
from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(frozen=True)
class AudioTarget:
    path: str
    seconds: float
    rate: float
    available: bool = True
    reason: str = ""


def clip_audio_target(clip, frame: float, output_fps: float) -> AudioTarget:
    """Use continuous export audio timing, including a split's original span."""
    origin, end, count = clip._split_audio_range()
    split = clip._split_render_range()
    offset = split[1] if split is not None else 0
    step = (end + 1 - origin) / max(0.1, clip.fps) / max(1, count)
    probe = getattr(clip, "preview_audio_probe", None) or {}
    available = clip.clip_type == "video" and clip.has_audio
    reason = "Silent segment."
    if available and int(probe.get("audio_stream_count") or 0) != 1:
        available = False
        reason = "Preview audio unavailable: selected stream cannot be verified on the loaded media."
    if available and not getattr(clip, "preview_audio_selection_valid", True):
        available = False
        reason = "Preview audio unavailable: loaded media does not match the selected audio stream."
    selected = getattr(clip, "preferred_audio_stream_index", None)
    if available and clip.path == clip.source_path and selected is not None and selected != probe.get("audio_stream_index"):
        available = False
        reason = "Preview audio unavailable: loaded media does not match the selected audio stream."
    rate = step * max(0.1, output_fps)
    if available and not 0.1 <= rate <= 10.0:
        available = False
        reason = "Preview audio unavailable at this timeline rate (supported: 0.1–10×)."
    return AudioTarget(clip.path, origin / max(0.1, clip.fps) + (offset + frame) * step,
                       rate, available, reason)


class PreviewAudioController:
    """Backend-neutral follower; the video clock never waits for audio."""

    def __init__(self, backend, status, clock=time.monotonic):
        self.backend = backend
        self.status = status
        self.clock = clock
        self.enabled = False
        self.muted = False
        self.volume = 0.7
        self.target = None
        self.playing = False
        self.path = None
        self.ready = False
        self.failed = False
        self.ended = False
        self.closed = False
        self.loaded_at = 0.0
        self.device_lost_at = None
        self.last_seek = float("-inf")
        backend.event = self.media_event

    def configure(self, enabled, volume, muted):
        self.enabled, self.volume, self.muted = enabled, volume, muted
        self.sync(self.target, self.playing, force=True, retry=True)

    def reset(self):
        self.path = None
        self.target = None
        self.ready = self.failed = self.ended = False
        self.device_lost_at = None
        self.backend.stop()
        self.backend.load(None)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.reset()
        close = getattr(self.backend, "close", None)
        if close is not None:
            close()

    def media_event(self, event, detail=""):
        if self.closed:
            return
        if event == "device":
            self.sync(self.target, self.playing, force=True, retry=True)
            return
        if self.path is None:
            return
        if event == "ready":
            if self.failed or self.ready:
                return
            self.ready = True
            if self.backend.track_count() != 1:
                self.media_event("error", "The loaded media does not expose exactly one verified audio track.")
                return
            if not self.backend.seekable():
                self.media_event("error", "The loaded media is not seekable.")
                return
            self.sync(self.target, self.playing, force=True)
        elif event == "ended":
            self.ended = True
            self.backend.pause()
            self.status("Preview audio: source ended; video continues in silence.")
        elif event == "error":
            self.failed = True
            self.backend.stop()
            self.status(f"Preview audio unavailable: {detail or 'media backend error'}. Video continues silently.")

    def sync(self, target, playing, force=False, retry=False):
        self.target, self.playing = target, playing
        if self.closed:
            return
        if not self.enabled or target is None or not target.available:
            if self.path is not None:
                self.reset()
                self.target = target
            self.status("Preview audio off (export audio unchanged)." if not self.enabled else
                        (target.reason if target is not None else "Preview audio: no clip."))
            return
        if not self.backend.has_device():
            if self.device_lost_at is None:
                self.device_lost_at = self.clock()
            self.backend.pause()
            self.status("Preview audio unavailable: no output device. Video continues silently.")
            return
        if self.device_lost_at is not None:
            self.loaded_at += self.clock() - self.device_lost_at
            self.device_lost_at = None
        # Automatic wraps and clip boundaries force seeks, not decoder retries.
        if self.path != target.path or (self.failed and retry):
            self.backend.stop()
            self.path = target.path
            self.ready = self.failed = self.ended = False
            self.loaded_at = self.clock()
            self.backend.load(target.path)
        if self.failed:
            return
        if not self.ready:
            if self.clock() - self.loaded_at > 8.0:
                self.media_event("error", "media loading timed out")
            else:
                self.status("Preview audio: loading; video continues.")
            return
        duration = self.backend.duration()
        if duration > 0 and target.seconds >= duration:
            self.backend.pause()
            self.status("Preview audio: source ended; video continues in silence.")
            return
        if self.ended and not force:
            return
        if force:
            self.ended = False
        if force or not playing:
            self.backend.pause()
            if self.failed:
                return
        # Qt transport calls can emit errorOccurred synchronously. Do not resume
        # the player or overwrite the failure status after that callback stops it.
        self.backend.volume(self.volume, self.muted or not playing)
        if self.failed:
            return
        self.backend.rate(target.rate)
        if self.failed:
            return
        now = self.clock()
        drift = abs(self.backend.position() - target.seconds)
        if force or (drift > max(0.12, 0.12 * target.rate) and now - self.last_seek >= 0.35):
            self.backend.seek(target.seconds)
            if self.failed:
                return
            self.last_seek = now
        if playing and not self.muted and self.volume > 0:
            self.backend.play()
            if self.failed:
                return
            self.status("Preview audio on (export audio unchanged).")
        else:
            self.backend.pause()
            if self.failed:
                return
            self.status("Preview audio: muted." if self.muted or self.volume == 0 else "Preview audio: paused.")


class QtAudioBackend:
    """QtMultimedia owns loading/decoding; no extraction or event pumping."""

    def __init__(self, parent):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
        self.QUrl = QUrl
        self.QMediaPlayer = QMediaPlayer
        self.parent = parent
        self.event = lambda *_: None
        self.devices = QMediaDevices(parent)
        self.output = QAudioOutput(parent)
        self.player = None
        self._closed = False
        self._retired_players = {}
        self._replace_player()
        self.devices.audioOutputsChanged.connect(self._devices_changed)

    def _retire_player(self):
        old, self.player = self.player, None
        if old is not None:
            key = id(old)
            self._retired_players[key] = old
            old.destroyed.connect(lambda *_: self._retired_players.pop(key, None))
            old.stop()
            # stop()/deleteLater() alone can leave recovered media locked until
            # the next event-loop turn, after clip cleanup has already run.
            old.setSource(self.QUrl())
            old.setAudioOutput(None)
            old.deleteLater()

    def _replace_player(self):
        self._retire_player()
        player = self.QMediaPlayer(self.parent)
        self.player = player
        player.setAudioOutput(self.output)
        # Queued notifications retain their originating player, even for A→B→A.
        player.mediaStatusChanged.connect(lambda status: self._status(status, player))
        player.errorOccurred.connect(
            lambda _error, message: self._error(player, message))

    def _error(self, player, message):
        if player is self.player:
            self.event("error", message)

    def _status(self, status, player):
        if player is not self.player:
            return
        statuses = self.QMediaPlayer.MediaStatus
        if status in (statuses.LoadedMedia, statuses.BufferedMedia):
            self.event("ready")
        elif status == statuses.EndOfMedia:
            # A queued EOF notification can arrive after a seek on this player.
            if player.mediaStatus() == statuses.EndOfMedia:
                self.event("ended")
        elif status == statuses.InvalidMedia:
            self.event("error", self.player.errorString())

    def _devices_changed(self):
        if self._closed:
            return
        self.output.setDevice(self.devices.defaultAudioOutput())
        self.event("device")

    def has_device(self):
        return not self.devices.defaultAudioOutput().isNull()

    def load(self, path):
        if self._closed:
            return
        self._replace_player()
        self.player.setSource(self.QUrl.fromLocalFile(str(path)) if path else self.QUrl())

    def track_count(self):
        return len(self.player.audioTracks())

    def seekable(self):
        return self.player.isSeekable()

    def duration(self):
        return self.player.duration() / 1000.0

    def position(self):
        return self.player.position() / 1000.0

    def seek(self, seconds):
        self.player.setPosition(round(seconds * 1000))

    def rate(self, rate):
        self.player.setPlaybackRate(rate)

    def volume(self, volume, muted):
        self.output.setVolume(volume)
        self.output.setMuted(muted)

    def play(self):
        if self.player.playbackState() != self.QMediaPlayer.PlaybackState.PlayingState:
            self.player.play()

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.event = lambda *_: None
        self.devices.audioOutputsChanged.disconnect(self._devices_changed)
        self._retire_player()
        self.output.deleteLater()
        self.devices.deleteLater()
