from contextlib import ExitStack
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import Mock, patch
import time

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from src.core import video_export
from src.ui import video_tool as vt
from tests.video_export_helpers import wait_for_video_export


def pump_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.fixture(scope='session')
def video_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(video_app):
    app = video_app
    widget = vt.VideoToolDialog()
    image = Image.new('RGBA', (18, 20), (120, 40, 20, 255))
    clip = vt._ClipEntry('source.png', 1, vt._ImageFrameGetter(image), clip_type='image', frame_size=image.size)
    clip.still_duration_frames = 2
    widget._clips = [clip]
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData('gif'))
    widget._mp4_export_available = True
    widget._record_export_history = Mock()
    with patch.object(vt.QMessageBox, 'information') as info, patch.object(vt.QMessageBox, 'critical') as error:
        widget._test_info = info
        widget._test_error = error
        original_release = widget._release_clips
        def traced_release():
            print('release_clips start', flush=True)
            original_release()
            print('release_clips end', flush=True)
        widget._release_clips = traced_release
        print('fixture yield', flush=True)
        yield widget
        print('fixture teardown start', widget.is_exporting(), widget._export_progress is None, flush=True)
        if widget.is_exporting():
            widget.request_export_cancel()
            wait_for_video_export(widget)
        print('fixture teardown close-start', flush=True)
        widget.close()
        print('fixture teardown after close', flush=True)
        sip.delete(widget)
        print('fixture teardown after delete', flush=True)
        app.processEvents()
        print('fixture teardown done', flush=True)


class Writer:
    def __init__(self, path):
        self.path = path
    def append_data(self, data):
        pass
    def close(self):
        Path(self.path).write_bytes(b'encoded MP4')


@pytest.mark.parametrize('stage', ['gif_save'])
@pytest.mark.parametrize('existing', [True])
def test_case(dialog, tmp_path, stage, existing):
    fmt = 'gif' if stage in {'render', 'gif_save'} else 'mp4'
    dialog._export_fmt_combo.setCurrentIndex(dialog._export_fmt_combo.findData(fmt))
    destination = tmp_path / f'output.{fmt}'
    if existing:
        destination.write_bytes(b'original')
    entered, release = Event(), Event()
    gui_thread = get_ident()
    worker_threads = []
    finished = []
    dialog.export_finished.connect(lambda: finished.append(not dialog.is_exporting()))
    heartbeat = []
    timer = QTimer(); timer.timeout.connect(lambda: heartbeat.append(1)); timer.start(5)

    def hold(where):
        worker_threads.append((where, get_ident()))
        print('entered hold', where, flush=True)
        entered.set()
        assert release.wait(8)
        print('release wait returned', where, flush=True)

    real_filter, real_save = vt._apply_filter, Image.Image.save
    def render(image, key):
        return real_filter(image, key)
    def save(image, path, *args, **kwargs):
        print('SAVE CALLED', flush=True)
        hold('save')
        return real_save(image, path, *args, **kwargs)
    def mux(*args):
        hold('mux')
        Path(args[1]).write_bytes(b'muxed')
        args[-1]()

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(vt.QFileDialog, 'getSaveFileName', return_value=(str(destination), '')))
            stack.enter_context(patch.object(vt, '_apply_filter', render))
            stack.enter_context(patch.object(Image.Image, 'save', save))
            stack.enter_context(patch('imageio.get_writer', side_effect=lambda path, **kw: Writer(path)))
            stack.enter_context(patch.object(dialog, '_should_mux_audio', return_value=False))
            stack.enter_context(patch.object(video_export, 'mux_mp4_audio', mux))
            dialog._btn_remove.setEnabled(False)
            shortcuts = dialog.findChildren(vt.QShortcut)
            shortcuts[0].setEnabled(False)
            print('before export', flush=True)
            dialog._export()
            print('after export call', flush=True)
            pump_until(entered.is_set)
            print('after entered wait', entered.is_set(), dialog.is_exporting(), flush=True)
            before = len(heartbeat)
            pump_until(lambda: len(heartbeat) >= before + 4)
            print('after heartbeat wait', flush=True)
            dialog._export()
            print('after second export', flush=True)
            dialog._load_image_paths(['not-a-real-file.png'])
            print('after load_image_paths', flush=True)
            assert len(dialog._clips) == 1
            dialog._cancel_export()
            print('after cancel call', dialog.is_exporting(), flush=True)
            assert dialog.is_exporting()
            release.set()
            print('release set', flush=True)
            wait_for_video_export(dialog)
            print('after wait_for_video_export', dialog.is_exporting(), flush=True)
            from PyQt6.QtWidgets import QApplication, QProgressDialog
            print('progress dialogs', len([w for w in QApplication.topLevelWidgets() if isinstance(w, QProgressDialog)]), flush=True)
            assert dialog._export_content.isEnabled()
            assert not dialog._btn_remove.isEnabled()
            assert not shortcuts[0].isEnabled()
        print('post-with assertions', flush=True)
        assert all(thread != gui_thread for thread in worker_threads)
        assert finished == [True]
        assert destination.read_bytes() == b'original' if existing else not destination.exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == ([destination.name] if existing else [])
        dialog._record_export_history.assert_not_called()
        dialog._test_info.assert_not_called()
        dialog._test_error.assert_not_called()
    finally:
        release.set()
        timer.stop()
        if dialog.is_exporting():
            print('finalizer waiting for export', flush=True)
            wait_for_video_export(dialog)
        print('test finally done', flush=True)
