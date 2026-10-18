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


_GIF_TRANSPARENCY_INDEX = 255
_GIF_ALPHA_THRESHOLD = 128


def _has_gif_transparency(frame: Image.Image) -> bool:
    rgba = frame if frame.mode == "RGBA" else frame.convert("RGBA")
    owns_rgba = rgba is not frame
    alpha = transparent = None
    try:
        alpha = rgba.getchannel("A")
        transparent = alpha.point(
            lambda value: 255 if value < _GIF_ALPHA_THRESHOLD else 0
        )
        return transparent.getbbox() is not None
    finally:
        if transparent is not None:
            transparent.close()
        if alpha is not None:
            alpha.close()
        if owns_rgba:
            rgba.close()


def _quantize_gif_frame(frame: Image.Image, *, reserve_transparency: bool) -> Image.Image:
    rgba = frame if frame.mode == "RGBA" else frame.convert("RGBA")
    owns_rgba = rgba is not frame
    alpha = rgba.getchannel("A")
    transparent = alpha.point(
        lambda value: 255 if value < _GIF_ALPHA_THRESHOLD else 0
    )
    rgb = rgba.convert("RGB")
    indexed = None
    try:
        if reserve_transparency:
            rgb.paste((0, 0, 0), mask=transparent)
        indexed = rgb.quantize(
            colors=255 if reserve_transparency else 256,
            method=Image.Quantize.FASTOCTREE,
            dither=0,
        )
        if reserve_transparency:
            indexed.paste(_GIF_TRANSPARENCY_INDEX, mask=transparent)
            indexed.info["transparency"] = _GIF_TRANSPARENCY_INDEX
        result = indexed
        indexed = None
        return result
    finally:
        if owns_rgba:
            rgba.close()
        alpha.close()
        transparent.close()
        rgb.close()
        if indexed is not None:
            indexed.close()


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
            has_transparency = False
            for source, _ in self.frames:
                self._checkpoint()
                if _has_gif_transparency(source):
                    has_transparency = True
                    break
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
                    prepared.append(
                        _quantize_gif_frame(
                            frame,
                            reserve_transparency=has_transparency,
                        )
                    )
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
                    transparency_options = {}
                    if has_transparency:
                        transparency_options = {
                            "transparency": _GIF_TRANSPARENCY_INDEX,
                            "background": _GIF_TRANSPARENCY_INDEX,
                            "disposal": [2] * len(prepared),
                        }
                    prepared[0].save(
                        staged_path, format="GIF", save_all=True,
                        append_images=prepared[1:],
                        duration=durations if len(prepared) > 1 else durations[0],
                        loop=settings.loop, optimize=settings.optimize,
                        **transparency_options,
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
