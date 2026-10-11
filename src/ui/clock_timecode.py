"""Clock timestamps, not SMPTE frame-number or drop-frame timecode."""
from decimal import Decimal, ROUND_HALF_UP
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLineEdit


def parse_clock_timecode(text: str) -> Decimal:
    """Parse HH:MM:SS.mmm exactly; hours may exceed two digits."""
    match = re.fullmatch(r"([0-9]{2,}):([0-5][0-9]):([0-5][0-9])\.([0-9]{3})", text)
    if match is None:
        raise ValueError("Expected HH:MM:SS.mmm clock timecode")
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    return Decimal(hours * 3600 + minutes * 60 + seconds) + Decimal(milliseconds) / 1000


def format_clock_timecode(frame: int, fps: float) -> str:
    milliseconds = int((Decimal(frame) * 1000 / Decimal(str(fps))).to_integral_value(
        rounding=ROUND_HALF_UP))
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


class ClockTimecodeEdit(QLineEdit):
    """Commit only edited valid timestamps; discard stale edits on model sync."""

    secondsCommitted = pyqtSignal(object)

    def __init__(self, description: str):
        super().__init__("00:00:00.000")
        self._canonical = self.text()
        self.setAccessibleName(description + " (clock HH:MM:SS.mmm)")
        self.setToolTip(
            description + "\nClock HH:MM:SS.mmm, not SMPTE/drop-frame. "
            "Nearest frame; half-frame ties go forward. Clamped to available frames. "
            "Invalid input is discarded.")
        self.editingFinished.connect(self._commit)

    def setFrame(self, frame: int, fps: float) -> None:
        self._canonical = format_clock_timecode(frame, fps)
        self.setText(self._canonical)
        self.setModified(False)

    def _commit(self) -> None:
        if not self.isModified():
            return
        text = self.text()
        self.setText(self._canonical)
        self.setModified(False)
        try:
            seconds = parse_clock_timecode(text)
        except ValueError:
            return
        self.secondsCommitted.emit(seconds)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit()
            event.accept()
            return
        super().keyPressEvent(event)
