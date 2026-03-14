"""
click_effects.py – per-theme click-triggered particle effects overlay.

Each theme maps to an "effect key" (see THEME_EFFECTS in theme_engine.py).
When the user clicks anywhere in the main window, a burst of themed particles
is spawned at the cursor position.  For the Bat Cave theme a periodic timer
also spawns bats that fly across the top of the window.

Public API
----------
  ClickEffectsOverlay(main_window)   – create and attach to main window
  .set_effect(effect_key: str)       – change the active effect
  .set_enabled(enabled: bool)        – toggle globally on/off
  .record_click()                    – increment click counter (for unlocks)
  .click_count → int                 – total clicks recorded
"""

import math
import random
from collections import deque

from PyQt6.QtCore import QEvent, QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from ..core.settings_manager import DEFAULT_CUSTOM_EMOJI as _DEFAULT_EMOJI_STR

# Cross-platform emoji font stack (matches mouse_trail.py)
_EMOJI_FONT_FAMILIES = "Apple Color Emoji, Segoe UI Emoji, Noto Color Emoji"


# ---------------------------------------------------------------------------
# Particle data class
# ---------------------------------------------------------------------------

class _Particle:
    """A single animated particle."""
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life",
                 "kind", "size", "color", "text")

    def __init__(self, x, y, vx, vy, life, kind, size, color, text=""):
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.life = float(life)
        self.max_life = float(life)
        self.kind = kind
        self.size = float(size)
        self.color = color
        self.text = text  # emoji / char for text-type particles

    @property
    def alpha_frac(self) -> float:
        return max(0.0, self.life / self.max_life)


# ---------------------------------------------------------------------------
# Effect spawner registry
# ---------------------------------------------------------------------------

def _rand_vel(speed_lo: float, speed_hi: float):
    angle = random.uniform(0, 2 * math.pi)
    speed = random.uniform(speed_lo, speed_hi)
    return math.cos(angle) * speed, math.sin(angle) * speed


def _spawn_default(x, y):
    particles = []
    for _ in range(4):
        particles.append(
            _Particle(x, y, *_rand_vel(1, 5), random.uniform(0.4, 0.8),
                      "circle", random.uniform(4, 10), QColor("#e94560"))
        )
    emoji = random.choice(["✨", "💥", "⭐", "💫", "🎉"])
    vx, vy = _rand_vel(1.5, 4.5)
    particles.append(_Particle(x, y, vx, vy, random.uniform(0.4, 0.9),
                               "text", random.uniform(14, 20),
                               QColor("#e94560"), emoji))
    return particles


def _spawn_gore(x, y):
    particles = []
    for _ in range(5):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(2, 9)
        vy = random.uniform(-8, 4)
        vx = math.cos(angle) * speed
        r = random.randint(160, 220)
        g = random.randint(0, 30)
        b = random.randint(0, 20)
        kind = random.choice(["circle", "drop"])
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.2),
                                   kind, random.uniform(4, 14),
                                   QColor(r, g, b)))
    particles.append(_Particle(x, y, *_rand_vel(1.5, 6), random.uniform(0.5, 1.0),
                               "text", random.uniform(14, 22),
                               QColor("#cc0000"), random.choice(["🩸", "💀", "☠"])))
    return particles


def _spawn_bat(x, y):
    particles = []
    bat_emojis = ["🦇", "🌙", "💜", "·"]
    for _ in range(4):
        vx, vy = _rand_vel(2, 7)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.5, 1.2), "text",
                                   random.uniform(14, 22),
                                   QColor(random.choice(["#7b2dff", "#9944ff", "#ccaaff"])),
                                   random.choice(bat_emojis)))
    particles.append(_Particle(x, y, *_rand_vel(1, 5), random.uniform(0.4, 0.8),
                               "circle", random.uniform(4, 10), QColor("#7b2dff")))
    return particles


def _spawn_rainbow(x, y):
    particles = []
    rainbow_colors = ["#ff0000", "#ff7700", "#ffff00",
                      "#00ff00", "#0088ff", "#8800ff", "#ff00ff"]
    emojis = ["🌈", "✨", "⭐", "🌟", "🦄"]
    for i in range(5):
        vx, vy = _rand_vel(2, 7)
        color = QColor(rainbow_colors[i % len(rainbow_colors)])
        kind = "text" if i % 2 == 0 else "circle"
        text = random.choice(emojis) if kind == "text" else ""
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.5, 1.0),
                                   kind, random.uniform(8, 18), color, text))
    return particles


def _spawn_otter(x, y):
    particles = []
    otter_emojis = ["🦦", "🐟", "💧", "🌊", "✨"]
    otter_colors = ["#e8a040", "#6699cc", "#88ccee", "#c8a870"]
    for _ in range(5):
        vx, vy = _rand_vel(1, 5)
        kind = "text" if random.random() < 0.6 else "circle"
        text = random.choice(otter_emojis) if kind == "text" else ""
        color = QColor(random.choice(otter_colors))
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.1),
                                   kind, random.uniform(12, 20), color, text))
    return particles


