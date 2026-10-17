"""Event-loop waits for asynchronous GIF export tests (no pytest-qt required)."""
import time

from PyQt6.QtTest import QTest


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "GIF export did not complete in time"
        QTest.qWait(5)


def wait_for_gif_export(widget):
    wait_until(lambda: widget._export_worker is None)
