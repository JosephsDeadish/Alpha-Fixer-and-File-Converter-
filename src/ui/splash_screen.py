"""
Animated themed startup splash screen.

Displays a 2.5-second splash with the active theme's banner SVG,
app name, and a smooth loading progress bar, then fades out.
"""
from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QFont, QFontMetrics, QRadialGradient, QLinearGradient, QPen
from PyQt6.QtWidgets import QSplashScreen, QApplication
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtCore import QByteArray


# Total display time before the splash auto-closes (ms)
_SPLASH_DURATION_MS = 2800
# How many ticks the progress bar takes to fill
_PROGRESS_TICKS = 56
_PROGRESS_INTERVAL_MS = _SPLASH_DURATION_MS // _PROGRESS_TICKS


class ThemeSplashScreen(QSplashScreen):
    """Animated themed splash screen shown at startup."""

    WIDTH  = 620
    HEIGHT = 300

    def __init__(self, settings) -> None:
        # Build a blank pixmap – we paint everything ourselves
        from PyQt6.QtGui import QPixmap
        pix = QPixmap(self.WIDTH, self.HEIGHT)
        pix.fill(Qt.GlobalColor.transparent)
        super().__init__(pix)

        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

        self._settings = settings
        self._progress = 0          # 0–100
        self._alpha_val = 255       # window opacity for fade-in/out
        self._anim_frame = 0        # banner text animation frame
        self._dots = ""             # loading dots "." / ".." / "..."

        # Resolve current theme colours
        theme = settings.get_theme() if settings else {}
        self._bg      = QColor(theme.get("background", "#1a1a2e"))
        self._surface = QColor(theme.get("surface",    "#16213e"))
        self._accent  = QColor(theme.get("accent",     "#0f3460"))
        self._text    = QColor(theme.get("text",       "#eaeaea"))
        self._bar_col = QColor(theme.get("progress_bar", theme.get("accent", "#e94560")))
        self._theme_name = theme.get("name", "Panda Dark") if theme else "Panda Dark"

        # Banner text for the theme — use animated frames if available
        from .theme_engine import get_theme_banner, get_theme_banner_frames
        self._banner_frames = get_theme_banner_frames(self._theme_name)
        self._banner_frame_idx = 0
        self._banner = self._banner_frames[0]

        # Try to load the theme's SVG
        self._svg_widget: Optional[QSvgWidget] = None
        self._load_svg(theme)

        # Progress timer
        self._prog_timer = QTimer(self)
        self._prog_timer.setInterval(_PROGRESS_INTERVAL_MS)
        self._prog_timer.timeout.connect(self._tick_progress)
        self._prog_timer.start()

        # Animation frame timer (dots + banner wiggle)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(200)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.start()

        # Fade-in timer
        self._fade_in_timer = QTimer(self)
        self._fade_in_timer.setInterval(30)
        self._fade_in_timer.timeout.connect(self._tick_fade_in)
        self._alpha_val = 0
        self._fade_in_timer.start()

        self._do_initial_draw()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_svg(self, theme: dict) -> None:
        from .theme_engine import get_theme_svg_path
        path = get_theme_svg_path(self._theme_name)
        if not path:
            return
        try:
            self._svg_widget = QSvgWidget(path, self)
            self._svg_widget.setGeometry(self.WIDTH - 200, 10, 190, 190)
            self._svg_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self._svg_widget.show()
        except Exception:
            self._svg_widget = None

    def _do_initial_draw(self) -> None:
        self._repaint_pixmap()
        self.setWindowOpacity(0.0)

    def _repaint_pixmap(self) -> None:
        from PyQt6.QtGui import QPixmap
        pix = QPixmap(self.WIDTH, self.HEIGHT)
        pix.fill(Qt.GlobalColor.transparent)
        self._paint_background(pix)
        self.setPixmap(pix)
        self.update()

    def _paint_background(self, pix) -> None:
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        W, H = self.WIDTH, self.HEIGHT

        # Rounded background
        path = QPainterPath()
        path.addRoundedRect(0, 0, W, H, 18, 18)

        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, self._bg)
        grad.setColorAt(1.0, self._surface)
        p.fillPath(path, grad)

        # Thin accent border
        pen = QPen(self._accent, 2)
        p.setPen(pen)
        p.drawPath(path)

        # Glowing radial hint in top-right corner
        rg = QRadialGradient(W - 80, 60, 120)
        glow = QColor(self._accent)
        glow.setAlpha(40)
        rg.setColorAt(0.0, glow)
        rg.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillPath(path, rg)

        # Subtle animated shimmer dots
        dot_col = QColor(self._accent)
        dot_col.setAlpha(30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot_col)
        for i in range(6):
            cx = int(30 + i * 60 + math.sin(self._anim_frame * 0.4 + i) * 5)
            cy = int(H - 60 + math.cos(self._anim_frame * 0.3 + i * 0.7) * 4)
            p.drawEllipse(cx - 3, cy - 3, 6, 6)

        # App name
        title_font = QFont("Segoe UI", 22, QFont.Weight.Bold)
        p.setFont(title_font)
        p.setPen(self._text)
        p.drawText(QRect(30, 28, W - 240, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Alpha Fixer & File Converter")

        # Theme banner / subtitle
        sub_font = QFont("Segoe UI", 11)
        p.setFont(sub_font)
        sub_col = QColor(self._accent)
        sub_col.setAlpha(220)
        p.setPen(sub_col)
        banner_y = 72
        p.drawText(QRect(30, banner_y, W - 240, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._banner)

        # Separator line
        sep_col = QColor(self._accent)
        sep_col.setAlpha(60)
        p.setPen(QPen(sep_col, 1))
        p.drawLine(30, 105, W - 30, 105)

        # Loading dots label
        msg_font = QFont("Segoe UI", 10)
        p.setFont(msg_font)
        msg_col = QColor(self._text)
        msg_col.setAlpha(160)
        p.setPen(msg_col)
        p.drawText(QRect(30, H - 60, 250, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"Loading{self._dots}")

        # Progress bar track
        bar_x, bar_y, bar_w, bar_h = 30, H - 36, W - 60, 8
        track_col = QColor(self._accent)
        track_col.setAlpha(40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track_col)
        p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)

        # Progress bar fill
        filled_w = int(bar_w * self._progress / 100)
        if filled_w > 0:
            bar_grad = QLinearGradient(bar_x, 0, bar_x + filled_w, 0)
            bar_grad.setColorAt(0.0, QColor(self._bar_col).lighter(130))
            bar_grad.setColorAt(1.0, self._bar_col)
            p.setBrush(bar_grad)
            p.drawRoundedRect(bar_x, bar_y, filled_w, bar_h, 4, 4)

        # Percentage text
        pct_font = QFont("Segoe UI", 9)
        p.setFont(pct_font)
        pct_col = QColor(self._text)
        pct_col.setAlpha(120)
        p.setPen(pct_col)
        p.drawText(QRect(bar_x + bar_w - 50, H - 58, 50, 20),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._progress}%")

        p.end()

    # ------------------------------------------------------------------
    # Timer slots
    # ------------------------------------------------------------------

    def _tick_fade_in(self) -> None:
        self._alpha_val = min(255, self._alpha_val + 18)
        self.setWindowOpacity(self._alpha_val / 255.0)
        if self._alpha_val >= 255:
            self._fade_in_timer.stop()

    def _tick_progress(self) -> None:
        self._progress = min(100, self._progress + (100 // _PROGRESS_TICKS) + 1)
        self._repaint_pixmap()
        if self._progress >= 100:
            self._prog_timer.stop()

    def _tick_anim(self) -> None:
        self._anim_frame += 1
        # Cycle loading dots
        dots_cycle = ["", ".", "..", "..."]
        self._dots = dots_cycle[self._anim_frame % len(dots_cycle)]
        # Cycle banner frames (advance every 4 anim ticks → ~800ms)
        if len(self._banner_frames) > 1 and self._anim_frame % 4 == 0:
            self._banner_frame_idx = (self._banner_frame_idx + 1) % len(self._banner_frames)
            self._banner = self._banner_frames[self._banner_frame_idx]
        self._repaint_pixmap()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def finish_and_close(self, main_window) -> None:
        """Fade out and then call finish(main_window)."""
        self._prog_timer.stop()
        self._anim_timer.stop()
        self._progress = 100
        self._dots = ""
        self._repaint_pixmap()

        self._fade_out_step = 0

        def _fade_out() -> None:
            self._fade_out_step += 1
            opacity = max(0.0, 1.0 - self._fade_out_step / 10.0)
            self.setWindowOpacity(opacity)
            if self._fade_out_step >= 10:
                _fade_timer.stop()
                self.finish(main_window)

        _fade_timer = QTimer(self)
        _fade_timer.setInterval(30)
        _fade_timer.timeout.connect(_fade_out)
        _fade_timer.start()
