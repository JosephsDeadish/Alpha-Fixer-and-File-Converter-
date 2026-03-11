"""
SoundEngine – optional click-sound effects for the application.

Strategy (in order of preference):
  1. PyQt6.QtMultimedia.QSoundEffect  – zero extra dependencies, async
  2. Subprocess call to paplay/aplay/afplay/winsound  – platform CLI fallback

If neither works, sound is silently disabled.

The default "click" sound is a short synthetic sine-wave blip generated at
startup (no external audio assets needed).  Users can also supply their own
.wav file path in Settings.
"""
import logging
import math
import os
import struct
import sys
import tempfile
import wave

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QAbstractButton

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WAV generator helpers
# ---------------------------------------------------------------------------

def _write_wav(samples: list, sample_rate: int = 22050) -> str:
    """Write a list of 16-bit PCM samples to a temp WAV file and return the path."""
    samples = [max(-32768, min(32767, s)) for s in samples]
    tf = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    path = tf.name
    tf.close()
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def _make_click_wav(freq: int = 880, duration: float = 0.06,
                    sample_rate: int = 22050) -> str:
    """Generate a short sine-wave blip and write it to a temp WAV file."""
    n = int(sample_rate * duration)
    samples = [
        int(28000 * math.sin(2 * math.pi * freq * i / sample_rate)
            * math.exp(-i / sample_rate * 45))
        for i in range(n)
    ]
    return _write_wav(samples, sample_rate)


def _make_success_wav(sample_rate: int = 22050) -> str:
    """Cheerful two-note chime: C5 → E5 (ascending major third)."""
    notes = [(523, 0.08), (659, 0.12)]   # C5, E5
    samples: list = []
    for freq, dur in notes:
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * 20)
            samples.append(int(22000 * math.sin(2 * math.pi * freq * i / sample_rate) * env))
    return _write_wav(samples, sample_rate)


def _make_error_wav(sample_rate: int = 22050) -> str:
    """Low descending buzz (E3 → C3)."""
    notes = [(165, 0.06), (131, 0.10)]   # E3, C3
    samples: list = []
    for freq, dur in notes:
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * 18)
            val = int(24000 * math.sin(2 * math.pi * freq * i / sample_rate) * env)
            samples.append(val)
    return _write_wav(samples, sample_rate)


def _make_unlock_wav(sample_rate: int = 22050) -> str:
    """Short ascending arpeggio fanfare: C4–E4–G4–C5."""
    notes = [(262, 0.07), (330, 0.07), (392, 0.07), (523, 0.18)]
    samples: list = []
    for freq, dur in notes:
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * 12)
            samples.append(int(26000 * math.sin(2 * math.pi * freq * i / sample_rate) * env))
    return _write_wav(samples, sample_rate)



# ---------------------------------------------------------------------------
# Click-filter that plays a sound on every QAbstractButton press
# ---------------------------------------------------------------------------

class _ButtonClickFilter(QObject):
    def __init__(self, engine: "SoundEngine"):
        super().__init__(engine)
        self._engine = engine

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and isinstance(obj, QAbstractButton)
        ):
            self._engine.play_click()
        return False


# ---------------------------------------------------------------------------
# SoundEngine
# ---------------------------------------------------------------------------

class SoundEngine(QObject):
    """Manages sound playback with optional Qt Multimedia backend.

    Provides four synthetic sounds generated at startup (no external assets):
      click   – short blip played on every button press
      success – cheerful two-note chime, played after a successful batch
      error   – descending buzz, played after a batch with errors
      unlock  – ascending arpeggio fanfare, played when a theme unlocks
    """

    def __init__(self, settings, parent: QObject = None):
        super().__init__(parent)
        self._settings = settings
        self._effect = None          # QSoundEffect for click (may be None)
        self._click_wav: str = ""
        self._success_wav: str = ""
        self._error_wav: str = ""
        self._unlock_wav: str = ""
        self._filter: _ButtonClickFilter | None = None
        self._setup()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        try:
            self._click_wav   = _make_click_wav()
            self._success_wav = _make_success_wav()
            self._error_wav   = _make_error_wav()
            self._unlock_wav  = _make_unlock_wav()
        except Exception as exc:
            logger.warning("Could not generate sound WAVs: %s", exc)
            return

        # Try Qt Multimedia first
        try:
            from PyQt6.QtMultimedia import QSoundEffect
            from PyQt6.QtCore import QUrl
            self._effect = QSoundEffect(self)
            self._effect.setSource(QUrl.fromLocalFile(self._click_wav))
            self._effect.setVolume(0.45)
            logger.info("SoundEngine: using QSoundEffect")
        except Exception as exc:
            self._effect = None
            logger.info("SoundEngine: QSoundEffect unavailable (%s), using subprocess fallback", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install_on_app(self, app: QObject) -> None:
        """Install event filter so every button click triggers a sound."""
        self._filter = _ButtonClickFilter(self)
        app.installEventFilter(self._filter)

    def play_click(self) -> None:
        """Play the click sound (respects the sound_enabled setting)."""
        if not self._settings.get("sound_enabled", False):
            return
        custom = self._settings.get("click_sound_path", "").strip()
        wav_path = custom if (custom and os.path.isfile(custom)) else self._click_wav
        if not wav_path:
            return
        self._play(wav_path)

    def play_success(self) -> None:
        """Play the success chime after a batch completes cleanly."""
        if not self._settings.get("sound_enabled", False):
            return
        if self._success_wav:
            self._play(self._success_wav)

    def play_error(self) -> None:
        """Play the error buzz when a batch finishes with failures."""
        if not self._settings.get("sound_enabled", False):
            return
        if self._error_wav:
            self._play(self._error_wav)

    def play_unlock(self) -> None:
        """Play the unlock fanfare when a hidden theme is revealed."""
        if not self._settings.get("sound_enabled", False):
            return
        if self._unlock_wav:
            self._play(self._unlock_wav)

    # ------------------------------------------------------------------
    # Internal playback
    # ------------------------------------------------------------------

    def _play(self, wav_path: str) -> None:
        if self._effect is not None:
            try:
                from PyQt6.QtCore import QUrl
                current_src = self._effect.source().toLocalFile()
                if current_src != wav_path:
                    self._effect.setSource(QUrl.fromLocalFile(wav_path))
                if not self._effect.isPlaying():
                    self._effect.play()
            except Exception as exc:
                logger.debug("QSoundEffect play failed: %s", exc)
        else:
            self._play_subprocess(wav_path)

    # ------------------------------------------------------------------
    # Subprocess fallback
    # ------------------------------------------------------------------

    def _play_subprocess(self, path: str) -> None:
        import subprocess
        try:
            if sys.platform == "win32":
                import winsound  # type: ignore
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", path],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            else:
                for cmd in ["paplay", "aplay"]:
                    try:
                        subprocess.Popen([cmd, path],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                        return
                    except FileNotFoundError:
                        continue
        except Exception as exc:
            logger.debug("subprocess sound fallback failed: %s", exc)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Remove temp WAV files on application exit."""
        for attr in ("_click_wav", "_success_wav", "_error_wav", "_unlock_wav"):
            path = getattr(self, attr, "")
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
