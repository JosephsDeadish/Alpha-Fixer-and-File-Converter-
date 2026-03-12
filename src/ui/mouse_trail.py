"""
MouseTrailOverlay – a transparent child widget that paints a fading particle
trail following the mouse cursor over the main application window.

Works on any platform that supports Qt child widgets with transparent
backgrounds (i.e., all modern Qt6 deployments).

The overlay supports six trail styles:
  • "dots"    – the default: fading coloured dots (original behaviour).
  • "fairy"   – fairy-dust sparkle emoji (✨💫⭐) that float and fade gently.
  • "wave"    – ocean-themed bubbles and sea emoji (🫧💧🌊) for aquatic themes.
  • "sparkle" – icy crystal sparkle emoji (✦❄✧💎) for arctic/ice themes.
  • "comet"   – a long tapered line-segment comet tail following the cursor.
  • "ribbon"  – a smooth connected ribbon/noodle drawn between trail points.
"""
from collections import deque
import random

from PyQt6.QtCore import Qt, QTimer, QEvent, QObject, QPoint
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
_ALL_STYLES = {"dots", "fairy", "wave", "sparkle", "comet", "ribbon"}


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
            self._timer.start()
            self.raise_()
            self.show()
        else:
            if app is not None:
                app.removeEventFilter(self)
            self._timer.stop()
            self._trail.clear()
            # Reset throttle state so the next enable sees a fresh start.
            self._last_trail_x = -9999
            self._last_trail_y = -9999
            self.hide()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)

    def set_style(self, style: str) -> None:
        """Set trail style: 'dots', 'fairy', 'wave', 'sparkle', 'comet', or 'ribbon'."""
        self._style = style if style in _ALL_STYLES else "dots"
        self._trail.clear()

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
                emoji_list = _EMOJI_LISTS.get(self._style, _FAIRY_DUST)
                emoji = random.choice(emoji_list) if self._style in _EMOJI_STYLES else ""
                self._trail.append([lx, ly, 1.0, emoji])
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
        new_trail = deque(maxlen=self._trail.maxlen)
        for entry in self._trail:
            x, y, a = entry[0], entry[1], entry[2]
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

        if self._style in _EMOJI_STYLES:
            self._paint_emoji(painter)
        elif self._style == "comet":
            self._paint_comet(painter)
        elif self._style == "ribbon":
            self._paint_ribbon(painter)
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

    def _paint_fairy(self, painter: QPainter) -> None:
        """Legacy alias for _paint_emoji (kept for compatibility)."""
        self._paint_emoji(painter)
