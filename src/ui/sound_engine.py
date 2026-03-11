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
    """Write a list of 16-bit PCM samples to a temp WAV file and return the path.

    *samples* should be non-empty; if an empty list is passed a warning is
    logged and a single silent frame is written so the caller always gets a
    valid WAV file back.
    """
    if not samples:
        logger.warning("_write_wav called with empty samples list — writing silent frame")
        samples = [0]
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


def _make_theme_click_wav(profile: str, sample_rate: int = 22050) -> str:
    """Generate a per-theme click sound based on *profile* name.

    Profiles (and their intended themes):
      soft    – gentle high chime (panda, sakura, fairy, spring, bubblegum)
      hard    – sharp low thud (gore, goth, skeleton, zombie, dragon, blood)
      bright  – crisp mid ping (neon, rainbow, candy, toxic, thunder, alien)
      dark    – deep hollow pulse (bat, galaxy, abyssal, space_cat, nebula)
      warm    – organic wood-block (otter, ocean, mermaid, sunset, forest)
      icy     – crystalline tinkle (ice, arctic, cyber_otter)
      sparkle – fast ascending twinkle (magic, rose, gold, pancake, noodle)
    """
    samples: list = []
    if profile == "hard":
        # Low thud with noise-like distortion
        freq, dur, decay = 180, 0.08, 30
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * decay)
            s = math.sin(2 * math.pi * freq * i / sample_rate)
            s += 0.4 * math.sin(2 * math.pi * freq * 2.1 * i / sample_rate)
            samples.append(int(26000 * s * env))
    elif profile == "bright":
        # Crisp mid-range ping
        freq, dur, decay = 1200, 0.05, 55
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * decay)
            samples.append(int(24000 * math.sin(2 * math.pi * freq * i / sample_rate) * env))
    elif profile == "dark":
        # Deep hollow pulse
        freq, dur, decay = 220, 0.10, 18
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * decay)
            s = math.sin(2 * math.pi * freq * i / sample_rate) * 0.7
            s += 0.3 * math.sin(2 * math.pi * freq * 0.5 * i / sample_rate)
            samples.append(int(24000 * s * env))
    elif profile == "warm":
        # Organic wood-block: quick attack, gentle decay
        freq, dur, decay = 440, 0.07, 40
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * decay)
            s = math.sin(2 * math.pi * freq * i / sample_rate)
            s += 0.25 * math.sin(2 * math.pi * freq * 3 * i / sample_rate)
            samples.append(int(22000 * s * env))
    elif profile == "icy":
        # Crystalline high tinkle with fast ring-off
        freq, dur, decay = 1760, 0.07, 25
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * decay)
            samples.append(int(20000 * math.sin(2 * math.pi * freq * i / sample_rate) * env))
    elif profile == "sparkle":
        # Two fast ascending notes
        notes = [(880, 0.04), (1320, 0.06)]
        for freq, dur in notes:
            n = int(sample_rate * dur)
            for i in range(n):
                env = math.exp(-i / sample_rate * 35)
                samples.append(int(20000 * math.sin(2 * math.pi * freq * i / sample_rate) * env))
    else:  # soft / default
        freq, dur, decay = 880, 0.06, 45
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * decay)
            samples.append(int(22000 * math.sin(2 * math.pi * freq * i / sample_rate) * env))
    return _write_wav(samples, sample_rate)


# Map theme name → sound profile
_THEME_SOUND_PROFILES: dict[str, str] = {
    # Preset themes
    "Panda Dark": "soft", "Panda Light": "soft", "Neon Panda": "bright",
    "Gore": "hard", "Bat Cave": "dark", "Rainbow Chaos": "bright",
    "Otter Cove": "warm", "Galaxy": "dark", "Galaxy Otter": "dark",
    "Goth": "hard", "Volcano": "hard", "Arctic": "icy",
    "Fairy Garden": "soft", "Mermaid": "warm", "Shark Bait": "warm",
    "Alien": "bright", "Noodle": "sparkle", "Pancake": "sparkle",
    # Hidden themes
    "Secret Skeleton": "hard", "Secret Sakura": "soft",
    "Deep Ocean": "warm", "Blood Moon": "hard", "Ice Cave": "icy",
    "Cyber Otter": "icy", "Toxic Neon": "bright", "Lava Cave": "hard",
    "Sunset Beach": "warm", "Midnight Forest": "warm",
    "Candy Land": "bright", "Zombie Apocalypse": "hard",
    "Dragon Fire": "hard", "Bubblegum": "soft", "Thunder Storm": "bright",
    "Rose Gold": "soft", "Space Cat": "dark", "Magic Mushroom": "sparkle",
    "Abyssal Void": "dark", "Spring Bloom": "soft",
    "Gold Rush": "sparkle", "Nebula": "dark",
}


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

    Provides synthetic sounds generated at startup (no external assets):
      click         – short blip played on every button press
      theme_click   – per-theme click sound variant (7 profiles)
      success       – cheerful two-note chime, played after a successful batch
      error         – descending buzz, played after a batch with errors
      unlock        – ascending arpeggio fanfare, played when a theme unlocks

    When 'use_theme_sound' is enabled in settings and a theme is active,
    play_click() uses the theme-appropriate sound profile instead of the
    generic default click.
    """

    def __init__(self, settings, parent: QObject = None):
        super().__init__(parent)
        self._settings = settings
        self._effect = None          # QSoundEffect for click (may be None)
        self._click_wav: str = ""
        self._success_wav: str = ""
        self._error_wav: str = ""
        self._unlock_wav: str = ""
        # Per-profile click WAVs keyed by profile name
        self._theme_click_wavs: dict[str, str] = {}
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
            # Generate one WAV per sound profile (7 profiles)
            for profile in ("soft", "hard", "bright", "dark", "warm", "icy", "sparkle"):
                self._theme_click_wavs[profile] = _make_theme_click_wav(profile)
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

    def set_theme(self, theme_name: str) -> None:
        """Called when the active theme changes so the click sound updates."""
        pass  # No pre-loading needed — profile is resolved at play time.

    def play_click(self) -> None:
        """Play the click sound (respects the sound_enabled setting).

        If 'use_theme_sound' is enabled the click uses the per-theme profile;
        otherwise a custom WAV path or the generic default is used.
        """
        if not self._settings.get("sound_enabled", False):
            return

        # Theme sound path
        if self._settings.get("use_theme_sound", False):
            try:
                theme = self._settings.get_theme()
                theme_name = theme.get("name", "")
                profile = _THEME_SOUND_PROFILES.get(theme_name, "soft")
                wav_path = self._theme_click_wavs.get(profile, self._click_wav)
            except Exception:
                wav_path = self._click_wav
        else:
            # Custom user-supplied WAV or generic default
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
        all_wavs = [self._click_wav, self._success_wav,
                    self._error_wav, self._unlock_wav]
        all_wavs.extend(self._theme_click_wavs.values())
        for path in all_wavs:
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
