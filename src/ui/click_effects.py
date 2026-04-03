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

from PyQt6.QtCore import QEvent, QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap)
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
                 "kind", "size", "base_size", "color", "text")

    def __init__(self, x, y, vx, vy, life, kind, size, color, text=""):
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.life = float(life)
        self.max_life = float(life)
        self.kind = kind
        self.size = float(size)
        self.base_size = float(size)  # original size used for wing-flap animation
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
    """Bubbles and sea creatures for Deep Ocean theme, with a ripple ring."""
    particles = []
    ocean_emojis = ["🫧", "🐠", "🐟", "🐙", "🦑", "🌊", "💧", "🫧"]
    ocean_colors = ["#00d4ff", "#00aacc", "#0088aa", "#33ccff", "#006688", "#00ffcc"]
    # One expanding ring for the watery feel
    particles.append(_Particle(x, y, 0.0, 0.0, 0.9, "ring", 5,
                                QColor("#00d4ff"), ""))
    for _ in range(5):  # 5 splash particles + 1 ring = 6 total
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
    """Water ripple / splash with true expanding ring particles."""
    particles = []
    ripple_emojis = ["💧", "🫧", "🌊", "🐚", "🐬"]
    ripple_colors = ["#33aaff", "#00ddee", "#55ccff", "#0099cc", "#77ddff", "#22bbdd"]
    # Two expanding ring particles — the hallmark water ripple visual
    for i in range(2):
        ring_size = 5 + i * 4          # inner ring starts smaller
        color = QColor(ripple_colors[i % len(ripple_colors)])
        # Rings stay at the click point (vx=vy=0) and expand outward
        life = 0.7 + i * 0.35
        particles.append(_Particle(x, y, 0.0, 0.0, life, "ring", ring_size, color, ""))
    # Radial splash particles
    for i in range(3):
        angle = (i / 3) * 2 * math.pi + random.uniform(-0.4, 0.4)
        speed = random.uniform(1.0, 4.5)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        kind = "circle" if random.random() < 0.6 else "text"
        color = QColor(random.choice(ripple_colors))
        text = random.choice(ripple_emojis) if kind == "text" else ""
        size = random.uniform(5, 11) if kind == "circle" else random.uniform(12, 19)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.2),
                                   kind, size, color, text))
    return particles


def _spawn_mermaid(x, y):
    """Mermaid-themed sparkles, fish, ocean magic, and a water ripple ring."""
    particles = []
    mermaid_emojis = ["🧜", "🐠", "🐟", "🦀", "🐚", "💧", "🫧", "🌊", "🪸", "✨"]
    mermaid_colors = ["#00ccaa", "#33ddff", "#aa44ff", "#ff66cc", "#77ffee", "#ff99cc"]
    # One expanding ring for the watery click feel
    particles.append(_Particle(x, y, 0.0, 0.0, 0.8, "ring", 6,
                                QColor(mermaid_colors[0]), ""))
    for _ in range(4):  # 4 splash particles + 1 ring = 5 total
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


def _spawn_slither(x, y):
    """Serpentine particles for Snake Pit theme."""
    particles = []
    snake_emojis = ["🐍", "🐉", "🌿", "💚", "🔪", "☠"]
    snake_colors = ["#00aa44", "#228833", "#44cc00", "#007722", "#33bb55", "#66dd44"]
    for _ in range(5):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(2.0, 6.0)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed + random.uniform(0.5, 1.5)  # slight gravity
        kind = "text" if random.random() < 0.55 else "circle"
        color = QColor(random.choice(snake_colors))
        text = random.choice(snake_emojis) if kind == "text" else ""
        size = random.uniform(12, 22) if kind == "text" else random.uniform(4, 10)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.3),
                                   kind, size, color, text))
    return particles


def _spawn_ghost(x, y):
    """Ghostly wisps for Ghost / haunted themes."""
    particles = []
    ghost_emojis = ["👻", "🕯", "💀", "🌙", "⚗", "🫧", "🌫", "🕷"]
    ghost_colors = ["#ccccff", "#aaaaff", "#ffffff", "#8888cc", "#ddddff", "#9999ee"]
    for _ in range(5):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(0.5, 3.0)  # slow, ethereal movement
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed - random.uniform(0.5, 2.0)  # drifting upward
        kind = "text" if random.random() < 0.6 else "circle"
        color = QColor(random.choice(ghost_colors))
        text = random.choice(ghost_emojis) if kind == "text" else ""
        size = random.uniform(12, 22) if kind == "text" else random.uniform(6, 14)
        particles.append(_Particle(x, y, vx, vy, random.uniform(1.0, 2.2),
                                   kind, size, color, text))
    return particles


def _spawn_slime(x, y):
    """Gooey slime drips and blobs for Slime theme."""
    particles = []
    slime_emojis = ["🟢", "🫧", "💚", "🧪", "☣", "🌿"]
    slime_colors = ["#44cc00", "#66ee00", "#22aa00", "#00cc44", "#88ff22", "#33bb00"]
    for _ in range(5):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1.0, 5.0)
        vx = math.cos(angle) * speed * 0.7   # slower horizontal for gooey feel
        vy = abs(math.sin(angle) * speed) + random.uniform(0.5, 2.0)  # downward drip
        rng = random.random()
        kind = "drop" if rng < 0.4 else ("text" if rng < 0.7 else "circle")
        color = QColor(random.choice(slime_colors))
        text = random.choice(slime_emojis) if kind == "text" else ""
        size = random.uniform(6, 14) if kind in ("drop", "circle") else random.uniform(12, 20)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.8, 1.8),
                                   kind, size, color, text))
    return particles


def _spawn_noodle(x, y):
    """Wiggly noodle strands and food emojis for the Noodle theme."""
    particles = []
    noodle_emojis = ["🍜", "🥢", "🍝", "🍲", "🥡", "🫕", "🫙", "🌾"]
    noodle_colors = ["#ffdd44", "#ffcc22", "#ffee77", "#ffe055", "#ccaa00", "#ffd700"]
    for _ in range(6):
        # Noodles spread in a wide arc — bias upward so they arc nicely
        angle = random.uniform(math.pi * 0.8, math.pi * 2.2)
        speed = random.uniform(1.5, 4.0)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed - random.uniform(1.0, 3.0)  # initial upward kick
        kind = "text" if random.random() < 0.65 else "circle"
        color = QColor(random.choice(noodle_colors))
        text = random.choice(noodle_emojis) if kind == "text" else ""
        size = random.uniform(14, 22) if kind == "text" else random.uniform(5, 9)
        particles.append(_Particle(x, y, vx, vy, random.uniform(1.0, 1.8),
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
    "slither":      _spawn_slither,
    "ghost":        _spawn_ghost,
    "slime":        _spawn_slime,
    "noodle":       _spawn_noodle,
    "custom":       _spawn_custom,
}


# ---------------------------------------------------------------------------
# Gore drip (periodic blood-drop particles for Gore / gore-effect themes)
# ---------------------------------------------------------------------------

class _GoreDrip(QObject):
    """Spawns slow-falling blood-drop particles from button tops periodically.

    Activated whenever the active click-effect key is ``"gore"``.  Drops fall
    from random buttons in the main window, giving the UI the impression of
    perpetually dripping blood.
    """

    _DRIP_INTERVAL_MS = 900  # spawn a new drip cluster roughly every 0.9 s

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._DRIP_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn_drip)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn_drip(self) -> None:
        from PyQt6.QtWidgets import QPushButton
        main_window = self._overlay._main_window
        if main_window is None or not main_window.isVisible():
            return
        btns = [w for w in main_window.findChildren(QPushButton)
                if w.isVisible() and w.width() > 20]
        if not btns:
            return
        btn = random.choice(btns)
        # Map top-center of button into main-window coordinates
        top_local_x = btn.rect().center().x()
        top_local_y = 0
        from PyQt6.QtCore import QPoint
        global_pt = btn.mapToGlobal(QPoint(top_local_x, top_local_y))
        mw_pt = main_window.mapFromGlobal(global_pt)
        x, y = mw_pt.x(), mw_pt.y()

        drip_colors = ["#880000", "#aa0000", "#cc1133", "#660000", "#990000", "#bb0022",
                       "#7a0000", "#9e1010"]
        # Spawn 1-4 realistic blood drips of mixed sizes
        count = random.randint(1, 4)
        for _ in range(count):
            # Slow horizontal drift + fast downward fall for flowing blood effect
            vx = random.uniform(-0.6, 0.6)
            vy = random.uniform(1.8, 4.5)
            color = QColor(random.choice(drip_colors))
            # Mix large heavy blobs with thin fast-dripping streaks
            rng = random.random()
            if rng < 0.55:
                # Smooth teardrop blood drop
                size = random.uniform(6, 14)
                kind = "blood_drip"
            elif rng < 0.80:
                # Heavy goopy blob
                size = random.uniform(8, 16)
                kind = "circle"
            else:
                # Small satellite droplet
                size = random.uniform(3, 6)
                kind = "circle"
            life = random.uniform(1.2, 2.8)
            p = _Particle(
                x + random.uniform(-8, 8), y,
                vx, vy, life,
                kind, size, color, "",
            )
            self._overlay._particles.append(p)

        # Occasionally spawn a splat cluster (multiple tiny blobs spreading sideways)
        # to simulate a drop hitting a surface below.
        if random.random() < 0.20:
            splat_y = y + random.randint(30, 80)
            splat_color = QColor(random.choice(drip_colors))
            for _ in range(random.randint(2, 4)):
                svx = random.uniform(-3.0, 3.0)
                svy = random.uniform(-0.5, 1.0)
                self._overlay._particles.append(_Particle(
                    x + random.uniform(-4, 4), splat_y,
                    svx, svy, random.uniform(0.4, 0.9),
                    "circle", random.uniform(3, 7), splat_color, "",
                ))

        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


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


