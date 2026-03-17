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
import random as _random
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
        data = [0]
    else:
        data = [max(-32768, min(32767, s)) for s in samples]
    tf = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    path = tf.name
    tf.close()
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(data)}h", *data))
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
      growl   – low rumbling growl (gore, zombie, blood moon)
      bubble  – watery bubble pop (ocean, mermaid, deep ocean, coral reef)
      chirp   – bright bird/fairy chirp (fairy garden, spring bloom, sakura)
      crunch  – bone-dry crunch (skeleton, goth, abyssal void)
      purr    – warm rhythmic purr (pancake, otter)
      meow    – rising-then-falling pitch glide with vibrato (space cat)
      roar    – low harmonic burst with slow attack (dragon fire)
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
    elif profile == "growl":
        # Low rumbling growl — two close low frequencies creating a beating effect
        n = int(sample_rate * 0.12)
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 14) * (1 - math.exp(-t * 60))
            s = math.sin(2 * math.pi * 90 * t)
            s += 0.7 * math.sin(2 * math.pi * 95 * t)    # beat frequency ~ 5 Hz
            s += 0.35 * math.sin(2 * math.pi * 180 * t)  # second harmonic
            samples.append(int(24000 * s * env))
    elif profile == "bubble":
        # Watery bubble pop — descending pitch glide (high→low pop)
        n = int(sample_rate * 0.09)
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 30)
            freq = 900 - 700 * (t / 0.09)  # glide 900 Hz → 200 Hz
            samples.append(int(22000 * math.sin(2 * math.pi * freq * t) * env))
    elif profile == "chirp":
        # Bird/fairy chirp — fast ascending frequency glide
        n = int(sample_rate * 0.07)
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 25) * (1 - math.exp(-t * 100))
            freq = 800 + 1400 * (t / 0.07)  # glide 800 Hz → 2200 Hz
            samples.append(int(20000 * math.sin(2 * math.pi * freq * t) * env))
    elif profile == "crunch":
        # Bone-dry crunch — short noise burst filtered with a sine carrier
        n = int(sample_rate * 0.06)
        rng = _random.Random(42)  # deterministic noise
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 50) * (1 - math.exp(-t * 200))
            noise = rng.uniform(-1, 1)
            carrier = math.sin(2 * math.pi * 300 * t)
            samples.append(int(22000 * (0.6 * noise + 0.4 * carrier) * env))
    elif profile == "purr":
        # Warm rhythmic purr — amplitude-modulated low tone (throat vibration)
        n = int(sample_rate * 0.14)
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 10) * (1 - math.exp(-t * 40))
            mod = 0.5 + 0.5 * math.sin(2 * math.pi * 28 * t)  # 28 Hz purr rate
            carrier = math.sin(2 * math.pi * 120 * t)
            carrier += 0.4 * math.sin(2 * math.pi * 240 * t)
            samples.append(int(20000 * carrier * mod * env))
    elif profile == "meow":
        # Cat meow — rising-then-falling pitch glide with vibrato and overtone.
        # Approximates the classic "mee-oow" shape: 400 Hz → 900 Hz → 600 Hz
        # over 150 ms with an 8 Hz vibrato for the natural cat-voice flutter.
        n = int(sample_rate * 0.15)
        dur = 0.15
        phase = 0.0
        for i in range(n):
            t = i / sample_rate
            # Pitch contour: rises in first 45 % then falls back
            if t < dur * 0.45:
                freq = 400.0 + 500.0 * (t / (dur * 0.45))      # 400 → 900 Hz
            else:
                freq = 900.0 - 300.0 * ((t - dur * 0.45) / (dur * 0.55))  # 900 → 600 Hz
            freq += 20.0 * math.sin(2 * math.pi * 8 * t)  # 8 Hz vibrato ±20 Hz
            env = math.exp(-t * 8.0) * (1.0 - math.exp(-t * 60.0))
            phase += 2 * math.pi * freq / sample_rate
            s = math.sin(phase) + 0.3 * math.sin(2 * phase)  # add 2nd harmonic
            samples.append(int(18000 * s * env / 1.3))  # 1.3 = peak of 1+0.3
    elif profile == "roar":
        # Dragon/lion roar — rich harmonic burst with slow attack and rumble.
        # Six partials (fundamental 80 Hz plus harmonics + slight detuning for
        # beating) with an exponential onset create a dramatic roaring click.
        n = int(sample_rate * 0.18)
        for i in range(n):
            t = i / sample_rate
            env = (1.0 - math.exp(-t * 30.0)) * math.exp(-t * 8.0)
            s = (math.sin(2 * math.pi * 80 * t)
                 + 0.85 * math.sin(2 * math.pi * 160 * t)
                 + 0.65 * math.sin(2 * math.pi * 240 * t)
                 + 0.45 * math.sin(2 * math.pi * 320 * t)
                 + 0.25 * math.sin(2 * math.pi * 400 * t)
                 + 0.40 * math.sin(2 * math.pi * 85 * t))  # detuned for rumble
            # 3.6 = 1 + 0.85 + 0.65 + 0.45 + 0.25 + 0.40 (sum of coefficients)
            samples.append(int(22000 * s * env / 3.6))
    elif profile == "bark":
        # Dog bark — sharp attack with mid-frequency burst, fast decay.
        # Two overlapping partials (200 Hz + 350 Hz) with a rapid percussive
        # onset simulate a short friendly bark.
        n = int(sample_rate * 0.10)
        for i in range(n):
            t = i / sample_rate
            env = (1.0 - math.exp(-t * 80.0)) * math.exp(-t * 18.0)
            s = (math.sin(2 * math.pi * 200 * t)
                 + 0.7 * math.sin(2 * math.pi * 350 * t)
                 + 0.3 * math.sin(2 * math.pi * 500 * t))
            # 2.0 = 1 + 0.7 + 0.3 (sum of coefficients)
            samples.append(int(22000 * s * env / 2.0))
    elif profile == "howl":
        # Dog howl — long ascending then sustained pitch glide, wolf-like
        n = int(sample_rate * 0.22)
        phase = 0.0
        for i in range(n):
            t = i / sample_rate
            # rise 200→500 Hz over first 60%, then sustain with slight vibrato
            if t < 0.13:
                freq = 200.0 + 2307.7 * t   # 200→500 Hz
            else:
                freq = 500.0 + 15.0 * math.sin(2 * math.pi * 5 * t)
            env = (1.0 - math.exp(-t * 20.0)) * math.exp(-t * 4.5)
            phase += 2 * math.pi * freq / sample_rate
            s = math.sin(phase) + 0.3 * math.sin(2 * phase)
            samples.append(int(20000 * s * env / 1.3))
    elif profile == "hiss":
        # Cat hiss — breathy noise burst with a sharp sibilant character
        rng = _random.Random(7)
        n = int(sample_rate * 0.12)
        for i in range(n):
            t = i / sample_rate
            env = (1.0 - math.exp(-t * 80.0)) * math.exp(-t * 22.0)
            noise = rng.uniform(-1, 1)
            carrier = math.sin(2 * math.pi * 2200 * t) * 0.3
            samples.append(int(20000 * (0.7 * noise + 0.3 * carrier) * env))
    elif profile == "rock1":
        # Goth/rock electric guitar power-chord stab — low distorted thud
        n = int(sample_rate * 0.14)
        rng = _random.Random(11)
        for i in range(n):
            t = i / sample_rate
            env = (1.0 - math.exp(-t * 40.0)) * math.exp(-t * 12.0)
            # E2 power chord (82 Hz root + 123 Hz fifth)
            s = math.sin(2 * math.pi * 82 * t)
            s += 0.8 * math.sin(2 * math.pi * 123 * t)
            s += 0.5 * math.sin(2 * math.pi * 164 * t)
            # Add slight distortion via soft-clip
            s = max(-1.2, min(1.2, s * 1.4))
            s += 0.15 * rng.uniform(-1, 1)  # amp grit
            samples.append(int(22000 * s * env / 2.0))
    elif profile == "rock2":
        # Goth/rock snare hit — tight percussive crack with noise transient
        n = int(sample_rate * 0.10)
        rng = _random.Random(13)
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 35.0)
            tone = math.sin(2 * math.pi * 200 * t) + 0.5 * math.sin(2 * math.pi * 280 * t)
            noise = rng.uniform(-1, 1)
            s = 0.45 * tone + 0.55 * noise
            samples.append(int(24000 * s * env))
    elif profile == "rock3":
        # Goth/rock bass string pluck — rich low thump with harmonic decay
        n = int(sample_rate * 0.16)
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 14.0) * (1.0 - math.exp(-t * 60.0))
            # A1 bass note (55 Hz) with harmonics
            s = (math.sin(2 * math.pi * 55 * t)
                 + 0.7 * math.sin(2 * math.pi * 110 * t)
                 + 0.4 * math.sin(2 * math.pi * 165 * t)
                 + 0.2 * math.sin(2 * math.pi * 220 * t))
            samples.append(int(22000 * s * env / 2.3))
    elif profile == "splash":
        # Water splash — noise burst with descending frequency sweep
        rng = _random.Random(17)
        n = int(sample_rate * 0.11)
        for i in range(n):
            t = i / sample_rate
            env = math.exp(-t * 28.0) * (1.0 - math.exp(-t * 120.0))
            freq = 1200.0 - 800.0 * (t / 0.11)
            tone = math.sin(2 * math.pi * freq * t)
            noise = rng.uniform(-1, 1) * 0.4
            samples.append(int(20000 * (0.6 * tone + noise) * env))
    elif profile == "moo":
        # Cow moo — low sustained tone with amplitude modulation
        n = int(sample_rate * 0.20)
        phase = 0.0
        for i in range(n):
            t = i / sample_rate
            freq = 120.0 + 10.0 * math.sin(2 * math.pi * 3 * t)
            env = (1.0 - math.exp(-t * 15.0)) * math.exp(-t * 5.0)
            phase += 2 * math.pi * freq / sample_rate
            s = math.sin(phase) + 0.5 * math.sin(2 * phase) + 0.25 * math.sin(3 * phase)
            samples.append(int(18000 * s * env / 1.75))
    elif profile == "tweet":
        # Bird tweet — two quick ascending chirps
        for chirp_base in (1000, 1300):
            n = int(sample_rate * 0.05)
            for i in range(n):
                t = i / sample_rate
                freq = chirp_base + 600 * (t / 0.05)
                env = math.exp(-t * 30.0) * (1.0 - math.exp(-t * 150.0))
                samples.append(int(18000 * math.sin(2 * math.pi * freq * t) * env))
            # tiny gap
            for _ in range(int(sample_rate * 0.02)):
                samples.append(0)
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
    "Gore": "growl", "Bat Cave": "dark", "Rainbow Chaos": "bright",
    "Otter Cove": "purr", "Galaxy": "dark", "Galaxy Otter": "dark",
    "Goth": "rock",  # cycles rock1/rock2/rock3
    "Volcano": "hard", "Arctic": "icy",
    "Fairy Garden": "chirp", "Mermaid": "splash", "Shark Bait": "bubble",
    "Alien": "bright", "Noodle": "sparkle", "Pancake": "sparkle",
    # Hidden themes
    "Secret Skeleton": "crunch", "Secret Sakura": "chirp",
    "Deep Ocean": "splash", "Blood Moon": "growl", "Ice Cave": "icy",
    "Cyber Otter": "icy", "Toxic Neon": "bright", "Lava Cave": "hard",
    "Sunset Beach": "warm", "Midnight Forest": "tweet",
    "Candy Land": "bright", "Zombie Apocalypse": "growl",
    "Dragon Fire": "roar", "Bubblegum": "bubble", "Thunder Storm": "hard",
    "Rose Gold": "chirp", "Space Cat": "meow", "Magic Mushroom": "sparkle",
    "Abyssal Void": "dark", "Spring Bloom": "tweet",
    "Gold Rush": "sparkle", "Nebula": "dark",
    # New hidden themes
    "Crystal Cave": "icy", "Glitch": "bright", "Wild West": "warm",
    "Pirate": "dark", "Deep Space": "dark", "Witch's Brew": "crunch",
    "Lava Lamp": "warm", "Coral Reef": "splash", "Storm Cloud": "hard",
    "Golden Hour": "sparkle",
    # Animal themes — sounds match the animal
    "Purrfect Cats": "purr",      # purring cat
    "Good Dog": "bark",           # friendly bark
    "Space Cat": "meow",          # meow in space
    "Otter Cove": "purr",         # otter chitter mapped to purr
    "Galaxy Otter": "purr",
}

