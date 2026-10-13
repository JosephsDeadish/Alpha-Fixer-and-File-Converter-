from contextlib import ExitStack
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import patch

from PIL import Image
from PyQt6.QtWidgets import QApplication

from src.ui import video_tool as vt
from src.core import video_export


def run_case(stage):
    app = QApplication.instance() or QApplication([])
    widget = vt.VideoToolDialog()
    image = Image.new('RGBA', (18, 20), (120, 40, 20, 255))
    clip = vt._ClipEntry('source.png', 1, vt._ImageFrameGetter(image), clip_type='image', frame_size=image.size)
    clip.still_duration_frames = 2
    widget._clips = [clip]
    widget._export_fmt_combo.setCurrentIndex(widget._export_fmt_combo.findData('mp4' if stage in {'writer_append','writer_close','mux'} else 'gif'))
    widget._mp4_export_available = True
    widget._record_export_history = lambda *a, **k: print('record_history', flush=True)
    entered, release = Event(), Event()
    worker_threads = []
    out = Path('/tmp/out.mp4' if stage in {'writer_append','writer_close','mux'} else '/tmp/out.gif')
    out.write_bytes(b'old')

    def hold(where):
        worker_threads.append((where, get_ident()))
        print('entered', where, flush=True)
        entered.set()
        ok = release.wait(8)
        print('released', where, ok, flush=True)

    class Writer:
        def __init__(self, path): self.path = path
        def append_data(self, data):
            if stage == 'writer_append':
                print('append_data', flush=True)
                hold('append_data')
        def close(self):
            if stage == 'writer_close':
                print('writer_close', flush=True)
                hold('writer_close')
            Path(self.path).write_bytes(b'encoded MP4')

    real_filter, real_save = vt._apply_filter, Image.Image.save

    def render(image, key):
        if stage == 'render':
            print('render', flush=True)
            hold('render')
        return real_filter(image, key)

    def save(image, path, *args, **kwargs):
        if stage == 'gif_save':
            print('save', flush=True)
            hold('save')
        return real_save(image, path, *args, **kwargs)

    def mux(*args):
        print('mux', flush=True)
        hold('mux')
        Path(args[1]).write_bytes(b'muxed')
        args[-1]()

    with ExitStack() as stack:
        stack.enter_context(patch.object(vt.QFileDialog, 'getSaveFileName', return_value=(str(out), '')))
        stack.enter_context(patch.object(vt, '_apply_filter', render))
        stack.enter_context(patch.object(Image.Image, 'save', save))
        stack.enter_context(patch('imageio.get_writer', side_effect=lambda path, **kw: Writer(path)))
        stack.enter_context(patch.object(widget, '_should_mux_audio', return_value=stage == 'mux'))
        stack.enter_context(patch.object(video_export, 'mux_mp4_audio', mux))
        stack.enter_context(patch.object(vt.QMessageBox, 'information'))
        stack.enter_context(patch.object(vt.QMessageBox, 'critical'))
        widget._btn_remove.setEnabled(False)
        shortcuts = widget.findChildren(vt.QShortcut)
        shortcuts[0].setEnabled(False)
        print('starting export', stage, flush=True)
        widget._export()
        print('export called', stage, flush=True)
        import time
        deadline = time.time() + 10
        while not entered.is_set() and time.time() < deadline:
            app.processEvents(); time.sleep(0.01)
        print('entered?', entered.is_set(), 'is_exporting', widget.is_exporting(), flush=True)
        print('second export', flush=True)
        widget._export()
        print('load image paths', flush=True)
        widget._load_image_paths(['not-a-real-file.png'])
        print('len clips', len(widget._clips), flush=True)
        print('canceling', flush=True)
        widget._cancel_export()
        print('after cancel', widget._export_canceling, widget.is_exporting(), flush=True)
        release.set()
        print('release set', flush=True)
        deadline = time.time() + 20
        while widget.is_exporting() and time.time() < deadline:
            app.processEvents(); time.sleep(0.01)
            print('poll exporting', flush=True)
        print('done', widget.is_exporting(), flush=True)
        print('worker threads', worker_threads, flush=True)


def test_writer_close():
    run_case('writer_close')

def test_mux():
    run_case('mux')