def _spawn_galaxy(x, y):
    particles = []
    star_colors = ["#4477ff", "#aabbff", "#ffffff", "#00ddaa", "#ffcc00"]
    star_chars = ["✦", "✧", "★", "·", "⭐"]
    for _ in range(5):
        vx, vy = _rand_vel(1, 6)
        color = QColor(random.choice(star_colors))
        kind = random.choice(["text", "circle"])
        text = random.choice(star_chars) if kind == "text" else ""
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.5, 1.2),
                                   kind, random.uniform(6, 16), color, text))
    return particles


def _spawn_galaxy_otter(x, y):
    particles = _spawn_galaxy(x, y)
    particles.append(_Particle(x, y, *_rand_vel(1, 4), random.uniform(0.7, 1.2),
                               "text", random.uniform(16, 22),
                               QColor(random.choice(["#a06aff", "#cc88ff"])),
                               random.choice(["🦦", "⭐", "✨"])))
    return particles


def _spawn_goth(x, y):
    particles = []
    goth_chars = ["💀", "🕷", "🦇", "☠", "🖤"]
    goth_colors = ["#8800aa", "#330033", "#aa00cc", "#ffffff", "#550055"]
    for _ in range(5):
        vx, vy = _rand_vel(1, 5)
        kind = "text" if random.random() < 0.6 else "circle"
        text = random.choice(goth_chars) if kind == "text" else ""
        color = QColor(random.choice(goth_colors))
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.5, 1.0),
                                   kind, random.uniform(10, 18), color, text))
    return particles


def _spawn_neon(x, y):
    """Electric neon / lightning-bolt particles."""
    particles = []
    neon_colors = ["#00ff88", "#ff00ff", "#00ffff", "#ffff00", "#ff00aa", "#aa00ff"]
    chars = ["⚡", "✦", "◆", "★", "✸"]
    for _ in range(5):
        vx, vy = _rand_vel(2, 8)
        kind = "text" if random.random() < 0.55 else "circle"
        color = QColor(random.choice(neon_colors))
        text = random.choice(chars) if kind == "text" else ""
        size = random.uniform(12, 18) if kind == "text" else random.uniform(4, 10)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.3, 0.8),
                                   kind, size, color, text))
    return particles


def _spawn_fire(x, y):
    """Rising flame and ember particles."""
    particles = []
    fire_colors = ["#ff4400", "#ff8800", "#ffcc00", "#ff2200", "#ffaa00"]
    for _ in range(5):
        angle = random.uniform(-math.pi * 0.78, -math.pi * 0.22)
        speed = random.uniform(2, 7)
        vx = math.cos(angle) * speed + random.uniform(-0.8, 0.8)
        vy = math.sin(angle) * speed
        color = QColor(random.choice(fire_colors))
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.4, 0.9),
                                   "circle", random.uniform(5, 14), color))
    particles.append(_Particle(x, y, random.uniform(-2, 2), random.uniform(-6, -3),
                               random.uniform(0.6, 1.0), "text", 22,
                               QColor("#ff8800"), random.choice(["🔥", "💥"])))
    return particles


def _spawn_ice(x, y):
    """Snowflake and frost crystal particles."""
    particles = []
    ice_colors = ["#aaddff", "#ffffff", "#88ccff", "#ccf0ff", "#6699cc"]
    flakes = ["❄", "❅", "❆", "·", "✦"]
    for _ in range(5):
        vx, vy = _rand_vel(0.8, 4)
        vy = abs(vy) * 0.4 + random.uniform(-1, 1)
        kind = "text" if random.random() < 0.65 else "circle"
        color = QColor(random.choice(ice_colors))
        text = random.choice(flakes) if kind == "text" else ""
        size = random.uniform(10, 20) if kind == "text" else random.uniform(4, 10)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.9, 1.6),
                                   kind, size, color, text))
    return particles


def _spawn_panda(x, y):
    """Cute panda-themed emoji and heart particles."""
    particles = []
    panda_emojis = ["🐼", "🎋", "🌸", "✨", "💕", "⭐"]
    panda_colors = ["#e94560", "#f0f0f0", "#1a1a1a", "#ffccdd", "#ffaacc"]
    for _ in range(4):
        vx, vy = _rand_vel(1, 5)
        kind = "text" if random.random() < 0.75 else "circle"
        color = QColor(random.choice(panda_colors))
        text = random.choice(panda_emojis) if kind == "text" else ""
        size = random.uniform(14, 22) if kind == "text" else random.uniform(5, 12)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.1),
                                   kind, size, color, text))
    particles.append(_Particle(x, y, random.uniform(-2, 2), random.uniform(-6, -3),
                               random.uniform(0.9, 1.4), "text", 26, QColor("#1a1a1a"), "🐼"))
    return particles


def _spawn_sakura(x, y):
    """Cherry-blossom petals for the Secret Sakura theme."""
    particles = []
    sakura_emojis = ["🌸", "🌺", "🌷", "💮", "✨", "💖"]
    sakura_colors = ["#ff6699", "#ff99bb", "#ffccdd", "#ff4477", "#ffaacc"]
    for _ in range(5):
        angle = random.uniform(-math.pi * 0.9, -math.pi * 0.1)
        speed = random.uniform(1.5, 6)
        vx = math.cos(angle) * speed + random.uniform(-0.5, 0.5)
        vy = math.sin(angle) * speed
        kind = "text" if random.random() < 0.75 else "circle"
        color = QColor(random.choice(sakura_colors))
        text = random.choice(sakura_emojis) if kind == "text" else ""
        size = random.uniform(12, 20) if kind == "text" else random.uniform(4, 10)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.7, 1.3),
                                   kind, size, color, text))
    return particles


