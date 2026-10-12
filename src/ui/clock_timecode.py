"""Clock timestamps and frame-exact SMPTE labels for supported video rates."""
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


def _smpte_rate(fps: float, drop_frame: bool) -> tuple[int, int]:
    rate = Decimal(str(fps))
    if not rate.is_finite():
        raise ValueError("Unsupported SMPTE FPS")
    for nominal, decimal_rate in ((30, "29.97"), (60, "59.94")):
        if rate == Decimal(decimal_rate) or abs(rate - Decimal(nominal * 1000) / 1001) <= Decimal("0.000001"):
            return nominal, nominal // 15 if drop_frame else 0
    if not drop_frame and 1 <= rate <= 60 and rate == rate.to_integral_value():
        return int(rate), 0
    raise ValueError("Drop-frame requires 29.97 or 59.94 FPS" if drop_frame else
                     "SMPTE requires integer 1–60, 29.97 or 59.94 FPS")


def parse_smpte_timecode(text: str, fps: float) -> int:
    """Return an absolute frame index; hours do not wrap at 24."""
    match = re.fullmatch(r"([0-9]{2,}):([0-5][0-9]):([0-5][0-9])([:;])([0-9]{2})", text)
    if match is None:
        raise ValueError("Expected HH:MM:SS:FF or HH:MM:SS;FF")
    hours, minutes, seconds, separator, frames = match.groups()
    hours, minutes, seconds, frames = map(int, (hours, minutes, seconds, frames))
    nominal, drop = _smpte_rate(fps, separator == ";")
    if frames >= nominal or (drop and minutes % 10 and seconds == 0 and frames < drop):
        raise ValueError("Invalid or skipped SMPTE frame label")
    total_minutes = hours * 60 + minutes
    return ((hours * 3600 + minutes * 60 + seconds) * nominal + frames
            - drop * (total_minutes - total_minutes // 10))


def format_smpte_timecode(frame: int, fps: float, drop_frame: bool = False) -> str:
    """Use integer label arithmetic, including drop-minute and hour carries."""
    if type(frame) is not int or frame < 0:
        raise ValueError("Expected a nonnegative frame index")
    nominal, drop = _smpte_rate(fps, drop_frame)
    if drop:
        per_minute = nominal * 60 - drop
        blocks, remainder = divmod(frame, nominal * 600 - drop * 9)
        frame += drop * (blocks * 9 + max(0, (remainder - drop) // per_minute))
    seconds, frames = divmod(frame, nominal)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    separator = ";" if drop_frame else ":"
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{frames:02}"


class ClockTimecodeEdit(QLineEdit):
    """Commit only edited valid timestamps; discard stale edits on model sync."""

    secondsCommitted = pyqtSignal(object)
    framesCommitted = pyqtSignal(object)

    def __init__(self, description: str):
        super().__init__("00:00:00.000")
        self._frame, self._fps = 0, 25.0
        self._display_format = "clock"
        self._canonical = self.text()
        self.setAccessibleName(description + " timecode")
        self.setToolTip(
            description + "\nClock HH:MM:SS.mmm rounds to nearest frame (ties forward). "
            "SMPTE HH:MM:SS:FF selects exact frames; HH:MM:SS;FF uses drop-frame "
            "at 29.97/59.94 FPS only. Other fractional rates use clock inputs. "
            "Hours may exceed 24. Clamped to available frames; invalid/skipped labels "
            "are discarded. Right-click to choose the display format.")
        self.editingFinished.connect(self._commit)

    def setFrame(self, frame: int, fps: float) -> None:
        self._frame, self._fps = frame, fps
        try:
            self._canonical = (format_clock_timecode(frame, fps) if self._display_format == "clock"
                               else format_smpte_timecode(frame, fps, self._display_format == "drop"))
        except ValueError:
            self._canonical = format_clock_timecode(frame, fps)
        self.setText(self._canonical)
        self.setModified(False)

    def setDisplayFormat(self, display_format: str) -> None:
        if display_format not in ("clock", "smpte", "drop"):
            raise ValueError("Unknown timecode display format")
        self._display_format = display_format
        self.setFrame(self._frame, self._fps)

    def createStandardContextMenu(self):
        menu = super().createStandardContextMenu()
        display = menu.addMenu("Timecode display")
        for label, mode in (("Clock (HH:MM:SS.mmm)", "clock"),
                            ("SMPTE (HH:MM:SS:FF)", "smpte"),
                            ("Drop-frame (HH:MM:SS;FF)", "drop")):
            action = display.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._display_format == mode)
            if mode != "clock":
                try:
                    _smpte_rate(self._fps, mode == "drop")
                except ValueError:
                    action.setEnabled(False)
            action.triggered.connect(lambda _checked=False, mode=mode: self.setDisplayFormat(mode))
        return menu

    def contextMenuEvent(self, event) -> None:
        menu = self.createStandardContextMenu()
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _commit(self) -> None:
        if not self.isModified():
            return
        text = self.text()
        self.setText(self._canonical)
        self.setModified(False)
        try:
            if "." in text:
                seconds = parse_clock_timecode(text)
            else:
                frame = parse_smpte_timecode(text, self._fps)
                self.framesCommitted.emit(frame)
                return
        except ValueError:
            return
        self.secondsCommitted.emit(seconds)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit()
            event.accept()
            return
        super().keyPressEvent(event)
