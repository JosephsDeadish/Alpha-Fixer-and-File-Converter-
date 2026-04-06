"""
MouseTrailOverlay – a transparent child widget that paints a fading particle
trail following the mouse cursor over the main application window.

Works on any platform that supports Qt child widgets with transparent
backgrounds (i.e., all modern Qt6 deployments).

The overlay supports fourteen trail styles:
  • "dots"        – the default: fading coloured dots (original behaviour).
  • "fairy"       – fairy-dust sparkle emoji (✨💫⭐) that float and fade gently.
  • "wave"        – ocean-themed bubbles and sea emoji (🫧💧🌊) for aquatic themes.
  • "sparkle"     – icy crystal sparkle emoji (✦❄✧💎) for arctic/ice themes.
  • "comet"       – a long tapered line-segment comet tail following the cursor.
  • "ribbon"      – a smooth connected ribbon/noodle drawn between trail points.
  • "rainbow"     – cycling full-spectrum hue dots, one revolution per trail length.
  • "noodle"      – a physics-simulated dangling chain: each segment lags behind the
                    cursor with spring + gravity forces, creating a realistic noodle
                    that wobbles and swings as the mouse moves.
  • "distortion"  – a sinusoidal ribbon that writhes and ripples as the cursor moves.
  • "fire"        – glowing embers drift upward behind the cursor (yellow→red).
  • "lightning"   – brief bright bolt-flashes crackle along the trail and vanish.
  • "plasma"      – electric arc sparks crackle in purple and cyan, fast fade.
  • "sakura"      – soft pink rotating petal ellipses drift and fall gently.
  • "smoke"       – expanding gray puffs rise and dissipate behind the cursor.
"""
from collections import deque
import math
import random

from PyQt6.QtCore import Qt, QTimer, QEvent, QObject
from PyQt6.QtGui import QColor, QPainter, QBrush, QFont, QPen, QPainterPath
from PyQt6.QtWidgets import QWidget, QApplication


_FAIRY_DUST   = ["✨", "⭐", "💫", "🌟", "💜", "💛", "🌸"]
_WAVE_DUST    = ["🫧", "💧", "🌊", "🐠", "🐚", "🌀", "🫧"]
_SPARKLE_DUST = ["✦", "❄", "✧", "💎", "❆", "✸", "❅"]
_EMOJI_FONT_FAMILIES = "Apple Color Emoji, Segoe UI Emoji, Noto Color Emoji"

_EMOJI_STYLES = {"fairy", "wave", "sparkle"}
_EMOJI_LISTS  = {
    "fairy":   _FAIRY_DUST,
    "wave":    _WAVE_DUST,
    "sparkle": _SPARKLE_DUST,
}
_ALL_STYLES = {"dots", "fairy", "wave", "sparkle", "comet", "ribbon", "rainbow", "noodle", "distortion",
               "fire", "lightning", "plasma", "sakura", "smoke"}

# ── Noodle physics constants ───────────────────────────────────────────────────────────────────────────
_NOODLE_SEGMENTS   = 18     # number of chain links
_NOODLE_SEG_LEN    = 12.0   # rest length of each link (pixels)
_NOODLE_SPRING_K   = 0.28   # spring stiffness (higher = stiffer, less lag)
_NOODLE_DAMPING    = 0.72   # velocity damping per tick (lower = more swing)
_NOODLE_GRAVITY    = 0.55   # downward gravity acceleration per tick
_NOODLE_MAX_VEL    = 18.0   # cap on link velocity to prevent explosions