# Goth/rock themes that cycle through rock sub-profiles on each click
_GOTH_ROCK_THEMES: frozenset[str] = frozenset({
    "Goth", "Secret Skeleton", "Abyssal Void", "Witch's Brew",
    "Blood Moon", "Zombie Apocalypse",
})
_ROCK_CYCLE: tuple[str, ...] = ("rock1", "rock2", "rock3")


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


def _make_file_add_wav(sample_rate: int = 22050) -> str:
    """Soft 'drop' sound for when a file is added to the queue — a gentle 'thunk'."""
    n = int(sample_rate * 0.05)
    samples: list = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 60) * (1 - math.exp(-t * 200))
        samples.append(int(18000 * math.sin(2 * math.pi * 350 * t) * env))
    return _write_wav(samples, sample_rate)


def _make_preview_wav(sample_rate: int = 22050) -> str:
    """Very subtle single-note 'ping' for preview refresh."""
    n = int(sample_rate * 0.04)
    samples: list = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 80)
        samples.append(int(14000 * math.sin(2 * math.pi * 1100 * t) * env))
    return _write_wav(samples, sample_rate)


def _make_process_start_wav(sample_rate: int = 22050) -> str:
    """Short ascending two-tone 'launch' cue played when a batch starts."""
    # Two quick beeps going up: 440 Hz then 660 Hz, each 40 ms with a soft envelope
    notes = [(440, 0.04), (660, 0.04)]
    samples: list = []
    pause = int(sample_rate * 0.015)  # 15 ms silence between notes
    for freq, dur in notes:
        n = int(sample_rate * dur)
        for i in range(n):
            t = i / sample_rate
            env = math.sin(math.pi * i / n)  # half-sine envelope
            samples.append(int(20000 * math.sin(2 * math.pi * freq * t) * env))
        samples.extend([0] * pause)
    return _write_wav(samples, sample_rate)


