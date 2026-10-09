"""
Shared UI helper utilities used by multiple tool widgets.

Qt helpers import lazily so this module can be imported early without
pulling in heavy Qt or Pillow dependencies.
"""
from contextlib import contextmanager


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


@contextmanager
def staged_output_path(destination):
    """Replace a destination only after successfully writing a sibling file."""
    import os
    from pathlib import Path
    import tempfile

    path = Path(destination)
    handle = tempfile.NamedTemporaryFile(
        prefix=".alpha_fixer_save_", suffix=path.suffix,
        dir=str(path.absolute().parent), delete=False,
    )
    staged = Path(handle.name)
    handle.close()
    try:
        yield str(staged)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


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
