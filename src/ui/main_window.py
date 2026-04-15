"""
Main application window.
"""
import collections
import math
import random
import sys
import webbrowser

from PyQt6.QtCore import Qt, QEvent, QObject, QPoint, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QFont, QFontMetrics, QIcon, QKeyEvent, QKeySequence, QPixmap, QPainter, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QMenu,
    QLabel, QPushButton, QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QMessageBox, QFileDialog,
)

from ..core.settings_manager import SettingsManager, DEFAULT_CUSTOM_EMOJI
from ..core.presets import PresetManager
from .alpha_tool import AlphaFixerTab
from .converter_tool import ConverterTab
from .history_tab import HistoryTab
from .selective_alpha_tool import SelectiveAlphaTool
from .settings_dialog import SettingsDialog
from .theme_engine import (
    build_stylesheet, THEME_EFFECTS,
    get_theme_svg_path, get_theme_status,
    get_theme_tab_labels, get_theme_icon,
)
from ..version import __version__

PATREON_URL = "https://www.patreon.com/c/DeadOnTheInside"

# QEvent.Type.ScreenChangeInternal is not exposed by name in all PyQt6 builds
# (the underlying Qt integer value is 214).  Resolve it once at import time so
# the comparison in changeEvent() never raises AttributeError at runtime.
try:
    _SCREEN_CHANGE_INTERNAL: QEvent.Type = QEvent.Type.ScreenChangeInternal
except AttributeError:
    _SCREEN_CHANGE_INTERNAL = QEvent.Type(214)  # type: ignore[assignment]


def _apply_dwm_title_bar_color(hwnd: int, hex_color: str) -> bool:
    """Attempt to set the Windows 11+ title bar color via DWM.

    Uses DwmSetWindowAttribute (DWMWA_CAPTION_COLOR = 35) which is only
    supported on Windows 11 build 22000+.  Silently returns False on older
    Windows versions or non-Windows platforms.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import ctypes.wintypes
        # Parse "#rrggbb" → COLORREF (0x00bbggrr)
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return False
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        colorref = ctypes.c_uint32(b << 16 | g << 8 | r)
        DWMWA_CAPTION_COLOR = 35
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.c_uint32(DWMWA_CAPTION_COLOR),
            ctypes.byref(colorref),
            ctypes.c_uint32(ctypes.sizeof(colorref)),
        )
        return True
    except Exception:
        return False

_CURSOR_MAP = {
    "Default":        Qt.CursorShape.ArrowCursor,
    "Cross":          Qt.CursorShape.CrossCursor,
    "Pointing Hand":  Qt.CursorShape.PointingHandCursor,
    "Open Hand":      Qt.CursorShape.OpenHandCursor,
    "Hourglass":      Qt.CursorShape.WaitCursor,
    "Forbidden":      Qt.CursorShape.ForbiddenCursor,
    "IBeam":          Qt.CursorShape.IBeamCursor,
    "Size All":       Qt.CursorShape.SizeAllCursor,
    "Blank":          Qt.CursorShape.BlankCursor,
}

# Cursor animation frame sequences keyed by the leading emoji character.
# Each entry is the ordered list of emoji to cycle through at ~400 ms per
# frame (≈ 2.5 fps — fast enough to be playful, slow enough to remain legible).
# Themes that map to one of these emoji automatically get an animated cursor
# when "Animate cursor" is enabled in settings.
_CURSOR_ANIM_FRAMES: dict[str, list[str]] = {
    # All previously listed entries are now shadowed by _CURSOR_SPIN_EMOJI
    # (checked first) or _CURSOR_WOBBLE_EMOJI (checked second) in
    # _start_cursor_anim, so the cycling fallback is never reached for any
    # of the themed emoji.  The dict is kept as the extension point for
    # future emoji that belong in neither the spin nor the wobble set.
}


def _make_emoji_cursor(emoji: str, size: int = 48) -> QCursor:
    """Render *emoji* into a square pixmap and return a QCursor from it.

    The emoji is drawn centred in the pixmap and the hotspot is placed at
    the logical centre so that interactions (clicks, hover) register at the
    visual centre of the emoji character rather than at the invisible
    top-left corner of the bounding box.

    The font is rendered at 65 % of the logical pixmap size (pixel-size, not
    point-size) so wide glyphs (e.g. 🦈 🌊) have adequate margin on every
    side and are never clipped at the pixmap boundary.

    On HiDPI / Retina displays the pixmap is created at the screen's physical
    pixel density (devicePixelRatio) and the ratio is set on the pixmap so Qt
    uses it at full physical resolution rather than scaling up a low-res bitmap.

    Falls back to the arrow cursor if pixmap painting is unavailable
    (e.g. running headless without a display).
    """
    try:
        # Obtain the current screen DPR so the cursor is sharp on HiDPI
        # displays.  Fall back to 1.0 if no screen is available (headless).
        from PyQt6.QtWidgets import QApplication  # local import – avoids circular
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        # Physical pixmap dimensions for crisp HiDPI rendering.
        phys = max(1, int(size * dpr))
        pix = QPixmap(phys, phys)
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pix)
        # Use a font stack that covers Windows (Segoe UI Emoji), macOS
        # (Apple Color Emoji), and Linux (Noto Color Emoji).
        font = QFont()
        font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"])
        # setPixelSize guarantees a fixed rendered glyph height in logical
        # pixels regardless of screen DPI, unlike setPointSize which scales
        # with DPI and produced a ~40 px glyph crammed into a 40 px pixmap
        # (zero margin → wide emoji were clipped).  65 % of the logical size
        # leaves ~17 % margin on each side — enough for any emoji glyph.
        font.setPixelSize(max(8, int(size * 0.65)))
        painter.setFont(font)
        # Draw in logical coordinates (0..size); the pixmap's DPR causes Qt
        # to automatically scale the drawing to physical resolution.
        painter.drawText(
            QRect(0, 0, size, size),
            Qt.AlignmentFlag.AlignCenter,
            emoji,
        )
        painter.end()
        # Hotspot at logical centre so the interaction point matches the
        # visual centre of the emoji on all display densities.
        return QCursor(pix, size // 2, size // 2)
    except Exception:
        return QCursor(Qt.CursorShape.ArrowCursor)


# ---------------------------------------------------------------------------
# Cursor spin-animation support
# ---------------------------------------------------------------------------

# Emoji that look good when they physically spin (rather than just cycling to
# different symbols).  The cursor animation system renders N rotated frames and
# cycles through them so the glyph visibly rotates.
_CURSOR_SPIN_EMOJI: frozenset = frozenset([
    "🪄", "⭐", "🌟", "✨", "❄", "🔮", "💎", "💫", "🌀", "🎯",
    "🌸", "🪸", "🍄", "🌺", "🎪",
])

# Number of rotation frames for spinning cursor animation
_CURSOR_SPIN_FRAMES = 12


def _make_spin_cursor_frames(emoji: str, size: int = 48) -> list[QCursor]:
    """Pre-render *n* evenly-spaced rotation frames of *emoji* as QCursor objects.

    Returns a list of ``_CURSOR_SPIN_FRAMES`` cursors, each rotated by
    ``360 / n`` degrees relative to the previous one.  Cycling through them
    at ~80 ms/frame produces a smooth ~15 fps spin effect.
    """
    try:
        from PyQt6.QtWidgets import QApplication  # local import to avoid circular
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        phys = max(1, int(size * dpr))
        n = _CURSOR_SPIN_FRAMES
        cursors: list[QCursor] = []
        for i in range(n):
            angle = 360.0 * i / n
            pix = QPixmap(phys, phys)
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            # Rotate around the logical centre
            cx = size / 2.0
            cy = size / 2.0
            p.translate(cx, cy)
            p.rotate(angle)
            p.translate(-cx, -cy)
            font = QFont()
            font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"])
            font.setPixelSize(max(8, int(size * 0.65)))
            p.setFont(font)
            p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, emoji)
            p.end()
            cursors.append(QCursor(pix, size // 2, size // 2))
        return cursors
    except Exception:
        return []


# Emoji that look good when they physically wobble (rock ±20° back and forth).
# Most non-round themed glyphs belong here rather than in _CURSOR_SPIN_EMOJI.
_CURSOR_WOBBLE_EMOJI: frozenset = frozenset([
    "🦈", "🐉", "🧙", "🧟", "🛸", "🧜", "🌹", "🌈", "🎃", "🦇",
    "🌙", "🐼", "🦦", "🌋", "🏴‍☠️", "💰", "🔥", "⚡", "🌊", "🫧",
    "🐱", "🌌", "🍜",
])

# Number of frames for the wobble animation (rocking ±_CURSOR_WOBBLE_MAX_ANGLE°)
_CURSOR_WOBBLE_FRAMES = 16
_CURSOR_WOBBLE_MAX_ANGLE = 20.0


def _make_wobble_cursor_frames(emoji: str, size: int = 48) -> list[QCursor]:
    """Pre-render *n* frames of *emoji* rocking ±``_CURSOR_WOBBLE_MAX_ANGLE``°.

    The result is a list of ``_CURSOR_WOBBLE_FRAMES`` cursors that, when
    cycled through, produce a pendulum-like physical wobble — a genuine
    animation instead of emoji cycling.
    """
    try:
        from PyQt6.QtWidgets import QApplication  # local import to avoid circular
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        phys = max(1, int(size * dpr))
        n = _CURSOR_WOBBLE_FRAMES
        cursors: list[QCursor] = []
        for i in range(n):
            # Sinusoidal angle: 0 → +max → 0 → -max → 0 over n frames
            phase = 2.0 * math.pi * i / n
            angle = math.sin(phase) * _CURSOR_WOBBLE_MAX_ANGLE
            pix = QPixmap(phys, phys)
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            cx = size / 2.0
            cy = size / 2.0
            p.translate(cx, cy)
            p.rotate(angle)
            p.translate(-cx, -cy)
            font = QFont()
            font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"])
            font.setPixelSize(max(8, int(size * 0.65)))
            p.setFont(font)
            p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, emoji)
            p.end()
            cursors.append(QCursor(pix, size // 2, size // 2))
        return cursors
    except Exception:
        return []


class _SpinningEmojiLabel(QWidget):
    """Renders a single emoji with one of several animation modes.

    Modes
    -----
    "spin"      Continuous 360° rotation (original behaviour).
    "bounce"    Vertical bobbing using a sine wave.
    "shake"     Rapid horizontal quiver.
    "pendulum"  Oscillating swing (±30°) like a metronome.
    "static"    No motion; used when an external flock effect is active.

    The active mode is set via ``set_mode()``.  The animation is toggled
    via ``set_animated()`` exactly as before so all callers stay compatible.
    """

    _INTERVAL_MS = 33  # ~30 fps

    # Per-mode speed constants
    _SPIN_DEG_PER_FRAME = 2.0   # full rotation ≈ 6 s
    _BOUNCE_STEP        = 0.12  # rad/tick ≈ full cycle / ~4 s
    _SHAKE_STEP         = 0.40  # rad/tick ≈ full cycle / ~0.5 s
    _PENDULUM_STEP      = 0.06  # rad/tick ≈ full cycle / ~10 s
    _PULSE_STEP         = 0.10  # rad/tick ≈ full cycle / ~5 s
    _FLOAT_STEP         = 0.04  # rad/tick ≈ very slow drift
    _FLIP_STEP          = 0.18  # rad/tick for horizontal squeeze-flip
    _ORBIT_STEP         = 0.08  # rad/tick ≈ full orbit / ~8 s
    _GLITCH_STEP        = 0.45  # rad/tick ≈ glitch strobe speed
    _DRIP_STEP          = 0.012 # 0–1 progress per tick ≈ 3 s fall cycle

    _BOUNCE_AMPLITUDE   = 6     # pixels
    _SHAKE_AMPLITUDE    = 5     # pixels
    _FLOAT_AMPLITUDE    = 8     # pixels vertical drift
    _PENDULUM_MAX_ANGLE = 30.0  # degrees
    _PULSE_MIN_SCALE    = 0.75
    _PULSE_MAX_SCALE    = 1.25
    _ORBIT_RADIUS       = 7     # pixels orbital radius
    _DRIP_FALL_PX       = 24    # pixels total fall distance for drip mode

    _VALID_MODES = frozenset({"spin", "bounce", "shake", "pendulum", "pulse", "float", "flip",
                               "orbit", "glitch", "drip", "static"})

    def __init__(self, emoji: str = "🐼", font_size: int = 20, parent=None):
        super().__init__(parent)
        self._emoji = emoji
        self._font_size = font_size
        self._mode = "spin"
        self._angle = 0.0     # degrees – used by spin / pendulum / flip
        self._phase = 0.0     # radians – used by bounce / shake / pendulum / pulse / float / flip
        self._offset_x = 0.0  # pixel offset for bounce / shake / float
        self._offset_y = 0.0
        self._scale = 1.0     # scale factor for pulse mode
        self._flip_sx = 1.0   # horizontal scale for flip (-1 reverses)
        self._update_size()
        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        # Timer is NOT started by default; set_animated(True) starts it.

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Change the animation mode and reset all motion state."""
        if mode not in self._VALID_MODES:
            mode = "spin"
        self._mode = mode
        self._angle = 0.0
        self._phase = 0.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._scale = 1.0
        self._flip_sx = 1.0
        self._update_size()
        self.update()

    def set_emoji(self, emoji: str) -> None:
        """Change the displayed emoji; takes effect on the next paint."""
        self._emoji = emoji
        self.update()

    def set_animated(self, enabled: bool) -> None:
        """Start or stop the animation timer.

        In "static" mode the timer is never started even when *enabled* is
        True — the emoji is always rendered without motion (the caller may
        activate an external flock effect instead).
        """
        if enabled and self._mode != "static":
            if not self._timer.isActive():
                self._timer.start()
        else:
            if self._timer.isActive():
                self._timer.stop()
            self._angle = 0.0
            self._phase = 0.0
            self._offset_x = 0.0
            self._offset_y = 0.0
            self._scale = 1.0
            self._flip_sx = 1.0
            self.update()

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        self._update_size()
        self.update()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_size(self) -> None:
        """Resize the widget to fit the emoji plus any animation headroom."""
        base = self._font_size + 16
        if self._mode == "bounce":
            self.setFixedSize(base, base + self._BOUNCE_AMPLITUDE * 2)
        elif self._mode == "shake":
            self.setFixedSize(base + self._SHAKE_AMPLITUDE * 2, base)
        elif self._mode == "float":
            self.setFixedSize(base, base + self._FLOAT_AMPLITUDE * 2)
        elif self._mode == "pulse":
            extra = int(base * (self._PULSE_MAX_SCALE - 1.0)) + 2
            self.setFixedSize(base + extra * 2, base + extra * 2)
        elif self._mode == "orbit":
            pad = self._ORBIT_RADIUS + 2
            self.setFixedSize(base + pad * 2, base + pad * 2)
        elif self._mode == "glitch":
            pad = 9  # max jitter (7 px) + 2 px safety margin
            self.setFixedSize(base + pad * 2, base + pad * 2)
        elif self._mode == "drip":
            # Extra vertical space for the fall distance + room to shrink
            self.setFixedSize(base + 8, base + self._DRIP_FALL_PX + 4)
        else:
            self.setFixedSize(base, base)

    def _tick(self) -> None:
        mode = self._mode
        if mode == "spin":
            self._angle = (self._angle + self._SPIN_DEG_PER_FRAME) % 360.0
        elif mode == "bounce":
            self._phase = (self._phase + self._BOUNCE_STEP) % (2 * math.pi)
            self._offset_y = math.sin(self._phase) * self._BOUNCE_AMPLITUDE
        elif mode == "shake":
            self._phase = (self._phase + self._SHAKE_STEP) % (2 * math.pi)
            self._offset_x = math.sin(self._phase) * self._SHAKE_AMPLITUDE
        elif mode == "pendulum":
            self._phase = (self._phase + self._PENDULUM_STEP) % (2 * math.pi)
            self._angle = math.sin(self._phase) * self._PENDULUM_MAX_ANGLE
        elif mode == "pulse":
            self._phase = (self._phase + self._PULSE_STEP) % (2 * math.pi)
            # Oscillate between _PULSE_MIN_SCALE and _PULSE_MAX_SCALE
            mid = (self._PULSE_MAX_SCALE + self._PULSE_MIN_SCALE) / 2.0
            amp = (self._PULSE_MAX_SCALE - self._PULSE_MIN_SCALE) / 2.0
            self._scale = mid + math.sin(self._phase) * amp
        elif mode == "float":
            self._phase = (self._phase + self._FLOAT_STEP) % (2 * math.pi)
            self._offset_y = math.sin(self._phase) * self._FLOAT_AMPLITUDE
        elif mode == "flip":
            self._phase = (self._phase + self._FLIP_STEP) % (2 * math.pi)
            # abs(cos) collapses to 0 then recovers – looks like a flip
            self._flip_sx = abs(math.cos(self._phase))
        elif mode == "orbit":
            # Emoji orbits in a small circle around the centre of the widget
            self._phase = (self._phase + self._ORBIT_STEP) % (2 * math.pi)
            self._offset_x = math.cos(self._phase) * self._ORBIT_RADIUS
            self._offset_y = math.sin(self._phase) * self._ORBIT_RADIUS
        elif self._mode == "drip":
            # Slowly fall downward and shrink, then reset to top at full size
            self._phase = (self._phase + self._DRIP_STEP) % 1.0
            t = self._phase  # 0..1 progress through the fall
            # Add a gentle left-right wobble as it falls (like a teardrop)
            self._offset_x = math.sin(t * math.pi * 4) * 2.0
            self._offset_y = t * self._DRIP_FALL_PX
            # Shrink from 1.0 to 0.5 as it falls
            self._scale = 1.0 - t * 0.5
            # Jitter resets to zero periodically so it "snaps" back to centre
            if int(self._phase * 4) % 3 == 0:
                self._offset_x = 0.0
                self._offset_y = 0.0
            else:
                amp = 4 + 3 * abs(math.sin(self._phase * 3))
                self._offset_x = random.uniform(-amp, amp)
                self._offset_y = random.uniform(-amp, amp)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.width(), self.height()
        # Translate to widget centre plus any per-mode positional offset.
        painter.translate(w / 2.0 + self._offset_x, h / 2.0 + self._offset_y)
        # Apply rotation for spin / pendulum modes.
        if self._angle != 0.0:
            painter.rotate(self._angle)
        # Apply scale for pulse mode.
        if self._mode == "pulse" and self._scale != 1.0:
            painter.scale(self._scale, self._scale)
        # Apply horizontal squeeze for flip mode.
        elif self._mode == "flip":
            sx = max(self._flip_sx, 0.01)  # avoid degenerate zero-width transform
            painter.scale(sx, 1.0)
        # Apply scale-down for drip mode.
        elif self._mode == "drip" and self._scale != 1.0:
            s = max(self._scale, 0.01)
            painter.scale(s, s)

        font = QFont()
        font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"])
        font.setPointSize(self._font_size)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(self._emoji)
        th = fm.height()
        painter.drawText(-tw // 2, th // 4, self._emoji)
        painter.end()


# ---------------------------------------------------------------------------
# Easter-egg helpers
# ---------------------------------------------------------------------------

class _EasterCollectible(QLabel):
    """A floating emoji collectible that appears when a secret spot is triggered.

    The label bobs gently while waiting.  Clicking it calls back into
    MainWindow to perform the theme unlock, play the fanfare, and hide this
    widget.
    """

    collected = pyqtSignal()

    # vertical bobbing parameters
    _BOB_STEP_RAD = 0.15
    _BOB_AMP_PX   = 5
    _INTERVAL_MS  = 40

    def __init__(self, emoji: str, tip: str, parent: QWidget):
        super().__init__(emoji, parent)
        font = QFont()
        font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"])
        font.setPointSize(26)
        self.setFont(font)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(52, 52)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "background: rgba(0,0,0,120); border-radius: 8px; border: 2px solid #ffcc00;"
        )
        self._phase = 0.0
        self._base_y = 0
        self._bob_timer = QTimer(self)
        self._bob_timer.setInterval(self._INTERVAL_MS)
        self._bob_timer.timeout.connect(self._bob_tick)
        self.hide()

    def show_at(self, win_pos: QPoint) -> None:
        """Show the collectible centred on *win_pos* in parent coordinates."""
        x = win_pos.x() - self.width() // 2
        y = win_pos.y() - self.height() // 2
        self._base_y = y
        self._phase = 0.0
        self.move(x, y)
        self.show()
        self.raise_()
        self._bob_timer.start()

    def dismiss(self) -> None:
        """Stop animation and hide without emitting *collected*."""
        self._bob_timer.stop()
        self.hide()

    def _bob_tick(self) -> None:
        self._phase = (self._phase + self._BOB_STEP_RAD) % (2 * math.pi)
        dy = int(self._BOB_AMP_PX * math.sin(self._phase))
        self.move(self.x(), self._base_y + dy)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._bob_timer.stop()
            self.hide()
            self.collected.emit()
        super().mousePressEvent(event)


