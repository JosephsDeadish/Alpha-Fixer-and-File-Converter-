import gc
import threading
import time
import weakref

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QThread
from PyQt6.QtWidgets import QApplication, QWidget

from src.ui.preview_pane import (
    ImagePreviewPane, _ConvertedThumbLoader, _ThumbLoader, _active_preview_loads,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def settle(app, condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert condition()


def deferred_deletes(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def loaders(tmp_path, app):
    path = tmp_path / "source.png"
    with Image.new("RGBA", (8, 8), (20, 40, 60, 128)) as image:
        image.save(path)
    workers = []

    def create(kind, outcome="loaded", blocked=False):
        base = _ThumbLoader if kind == "source" else _ConvertedThumbLoader

        class ControlledLoader(base):
            def run(self):
                self.entered.set()
                if not self.decode.wait(3):
                    return
                try:
                    super().run()
                finally:
                    self.result_queued.set()
                    self.finish_gate.wait(3)

        args = (str(path if outcome == "loaded" else tmp_path / "missing.png"),)
        if kind == "converted":
            args += ("JPEG", 75)
        loader = ControlledLoader(*args)
        loader.entered = threading.Event()
        loader.decode = threading.Event()
        loader.result_queued = threading.Event()
        loader.finish_gate = threading.Event()
        if not blocked:
            loader.decode.set()
        workers.append(loader)
        return loader

    create.workers = workers
    yield create
    for loader in workers:
        loader.decode.set()
        loader.finish_gate.set()
        if not sip.isdeleted(loader):
            assert loader.wait(3000)
    settle(app, lambda: not _active_preview_loads)
    deferred_deletes(app)


class RecordingPane(ImagePreviewPane):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.callback_threads = []

    def _on_loaded(self, *args):
        self.callback_threads.append(QThread.currentThread())
        super()._on_loaded(*args)

    def _on_failed(self, *args):
        self.callback_threads.append(QThread.currentThread())
        super()._on_failed(*args)


@pytest.mark.parametrize("kind", ["source", "converted"])
def test_retired_worker_survives_without_pane_or_external_owner(app, loaders, kind):
    pane = RecordingPane()
    loader = loaders(kind, blocked=True)
    pane._start_loader(loader)
    assert loader.entered.wait(3)
    loader_ref = weakref.ref(loader)
    decode, queued, finish = (
        loader.decode, loader.result_queued, loader.finish_gate,
    )
    loaders.workers.remove(loader)
    try:
        pane.close()
        sip.delete(pane)
        del pane, loader
        gc.collect()
        assert loader_ref() is not None
        assert loader_ref().isRunning()
        assert loader_ref()._abort
    finally:
        decode.set()
        assert queued.wait(3)
        finish.set()
        worker = loader_ref()
        if worker is not None:
            assert worker.wait(3000)
        settle(app, lambda: not _active_preview_loads)
        deferred_deletes(app)
        del worker
    gc.collect()
    assert loader_ref() is None


@pytest.mark.parametrize("kind", ["source", "converted"])
@pytest.mark.parametrize("outcome", ["loaded", "failed"])
def test_result_is_gui_affine_and_retains_worker_until_finished(app, loaders, kind, outcome):
    pane = RecordingPane()
    loader = loaders(kind, outcome)
    try:
        pane._start_loader(loader)
        assert loader.result_queued.wait(3)
        settle(app, lambda: bool(pane.callback_threads))
        assert pane.callback_threads == [app.thread()]
        assert loader.isRunning()
        assert pane._loader is loader
        connection = pane._loader_connection
        assert connection in _active_preview_loads
        assert (pane._current_pix is not None) == (outcome == "loaded")
        loader.finish_gate.set()
        settle(app, lambda: pane._loader is None)
        assert connection not in _active_preview_loads
        deferred_deletes(app)
        assert sip.isdeleted(loader)
    finally:
        pane.close()
        sip.delete(pane)


@pytest.mark.parametrize("kind", ["source", "converted"])
@pytest.mark.parametrize("outcome", ["loaded", "failed"])
@pytest.mark.parametrize("action", ["close", "clear", "replace"])
def test_already_queued_results_cannot_update_stopped_or_reopened_pane(
    app, loaders, kind, outcome, action,
):
    pane = RecordingPane()
    old = loaders(kind, outcome)
    try:
        pane._start_loader(old)
        assert old.result_queued.wait(3)  # no GUI event delivery yet
        request = pane._load_request_id
        if action == "replace":
            new = loaders(kind, blocked=True)
            pane._start_loader(new)
        elif action == "clear":
            pane.clear()
        else:
            pane.close()
            pane.show()
        assert pane._load_request_id > request
        expected = pane._meta_label.text()
        app.processEvents()
        assert pane.callback_threads == []
        assert pane._meta_label.text() == expected
        assert pane._current_pix is None
        assert old._abort
        assert old.isRunning()
        old.finish_gate.set()
        if action == "replace":
            assert new.entered.wait(3)
            settle(app, lambda: not old.isRunning())
            app.processEvents()
            assert pane._loader is new
            new.decode.set()
            assert new.result_queued.wait(3)
            settle(app, lambda: bool(pane.callback_threads))
            assert pane.callback_threads == [app.thread()]
    finally:
        pane.close()
        sip.delete(pane)


@pytest.mark.parametrize("kind", ["source", "converted"])
@pytest.mark.parametrize("outcome", ["loaded", "failed"])
def test_close_after_result_still_cancels_unfinished_worker(app, loaders, kind, outcome):
    pane = RecordingPane()
    loader = loaders(kind, outcome)
    try:
        pane._start_loader(loader)
        assert loader.result_queued.wait(3)
        settle(app, lambda: bool(pane.callback_threads))
        pane.close()
        assert loader.isRunning()
        assert loader._abort
        assert pane._loader is None
    finally:
        sip.delete(pane)


@pytest.mark.parametrize("kind", ["source", "converted"])
@pytest.mark.parametrize("outcome", ["loaded", "failed"])
@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("delete_parent", [False, True])
def test_widget_deletion_cancels_without_destroying_running_thread(
    app, loaders, kind, outcome, queued, delete_parent,
):
    parent = QWidget() if delete_parent else None
    pane = RecordingPane(parent)
    loader = loaders(kind, outcome, blocked=not queued)
    pane._start_loader(loader)
    assert (loader.result_queued if queued else loader.entered).wait(3)
    connection = pane._loader_connection
    pane_ref = weakref.ref(pane)
    owner = parent if delete_parent else pane
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(pane)
    assert loader._abort
    assert loader.isRunning()
    assert connection in _active_preview_loads
    del owner, parent, pane
    gc.collect()
    assert pane_ref() is None
    app.processEvents()
    loader.decode.set()
    assert loader.result_queued.wait(3)
    loader.finish_gate.set()
    settle(app, lambda: connection not in _active_preview_loads)
    deferred_deletes(app)
    assert sip.isdeleted(loader)