def _make_file_remove_wav(sample_rate: int = 22050) -> str:
    """Short descending 'pop' played when files are removed from the queue."""
    n = int(sample_rate * 0.05)
    samples: list = []
    for i in range(n):
        t = i / sample_rate
        # Slightly descending pitch: start at 500 Hz, end near 350 Hz
        freq = 500 - 3000 * t
        env = math.exp(-t * 50) * (1 - math.exp(-t * 300))
        samples.append(int(16000 * math.sin(2 * math.pi * freq * t) * env))
    return _write_wav(samples, sample_rate)


def _make_theme_change_wav(sample_rate: int = 22050) -> str:
    """Soft upward whoosh played when the active theme changes.

    Two overlapping sine glides sweep from low to high to convey a
    smooth 'slide' feel (like a curtain being drawn back).
    """
    n = int(sample_rate * 0.14)
    samples: list = []
    for i in range(n):
        t = i / sample_rate
        env = math.sin(math.pi * t / 0.14) * 0.9  # half-sine — soft attack+release
        # Two parallel rising tones create a richer whoosh texture
        f1 = 300 + 1200 * (t / 0.14)   # 300 Hz → 1500 Hz
        f2 = 200 + 800  * (t / 0.14)   # 200 Hz → 1000 Hz
        s = 0.6 * math.sin(2 * math.pi * f1 * t)
        s += 0.4 * math.sin(2 * math.pi * f2 * t)
        samples.append(int(18000 * s * env))
    return _write_wav(samples, sample_rate)