# ---------------------------------------------------------------------------
# Fish flock (periodic fish swimming for Mermaid theme)
# ---------------------------------------------------------------------------

class _FishFlock(QObject):
    """Spawns fish that swim across the middle/lower portion of the window.

    Activated whenever the active click-effect key is ``"mermaid"``.  Fish
    swim from left-to-right (or right-to-left) through the window, giving
    the impression of an underwater aquarium beneath the UI.
    """

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._launch)
        self._timer.setInterval(random.randint(4000, 9000))

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _launch(self) -> None:
        self._timer.setInterval(random.randint(4000, 10000))
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        count = random.randint(1, 4)
        left_to_right = random.random() < 0.5
        speed_sign = 1 if left_to_right else -1
        fish_emojis = ["🐠", "🐟", "🐡", "🦈", "🦑", "🐬", "🪸"]
        fish_colors = ["#00ccaa", "#33ddff", "#0099cc", "#aa44ff", "#ff99cc"]
        # Fish swim through the middle-to-lower band (40–85% of window height)
        band_lo = max(80, int(h * 0.40))
        band_hi = max(band_lo + 20, int(h * 0.85))
        for i in range(count):
            y_start = random.randint(band_lo, band_hi)
            x_start = (random.randint(-30, -10) if left_to_right
                       else w + random.randint(10, 30))
            speed = random.uniform(2.0, 5.5) * speed_sign
            vy = random.uniform(-0.5, 0.5)
            life = (w + 80) / max(abs(speed), 1) * 0.05 + random.uniform(0.3, 1.0)
            emoji = random.choice(fish_emojis)
            color = QColor(random.choice(fish_colors))
            p = _Particle(
                x_start + i * random.randint(25, 65), y_start,
                speed, vy, life,
                # "fairy_fly" renders emoji text at a fixed size — ideal for
                # emoji fish that just need to drift horizontally off-screen.
                "fairy_fly", random.uniform(16, 26),
                color, emoji,
            )
            self._overlay._add_particle(p)


# ---------------------------------------------------------------------------
# Alien beam (periodic tractor beam for Alien theme)
# ---------------------------------------------------------------------------