class _EasterClickFilter(QObject):
    """Event filter that counts left-clicks on a watched widget.

    When the click count reaches *threshold*, ``triggered`` is emitted with
    the global position of the click.
    """

    triggered = pyqtSignal(QPoint)

    def __init__(self, threshold: int, parent: QObject = None):
        super().__init__(parent)
        self._threshold = threshold
        self._count = 0

    def reset(self) -> None:
        self._count = 0

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._count += 1
                if self._count >= self._threshold:
                    self._count = 0
                    # Map the local click to global coords
                    try:
                        gpos = obj.mapToGlobal(event.position().toPoint())
                    except Exception:
                        gpos = QPoint(0, 0)
                    self.triggered.emit(gpos)
        return False  # never consume the event


class _KeySecretFilter(QObject):
    """Application-level event filter that watches for secret key sequences.

    Installs on ``QApplication.instance()`` so it captures keyboard events
    from every widget.  Emits ``triggered(name)`` when a full sequence is
    matched, where *name* is the sequence identifier used to look up unlock
    data in ``MainWindow._KEY_SECRETS``.

    All matching is case-insensitive for letter keys; modifier keys (Shift,
    Ctrl, Alt, Meta) are ignored so ordinary typing that happens to end in a
    secret word doesn't accidentally fire (arrow-key and function-key
    sequences can't be produced accidentally by typing).
    """

    triggered = pyqtSignal(str)  # sequence name

    # Maximum key-buffer size: longest sequence + small slack
    _MAX_BUF = 14

    def __init__(self, sequences: dict, parent: QObject = None):
        super().__init__(parent)
        # sequences: {name: (key_int, ...)}
        self._sequences = sequences
        self._buf: list[int] = []

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event.key()
        # Normalise letter keys to uppercase so typing produces consistent
        # values regardless of Shift/caps-lock state.
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            pass  # already stored as uppercase Qt.Key values
        elif key in (
            Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
            Qt.Key.Key_Return, Qt.Key.Key_Enter,
        ):
            pass  # navigation keys – allowed as-is
        else:
            return False  # ignore digits, punctuation, function keys, etc.

        self._buf.append(key)
        if len(self._buf) > self._MAX_BUF:
            self._buf.pop(0)

        # Check each registered sequence against the tail of the buffer.
        for name, seq in self._sequences.items():
            slen = len(seq)
            if slen == 0:
                continue
            if tuple(self._buf[-slen:]) == seq:
                self._buf.clear()
                try:
                    self.triggered.emit(name)
                except RuntimeError:
                    pass
                break  # only fire one sequence per key event
        return False  # never consume the event