def _make_tab_switch_wav(sample_rate: int = 22050) -> str:
    """Quick soft tick played when the user switches tabs."""
    n = int(sample_rate * 0.04)
    samples: list = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 80)
        samples.append(int(16000 * math.sin(2 * math.pi * 750 * t) * env))
    return _write_wav(samples, sample_rate)


def _make_drag_enter_wav(sample_rate: int = 22050) -> str:
    """Gentle rising two-note ping played when files are dragged over the drop zone."""
    notes = [(660, 0.05), (880, 0.07)]
    samples: list = []
    for freq, dur in notes:
        n = int(sample_rate * dur)
        for i in range(n):
            env = math.exp(-i / sample_rate * 28)
            samples.append(int(16000 * math.sin(2 * math.pi * freq * i / sample_rate) * env))
    return _write_wav(samples, sample_rate)


def _make_zone_paint_wav(sample_rate: int = 22050) -> str:
    """Soft brush-swipe sound for zone painting.

    A short band of filtered noise with a quick decay mimics the feel of a
    soft-bristle brush dragging across canvas.
    """
    n = int(sample_rate * 0.06)
    samples: list = []
    import random as _rng
    rng = _rng.Random(42)
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 35) * 0.7
        noise = rng.uniform(-1.0, 1.0)
        # Band-pass effect: mix noise with a low sine to add warmth
        warm = 0.3 * math.sin(2 * math.pi * 220 * t)
        samples.append(int(12000 * (noise * 0.7 + warm) * env))
    return _write_wav(samples, sample_rate)