def _spawn_fairy(x, y):
    """Fairy-dust sparkles for Fairy Garden theme (click burst)."""
    particles = []
    # Use only fairy emoji for consistency with the flying fairies overhead
    fairy_colors = ["#dd44ff", "#ff88ff", "#ffccee", "#cc88ff", "#ffffff", "#aa44ff"]
    for i in range(5):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1.5, 7)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        color = QColor(random.choice(fairy_colors))
        size = random.uniform(12, 22)
        # Alternate between emoji and circles to reduce font-rendering cost
        if i < 3:
            particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.2),
                                       "text", size, color, "🧚"))
        else:
            particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.2),
                                       "circle", random.uniform(4, 10), color))
    return particles


# ---------------------------------------------------------------------------
# Custom emoji effect (user-configurable)
# ---------------------------------------------------------------------------

# Mutable module-level list – updated by ClickEffectsOverlay.set_custom_emoji()
_CUSTOM_EMOJI: list[str] = _DEFAULT_EMOJI_STR.split()


def set_custom_emoji(emoji_list: list[str]) -> None:
    """Update the emoji used by the 'custom' effect spawner."""
    global _CUSTOM_EMOJI
    _CUSTOM_EMOJI = list(emoji_list) if emoji_list else _DEFAULT_EMOJI_STR.split()


def _spawn_custom(x, y):
    particles = []
    emoji_list = _CUSTOM_EMOJI or ["✨"]
    accent_colors = ["#e94560", "#00ff88", "#4477ff", "#ffcc00", "#ff88ff"]
    for _ in range(5):
        vx, vy = _rand_vel(1, 6)
        kind = "text" if random.random() < 0.7 else "circle"
        text = random.choice(emoji_list) if kind == "text" else ""
        color = QColor(random.choice(accent_colors))
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.5, 1.1),
                                   kind, random.uniform(12, 22), color, text))
    return particles


def _spawn_ocean(x, y):
    """Bubbles and sea creatures for Deep Ocean theme."""
    particles = []
    ocean_emojis = ["🫧", "🐠", "🐟", "🐙", "🦑", "🌊", "💧", "🫧"]
    ocean_colors = ["#00d4ff", "#00aacc", "#0088aa", "#33ccff", "#006688", "#00ffcc"]
    for _ in range(6):  # reduced from 10 to cut CPU
        angle = random.uniform(-math.pi, 0)  # mostly upward, like bubbles rising
        speed = random.uniform(1.5, 6)
        vx = math.cos(angle) * speed * 0.5  # gentle sideways drift
        vy = math.sin(angle) * speed - random.uniform(1, 3)  # biased upward
        kind = "text" if random.random() < 0.55 else "circle"
        color = QColor(random.choice(ocean_colors))
        text = random.choice(ocean_emojis) if kind == "text" else ""
        size = random.uniform(10, 18) if kind == "text" else random.uniform(4, 10)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.7, 1.5),
                                   kind, size, color, text))
    return particles


def _spawn_sparkle(x, y):
    """Glittering star sparkles for icy/crystalline themes."""
    particles = []
    sparkle_emojis = ["✨", "⭐", "💫", "🌟", "❄", "💎", "🔷", "✦"]
    sparkle_colors = ["#aaddff", "#ffffff", "#88ccff", "#cceeFF", "#66bbff", "#eef4ff"]
    for _ in range(5):  # reduced from 7 to cut CPU
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1.5, 6.5)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        kind = "text" if random.random() < 0.6 else "circle"
        color = QColor(random.choice(sparkle_colors))
        text = random.choice(sparkle_emojis) if kind == "text" else ""
        size = random.uniform(10, 20) if kind == "text" else random.uniform(3, 8)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.3),
                                   kind, size, color, text))
    return particles


def _spawn_ripple(x, y):
    """Water ripple / splash for aquatic / mermaid themes."""
    particles = []
    ripple_emojis = ["💧", "🫧", "🌊", "🐚", "🐬", "🦈"]
    ripple_colors = ["#33aaff", "#00ddee", "#55ccff", "#0099cc", "#77ddff", "#22bbdd"]
    for i in range(5):  # reduced from 8 to cut CPU
        # Spray outward in all directions at low speed, simulating a ripple
        angle = (i / 5) * 2 * math.pi + random.uniform(-0.3, 0.3)
        speed = random.uniform(1.0, 5.0)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        kind = "circle" if random.random() < 0.55 else "text"
        color = QColor(random.choice(ripple_colors))
        text = random.choice(ripple_emojis) if kind == "text" else ""
        size = random.uniform(5, 12) if kind == "circle" else random.uniform(12, 20)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.7, 1.4),
                                   kind, size, color, text))
    return particles


