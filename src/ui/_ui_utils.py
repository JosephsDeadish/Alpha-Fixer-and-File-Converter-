"""
Shared UI helper utilities used by multiple tool widgets.

Qt helpers import lazily so this module can be imported early without
pulling in heavy Qt or Pillow dependencies.
"""
from src.core.atomic_output import staged_output_path


def reserve_space_for_focused_control(dialog, event):
    """Let focused buttons and text editors handle Space before preview shortcuts."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtWidgets import (
        QApplication, QAbstractButton, QLineEdit, QTextEdit, QPlainTextEdit,
        QAbstractSpinBox, QComboBox,
    )

    if (event.type() != QEvent.Type.ShortcutOverride
            or event.key() != Qt.Key.Key_Space
            or event.modifiers() != Qt.KeyboardModifier.NoModifier):
        return False
    focused = QApplication.focusWidget()
    if (focused is not None and dialog.isAncestorOf(focused)
            and isinstance(focused, (
                QAbstractButton, QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox,
            ))):
        event.accept()
        return True
    return False


def set_status_tone(label, tone: str) -> None:
    """Refresh theme styling after a status label changes semantic tone."""
    from PyQt6.QtCore import QCoreApplication, QEvent

    tone = tone if tone in ("neutral", "success", "warning", "error") else "neutral"
    if label.property("statusTone") == tone:
        return
    label.setProperty("statusTone", tone)
    label.style().unpolish(label)
    label.style().polish(label)
    QCoreApplication.sendEvent(label, QEvent(QEvent.Type.StyleChange))
    label.updateGeometry()
    label.update()


def confirm_normalized_save_path(parent, chosen_path, final_path):
    """Confirm an existing destination missed by the native save dialog."""
    from pathlib import Path
    from PyQt6.QtWidgets import QMessageBox

    if final_path == chosen_path or not Path(final_path).exists():
        return True
    reply = QMessageBox.question(
        parent, "Replace Existing File?",
        f"The final output already exists:\n{final_path}\n\nReplace it?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


from PyQt6.QtWidgets import QScrollArea


class DialogContentScrollArea(QScrollArea):
    """Scroll area that keeps ensureWidgetVisible from overcorrecting dialogs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ensureWidgetVisible = self._ensure_widget_visible_safe

    def _ensure_widget_visible_safe(self, child=None, xmargin=50, ymargin=50):
        from PyQt6.QtCore import QPoint

        if child is None:
            return super().ensureWidgetVisible(child, xmargin, ymargin)
        content = self.widget()
        if content is None or not child.isWidgetType():
            return super().ensureWidgetVisible(child, xmargin, ymargin)
        if child is content:
            return super().ensureWidgetVisible(child, xmargin, ymargin)
        viewport = self.viewport()
        center = child.mapTo(viewport, child.rect().center())
        if viewport.rect().contains(center):
            return
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        if center.x() < 0 or center.x() > viewport.width():
            target_x = hbar.value() + center.x() - viewport.width() // 2
            hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), target_x)))
        if center.y() < 0 or center.y() > viewport.height():
            target_y = vbar.value() + center.y() - viewport.height() // 2
            vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), target_y)))


def scrollable_dialog_layout(dialog):
    """Keep large tool layouts reachable without forcing the window off-screen."""
    from PyQt6.QtWidgets import QLayout, QVBoxLayout, QWidget

    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(8, 8, 8, 8)
    outer.setSpacing(6)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    scroll = DialogContentScrollArea(dialog)
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


def batch_completion_summary(success: int, errors: int, total: int, stopped: bool) -> tuple[int, str]:
    """Keep cancelled batch feedback distinct from a fully completed run."""
    processed = success + errors
    remaining = max(0, total - processed)
    interrupted = stopped or remaining > 0
    progress = min(100, int(100 * processed / total)) if total > 0 else (0 if stopped else 100)
    status = f"{'Stopped' if interrupted else 'Done'}. ✔ {success} succeeded, ✘ {errors} failed."
    if remaining:
        status += f" {remaining} not processed."
    return progress, status


def verified_originals(outputs: dict[str, str]) -> list[str]:
    """Only offer originals with existing outputs that are not originals themselves."""
    from collections import Counter
    from pathlib import Path

    try:
        output_paths = {Path(dest).resolve() for dest in outputs.values()}
        output_stats = [Path(dest).stat() for dest in outputs.values() if Path(dest).is_file()]
        output_ids = Counter((stat.st_dev, stat.st_ino) for stat in output_stats)
    except (OSError, RuntimeError):
        return []
    originals = []
    for source, dest in outputs.items():
        try:
            if not Path(source).is_file() or not Path(dest).is_file():
                continue
            source_stat = Path(source).stat()
            dest_stat = Path(dest).stat()
            if (Path(source).resolve() not in output_paths
                    and (source_stat.st_dev, source_stat.st_ino) not in output_ids
                    and output_ids[(dest_stat.st_dev, dest_stat.st_ino)] == 1):
                originals.append(source)
        except (OSError, RuntimeError):
            continue
    return originals


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