class MouseTrailOverlay(QWidget):
    """
    Transparent overlay placed over the main window.

    • WA_TransparentForMouseEvents – all clicks pass through to widgets below.
    • setAutoFillBackground(False) + transparent stylesheet keeps it invisible
      except for the trail dots.
    • An event filter on QApplication captures global mouse-move events.
    • A 60-fps QTimer drives the fade animation.
    """

    def __init__(self, main_window: QWidget):
        super().__init__(main_window)

        # Transparent, non-interactive overlay
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Do NOT set WA_TranslucentBackground or WA_NoSystemBackground here.
        # Those attributes are only effective on top-level windows; on child
        # widgets, WA_NoSystemBackground breaks Qt's backing-store pipeline and
        # causes CompositionMode_Clear to render as solid black.  The correct
        # approach for a transparent child overlay is to leave the backing-store
        # pipeline intact so Qt repaints the parent region before our
        # paintEvent, naturally clearing stale trail pixels.
        self.setAutoFillBackground(False)

        self._main_window = main_window
        self._color = QColor("#e94560")
        # deque of (x, y, alpha_fraction, style_data) where 1.0 = freshest, 0.0 = invisible
        self._trail: deque = deque(maxlen=50)
        self._enabled = False
        # Trail style: "dots" (default), "fairy", "wave", "sparkle", "comet", "ribbon"
        self._style = "dots"
        # Configurable trail parameters
        self._fade_speed: int = 5    # 1=slowest … 10=fastest; maps to decay per tick
        self._intensity: int = 100   # 10–100 %  max rendered alpha (220 × intensity/100)

        # Throttle MouseMove events: only append a new trail point when the
        # cursor has moved at least this many pixels from the last recorded
        # point.  High-DPI mice can fire hundreds of events per second; without
        # throttling the deque fills up instantly, random.choice() runs on
        # every event, and the tick loop processes far more entries than needed.
        self._last_trail_x: int = -9999
        self._last_trail_y: int = -9999
        _MIN_MOVE_PX = 4  # minimum pixel distance before adding a new trail point
        self._min_move_sq: int = _MIN_MOVE_PX * _MIN_MOVE_PX

        # Noodle physics state: list of [x, y, vx, vy] for each chain link.
        # links[0] is anchored at the cursor; links[-1] is the dangling tip.
        self._noodle_links: list = []
        self._noodle_cx: float = 0.0  # last known cursor x (window coords)
        self._noodle_cy: float = 0.0  # last known cursor y (window coords)
        self._noodle_active: bool = False

        # Distortion wave style: running phase counter advanced each tick.
        self._distortion_phase: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps – smoother trail fade without hogging CPU
        self._timer.timeout.connect(self._tick)

        # Cover the entire main window
        self.setGeometry(main_window.rect())
        self.raise_()
        # Start hidden; the overlay is only made visible when the trail is
        # enabled via set_enabled(True).
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        app = QApplication.instance()
        if enabled:
            if app is not None:
                app.installEventFilter(self)
            if self._style == "noodle":
                self._init_noodle()
            else:
                self._timer.start()
            self.raise_()
            self.show()
        else:
            if app is not None:
                app.removeEventFilter(self)
            self._timer.stop()
            self._trail.clear()
            self._noodle_active = False
            self._noodle_links = []
            # Reset throttle state so the next enable sees a fresh start.
            self._last_trail_x = -9999
            self._last_trail_y = -9999
            self.hide()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)

    def set_style(self, style: str) -> None:
        """Set trail style: 'dots', 'fairy', 'wave', 'sparkle', 'comet', 'ribbon',
        'rainbow', 'noodle', or 'distortion'."""
        self._style = style if style in _ALL_STYLES else "dots"
        self._trail.clear()
        if style == "noodle":
            self._init_noodle()
        else:
            self._noodle_active = False
            self._noodle_links = []

    def set_length(self, length: int) -> None:
        """Set trail length (number of trail points kept, 10–200)."""
        length = max(10, min(200, int(length)))
        if length != self._trail.maxlen:
            # Rebuild deque with new maxlen, preserving as many existing points as possible
            old = list(self._trail)
            self._trail = deque(old[-length:] if len(old) > length else old, maxlen=length)

    def set_fade_speed(self, speed: int) -> None:
        """Set fade speed (1=very slow, 10=very fast)."""
        self._fade_speed = max(1, min(10, int(speed)))

    def set_intensity(self, intensity: int) -> None:
        """Set maximum trail opacity (10–100 %)."""
        self._intensity = max(10, min(100, int(intensity)))

    # ------------------------------------------------------------------
    # Event filter – catches global MouseMove for the trail positions
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if not self._enabled:
            return False

        t = event.type()

        # Track mouse positions relative to the main window
        if t in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
            try:
                global_pos = event.globalPosition().toPoint()
                local = self._main_window.mapFromGlobal(global_pos)
                lx, ly = local.x(), local.y()
                # Throttle: skip if cursor has not moved far enough from the
                # last recorded point.  High-DPI mice emit hundreds of events
                # per second; recording every one bloats the deque and calls
                # random.choice() far more than necessary.
                dx = lx - self._last_trail_x
                dy = ly - self._last_trail_y
                if dx * dx + dy * dy < self._min_move_sq:
                    return False
                self._last_trail_x = lx
                self._last_trail_y = ly
                # Store extra data for emoji styles: which emoji to show
                if self._style == "fire":
                    # Fire particles drift upward; store per-particle x-velocity
                    vx = random.uniform(-0.9, 0.9)
                    self._trail.append([lx, ly, 1.0, "", vx])
                elif self._style == "lightning":
                    # Lightning: store random angle and half-length for the bolt line
                    angle = random.uniform(0.0, 2.0 * math.pi)
                    hl = random.uniform(9.0, 22.0)
                    self._trail.append([lx, ly, 1.0, "", angle, hl])
                elif self._style == "plasma":
                    # Plasma: store per-particle hue offset and base angle
                    hue_off = random.randint(0, 359)
                    base_ang = random.uniform(0.0, 2.0 * math.pi)
                    self._trail.append([lx, ly, 1.0, "", hue_off, base_ang])
                elif self._style == "sakura":
                    # Sakura: store rotation angle and horizontal drift velocity
                    rotation = random.uniform(0.0, 2.0 * math.pi)
                    drift_x = random.uniform(-0.7, 0.7)
                    self._trail.append([lx, ly, 1.0, "", rotation, drift_x])
                elif self._style == "smoke":
                    # Smoke: store initial expand radius
                    init_r = random.uniform(4.0, 8.0)
                    self._trail.append([lx, ly, 1.0, "", init_r])
                else:
                    emoji_list = _EMOJI_LISTS.get(self._style, _FAIRY_DUST)
                    emoji = random.choice(emoji_list) if self._style in _EMOJI_STYLES else ""
                    self._trail.append([lx, ly, 1.0, emoji])
                # Noodle: keep cursor position in sync
                if self._style == "noodle":
                    self._noodle_cx = float(lx)
                    self._noodle_cy = float(ly)
                # If the timer was stopped because the trail had emptied, restart it
                # now that a new point has been added.
                if not self._timer.isActive():
                    self._timer.start()
            except AttributeError:
                pass

        # Keep overlay covering the whole window when it resizes
        elif t == QEvent.Type.Resize and obj is self._main_window:
            self.setGeometry(self._main_window.rect())
            self.raise_()

        return False  # never consume events

    # ------------------------------------------------------------------
    # Animation tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        # Noodle style: advance physics and paint, bypassing the regular trail.
        if self._style == "noodle" and self._noodle_active:
            self._tick_noodle()
            return
        if not self._trail:
            # Trail is empty — nothing to animate.  Stop the timer so we don't
            # fire 30 no-op callbacks per second while the mouse is idle.
            # The timer is restarted by eventFilter when the next point arrives.
            self._timer.stop()
            return
        # Skip rendering while the window is minimised to avoid wasting CPU
        # on emoji font shaping for pixels that are never shown.
        if self._main_window.isMinimized() or not self._main_window.isVisible():
            return
        # Compute per-tick decay from fade speed (1=slow, 10=fast)
        # Speed 1 → ~0.02/tick, speed 5 → ~0.05/tick, speed 10 → ~0.12/tick
        base_decay = 0.015 + (self._fade_speed - 1) * 0.012
        # Emoji styles get slightly slower base fade for a lingering sparkle feel
        if self._style in _EMOJI_STYLES:
            base_decay *= 0.7
        # Lightning: very fast fade so bolts flash briefly
        if self._style == "lightning":
            base_decay *= 4.0
        # Plasma: fast fade so arcs crackle and vanish
        if self._style == "plasma":
            base_decay *= 3.0
        # Distortion wave: advance the sinusoidal phase each tick for animation
        if self._style == "distortion":
            self._distortion_phase += 0.18
        new_trail = deque(maxlen=self._trail.maxlen)
        for entry in self._trail:
            x, y, a = entry[0], entry[1], entry[2]
            if self._style == "fire":
                # Drift upward; older (lower alpha) particles have already moved more
                x += entry[4] if len(entry) > 4 else 0.0
                y -= 1.6
                a -= base_decay
                if a > 0.0:
                    new_trail.append([x, y, a, "", entry[4] if len(entry) > 4 else 0.0])
            elif self._style == "lightning":
                a -= base_decay
                if a > 0.0:
                    new_trail.append([x, y, a, "",
                                      entry[4] if len(entry) > 4 else 0.0,
                                      entry[5] if len(entry) > 5 else 15.0])
            elif self._style == "plasma":
                # Particles stay in place; fast fade reveals crackling arc effect
                a -= base_decay
                if a > 0.0:
                    new_trail.append([x, y, a, "",
                                      entry[4] if len(entry) > 4 else 0,
                                      entry[5] if len(entry) > 5 else 0.0])
            elif self._style == "sakura":
                # Petals drift downward and sideways, rotating as they fall
                drift_x = entry[5] if len(entry) > 5 else 0.0
                rotation = (entry[4] if len(entry) > 4 else 0.0) + 0.06
                x += drift_x * 0.5
                y += 0.8
                a -= base_decay
                if a > 0.0:
                    new_trail.append([x, y, a, "", rotation, drift_x])
            elif self._style == "smoke":
                # Smoke puffs drift upward and expand
                expand_r = (entry[4] if len(entry) > 4 else 4.0) + 0.8
                y -= 0.5
                a -= base_decay * 1.2
                if a > 0.0:
                    new_trail.append([x, y, a, "", expand_r])
            else:
                emoji = entry[3] if len(entry) > 3 else ""
                a -= base_decay
                if a > 0.0:
                    new_trail.append([x, y, a, emoji])
        self._trail = new_trail
        if not self._trail:
            # Last particles just faded out — stop the timer until new points
            # arrive.  This is a second check (distinct from the early-return
            # above) to handle the case where the trail transitioned from
            # non-empty to empty during this tick.
            self._timer.stop()
        # Always request a full repaint so Qt re-paints the parent region
        # first, clearing stale trail pixels before we draw new ones.
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Qt's backing store already re-painted the parent region before
        # calling this paintEvent (standard non-opaque child widget behaviour),
        # so stale trail pixels from previous frames are automatically cleared.
        # We simply draw the current trail on top.
        if not self._trail:
            painter.end()
            return

        painter.setPen(Qt.PenStyle.NoPen)

        if self._style == "noodle":
            self._paint_noodle(painter)
        elif self._style in _EMOJI_STYLES:
            self._paint_emoji(painter)
        elif self._style == "comet":
            self._paint_comet(painter)
        elif self._style == "ribbon":
            self._paint_ribbon(painter)
        elif self._style == "rainbow":
            self._paint_rainbow(painter)
        elif self._style == "distortion":
            self._paint_distortion(painter)
        elif self._style == "fire":
            self._paint_fire(painter)
        elif self._style == "lightning":
            self._paint_lightning(painter)
        elif self._style == "plasma":
            self._paint_plasma(painter)
        elif self._style == "sakura":
            self._paint_sakura(painter)
        elif self._style == "smoke":
            self._paint_smoke(painter)
        else:
            self._paint_dots(painter)

        painter.end()

    def _paint_dots(self, painter: QPainter) -> None:
        max_alpha = int(220 * self._intensity / 100)
        for entry in self._trail:
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            alpha = max(0, min(255, int(alpha_frac * max_alpha)))
            radius = max(2, int(alpha_frac * 9))
            c = QColor(self._color)
            c.setAlpha(alpha)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)

    def _paint_emoji(self, painter: QPainter) -> None:
        """Paint emoji-style trail particles (fairy, wave, sparkle)."""
        font = QFont(_EMOJI_FONT_FAMILIES, 14)
        painter.setFont(font)
        # Limit the number of emoji drawText calls per frame to prevent
        # font-rendering storms when moving the mouse quickly over a long trail.
        max_emit = 12
        emitted = 0
        max_opacity = self._intensity / 100.0
        for entry in self._trail:
            if emitted >= max_emit:
                break
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            emoji = entry[3] if len(entry) > 3 and entry[3] else "✨"
            alpha = max(0, min(255, int(alpha_frac * 210 * max_opacity)))
            # Tint text using alpha via composition
            painter.setOpacity(alpha / 255.0)
            painter.drawText(x - 8, y + 8, emoji)
            emitted += 1
        painter.setOpacity(1.0)

    def _paint_comet(self, painter: QPainter) -> None:
        """Paint a tapered comet-tail: wide bright head tapering to thin faint tail."""
        trail_list = list(self._trail)
        n = len(trail_list)
        if n < 2:
            return
        max_alpha = int(230 * self._intensity / 100)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, entry in enumerate(trail_list):
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            # Newest entries are at the end of the deque; head = last entry
            pos_frac = i / max(n - 1, 1)  # 0 = tail, 1 = head
            alpha = max(0, min(255, int(alpha_frac * max_alpha * pos_frac)))
            radius = max(1, int(pos_frac * 11))
            c = QColor(self._color)
            c.setAlpha(alpha)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)

    def _paint_ribbon(self, painter: QPainter) -> None:
        """Paint a smooth connected ribbon/noodle through all trail points."""
        trail_list = list(self._trail)
        n = len(trail_list)
        if n < 2:
            return
        max_alpha = int(200 * self._intensity / 100)
        # Draw a Bezier path through the trail points with varying width
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(1, n):
            x1, y1, a1 = trail_list[i-1][0], trail_list[i-1][1], trail_list[i-1][2]
            x2, y2, a2 = trail_list[i][0], trail_list[i][1], trail_list[i][2]
            alpha = max(0, min(255, int((a1 + a2) / 2 * max_alpha)))
            pos_frac = i / max(n - 1, 1)
            width = max(1.0, pos_frac * 8.0)
            c = QColor(self._color)
            c.setAlpha(alpha)
            pen = QPen(c, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(x1, y1, x2, y2)
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_rainbow(self, painter: QPainter) -> None:
        """Paint rainbow dots — each dot cycles through the full hue spectrum.

        The hue advances by (360 / trail_length) per point so that a full trail
        sweeps through the entire colour wheel exactly once, creating a smooth
        rainbow ribbon regardless of mouse speed.
        """
        trail_list = list(self._trail)
        n = len(trail_list)
        if n == 0:
            return
        max_alpha = int(220 * self._intensity / 100)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, entry in enumerate(trail_list):
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            alpha = max(0, min(255, int(alpha_frac * max_alpha)))
            # Hue cycles 0→360 from tail (index 0) to head (index n-1)
            hue = int(i / max(n - 1, 1) * 359)
            c = QColor.fromHsv(hue, 255, 255, alpha)
            radius = max(2, int(alpha_frac * 9))
            painter.setBrush(QBrush(c))
            painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)

    def _paint_distortion(self, painter: QPainter) -> None:
        """Paint a wavy sinusoidal ribbon — the trail path is bent perpendicular
        to each segment by a sine wave that advances each tick, creating a
        living, writhing distortion effect as the cursor moves.
        """
        trail_list = list(self._trail)
        n = len(trail_list)
        if n < 2:
            return
        max_alpha = int(220 * self._intensity / 100)
        # Maximum perpendicular displacement in pixels (shrinks as trail fades)
        amp_base = 12.0
        for i in range(1, n):
            x1, y1, a1 = trail_list[i-1][0], trail_list[i-1][1], trail_list[i-1][2]
            x2, y2, a2 = trail_list[i][0], trail_list[i][1], trail_list[i][2]
            # Perpendicular unit vector to this segment
            dx, dy = x2 - x1, y2 - y1
            seg_len = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / seg_len, dx / seg_len
            # Sinusoidal displacement fades proportionally with trail opacity
            avg_a = (a1 + a2) * 0.5
            amp = amp_base * avg_a
            w1 = math.sin(self._distortion_phase + i * 1.2) * amp
            w2 = math.sin(self._distortion_phase + (i + 1) * 1.2) * amp
            px1 = x1 + nx * w1
            py1 = y1 + ny * w1
            px2 = x2 + nx * w2
            py2 = y2 + ny * w2
            alpha = max(0, min(255, int(avg_a * max_alpha)))
            pos_frac = i / max(n - 1, 1)
            width = max(1.5, pos_frac * 6.0)
            c = QColor(self._color)
            c.setAlpha(alpha)
            pen = QPen(c, width, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(int(px1), int(py1), int(px2), int(py2))
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_fire(self, painter: QPainter) -> None:
        """Fire trail – warm-colored discs that drift upward and fade.

        Young (high alpha) particles are bright yellow; older ones are orange
        then deep red, simulating cooling embers rising from a flame.
        """
        max_alpha = int(210 * self._intensity / 100)
        for entry in self._trail:
            x, y, alpha_frac = int(entry[0]), int(entry[1]), entry[2]
            alpha = max(0, min(255, int(alpha_frac * max_alpha)))
            # Hue: 60° (yellow) at peak → 15° (orange-red) as it fades
            hue = int(60 * alpha_frac + 10 * (1.0 - alpha_frac))
            sat = 255
            val = max(160, int(255 * alpha_frac))
            c = QColor.fromHsv(hue, sat, val, alpha)
            radius = max(2, int(alpha_frac * 8 + 2))
            painter.setBrush(QBrush(c))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_lightning(self, painter: QPainter) -> None:
        """Lightning trail – brief bright bolt segments that flash and vanish.

        Each particle is a short bright line at a stored random angle.  The
        bolts are white-to-cyan and fade out very rapidly (high decay in tick).
        """
        max_alpha = int(255 * self._intensity / 100)
        for entry in self._trail:
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            angle = entry[4] if len(entry) > 4 else 0.0
            hl    = entry[5] if len(entry) > 5 else 15.0
            alpha = max(0, min(255, int(alpha_frac * max_alpha)))
            # Colour: bright white-cyan at peak, cooler blue as it fades
            hue = int(195 + 30 * (1.0 - alpha_frac))   # 195–225 (cyan → blue)
            c = QColor.fromHsv(hue, max(0, int(220 * (1.0 - alpha_frac))),
                                255, alpha)
            width = max(1.0, alpha_frac * 3.0)
            pen = QPen(c, width, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            dx = math.cos(angle) * hl
            dy = math.sin(angle) * hl
            painter.drawLine(int(x - dx), int(y - dy), int(x + dx), int(y + dy))
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_fairy(self, painter: QPainter) -> None:
        """Legacy alias for _paint_emoji (kept for compatibility)."""
        self._paint_emoji(painter)

    def _paint_plasma(self, painter: QPainter) -> None:
        """Plasma trail – electric arc sparks in cycling cyan/purple/white.

        Each particle emits three radiating spark lines 120° apart at a
        randomly assigned base angle, creating a crackling arc-burst effect.
        The colour sweeps through purple→cyan at high saturation and fades fast.
        """
        max_alpha = int(230 * self._intensity / 100)
        for entry in self._trail:
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            hue_off = int(entry[4]) if len(entry) > 4 else 0
            base_ang = entry[5] if len(entry) > 5 else 0.0
            alpha = max(0, min(255, int(alpha_frac * max_alpha)))
            # Hue: purple (270°) at peak → cyan (180°) as it fades, shifted by hue_off
            hue = (270 - int(90 * (1.0 - alpha_frac)) + hue_off) % 360
            sat = max(80, int(220 * alpha_frac + 60 * (1.0 - alpha_frac)))
            val = min(255, int(220 * alpha_frac + 100 * (1.0 - alpha_frac)))
            c = QColor.fromHsv(hue, sat, val, alpha)
            spark_len = max(3, int(alpha_frac * 14))
            width = max(1.0, alpha_frac * 2.5)
            pen = QPen(c, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            for ang_off in (0.0, math.pi * 2.0 / 3.0, math.pi * 4.0 / 3.0):
                angle = base_ang + ang_off
                dx = math.cos(angle) * spark_len
                dy = math.sin(angle) * spark_len
                painter.drawLine(int(x - dx), int(y - dy), int(x + dx), int(y + dy))
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_sakura(self, painter: QPainter) -> None:
        """Sakura trail – small pink petal-shaped ellipses that drift and rotate.

        Each particle is a slightly elongated ellipse rotated by a per-particle
        angle that advances each tick, simulating petals drifting in a breeze.
        Colour fades from deep pink at peak to near-white as the petal ages.
        """
        max_alpha = int(200 * self._intensity / 100)
        for entry in self._trail:
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            rotation = entry[4] if len(entry) > 4 else 0.0
            alpha = max(0, min(255, int(alpha_frac * max_alpha)))
            # Deep pink (HSV 338°) at peak → near-white as it fades
            hue = 338
            sat = max(0, int(180 * alpha_frac))
            val = min(255, int(200 + 55 * alpha_frac))
            c = QColor.fromHsv(hue, sat, val, alpha)
            petal_w = max(2, int(alpha_frac * 9 + 2))
            petal_h = max(1, int(alpha_frac * 5 + 1))
            painter.save()
            painter.translate(int(x), int(y))
            painter.rotate(math.degrees(rotation))
            painter.setBrush(QBrush(c))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(-petal_w, -petal_h, petal_w * 2, petal_h * 2)
            painter.restore()
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_smoke(self, painter: QPainter) -> None:
        """Smoke trail – soft expanding gray puffs that rise and dissipate.

        Each particle is a circle whose radius grows each tick as alpha fades,
        mimicking a smoke puff that expands and thins out as it rises.
        """
        max_alpha = int(160 * self._intensity / 100)
        for entry in self._trail:
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            expand_r = int(entry[4]) if len(entry) > 4 else 4
            # Translucency ramps down as puff expands; cap to avoid overdraw
            effective_alpha = max(0, min(255, int(alpha_frac * max_alpha * 0.65)))
            # Light gray at fresh, slightly darker as it ages
            gray_val = min(255, int(180 + 60 * alpha_frac))
            c = QColor(gray_val, gray_val, gray_val, effective_alpha)
            painter.setBrush(QBrush(c))
            painter.setPen(Qt.PenStyle.NoPen)
            r = max(3, expand_r)
            painter.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)
        painter.setPen(Qt.PenStyle.NoPen)

    # ------------------------------------------------------------------
    # Noodle physics helpers
    # ------------------------------------------------------------------

    def _init_noodle(self) -> None:
        """Initialise the noodle chain with all links at the current cursor
        position (or 0,0 if cursor has not been seen yet)."""
        cx = self._noodle_cx if self._noodle_cx else 0.0
        cy = self._noodle_cy if self._noodle_cy else 0.0
        self._noodle_links = [
            [cx, cy + i * _NOODLE_SEG_LEN, 0.0, 0.0]
            for i in range(_NOODLE_SEGMENTS)
        ]
        self._noodle_active = True
        if not self._timer.isActive():
            self._timer.start()
        self.raise_()
        self.show()

    def _tick_noodle(self) -> None:
        """Advance the noodle spring-chain physics by one tick and repaint."""
        if not self._noodle_links:
            return
        links = self._noodle_links
        # link[0] is pulled strongly toward the cursor
        cx, cy = self._noodle_cx, self._noodle_cy
        lx0, ly0 = links[0][0], links[0][1]
        links[0][2] += (cx - lx0) * 0.55  # strong attraction to cursor
        links[0][3] += (cy - ly0) * 0.55
        links[0][2] *= _NOODLE_DAMPING
        links[0][3] *= _NOODLE_DAMPING
        links[0][0] += links[0][2]
        links[0][1] += links[0][3]
        # Each subsequent link is attracted to the link before it
        for i in range(1, len(links)):
            px, py = links[i - 1][0], links[i - 1][1]
            lx, ly, vx, vy = links[i]
            # Spring force toward previous link
            dx, dy = px - lx, py - ly
            dist = math.hypot(dx, dy) or 1.0
            stretch = dist - _NOODLE_SEG_LEN
            force_x = (dx / dist) * stretch * _NOODLE_SPRING_K
            force_y = (dy / dist) * stretch * _NOODLE_SPRING_K
            vx = (vx + force_x) * _NOODLE_DAMPING
            vy = (vy + force_y + _NOODLE_GRAVITY) * _NOODLE_DAMPING
            # Cap velocity
            speed = math.hypot(vx, vy)
            if speed > _NOODLE_MAX_VEL:
                vx = vx / speed * _NOODLE_MAX_VEL
                vy = vy / speed * _NOODLE_MAX_VEL
            links[i] = [lx + vx, ly + vy, vx, vy]
        self.update()

    def _paint_noodle(self, painter: QPainter) -> None:
        """Draw the physics-based noodle chain.

        The noodle is rendered as a smooth tapered ribbon: thicker near the
        cursor end, thinner at the dangling tip, in the trail colour.
        """
        links = self._noodle_links
        n = len(links)
        if n < 2:
            return
        max_alpha = int(220 * self._intensity / 100)
        painter.setPen(Qt.PenStyle.NoPen)
        # Draw individual segments from head (index 0) to tail (index n-1)
        for i in range(1, n):
            x1, y1 = links[i - 1][0], links[i - 1][1]
            x2, y2 = links[i][0], links[i][1]
            # Opacity and width taper toward the tail
            pos_frac = 1.0 - (i / (n - 1))  # 1.0 at head, 0.0 at tail
            alpha = max(0, min(255, int(pos_frac * max_alpha)))
            width = max(1.5, pos_frac * 9.0)
            c = QColor(self._color)
            c.setAlpha(alpha)
            pen = QPen(
                c, width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.setPen(Qt.PenStyle.NoPen)
        # Draw a small dot at the tip
        tx, ty = int(links[-1][0]), int(links[-1][1])
        tip_c = QColor(self._color)
        tip_c.setAlpha(max(0, min(255, int(0.4 * max_alpha))))
        painter.setBrush(QBrush(tip_c))
        painter.drawEllipse(tx - 3, ty - 3, 6, 6)