def _spawn_mermaid(x, y):
    """Mermaid-themed sparkles, fish, and ocean magic."""
    particles = []
    mermaid_emojis = ["🧜", "🐠", "🐟", "🦀", "🐚", "💧", "🫧", "🌊", "🪸", "✨"]
    mermaid_colors = ["#00ccaa", "#33ddff", "#aa44ff", "#ff66cc", "#77ffee", "#ff99cc"]
    for _ in range(5):  # reduced from 8 to cut CPU
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1.5, 6.0)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed - random.uniform(0, 2)  # slight upward bias
        kind = "text" if random.random() < 0.65 else "circle"
        color = QColor(random.choice(mermaid_colors))
        text = random.choice(mermaid_emojis) if kind == "text" else ""
        size = random.uniform(12, 22) if kind == "text" else random.uniform(4, 9)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.7, 1.4),
                                   kind, size, color, text))
    return particles


def _spawn_alien(x, y):
    """UFO tractor beam abduction effects."""
    particles = []
    alien_emojis = ["🛸", "👽", "🌌", "⭐", "💫", "🔬", "☄", "🪐"]
    alien_colors = ["#00ff88", "#88ff00", "#00ffcc", "#44ff44", "#ccff00", "#66ff66"]
    for _ in range(5):  # reduced from 8 to cut CPU
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1.0, 5.5)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed - random.uniform(1.5, 4)  # biased upward (abduction!)
        kind = "text" if random.random() < 0.65 else "circle"
        color = QColor(random.choice(alien_colors))
        text = random.choice(alien_emojis) if kind == "text" else ""
        size = random.uniform(12, 22) if kind == "text" else random.uniform(4, 9)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.2),
                                   kind, size, color, text))
    return particles


def _spawn_shark(x, y):
    """Shark teeth bite and oceanic carnage effects."""
    particles = []
    shark_emojis = ["🦈", "🩸", "💥", "🐟", "🐠", "💦", "🫧"]
    shark_colors = ["#1177aa", "#0055cc", "#3399cc", "#cc1133", "#aa3355", "#ff4466"]
    for _ in range(5):  # reduced from 8 to cut CPU
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(2.0, 7.0)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        kind = "text" if random.random() < 0.6 else "circle"
        color = QColor(random.choice(shark_colors))
        text = random.choice(shark_emojis) if kind == "text" else ""
        size = random.uniform(12, 22) if kind == "text" else random.uniform(4, 9)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.2),
                                   kind, size, color, text))
    return particles


_SPAWNERS = {
    "default":      _spawn_default,
    "gore":         _spawn_gore,
    "bat":          _spawn_bat,
    "rainbow":      _spawn_rainbow,
    "otter":        _spawn_otter,
    "galaxy":       _spawn_galaxy,
    "galaxy_otter": _spawn_galaxy_otter,
    "goth":         _spawn_goth,
    "neon":         _spawn_neon,
    "fire":         _spawn_fire,
    "ice":          _spawn_ice,
    "panda":        _spawn_panda,
    "sakura":       _spawn_sakura,
    "fairy":        _spawn_fairy,
    "ocean":        _spawn_ocean,
    "sparkle":      _spawn_sparkle,
    "ripple":       _spawn_ripple,
    "mermaid":      _spawn_mermaid,
    "alien":        _spawn_alien,
    "shark":        _spawn_shark,
    "custom":       _spawn_custom,
}


# ---------------------------------------------------------------------------
# Bat flock (periodic background animation for Bat Cave theme)
# ---------------------------------------------------------------------------

class _BatFlock(QObject):
    """Spawns bats flying across the top of the window every few seconds."""

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._launch)
        self._timer.setInterval(random.randint(4000, 8000))

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _launch(self):
        self._timer.setInterval(random.randint(4000, 9000))
        w = self._overlay.width()
        count = random.randint(2, 5)  # was 3–7; fewer bats = less emoji rendering cost
        for i in range(count):
            y_start = random.randint(10, 60)
            x_start = random.randint(-20, 20)
            speed = random.uniform(3, 7)
            # life is in units consumed by _tick (which decrements by 0.05 per frame
            # at 20fps = 50ms interval).  Crossing window at speed px/frame takes
            # roughly (w + 60) / speed frames.
            life = (w + 60) / max(speed, 1) * 0.05 + random.uniform(0.3, 1.0)
            bat = _Particle(x_start + i * 25, y_start,
                            speed, random.uniform(-0.5, 0.5), life,
                            "bat_fly", random.uniform(18, 26),
                            QColor("#7b2dff"), "🦇")
            self._overlay._add_particle(bat)