class _AlienBeam(QObject):
    """Periodically fires a tractor-beam effect: green particles rise from a
    random button toward the top of the window, simulating alien abduction.

    Activated whenever the active click-effect key is ``"alien"``.
    """

    _INTERVAL_LO = 5000
    _INTERVAL_HI = 12000

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fire_beam)
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _fire_beam(self) -> None:
        """Spawn a vertical column of rising green particles from a random button."""
        from PyQt6.QtWidgets import QPushButton
        from PyQt6.QtCore import QPoint as _QPoint
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))
        main_window = self._overlay._main_window
        if main_window is None or not main_window.isVisible():
            return
        btns = [w for w in main_window.findChildren(QPushButton)
                if w.isVisible() and w.width() > 30]
        if not btns:
            return
        btn = random.choice(btns)
        global_pt = btn.mapToGlobal(_QPoint(btn.rect().center().x(), 0))
        mw_pt = main_window.mapFromGlobal(global_pt)
        bx, by = mw_pt.x(), mw_pt.y()
        beam_colors = ["#00ff88", "#44ff44", "#88ff00", "#00ffcc", "#ccff00"]
        # Spawn a dense column of particles rising from button top to window top
        steps = max(4, by // 18)
        for i in range(steps):
            y_pos = by - i * 18
            p = _Particle(
                bx + random.uniform(-6, 6), y_pos,
                random.uniform(-0.3, 0.3), random.uniform(-3.0, -1.5),
                random.uniform(0.4, 0.9),
                "circle", random.uniform(4, 9),
                QColor(random.choice(beam_colors)), "",
            )
            self._overlay._add_particle(p)
        # Abduction emoji at beam origin for dramatic flair
        abduct_emojis = ["🛸", "👽", "⬆", "💫"]
        for _ in range(2):
            p = _Particle(
                bx, by,
                random.uniform(-1.0, 1.0), random.uniform(-4.0, -2.0),
                random.uniform(0.8, 1.5),
                "text", random.uniform(14, 22),
                QColor(random.choice(beam_colors)), random.choice(abduct_emojis),
            )
            self._overlay._add_particle(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Slime drip (periodic green-slime drops for Slime theme)
# ---------------------------------------------------------------------------

class _SlimeDrip(QObject):
    """Spawns gooey slime-drop particles falling from button tops periodically.

    Activated whenever the active click-effect key is ``"slime"``.  Drops use
    the ``"drop"`` particle kind (teardrop shape) in neon-green slime colours,
    creating the impression of perpetually oozing buttons.
    """

    _DRIP_INTERVAL_MS = 1100  # slightly slower / lazier than gore drip

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._DRIP_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn_drip)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn_drip(self) -> None:
        from PyQt6.QtWidgets import QPushButton
        from PyQt6.QtCore import QPoint as _QPoint
        main_window = self._overlay._main_window
        if main_window is None or not main_window.isVisible():
            return
        btns = [w for w in main_window.findChildren(QPushButton)
                if w.isVisible() and w.width() > 20]
        if not btns:
            return
        btn = random.choice(btns)
        global_pt = btn.mapToGlobal(_QPoint(btn.rect().center().x(), 0))
        mw_pt = main_window.mapFromGlobal(global_pt)
        x, y = mw_pt.x(), mw_pt.y()
        slime_colors = ["#44cc00", "#66ee00", "#22aa00", "#00cc44", "#88ff22", "#33bb00"]
        count = random.randint(1, 3)
        for _ in range(count):
            p = _Particle(
                x + random.uniform(-5, 5), y,
                random.uniform(-0.5, 0.5), random.uniform(1.2, 3.0),
                random.uniform(1.0, 2.5),
                "drop", random.uniform(5, 10),
                QColor(random.choice(slime_colors)), "",
            )
            self._overlay._add_particle(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Water drip (periodic water-drop particles for aquatic / ripple themes)
# ---------------------------------------------------------------------------

class _WaterDrip(QObject):
    """Spawns thin, fast water-drop particles from button tops periodically.

    Activated for ``"ripple"``, ``"ocean"``, and ``"mermaid"`` effect keys.
    Uses the ``"drip_streak"`` particle kind in translucent cyan/blue tones,
    creating the impression of water trickling down the UI — thinner and more
    fluid than the blood-drip counterpart.
    """

    _DRIP_INTERVAL_MS = 700  # slightly faster than gore drip — water runs freely

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._DRIP_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn_drip)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn_drip(self) -> None:
        from PyQt6.QtWidgets import QPushButton
        from PyQt6.QtCore import QPoint as _QPoint
        main_window = self._overlay._main_window
        if main_window is None or not main_window.isVisible():
            return
        btns = [w for w in main_window.findChildren(QPushButton)
                if w.isVisible() and w.width() > 20]
        if not btns:
            return
        btn = random.choice(btns)
        global_pt = btn.mapToGlobal(_QPoint(btn.rect().center().x(), 0))
        mw_pt = main_window.mapFromGlobal(global_pt)
        x, y = mw_pt.x(), mw_pt.y()

        # Translucent blue/cyan palette — lighter than blood, more fluid
        water_colors = [
            "#00aacc", "#00ccee", "#22aabb", "#0088bb", "#33ccdd",
            "#44bbdd", "#007799", "#00ddff",
        ]
        count = random.randint(1, 3)
        for _ in range(count):
            vx = random.uniform(-0.3, 0.3)   # thin drips run almost straight down
            vy = random.uniform(2.5, 5.5)    # faster than blood — water is thinner
            color = QColor(random.choice(water_colors))
            # Water drops are thinner than blood
            rng = random.random()
            if rng < 0.65:
                kind = "water_drip"
                size = random.uniform(3, 8)
            else:
                # Tiny bubble / droplet
                kind = "circle"
                size = random.uniform(2, 5)
            # Water is partially transparent — reduce max alpha via short life
            life = random.uniform(0.8, 1.8)
            p = _Particle(
                x + random.uniform(-6, 6), y,
                vx, vy, life,
                kind, size, color, "",
            )
            self._overlay._add_particle(p)

        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Snow drift (ambient snowfall for Ice / Arctic themes)
# ---------------------------------------------------------------------------

class _SnowDrift(QObject):
    """Continuously drifts snowflakes down from the top of the window.

    Activated for ``"ice"`` effect key.  Flakes spawn from the entire top edge
    at varying speeds and sizes, creating a gentle layered snowfall effect.
    """

    _SPAWN_INTERVAL_MS = 400

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        snow_colors = [
            "#ffffff", "#e8f4ff", "#cce8ff", "#ddeeff",
            "#aaccff", "#bbddff", "#f0f8ff",
        ]
        count = random.randint(2, 5)
        for _ in range(count):
            x = random.uniform(0, w)
            vx = random.uniform(-0.4, 0.4)
            vy = random.uniform(0.4, 1.5)
            size = random.uniform(2.5, 6.0)
            life = (h / max(vy, 0.1)) * 0.05 + random.uniform(0.5, 2.0)
            p = _Particle(
                x, random.uniform(-10, 5),
                vx, vy, life,
                "snow", size, QColor(random.choice(snow_colors)), "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Ember drift (rising embers for Fire / Lava themes)
# ---------------------------------------------------------------------------

class _EmberDrift(QObject):
    """Continuously drifts glowing embers upward from the bottom of the window.

    Activated for ``"fire"`` effect key.  Particles rise with a gentle
    horizontal wobble and fade, suggesting heat haze rising off burning coals.
    """

    _SPAWN_INTERVAL_MS = 280

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        ember_colors = [
            "#ff8800", "#ff6600", "#ffaa00", "#ff4400",
            "#ffcc44", "#ff3300", "#ffdd00", "#cc2200",
        ]
        count = random.randint(2, 5)
        for _ in range(count):
            x = random.uniform(0, w)
            vx = random.uniform(-0.8, 0.8)
            vy = random.uniform(-3.5, -1.5)
            size = random.uniform(1.5, 4.5)
            life = (h / max(abs(vy), 0.1)) * 0.05 + random.uniform(0.3, 1.5)
            p = _Particle(
                x, h + random.uniform(0, 20),
                vx, vy, life,
                "ember", size, QColor(random.choice(ember_colors)), "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Sakura petal drift (falling petals for Sakura / Spring themes)
# ---------------------------------------------------------------------------

class _SakuraPetal(QObject):
    """Spawns drifting cherry-blossom petals for the Sakura / Spring themes.

    Petals enter from the top edge and drift diagonally downward with gentle
    side sway.  The ``"fairy_fly"`` particle kind is reused so the existing
    size-oscillation produces a pleasing tumble animation.
    """

    _SPAWN_INTERVAL_MS = 700

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._launch)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _launch(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        petal_emojis = ["🌸", "🌺", "🌼", "🌷"]
        petal_colors = [
            "#ffb7c5", "#ff99aa", "#ffd4dc", "#ffaabb",
            "#ff88aa", "#ffc0cb", "#f5c6d0",
        ]
        count = random.randint(1, 3)
        for _ in range(count):
            x = random.uniform(-20, w + 20)
            vy = random.uniform(0.4, 1.2)
            vx = random.uniform(-0.6, 0.6)
            life = (h / max(vy, 0.1)) * 0.05 + random.uniform(0.5, 2.0)
            size = random.uniform(14, 22)
            emoji = random.choice(petal_emojis)
            color = QColor(random.choice(petal_colors))
            p = _Particle(
                x, random.uniform(-20, 0),
                vx, vy, life,
                "fairy_fly", size, color, emoji,
            )
            self._overlay._add_particle(p)


# ---------------------------------------------------------------------------
# Star shoot (shooting stars for Galaxy / Space themes)
# ---------------------------------------------------------------------------

class _StarShoot(QObject):
    """Periodically fires shooting-star streaks across the window.

    Activated for ``"galaxy"`` and ``"galaxy_otter"`` effect keys.  A dense
    cluster of bright white/gold particles is spawned along a diagonal
    trajectory; brightness fades head-to-tail to simulate a single fast streak.
    """

    _INTERVAL_LO = 3500
    _INTERVAL_HI = 9000

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._shoot)
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _shoot(self) -> None:
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        angle_deg = random.uniform(10, 40)
        angle = math.radians(angle_deg)
        x_start = random.uniform(-30, w * 0.35)
        y_start = random.uniform(0, h * 0.45)
        speed = random.uniform(18, 32)
        dx = math.cos(angle) * speed
        dy = math.sin(angle) * speed
        star_colors = [
            "#ffffff", "#ffffee", "#ffffcc", "#aaaaff", "#ddddff", "#ffff88",
        ]
        n = random.randint(8, 14)
        for i in range(n):
            frac = i / n
            tail_x = x_start - dx * frac * 0.15
            tail_y = y_start - dy * frac * 0.15
            size = max(1.5, 5.5 - frac * 4.0)
            life_base = random.uniform(0.6, 1.2)
            life = life_base * (1.0 - frac * 0.6)
            p = _Particle(
                tail_x + random.uniform(-1, 1),
                tail_y + random.uniform(-1, 1),
                dx * 0.18, dy * 0.18,
                life,
                "circle", size,
                QColor(random.choice(star_colors)), "",
            )
            self._overlay._particles.append(p)
        emoji_p = _Particle(
            x_start, y_start,
            dx * 0.12, dy * 0.12,
            random.uniform(0.5, 0.9),
            "text", 16, QColor("#ffffcc"), "✨",
        )
        self._overlay._particles.append(emoji_p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Bubble rise (rising bubbles for Ocean / Ripple themes)
# ---------------------------------------------------------------------------

class _BubbleRise(QObject):
    """Continuously rises translucent bubbles from the bottom of the window.

    Activated for ``"ocean"`` and ``"ripple"`` effect keys.  Bubbles sway
    gently as they ascend and fade near the top, giving an underwater atmosphere.
    """

    _SPAWN_INTERVAL_MS = 550

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        bubble_colors = [
            "#aaddff", "#bbeeff", "#99ccff", "#cceeff",
            "#88bbff", "#ddf4ff", "#b3e5fc",
        ]
        count = random.randint(1, 3)
        for _ in range(count):
            x = random.uniform(20, w - 20)
            vx = random.uniform(-0.25, 0.25)
            vy = random.uniform(-2.0, -0.8)
            size = random.uniform(6, 18)
            life = (h / max(abs(vy), 0.1)) * 0.05 + random.uniform(0.3, 1.2)
            p = _Particle(
                x, h + random.uniform(0, 30),
                vx, vy, life,
                "bubble", size, QColor(random.choice(bubble_colors)), "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Neon flicker (spark arcs for Neon / Cyber themes)
# ---------------------------------------------------------------------------

class _NeonFlicker(QObject):
    """Randomly sparks neon-coloured flashes near UI buttons.

    Activated for ``"neon"`` effect key.  Bright short-lived sparks arc
    outward from button edges in all directions, mimicking unstable neon-tube
    discharge.
    """

    _INTERVAL_LO = 1200
    _INTERVAL_HI = 3200

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._flicker)
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _flicker(self) -> None:
        from PyQt6.QtWidgets import QPushButton
        from PyQt6.QtCore import QPoint as _QPoint
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))
        main_window = self._overlay._main_window
        if main_window is None or not main_window.isVisible():
            return
        btns = [w for w in main_window.findChildren(QPushButton)
                if w.isVisible() and w.width() > 20]
        if not btns:
            return
        btn = random.choice(btns)
        r = btn.rect()
        edge = random.choice(["top", "bottom", "left", "right"])
        if edge == "top":
            lx, ly = random.randint(0, r.width()), 0
        elif edge == "bottom":
            lx, ly = random.randint(0, r.width()), r.height()
        elif edge == "left":
            lx, ly = 0, random.randint(0, r.height())
        else:
            lx, ly = r.width(), random.randint(0, r.height())
        global_pt = btn.mapToGlobal(_QPoint(lx, ly))
        mw_pt = main_window.mapFromGlobal(global_pt)
        bx, by = mw_pt.x(), mw_pt.y()
        neon_colors = [
            "#00ffff", "#ff00ff", "#00ff88", "#ff0099",
            "#aaff00", "#ff6600", "#cc00ff", "#00ccff",
        ]
        count = random.randint(5, 12)
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(2.0, 6.0)
            p = _Particle(
                bx + random.uniform(-3, 3),
                by + random.uniform(-3, 3),
                math.cos(angle) * speed, math.sin(angle) * speed,
                random.uniform(0.25, 0.70),
                "circle", random.uniform(2, 5),
                QColor(random.choice(neon_colors)), "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Ghost wisp (drifting spectral figures for Goth / Ghost themes)
# ---------------------------------------------------------------------------

class _GhostWisp(QObject):
    """Slowly drifts ghostly emoji wisps across the window.

    Activated for ``"goth"`` and ``"ghost"`` effect keys.  Spectral figures
    drift with slow, eerie undulation — barely visible, just enough to create
    a haunted atmosphere without distracting from the UI.
    """

    _INTERVAL_LO = 5000
    _INTERVAL_HI = 12000

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._haunt)
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _haunt(self) -> None:
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        ghost_emojis = ["👻", "💀", "🕷️", "🌑", "🕸️"]
        ghost_colors = [
            "#aaaacc", "#bbbbdd", "#9999bb", "#ccccee",
            "#8888aa", "#ffffff", "#ddddff",
        ]
        count = random.randint(1, 2)
        left_to_right = random.random() < 0.5
        speed_sign = 1 if left_to_right else -1
        for i in range(count):
            y_start = random.randint(20, max(21, int(h * 0.8)))
            x_start = (random.randint(-40, -10) if left_to_right
                       else w + random.randint(10, 40))
            speed = random.uniform(0.6, 2.2) * speed_sign
            vy = random.uniform(-0.3, 0.3)
            life = (w + 80) / max(abs(speed), 1) * 0.05 + random.uniform(0.5, 1.5)
            size = random.uniform(18, 28)
            emoji = random.choice(ghost_emojis)
            color = QColor(random.choice(ghost_colors))
            p = _Particle(
                x_start + i * random.randint(30, 80), y_start,
                speed, vy, life,
                "fairy_fly", size, color, emoji,
            )
            self._overlay._add_particle(p)



# ---------------------------------------------------------------------------
# Rainbow confetti (drifting colourful confetti for Rainbow / Candy themes)
# ---------------------------------------------------------------------------

class _RainbowConfetti(QObject):
    """Spawns colourful rotating confetti rectangles that drift across the window.

    Activated for the ``"rainbow"`` effect key.  Pieces enter from random edges,
    spin as they travel, and cycle through the full hue spectrum so the screen
    always feels celebratory and chaotic.
    """

    _SPAWN_INTERVAL_MS = 160

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn)
        self._hue = 0.0  # cycling hue 0-360

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        count = random.randint(3, 7)
        for _ in range(count):
            # Pick a random starting edge (top, bottom, left, right)
            edge = random.randint(0, 3)
            if edge == 0:   # top
                x, y = random.uniform(0, w), random.uniform(-15, 0)
                vx, vy = random.uniform(-1.2, 1.2), random.uniform(0.8, 2.5)
            elif edge == 1:  # bottom
                x, y = random.uniform(0, w), h + random.uniform(0, 15)
                vx, vy = random.uniform(-1.2, 1.2), random.uniform(-2.5, -0.8)
            elif edge == 2:  # left
                x, y = random.uniform(-15, 0), random.uniform(0, h)
                vx, vy = random.uniform(0.8, 2.5), random.uniform(-0.8, 0.8)
            else:            # right
                x, y = w + random.uniform(0, 15), random.uniform(0, h)
                vx, vy = random.uniform(-2.5, -0.8), random.uniform(-0.8, 0.8)
            # Cycling hue with slight per-particle offset
            hue = (self._hue + random.uniform(-30, 30)) % 360
            color = QColor.fromHsvF(hue / 360.0, 1.0, 1.0)
            size = random.uniform(5.0, 12.0)
            # life proportional to travel distance
            dist = max(w, h)
            speed = max(abs(vx), abs(vy), 0.1)
            life = (dist / speed) * 0.05 + random.uniform(0.5, 1.5)
            # Encode spin turns (full rotations over lifetime) in text
            spin = random.uniform(1.5, 4.0)
            p = _Particle(x, y, vx, vy, life, "confetti", size, color, f"{spin:.2f}")
            self._overlay._particles.append(p)
        self._hue = (self._hue + 15) % 360
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Star dust (twinkling micro-stars for Sparkle themes)
# ---------------------------------------------------------------------------