def _make_mask_copy_wav(sample_rate: int = 22050) -> str:
    """Crisp camera-shutter click for copying a zone mask.

    Two very short transients — a mechanical click followed by a faint echo —
    imitate the feel of pressing a physical copy button.
    """
    n = int(sample_rate * 0.07)
    samples: list = []
    click_at = [0, int(sample_rate * 0.025)]
    for i in range(n):
        t = i / sample_rate
        s = 0.0
        for onset in click_at:
            dt = i - onset
            if dt >= 0:
                env = math.exp(-dt / sample_rate * 120)
                s += math.sin(2 * math.pi * 1800 * dt / sample_rate) * env
        samples.append(int(16000 * s * 0.5))
    return _write_wav(samples, sample_rate)


def _make_mask_paste_wav(sample_rate: int = 22050) -> str:
    """Soft 'splat/plop' for pasting a zone mask.

    A low-frequency tone with a quick non-linear decay simulates a dab or
    stamp landing on a surface.
    """
    n = int(sample_rate * 0.08)
    samples: list = []
    for i in range(n):
        t = i / sample_rate
        # Pitch drops rapidly (stamp landing + resonance)
        freq = 320 - 180 * (t / 0.08)
        env = math.exp(-t * 40) * (1.0 - math.exp(-t * 300))
        samples.append(int(18000 * math.sin(2 * math.pi * freq * t) * env))
    return _write_wav(samples, sample_rate)


def _make_bat_screech_wav(sample_rate: int = 22050) -> str:
    """Bat echolocation screech — rapid high-frequency chirp bursts.

    Three short ultra-high sine pulses with very quick decay simulate a bat's
    biosonar click train, giving the Bat Cave theme its own distinctive sound.
    """
    n = int(sample_rate * 0.12)
    samples: list = []
    pulse_onsets = [0, int(sample_rate * 0.035), int(sample_rate * 0.07)]
    for i in range(n):
        s = 0.0
        for onset in pulse_onsets:
            dt = i - onset
            if 0 <= dt < int(sample_rate * 0.025):
                t = dt / sample_rate
                env = math.exp(-t * 200)
                # High-frequency chirp that descends slightly (bat echolocation)
                freq = 5500 - 1500 * t / 0.025
                s += math.sin(2 * math.pi * freq * t) * env
        samples.append(int(14000 * s))
    return _write_wav(samples, sample_rate)