class MainWindow(QMainWindow):
    # Unlock table: (click_threshold, settings_key, banner_message).
    # Stored at class level so it is built once, not rebuilt on every click.
    _UNLOCK_TABLE = [
        (100,  "unlock_skeleton",        "🔓 'Secret Skeleton' theme unlocked! (Settings → Theme)"),
        (150,  "unlock_ice_cave",         "❄ 'Ice Cave' theme unlocked! (Settings → Theme)"),
        (200,  "unlock_cyber_otter",      "🦦 'Cyber Otter' theme unlocked! (Settings → Theme)"),
        (250,  "unlock_sakura",           "🌸 'Secret Sakura' theme unlocked! (Settings → Theme)"),
        (350,  "unlock_toxic_neon",       "☢ 'Toxic Neon' theme unlocked! (Settings → Theme)"),
        (400,  "unlock_sunset_beach",     "🌅 'Sunset Beach' theme unlocked! (Settings → Theme)"),
        (500,  "unlock_ocean",            "🌊 'Deep Ocean' theme unlocked! (Settings → Theme)"),
        (600,  "unlock_lava_cave",        "🌋 'Lava Cave' theme unlocked! (Settings → Theme)"),
        (750,  "unlock_blood_moon",       "🩸 'Blood Moon' theme unlocked! (Settings → Theme)"),
        (1000, "unlock_midnight_forest",  "🌲 'Midnight Forest' theme unlocked! (Settings → Theme)"),
        (1250, "unlock_candy_land",       "🍭 'Candy Land' theme unlocked! (Settings → Theme)"),
        (1500, "unlock_zombie",           "🧟 'Zombie Apocalypse' theme unlocked! (Settings → Theme)"),
        (1750, "unlock_dragon_fire",      "🐉 'Dragon Fire' theme unlocked! (Settings → Theme)"),
        (2000, "unlock_bubblegum",        "🫧 'Bubblegum' theme unlocked! (Settings → Theme)"),
        (2250, "unlock_thunder_storm",    "⚡ 'Thunder Storm' theme unlocked! (Settings → Theme)"),
        (2500, "unlock_rose_gold",        "🌹 'Rose Gold' theme unlocked! (Settings → Theme)"),
        (2750, "unlock_space_cat",        "🐱 'Space Cat' theme unlocked! (Settings → Theme)"),
        (3000, "unlock_magic_mushroom",   "🍄 'Magic Mushroom' theme unlocked! (Settings → Theme)"),
        (3500, "unlock_abyssal_void",     "🕳 'Abyssal Void' theme unlocked! (Settings → Theme)"),
        (4000, "unlock_spring_bloom",     "🌷 'Spring Bloom' theme unlocked! (Settings → Theme)"),
        (4500, "unlock_gold_rush",        "💰 'Gold Rush' theme unlocked! (Settings → Theme)"),
        (5000, "unlock_nebula",           "🌌 'Nebula' theme unlocked! (Settings → Theme)"),
        (5500, "unlock_crystal_cave",     "💎 'Crystal Cave' theme unlocked! (Settings → Theme)"),
        (6000, "unlock_glitch",           "📡 'Glitch' theme unlocked! (Settings → Theme)"),
        (6500, "unlock_wild_west",        "🤠 'Wild West' theme unlocked! (Settings → Theme)"),
        (7000, "unlock_pirate",           "🏴‍☠️ 'Pirate' theme unlocked! (Settings → Theme)"),
        (7500, "unlock_deep_space",       "🛸 'Deep Space' theme unlocked! (Settings → Theme)"),
        (8000, "unlock_witchs_brew",      "🧙 'Witch's Brew' theme unlocked! (Settings → Theme)"),
        (8500, "unlock_lava_lamp",        "🪔 'Lava Lamp' theme unlocked! (Settings → Theme)"),
        (9000, "unlock_coral_reef",       "🪸 'Coral Reef' theme unlocked! (Settings → Theme)"),
        (9500, "unlock_storm_cloud",      "⛈ 'Storm Cloud' theme unlocked! (Settings → Theme)"),
        (10000, "unlock_golden_hour",     "🌇 'Golden Hour' theme unlocked! (Settings → Theme)"),
        (10500, "unlock_cat",             "🐱 'Purrfect Cats' theme unlocked! (Settings → Theme)"),
        (11000, "unlock_dog",             "🐶 'Good Dog' theme unlocked! (Settings → Theme)"),
        (11500, "unlock_snake",           "🐍 'Snake Pit' theme unlocked! (Settings → Theme)"),
        (12000, "unlock_ghost",           "👻 'Ghost' theme unlocked! (Settings → Theme)"),
        (12500, "unlock_slime",           "🟢 'Slime' theme unlocked! (Settings → Theme)"),
        (13000, "unlock_anime",           "🌸 'Anime' theme unlocked! (Settings → Theme)"),
        (13500, "unlock_waifu",           "💖 'Waifu' theme unlocked! (Settings → Theme)"),
    ]

    # Alternative unlock path: number of *alpha-fix files processed* required.
    # Uses the same settings keys as _UNLOCK_TABLE so the first path to fire wins
    # and no duplicate notification is shown.
    _ALPHA_MILESTONES = [
        (10,   "unlock_ice_cave",     "❄ 'Ice Cave' theme unlocked! (10 alpha fixes done)"),
        (50,   "unlock_ocean",        "🌊 'Deep Ocean' theme unlocked! (50 alpha fixes done)"),
        (250,  "unlock_midnight_forest", "🌲 'Midnight Forest' theme unlocked! (250 alpha fixes done)"),
        (1000, "unlock_nebula",       "🌌 'Nebula' theme unlocked! (1 000 alpha fixes done)"),
        (2500, "unlock_ghost",        "👻 'Ghost' theme unlocked! (2 500 alpha fixes done)"),
        (5000, "unlock_golden_hour",  "🌇 'Golden Hour' theme unlocked! (5 000 alpha fixes done)"),
    ]

    # Alternative unlock path: number of *converted files* required.
    _CONV_MILESTONES = [
        (10,   "unlock_blood_moon",   "🩸 'Blood Moon' theme unlocked! (10 conversions done)"),
        (50,   "unlock_dragon_fire",  "🐉 'Dragon Fire' theme unlocked! (50 conversions done)"),
        (250,  "unlock_spring_bloom", "🌷 'Spring Bloom' theme unlocked! (250 conversions done)"),
        (1000, "unlock_crystal_cave", "💎 'Crystal Cave' theme unlocked! (1 000 conversions done)"),
        (2500, "unlock_slime",        "🟢 'Slime' theme unlocked! (2 500 conversions done)"),
        (5000, "unlock_coral_reef",   "🪸 'Coral Reef' theme unlocked! (5 000 conversions done)"),
    ]

    # -----------------------------------------------------------------------
    # Easter-egg spots: (spot_id, click_threshold, unlock_key, collectible_emoji,
    #                    collectible_tip, unlock_banner_msg)
    # spot_id is used as a key in _easter_filters and _easter_collectibles.
    # -----------------------------------------------------------------------
    _EASTER_SPOTS = [
        # Left banner panda — click 7× to summon a shark 🦈 → unlocks Deep Ocean
        ("egg_banner_left",  7,
         "unlock_ocean",
         "🦈",
         "✨ Secret found!  Click the shark to unlock Deep Ocean theme!",
         "🌊 'Deep Ocean' theme unlocked via secret easter egg!"),
        # Right banner panda — click 7× to summon a mermaid 🧜 → unlocks Midnight Forest
        ("egg_banner_right", 7,
         "unlock_midnight_forest",
         "🧜",
         "✨ Secret found!  Click the mermaid to unlock Midnight Forest theme!",
         "🌙 'Midnight Forest' theme unlocked via secret easter egg!"),
        # Help button — click 5× to summon a mushroom 🍄 → unlocks Magic Mushroom
        ("egg_help_btn",     5,
         "unlock_magic_mushroom",
         "🍄",
         "✨ Secret found!  Click the mushroom to unlock Magic Mushroom theme!",
         "🍄 'Magic Mushroom' theme unlocked via secret easter egg!"),
        # History tab header — click 6× to summon an otter 🦦 → unlocks Cyber Otter
        ("egg_history_tab",  6,
         "unlock_cyber_otter",
         "🦦",
         "✨ Secret found!  Click the otter to unlock Cyber Otter theme!",
         "🦦 'Cyber Otter' theme unlocked via secret easter egg!"),
        # Status bar — click 5× to summon a ghost 👻 → unlocks Ghost
        ("egg_status_bar",   5,
         "unlock_ghost",
         "👻",
         "✨ Secret found!  Click the ghost to unlock Ghost theme!",
         "👻 'Ghost' theme unlocked via secret easter egg!"),
        # Patreon button — click 8× to summon a pirate ☠ → unlocks Pirate
        ("egg_patreon_btn",  8,
         "unlock_pirate",
         "☠",
         "✨ Secret found!  Click the skull to unlock Pirate theme!",
         "🏴‍☠️ 'Pirate' theme unlocked via secret easter egg!"),
        # Converter tab header — click 9× to summon a UFO 🛸 → unlocks Alien
        ("egg_converter_tab", 9,
         "unlock_alien",
         "🛸",
         "🛸 Secret found!  Click the UFO to unlock Alien theme!",
         "👽 'Alien' theme unlocked via secret easter egg!"),
        # Alpha tab header — click 10× to summon a shark 🦈 → unlocks Shark Bait
        ("egg_alpha_tab",    10,
         "unlock_shark_bait",
         "🦈",
         "🦈 Secret found!  Click the shark to unlock Shark Bait theme!",
         "🦈 'Shark Bait' theme unlocked via secret easter egg!"),
        # Selective Alpha tab — click 8× to summon a noodle 🍜 → unlocks Noodle
        ("egg_selective_tab", 8,
         "unlock_noodle",
         "🍜",
         "🍜 Secret found!  Click the noodle to unlock Noodle theme!",
         "🍜 'Noodle' theme unlocked via secret easter egg!"),
        # Settings button — click 9× to summon a cowboy 🤠 → unlocks Wild West
        ("egg_settings_btn", 9,
         "unlock_wild_west",
         "🤠",
         "🤠 Secret found!  Click the cowboy to unlock Wild West theme!",
         "🤠 'Wild West' theme unlocked via secret easter egg!"),
        # SVG theme badge — click 7× to summon a rose 🌹 → unlocks Rose Gold
        ("egg_svg_badge", 7,
         "unlock_rose_gold",
         "🌹",
         "🌹 Secret found!  Click the rose to unlock Rose Gold theme!",
         "🌹 'Rose Gold' theme unlocked via secret easter egg!"),
        # Theme label — click 11× to summon a lightning bolt ⚡ → unlocks Thunder Storm
        ("egg_theme_label", 11,
         "unlock_thunder_storm",
         "⚡",
         "⚡ Secret found!  Click the bolt to unlock Thunder Storm theme!",
         "⚡ 'Thunder Storm' theme unlocked via secret easter egg!"),
        # Unlock status label — click 6× to summon a witch 🧙 → unlocks Witch's Brew
        ("egg_unlock_lbl", 6,
         "unlock_witchs_brew",
         "🧙",
         "🧙 Secret found!  Click the witch to unlock Witch's Brew theme!",
         "🧙 'Witch\\'s Brew' theme unlocked via secret easter egg!"),
        # Center banner text — click 8× to summon a sakura blossom 🌸 → unlocks Anime
        ("egg_banner_text", 8,
         "unlock_anime",
         "🌸",
         "🌸 Secret found!  Click the sakura to unlock Anime theme!",
         "🌸 'Anime' theme unlocked via secret easter egg!"),
        # Tab bar header row — click 9× to summon a heart 💖 → unlocks Waifu
        ("egg_tab_bar", 9,
         "unlock_waifu",
         "💖",
         "💖 Secret found!  Click the heart to unlock Waifu theme!",
         "💖 'Waifu' theme unlocked via secret easter egg!"),
    ]

    # -----------------------------------------------------------------------
    # Secret key sequences: typing these anywhere in the app unlocks a theme.
    # Each entry is a tuple of Qt.Key integer values.  Letter sequences use
    # the uppercase Key_A–Key_Z constants regardless of Shift/caps state.
    # -----------------------------------------------------------------------
    @classmethod
    def _build_key_secrets(cls) -> dict:
        """Build the _KEY_SECRETS dict deferred until Qt is initialised."""
        K = Qt.Key

        def _word_seq(s: str) -> tuple:
            """Return a tuple of Qt.Key values for each uppercase letter in s."""
            return tuple(getattr(K, f"Key_{c}") for c in s.upper())

        return {
            # Konami code: ↑↑↓↓←→←→BA
            "konami": {
                "seq": (
                    K.Key_Up, K.Key_Up, K.Key_Down, K.Key_Down,
                    K.Key_Left, K.Key_Right, K.Key_Left, K.Key_Right,
                    K.Key_B, K.Key_A,
                ),
                "unlock_key":  "unlock_glitch",
                "emoji":       "🎮",
                "collectible_tip": "🎮 KONAMI CODE! Click to unlock Glitch theme!",
                "banner_msg":  "🎮 KONAMI CODE! 🕹 'Glitch' theme unlocked!",
            },
            # Secret word DRAGON → Dragon Fire
            "dragon": {
                "seq": _word_seq("DRAGON"),
                "unlock_key":  "unlock_dragon_fire",
                "emoji":       "🐉",
                "collectible_tip": "🐉 Secret word found! Click to unlock Dragon Fire!",
                "banner_msg":  "🐉 Secret word: DRAGON! 'Dragon Fire' theme unlocked!",
            },
            # Secret word CANDY → Candy Land
            "candy": {
                "seq": _word_seq("CANDY"),
                "unlock_key":  "unlock_candy_land",
                "emoji":       "🍬",
                "collectible_tip": "🍬 Secret word found! Click to unlock Candy Land!",
                "banner_msg":  "🍬 Secret word: CANDY! 'Candy Land' theme unlocked!",
            },
            # Secret word ZOMBIE → Zombie Apocalypse
            "zombie": {
                "seq": _word_seq("ZOMBIE"),
                "unlock_key":  "unlock_zombie",
                "emoji":       "🧟",
                "collectible_tip": "🧟 Secret word found! Click to unlock Zombie Apocalypse!",
                "banner_msg":  "🧟 Secret word: ZOMBIE! 'Zombie Apocalypse' theme unlocked!",
            },
            # Secret word CORAL → Coral Reef
            "coral": {
                "seq": _word_seq("CORAL"),
                "unlock_key":  "unlock_coral_reef",
                "emoji":       "🪸",
                "collectible_tip": "🪸 Secret word found! Click to unlock Coral Reef!",
                "banner_msg":  "🪸 Secret word: CORAL! 'Coral Reef' theme unlocked!",
            },
            # Secret word ABYSS → Abyssal Void
            "abyss": {
                "seq": _word_seq("ABYSS"),
                "unlock_key":  "unlock_abyssal_void",
                "emoji":       "🕳",
                "collectible_tip": "🕳 Secret word found! Click to unlock Abyssal Void!",
                "banner_msg":  "🕳 Secret word: ABYSS! 'Abyssal Void' theme unlocked!",
            },
        }

    def __init__(self, settings: SettingsManager):
        super().__init__()
        self._settings = settings
        self._preset_mgr = PresetManager(settings)
        self._trail_overlay = None
        self._click_effects = None
        self._button_anim = None
        self._tooltip_mgr = None
        self._sound = None
        self._svg_badge = None
        self._banner_lbl = None
        self._banner_emoji_left: "_SpinningEmojiLabel | None" = None
        self._banner_emoji_right: "_SpinningEmojiLabel | None" = None
        self._toolbar_panda_lbl: "QLabel | None" = None
        self._status_bar = None
        self._unlock_timer = None
        self._anim_timer = None    # kept for compatibility (no longer used for cycling)
        # Cursor animation state
        self._cursor_anim_timer: "QTimer | None" = None
        self._cursor_anim_frames: list[str] = []  # current animation sequence (text cycling)
        self._cursor_spin_cursors: list = []       # pre-rendered QCursor frames (spin or wobble)
        self._cursor_spin_emoji: str = ""          # which emoji is currently spin/wobble-animated
        self._cursor_anim_idx: int = 0            # index of next frame to show
        self._banner_frames: list[str] = []
        self._banner_frame_idx: int = 0
        self._tab_base_labels: tuple = ()   # set during first _apply_theme()
        # Debounce timer: collapses rapid settings_changed signals into a
        # single re-apply call so slider drags / spinbox scrolling don't
        # trigger dozens of expensive setStyleSheet() calls per second.
        self._settings_apply_timer = QTimer(self)
        self._settings_apply_timer.setSingleShot(True)
        self._settings_apply_timer.setInterval(300)
        self._settings_apply_timer.timeout.connect(self._apply_settings_now)
        # Cache last applied stylesheet to avoid redundant setStyleSheet calls
        self._last_stylesheet: str = ""
        # Easter-egg state: per-spot event filters and collectible widgets
        self._easter_filters: dict[str, _EasterClickFilter] = {}
        self._easter_collectibles: dict[str, _EasterCollectible] = {}
        # Key-secret collectibles: shown when a key-sequence unlock fires
        self._key_secret_collectibles: dict[str, _EasterCollectible] = {}
        self._key_secret_filter: _KeySecretFilter | None = None
        # Resize debounce timer: window resize fires very rapidly during an
        # interactive drag.  Repositioning the overlays on every pixel update
        # is wasteful; coalesce them into a single update 50ms after the last
        # resize event to keep the UI responsive during dragging.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._reposition_overlays)
        self._setup_window()
        self._setup_ui()
        self._restore_geometry()
        self._apply_theme()
        self._setup_effects()
        self._setup_easter_eggs()
        self._setup_key_secrets()
        # Connect to screen-topology and DPI-change signals so the window
        # geometry stays valid when the user plugs in / removes a monitor or
        # changes the system display-scale setting.
        app = QApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screens_changed)
            app.screenRemoved.connect(self._on_screens_changed)
            app.primaryScreenChanged.connect(self._on_screens_changed)

    # ------------------------------------------------------------------
    # Window setup / minimum-size helpers (screen-adaptive)
    # ------------------------------------------------------------------

    def _update_minimum_size(self) -> None:
        """Recompute and apply the window's minimum size based on the current
        screen's available geometry.

        The cap of 900×700 is the design-target minimum.  On displays where
        the available area is smaller (e.g. 1280×720 laptops with a taskbar)
        we shrink the minimum proportionally so the window can still be shown
        without the OS forcing it to overflow the working area.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            ag = screen.availableGeometry()
            # Use at most 75 % of the available width/height so there is
            # meaningful margin on common laptop screens (e.g. 1366×768).
            # The caps of 900×700 are the design-target minimums; the floors
            # of 640×520 ensure the UI is still usable on very small displays.
            min_w = min(900, max(640, int(ag.width()  * 0.75)))
            min_h = min(700, max(520, int(ag.height() * 0.75)))
        else:
            min_w, min_h = 900, 700
        self.setMinimumSize(min_w, min_h)

    def _setup_window(self):
        self.setWindowTitle(f"🐼 Alpha & RGBA Adjuster  |  File Converter  v{__version__}")
        self._update_minimum_size()
        # Enable whole-window drag-and-drop (item 28).
        self.setAcceptDrops(True)
        # Set the panda SVG as the window / taskbar icon (initial default).
        # Prefer the pre-generated multi-size ICO (embedded by PyInstaller)
        # which contains all shell sizes (16 → 256 px) for crisp display at
        # every zoom level.  Falls back to rendering the SVG directly when the
        # ICO is not present (e.g. running from source without running
        # scripts/make_icon.py first).
        self._set_panda_window_icon()

    @staticmethod
    def _render_svg_to_icon(svg_path: str) -> "QIcon | None":
        """Render *svg_path* at multiple resolutions and return a QIcon.

        Provides 16, 24, 32, 48, 64, 128 and 256 px variants so Qt always has
        a sharp pixmap for the title bar (16 px), taskbar (32/40/48 px) and
        the jump-list thumbnail (256 px) on every platform and DPI setting.
        Returns *None* if QtSvg is not available.
        """
        try:
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(svg_path)
            icon = QIcon()
            for size in (16, 24, 32, 40, 48, 64, 128, 256):
                pix = QPixmap(size, size)
                pix.fill(Qt.GlobalColor.transparent)
                p = QPainter(pix)
                if p.isActive():
                    renderer.render(p)
                    p.end()
                    icon.addPixmap(pix)
            return icon if not icon.isNull() else None
        except (ImportError, Exception):
            return None

    def _set_panda_window_icon(self):
        """Set the initial window / taskbar icon to the panda theme graphic."""
        import os
        assets_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "assets")
        )
        # 1. Prefer the pre-generated multi-size ICO (ships with the repo).
        ico_path = os.path.join(assets_dir, "icon.ico")
        if os.path.isfile(ico_path):
            icon = QIcon(ico_path)
            if not icon.isNull():
                self.setWindowIcon(icon)
                QApplication.setWindowIcon(icon)
                return
        # 2. Fall back to rendering the panda SVG at multiple sizes.
        svg_dir = os.path.join(assets_dir, "svg")
        for candidate in ("panda_dark.svg", "panda_light.svg"):
            svg_path = os.path.normpath(os.path.join(svg_dir, candidate))
            if os.path.isfile(svg_path):
                icon = self._render_svg_to_icon(svg_path)
                if icon is not None:
                    self.setWindowIcon(icon)
                    QApplication.setWindowIcon(icon)
                return

    def _setup_ui(self):
        # Keep keyboard shortcuts that were previously tied to menu actions
        from PyQt6.QtGui import QShortcut
        sc_quit = QShortcut(QKeySequence("Ctrl+Q"), self)
        sc_quit.activated.connect(self.close)
        sc_settings = QShortcut(QKeySequence("Ctrl+,"), self)
        sc_settings.activated.connect(self._open_settings)
        sc_help = QShortcut(QKeySequence("F1"), self)
        sc_help.activated.connect(self._show_shortcuts)

        # Central widget with tabs
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # Animated banner: a left+right spinning emoji flanks the static title text.
        # The emoji rotates continuously (like a turning gear) using _SpinningEmojiLabel.
        # The emoji changes to reflect the active theme without cycling between emojis.
        banner_container = QWidget()
        banner_container.setObjectName("header")
        banner_layout = QHBoxLayout(banner_container)
        banner_layout.setContentsMargins(8, 6, 8, 6)
        banner_layout.setSpacing(8)

        self._banner_emoji_left = _SpinningEmojiLabel("🐼", font_size=20)
        banner_layout.addWidget(self._banner_emoji_left)

        banner_text = QLabel("Alpha & RGBA Adjuster  |  File Converter")
        banner_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner_text.setObjectName("header")
        banner_text.setStyleSheet("padding: 0; font-size: 20px; background: transparent; border: none;")
        banner_text.setMinimumHeight(36)
        banner_layout.addWidget(banner_text, 1)

        self._banner_emoji_right = _SpinningEmojiLabel("🐼", font_size=20)
        banner_layout.addWidget(self._banner_emoji_right)

        cv.addWidget(banner_container)
        self._banner_lbl = banner_text  # kept for theme update compatibility

        self._tabs = QTabWidget()
        self._tabs.setUsesScrollButtons(True)
        self._tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        # Prevent tabs from stretching to fill available space; scroll buttons
        # will appear automatically when the window is narrower than all tabs.
        self._tabs.tabBar().setExpanding(False)
        # Allow the user to drag tabs to reorder them.
        self._tabs.tabBar().setMovable(True)
        self._alpha_tab = AlphaFixerTab(self._preset_mgr, self._settings)
        self._converter_tab = ConverterTab(self._settings)
        self._history_tab = HistoryTab(self._settings)
        self._selective_alpha_tab = SelectiveAlphaTool(self._settings)
        self._tabs.addTab(self._alpha_tab, "🖼  Alpha & RGBA Adjuster")
        self._tabs.addTab(self._converter_tab, "🔄  Converter")
        self._tabs.addTab(self._history_tab, "📋  History")
        self._tabs.addTab(self._selective_alpha_tab, "🎨  Selective Alpha")
        # Refresh history whenever the user switches to it
        self._tabs.currentChanged.connect(self._on_tab_changed)
        cv.addWidget(self._tabs, 1)

        # Keyboard shortcuts for tab switching: Ctrl+1/2/3/4
        for idx, key in enumerate(("Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4")):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(lambda i=idx: self._tabs.setCurrentIndex(i))

        # Corner widget: Settings / Help / Patreon buttons on the right of the tab bar.
        # This puts all tool controls in one row, freeing vertical space for content.
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(2, 2, 6, 2)
        corner_layout.setSpacing(4)

        # Unlock status label (shown briefly when a secret theme unlocks)
        self._unlock_lbl = QLabel("")
        self._unlock_lbl.setObjectName("subheader")
        self._unlock_lbl.setStyleSheet("color: #ffcc00; padding: 0 6px;")
        corner_layout.addWidget(self._unlock_lbl)

        # Current theme label
        self._theme_label = QLabel("  Theme: Panda Dark  ")
        self._theme_label.setObjectName("subheader")
        corner_layout.addWidget(self._theme_label)

        # SVG theme badge (decorative – shows animated SVG for the active theme)
        self._svg_badge = self._make_svg_badge()
        if self._svg_badge is not None:
            corner_layout.addWidget(self._svg_badge)

        # ⚙ Settings button
        btn_settings = QPushButton("⚙  Settings")
        btn_settings.setMinimumWidth(110)
        btn_settings.setMinimumHeight(28)
        btn_settings.setToolTip("Open Settings (Ctrl+,)")
        btn_settings.clicked.connect(self._open_settings)
        corner_layout.addWidget(btn_settings)
        self._btn_settings = btn_settings

        # ❓ Help button – opens a dropdown with shortcuts/about/export/import
        btn_help = QPushButton("❓  Help")
        btn_help.setMinimumWidth(90)
        btn_help.setMinimumHeight(28)
        btn_help.setToolTip("Keyboard shortcuts, About, Export/Import settings")
        btn_help.clicked.connect(self._show_help_menu)
        corner_layout.addWidget(btn_help)
        self._btn_help = btn_help

        # ❤ Patreon button
        btn_patreon = QPushButton("❤  Patreon")
        btn_patreon.setMinimumWidth(110)
        btn_patreon.setMinimumHeight(28)
        btn_patreon.setToolTip(
            "Support development on Patreon!\n"
            "patreon.com/c/DeadOnTheInside"
        )
        btn_patreon.clicked.connect(self._open_patreon)
        corner_layout.addWidget(btn_patreon)
        self._btn_patreon = btn_patreon

        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        self.setCentralWidget(central)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready  🐼")

        # Toolbar panda label no longer used (toolbar removed); keep None so
        # _refresh_toolbar_icon() early-returns without errors.
        self._toolbar_panda_lbl = None

    # ------------------------------------------------------------------
    # Visual / audio effects (trail, cursor, sound, click effects, tooltips)
    # ------------------------------------------------------------------

    def _setup_effects(self):
        # Mouse trail overlay
        from .mouse_trail import MouseTrailOverlay
        self._trail_overlay = MouseTrailOverlay(self)
        self._trail_overlay.setGeometry(self.rect())
        self._trail_overlay.raise_()
        self._apply_trail()

        # Click effects overlay
        from .click_effects import ClickEffectsOverlay
        self._click_effects = ClickEffectsOverlay(self)
        self._click_effects.setGeometry(self.rect())
        self._click_effects.raise_()
        effects_enabled = self._settings.get("click_effects_enabled", False)
        self._click_effects.set_enabled(effects_enabled)
        self._click_effects.click_registered.connect(self._check_unlocks)
        self._apply_theme_effect()

        # Button press animator
        from .click_effects import ButtonPressAnimator
        self._button_anim = ButtonPressAnimator(self, self._click_effects)
        self._apply_button_anim()
        self._apply_bg_drip()
        self._apply_bg_flock()
        self._apply_bg_ambient()

        # Connect processing-done signals so file processing can unlock themes
        self._alpha_tab.processing_done.connect(self._on_processing_done)
        self._converter_tab.processing_done.connect(self._on_processing_done)
        # Additional connections for per-tool milestone unlocks
        self._alpha_tab.processing_done.connect(self._on_alpha_processing_done)
        self._converter_tab.processing_done.connect(self._on_conv_processing_done)
        # Processing-error sounds
        self._alpha_tab.processing_error.connect(self._on_processing_error)
        self._converter_tab.processing_error.connect(self._on_processing_error)
        # Processing-started sounds
        self._alpha_tab.processing_started.connect(self._on_processing_started)
        self._converter_tab.processing_started.connect(self._on_processing_started)
        # First-use unlock triggers
        self._alpha_tab.first_alpha_fix.connect(self._on_first_alpha_fix)
        self._converter_tab.first_conversion.connect(self._on_first_conversion)
        # File-add sounds
        self._alpha_tab.files_added.connect(self._on_files_added)
        self._converter_tab.files_added.connect(self._on_files_added)
        # File-remove sounds
        self._alpha_tab.files_removed.connect(self._on_files_removed)
        self._converter_tab.files_removed.connect(self._on_files_removed)
        # List-cleared sound (dog bark)
        self._alpha_tab.list_cleared.connect(self._on_list_cleared)
        self._converter_tab.list_cleared.connect(self._on_list_cleared)
        # Drag-enter sounds
        self._alpha_tab.drag_entered.connect(self._on_drag_entered)
        self._converter_tab.drag_entered.connect(self._on_drag_entered)
        # Preview-refresh sounds
        self._alpha_tab.preview_refreshed.connect(self._on_preview_refreshed)
        # Cross-tool zone sharing: zones copied from the Alpha tool preview flow
        # directly into the Selective Alpha tool's import buffer.
        self._alpha_tab.zone_masks_shared.connect(
            self._selective_alpha_tab.receive_shared_zones
        )
        # Cross-tool output directory sync (item #3): when one tool's output
        # dir changes via Browse, mirror it to the other tool so users only
        # have to set it once.
        self._alpha_tab.output_dir_changed.connect(
            self._converter_tab.set_output_dir
        )
        self._converter_tab.output_dir_changed.connect(
            self._alpha_tab.set_output_dir
        )

        # Cursor
        self._apply_cursor()

        # Sound engine
        from .sound_engine import SoundEngine
        self._sound = SoundEngine(self._settings, parent=self)
        self._sound.install_on_app(QApplication.instance())
        # Wire sound engine into tools that need it.
        self._selective_alpha_tab._sound = self._sound

        # Font size
        self._apply_font_size()

        # Tooltip manager
        from .tooltip_manager import TooltipManager
        self._tooltip_mgr = TooltipManager(self._settings, parent=self)
        self._tooltip_mgr.install_on_app(QApplication.instance())
        self._register_tooltips()

    def _register_tooltips(self) -> None:
        """Wire all main-window and tab widgets to the TooltipManager."""
        mgr = self._tooltip_mgr
        if mgr is None:
            return
        mgr.register(self._btn_settings, "settings_btn")
        mgr.register(self._btn_help, "help_btn")
        mgr.register(self._btn_patreon, "patreon_btn")
        # Register per-tab tooltips on the QTabBar
        mgr.register_tab_bar(
            self._tabs.tabBar(),
            ["alpha_fixer_tab", "converter_tab", "history_tab", "selective_alpha_tab"],
        )
        self._alpha_tab.register_tooltips(mgr)
        self._converter_tab.register_tooltips(mgr)
        self._history_tab.register_tooltips(mgr)
        self._selective_alpha_tab.register_tooltips(mgr)

    def _apply_theme_effect(self):
        """Set the click-effects overlay to match the active theme's effect key."""
        if self._click_effects is None:
            return
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "Panda Dark")
        # If "use theme effect" is enabled, always auto-select from THEME_EFFECTS map
        if self._settings.get("use_theme_effect", False):
            effect_key = THEME_EFFECTS.get(theme_name, "default")
        else:
            # Prefer the theme dict's own _effect key (which the user may have
            # customised in the settings dialog) over the hardcoded THEME_EFFECTS
            # map.  This ensures that changing the "Click Effect Style" combo in
            # Settings → Theme is actually respected even for preset themes.
            # Fall back to THEME_EFFECTS only when no _effect key is stored.
            effect_key = theme.get("_effect") or THEME_EFFECTS.get(theme_name, "default")
        self._click_effects.set_effect(effect_key)
        # Push the user's custom emoji list to the custom spawner
        custom_raw = self._settings.get("custom_emoji", DEFAULT_CUSTOM_EMOJI)
        custom_emoji = custom_raw.split() if custom_raw.strip() else DEFAULT_CUSTOM_EMOJI.split()
        self._click_effects.set_custom_emoji(custom_emoji)

    def _apply_button_anim(self) -> None:
        """Enable or disable button press animations to match the active settings."""
        if self._button_anim is None:
            return
        enabled = self._settings.get("button_anim_enabled")  # default True from DEFAULTS
        if not enabled:
            self._button_anim.set_enabled(False)
            return
        theme = self._settings.get_theme()
        if self._settings.get("use_theme_button_anim"):
            mode = theme.get("_button_anim", "press")
        else:
            mode = self._settings.get("button_anim_style", "press")
        self._button_anim.set_enabled(True, mode)

    def _apply_bg_drip(self) -> None:
        """Apply the background drip effect based on current settings."""
        if self._click_effects is None:
            return
        enabled = self._settings.get("bg_drip_enabled", False)
        if not enabled:
            self._click_effects.set_bg_drip("blood", False)
            return
        if self._settings.get("use_theme_drip", False):
            theme = self._settings.get_theme()
            effect_key = theme.get("_effect", "default")
            # Map theme effects to drip types
            if effect_key in ("gore", "shark"):
                drip_type = "blood"
            elif effect_key in ("ocean", "ripple", "mermaid"):
                drip_type = "water"
            else:
                drip_type = self._settings.get("bg_drip_type", "blood")
        else:
            drip_type = self._settings.get("bg_drip_type", "blood")
        self._click_effects.set_bg_drip(drip_type, True)

    def _apply_bg_flock(self) -> None:
        """Apply the background flock effect based on current settings."""
        if self._click_effects is None:
            return
        enabled = self._settings.get("bg_flock_enabled", False)
        if not enabled:
            self._click_effects.set_bg_flock(False)
            return
        _FLOCK_EMOJI = {
            "bats":        "🦇",
            "fairies":     "🧚",
            "fish":        "🐟",
            "butterflies": "🦋",
            "birds":       "🐦",
            "stars":       "⭐",
            "petals":      "🌸",
        }
        theme = self._settings.get_theme()
        trail_color = theme.get("_trail_color", "#e94560")
        use_theme_flock = self._settings.get("use_theme_flock", False)
        if use_theme_flock:
            # Only show a flock if the theme explicitly defines one via "_flock".
            # Themes without "_flock" mean "no themed flock" — don't show bats
            # on a Goth or Panda theme just because it's the default.
            theme_flock = theme.get("_flock")
            if not theme_flock:
                self._click_effects.set_bg_flock(False)
                return
            emoji = _FLOCK_EMOJI.get(theme_flock, theme.get("_icon", "🐼"))
        else:
            flock_style = self._settings.get("bg_flock_style", "bats")
            emoji = _FLOCK_EMOJI.get(flock_style, theme.get("_icon", "🐼"))
        self._click_effects.set_bg_flock(True, emoji, trail_color)

    def _apply_bg_ambient(self) -> None:
        """Apply the background ambient effect based on current settings."""
        if self._click_effects is None:
            return
        enabled = self._settings.get("bg_ambient_enabled", False)
        if not enabled:
            self._click_effects.set_bg_ambient("none", False)
            return
        use_theme_ambient = self._settings.get("use_theme_ambient", False)
        if use_theme_ambient:
            from .theme_engine import THEME_AMBIENT_MAP
            theme = self._settings.get_theme()
            ambient_key = THEME_AMBIENT_MAP.get(theme.get("name", ""))
            if not ambient_key:
                # This theme has no defined ambient — respect that and disable.
                self._click_effects.set_bg_ambient("none", False)
                return
            self._click_effects.set_bg_ambient(ambient_key, True)
        else:
            ambient_type = self._settings.get("bg_ambient_type", "snow")
            self._click_effects.set_bg_ambient(ambient_type if ambient_type != "none" else "snow", True)

    # ------------------------------------------------------------------
    # Easter-egg discovery system
    # ------------------------------------------------------------------

    def _setup_easter_eggs(self) -> None:
        """Create collectible widgets and attach click filters to secret spots."""
        # Map spot_id → the widget to watch
        spot_widgets: dict[str, QWidget] = {
            "egg_banner_left":   self._banner_emoji_left,
            "egg_banner_right":  self._banner_emoji_right,
            "egg_help_btn":      self._btn_help,
            "egg_history_tab":   self._history_tab,
            "egg_status_bar":    self._status_bar,
            "egg_patreon_btn":   self._btn_patreon,
            "egg_converter_tab": self._converter_tab,
            "egg_alpha_tab":     self._alpha_tab,
            "egg_selective_tab": self._selective_alpha_tab,
            "egg_settings_btn":  self._btn_settings,
            "egg_svg_badge":     self._svg_badge,
            "egg_theme_label":   self._theme_label,
            "egg_unlock_lbl":    self._unlock_lbl,
            "egg_banner_text":   self._banner_lbl,
            "egg_tab_bar":       self._tabs.tabBar(),
        }

        for spot_id, threshold, unlock_key, emoji, tip, banner in self._EASTER_SPOTS:
            widget = spot_widgets.get(spot_id)
            if widget is None:
                continue

            # Create & store the collectible
            collectible = _EasterCollectible(emoji, tip, self)
            self._easter_collectibles[spot_id] = collectible

            # Connect its "collected" signal (lambda captures spot_id & data)
            def _make_collect_handler(sid, uk, bm, col):
                def _on_collect():
                    self._on_collectible_collected(sid, uk, bm, col)
                return _on_collect

            collectible.collected.connect(
                _make_collect_handler(spot_id, unlock_key, banner, collectible)
            )

            # Create & attach the click filter
            filt = _EasterClickFilter(threshold, self)
            self._easter_filters[spot_id] = filt
            widget.installEventFilter(filt)

            # Connect the filter's "triggered" signal
            def _make_trigger_handler(sid, uk, col):
                def _on_trigger(gpos: QPoint):
                    self._on_easter_spot_triggered(sid, uk, gpos, col)
                return _on_trigger

            filt.triggered.connect(_make_trigger_handler(spot_id, unlock_key, collectible))

    def _on_easter_spot_triggered(
        self, spot_id: str, unlock_key: str, gpos: QPoint, collectible: "_EasterCollectible"
    ) -> None:
        """A secret spot accumulated enough clicks — show the collectible.

        Silently skips if the theme is already unlocked so the collectible
        does not keep re-appearing after the player has collected it.
        """
        if collectible.isVisible():
            return  # already showing
        # Don't re-spawn if the theme was already unlocked
        try:
            if self._settings.get(unlock_key, False):
                return
        except Exception:
            pass
        # Map global pos to main-window-local coords for positioning
        local_pos = self.mapFromGlobal(gpos)
        # Nudge upward a bit so the collectible appears above the click point
        local_pos = QPoint(local_pos.x(), local_pos.y() - 30)
        collectible.show_at(local_pos)
        # Optionally play a discovery sound (soft chime)
        try:
            self._sound.play_file_add()
        except Exception:
            pass

    def _on_collectible_collected(
        self,
        spot_id: str,
        unlock_key: str,
        banner_msg: str,
        collectible: "_EasterCollectible",
    ) -> None:
        """The user clicked a collectible — unlock the theme and celebrate."""
        try:
            if not self._settings.get(unlock_key, False):
                self._settings.set(unlock_key, True)
                self._unlock_lbl.setText(banner_msg)
                self._schedule_unlock_clear()
                try:
                    self._sound.play_unlock()
                except Exception:
                    try:
                        QApplication.instance().beep()
                    except Exception:
                        pass
            else:
                # Theme was already unlocked — still celebrate with a message
                self._unlock_lbl.setText(f"✅ Already unlocked!  {banner_msg}")
                self._schedule_unlock_clear()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Secret key-sequence unlocks
    # ------------------------------------------------------------------

    def _setup_key_secrets(self) -> None:
        """Install an app-level event filter that watches for secret key sequences.

        Uses :class:`_KeySecretFilter` to track key-press events across every
        widget.  When a registered sequence completes (Konami code or a secret
        word), a :class:`_EasterCollectible` floats at the centre of the window
        and the user can click it to unlock the corresponding hidden theme.
        """
        secrets = self._build_key_secrets()
        # Build the raw {name: (key_int, ...)} mapping for the filter.
        seq_map = {name: data["seq"] for name, data in secrets.items()}

        self._key_secret_filter = _KeySecretFilter(seq_map, self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._key_secret_filter)

        # Pre-create a collectible for each secret so we can reuse them.
        for name, data in secrets.items():
            col = _EasterCollectible(data["emoji"], data["collectible_tip"], self)
            col._unlock_key = data["unlock_key"]  # stored for already-unlocked guard
            self._key_secret_collectibles[name] = col

            # Connect the "collected" signal.
            def _make_handler(n, uk, bm, c):
                def _on_collect():
                    # Re-use the same collectible_collected logic as easter eggs.
                    self._on_collectible_collected(
                        f"key_secret_{n}", uk, bm, c
                    )
                return _on_collect

            col.collected.connect(
                _make_handler(name, data["unlock_key"], data["banner_msg"], col)
            )

        # Connect the filter's triggered signal to the handler.
        self._key_secret_filter.triggered.connect(self._on_key_secret_triggered)

    def _on_key_secret_triggered(self, name: str) -> None:
        """A secret key sequence was completed — show the collectible.

        The collectible appears near the centre of the main window.  For the
        Konami code a brief special banner flash is shown before the collectible
        so the player knows something happened.
        """
        col = self._key_secret_collectibles.get(name)
        if col is None or col.isVisible():
            return
        # Don't re-spawn if the theme was already unlocked
        try:
            uk = getattr(col, "_unlock_key", None)
            if uk and self._settings.get(uk, False):
                return
        except Exception:
            pass

        # Konami code: briefly flash a hint in the unlock label first.
        if name == "konami":
            self._unlock_lbl.setText("🎮 ↑↑↓↓←→←→BA … CHEAT CODE DETECTED!")
            self._schedule_unlock_clear()

        # Show collectible near the horizontal centre, slightly above middle.
        w, h = self.width(), self.height()
        cx = w // 2
        cy = max(60, h // 3)
        col.show_at(QPoint(cx, cy))

        try:
            self._sound.play_file_add()
        except Exception:
            pass

    def _check_unlocks(self) -> None:
        """Check whether any hidden theme should be unlocked (click path)."""
        try:
            total = self._settings.get("total_clicks", 0) + 1
            self._settings.set("total_clicks", total)
        except Exception:
            return
        self._run_unlock_checks(total)

    def _run_unlock_checks(self, total: int) -> None:
        """Evaluate the unlock table against *total* and fire any new unlocks."""
        # _UNLOCK_TABLE is a class constant (sorted ascending by threshold).
        # The break condition `threshold > total` is equivalent to the original
        # `total >= threshold` guard, with the addition that we skip the rest of
        # the table once no further entry can fire — avoiding iterating all 33
        # entries on every click for users who have not yet reached many thresholds.
        newly_unlocked = False
        for threshold, key, message in self._UNLOCK_TABLE:
            if threshold > total:
                # Remaining entries all have higher thresholds; none can fire.
                break
            if not self._settings.get(key, False):
                # Threshold reached and not yet unlocked.
                self._settings.set(key, True)
                self._unlock_lbl.setText(message)
                # Play unlock fanfare via SoundEngine (falls back to beep)
                try:
                    self._sound.play_unlock()
                except Exception:
                    try:
                        QApplication.instance().beep()
                    except Exception:
                        pass
                newly_unlocked = True

        # Auto-clear the unlock banner after 6 seconds
        if newly_unlocked:
            self._schedule_unlock_clear()

    def _on_processing_done(self, file_count: int) -> None:
        """Called when a batch of files is processed (alpha-fix or convert).

        Each file successfully processed is counted as a 'bonus click' so
        that heavy users who batch-process files naturally unlock themes
        without having to manually click thousands of times.  Also plays
        the success chime (or a special fanfare for large batches) if sound
        is enabled.
        """
        if file_count <= 0:
            return
        try:
            if file_count >= 100:
                # Large batch — play the rising fanfare instead of the normal ping
                self._sound.play_batch_done()
            else:
                self._sound.play_success()
        except Exception:
            pass
        try:
            total = self._settings.get("total_clicks", 0) + file_count
            self._settings.set("total_clicks", total)
        except Exception:
            return
        # Re-use the click-based unlock table but driven by total_clicks
        # (which now includes processing bonuses).
        self._run_unlock_checks(total)

    def _on_files_added(self) -> None:
        """Play a soft sound when files are added to either tab's queue."""
        try:
            self._sound.play_file_add()
        except Exception:
            pass

    def _on_files_removed(self) -> None:
        """Play a short pop when files are removed from either tab's queue."""
        try:
            self._sound.play_file_remove()
        except Exception:
            pass

    def _on_list_cleared(self) -> None:
        """Play a dog bark when the entire file list is cleared at once."""
        try:
            self._sound.play_dog_bark()
        except Exception:
            pass

    def _on_drag_entered(self) -> None:
        """Play a gentle ping when files are dragged over either tab's drop zone."""
        try:
            self._sound.play_drag_enter()
        except Exception:
            pass

    def _on_processing_started(self) -> None:
        """Play an ascending two-tone cue when a batch starts processing."""
        try:
            self._sound.play_process_start()
        except Exception:
            pass

    def _on_processing_error(self, error_count: int) -> None:
        """Play an error buzz when a batch finishes with failures."""
        try:
            self._sound.play_error()
        except Exception:
            pass

    def _on_preview_refreshed(self) -> None:
        """Play a subtle ping when the live preview refreshes (opt-in, off by default)."""
        try:
            self._sound.play_preview()
        except Exception:
            pass

    def _on_theme_changed_sound(self) -> None:
        """Play a sound when the user switches to a different theme.

        The default is a soft whoosh.  Certain themes play their own
        characteristic animal sound instead (if the user has that toggle on).
        """
        try:
            theme_name = self._settings.get_theme().get("name", "")
            if theme_name == "Bat Cave":
                # Try bat screech first; fall back to the generic whoosh if
                # the per-event toggle is off.
                self._sound.play_bat_screech()
                self._sound.play_theme_change()
            elif theme_name in ("Panda Dark", "Panda Light",
                                "Purrfect Cats", "Space Cat"):
                # Cat / panda themes play a meow; fall back to whoosh.
                self._sound.play_cat_meow()
                self._sound.play_theme_change()
            elif theme_name == "Good Dog":
                # Dog theme plays a bark; fall back to whoosh.
                self._sound.play_dog_bark()
                self._sound.play_theme_change()
            elif theme_name in ("Slime", "Snake Pit"):
                # Swamp / nature themes get a frog croak; fall back to whoosh.
                self._sound.play_frog_croak()
                self._sound.play_theme_change()
            else:
                self._sound.play_theme_change()
        except Exception:
            pass

    def _schedule_unlock_clear(self) -> None:
        """Start (or restart) a one-shot timer that clears the unlock label."""
        from PyQt6.QtCore import QTimer
        if self._unlock_timer is None:
            self._unlock_timer = QTimer(self)
            self._unlock_timer.setSingleShot(True)
            # Use a named method instead of a lambda so the callback is safe
            # even if the window starts closing before the 6-second timeout fires.
            self._unlock_timer.timeout.connect(self._clear_unlock_label)
        self._unlock_timer.start(6000)

    def _clear_unlock_label(self) -> None:
        """Clear the unlock notification label.  Guards against the label being
        None (destroyed) if the timer fires during window teardown."""
        if self._unlock_lbl is not None:
            self._unlock_lbl.setText("")

    def _apply_cursor(self):
        use_theme = self._settings.get("use_theme_cursor", False)
        anim_enabled = self._settings.get("cursor_anim_enabled", True)
        if use_theme:
            # Read the active theme's preferred cursor
            theme = self._settings.get_theme()
            cursor_spec = theme.get("_cursor", "Default")
            if cursor_spec.startswith("emoji:"):
                emoji = cursor_spec[len("emoji:"):]
                self._start_cursor_anim(emoji) if anim_enabled else self._stop_cursor_anim()
                if not anim_enabled:
                    self.setCursor(_make_emoji_cursor(emoji))
                return
            # Otherwise treat it as a named cursor key
            self._stop_cursor_anim()
            shape = _CURSOR_MAP.get(cursor_spec, Qt.CursorShape.ArrowCursor)
            self.setCursor(QCursor(shape))
        else:
            cursor_name = self._settings.get("cursor", "Default")
            # Check if it's a system cursor name
            if cursor_name in _CURSOR_MAP:
                self._stop_cursor_anim()
                self.setCursor(QCursor(_CURSOR_MAP[cursor_name]))
            elif cursor_name.startswith("emoji:"):
                # Stored as "emoji:<char>" from theme profiles
                emoji = cursor_name[len("emoji:"):]
                self._start_cursor_anim(emoji) if anim_enabled else self._stop_cursor_anim()
                if not anim_enabled:
                    self.setCursor(_make_emoji_cursor(emoji))
            else:
                # Combo items like "🐼 Panda" – extract the emoji (first char/cluster)
                # by taking everything before the first space
                parts = cursor_name.split(" ", 1)
                if parts and parts[0].strip():
                    emoji = parts[0]
                    self._start_cursor_anim(emoji) if anim_enabled else self._stop_cursor_anim()
                    if not anim_enabled:
                        self.setCursor(_make_emoji_cursor(emoji))
                else:
                    self._stop_cursor_anim()
                    self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def _start_cursor_anim(self, emoji: str) -> None:
        """Start cursor animation for *emoji*.

        Priority:
        1. ``_CURSOR_SPIN_EMOJI`` — round/symmetric glyphs get a smooth 360° spin.
        2. ``_CURSOR_WOBBLE_EMOJI`` — directional/elongated glyphs get a ±20° pendulum
           wobble rendered as pre-composited QCursor frames (a true physical animation).
        3. ``_CURSOR_ANIM_FRAMES`` — symbol-cycling fallback.
        4. Static cursor if nothing else matches.
        """
        # ── 1. Physical spin animation for round/symmetric emoji ──────────────
        if emoji in _CURSOR_SPIN_EMOJI:
            # Avoid re-generating frames if the same emoji is already spinning.
            if (self._cursor_spin_emoji == emoji
                    and self._cursor_spin_cursors
                    and self._cursor_anim_timer is not None
                    and self._cursor_anim_timer.isActive()):
                return
            spin_cursors = _make_spin_cursor_frames(emoji)
            if spin_cursors:
                self._cursor_anim_frames = []
                self._cursor_spin_cursors = spin_cursors
                self._cursor_spin_emoji = emoji
                self._cursor_anim_idx = 0
                # Show the first frame immediately so there is no blank gap.
                self.setCursor(spin_cursors[0])
                if self._cursor_anim_timer is None:
                    self._cursor_anim_timer = QTimer(self)
                    self._cursor_anim_timer.timeout.connect(self._tick_cursor_anim)
                self._cursor_anim_timer.setInterval(80)  # ~12 fps — smooth spin
                self._cursor_anim_timer.start()
                return
            # Fall through if frame generation failed.

        # ── 2. Physical wobble animation for directional/themed emoji ─────────
        if emoji in _CURSOR_WOBBLE_EMOJI:
            if (self._cursor_spin_emoji == emoji
                    and self._cursor_spin_cursors
                    and self._cursor_anim_timer is not None
                    and self._cursor_anim_timer.isActive()):
                return
            wobble_cursors = _make_wobble_cursor_frames(emoji)
            if wobble_cursors:
                self._cursor_anim_frames = []
                self._cursor_spin_cursors = wobble_cursors
                self._cursor_spin_emoji = emoji
                self._cursor_anim_idx = 0
                self.setCursor(wobble_cursors[0])
                if self._cursor_anim_timer is None:
                    self._cursor_anim_timer = QTimer(self)
                    self._cursor_anim_timer.timeout.connect(self._tick_cursor_anim)
                self._cursor_anim_timer.setInterval(60)  # ~16 fps — smooth wobble
                self._cursor_anim_timer.start()
                return
            # Fall through if frame generation failed.

        # ── 3. Symbol-cycling animation ────────────────────────────────────────
        self._cursor_spin_cursors = []
        self._cursor_spin_emoji = ""
        frames = _CURSOR_ANIM_FRAMES.get(emoji)
        if not frames:
            # No animation frames defined for this emoji – render it static.
            self._stop_cursor_anim()
            self.setCursor(_make_emoji_cursor(emoji))
            return
        # If the same sequence is already running, don't restart it
        # (avoids the cursor jumping back to frame 0 on minor settings refreshes).
        if self._cursor_anim_frames == frames and self._cursor_anim_timer is not None and self._cursor_anim_timer.isActive():
            return
        self._cursor_anim_frames = frames
        self._cursor_anim_idx = 0
        # Show the first frame immediately so there's no blank-cursor gap.
        self.setCursor(_make_emoji_cursor(frames[0]))
        if self._cursor_anim_timer is None:
            self._cursor_anim_timer = QTimer(self)
            self._cursor_anim_timer.timeout.connect(self._tick_cursor_anim)
        self._cursor_anim_timer.setInterval(250)  # 250 ms per frame ≈ 4 fps – noticeably animated
        self._cursor_anim_timer.start()

    def _stop_cursor_anim(self) -> None:
        """Stop the cursor animation timer and clear the frame buffer."""
        if self._cursor_anim_timer is not None:
            self._cursor_anim_timer.stop()
        self._cursor_anim_frames = []
        self._cursor_spin_cursors = []
        self._cursor_spin_emoji = ""
        self._cursor_anim_idx = 0

    def _tick_cursor_anim(self) -> None:
        """Advance to the next cursor animation frame."""
        # ── Physical spin frames (list[QCursor]) ─────────────────────────
        if self._cursor_spin_cursors:
            self._cursor_anim_idx = (self._cursor_anim_idx + 1) % len(self._cursor_spin_cursors)
            try:
                self.setCursor(self._cursor_spin_cursors[self._cursor_anim_idx])
            except RuntimeError:
                if self._cursor_anim_timer is not None:
                    self._cursor_anim_timer.stop()
            return
        # ── Symbol-cycling frames (list[str]) ─────────────────────────────
        if not self._cursor_anim_frames:
            if self._cursor_anim_timer is not None:
                self._cursor_anim_timer.stop()
            return
        self._cursor_anim_idx = (self._cursor_anim_idx + 1) % len(self._cursor_anim_frames)
        try:
            self.setCursor(_make_emoji_cursor(self._cursor_anim_frames[self._cursor_anim_idx]))
        except RuntimeError:
            # Widget destroyed during teardown – stop the timer gracefully.
            if self._cursor_anim_timer is not None:
                self._cursor_anim_timer.stop()

    def _apply_font_size(self):
        base_size = self._settings.get("font_size", 10)
        base_size = max(8, min(24, int(base_size)))
        # Apply UI scale factor on top of the base font size.
        _scale_factors = {"Compact": 0.85, "Normal": 1.0, "Large": 1.15, "Extra Large": 1.30}
        scale_key = self._settings.get("ui_scale", "Normal")
        factor = _scale_factors.get(scale_key, 1.0)
        size = max(7, int(round(base_size * factor)))
        app = QApplication.instance()
        if app is None:
            return
        font = QFont(app.font())
        if font.pointSize() == size:
            return  # No change — skip the re-polish cost.
        font.setPointSize(size)
        app.setFont(font)
        # Force all open windows to immediately re-polish their appearance so
        # the font-size change is visible without requiring a restart.  Calling
        # app.setStyle(app.style()) emits a StyleChange event which causes
        # every widget to re-measure text and repaint itself.
        try:
            app.setStyle(app.style())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Geometry / state
    # ------------------------------------------------------------------

    def _restore_geometry(self):
        if self._settings.get("window_maximized", False):
            self.showMaximized()
            return
        x = self._settings.get("window_x")
        y = self._settings.get("window_y")
        w = self._settings.get("window_w")
        h = self._settings.get("window_h")
        # Guard against the window being positioned entirely off-screen
        # (e.g. after a secondary monitor is disconnected).  We check that at
        # least a strip of the title bar is visible on *some* available screen.
        _MIN_VISIBLE_W = 100   # minimum logical pixels of title bar that must be visible
        _MIN_VISIBLE_H = 50    # height of the title-bar strip we check
        title_bar_strip = QRect(x, y, max(w, _MIN_VISIBLE_W), _MIN_VISIBLE_H)
        screens = QApplication.screens()
        on_screen = any(
            scr.availableGeometry().intersects(title_bar_strip)
            for scr in screens
        )
        primary = QApplication.primaryScreen()
        if primary is None and screens:
            primary = screens[0]
        if not on_screen:
            # Centre on the primary (or first available) screen instead.
            if primary is not None:
                ag = primary.availableGeometry()
                x = ag.x() + max(0, (ag.width()  - w) // 2)
                y = ag.y() + max(0, (ag.height() - h) // 2)
        # Clamp saved size so it doesn't exceed the available area
        # (e.g. the user previously ran on a larger monitor or higher resolution)
        # and clamp position so the window is fully within the available area.
        if primary is not None:
            ag = primary.availableGeometry()
            w = min(w, ag.width())
            h = min(h, ag.height())
            # Shift the window left/up if the right/bottom edge extends off screen.
            x = max(ag.x(), min(x, ag.x() + ag.width()  - w))
            y = max(ag.y(), min(y, ag.y() + ag.height() - h))
        self.setGeometry(x, y, w, h)

    def _save_geometry(self):
        self._settings.set("window_maximized", self.isMaximized())
        if not self.isMaximized():
            g = self.geometry()
            self._settings.set("window_x", g.x())
            self._settings.set("window_y", g.y())
            self._settings.set("window_w", g.width())
            self._settings.set("window_h", g.height())

    def _clamp_to_screen(self) -> None:
        """Ensure the window is visible on *some* available screen.

        Called after the user:
        • Moves the window to a different monitor
        • Changes the system DPI / display-scale setting
        • Connects or disconnects a monitor

        If the title bar is entirely off-screen the window is centred on the
        primary (or first available) screen.  The window size is also clamped
        so it never exceeds the available screen area.
        """
        if self.isMaximized() or self.isFullScreen():
            return
        g = self.geometry()
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        _MIN_VISIBLE_W = 100
        _MIN_VISIBLE_H = 50
        title_bar_strip = QRect(x, y, max(w, _MIN_VISIBLE_W), _MIN_VISIBLE_H)
        screens = QApplication.screens()
        on_screen = any(
            scr.availableGeometry().intersects(title_bar_strip)
            for scr in screens
        )
        primary = QApplication.primaryScreen()
        if primary is None and screens:
            primary = screens[0]
        if primary is not None:
            ag = primary.availableGeometry()
            # Clamp size to available area
            w = min(w, ag.width())
            h = min(h, ag.height())
            if not on_screen:
                x = ag.x() + max(0, (ag.width()  - w) // 2)
                y = ag.y() + max(0, (ag.height() - h) // 2)
            self.setGeometry(x, y, w, h)
        elif not on_screen and screens:
            # No primary screen object – just re-centre on the first screen
            ag = screens[0].availableGeometry()
            w = min(w, ag.width())
            h = min(h, ag.height())
            self.setGeometry(
                ag.x() + max(0, (ag.width()  - w) // 2),
                ag.y() + max(0, (ag.height() - h) // 2),
                w, h,
            )

    def _on_screens_changed(self, *_args) -> None:
        """Handle monitor added/removed or primary-screen change.

        Deferred 250 ms so the OS has time to finish updating screen geometry
        before we query it.  Two timers coalesce into one callback even when
        multiple signals fire in quick succession (e.g. a resolution change
        can emit both ``screenRemoved`` and ``screenAdded`` for the same
        physical monitor).
        """
        QTimer.singleShot(250, self._clamp_to_screen)
        QTimer.singleShot(250, self._update_minimum_size)
        QTimer.singleShot(250, self._apply_font_size)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_theme(self):
        theme = self._settings.get_theme()
        tooltip_style = self._settings.get("tooltip_style", "Auto (follow theme)")
        app = QApplication.instance()
        if app is not None:
            new_ss = build_stylesheet(theme, tooltip_style)
            if new_ss != self._last_stylesheet:
                app.setStyleSheet(new_ss)
                self._last_stylesheet = new_ss
        theme_name = theme.get("name", "Custom")
        self._theme_label.setText(f"  Theme: {theme_name}  ")
        # Keep the tooltip manager in sync so theme-aware tooltips display
        # the correct theme name.
        if self._tooltip_mgr is not None:
            self._tooltip_mgr.set_active_theme(theme_name)
        # Update the banner emoji widget to the theme's representative icon.
        icon = get_theme_icon(theme_name)
        animated = self._settings.get("animated_banner_enabled", False)
        # Determine the animation mode: use theme's _banner_anim if the
        # "Use theme animation" setting is on, otherwise the manual setting.
        if self._settings.get("banner_use_theme_anim", True):
            anim_mode = theme.get("_banner_anim", "spin")
        else:
            anim_mode = self._settings.get("banner_anim_style", "spin")
        # "flock" has moved to Background Effects; map it to "bounce" for banner.
        if anim_mode == "flock":
            anim_mode = "bounce"
        if self._banner_emoji_left is not None:
            self._banner_emoji_left.set_emoji(icon)
            self._banner_emoji_left.set_mode(anim_mode)
            self._banner_emoji_left.set_animated(animated)
        if self._banner_emoji_right is not None:
            self._banner_emoji_right.set_emoji(icon)
            self._banner_emoji_right.set_mode(anim_mode)
            self._banner_emoji_right.set_animated(animated)
        if self._click_effects is not None:
            trail_color = theme.get("_trail_color", "#e94560")
            self._click_effects.set_banner_flock(False, icon, trail_color)
        # Keep static text label; update it to the theme banner (without emojis)
        if self._banner_lbl is not None:
            self._banner_lbl.setText("Alpha & RGBA Adjuster  |  File Converter")
        # Stop any legacy animation timer (banner no longer cycles emojis)
        if self._anim_timer is not None:
            self._anim_timer.stop()
        # Store theme-specific tab labels; update tab text directly (no spinner).
        self._tab_base_labels = get_theme_tab_labels(theme_name)
        self._update_tab_labels()
        # Update inner tab headers to also reflect the active theme
        self._alpha_tab.update_theme(theme_name)
        self._converter_tab.update_theme(theme_name)
        self._history_tab.update_theme(theme_name)
        # Update status bar with per-theme flavor message
        if self._status_bar is not None:
            self._status_bar.showMessage(get_theme_status(theme_name))
        # Re-apply cursor so theme-cursor mode updates immediately on theme change
        self._apply_cursor()
        # Update window icon and taskbar icon to match the current theme SVG
        self._refresh_window_icon(theme_name)
        # Update toolbar icon to match the current theme
        self._refresh_toolbar_icon(theme_name)
        # Refresh SVG badge to match new theme
        self._refresh_svg_badge()
        # Keep trail and click-effects in sync with the active theme.
        # These overlays are created in _setup_effects() which runs after the
        # first _apply_theme() call, so guard with None checks.
        if self._trail_overlay is not None:
            self._apply_trail()
        if self._click_effects is not None:
            self._apply_theme_effect()
            self._apply_bg_drip()
            self._apply_bg_flock()
            self._apply_bg_ambient()
        if self._button_anim is not None:
            self._apply_button_anim()
        # On Windows 11+, colour the native title bar to match the theme's
        # primary/surface colour so the window chrome integrates with the theme.
        try:
            hwnd = int(self.winId())
            # Use the theme's 'primary' colour for the title bar background.
            # Fallback to surface, then a dark default.
            title_color = (
                theme.get("primary")
                or theme.get("surface")
                or "#1a1a2e"
            )
            _apply_dwm_title_bar_color(hwnd, title_color)
        except Exception:
            pass
        # Update the before/after comparison divider colour to match the theme.
        accent = theme.get("accent") or "#e94560"
        try:
            self._alpha_tab._compare.set_divider_color(accent)
        except AttributeError:
            pass
        try:
            self._converter_tab._compare.set_divider_color(accent)
        except AttributeError:
            pass

    def _update_tab_labels(self):
        """Write the theme-specific label to every tab (no animation prefix)."""
        for i, base in enumerate(self._tab_base_labels):
            self._tabs.setTabText(i, base)

    def _make_toolbar_panda_icon(self):
        """Render the panda SVG to a 28×28 QLabel for the toolbar. Returns None on failure."""
        import os
        svg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "svg")
        for candidate in ("panda_dark.svg", "panda_light.svg"):
            svg_path = os.path.normpath(os.path.join(svg_dir, candidate))
            if os.path.isfile(svg_path):
                try:
                    from PyQt6.QtSvg import QSvgRenderer
                    renderer = QSvgRenderer(svg_path)
                    pix = QPixmap(28, 28)
                    pix.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(pix)
                    renderer.render(painter)
                    painter.end()
                    lbl = QLabel()
                    lbl.setPixmap(pix)
                    lbl.setToolTip("Alpha && RGBA Adjuster  |  File Converter 🐼")
                    lbl.setContentsMargins(4, 0, 4, 0)
                    return lbl
                except Exception:
                    pass
                break
        # Fallback: plain text panda emoji
        lbl = QLabel("🐼")
        lbl.setToolTip("Alpha && RGBA Adjuster  |  File Converter 🐼")
        lbl.setContentsMargins(4, 0, 4, 0)
        return lbl

    def _make_svg_badge(self):
        """Create a small SVG theme badge widget.  Returns None if QtSvg unavailable."""
        try:
            from PyQt6.QtSvgWidgets import QSvgWidget
            badge = QSvgWidget()
            badge.setFixedSize(48, 48)
            badge.setToolTip("Theme decoration")
            return badge
        except ImportError:
            return None

    def _refresh_svg_badge(self):
        """Update the SVG badge to show the decoration for the current theme.

        The badge is hidden when animated_banner_enabled is False because the
        SVG files themselves contain <animate> elements – showing them while
        animations are disabled would be misleading.
        """
        if self._svg_badge is None:
            return
        # Hide badge entirely when animations are disabled
        if not self._settings.get("animated_banner_enabled", False):
            self._svg_badge.hide()
            return
        theme = self._settings.get_theme()
        svg_path = get_theme_svg_path(theme.get("name", ""))
        if svg_path:
            self._svg_badge.load(svg_path)
            self._svg_badge.setToolTip(f"{theme.get('name','?')} theme")
            self._svg_badge.show()
        else:
            self._svg_badge.hide()

    def _refresh_window_icon(self, theme_name: str):
        """Update the window / taskbar icon to the theme-specific SVG.

        Renders the theme SVG at multiple resolutions (16 → 256 px) so Qt can
        pick the sharpest pixmap for each use-case (title bar, taskbar, etc.).
        Falls back to the panda default when no theme SVG is available.
        """
        import os
        svg_path = get_theme_svg_path(theme_name)
        if not svg_path:
            # No theme SVG – use the panda default
            svg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "svg")
            for candidate in ("panda_dark.svg", "panda_light.svg"):
                candidate_path = os.path.normpath(os.path.join(svg_dir, candidate))
                if os.path.isfile(candidate_path):
                    svg_path = candidate_path
                    break
        if not svg_path:
            return
        try:
            icon = self._render_svg_to_icon(svg_path)
            if icon is None:
                return
            self.setWindowIcon(icon)
            QApplication.setWindowIcon(icon)
        except RuntimeError:
            # Widget destroyed – silently skip.
            pass

    def _refresh_toolbar_icon(self, theme_name: str) -> None:
        """Update the toolbar icon label to show the active theme's graphic.

        Tries to render the theme's SVG at 28×28 first; falls back to the
        theme's representative emoji when SVG rendering is unavailable.
        """
        if self._toolbar_panda_lbl is None:
            return
        svg_path = get_theme_svg_path(theme_name)
        if svg_path:
            try:
                from PyQt6.QtSvg import QSvgRenderer
                renderer = QSvgRenderer(svg_path)
                pix = QPixmap(28, 28)
                pix.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pix)
                if painter.isActive():
                    renderer.render(painter)
                    painter.end()
                    self._toolbar_panda_lbl.setPixmap(pix)
                    self._toolbar_panda_lbl.setText("")
                    self._toolbar_panda_lbl.setToolTip(f"{theme_name} theme")
                    return
            except Exception:
                pass
        # Fallback: plain emoji text
        icon = get_theme_icon(theme_name)
        self._toolbar_panda_lbl.setPixmap(QPixmap())
        self._toolbar_panda_lbl.setText(icon)
        self._toolbar_panda_lbl.setToolTip(f"{theme_name} theme")

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int):
        if self._tabs.widget(index) is self._history_tab:
            self._history_tab.refresh()
        try:
            self._sound.play_tab_switch()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self, tooltip_mgr=self._tooltip_mgr)
        dlg.theme_changed.connect(lambda t: self._on_settings_changed())
        dlg.theme_changed.connect(lambda t: self._on_theme_changed_sound())
        dlg.settings_changed.connect(self._on_settings_changed)
        # First tooltip mode change unlocks Secret Skeleton (independent of click count)
        dlg.first_tooltip_mode_change.connect(self._on_first_tooltip_mode_change)
        # First cursor animation enable unlocks Toxic Neon
        dlg.first_cursor_anim_enabled.connect(self._on_first_cursor_anim_enabled)
        # First theme preset change unlocks Candy Land
        dlg.first_theme_changed.connect(self._on_first_theme_changed)
        # First trail enable unlocks Midnight Forest
        dlg.first_trail_enabled.connect(self._on_first_trail_enabled)

        # Attach a click-effects overlay to the dialog so particle effects are
        # visible while the settings window is open (the main overlay is behind
        # the modal and therefore not visible).
        dlg_overlay = None
        if self._click_effects is not None:
            from .click_effects import ClickEffectsOverlay
            dlg_overlay = ClickEffectsOverlay(dlg)
            # Mirror the current effect setting but don't count clicks toward
            # secret-theme unlocks (leave click_registered unconnected).
            theme = self._settings.get_theme()
            effect_key = (theme.get("_effect")
                          or THEME_EFFECTS.get(theme.get("name", ""), "default"))
            dlg_overlay.set_effect(effect_key)
            custom_emoji = self._settings.get("custom_emoji", DEFAULT_CUSTOM_EMOJI)
            dlg_overlay.set_custom_emoji(custom_emoji.split() if custom_emoji.strip() else [])
            if self._settings.get("click_effects_enabled", False):
                dlg_overlay.set_enabled(True)
            # Mirror bg drip / flock / ambient so all effects show in the dialog too.
            if self._settings.get("bg_drip_enabled", False):
                use_theme_drip = self._settings.get("use_theme_drip", False)
                if use_theme_drip:
                    eff = theme.get("_effect", "default")
                    drip_type = "blood" if eff in ("gore", "shark") else (
                        "water" if eff in ("ocean", "ripple", "mermaid") else
                        self._settings.get("bg_drip_type", "blood")
                    )
                else:
                    drip_type = self._settings.get("bg_drip_type", "blood")
                dlg_overlay.set_bg_drip(drip_type, True)
            if self._settings.get("bg_flock_enabled", False):
                use_theme_flock = self._settings.get("use_theme_flock", False)
                if use_theme_flock:
                    flock_emoji = theme.get("_icon", "🐼")
                    flock_color = theme.get("_accent1", "#e94560")
                else:
                    flock_style = self._settings.get("bg_flock_style", "bats")
                    _FLOCK_EMOJI = {
                        "bats": "🦇", "fairies": "🧚", "fish": "🐟",
                        "butterflies": "🦋", "birds": "🐦",
                        "stars": "⭐", "petals": "🌸",
                    }
                    flock_emoji = _FLOCK_EMOJI.get(flock_style, "🦇")
                    flock_color = self._settings.get("trail_color", "#e94560")
                dlg_overlay.set_bg_flock(True, flock_emoji, flock_color)
            if self._settings.get("bg_ambient_enabled", False):
                ambient_type = self._settings.get("bg_ambient_type", "none")
                if ambient_type and ambient_type != "none":
                    dlg_overlay.set_bg_ambient(ambient_type, True)

        # Attach a mouse-trail overlay to the dialog if trail is enabled.
        dlg_trail = None
        if (self._trail_overlay is not None
                and self._settings.get("trail_enabled", False)):
            try:
                from .mouse_trail import MouseTrailOverlay
                dlg_trail = MouseTrailOverlay(dlg)
                dlg_trail.setGeometry(dlg.rect())
                dlg_trail.raise_()
                self._apply_trail_to(dlg_trail)
                dlg_trail.set_enabled(True)
            except Exception:
                dlg_trail = None

        dlg.exec()

        if dlg_overlay is not None:
            try:
                dlg_overlay.pause_for_minimize()
            except Exception:
                pass
        if dlg_trail is not None:
            try:
                dlg_trail.set_enabled(False)
            except Exception:
                pass

    def _apply_trail_to(self, overlay) -> None:
        """Apply the current trail settings to *overlay* (a MouseTrailOverlay)."""
        if overlay is None:
            return
        from .theme_engine import ALL_THEMES
        trail_enabled = self._settings.get("trail_enabled", False)
        use_theme_trail = self._settings.get("use_theme_trail", True)
        theme = self._settings.get_theme()
        theme_name = theme.get("name", "")
        if use_theme_trail:
            color = theme.get("_trail_color") or "#ffffff"
            style = theme.get("_trail") or "dots"
        else:
            color = self._settings.get("trail_color", "#ffffff")
            style = self._settings.get("trail_style", "dots")
        overlay.set_color(color)
        overlay.set_style(style)
        overlay.set_length(int(self._settings.get("trail_length", 50)))
        overlay.set_fade_speed(int(self._settings.get("trail_fade_speed", 5)))
        overlay.set_intensity(int(self._settings.get("trail_intensity", 100)))
        overlay.set_enabled(trail_enabled)

    def _on_settings_changed(self):
        """Schedule a deferred re-apply of all effect-related settings.

        The signal can fire very rapidly (e.g. every step of a spinbox or
        slider drag).  Restarting a 200 ms single-shot timer each time
        collapses bursts of signals into a single apply call, eliminating
        the per-change setStyleSheet / icon-refresh lag.
        """
        self._settings_apply_timer.start()

    def _apply_settings_now(self):
        """Re-apply all effect-related settings (called via debounce timer)."""
        self._apply_theme()
        self._apply_cursor()
        self._apply_font_size()
        self._apply_theme_effect()
        self._apply_trail()
        self._apply_button_anim()
        self._apply_bg_drip()
        self._apply_bg_flock()
        self._apply_bg_ambient()
        if self._click_effects is not None:
            self._click_effects.set_enabled(
                self._settings.get("click_effects_enabled", False)
            )

    def _on_first_tooltip_mode_change(self) -> None:
        """Unlock Secret Skeleton the first time the user changes the tooltip mode."""
        if not self._settings.get("unlock_skeleton", False):
            self._settings.set("unlock_skeleton", True)
            self._unlock_lbl.setText("🔓 'Secret Skeleton' theme unlocked! (Settings → Theme)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _on_first_alpha_fix(self) -> None:
        """Unlock Secret Sakura the very first time the user runs an alpha fix."""
        if not self._settings.get("unlock_sakura", False):
            self._settings.set("unlock_sakura", True)
            self._unlock_lbl.setText("🌸 'Secret Sakura' theme unlocked! (first alpha fix!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _on_first_conversion(self) -> None:
        """Unlock Sunset Beach the very first time the user converts files."""
        if not self._settings.get("unlock_sunset_beach", False):
            self._settings.set("unlock_sunset_beach", True)
            self._unlock_lbl.setText("🌅 'Sunset Beach' theme unlocked! (first conversion!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _on_first_cursor_anim_enabled(self) -> None:
        """Unlock Toxic Neon the first time the user enables cursor animation."""
        if not self._settings.get("unlock_toxic_neon", False):
            self._settings.set("unlock_toxic_neon", True)
            self._unlock_lbl.setText("☢ 'Toxic Neon' theme unlocked! (cursor animation enabled!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _on_first_theme_changed(self) -> None:
        """Unlock Candy Land the very first time the user selects a different theme."""
        if not self._settings.get("unlock_candy_land", False):
            self._settings.set("unlock_candy_land", True)
            self._unlock_lbl.setText("🍭 'Candy Land' theme unlocked! (first theme change!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _on_first_trail_enabled(self) -> None:
        """Unlock Midnight Forest the very first time the user enables the mouse trail."""
        if not self._settings.get("unlock_midnight_forest", False):
            self._settings.set("unlock_midnight_forest", True)
            self._unlock_lbl.setText("🌲 'Midnight Forest' theme unlocked! (mouse trail enabled!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _run_milestone_checks(self, total: int,
                              table: list[tuple[int, str, str]]) -> None:
        """Evaluate *table* against *total* and fire any newly reached milestones.

        Re-uses the existing unlock-notification infrastructure so milestone
        unlocks look identical to click-based ones.
        """
        newly_unlocked = False
        for threshold, key, message in table:
            if threshold > total:
                break
            if not self._settings.get(key, False):
                self._settings.set(key, True)
                self._unlock_lbl.setText(message)
                try:
                    self._sound.play_unlock()
                except Exception:
                    try:
                        QApplication.instance().beep()
                    except Exception:
                        pass
                newly_unlocked = True
        if newly_unlocked:
            self._schedule_unlock_clear()

    def _on_alpha_processing_done(self, file_count: int) -> None:
        """Track cumulative alpha fixes and check alpha-milestone unlocks."""
        if file_count <= 0:
            return
        try:
            total = self._settings.get("alpha_fixes_total", 0) + file_count
            self._settings.set("alpha_fixes_total", total)
            self._run_milestone_checks(total, self._ALPHA_MILESTONES)
        except Exception:
            pass

    def _on_conv_processing_done(self, file_count: int) -> None:
        """Track cumulative conversions and check conversion-milestone unlocks."""
        if file_count <= 0:
            return
        try:
            total = self._settings.get("conversions_total", 0) + file_count
            self._settings.set("conversions_total", total)
            self._run_milestone_checks(total, self._CONV_MILESTONES)
        except Exception:
            pass

    def _apply_trail(self):
        """Apply trail color, style, length, fade speed, intensity and enabled state."""
        if self._trail_overlay is None:
            return
        use_theme = self._settings.get("use_theme_trail", False)
        if use_theme:
            theme = self._settings.get_theme()
            color = theme.get("_trail_color", "#e94560")
            # Use the explicit _trail key added to every theme dict.
            # Fall back to the legacy _effect → style mapping for any custom
            # themes that were saved before the _trail key was introduced.
            if "_trail" in theme:
                style = theme["_trail"]
            else:
                effect = theme.get("_effect", "default")
                if effect == "fairy":
                    style = "fairy"
                elif effect in ("ocean", "mermaid", "ripple"):
                    style = "wave"
                elif effect in ("sparkle", "ice"):
                    style = "sparkle"
                else:
                    style = "dots"
        else:
            color = self._settings.get("trail_color", "#e94560")
            style = self._settings.get("trail_style", "dots")
        self._trail_overlay.set_color(color)
        self._trail_overlay.set_style(style)
        # Apply length/fade/intensity — always from user settings regardless of theme trail
        self._trail_overlay.set_length(int(self._settings.get("trail_length", 50)))
        self._trail_overlay.set_fade_speed(int(self._settings.get("trail_fade_speed", 5)))
        self._trail_overlay.set_intensity(int(self._settings.get("trail_intensity", 100)))
        self._trail_overlay.set_enabled(
            self._settings.get("trail_enabled", False)
        )

    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings", "alpha_fixer_settings.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self._settings.export_settings(path)
            QMessageBox.information(self, "Export Settings",
                                    f"Settings exported to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _import_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Settings", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            keys = self._settings.import_settings(path)
            self._on_settings_changed()
            QMessageBox.information(
                self, "Import Settings",
                f"Imported {len(keys)} settings from:\n{path}\n\n"
                "Restart the app to fully apply all changes.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", str(exc))

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def _show_shortcuts(self):
        from PyQt6.QtWidgets import QDialog, QScrollArea, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("⌨  Keyboard Shortcuts")
        dlg.setMinimumSize(540, 460)
        dlg.setSizeGripEnabled(True)
        # Open at a comfortable initial size, centered on the parent window
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            init_w = max(640, min(800, int(avail.width() * 0.5)))
            init_h = max(540, min(700, int(avail.height() * 0.65)))
        else:
            init_w, init_h = 640, 540
        dlg.resize(init_w, init_h)
        dlg.move(
            self.x() + (self.width() - init_w) // 2,
            self.y() + (self.height() - init_h) // 2,
        )

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(12, 12, 12, 8)
        outer.setSpacing(8)

        content_lbl = QLabel(
            "<h3>Global Shortcuts</h3>"
            "<table cellpadding='4'>"
            "<tr><td><b>F1</b></td><td>Open this Keyboard Shortcuts help window</td></tr>"
            "<tr><td><b>Ctrl+,</b></td><td>Open Settings (themes, effects, UI scale, history)</td></tr>"
            "<tr><td><b>Ctrl+Q</b></td><td>Quit the application</td></tr>"
            "<tr><td><b>Ctrl+1</b></td><td>Switch to the Alpha &amp; RGBA Adjuster tab</td></tr>"
            "<tr><td><b>Ctrl+2</b></td><td>Switch to the File Converter tab</td></tr>"
            "<tr><td><b>Ctrl+3</b></td><td>Switch to the Processing History tab</td></tr>"
            "<tr><td><b>Ctrl+4</b></td><td>Switch to the Selective Alpha Tool tab</td></tr>"
            "</table>"

            "<h3>Alpha &amp; RGBA Adjuster &amp; File Converter</h3>"
            "<table cellpadding='4'>"
            "<tr><td><b>F5</b></td><td>Start processing / conversion batch</td></tr>"
            "<tr><td><b>Esc</b></td><td>Stop the current processing operation</td></tr>"
            "<tr><td><b>Ctrl+O</b></td><td>Add image files to the queue</td></tr>"
            "<tr><td><b>Ctrl+Shift+O</b></td><td>Add an entire folder (all supported images) to the queue</td></tr>"
            "<tr><td><b>Delete</b></td><td>Remove the selected file(s) from the queue</td></tr>"
            "<tr><td><b>Ctrl+A</b></td><td>Select all files in the queue</td></tr>"
            "</table>"

            "<h3>Selective Alpha Tool  (Canvas)</h3>"
            "<table cellpadding='4'>"
            "<tr><td><b>Ctrl+O</b></td><td>Open an image to edit</td></tr>"
            "<tr><td><b>Ctrl+Z</b></td><td>Undo the last paint / erase stroke</td></tr>"
            "<tr><td><b>Ctrl+Y</b> or <b>Ctrl+Shift+Z</b></td><td>Redo the last undone stroke</td></tr>"
            "<tr><td><b>Ctrl+Enter</b></td><td>Apply alpha zones to the image (generate result)</td></tr>"
            "<tr><td><b>Ctrl+S</b></td><td>Save the processed result to disk</td></tr>"
            "<tr><td><b>Ctrl+Wheel</b></td><td>Zoom in / out on the canvas</td></tr>"
            "<tr><td><b>Middle-mouse drag</b></td><td>Pan around the canvas (hold scroll wheel and drag)</td></tr>"
            "<tr><td><b>Alt + Left-drag</b></td><td>Pan the canvas (alternative to middle-mouse)</td></tr>"
            "</table>"
            "<h3>Selective Alpha Tool  (Drawing Tools)</h3>"
            "<table cellpadding='4'>"
            "<tr><td><b>B</b></td><td>Brush tool — freehand paint alpha zones</td></tr>"
            "<tr><td><b>E</b></td><td>Eraser tool — erase painted zones</td></tr>"
            "<tr><td><b>L</b></td><td>Line tool — draw straight lines</td></tr>"
            "<tr><td><b>R</b></td><td>Rectangle tool — fill a rectangular region</td></tr>"
            "<tr><td><b>X</b></td><td>Ellipse tool — fill an elliptical region</td></tr>"
            "<tr><td><b>F</b></td><td>Fill tool — flood-fill a connected region</td></tr>"
            "<tr><td><b>P</b></td><td>Polygon tool — draw a closed polygon</td></tr>"
            "<tr><td><b>T</b></td><td>Transform tool — move and scale painted zones</td></tr>"
            "<tr><td><b>[</b> / <b>]</b></td><td>Decrease / increase brush size</td></tr>"
            "</table>"

            "<h3>GIF Builder</h3>"
            "<table cellpadding='4'>"
            "<tr><td><b>Space</b></td><td>Play / Pause the GIF preview</td></tr>"
            "<tr><td><b>Ctrl+S</b></td><td>Export the composed GIF to a file</td></tr>"
            "</table>"

            "<h3>Video Editor</h3>"
            "<table cellpadding='4'>"
            "<tr><td><b>Space</b></td><td>Play / Pause the video preview</td></tr>"
            "<tr><td><b>Ctrl+S</b></td><td>Export the video to a file</td></tr>"
            "<tr><td><b>Delete</b></td><td>Remove the selected clip from the clip list</td></tr>"
            "</table>"

            "<h3>Quick Access (Right-Click)</h3>"
            "<table cellpadding='4'>"
            "<tr><td><b>Right-click window</b></td><td>Open GIF Builder, Video Editor, or Settings directly</td></tr>"
            "</table>"
        )
        content_lbl.setWordWrap(True)
        content_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        from PyQt6.QtWidgets import QSizePolicy
        content_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setWidget(content_lbl)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        outer.addWidget(buttons)

        dlg.exec()

    def _show_about(self):
        from PyQt6.QtWidgets import QDialog, QScrollArea, QDialogButtonBox, QSizePolicy
        from PyQt6.QtCore import QSize

        dlg = QDialog(self)
        dlg.setWindowTitle("About 🐼 Alpha & RGBA Adjuster  |  File Converter")
        dlg.setSizeGripEnabled(True)
        # Open at a comfortable initial size, centered on the parent window
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            init_w = max(520, min(800, int(avail.width() * 0.5)))
            init_h = max(420, min(700, int(avail.height() * 0.55)))
        else:
            init_w, init_h = 580, 460
        # Minimum size: 40% of screen to avoid dialog being inaccessibly small,
        # but cap below the initial size so the grip actually allows shrinking.
        if screen is not None:
            avail = screen.availableGeometry()
            min_w = max(360, min(init_w, int(avail.width() * 0.35)))
            min_h = max(280, min(init_h, int(avail.height() * 0.35)))
        else:
            min_w, min_h = 360, 280
        dlg.setMinimumSize(min_w, min_h)
        dlg.resize(init_w, init_h)
        dlg.move(
            self.x() + (self.width() - init_w) // 2,
            self.y() + (self.height() - init_h) // 2,
        )

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(12, 12, 12, 8)
        outer.setSpacing(8)

        content_lbl = QLabel(
            f"<h2>🐼 Alpha &amp; RGBA Adjuster  |  File Converter  v{__version__}</h2>"
            "<p>A powerful panda-themed tool for fixing alpha channels, converting image "
            "files, and painting custom alpha zones.</p>"
            "<ul>"
            "<li><b>Alpha &amp; RGBA Adjuster:</b> PS2, N64, No Alpha, Max Alpha presets + "
            "custom fine-tune (set / multiply / add / subtract), per-channel RGBA ±255 adjustments</li>"
            "<li><b>File Converter:</b> PNG, DDS, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF, AVIF, "
            "QOI and more — batch folder processing with live before/after preview</li>"
            "<li><b>Selective Alpha Tool:</b> paint alpha zones on images (up to 40 zones), "
            "freehand / line / rectangle / ellipse / fill / polygon / eraser / transform tools, "
            "keyboard shortcuts (B/E/L/R/X/F/P/T), copy/paste zones, clipboard slots</li>"
            "<li><b>GIF Builder &amp; Video Editor:</b> create animated GIFs and videos from "
            "images, GIFs, and video clips with drag-based reordering and trim controls</li>"
            "<li>Processing history with search, filter, and multi-format export</li>"
            "<li>50+ preset themes + hidden unlockable themes (keep clicking!)</li>"
            "<li>21+ click effects: Gore 🩸, Bat Cave 🦇, Rainbow 🌈, Galaxy ✦, Neon ⚡, "
            "Fire 🔥, Ice ❄, Panda 🐼, Sakura 🌸, Ocean 🌊, Mermaid 🧜, Alien 🛸, "
            "Shark 🦈, and more…</li>"
            "<li>14 mouse trail styles: Dots, Ribbon 🎀, Noodle 🍜, Comet, Fairy ✨, "
            "Wave 🌊, Sparkle ❄, Rainbow 🌈, Distortion, Fire 🔥, Lightning ⚡, "
            "Plasma, Sakura 🌸, Smoke</li>"
            "<li>Background ambient effects (12 styles with 'Use theme ambient' auto-matching), "
            "background flock with themed emoji (Use theme flock), blood/water drip</li>"
            "<li>Button press animations: press, fall, bounce, shake, shatter</li>"
            "<li>Animated banner (10 modes) + animated emoji cursors: spin, wobble, cycling</li>"
            "<li>Cycling tooltips with Normal, Dumbed Down, and No Filter 🤬 modes</li>"
            "<li>Keyboard shortcuts: F5 run · Esc stop · Ctrl+O add files · "
            "Ctrl+1/2/3/4 switch tabs · F1 shortcuts</li>"
            "</ul>"
            "<p>Built with <b>Python + PyQt6 + Pillow</b>.</p>"
            f'<p><a href="{PATREON_URL}">❤ Support on Patreon</a></p>'
        )
        content_lbl.setWordWrap(True)
        content_lbl.setOpenExternalLinks(True)
        content_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        content_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setWidget(content_lbl)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        outer.addWidget(buttons)

        dlg.exec()

    def _open_patreon(self):
        webbrowser.open(PATREON_URL)
        # First Patreon visit unlocks Rose Gold as a thank-you.
        if not self._settings.get("unlock_rose_gold", False):
            self._settings.set("unlock_rose_gold", True)
            self._unlock_lbl.setText("🌹 'Rose Gold' theme unlocked! (thanks for supporting!)")
            try:
                self._sound.play_unlock()
            except Exception:
                pass
            self._schedule_unlock_clear()

    def _show_help_menu(self):
        """Show a popup menu from the Help button with shortcuts, about, and I/O options."""
        menu = QMenu(self)
        act_shortcuts = menu.addAction("⌨  Keyboard Shortcuts  (F1)")
        act_shortcuts.setToolTip("View all keyboard shortcuts for every tool (also F1).")
        act_shortcuts.setStatusTip("View keyboard shortcuts for all tools.")
        act_shortcuts.triggered.connect(self._show_shortcuts)
        act_about = menu.addAction("ℹ  About")
        act_about.setToolTip("Show the About dialog with app version and feature list.")
        act_about.setStatusTip("About this application – version and features.")
        act_about.triggered.connect(self._show_about)
        menu.addSeparator()
        act_patreon = menu.addAction("❤  Support on Patreon…")
        act_patreon.setToolTip("Open the Patreon page to support development.")
        act_patreon.setStatusTip("Support the developer on Patreon.")
        act_patreon.triggered.connect(self._open_patreon)
        menu.addSeparator()
        act_export = menu.addAction("📤  Export Settings…")
        act_export.setToolTip(
            "Save all current settings to a JSON file for backup or transfer."
        )
        act_export.setStatusTip("Export all settings to a JSON backup file.")
        act_export.triggered.connect(self._export_settings)
        act_import = menu.addAction("📥  Import Settings…")
        act_import.setToolTip(
            "Restore settings from a previously exported JSON file."
        )
        act_import.setStatusTip("Import settings from a JSON backup file.")
        act_import.triggered.connect(self._import_settings)
        # Show the menu just below the Help button
        btn = self._btn_help
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        menu.exec(pos)

    def contextMenuEvent(self, event) -> None:
        """Right-click anywhere on the main window to access the tool shortcuts."""
        from .gif_builder import GifBuilderDialog
        from .video_tool import VideoToolDialog
        menu = QMenu(self)
        act_gif = menu.addAction("🎞  Open GIF Builder")
        act_gif.setToolTip(
            "Open the GIF Builder to compose animated GIFs from any images."
        )
        act_video = menu.addAction("🎬  Open Video Editor")
        act_video.setToolTip(
            "Open the Video Editor to merge, trim and adjust video clips."
        )
        menu.addSeparator()
        act_settings = menu.addAction("⚙  Settings")
        act_settings.triggered.connect(self._open_settings)
        chosen = menu.exec(event.globalPos())
        if chosen is act_gif:
            if not hasattr(self, "_gif_builder_dlg") or self._gif_builder_dlg is None:
                self._gif_builder_dlg = GifBuilderDialog(parent=self)
            self._gif_builder_dlg.show()
            self._gif_builder_dlg.raise_()
            self._gif_builder_dlg.activateWindow()
        elif chosen is act_video:
            if not hasattr(self, "_video_tool_dlg") or self._video_tool_dlg is None:
                self._video_tool_dlg = VideoToolDialog(parent=self)
            self._video_tool_dlg.show()
            self._video_tool_dlg.raise_()
            self._video_tool_dlg.activateWindow()
        event.accept()

    def changeEvent(self, event: "QEvent") -> None:
        """Handle runtime display/DPI changes and minimize/restore events.

        Qt fires ``QEvent.Type.ScreenChangeInternal`` whenever:
        • The window is dragged to a monitor with a different device-pixel ratio
        • The user changes the system display-scale setting (e.g. 100 % → 150 %)
        • Windows sends a WM_DPICHANGED message (per-monitor DPI awareness)

        In response we:
        1. Recalculate the adaptive minimum size for the new screen's geometry.
        2. Clamp the window so it remains visible and fits within the new area.
        3. Re-apply the saved font size so point-size metrics are correct on
           the new display (the font family/size stays the same, but Qt must
           recalculate layout metrics after a DPI change).

        We also pause background visual effects when the window is minimised
        and resume them when it is restored to prevent timers stacking up
        while the window is hidden (which can cause lag/crash on restore).
        """
        super().changeEvent(event)
        if event.type() == _SCREEN_CHANGE_INTERNAL:
            # Defer slightly so Qt has updated screen/geometry data first.
            QTimer.singleShot(150, self._update_minimum_size)
            QTimer.singleShot(150, self._clamp_to_screen)
            QTimer.singleShot(150, self._apply_font_size)
        elif event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._pause_visual_effects()
            elif not self.isMinimized():
                # Defer resume briefly so Qt has finished compositing the window
                QTimer.singleShot(200, self._resume_visual_effects)

    def _pause_visual_effects(self) -> None:
        """Stop all background visual effect timers while the window is minimised."""
        if self._click_effects is not None:
            try:
                self._click_effects.pause_for_minimize()
            except Exception:
                pass
        if self._trail_overlay is not None:
            try:
                # Clear the trail buffer so it doesn't replay stale points.
                self._trail_overlay._trail.clear()
                self._trail_overlay._timer.stop()
                self._trail_overlay._enabled = False
                self._trail_overlay.hide()
            except Exception:
                pass

    def _resume_visual_effects(self) -> None:
        """Re-apply all visual effects after the window is restored from minimise.

        Re-applying from settings is safer than resuming timers directly because
        it guarantees only the currently-enabled effects are started and prevents
        timers from accumulating from multiple pause/resume cycles.
        Since _pause_visual_effects sets _enabled=False on both overlays, the
        set_enabled(True) calls inside _apply_settings_now will trigger a real
        state transition that restarts timers, event filters, and sub-timers.
        """
        if self.isMinimized():
            return  # another minimise happened before the timer fired
        # _apply_settings_now re-enables everything that was active before the pause.
        QTimer.singleShot(0, self._apply_settings_now)

    def _reposition_overlays(self) -> None:
        """Reposition both overlays to fill the window after a resize burst."""
        if self._trail_overlay is not None:
            self._trail_overlay.setGeometry(self.rect())
            self._trail_overlay.raise_()
        if self._click_effects is not None:
            self._click_effects.setGeometry(self.rect())
            self._click_effects.raise_()
        # Keep any visible easter-egg collectibles on-screen
        for collectible in self._easter_collectibles.values():
            if collectible.isVisible():
                x = max(0, min(self.width()  - collectible.width(),  collectible.x()))
                y = max(0, min(self.height() - collectible.height(), collectible.y()))
                collectible.move(x, y)
                collectible.raise_()

    # ------------------------------------------------------------------
    # Whole-window drag-and-drop (item 28)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        """Accept file drops anywhere on the main window."""
        mime = event.mimeData()
        if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        """Keep accepting moves while the user drags over the window."""
        mime = event.mimeData()
        if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        """Route dropped files to the appropriate tool.

        If the active tab is Alpha & RGBA Adjuster or Converter the files go
        there directly.  If the active tab is Settings or History (tabs 2/3 in
        the tab widget) a small dialog asks which tool to use.
        Selective Alpha always asks because it handles only single images.
        """
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()

        active_widget = self._tabs.currentWidget()
        # For Alpha / Converter tabs, route directly
        if active_widget is self._alpha_tab:
            self._alpha_tab._add_to_list(paths)
            return
        if active_widget is self._converter_tab:
            self._converter_tab._add_to_list(paths)
            return

        # For History, Settings, Selective Alpha tabs → ask the user
        from PyQt6.QtWidgets import QInputDialog
        choices = ["Alpha & RGBA Adjuster", "Converter"]
        choice, ok = QInputDialog.getItem(
            self,
            "Open With…",
            f"You dropped {len(paths)} file(s) on the {self._tabs.tabText(self._tabs.currentIndex())} tab.\n"
            "Which tool should receive the file(s)?",
            choices, 0, False,
        )
        if not ok:
            return
        if choice == "Alpha & RGBA Adjuster":
            self._tabs.setCurrentWidget(self._alpha_tab)
            self._alpha_tab._add_to_list(paths)
        else:
            self._tabs.setCurrentWidget(self._converter_tab)
            self._converter_tab._add_to_list(paths)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        # Remove the global keyboard-secret event filter so it cannot fire
        # against partially-torn-down widgets after close begins.
        if self._key_secret_filter is not None:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self._key_secret_filter)
        # Disable overlays first so their event filters are unregistered and
        # their internal timers (animation, bat/fairy flock, etc.) are stopped
        # before any Qt objects start being torn down.
        if self._click_effects is not None:
            self._click_effects.set_enabled(False)
        if self._trail_overlay is not None:
            self._trail_overlay.set_enabled(False)
        if self._button_anim is not None:
            self._button_anim.set_enabled(False)
        # Stop any running workers gracefully
        for tab in (self._alpha_tab, self._converter_tab):
            if hasattr(tab, "_worker") and tab._worker and tab._worker.isRunning():
                tab._worker.stop()
                # Allow up to 15 seconds for an in-flight batch to finish its
                # current file so it can reach the abort check.  3 seconds was
                # too short when processing large images or slow storage.
                tab._worker.wait(15000)
            # Cancel any in-flight preview loaders so their threads don't
            # try to emit signals into already-destroyed Qt objects.
            if hasattr(tab, "_preview_loader") and tab._preview_loader is not None:
                tab._preview_loader.stop()
                # Wait for the preview thread to finish so it cannot emit into
                # widgets that are being torn down below.
                tab._preview_loader.wait(3000)
            # Stop preview debounce timers so pending timeouts don't fire
            # after the tab widgets have been torn down.
            if hasattr(tab, "_preview_debounce") and tab._preview_debounce is not None:
                tab._preview_debounce.stop()
        # Drain the global QThreadPool (used by thumbnail loaders in the drop
        # lists).  Without this, runnables that are still running when Qt
        # starts tearing down widgets may emit signals to deleted objects and
        # crash.  We cancel all pending runnables first via the cancel events
        # already held by each DropFileList, then give the pool 3 seconds to
        # let any already-running runnable reach its own cancel check.
        try:
            from .drop_list import DropFileList
            from PyQt6.QtCore import QThreadPool
            for tab in (self._alpha_tab, self._converter_tab):
                for attr in ("_file_list", "_drop_list"):
                    widget = getattr(tab, attr, None)
                    if isinstance(widget, DropFileList):
                        widget._cancel_event.set()
            QThreadPool.globalInstance().waitForDone(3000)
        except Exception:
            pass
        # Stop main-window timers before the window is destroyed
        for timer in (
            self._settings_apply_timer,
            self._resize_timer,
            self._unlock_timer,
            self._anim_timer,
            self._cursor_anim_timer,
        ):
            if timer is not None:
                timer.stop()
        # Clean up temp sound file
        if self._sound is not None:
            self._sound.cleanup()
        self._save_geometry()
        # Flush any buffered QSettings writes to disk before closing.
        # This is the one place we explicitly sync since set() no longer
        # calls sync() after every write (which caused per-click disk I/O).
        # Save Selective Alpha Tool state first so it is included in the sync.
        try:
            self._selective_alpha_tab._save_settings()
        except Exception:
            pass
        # Release PIL images held by the Selective Alpha tab (source image,
        # masks, result images).  closeEvent on embedded widgets is never
        # triggered by Qt during application shutdown, so we invoke it
        # explicitly here to ensure deterministic resource cleanup.
        try:
            self._selective_alpha_tab.closeEvent(event)
        except Exception:
            pass
        try:
            self._settings.sync()
        except Exception:
            pass
        super().closeEvent(event)

