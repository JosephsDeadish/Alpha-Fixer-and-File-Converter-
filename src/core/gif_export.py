"""Widget-free GIF encoding with cooperative, transactional cancellation."""
from dataclasses import dataclass
from threading import Event, Lock

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal


@dataclass(frozen=True)
class GifExportSettings:
    output: str
    max_width: int
    max_height: int
    delay: int
    loop: int
    optimize: bool


class _ExportCanceled(Exception):
    pass


class GifExportWorker(QThread):
    progress = pyqtSignal(int, str)

    def __init__(self, settings: GifExportSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        # Assigned once, before start(); the worker owns these image copies.
        self.frames = ()
        self.outcome = ""
        self.error = ""
        self._cancel = Event()
        self._publish_lock = Lock()
        self._published = False

    def cancel(self) -> bool:
        # Serialize the cancellation decision with the short atomic publish,
        # never with frame preparation or Pillow's uninterruptible save().
        # If publication already owns the lock, it is too late to cancel.
        # Do not block the GUI even on a slow destination filesystem.
        if not self._publish_lock.acquire(blocking=False):
            return False
        try:
            if self._published:
                return False
            self._cancel.set()
            return True
        finally:
            self._publish_lock.release()

    def _checkpoint(self):
        if self._cancel.is_set():
            raise _ExportCanceled()

    def run(self):
        prepared = []
        durations = []
        settings = self.settings
        try:
            for index, (source, delay) in enumerate(self.frames):
                self._checkpoint()
                self.progress.emit(index, f"Building GIF… frame {index + 1} / {len(self.frames)}")
                frame = source.copy()
                try:
                    if settings.max_width > 0 or settings.max_height > 0:
                        frame.thumbnail(
                            (settings.max_width or 99999, settings.max_height or 99999),
                            Image.Resampling.LANCZOS,
                        )
                    self._checkpoint()
                    prepared.append(frame.quantize(
                        colors=255, method=Image.Quantize.FASTOCTREE, dither=0,
                    ))
                finally:
                    frame.close()
                durations.append(delay)
                self._checkpoint()
            self._checkpoint()
            self.progress.emit(len(self.frames), "Saving GIF… Cancel will discard the result after encoding finishes.")
            from src.ui._ui_utils import staged_output_path
            # Hold the publish lock only across context exit (os.replace).
            # Raising inside the context removes the stage without publishing.
            locked = False
            try:
                with staged_output_path(settings.output) as staged_path:
                    self._checkpoint()
                    prepared[0].save(
                        staged_path, format="GIF", save_all=True,
                        append_images=prepared[1:],
                        duration=durations if len(prepared) > 1 else durations[0],
                        loop=settings.loop, optimize=settings.optimize,
                    )
                    self._publish_lock.acquire()
                    locked = True
                    self._checkpoint()
                self._published = True
            finally:
                if locked:
                    self._publish_lock.release()
            self.outcome = "success"
        except _ExportCanceled:
            self.outcome = "canceled"
        except Exception as exc:
            self.outcome = "canceled" if self._cancel.is_set() else "error"
            self.error = str(exc)
        finally:
            for frame in prepared:
                frame.close()
            for frame, _ in self.frames:
                frame.close()
            self.frames = ()