def _make_cat_meow_wav(sample_rate: int = 22050) -> str:
    """Synthetic cat meow — falling pitch with nasal formant resonance.

    A long tone starting at ~800 Hz and falling to ~400 Hz with a slow
    amplitude envelope mimics the characteristic shape of a cat's meow.
    """
    duration = 0.30
    n = int(sample_rate * duration)
    samples: list = []
    for i in range(n):
        t = i / sample_rate
        # Fundamental: pitch glide down
        freq = 800 - 400 * (t / duration) ** 0.6
        # Envelope: slow attack + long decay
        env = (1.0 - math.exp(-t * 30)) * math.exp(-t * 6)
        # Nasal second harmonic for realism
        s = math.sin(2 * math.pi * freq * t) * 0.7
        s += math.sin(2 * math.pi * freq * 2 * t) * 0.25 * math.exp(-t * 12)
        samples.append(int(16000 * s * env))
    return _write_wav(samples, sample_rate)


def _make_dog_bark_wav(sample_rate: int = 22050) -> str:
    """Sharp dog bark — two staccato low-frequency bursts.

    Two punchy tonal bursts at ~200 Hz with quick attack/decay simulate a
    short double-bark sound.
    """
    n = int(sample_rate * 0.20)
    samples: list = []
    bark_onsets = [0, int(sample_rate * 0.10)]
    for i in range(n):
        s = 0.0
        for onset in bark_onsets:
            dt = i - onset
            if 0 <= dt < int(sample_rate * 0.07):
                t = dt / sample_rate
                env = math.exp(-t * 55) * (1.0 - math.exp(-t * 400))
                freq = 220 - 60 * (t / 0.07)
                s += math.sin(2 * math.pi * freq * t) * env
                # Add a little roughness for texture
                s += math.sin(2 * math.pi * freq * 3 * t) * env * 0.18
        samples.append(int(18000 * s))
    return _write_wav(samples, sample_rate)


def _make_frog_croak_wav(sample_rate: int = 22050) -> str:
    """Frog croak — two low, raspy pulses with rough timbre.

    A pair of short tonal bursts at ~120 Hz with added odd harmonics give the
    characteristic raspy quality of a tree-frog croak.
    """
    n = int(sample_rate * 0.25)
    samples: list = []
    croak_onsets = [0, int(sample_rate * 0.11)]
    import random as _rng
    rng = _rng.Random(7)
    for i in range(n):
        s = 0.0
        for onset in croak_onsets:
            dt = i - onset
            if 0 <= dt < int(sample_rate * 0.08):
                t = dt / sample_rate
                env = math.exp(-t * 45) * (1.0 - math.exp(-t * 200))
                base = 120.0
                s += math.sin(2 * math.pi * base * t) * env
                # Odd harmonics = raspiness
                s += math.sin(2 * math.pi * base * 3 * t) * env * 0.40
                s += math.sin(2 * math.pi * base * 5 * t) * env * 0.18
                # Small noise component
                s += rng.uniform(-0.1, 0.1) * env
        samples.append(int(16000 * s))
    return _write_wav(samples, sample_rate)


