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

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
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
    """Fairy-dust sparkles for Fairy Garden theme."""
    particles = []
    fairy_emojis = ["✨", "⭐", "🌟", "💫", "🪄", "🧚"]
    fairy_colors = ["#dd44ff", "#ff88ff", "#ffccee", "#cc88ff", "#ffffff", "#aa44ff"]
    for _ in range(6):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1.5, 8)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        kind = "text" if random.random() < 0.65 else "circle"
        color = QColor(random.choice(fairy_colors))
        text = random.choice(fairy_emojis) if kind == "text" else ""
        size = random.uniform(10, 20) if kind == "text" else random.uniform(3, 8)
        particles.append(_Particle(x, y, vx, vy, random.uniform(0.6, 1.4),
                                   kind, size, color, text))
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
    for _ in range(10):
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
        count = random.randint(3, 7)
        for i in range(count):
            y_start = random.randint(10, 60)
            x_start = random.randint(-20, 20)
            speed = random.uniform(3, 7)
            life = (w + 60) / max(speed, 1) / 60 + random.uniform(0.5, 1.5)
            bat = _Particle(x_start + i * 25, y_start,
                            speed, random.uniform(-0.5, 0.5), life,
                            "bat_fly", random.uniform(18, 28),
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
        count = random.randint(2, 5)
        left_to_right = random.random() < 0.5
        speed_sign = 1 if left_to_right else -1
        fairy_emojis = ["🧚", "✨", "🪄", "⭐", "💜", "🌟"]
        fairy_colors = ["#dd44ff", "#ff88ff", "#ffccee", "#cc88ff", "#ffffff", "#ffaaff"]
        for i in range(count):
            y_start = random.randint(40, max(41, h - 100)) + i * random.randint(-10, 20)
            x_start = (random.randint(-30, -10) if left_to_right
                       else w + random.randint(10, 30))
            speed = random.uniform(1.5, 4.5) * speed_sign
            vy = random.uniform(-0.4, 0.4)
            life = (w + 80) / max(abs(speed), 1) / 60 + random.uniform(0.5, 2.0)
            fairy = _Particle(
                x_start + i * random.randint(20, 50), y_start,
                speed, vy, life,
                "fairy_fly", random.uniform(18, 28),
                QColor(random.choice(fairy_colors)),
                random.choice(fairy_emojis),
            )
            self._overlay._add_particle(fairy)


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

    click_registered = pyqtSignal(int)  # emitted with total click count on each click

    def __init__(self, main_window: QWidget):
        super().__init__(main_window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # WA_TranslucentBackground gives the widget a real alpha channel in its
        # backing surface so that CompositionMode_Clear produces transparent
        # pixels rather than black ones.  Without this attribute the overlay
        # renders as a solid black rectangle covering the whole window.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # WA_NoSystemBackground prevents Qt from pre-filling this widget's
        # region with the background colour before paintEvent.  Without it
        # the overlay would erase every child widget drawn beneath it.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

        self._main_window = main_window
        self._effect_key = "default"
        self._particles: list[_Particle] = []
        self._enabled = False
        self._click_count = 0
        self._bat_flock: _BatFlock | None = None
        self._fairy_flock: _FairyFlock | None = None
        self._font = QFont(_EMOJI_FONT_FAMILIES, 14)
        # Cache QFont objects per integer point-size to avoid repeated
        # mutations and implicit font-metric recalculations each frame.
        self._font_cache: dict[int, QFont] = {}
        # Bounding rect from the previous frame so we can union it with the
        # current frame and only request a repaint of the dirty region.
        self._prev_dirty = None

        self._timer = QTimer(self)
        self._timer.setInterval(33)   # 30 fps — smooth enough, much less CPU than 60fps
        self._timer.timeout.connect(self._tick)

        self.setGeometry(main_window.rect())
        self.raise_()
        # Start hidden: the overlay is only made visible when effects are
        # actually enabled.  An invisible overlay cannot trigger the
        # CompositionMode_Clear paintEvent that would otherwise black out the
        # window on platforms where child widgets have no real alpha channel.
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
        if enabled:
            QApplication.instance().installEventFilter(self)
            self._timer.start()
            self.raise_()
            self.show()
        else:
            QApplication.instance().removeEventFilter(self)
            self._timer.stop()
            self._particles.clear()
            if self._bat_flock:
                self._bat_flock.stop()
            if self._fairy_flock:
                self._fairy_flock.stop()
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
        # Hard cap to prevent unbounded growth during rapid clicking
        if len(self._particles) > 150:
            self._particles = self._particles[-80:]

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

    def _particle_rect(self, p: _Particle):
        """Return the approximate bounding QRect for a single particle."""
        from PyQt6.QtCore import QRect
        r = max(6, int(p.size + self._DIRTY_MARGIN))
        return QRect(int(p.x) - r, int(p.y) - r, r * 2, r * 2)

    def _tick(self) -> None:
        if not self._particles:
            return

        # Compute the dirty rect covering all current particle positions
        # BEFORE advancing them — ensures old positions are repainted (cleared).
        from PyQt6.QtCore import QRect
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
            p.life -= 0.03   # faster decay → shorter burst, fewer frames rendered
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

    def _get_font(self, size: int) -> QFont:
        """Return a cached QFont for *size* points (avoids per-particle mutation)."""
        size = max(6, size)
        if size not in self._font_cache:
            f = QFont(_EMOJI_FONT_FAMILIES, size)
            self._font_cache[size] = f
        return self._font_cache[size]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Always erase the update region first.  WA_NoSystemBackground means Qt
        # never pre-clears this overlay's surface, so without an explicit clear
        # particle pixels from previous frames "stick" on screen after the
        # particles have died.  CompositionMode_Clear sets every pixel in the
        # rect to fully transparent, which erases stale paint while leaving the
        # widgets underneath perfectly visible.
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Clear
        )
        painter.fillRect(event.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )

        if not self._particles:
            painter.end()
            return

        painter.setPen(Qt.PenStyle.NoPen)

        for p in self._particles:
            alpha = max(0, min(255, int(p.alpha_frac * 220)))
            if p.kind in ("text", "bat_fly", "fairy_fly"):
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
