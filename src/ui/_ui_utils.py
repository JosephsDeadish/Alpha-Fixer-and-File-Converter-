"""
Shared UI helper utilities used by multiple tool widgets.

Qt helpers import lazily so this module can be imported early without
pulling in heavy Qt or Pillow dependencies.
"""


def scrollable_dialog_layout(dialog):
    """Keep large tool layouts reachable without forcing the window off-screen."""
    from PyQt6.QtWidgets import QLayout, QScrollArea, QVBoxLayout, QWidget

    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(8, 8, 8, 8)
    outer.setSpacing(6)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    scroll = QScrollArea(dialog)
    scroll.setObjectName("dialogContentScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(content)
    outer.addWidget(scroll)
    return layout


def fit_dialog_to_screen(dialog):
    """Clamp the decorated window to its current screen's usable area."""
    from PyQt6.QtCore import QPoint

    screen = dialog.screen()
    if screen is None:
        return
    available = screen.availableGeometry()
    frame = dialog.frameGeometry()
    border_w = max(0, frame.width() - dialog.width())
    border_h = max(0, frame.height() - dialog.height())
    safe_w = max(1, available.width() - border_w - 16)
    safe_h = max(1, available.height() - border_h - 16)
    dialog.setMinimumSize(
        min(dialog.minimumWidth(), safe_w),
        min(dialog.minimumHeight(), safe_h),
    )
    dialog.resize(min(dialog.width(), safe_w), min(dialog.height(), safe_h))
    frame = dialog.frameGeometry()
    x = max(available.left() + 8, min(frame.x(), available.right() - frame.width() - 7))
    y = max(available.top() + 8, min(frame.y(), available.bottom() - frame.height() - 7))
    dialog.move(dialog.pos() + QPoint(x - frame.x(), y - frame.y()))


def format_eta(current: int, total: int, elapsed: float, threshold: int = 500) -> str:
    """Return an ETA string for a batch progress update, or '' if not shown.

    The ETA is only shown when the batch is large enough (*total* ≥ *threshold*)
    and enough time has passed to produce a meaningful rate estimate (elapsed > 1 s).

    Parameters
    ----------
    current:   number of items completed so far (0-based index).
    total:     total number of items in the batch.
    elapsed:   wall-clock seconds since the batch started.
    threshold: minimum batch size before an ETA is shown (default: 500).

    Returns
    -------
    str: "  ETA ~Xm YYs" or "  ETA ~Xs" when applicable, otherwise "".
    """
    if total < threshold or current <= 0 or elapsed <= 1.0:
        return ""
    rate = current / elapsed          # items per second
    if rate <= 0:
        return ""
    eta_secs = int(max(0, total - current) / rate)
    if eta_secs >= 60:
        return f"  ETA ~{eta_secs // 60}m {eta_secs % 60:02d}s"
    return f"  ETA ~{eta_secs}s"
