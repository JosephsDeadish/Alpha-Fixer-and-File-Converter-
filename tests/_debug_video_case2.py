from contextlib import ExitStack
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
import time

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from src.ui import video_tool as vt

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
    with patch.object(vt.QMessageBox, 'information'), patch.object(vt.QMessageBox, 'critical'):
        yield widget
        if widget.is_exporting():
            widget.request_export_cancel()
            # don't wait here for this trace
        widget.close(); sip.delete(widget); app.processEvents()

class Writer:
    def __init__(self, path): self.path = path
    def append_data(self, data): pass
    def close(self): Path(self.path).write_bytes(b'encoded MP4')

@pytest.mark.parametrize('existing', [True])
def test_case(dialog, tmp_path, existing):
    stage = 'gif_save'
    fmt = 'gif'
    destination = tmp_path / 'output.gif'
    destination.write_bytes(b'original')
    entered, release = Event(), Event()
    heartbeat = []
    timer = QTimer(); timer.timeout.connect(lambda: heartbeat.append(1)); timer.start(5)
    real_filter, real_save = vt._apply_filter, Image.Image.save
    def render(image, key):
        return real_filter(image, key)
    def save(image, path, *args, **kwargs):
        print('SAVE CALLED', flush=True)
        entered.set()
        return real_save(image, path, *args, **kwargs)
    with ExitStack() as stack:
        stack.enter_context(patch.object(vt.QFileDialog, 'getSaveFileName', return_value=(str(destination), '')))
        stack.enter_context(patch.object(vt, '_apply_filter', render))
        stack.enter_context(patch.object(Image.Image, 'save', save))
        stack.enter_context(patch('imageio.get_writer', side_effect=lambda path, **kw: Writer(path)))
        stack.enter_context(patch.object(dialog, '_should_mux_audio', return_value=False))
        stack.enter_context(patch.object(vt.QMessageBox, 'information'))
        stack.enter_context(patch.object(vt.QMessageBox, 'critical'))
        dialog._export()
        print('export returned', flush=True)
        for i in range(20):
            print('loop', i, 'before processEvents', entered.is_set(), flush=True)
            dialog.window().QApplication.processEvents() if False else None
            QApplication.instance().processEvents()
            print('loop', i, 'after processEvents', entered.is_set(), len(heartbeat), flush=True)
            time.sleep(0.1)
        print('done loop', entered.is_set(), len(heartbeat), flush=True)
    timer.stop()