class _FairyFlock(QObject):
    """Spawns fairies that flutter across the window for the Fairy Garden theme."""

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._launch)
        self._timer.setInterval(random.randint(3000, 7000))

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _launch(self):
        self._timer.setInterval(random.randint(3000, 8000))
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        count = random.randint(1, 3)  # was 2–5; fewer fairies = less emoji rendering cost
        left_to_right = random.random() < 0.5
        speed_sign = 1 if left_to_right else -1
        # Fairies fly only through the top 20% of the window.
        # Only the fairy emoji 🧚 is used — no random sparkles/wands.
        fairy_colors = ["#dd44ff", "#ff88ff", "#ffccee", "#cc88ff", "#ffffff", "#ffaaff"]
        top_band = max(80, h // 5)
        for i in range(count):
            y_start = random.randint(10, top_band)
            x_start = (random.randint(-30, -10) if left_to_right
                       else w + random.randint(10, 30))
            speed = random.uniform(1.5, 4.5) * speed_sign
            vy = random.uniform(-0.3, 0.3)
            # life is in units consumed by _tick (which decrements by 0.05 per frame
            # at 20fps = 50ms interval).  Crossing window at speed px/frame takes
            # roughly (w + 80) / abs(speed) frames.
            life = (w + 80) / max(abs(speed), 1) * 0.05 + random.uniform(0.3, 1.0)
            fairy = _Particle(
                x_start + i * random.randint(20, 50), y_start,
                speed, vy, life,
                "fairy_fly", random.uniform(18, 26),
                QColor(random.choice(fairy_colors)),
                "🧚",
            )
            self._overlay._add_particle(fairy)


class _BannerFlock(QObject):
    """Spawns themed emoji flying across the top band of the window periodically.

    Unlike *_BatFlock* and *_FairyFlock* (which are activated by the click
    effect key), this class is driven by the **banner animation mode**.  It is
    configured with the theme's representative icon emoji and accent colour so
    it complements whatever theme is active.  It works independently of whether
    click effects are enabled.
    """

    def __init__(self, overlay: "ClickEffectsOverlay",
                 emoji: str = "🐼", color: str = "#e94560"):
        super().__init__(overlay)
        self._overlay = overlay
        self._emoji = emoji
        self._color = QColor(color)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._launch)
        self._timer.setInterval(random.randint(4000, 8000))

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_emoji(self, emoji: str, color: str) -> None:
        """Update the emoji and accent colour used for spawned particles."""
        self._emoji = emoji
        self._color = QColor(color)

    def _launch(self) -> None:
        self._timer.setInterval(random.randint(4000, 9000))
        w = self._overlay.width()
        if w <= 0:
            return
        count = random.randint(2, 4)
        for i in range(count):
            y_start = random.randint(8, 55)
            x_start = random.randint(-20, 20)
            speed = random.uniform(2.5, 6.0)
            life = (w + 60) / max(speed, 1) * 0.05 + random.uniform(0.2, 0.8)
            p = _Particle(
                x_start + i * 28, y_start,
                speed, random.uniform(-0.4, 0.4), life,
                "bat_fly", random.uniform(18, 26),
                QColor(self._color), self._emoji,
            )
            self._overlay._add_particle(p)


# ---------------------------------------------------------------------------
# Main overlay widget
# ---------------------------------------------------------------------------