class _StarDust(QObject):
    """Spawns tiny twinkling 4-pointed stars at random screen positions.

    Activated for the ``"sparkle"`` effect key.  Stars appear at random
    positions with almost no velocity, flicker briefly, and fade — like
    light glinting off crystal or glitter scattered across the UI.
    """

    _SPAWN_INTERVAL_MS = 200

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        star_colors = [
            "#fffde7", "#fff9c4", "#ffffff", "#e8f4ff",
            "#ffe4e1", "#f3e5f5", "#e0f7fa", "#fff8e1",
        ]
        count = random.randint(3, 8)
        for _ in range(count):
            x = random.uniform(0, w)
            y = random.uniform(0, h)
            vx = random.uniform(-0.3, 0.3)
            vy = random.uniform(-0.3, 0.3)
            size = random.uniform(3.0, 9.0)
            life = random.uniform(0.8, 2.5)
            p = _Particle(
                x, y, vx, vy, life,
                "star_dust", size, QColor(random.choice(star_colors)), "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Bamboo leaf drift (falling leaves for Panda themes)
# ---------------------------------------------------------------------------

class _BambooLeaf(QObject):
    """Spawns rotating bamboo leaves that drift down from the top edge.

    Activated for the ``"panda"`` effect key.  Each leaf is an elongated
    ellipse in various greens that drifts diagonally downward with gentle
    rotation — evoking the tranquil atmosphere of a bamboo grove.
    """

    _SPAWN_INTERVAL_MS = 550

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        leaf_colors = [
            "#4caf50", "#388e3c", "#66bb6a", "#81c784",
            "#a5d6a7", "#558b2f", "#8bc34a", "#c5e1a5",
        ]
        count = random.randint(1, 3)
        for _ in range(count):
            x = random.uniform(0, w)
            vx = random.uniform(-0.9, 0.9)
            vy = random.uniform(0.6, 1.8)
            size = random.uniform(7.0, 16.0)
            life = (h / max(vy, 0.1)) * 0.05 + random.uniform(0.5, 2.0)
            # spin turns encoded in text
            spin = random.uniform(0.4, 1.8) * random.choice([-1, 1])
            p = _Particle(
                x, random.uniform(-20, 0),
                vx, vy, life,
                "bamboo_leaf", size, QColor(random.choice(leaf_colors)),
                f"{spin:.2f}",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Otter bubble clusters (rising bubble puffs for Otter themes)
# ---------------------------------------------------------------------------

class _OtterBubble(QObject):
    """Spawns cheerful clusters of tiny bubbles rising from the bottom edge.

    Activated for the ``"otter"`` effect key.  Bubbles are smaller and
    more densely grouped than the ocean bubbles, with a warm teal tint
    suggesting a playful otter paddling in shallow water.
    """

    _SPAWN_INTERVAL_MS = 480

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._spawn)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _spawn(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        bubble_colors = [
            "#80deea", "#4dd0e1", "#b2ebf2", "#26c6da",
            "#a7ffeb", "#84ffff", "#e0f7fa",
        ]
        # Spawn 1-3 cluster centres
        cluster_count = random.randint(1, 3)
        for _ in range(cluster_count):
            cx = random.uniform(w * 0.05, w * 0.95)
            cy = h + random.uniform(0, 20)
            # 3-6 tiny bubbles per cluster
            for _ in range(random.randint(3, 6)):
                x = cx + random.uniform(-20, 20)
                y = cy + random.uniform(-10, 10)
                vx = random.uniform(-0.4, 0.4)
                vy = random.uniform(-1.8, -0.8)
                size = random.uniform(3.0, 8.0)
                life = (h / max(abs(vy), 0.1)) * 0.05 + random.uniform(0.3, 1.2)
                p = _Particle(
                    x, y, vx, vy, life,
                    "otter_bubble", size, QColor(random.choice(bubble_colors)), "",
                )
                self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Shark fin glide (menacing fins for the Shark Bait theme)
# ---------------------------------------------------------------------------

class _SharkFin(QObject):
    """Spawns shark-fin silhouettes that glide slowly across the window.

    Activated for the ``"shark"`` effect key.  Fins appear infrequently
    and travel from one side of the screen to the other at the lower
    portion of the window — slow, dark, and ominous.
    """

    _INTERVAL_LO = 2500
    _INTERVAL_HI = 5500

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._glide)
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _glide(self) -> None:
        self._timer.setInterval(random.randint(self._INTERVAL_LO, self._INTERVAL_HI))
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        fin_colors = ["#37474f", "#455a64", "#546e7a", "#263238", "#1a237e"]
        count = random.randint(1, 2)
        left_to_right = random.random() < 0.5
        speed_sign = 1 if left_to_right else -1
        for i in range(count):
            y_start = random.randint(int(h * 0.55), int(h * 0.82))
            x_start = (random.randint(-30, -10) if left_to_right
                       else w + random.randint(10, 30))
            speed = random.uniform(1.2, 3.0) * speed_sign
            vy = random.uniform(-0.15, 0.15)
            life = (w + 80) / max(abs(speed), 1) * 0.05 + random.uniform(0.2, 0.8)
            size = random.uniform(18, 34)
            color = QColor(random.choice(fin_colors))
            p = _Particle(
                x_start + i * random.randint(60, 140), y_start,
                speed, vy, life,
                "shark_fin", size, color, "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Slither wiggler (sinusoidal snake traces for the Snake Pit theme)
# ---------------------------------------------------------------------------

class _SlitherWiggler(QObject):
    """Spawns sinusoidal "snake body" particle chains that wriggle across.

    Activated for the ``"slither"`` effect key.  Segments are spawned along
    a sine-wave path at a fixed horizontal y-band, giving the impression of
    a snake slithering across the screen from edge to edge.
    """

    _SPAWN_INTERVAL_MS = 1800

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._slither)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _slither(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        snake_colors = [
            "#558b2f", "#689f38", "#7cb342", "#8bc34a",
            "#f9a825", "#f57f17", "#33691e",
        ]
        left_to_right = random.random() < 0.5
        speed = random.uniform(1.8, 3.5) * (1 if left_to_right else -1)
        base_y = random.uniform(h * 0.2, h * 0.8)
        amplitude = random.uniform(h * 0.04, h * 0.10)
        wavelength = random.uniform(80, 160)
        n_segs = 18
        seg_spacing = 12
        color_choice = random.choice(snake_colors)
        for i in range(n_segs):
            # Stagger spawn time so segments travel together as a snake body.
            phase = i * seg_spacing
            if left_to_right:
                x_start = -phase - random.uniform(0, 20)
            else:
                x_start = w + phase + random.uniform(0, 20)
            y_offset = amplitude * math.sin(phase / wavelength * 2 * math.pi)
            # Vary colour slightly along the body (head lighter, tail darker)
            hue_shift = int((i / n_segs) * 20)
            c = QColor(color_choice)
            c = c.lighter(100 + hue_shift) if i < n_segs // 2 else c.darker(100 + hue_shift)
            size = random.uniform(5.0, 9.0) * (1.0 - 0.3 * i / n_segs)
            life = (w + 200) / max(abs(speed), 1) * 0.05 + random.uniform(0.5, 1.5)
            p = _Particle(
                x_start, base_y + y_offset,
                speed, 0.0, life,
                "wriggle", size, c, "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()


# ---------------------------------------------------------------------------
# Noodle strand drift (flowing pasta strands for the Noodle theme)
# ---------------------------------------------------------------------------

class _NoodleStrand(QObject):
    """Spawns flowing colourful noodle-strand particle chains.

    Activated for the ``"noodle"`` effect key.  Segments are spawned in a
    gently curved arc that drifts upward with slight side sway — like noodles
    being tossed into the air.  Warm creamy and bright colours reinforce the
    pasta-themed identity.
    """

    _SPAWN_INTERVAL_MS = 350

    def __init__(self, overlay: "ClickEffectsOverlay"):
        super().__init__(overlay)
        self._overlay = overlay
        self._timer = QTimer(self)
        self._timer.setInterval(self._SPAWN_INTERVAL_MS)
        self._timer.timeout.connect(self._toss)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _toss(self) -> None:
        w = self._overlay.width()
        h = self._overlay.height()
        if w <= 0 or h <= 0:
            return
        noodle_colors = [
            "#ffe082", "#ffcc80", "#ffab40", "#ff8a65",
            "#f48fb1", "#ce93d8", "#80cbc4", "#80deea",
        ]
        # Spawn a curved chain of dots from the bottom edge
        n_segs = random.randint(10, 18)
        base_x = random.uniform(w * 0.1, w * 0.9)
        base_y = h + random.uniform(0, 20)
        curve_amp = random.uniform(20, 55)
        curve_freq = random.uniform(0.08, 0.18)
        color_choice = random.choice(noodle_colors)
        for i in range(n_segs):
            x = base_x + curve_amp * math.sin(i * curve_freq) + i * random.uniform(-3, 3)
            y = base_y - i * random.uniform(8, 14)
            vx = random.uniform(-0.5, 0.5)
            vy = random.uniform(-1.5, -0.6)
            size = random.uniform(4.0, 9.0)
            life = (h / max(abs(vy), 0.1)) * 0.05 + random.uniform(0.5, 2.0)
            c = QColor(color_choice)
            c = c.lighter(100 + int(i * 4))
            p = _Particle(
                x, y, vx, vy, life,
                "noodle_strand", size, c, "",
            )
            self._overlay._particles.append(p)
        if not self._overlay._timer.isActive():
            self._overlay._timer.start()
        if not self._overlay.isVisible():
            self._overlay.show()



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
        self._gore_drip: _GoreDrip | None = None
        self._fish_flock: _FishFlock | None = None
        self._alien_beam: _AlienBeam | None = None
        self._slime_drip: _SlimeDrip | None = None
        self._water_drip: _WaterDrip | None = None
        self._banner_flock: _BannerFlock | None = None
        self._banner_flock_active: bool = False
        self._snow_drift: _SnowDrift | None = None
        self._ember_drift: _EmberDrift | None = None
        self._sakura_petal: _SakuraPetal | None = None
        self._star_shoot: _StarShoot | None = None
        self._bubble_rise: _BubbleRise | None = None
        self._neon_flicker: _NeonFlicker | None = None
        self._ghost_wisp: _GhostWisp | None = None
        self._rainbow_confetti: _RainbowConfetti | None = None
        self._star_dust: _StarDust | None = None
        self._bamboo_leaf: _BambooLeaf | None = None
        self._otter_bubble: _OtterBubble | None = None
        self._shark_fin: _SharkFin | None = None
        self._slither_wiggler: _SlitherWiggler | None = None
        self._noodle_strand: _NoodleStrand | None = None
        # Background drip state (independent of click effects)
        self._bg_drip_enabled: bool = False
        self._bg_drip_type: str = "blood"  # "blood" or "water"
        # Background flock state (independent of banner animation)
        self._bg_flock_enabled: bool = False
        # Background ambient state (independent of click effects / theme)
        self._bg_ambient_enabled: bool = False
        self._bg_ambient_type: str = "none"
        self._font = QFont(_EMOJI_FONT_FAMILIES, 14)
        # Cache QFont objects per integer point-size to avoid repeated
        # mutations and implicit font-metric recalculations each frame.
        self._font_cache: dict[int, QFont] = {}
        # Bounding rect from the previous frame so we can union it with the
        # current frame and only request a repaint of the dirty region.
        self._prev_dirty = None

        self._timer = QTimer(self)
        self._timer.setInterval(33)   # ~30 fps – smooth animation with reasonable CPU cost
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
            if self._fish_flock:
                self._fish_flock.stop()
            if self._alien_beam:
                self._alien_beam.stop()
            if self._slime_drip:
                self._slime_drip.stop()
            if self._snow_drift and not (self._bg_ambient_enabled and self._bg_ambient_type == "snow"):
                self._snow_drift.stop()
            if self._ember_drift and not (self._bg_ambient_enabled and self._bg_ambient_type == "ember"):
                self._ember_drift.stop()
            if self._sakura_petal and not (self._bg_ambient_enabled and self._bg_ambient_type == "sakura"):
                self._sakura_petal.stop()
            if self._star_shoot and not (self._bg_ambient_enabled and self._bg_ambient_type == "stars"):
                self._star_shoot.stop()
            if self._bubble_rise and not (self._bg_ambient_enabled and self._bg_ambient_type == "bubbles"):
                self._bubble_rise.stop()
            if self._neon_flicker and not (self._bg_ambient_enabled and self._bg_ambient_type == "neon"):
                self._neon_flicker.stop()
            if self._ghost_wisp and not (self._bg_ambient_enabled and self._bg_ambient_type == "ghost"):
                self._ghost_wisp.stop()
            # Only hide the overlay if the banner flock is also inactive.
            # When banner flock is running we still need the overlay visible
            # so flying particles can be rendered even without click effects.
            if not self._banner_flock_active and not self._bg_drip_enabled and not self._bg_ambient_enabled:
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
            # If click effects and bg drip are also disabled, stop the timer and hide.
            if not self._enabled and not self._bg_drip_enabled and not self._bg_ambient_enabled:
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
        # gore_drip is no longer tied to click effects; it is driven by
        # the background drip system (set_bg_drip). Any active gore drip
        # should remain untouched here.
        # Manage fish flock (mermaid theme)
        if effect_key == "mermaid" and self._enabled:
            if self._fish_flock is None:
                self._fish_flock = _FishFlock(self)
            self._fish_flock.start()
        else:
            if self._fish_flock:
                self._fish_flock.stop()
        # Manage alien tractor beam
        if effect_key == "alien" and self._enabled:
            if self._alien_beam is None:
                self._alien_beam = _AlienBeam(self)
            self._alien_beam.start()
        else:
            if self._alien_beam:
                self._alien_beam.stop()
        # Manage slime drip (slime theme click effect)
        if effect_key == "slime" and self._enabled:
            if self._slime_drip is None:
                self._slime_drip = _SlimeDrip(self)
            self._slime_drip.start()
        else:
            if self._slime_drip:
                self._slime_drip.stop()
        # water_drip is no longer tied to click effects; it is driven by
        # the background drip system (set_bg_drip). Any active water drip
        # should remain untouched here.
        # Manage snow drift (ice theme)
        if (effect_key == "ice" and self._enabled) or (self._bg_ambient_enabled and self._bg_ambient_type == "snow"):
            if self._snow_drift is None:
                self._snow_drift = _SnowDrift(self)
            self._snow_drift.start()
        else:
            if self._snow_drift:
                self._snow_drift.stop()
        # Manage ember drift (fire theme)
        if (effect_key == "fire" and self._enabled) or (self._bg_ambient_enabled and self._bg_ambient_type == "ember"):
            if self._ember_drift is None:
                self._ember_drift = _EmberDrift(self)
            self._ember_drift.start()
        else:
            if self._ember_drift:
                self._ember_drift.stop()
        # Manage sakura petals (sakura theme)
        if (effect_key == "sakura" and self._enabled) or (self._bg_ambient_enabled and self._bg_ambient_type == "sakura"):
            if self._sakura_petal is None:
                self._sakura_petal = _SakuraPetal(self)
            self._sakura_petal.start()
        else:
            if self._sakura_petal:
                self._sakura_petal.stop()
        # Manage shooting stars (galaxy / galaxy_otter themes)
        if (effect_key in ("galaxy", "galaxy_otter") and self._enabled) or (self._bg_ambient_enabled and self._bg_ambient_type == "stars"):
            if self._star_shoot is None:
                self._star_shoot = _StarShoot(self)
            self._star_shoot.start()
        else:
            if self._star_shoot:
                self._star_shoot.stop()
        # Manage bubble rise (ocean / ripple themes)
        if (effect_key in ("ocean", "ripple") and self._enabled) or (self._bg_ambient_enabled and self._bg_ambient_type == "bubbles"):
            if self._bubble_rise is None:
                self._bubble_rise = _BubbleRise(self)
            self._bubble_rise.start()
        else:
            if self._bubble_rise:
                self._bubble_rise.stop()
        # Manage neon flicker (neon theme)
        if (effect_key == "neon" and self._enabled) or (self._bg_ambient_enabled and self._bg_ambient_type == "neon"):
            if self._neon_flicker is None:
                self._neon_flicker = _NeonFlicker(self)
            self._neon_flicker.start()
        else:
            if self._neon_flicker:
                self._neon_flicker.stop()
        # Manage ghost wisps (goth / ghost themes)
        if (effect_key in ("goth", "ghost") and self._enabled) or (self._bg_ambient_enabled and self._bg_ambient_type == "ghost"):
            if self._ghost_wisp is None:
                self._ghost_wisp = _GhostWisp(self)
            self._ghost_wisp.start()
        else:
            if self._ghost_wisp:
                self._ghost_wisp.stop()
        # Manage rainbow confetti (rainbow theme)
        if effect_key == "rainbow" and self._enabled:
            if self._rainbow_confetti is None:
                self._rainbow_confetti = _RainbowConfetti(self)
            self._rainbow_confetti.start()
        else:
            if self._rainbow_confetti:
                self._rainbow_confetti.stop()
        # Manage star dust (sparkle theme)
        if effect_key == "sparkle" and self._enabled:
            if self._star_dust is None:
                self._star_dust = _StarDust(self)
            self._star_dust.start()
        else:
            if self._star_dust:
                self._star_dust.stop()
        # Manage bamboo leaf drift (panda theme)
        if effect_key == "panda" and self._enabled:
            if self._bamboo_leaf is None:
                self._bamboo_leaf = _BambooLeaf(self)
            self._bamboo_leaf.start()
        else:
            if self._bamboo_leaf:
                self._bamboo_leaf.stop()
        # Manage otter bubble clusters (otter theme)
        if effect_key == "otter" and self._enabled:
            if self._otter_bubble is None:
                self._otter_bubble = _OtterBubble(self)
            self._otter_bubble.start()
        else:
            if self._otter_bubble:
                self._otter_bubble.stop()
        # Manage shark fin glide (shark theme)
        if effect_key == "shark" and self._enabled:
            if self._shark_fin is None:
                self._shark_fin = _SharkFin(self)
            self._shark_fin.start()
        else:
            if self._shark_fin:
                self._shark_fin.stop()
        # Manage slither wiggler (slither theme)
        if effect_key == "slither" and self._enabled:
            if self._slither_wiggler is None:
                self._slither_wiggler = _SlitherWiggler(self)
            self._slither_wiggler.start()
        else:
            if self._slither_wiggler:
                self._slither_wiggler.stop()
        # Manage noodle strand drift (noodle theme)
        if effect_key == "noodle" and self._enabled:
            if self._noodle_strand is None:
                self._noodle_strand = _NoodleStrand(self)
            self._noodle_strand.start()
        else:
            if self._noodle_strand:
                self._noodle_strand.stop()

    def set_bg_drip(self, drip_type: str, enabled: bool) -> None:
        """Enable or disable the background drip effect independently of click effects.

        *drip_type* is ``"blood"`` (crimson teardrops via :class:`_GoreDrip`) or
        ``"water"`` (translucent cyan teardrops via :class:`_WaterDrip``).
        The drip overlay is independent of the click-effects enabled state so
        blood/water drips can run even when click effects are off.
        """
        self._bg_drip_enabled = enabled
        self._bg_drip_type = drip_type if drip_type in ("blood", "water") else "blood"

        if enabled:
            # Make overlay visible and timer running
            self.show()
            if not self._timer.isActive():
                self._timer.start()
            if self._bg_drip_type == "blood":
                # Stop water drip if it was running
                if self._water_drip:
                    self._water_drip.stop()
                if self._gore_drip is None:
                    self._gore_drip = _GoreDrip(self)
                self._gore_drip.start()
            else:
                # Stop blood drip if it was running
                if self._gore_drip:
                    self._gore_drip.stop()
                if self._water_drip is None:
                    self._water_drip = _WaterDrip(self)
                self._water_drip.start()
        else:
            # Stop both drip types
            if self._gore_drip:
                self._gore_drip.stop()
            if self._water_drip:
                self._water_drip.stop()
            # Hide if nothing else needs the overlay
            if not self._enabled and not self._banner_flock_active and not self._bg_ambient_enabled and not self._bg_flock_enabled:
                self._timer.stop()
                self.hide()

    def set_bg_flock(self, enabled: bool, emoji: str = "🐼", color: str = "#e94560") -> None:
        """Enable or disable a background flock independently of the banner animation."""
        self._bg_flock_enabled = enabled
        # Delegate to banner_flock mechanism (reuses same overlay infrastructure)
        self.set_banner_flock(enabled, emoji, color)

    def set_bg_ambient(self, ambient_type: str, enabled: bool) -> None:
        """Enable or disable a manual ambient background effect.

        *ambient_type* is one of: ``"snow"``, ``"ember"``, ``"sakura"``,
        ``"stars"``, ``"bubbles"``, ``"neon"``, ``"ghost"``, ``"none"``.
        The ambient effect is independent of the click-effects enabled state.
        """
        # Stop old ambient if type changed or disabling
        if not enabled or ambient_type != self._bg_ambient_type:
            self._stop_bg_ambient()
        self._bg_ambient_enabled = enabled
        self._bg_ambient_type = ambient_type if enabled else "none"

        if not enabled or ambient_type == "none":
            # If nothing else needs overlay, stop timer and hide
            if not self._enabled and not self._banner_flock_active and not self._bg_drip_enabled:
                self._timer.stop()
                self.hide()
            return

        # Ensure overlay and timer are running
        self.show()
        if not self._timer.isActive():
            self._timer.start()

        # Start the appropriate ambient effect instance
        _AM = ambient_type
        if _AM == "snow":
            if self._snow_drift is None:
                self._snow_drift = _SnowDrift(self)
            self._snow_drift.start()
        elif _AM == "ember":
            if self._ember_drift is None:
                self._ember_drift = _EmberDrift(self)
            self._ember_drift.start()
        elif _AM == "sakura":
            if self._sakura_petal is None:
                self._sakura_petal = _SakuraPetal(self)
            self._sakura_petal.start()
        elif _AM == "stars":
            if self._star_shoot is None:
                self._star_shoot = _StarShoot(self)
            self._star_shoot.start()
        elif _AM == "bubbles":
            if self._bubble_rise is None:
                self._bubble_rise = _BubbleRise(self)
            self._bubble_rise.start()
        elif _AM == "neon":
            if self._neon_flicker is None:
                self._neon_flicker = _NeonFlicker(self)
            self._neon_flicker.start()
        elif _AM == "ghost":
            if self._ghost_wisp is None:
                self._ghost_wisp = _GhostWisp(self)
            self._ghost_wisp.start()

    def _stop_bg_ambient(self) -> None:
        """Stop whichever ambient is currently running as the manual bg ambient."""
        _AM = self._bg_ambient_type
        if _AM == "snow" and self._snow_drift:
            self._snow_drift.stop()
        elif _AM == "ember" and self._ember_drift:
            self._ember_drift.stop()
        elif _AM == "sakura" and self._sakura_petal:
            self._sakura_petal.stop()
        elif _AM == "stars" and self._star_shoot:
            self._star_shoot.stop()
        elif _AM == "bubbles" and self._bubble_rise:
            self._bubble_rise.stop()
        elif _AM == "neon" and self._neon_flicker:
            self._neon_flicker.stop()
        elif _AM == "ghost" and self._ghost_wisp:
            self._ghost_wisp.stop()

    def set_custom_emoji(self, emoji_list: list[str]) -> None:
        """Update the emoji list used by the 'custom' effect spawner."""
        set_custom_emoji(emoji_list)
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
    # Particle kinds that behave as ambient (no gravity, off-screen culling).
    _AMBIENT_KINDS = frozenset((
        "bat_fly", "fairy_fly", "snow", "ember", "bubble",
        "confetti", "star_dust", "bamboo_leaf", "otter_bubble",
        "shark_fin", "wriggle", "noodle_strand",
    ))
    # Margin in pixels around each particle's bounding box added to the dirty
    # rect to ensure antialiased edges are fully covered.
    _DIRTY_MARGIN = 6
    # Maximum number of text/emoji particles rendered per frame.
    # Emoji font shaping is expensive; beyond this limit additional text
    # particles are skipped for the frame (they are still tracked and will
    # render in the next frame once earlier ones have faded).
    _MAX_TEXT_PER_FRAME = 8

    # Wing-flap animation constants for bat_fly / fairy_fly particles.
    # Each tick decrements life by 0.05, so the elapsed frame counter is
    # (max_life - life) / 0.05.  Multiplying by π * _FLAP_FREQ_MULT gives
    # the phase argument to sin(); at 20 fps this produces approximately
    # _FLAP_FREQ_MULT * 10 flap cycles per second.  0.55 ≈ 0.9 Hz — one
    # visible wing stroke every ~1.1 s, natural-looking without being jittery.
    _FLAP_FREQ_MULT  = 0.55   # radians-per-frame phase multiplier (~0.9 Hz @ 20 fps)
    _FLAP_AMPLITUDE  = 0.28   # ±28 % size oscillation (wings spread vs. folded)

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
            if p.kind not in self._AMBIENT_KINDS:
                p.vy += self._GRAVITY
            p.life -= 0.05   # slightly faster decay → shorter burst, fewer frames rendered
            # Wing-flap animation: oscillate particle size for flying bat/fairy
            # particles to simulate wing movement.  Uses a sine wave at ~3 Hz
            # (0.6 π rad per 20fps frame = 6 rad/s ≈ ~1 Hz, noticeable flutter)
            # so the apparent "wingspan" pulses visibly without requiring extra
            # emoji assets.
            if p.kind in ("bat_fly", "fairy_fly"):
                elapsed = (p.max_life - p.life) / 0.05  # frame counter
                flap = math.sin(elapsed * math.pi * self._FLAP_FREQ_MULT)
                p.size = p.base_size * (1.0 + self._FLAP_AMPLITUDE * flap)
            # Cull ambient (bat/fairy) particles that have completely left the
            # window so they never accumulate off-screen indefinitely.
            if p.kind in self._AMBIENT_KINDS:
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

    # Pre-rendered emoji pixmap cache keyed by (emoji_str, size_int).
    # Bat/fairy/banner particles are the same emoji at the same size every
    # frame; rasterising them once to a QPixmap and blitting it is far
    # cheaper than calling drawText (which triggers full Unicode/emoji shaping
    # on every frame).  The cache is class-level so it is shared across the
    # whole process lifetime — there is typically only one overlay instance.
    #
    # Theme changes do NOT require cache invalidation: the cache always uses
    # the emoji font stack (_EMOJI_FONT_FAMILIES) regardless of the active
    # app theme, and coloured emoji glyphs are rendered by the OS emoji font
    # with their own built-in colours that are unaffected by Qt pen or theme
    # settings.  Only font *size* matters, which is already part of the key.
    _EMOJI_PIXMAP_CACHE: dict = {}
    _EMOJI_PIXMAP_CACHE_MAX = 128

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

    def _get_emoji_pixmap(self, emoji: str, size: int) -> QPixmap:
        """Return a pre-rendered QPixmap for *emoji* at *size* points.

        Emoji shaping via drawText is expensive (it runs the full Unicode
        shaping pipeline every call).  Rasterising each unique (emoji, size)
        combination once and caching the result as a QPixmap cuts per-frame
        cost to a simple blit — a 10–20× speedup for ambient bat/fairy/banner
        flocks that render the same glyph every tick.
        """
        key = (emoji, size)
        cached = self._EMOJI_PIXMAP_CACHE.get(key)
        if cached is not None:
            return cached
        cache = self._EMOJI_PIXMAP_CACHE
        if len(cache) >= self._EMOJI_PIXMAP_CACHE_MAX:
            # Evict the oldest (insertion-order) entry to keep the cache bounded.
            cache.pop(next(iter(cache)))
        # Add a small margin so descenders / ascenders are not clipped.
        px_size = max(12, size + 10)
        pix = QPixmap(px_size, px_size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._get_font(size))
        # White pen — coloured emoji ignore pen colour; single-colour glyphs
        # will be tinted during drawing via painter.setOpacity().
        p.setPen(QColor(255, 255, 255, 255))
        p.drawText(QRect(0, 0, px_size, px_size),
                   Qt.AlignmentFlag.AlignCenter, emoji)
        p.end()
        cache[key] = pix
        return pix

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
            if p.kind in ("bat_fly", "fairy_fly"):
                # Ambient flying particles are always the same emoji/size —
                # use the pixmap cache to avoid re-shaping the glyph every frame.
                if text_drawn >= self._MAX_TEXT_PER_FRAME:
                    continue
                text_drawn += 1
                pix = self._get_emoji_pixmap(p.text, int(p.size))
                painter.setOpacity(alpha / 255.0)
                painter.drawPixmap(
                    int(p.x) - pix.width() // 2,
                    int(p.y) - pix.height() // 2,
                    pix,
                )
                painter.setOpacity(1.0)
            elif p.kind == "text":
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
            elif p.kind == "drip_streak":
                # Realistic drip: rounded drop head with a tapered streak tail
                # trailing upward — the streak lengthens as the drop falls further.
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                body_w = max(2, int(p.size * 0.55))
                body_h = max(2, int(p.size * 0.85))
                # Draw the rounded drop body
                painter.drawEllipse(
                    int(p.x) - body_w // 2,
                    int(p.y) - body_h // 2,
                    body_w, body_h,
                )
                # Trailing streak: grows longer as the drop falls (life decreases)
                streak_len = max(2, int(p.size * (1.2 + (1.0 - p.alpha_frac) * 4.0)))
                streak_w = max(1, body_w // 2)
                streak_c = QColor(p.color)
                streak_c.setAlpha(max(0, int(alpha * 0.55)))
                painter.setBrush(QBrush(streak_c))
                # Tapered rect above the drop center
                painter.drawRect(
                    int(p.x) - streak_w // 2,
                    int(p.y) - body_h // 2 - streak_len,
                    streak_w, streak_len,
                )
                # Tiny tapered tip at the very top of the streak (half-width)
                tip_w = max(1, streak_w // 2)
                tip_h = max(1, int(streak_len * 0.25))
                tip_c = QColor(p.color)
                tip_c.setAlpha(max(0, int(alpha * 0.25)))
                painter.setBrush(QBrush(tip_c))
                painter.drawRect(
                    int(p.x) - tip_w // 2,
                    int(p.y) - body_h // 2 - streak_len - tip_h,
                    tip_w, tip_h,
                )
            elif p.kind == "blood_drip":
                # Smooth goopy teardrop for blood.  The rounded head is at the
                # bottom (where the drop has gathered mass) and a tapered tail
                # trails upward behind it.  A QLinearGradient gives the blob a
                # deep crimson core that lightens slightly at the tip.
                head_r = max(2, int(p.size * 0.55))
                tail_len = max(4, int(p.size * (1.6 + (1.0 - p.alpha_frac) * 3.5)))
                cx = int(p.x)
                head_y = int(p.y)
                tail_y = head_y - head_r - tail_len

                grad = QLinearGradient(cx, tail_y, cx, head_y + head_r)
                tip_c = QColor(p.color)
                tip_c.setAlpha(max(0, int(alpha * 0.18)))
                mid_c = QColor(p.color)
                mid_c.setAlpha(max(0, int(alpha * 0.65)))
                head_c = QColor(p.color)
                head_c.setAlpha(alpha)
                grad.setColorAt(0.0, tip_c)
                grad.setColorAt(0.45, mid_c)
                grad.setColorAt(1.0, head_c)

                path = QPainterPath()
                # Teardrop: start at the tail tip, curve down to the rounded head
                path.moveTo(cx, tail_y)
                path.quadTo(cx + head_r * 0.9, head_y - head_r, cx + head_r, head_y)
                # bottom arc of the round head
                path.arcTo(cx - head_r, head_y - head_r, head_r * 2, head_r * 2, 0, -180)
                path.quadTo(cx - head_r * 0.9, head_y - head_r, cx, tail_y)
                path.closeSubpath()

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
                painter.drawPath(path)
                painter.setBrush(Qt.BrushStyle.NoBrush)

            elif p.kind == "water_drip":
                # Slim fluid teardrop for water.  Much thinner than blood and
                # more translucent; the gradient fades to near-invisible at the
                # tail tip to suggest a thin trickle.
                head_r = max(1, int(p.size * 0.38))
                tail_len = max(3, int(p.size * (1.2 + (1.0 - p.alpha_frac) * 2.5)))
                cx = int(p.x)
                head_y = int(p.y)
                tail_y = head_y - head_r - tail_len

                grad = QLinearGradient(cx, tail_y, cx, head_y + head_r)
                tip_c = QColor(p.color)
                tip_c.setAlpha(0)
                mid_c = QColor(p.color)
                mid_c.setAlpha(max(0, int(alpha * 0.45)))
                head_c = QColor(p.color)
                head_c.setAlpha(max(0, int(alpha * 0.80)))
                grad.setColorAt(0.0, tip_c)
                grad.setColorAt(0.5, mid_c)
                grad.setColorAt(1.0, head_c)

                path = QPainterPath()
                path.moveTo(cx, tail_y)
                path.quadTo(cx + head_r * 0.85, head_y - head_r, cx + head_r, head_y)
                path.arcTo(cx - head_r, head_y - head_r, head_r * 2, head_r * 2, 0, -180)
                path.quadTo(cx - head_r * 0.85, head_y - head_r, cx, tail_y)
                path.closeSubpath()

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
                painter.drawPath(path)
                painter.setBrush(Qt.BrushStyle.NoBrush)

            elif p.kind == "snow":
                # Soft white snowflake: two overlapping ellipses for a cross shape,
                # with a faint outer glow circle.
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                r = max(2, int(p.size * 0.5))
                # Horizontal bar
                painter.drawEllipse(int(p.x) - r, int(p.y) - max(1, r // 2),
                                    r * 2, max(1, r))
                # Vertical bar
                painter.drawEllipse(int(p.x) - max(1, r // 2), int(p.y) - r,
                                    max(1, r), r * 2)
                # Bright centre dot
                bright = QColor(255, 255, 255, min(255, alpha + 40))
                painter.setBrush(QBrush(bright))
                cr = max(1, r // 2)
                painter.drawEllipse(int(p.x) - cr, int(p.y) - cr, cr * 2, cr * 2)
            elif p.kind == "ember":
                # Glowing ember: bright hot centre that cools to a dim red edge.
                cx, cy = int(p.x), int(p.y)
                r = max(2, int(p.size))
                grad = QLinearGradient(cx - r, cy - r, cx + r, cy + r)
                hot_c = QColor(p.color)
                hot_c.setAlpha(alpha)
                cool_c = QColor(p.color)
                cool_c.setAlpha(max(0, int(alpha * 0.25)))
                bright_c = QColor(255, 220, 100, min(255, int(alpha * 1.3)))
                grad.setColorAt(0.0, bright_c)
                grad.setColorAt(0.45, hot_c)
                grad.setColorAt(1.0, cool_c)
                painter.setBrush(QBrush(grad))
                painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            elif p.kind == "bubble":
                # Translucent ring with a bright highlight dot (top-left).
                cx, cy = int(p.x), int(p.y)
                r = max(4, int(p.size))
                ring_c = QColor(p.color)
                ring_c.setAlpha(max(0, int(alpha * 0.55)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                pen = QPen(ring_c)
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
                painter.setPen(Qt.PenStyle.NoPen)
                # Bright highlight in upper-left quadrant
                highlight_c = QColor(255, 255, 255, max(0, int(alpha * 0.70)))
                painter.setBrush(QBrush(highlight_c))
                hr = max(1, r // 3)
                painter.drawEllipse(cx - r + hr // 2, cy - r + hr // 2, hr, hr)

            elif p.kind == "ring":
                # Expanding hollow circle — grows as life fades, simulating a
                # water ripple ring radiating outward from the click point.
                c = QColor(p.color)
                c.setAlpha(alpha)
                pen_w = max(1, int(3.0 * p.alpha_frac))
                painter.setPen(QPen(c, pen_w))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                progress = 1.0 - p.alpha_frac          # 0 at birth → 1 at death
                r = max(2, int(p.size * (1.0 + progress * 5.0)))  # expands up to 6×
                painter.drawEllipse(int(p.x) - r, int(p.y) - r, r * 2, r * 2)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(Qt.BrushStyle.NoBrush)

            elif p.kind == "confetti":
                # Colourful spinning rectangle — rotates continuously over its lifetime.
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                elapsed_frac = 1.0 - p.alpha_frac
                try:
                    spin_turns = float(p.text)
                except (ValueError, AttributeError):
                    spin_turns = 2.0
                angle_deg = spin_turns * elapsed_frac * 360.0
                w_r = max(3, int(p.size * 0.9))
                h_r = max(2, int(p.size * 0.45))
                painter.save()
                painter.translate(int(p.x), int(p.y))
                painter.rotate(angle_deg)
                painter.drawRect(-w_r, -h_r, w_r * 2, h_r * 2)
                painter.restore()

            elif p.kind == "star_dust":
                # Tiny 4-pointed star that pulses opacity (twinkle effect).
                c = QColor(p.color)
                # Twinkle: modulate alpha with a sine wave over lifetime
                twinkle = abs(math.sin(p.alpha_frac * math.pi * 4))
                c.setAlpha(max(0, int(alpha * twinkle)))
                painter.setBrush(QBrush(c))
                r_out = max(2, int(p.size))
                r_in = max(1, int(p.size * 0.35))
                star = QPainterPath()
                for arm in range(4):
                    outer_a = math.radians(arm * 90.0)
                    inner_a = math.radians(arm * 90.0 + 45.0)
                    ox = int(p.x) + r_out * math.cos(outer_a)
                    oy = int(p.y) + r_out * math.sin(outer_a)
                    ix2 = int(p.x) + r_in * math.cos(inner_a)
                    iy2 = int(p.y) + r_in * math.sin(inner_a)
                    if arm == 0:
                        star.moveTo(ox, oy)
                    else:
                        star.lineTo(ox, oy)
                    star.lineTo(ix2, iy2)
                star.closeSubpath()
                painter.drawPath(star)

            elif p.kind == "bamboo_leaf":
                # Elongated ellipse leaf that rotates as it falls.
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                elapsed_frac = 1.0 - p.alpha_frac
                try:
                    spin_turns = float(p.text)
                except (ValueError, AttributeError):
                    spin_turns = 0.8
                angle_deg = spin_turns * elapsed_frac * 360.0
                w_r = max(2, int(p.size * 0.3))
                h_r = max(4, int(p.size))
                painter.save()
                painter.translate(int(p.x), int(p.y))
                painter.rotate(angle_deg)
                painter.drawEllipse(-w_r, -h_r, w_r * 2, h_r * 2)
                painter.restore()

            elif p.kind == "otter_bubble":
                # Small translucent ring with warm highlight — cuter than ocean bubbles.
                cx, cy = int(p.x), int(p.y)
                r = max(3, int(p.size))
                ring_c = QColor(p.color)
                ring_c.setAlpha(max(0, int(alpha * 0.65)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                pen = QPen(ring_c)
                pen.setWidth(max(1, r // 3))
                painter.setPen(pen)
                painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
                painter.setPen(Qt.PenStyle.NoPen)
                # Warm golden highlight (otter-themed)
                hl_c = QColor(255, 230, 180, max(0, int(alpha * 0.80)))
                painter.setBrush(QBrush(hl_c))
                hr = max(1, r // 3)
                painter.drawEllipse(cx - r + hr // 2, cy - r + hr // 2, hr, hr)

            elif p.kind == "shark_fin":
                # Triangular shark-fin silhouette gliding horizontally.
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                fin_w = max(6, int(p.size * 0.9))
                fin_h = max(8, int(p.size))
                cx, cy = int(p.x), int(p.y)
                # Direction determines which side the fin curves toward
                direction = 1 if p.vx >= 0 else -1
                fin_path = QPainterPath()
                # Base of fin (waterline)
                fin_path.moveTo(cx - fin_w * direction, cy + fin_h // 3)
                fin_path.lineTo(cx + fin_w * direction, cy + fin_h // 3)
                # Tip of fin (sharp point)
                fin_path.lineTo(cx - fin_w // 3 * direction, cy - fin_h)
                fin_path.closeSubpath()
                painter.drawPath(fin_path)

            elif p.kind == "wriggle":
                # Small oval for snake-body segments — slightly elongated horizontally.
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                rw = max(2, int(p.size))
                rh = max(1, int(p.size * 0.65))
                painter.drawEllipse(int(p.x) - rw, int(p.y) - rh, rw * 2, rh * 2)

            elif p.kind == "noodle_strand":
                # Smooth small disc — building block of flowing noodle strands.
                c = QColor(p.color)
                c.setAlpha(alpha)
                painter.setBrush(QBrush(c))
                r = max(2, int(p.size * 0.7))
                painter.drawEllipse(int(p.x) - r, int(p.y) - r, r * 2, r * 2)

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
    # Maximum bite level (progressive damage stages)
    _MAX_BITE = 6
    # Milliseconds of inactivity before bite level decays one step
    _BITE_DECAY_MS = 5000

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
        # Bite-mark tracking: maps button id() → (level, decay_timer)
        self._bite_levels: dict[int, int] = {}
        self._bite_timers: dict[int, QTimer] = {}
        self._bite_widgets: dict[int, "QWidget | None"] = {}  # weak refs via id

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
        elif mode == "abduct":
            self._do_abduct(btn)
        elif mode == "bite":
            self._do_bite(btn)

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

    def _do_abduct(self, btn: QWidget) -> None:
        """UFO tractor-beam abduction: button drifts upward as if lifted by a
        beam, then snaps back down to its original position.

        The animation has three phases:
          1. Rise – button slides 22 px upward over 280 ms with OutQuad easing.
          2. Hover – button holds the high position for 120 ms (zero-duration noop
             simulated with a tiny pause anim).
          3. Drop – button falls back with InBack easing (slight overshoot).
        """
        from PyQt6.QtCore import (
            QPropertyAnimation, QSequentialAnimationGroup,
            QEasingCurve, QRect, QPauseAnimation,
        )
        orig = QRect(btn.geometry())
        high = QRect(orig.translated(0, -22))

        a_rise = QPropertyAnimation(btn, b"geometry", self)
        a_rise.setDuration(280)
        a_rise.setStartValue(orig)
        a_rise.setEndValue(high)
        a_rise.setEasingCurve(QEasingCurve.Type.OutQuad)

        a_hover = QPauseAnimation(120, self)

        a_drop = QPropertyAnimation(btn, b"geometry", self)
        a_drop.setDuration(200)
        a_drop.setStartValue(high)
        a_drop.setEndValue(orig)
        a_drop.setEasingCurve(QEasingCurve.Type.InBack)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(a_rise)
        group.addAnimation(a_hover)
        group.addAnimation(a_drop)
        self._start(group)
        # Spawn click particles from the button centre
        self._do_shatter(btn)

    # ------------------------------------------------------------------

    def _do_bite(self, btn: QWidget) -> None:
        """Progressive shark-bite animation.

        Each click increments the button's ``bite level`` (capped at
        ``_MAX_BITE``).  The level determines how many particles are spawned
        and how intense they appear (more blood/sharks at higher levels).
        A per-button decay timer resets the level step-by-step after
        ``_BITE_DECAY_MS`` ms of inactivity so the button eventually
        'heals' once the user stops clicking.
        """
        bid = id(btn)
        # Register / increment bite level
        level = min(self._bite_levels.get(bid, 0) + 1, self._MAX_BITE)
        self._bite_levels[bid] = level
        self._bite_widgets[bid] = btn

        # Reset / start the decay timer for this button
        if bid in self._bite_timers:
            self._bite_timers[bid].stop()
            self._bite_timers[bid].deleteLater()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(self._BITE_DECAY_MS)
        # Capture bid by value for the closure
        timer.timeout.connect(lambda b=bid: self._decay_bite(b))
        self._bite_timers[bid] = timer
        timer.start()

        # Spawn particles proportional to bite level
        if self._click_effects is not None:
            centre_local = btn.rect().center()
            centre_global = btn.mapToGlobal(centre_local)
            centre_mw = self._main_window.mapFromGlobal(centre_global)
            x, y = centre_mw.x(), centre_mw.y()

            # More particles and blood at higher levels
            shark_emojis = ["🦈", "🩸", "💥", "🐟", "🐠"]
            shark_colors = ["#1177aa", "#0055cc", "#cc1133", "#aa3355", "#ff4466"]
            count = max(2, level + 1)
            for _ in range(count):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(2.0 + level * 0.5, 5.0 + level * 1.0)
                vx = math.cos(angle) * speed
                vy = math.sin(angle) * speed
                # At higher levels, prefer blood/damage emojis
                if level >= 4:
                    emoji_pool = ["🩸", "💥", "🦈"]
                elif level >= 2:
                    emoji_pool = ["🦈", "🩸", "🐟"]
                else:
                    emoji_pool = shark_emojis
                kind = "text" if random.random() < 0.65 else "circle"
                color = QColor(random.choice(shark_colors))
                text = random.choice(emoji_pool) if kind == "text" else ""
                size = random.uniform(10 + level, 18 + level * 2) if kind == "text" \
                    else random.uniform(3, 6 + level)
                p = _Particle(x, y, vx, vy,
                              random.uniform(0.5, 0.9 + level * 0.1),
                              kind, size, color, text)
                self._click_effects._particles.append(p)

            if not self._click_effects._timer.isActive():
                self._click_effects._timer.start()
            if not self._click_effects.isVisible():
                self._click_effects.show()

        # Also do a small press animation so the button responds physically
        self._do_slide(btn, dy=3, duration=120)

    def _decay_bite(self, bid: int) -> None:
        """Reduce the bite level for button *bid* by 1 (or remove if zero)."""
        level = self._bite_levels.get(bid, 0)
        if level <= 1:
            self._bite_levels.pop(bid, None)
            self._bite_timers.pop(bid, None)
            self._bite_widgets.pop(bid, None)
        else:
            self._bite_levels[bid] = level - 1
            # Schedule next decay step
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self._BITE_DECAY_MS)
            timer.timeout.connect(lambda b=bid: self._decay_bite(b))
            old = self._bite_timers.pop(bid, None)
            if old is not None:
                old.deleteLater()
            self._bite_timers[bid] = timer
            timer.start()

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
