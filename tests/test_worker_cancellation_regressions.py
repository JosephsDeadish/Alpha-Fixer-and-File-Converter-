"""Cancellation retains results of work already started, without admitting more."""

import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from PyQt6.QtCore import QCoreApplication

from src.core import worker as worker_module
from src.core.worker import AlphaWorker, ConverterWorker


class _ControlledExecutor:
    """Keep index 1 queued while indices 0 and 2 can finish out of order."""

    def __init__(self, release_first, first_pending=False):
        self.release_first = release_first
        self.first_pending = first_pending
        self.futures = {}
        self.threads = []
        self.submitted = []
        self.wait_calls = 0

    def submit(self, fn, idx, src):
        future = concurrent.futures.Future()
        self.futures[idx] = future
        self.submitted.append(idx)
        if idx == 1 or (idx == 0 and self.first_pending):
            return future

        # Mark running before returning so cancellation cannot depend on scheduling.
        future.set_running_or_notify_cancel()

        def run():
            try:
                future.set_result(fn(idx, src))
            except BaseException as exc:
                future.set_exception(exc)

        thread = threading.Thread(target=run)
        self.threads.append(thread)
        thread.start()
        return future

    def wait(self, pending, return_when):
        self.wait_calls += 1
        if self.wait_calls == 1:
            # Finish the later input first so its result must remain buffered.
            self.futures[2].result(timeout=5)
            return {self.futures[2]}, set(pending) - {self.futures[2]}
        if any(future.cancelled() for future in pending):
            raise AssertionError("wait() received an intentionally cancelled future")
        self.release_first.set()
        done, remaining = _REAL_WAIT(pending, timeout=5, return_when=return_when)
        if not done:
            raise AssertionError("in-flight conversion did not finish")
        return done, remaining

    def shutdown(self, wait=True, cancel_futures=False):
        # Also release on the old, buggy break/shutdown path: failures never hang.
        self.release_first.set()
        if cancel_futures:
            for future in self.futures.values():
                future.cancel()
        for thread in self.threads:
            thread.join(timeout=5)
            if thread.is_alive():
                raise AssertionError("conversion thread did not exit")


_REAL_WAIT = concurrent.futures.wait


class TestWorkerCancellationRegressions(unittest.TestCase):
    def setUp(self):
        # Worker cleanup may run GC; retain the Qt wrapper from earlier UI tests.
        self._app = QCoreApplication.instance()

    def test_alpha_accounts_for_inflight_completion_after_stop(self):
        for fails in (False, True):
            with self.subTest(fails=fails), tempfile.TemporaryDirectory(dir=".") as root:
                files = [str(Path(root, f"{idx}.png")) for idx in range(3)]
                output_dir = Path(root, "out")
                worker = AlphaWorker(files, output_dir=str(output_dir))
                file_done = []
                finished = []
                worker.file_done.connect(lambda *args: file_done.append(args))
                worker.finished.connect(lambda *args: finished.append(args))
                entered_save = threading.Event()
                release_save = threading.Event()
                controller_errors = []

                def cancel_inflight():
                    if not entered_save.wait(timeout=5):
                        controller_errors.append("save did not start")
                    worker.stop()
                    release_save.set()

                def save(img, dest, ext):
                    entered_save.set()
                    if not release_save.wait(timeout=5):
                        raise AssertionError("stop controller did not release save")
                    if fails:
                        raise OSError("in-flight alpha save failed")
                    Path(dest).write_bytes(b"completed alpha output")

                controller = threading.Thread(target=cancel_inflight)
                controller.start()
                image = Image.new("RGBA", (1, 1))
                try:
                    with mock.patch.object(worker_module, "load_image", return_value=image) as load, \
                            mock.patch.object(worker_module, "save_image", side_effect=save):
                        worker.run()
                finally:
                    release_save.set()
                    controller.join(timeout=5)
                    image.close()
                self.assertFalse(controller.is_alive())
                self.assertEqual(controller_errors, [])
                load.assert_called_once_with(files[0])
                self.assertEqual(finished, [(0, 1) if fails else (1, 0)])
                self.assertEqual([(src, ok) for src, ok, _ in file_done], [(files[0], not fails)])
                self.assertEqual(
                    sorted(path.name for path in output_dir.iterdir()),
                    [] if fails else ["0.png"],
                )
                if fails:
                    self.assertIn("in-flight alpha save failed", file_done[0][2])

    def test_converter_drains_inflight_and_buffered_results_across_cancelled_gap(self):
        for first_fails, first_pending in ((False, False), (True, False), (False, True)):
            with self.subTest(first_fails=first_fails, first_pending=first_pending), \
                    tempfile.TemporaryDirectory(dir=".") as root:
                files = [str(Path(root, f"{idx}.png")) for idx in range(5)]
                output_dir = Path(root, "out")
                output_dir.mkdir()
                worker = ConverterWorker(files, "PNG", ".png", output_dir=str(output_dir))
                release_first = threading.Event()
                first_entered = threading.Event()
                executor = _ControlledExecutor(release_first, first_pending=first_pending)
                file_done = []
                finished = []
                progress = []
                worker.file_done.connect(lambda *args: file_done.append(args))
                worker.finished.connect(lambda *args: finished.append(args))
                worker.progress.connect(lambda idx, *_: progress.append(idx))

                def convert(src, dest, fmt, **kwargs):
                    if src == files[0]:
                        first_entered.set()
                        if not release_first.wait(timeout=5):
                            raise AssertionError("first conversion was not released")
                        if first_fails:
                            raise OSError("in-flight conversion failed")
                    elif src == files[2]:
                        if not first_pending and not first_entered.wait(timeout=5):
                            raise AssertionError("first conversion did not start")
                        worker.stop()
                    else:
                        raise AssertionError("cancelled or unsubmitted input was converted")
                    Path(dest).write_bytes(b"completed converted output")

                with mock.patch.object(worker, "_recommend_worker_count", return_value=3), \
                        mock.patch.object(worker_module, "convert_file", side_effect=convert), \
                        mock.patch.object(worker_module.concurrent.futures, "ThreadPoolExecutor",
                                          return_value=executor), \
                        mock.patch.object(worker_module.concurrent.futures, "wait",
                                          side_effect=executor.wait):
                    worker.run()

                expected_success = 1 if first_fails or first_pending else 2
                self.assertEqual(finished, [(expected_success, int(first_fails))])
                self.assertEqual(executor.submitted, [0, 1, 2])
                self.assertTrue(executor.futures[1].cancelled())
                expected_indices = [2] if first_pending else [0, 2]
                self.assertEqual(progress, expected_indices)
                self.assertEqual(
                    [(src, ok) for src, ok, _ in file_done],
                    [(files[idx], not (idx == 0 and first_fails)) for idx in expected_indices],
                )
                expected_outputs = ["2.png"] if first_fails or first_pending else ["0.png", "2.png"]
                self.assertEqual(sorted(path.name for path in output_dir.iterdir()), expected_outputs)
                if first_fails:
                    self.assertIn("in-flight conversion failed", file_done[0][2])

    def test_stopped_workers_do_not_start_inputs(self):
        for worker in (AlphaWorker(["never.png"]), ConverterWorker(["never.png"], "PNG", ".png")):
            with self.subTest(worker=type(worker).__name__):
                finished = []
                worker.finished.connect(lambda *args: finished.append(args))
                worker.stop()
                with mock.patch.object(worker_module, "load_image") as load, \
                        mock.patch.object(worker_module, "convert_file") as convert:
                    worker.run()
                load.assert_not_called()
                convert.assert_not_called()
                self.assertEqual(finished, [(0, 0)])