class ClickEffectsOverlay(QWidget):
    """
    Transparent overlay that renders per-theme click effects.

    • WA_TransparentForMouseEvents – all clicks pass through.
    • An event filter on QApplication intercepts mouse press events.
    • A 60fps timer drives animation.
    • click_registered signal fires with the total click count after each click.
    """

    # Particles whose computed alpha falls below this value are skipped
    # during painting — they are essentially invisible and not worth rendering.
    _MIN_VISIBLE_ALPHA = 6

    click_registered = pyqtSignal(int)  # emitted with total click count on each click

    def __init__(self, main_window: QWidget):
        super().__init__(main_window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Do NOT set WA_TranslucentBackground or WA_NoSystemBackground here.
        # Those attributes are only effective on top-level windows; on child
        # widgets they break Qt's backing-store machinery.  Specifically,
        # WA_NoSystemBackground prevents Qt from re-painting the parent region
        # before this widget's paintEvent, which means CompositionMode_Clear
        # would write (0,0,0,0) onto a surface with no real alpha channel and
        # render as solid black.  The correct approach for a transparent child
        # overlay is to keep the backing-store pipeline intact (Qt repaints the
        # parent first, then the child draws on top) and simply not fill the
        # background in paintEvent.
        self.setAutoFillBackground(False)

        self._main_window = main_window
        self._effect_key = "default"
        self._particles: list[_Particle] = []
        self._enabled = False
        self._click_count = 0
        self._bat_flock: _BatFlock | None = None
        self._fairy_flock: _FairyFlock | None = None
        self._banner_flock: _BannerFlock | None = None
        self._banner_flock_active: bool = False
        self._font = QFont(_EMOJI_FONT_FAMILIES, 14)
        # Cache QFont objects per integer point-size to avoid repeated
        # mutations and implicit font-metric recalculations each frame.
        self._font_cache: dict[int, QFont] = {}
        # Bounding rect from the previous frame so we can union it with the
        # current frame and only request a repaint of the dirty region.
        self._prev_dirty = None

        self._timer = QTimer(self)
        self._timer.setInterval(50)   # 20 fps – reduces CPU load, still smooth enough
        self._timer.timeout.connect(self._tick)

        self.setGeometry(main_window.rect())
        self.raise_()
        # Start hidden; the overlay is only made visible when effects are
        # actually enabled via set_enabled(True).
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def click_count(self) -> int:
        return self._click_count

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        app = QApplication.instance()
        if enabled:
            if app is not None:
                app.installEventFilter(self)
            self._timer.start()
            self.raise_()
            self.show()
        else:
            if app is not None:
                app.removeEventFilter(self)
            self._timer.stop()
            self._particles.clear()
            if self._bat_flock:
                self._bat_flock.stop()
            if self._fairy_flock:
                self._fairy_flock.stop()
            # Only hide the overlay if the banner flock is also inactive.
            # When banner flock is running we still need the overlay visible
            # so flying particles can be rendered even without click effects.
            if not self._banner_flock_active:
                self.hide()
            else:
                # Ensure the banner-flock timer keeps running even though
                # the click-effect timer was stopped above.
                if not self._timer.isActive():
                    self._timer.start()

    def set_banner_flock(self, enabled: bool,
                         emoji: str = "🐼", color: str = "#e94560") -> None:
        """Activate or deactivate the banner flock animation.

        The banner flock flies themed emoji across the top area of the window
        at regular intervals.  It is independent of the click-effects enabled
        state — the overlay stays visible (but transparent) whenever the banner
        flock is running, even if click effects are off.
        """
        self._banner_flock_active = enabled
        if enabled:
            if self._banner_flock is None:
                self._banner_flock = _BannerFlock(self, emoji, color)
            else:
                self._banner_flock.set_emoji(emoji, color)
            self.show()
            if not self._timer.isActive():
                self._timer.start()
            self._banner_flock.start()
        else:
            if self._banner_flock is not None:
                self._banner_flock.stop()
            # If click effects are also disabled, stop the timer and hide.
            if not self._enabled:
                self._timer.stop()
                self.hide()

    def set_effect(self, effect_key: str) -> None:
        self._effect_key = effect_key if effect_key in _SPAWNERS else "default"
        # Manage bat flock timer
        if effect_key == "bat" and self._enabled:
            if self._bat_flock is None:
                self._bat_flock = _BatFlock(self)
            self._bat_flock.start()
        else:
            if self._bat_flock:
                self._bat_flock.stop()
        # Manage fairy flock timer
        if effect_key == "fairy" and self._enabled:
            if self._fairy_flock is None:
                self._fairy_flock = _FairyFlock(self)
            self._fairy_flock.start()
        else:
            if self._fairy_flock:
                self._fairy_flock.stop()

    def set_custom_emoji(self, emoji_list: list[str]) -> None:
        """Update the emoji list used by the 'custom' effect spawner."""
        set_custom_emoji(emoji_list)

    def record_click(self) -> int:
        self._click_count += 1
        return self._click_count

    def _add_particle(self, p: _Particle) -> None:
        self._particles.append(p)
        # Restart the animation timer if it was stopped after the previous
        # burst of particles finished (see _tick).
        if not self._timer.isActive():
            self._timer.start()
        # Hard cap to prevent unbounded growth during rapid clicking.
        # Keep the most recent particles (newest burst) so the effect feels
        # responsive, and cull the oldest ones first.
        if len(self._particles) > 40:
            self._particles = self._particles[-25:]

    # ------------------------------------------------------------------
    # Event filter
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if not self._enabled:
            return False
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            try:
                gp = event.globalPosition().toPoint()
                lp = self._main_window.mapFromGlobal(gp)
                spawner = _SPAWNERS.get(self._effect_key, _spawn_default)
                for p in spawner(lp.x(), lp.y()):
                    self._add_particle(p)
                self._click_count += 1
                self.click_registered.emit(self._click_count)
            except AttributeError:
                pass
        elif event.type() == QEvent.Type.Resize and obj is self._main_window:
            self.setGeometry(self._main_window.rect())
            self.raise_()
        return False

    # ------------------------------------------------------------------
    # Animation tick
    # ------------------------------------------------------------------

    _GRAVITY = 0.4
    # Margin in pixels around each particle's bounding box added to the dirty
    # rect to ensure antialiased edges are fully covered.
    _DIRTY_MARGIN = 6
    # Maximum number of text/emoji particles rendered per frame.
    # Emoji font shaping is expensive; beyond this limit additional text
    # particles are skipped for the frame (they are still tracked and will
    # render in the next frame once earlier ones have faded).
    _MAX_TEXT_PER_FRAME = 8

    def _particle_rect(self, p: _Particle):
        """Return the approximate bounding QRect for a single particle."""
        r = max(6, int(p.size + self._DIRTY_MARGIN))
        return QRect(int(p.x) - r, int(p.y) - r, r * 2, r * 2)

    def _tick(self) -> None:
        if not self._particles:
            return

        # Skip animation while the window is minimised — no visible pixels
        # are produced and we waste CPU driving font rendering for nothing.
        mw = self._main_window
        if mw.isMinimized() or not mw.isVisible():
            return

        # Compute the dirty rect covering all current particle positions
        # BEFORE advancing them — ensures old positions are repainted (cleared).
        dirty = QRect()
        for p in self._particles:
            dirty = dirty.united(self._particle_rect(p))

        ow = self.width()
        oh = self.height()
        surviving = []
        for p in self._particles:
            p.x += p.vx
            p.y += p.vy
            if p.kind not in ("bat_fly", "fairy_fly"):
                p.vy += self._GRAVITY
            p.life -= 0.05   # slightly faster decay → shorter burst, fewer frames rendered
            # Cull ambient (bat/fairy) particles that have completely left the
            # window so they never accumulate off-screen indefinitely.
            if p.kind in ("bat_fly", "fairy_fly"):
                if p.x < -100 or p.x > ow + 100 or p.y < -100 or p.y > oh + 100:
                    continue
            if p.life > 0:
                surviving.append(p)
                # Expand dirty rect to cover new position too
                dirty = dirty.united(self._particle_rect(p))

        self._particles = surviving

        if surviving:
            # Only repaint the region particles actually occupy
            self.update(dirty)
        else:
            self._timer.stop()
            # Full repaint to clear every stale pixel left by the last frame.
            # update(dirty) alone is not enough because WA_NoSystemBackground
            # means Qt never pre-fills the surface, so old pixels linger.
            self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    # Maximum number of QFont objects to keep in the per-size font cache.
    # Each entry is ~1 KB; 32 entries = ~32 KB, comfortably bounded.
    # Eviction uses insertion-order (Python 3.7+ dict guarantee) to remove
    # the oldest (least recently inserted) entry when the limit is reached.
    _FONT_CACHE_MAX = 32

    def _get_font(self, size: int) -> QFont:
        """Return a cached QFont for *size* points (avoids per-particle mutation)."""
        size = max(6, size)
        if size not in self._font_cache:
            if len(self._font_cache) >= self._FONT_CACHE_MAX:
                # Evict the least-recently-inserted entry to keep the cache bounded.
                self._font_cache.pop(next(iter(self._font_cache)))
            f = QFont(_EMOJI_FONT_FAMILIES, size)
            self._font_cache[size] = f
        return self._font_cache[size]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Qt's backing store already re-painted the parent region before
        # calling this paintEvent (standard non-opaque child widget behaviour),
        # so stale particle pixels from previous frames are automatically
        # cleared.  We simply draw the current particles on top.
        if not self._particles:
            painter.end()
            return

        painter.setPen(Qt.PenStyle.NoPen)

        # Track how many text/emoji particles have been drawn this frame.
        # Emoji font rendering is expensive; cap it per frame to bound CPU
        # cost when many text particles pile up during rapid clicking.
        text_drawn = 0

        for p in self._particles:
            alpha = max(0, min(255, int(p.alpha_frac * 220)))
            if alpha < self._MIN_VISIBLE_ALPHA:
                continue  # skip nearly transparent particles — not visible, free CPU
            if p.kind in ("text", "bat_fly", "fairy_fly"):
                if text_drawn >= self._MAX_TEXT_PER_FRAME:
                    continue  # defer this emoji particle to the next frame
                text_drawn += 1
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setFont(self._get_font(int(p.size)))
                painter.setPen(QPen(c))
                painter.drawText(int(p.x), int(p.y), p.text)
                painter.setPen(Qt.PenStyle.NoPen)
            elif p.kind == "drop":
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                w = max(2, int(p.size * 0.6))
                h = max(2, int(p.size * 1.4))
                painter.drawEllipse(int(p.x) - w // 2, int(p.y) - h // 2, w, h)
            else:
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                r = max(2, int(p.size * p.alpha_frac * 0.5 + p.size * 0.5))
                painter.drawEllipse(int(p.x) - r, int(p.y) - r, r * 2, r * 2)

        painter.end()


# ---------------------------------------------------------------------------
# Button press animation
# ---------------------------------------------------------------------------

class ButtonPressAnimator(QObject):
    """Installs lightweight press animations on ``QPushButton`` widgets.

    This class works as an application-level event filter: when a left mouse
    press is detected on a ``QPushButton`` the configured animation is run on
    that button.  The button is never re-parented or removed from its layout;
    all animations restore the button's original geometry when they finish.

    Modes
    -----
    ``"none"``     – no animation (disabled).
    ``"press"``    – button shifts 2 px down on press, springs back.
    ``"fall"``     – button slides 8 px down then springs back.
    ``"shake"``    – button vibrates left/right rapidly.
    ``"shatter"``  – triggers click-effect particles from the button centre.
    ``"bounce"``   – button bounces up then falls back.

    Usage
    -----
        animator = ButtonPressAnimator(main_window, click_effects_overlay)
        animator.set_enabled(True, "press")
    """

    # How many simultaneous animations we allow.  Each takes a negligible
    # amount of memory; this just caps runaway accumulation during rapid
    # clicking.
    _MAX_ACTIVE = 20

    def __init__(self, main_window: QWidget,
                 click_effects: "ClickEffectsOverlay | None" = None):
        super().__init__(main_window)
        self._main_window = main_window
        self._click_effects: "ClickEffectsOverlay | None" = click_effects
        self._mode = "none"
        self._enabled = False
        # Keep references to running animation groups so they are not
        # garbage-collected before they finish.
        self._active: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool, mode: str = "press") -> None:
        """Enable or disable button animations with the given *mode*."""
        if self._enabled == enabled and self._mode == mode:
            return
        self._mode = mode
        app = QApplication.instance()
        if enabled and not self._enabled:
            if app is not None:
                app.installEventFilter(self)
        elif not enabled and self._enabled:
            if app is not None:
                app.removeEventFilter(self)
        self._enabled = enabled

    def set_mode(self, mode: str) -> None:
        """Change the animation mode without altering the enabled state."""
        self._mode = mode

    # ------------------------------------------------------------------
    # Event filter
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        from PyQt6.QtWidgets import QPushButton
        if (self._enabled
                and isinstance(obj, QPushButton)
                and event.type() == QEvent.Type.MouseButtonPress
                and hasattr(event, "button")
                and event.button() == Qt.MouseButton.LeftButton
                and len(self._active) < self._MAX_ACTIVE):
            self._animate(obj)
        return False  # always pass the event through

    # ------------------------------------------------------------------
    # Animation dispatch
    # ------------------------------------------------------------------

    def _animate(self, btn: QWidget) -> None:
        mode = self._mode
        if mode == "none":
            return
        elif mode == "press":
            self._do_slide(btn, dy=2, duration=100)
        elif mode == "fall":
            self._do_slide(btn, dy=8, duration=220)
        elif mode == "bounce":
            self._do_bounce(btn)
        elif mode == "shake":
            self._do_shake(btn)
        elif mode == "shatter":
            self._do_shatter(btn)

    # ------------------------------------------------------------------
    # Individual animation implementations
    # ------------------------------------------------------------------

    def _do_slide(self, btn: QWidget, dy: int = 5, duration: int = 160) -> None:
        """Slide button down by *dy* pixels then spring back."""
        from PyQt6.QtCore import (
            QPropertyAnimation, QSequentialAnimationGroup,
            QEasingCurve, QRect,
        )
        orig = QRect(btn.geometry())
        fallen = QRect(orig.translated(0, dy))

        half = max(30, duration // 2)
        anim_down = QPropertyAnimation(btn, b"geometry", self)
        anim_down.setDuration(half)
        anim_down.setStartValue(orig)
        anim_down.setEndValue(fallen)
        anim_down.setEasingCurve(QEasingCurve.Type.OutQuad)

        anim_up = QPropertyAnimation(btn, b"geometry", self)
        anim_up.setDuration(half)
        anim_up.setStartValue(fallen)
        anim_up.setEndValue(orig)
        anim_up.setEasingCurve(QEasingCurve.Type.InQuad)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(anim_down)
        group.addAnimation(anim_up)
        self._start(group)

    def _do_bounce(self, btn: QWidget) -> None:
        """Button shoots 6 px *up* then falls back with a slight overshoot."""
        from PyQt6.QtCore import (
            QPropertyAnimation, QSequentialAnimationGroup,
            QEasingCurve, QRect,
        )
        orig = QRect(btn.geometry())
        up = QRect(orig.translated(0, -6))
        over = QRect(orig.translated(0, 3))

        a_up = QPropertyAnimation(btn, b"geometry", self)
        a_up.setDuration(100)
        a_up.setStartValue(orig)
        a_up.setEndValue(up)
        a_up.setEasingCurve(QEasingCurve.Type.OutQuad)

        a_down = QPropertyAnimation(btn, b"geometry", self)
        a_down.setDuration(80)
        a_down.setStartValue(up)
        a_down.setEndValue(over)
        a_down.setEasingCurve(QEasingCurve.Type.InQuad)

        a_restore = QPropertyAnimation(btn, b"geometry", self)
        a_restore.setDuration(60)
        a_restore.setStartValue(over)
        a_restore.setEndValue(orig)
        a_restore.setEasingCurve(QEasingCurve.Type.OutBounce)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(a_up)
        group.addAnimation(a_down)
        group.addAnimation(a_restore)
        self._start(group)

    def _do_shake(self, btn: QWidget) -> None:
        """Rapid left/right vibration."""
        from PyQt6.QtCore import (
            QPropertyAnimation, QSequentialAnimationGroup, QRect,
        )
        orig = QRect(btn.geometry())
        dx = 5
        offsets = [-dx, dx, -dx, dx, 0]

        group = QSequentialAnimationGroup(self)
        prev = orig
        for target_dx in offsets:
            a = QPropertyAnimation(btn, b"geometry", self)
            a.setDuration(38)
            a.setStartValue(prev)
            target = QRect(orig.translated(target_dx, 0))
            a.setEndValue(target)
            group.addAnimation(a)
            prev = target

        # Final explicit restore
        restore = QPropertyAnimation(btn, b"geometry", self)
        restore.setDuration(38)
        restore.setStartValue(prev)
        restore.setEndValue(orig)
        group.addAnimation(restore)
        self._start(group)

    def _do_shatter(self, btn: QWidget) -> None:
        """Spawn click-effect particles emanating from the button centre."""
        if self._click_effects is None:
            return
        # Map the button's visual centre to main-window coordinates.
        centre_local = btn.rect().center()
        centre_global = btn.mapToGlobal(centre_local)
        centre_mw = self._main_window.mapFromGlobal(centre_global)
        x, y = centre_mw.x(), centre_mw.y()

        # Spawn particles using the currently active effect spawner.
        key = self._click_effects._effect_key
        spawner = _SPAWNERS.get(key, _spawn_default)
        new_particles = spawner(x, y)
        self._click_effects._particles.extend(new_particles)

        # Ensure the overlay is visible and the timer is running.
        if not self._click_effects._timer.isActive():
            self._click_effects._timer.start()
        if not self._click_effects.isVisible():
            self._click_effects.show()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _start(self, group) -> None:
        """Register *group* in the active list and start it.

        The finished signal removes the group from the active list so it can
        be garbage-collected once the animation is complete.
        """
        self._active.append(group)
        # Use a default-argument capture to avoid closure-over-loop issues.
        group.finished.connect(lambda g=group: self._active.remove(g)
                               if g in self._active else None)
        group.start()
