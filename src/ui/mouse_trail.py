"""
MouseTrailOverlay – a transparent child widget that paints a fading particle
trail following the mouse cursor over the main application window.

Works on any platform that supports Qt child widgets with transparent
backgrounds (i.e., all modern Qt6 deployments).

The overlay supports two trail styles:
  • "dots"  – the default: fading coloured dots (original behaviour).
  • "fairy" – fairy-dust sparkle emoji (✨💫⭐) that float and fade gently.
"""
from collections import deque

from PyQt6.QtCore import Qt, QTimer, QEvent, QObject
from PyQt6.QtGui import QColor, QPainter, QBrush, QFont
from PyQt6.QtWidgets import QWidget, QApplication


_FAIRY_DUST = ["✨", "⭐", "💫", "🌟", "💜", "💛", "🌸"]
_EMOJI_FONT_FAMILIES = "Apple Color Emoji, Segoe UI Emoji, Noto Color Emoji"


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
        # WA_NoSystemBackground prevents Qt from pre-filling this widget's
        # region with the background colour before paintEvent.  Without it
        # the overlay would erase every child widget drawn beneath it.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

        self._main_window = main_window
        self._color = QColor("#e94560")
        # deque of (x, y, alpha_fraction, style_data) where 1.0 = freshest, 0.0 = invisible
        self._trail: deque = deque(maxlen=30)
        self._enabled = False
        # Trail style: "dots" (default) or "fairy" (sparkle emoji)
        self._style = "dots"

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._tick)

        # Cover the entire main window
        self.setGeometry(main_window.rect())
        self.raise_()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            self._trail.clear()
            self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)

    def set_style(self, style: str) -> None:
        """Set trail style: 'dots' (default) or 'fairy' (sparkle emoji)."""
        self._style = style if style in ("dots", "fairy") else "dots"
        self._trail.clear()

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
                import random
                # Store extra data for fairy style: which emoji to show
                emoji = random.choice(_FAIRY_DUST) if self._style == "fairy" else ""
                self._trail.append([local.x(), local.y(), 1.0, emoji])
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
            return
        # Fairy dust fades more slowly for a lingering sparkle effect
        decay = 0.05 if self._style == "fairy" else 0.08
        new_trail = deque(maxlen=self._trail.maxlen)
        for entry in self._trail:
            x, y, a = entry[0], entry[1], entry[2]
            emoji = entry[3] if len(entry) > 3 else ""
            a -= decay
            if a > 0.0:
                new_trail.append([x, y, a, emoji])
        self._trail = new_trail
        if new_trail:
            self.update()
        else:
            parent = self.parentWidget()
            if parent is not None:
                parent.update(self.geometry())

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        if not self._trail:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if self._style == "fairy":
            self._paint_fairy(painter)
        else:
            self._paint_dots(painter)

        painter.end()

    def _paint_dots(self, painter: QPainter) -> None:
        for entry in self._trail:
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            alpha = max(0, min(255, int(alpha_frac * 220)))
            radius = max(2, int(alpha_frac * 9))
            c = QColor(self._color)
            c.setAlpha(alpha)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)

    def _paint_fairy(self, painter: QPainter) -> None:
        font = QFont(_EMOJI_FONT_FAMILIES, 14)
        painter.setFont(font)
        for entry in self._trail:
            x, y, alpha_frac = entry[0], entry[1], entry[2]
            emoji = entry[3] if len(entry) > 3 and entry[3] else "✨"
            alpha = max(0, min(255, int(alpha_frac * 210)))
            # Tint text using alpha via composition
            painter.setOpacity(alpha / 255.0)
            painter.drawText(x - 8, y + 8, emoji)
        painter.setOpacity(1.0)