def _make_batch_done_wav(sample_rate: int = 22050) -> str:
    """Ascending victory fanfare for completing a large file batch.

    Four rising notes (C4→E4→G4→C5) with a bright timbre celebrate the
    completion of a big job — played only when the batch size is ≥ 100 files.
    """
    notes = [261.63, 329.63, 392.00, 523.25]   # C4, E4, G4, C5
    note_dur = 0.09
    gap = 0.025
    total = len(notes) * note_dur + (len(notes) - 1) * gap
    n = int(sample_rate * total)
    samples: list = []
    note_start = 0
    schedule: list = []
    for freq in notes:
        schedule.append((note_start, freq))
        note_start += note_dur + gap

    for i in range(n):
        t_abs = i / sample_rate
        s = 0.0
        for onset_t, freq in schedule:
            dt = t_abs - onset_t
            if 0 <= dt < note_dur:
                env = math.exp(-dt * 10) * (1.0 - math.exp(-dt * 300))
                s += math.sin(2 * math.pi * freq * dt) * env
                # Bright second harmonic
                s += math.sin(2 * math.pi * freq * 2 * dt) * env * 0.35
        samples.append(int(14000 * s))
    return _write_wav(samples, sample_rate)


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
      theme_click   – per-theme click sound variant (12 profiles)
      success       – cheerful two-note chime, played after a successful batch
      error         – descending buzz, played after a batch with errors
      unlock        – ascending arpeggio fanfare, played when a theme unlocks
      file_add      – soft 'thunk' when a file is dropped into the queue
      preview       – subtle ping when the live preview refreshes

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
        self._file_add_wav: str = ""
        self._preview_wav: str = ""
        self._process_start_wav: str = ""
        self._file_remove_wav: str = ""
        self._theme_change_wav: str = ""
        self._tab_switch_wav: str = ""
        self._drag_enter_wav: str = ""
        self._zone_paint_wav: str = ""
        self._mask_copy_wav: str = ""
        self._mask_paste_wav: str = ""
        # Animal / event sounds
        self._bat_screech_wav: str = ""
        self._cat_meow_wav: str = ""
        self._dog_bark_wav: str = ""
        self._frog_croak_wav: str = ""
        self._batch_done_wav: str = ""
        # Per-profile click WAVs keyed by profile name
        self._theme_click_wavs: dict[str, str] = {}
        self._filter: _ButtonClickFilter | None = None
        self._goth_rock_idx: int = 0   # cycles 0→1→2→0 for rock themes
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
            self._file_add_wav = _make_file_add_wav()
            self._preview_wav  = _make_preview_wav()
            self._process_start_wav = _make_process_start_wav()
            self._file_remove_wav   = _make_file_remove_wav()
            self._theme_change_wav  = _make_theme_change_wav()
            self._tab_switch_wav    = _make_tab_switch_wav()
            self._drag_enter_wav    = _make_drag_enter_wav()
            self._zone_paint_wav    = _make_zone_paint_wav()
            self._mask_copy_wav     = _make_mask_copy_wav()
            self._mask_paste_wav    = _make_mask_paste_wav()
            # Animal / event sounds
            self._bat_screech_wav   = _make_bat_screech_wav()
            self._cat_meow_wav      = _make_cat_meow_wav()
            self._dog_bark_wav      = _make_dog_bark_wav()
            self._frog_croak_wav    = _make_frog_croak_wav()
            self._batch_done_wav    = _make_batch_done_wav()
            # Generate one WAV per sound profile (all profiles)
            for profile in ("soft", "hard", "bright", "dark", "warm", "icy", "sparkle",
                            "growl", "bubble", "chirp", "crunch", "purr", "meow", "roar",
                            "bark", "howl", "hiss", "rock1", "rock2", "rock3",
                            "splash", "moo", "tweet"):
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

    def _volume(self) -> float:
        """Return the current master volume as a 0.0–1.0 float."""
        raw = self._settings.get("sound_volume", 50)
        try:
            return max(0.0, min(1.0, int(raw) / 100.0))
        except (TypeError, ValueError):
            return 0.45

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
        goth/rock themes cycle through rock1/rock2/rock3 on each press.
        """
        if not self._settings.get("sound_enabled", False):
            return

        # Theme sound path
        if self._settings.get("use_theme_sound", False):
            try:
                theme = self._settings.get_theme()
                theme_name = theme.get("name", "")
                # Goth/rock themes cycle between three rock sub-profiles
                if theme_name in _GOTH_ROCK_THEMES:
                    profile = _ROCK_CYCLE[self._goth_rock_idx % len(_ROCK_CYCLE)]
                    self._goth_rock_idx += 1
                else:
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
        if not self._settings.get("sound_success", True):
            return
        if self._success_wav:
            self._play(self._success_wav)

    def play_error(self) -> None:
        """Play the error buzz when a batch finishes with failures."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_error", True):
            return
        if self._error_wav:
            self._play(self._error_wav)

    def play_unlock(self) -> None:
        """Play the unlock fanfare when a hidden theme is revealed."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_unlock", True):
            return
        if self._unlock_wav:
            self._play(self._unlock_wav)

    def play_file_add(self) -> None:
        """Play a soft 'thunk' when a file is added to the queue."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_file_add", True):
            return
        if self._file_add_wav:
            self._play(self._file_add_wav)

    def play_preview(self) -> None:
        """Play a subtle ping when the live preview refreshes."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_preview", False):
            return
        if self._preview_wav:
            self._play(self._preview_wav)

    def play_process_start(self) -> None:
        """Play a short ascending cue when a batch starts processing."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_process_start", False):
            return
        if self._process_start_wav:
            self._play(self._process_start_wav)

    def play_file_remove(self) -> None:
        """Play a short descending pop when files are removed from the queue."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_file_remove", False):
            return
        if self._file_remove_wav:
            self._play(self._file_remove_wav)

    def play_theme_change(self) -> None:
        """Play a soft upward whoosh when the active theme changes."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_theme_change", False):
            return
        if self._theme_change_wav:
            self._play(self._theme_change_wav)

    def play_tab_switch(self) -> None:
        """Play a quick soft tick when the user switches tabs."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_tab_switch", False):
            return
        if self._tab_switch_wav:
            self._play(self._tab_switch_wav)

    def play_drag_enter(self) -> None:
        """Play a gentle rising ping when files are dragged over the drop zone."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_drag_enter", False):
            return
        if self._drag_enter_wav:
            self._play(self._drag_enter_wav)

    def play_zone_paint(self) -> None:
        """Play a soft brush swipe when zone paint is applied to the canvas."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_zone_paint", False):
            return
        if self._zone_paint_wav:
            self._play(self._zone_paint_wav)

    def play_mask_copy(self) -> None:
        """Play a crisp click when a zone mask is copied to the clipboard."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_mask_copy", False):
            return
        if self._mask_copy_wav:
            self._play(self._mask_copy_wav)

    def play_mask_paste(self) -> None:
        """Play a soft splat/plop when a zone mask is pasted from the clipboard."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_mask_paste", False):
            return
        if self._mask_paste_wav:
            self._play(self._mask_paste_wav)

    def play_bat_screech(self) -> None:
        """Play a bat echolocation screech (e.g. when switching to Bat Cave theme)."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_bat_screech", False):
            return
        if self._bat_screech_wav:
            self._play(self._bat_screech_wav)

    def play_cat_meow(self) -> None:
        """Play a synthetic cat meow (e.g. when switching to Panda Dark theme)."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_cat_meow", False):
            return
        if self._cat_meow_wav:
            self._play(self._cat_meow_wav)

    def play_dog_bark(self) -> None:
        """Play a short dog bark (e.g. when clearing the file list)."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_dog_bark", False):
            return
        if self._dog_bark_wav:
            self._play(self._dog_bark_wav)

    def play_frog_croak(self) -> None:
        """Play a frog croak (e.g. when a new theme is unlocked via alpha milestones)."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_frog_croak", False):
            return
        if self._frog_croak_wav:
            self._play(self._frog_croak_wav)

    def play_batch_done(self) -> None:
        """Play an ascending fanfare when a large batch (≥ 100 files) completes."""
        if not self._settings.get("sound_enabled", False):
            return
        if not self._settings.get("sound_batch_done", False):
            return
        if self._batch_done_wav:
            self._play(self._batch_done_wav)


        if self._effect is not None:
            try:
                from PyQt6.QtCore import QUrl
                current_src = self._effect.source().toLocalFile()
                if current_src != wav_path:
                    self._effect.setSource(QUrl.fromLocalFile(wav_path))
                # Apply master volume (0.0–1.0) from settings
                self._effect.setVolume(self._volume())
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
                    self._error_wav, self._unlock_wav,
                    self._file_add_wav, self._preview_wav,
                    self._process_start_wav, self._file_remove_wav,
                    self._theme_change_wav, self._tab_switch_wav,
                    self._drag_enter_wav,
                    self._zone_paint_wav, self._mask_copy_wav, self._mask_paste_wav,
                    self._bat_screech_wav, self._cat_meow_wav, self._dog_bark_wav,
                    self._frog_croak_wav, self._batch_done_wav]
        all_wavs.extend(self._theme_click_wavs.values())
        for path in all_wavs:
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
