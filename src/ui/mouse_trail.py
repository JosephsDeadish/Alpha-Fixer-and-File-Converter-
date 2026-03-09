"""
MouseTrailOverlay – a transparent child widget that paints a fading particle
trail following the mouse cursor over the main application window.

Works on any platform that supports Qt child widgets with transparent
backgrounds (i.e., all modern Qt6 deployments).
"""
from collections import deque

from PyQt6.QtCore import Qt, QTimer, QEvent, QObject
from PyQt6.QtGui import QColor, QPainter, QBrush
from PyQt6.QtWidgets import QWidget, QApplication


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
        # Do NOT set WA_NoSystemBackground – without it Qt erases the widget
        # region before paintEvent, required for correct transparent behaviour.
        self.setAutoFillBackground(False)
        # Transparent background so Qt composites parent content through it.
        self.setStyleSheet("background-color: transparent;")

        self._main_window = main_window
        self._color = QColor("#e94560")
        # deque of (x, y, alpha_fraction) where 1.0 = freshest, 0.0 = invisible
        self._trail: deque = deque(maxlen=30)
        self._enabled = False

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
                self._trail.append([local.x(), local.y(), 1.0])
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
        # Fade each particle
        decay = 0.08
        new_trail = deque(maxlen=self._trail.maxlen)
        for x, y, a in self._trail:
            a -= decay
            if a > 0.0:
                new_trail.append([x, y, a])
        self._trail = new_trail
        # Always repaint so the clear-frame is issued when the trail empties.
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Erase stale trail pixels from the previous frame so the trail fades
        # correctly rather than leaving permanent ghost marks.
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )

        if not self._trail:
            painter.end()
            return

        painter.setPen(Qt.PenStyle.NoPen)

        for x, y, alpha_frac in self._trail:
            alpha = max(0, min(255, int(alpha_frac * 220)))
            radius = max(2, int(alpha_frac * 9))
            c = QColor(self._color)
            c.setAlpha(alpha)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)

        painter.end()
