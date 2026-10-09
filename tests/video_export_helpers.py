import time

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication


def wait_for_video_export(widget, timeout=15):
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    while widget.is_exporting() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert not widget.is_exporting(), "Video export did not finish"
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
