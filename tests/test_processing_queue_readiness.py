"""Source-run queue qualification; not a sustained-RSS or native-runtime benchmark."""

import concurrent.futures
import errno
import os
import stat
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image
from PyQt6.QtCore import QCoreApplication

from src.core import file_converter, worker as worker_module
from src.core.worker import AlphaWorker, ConverterWorker


@pytest.fixture
def queue_root():
    root = Path("build/queue-tests")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        yield Path(directory)


@pytest.mark.parametrize("kind", ["alpha", "converter"])
def test_flat_destination_collisions_are_reported_without_overwriting(queue_root, kind):
    files = []
    for index, color in enumerate(((10, 20, 30, 200), (80, 90, 100, 200))):
        src = queue_root / str(index) / "same.png"
        src.parent.mkdir()
        with Image.new("RGBA", (2, 2), color) as image:
            image.save(src)
        files.append(str(src))
    output = queue_root / "out"
    worker = (AlphaWorker(files, output_dir=str(output)) if kind == "alpha"
              else ConverterWorker(files, "PNG", ".png", output_dir=str(output)))
    finished, manifests, failures = [], [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)
    worker.file_done.connect(lambda *args: failures.append(args) if not args[1] else None)
    worker.run()
    assert finished == [(1, 1)]
    assert len(manifests[0]) == 1
    assert "collision" in failures[0][2].lower()
    assert len(list(output.iterdir())) == 1


def test_converter_slow_head_bounds_admitted_result_window(queue_root):
    """Completed later inputs must not turn the entire queue into buffered results."""
    app = QCoreApplication.instance()  # Keep a prior Qt application alive through GC.
    files = [str(queue_root / f"{index}.png") for index in range(1000)]
    worker = ConverterWorker(files, "PNG", ".png")
    release = threading.Event()
    admitted = []
    real_wait = concurrent.futures.wait

    def convert(src, *_args, **_kwargs):
        admitted.append(src)
        if src == files[0]:
            assert release.wait(5)

    waits = 0

    def wait(pending, return_when):
        nonlocal waits
        waits += 1
        if waits == 1:
            later = [future for future in pending if future.done()]
            if not later:
                done, _ = real_wait(pending, timeout=5, return_when=return_when)
                later = list(done)
            return set(later), set(pending) - set(later)
        # The first result remains stalled after a later result has been buffered.
        try:
            assert len(admitted) <= 2
        finally:
            worker.stop()
            release.set()
        return real_wait(pending, timeout=5, return_when=return_when)

    try:
        with mock.patch.object(worker, "_recommend_worker_count", return_value=2), \
                mock.patch.object(worker_module, "convert_file", side_effect=convert), \
                mock.patch.object(worker_module.concurrent.futures, "wait", side_effect=wait):
            worker.run()
    finally:
        release.set()
    assert len(admitted) == 2


@pytest.mark.parametrize("fails", [False, True])
def test_pbm_intermediates_close_on_success_and_failure(queue_root, fails):
    source = queue_root / "source.png"
    with Image.new("RGBA", (3, 3), (30, 60, 90, 120)) as image:
        image.save(source)
    converted = []
    real_convert = Image.Image.convert

    def track(image, *args, **kwargs):
        result = real_convert(image, *args, **kwargs)
        converted.append(result)
        return result

    with mock.patch.object(Image.Image, "convert", track):
        if fails:
            with mock.patch.object(Image.Image, "save", side_effect=OSError("encoder failed")), \
                    pytest.raises(OSError, match="encoder failed"):
                file_converter.convert_file(str(source), str(queue_root / "out.pbm"), "PBM")
        else:
            file_converter.convert_file(str(source), str(queue_root / "out.pbm"), "PBM")
    assert converted
    for image in converted:
        with pytest.raises(ValueError, match="closed image"):
            image.getpixel((0, 0))


@pytest.mark.parametrize("kind", ["alpha", "converter"])
def test_thousand_input_queue_repeated_completion_and_cleanup(queue_root, kind):
    app = QCoreApplication.instance()
    source = queue_root / "input"
    source.mkdir()
    files = []
    for index in range(1000):
        path = source / f"{index:04}.png"
        with Image.new("RGBA", (2, 2), (index % 256, 30, 60, 120)) as image:
            image.save(path)
        files.append(str(path))
    Path(files[501]).write_bytes(b"corrupt image")

    for cycle in range(2):
        output = queue_root / f"out-{cycle}"
        worker = (AlphaWorker(files, manual_params={"invert": True}, output_dir=str(output))
                  if kind == "alpha" else
                  ConverterWorker(files, "PNG", ".png", output_dir=str(output), resize=(3, 3)))
        finished, manifests, messages, progress, opened = [], [], [], [], []
        worker.finished.connect(lambda *args: finished.append(args))
        worker.output_manifest.connect(manifests.append)
        worker.file_done.connect(lambda *args: messages.append(args))
        worker.progress.connect(lambda *args: progress.append(args))
        module = worker_module if kind == "alpha" else file_converter
        loader_name = "load_image" if kind == "alpha" else "_open_image"
        loader = getattr(module, loader_name)

        def track(path):
            image = loader(path)
            opened.append(image)
            return image

        with mock.patch.object(module, loader_name, side_effect=track), \
                mock.patch.object(worker_module.time, "monotonic", return_value=1.0):
            worker.run()
        assert finished == [(999, 1)]
        assert len(manifests[0]) == 999
        assert list(manifests[0]) == [path for path in files if path != files[501]]
        assert len(messages) == 1 and messages[0][:2] == (files[501], False)
        assert [row[0] for row in progress] == [0, 999]
        assert len(list(output.iterdir())) == 999
        assert not list(output.glob(".alpha_fixer_save_*"))
        assert len(opened) == 999
        for image in opened:
            with pytest.raises(ValueError, match="closed image"):
                image.getpixel((0, 0))
        for index in (0, 999):
            with Image.open(manifests[0][files[index]]) as image:
                assert image.size == ((2, 2) if kind == "alpha" else (3, 3))
                pixel = image.getpixel((0, 0))
                assert pixel[3] == (135 if kind == "alpha" else 120)
                # Pillow's RGBA resize uses premultiplied channels with rounding.
                assert all(abs(actual - expected) <= 2
                           for actual, expected in zip(pixel[:3], (index % 256, 30, 60)))


@pytest.mark.parametrize("kind", ["alpha", "converter"])
@pytest.mark.parametrize("failure", ["encoder", "replace"])
def test_failed_save_preserves_existing_destination_and_removes_stage(queue_root, kind, failure):
    source = queue_root / "source.png"
    with Image.new("RGBA", (2, 2), (10, 20, 30, 120)) as image:
        image.save(source)
    original = source.read_bytes()
    worker = (AlphaWorker([str(source)], overwrite=True) if kind == "alpha" else
              ConverterWorker([str(source)], "PNG", ".png"))
    finished, manifests = [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)

    def partial_save(_image, path, *_args, **_kwargs):
        Path(path).write_bytes(b"partial encoding")
        raise OSError("encoder failed")

    patch = (mock.patch.object(Image.Image, "save", side_effect=partial_save)
             if failure == "encoder" else
             mock.patch("os.replace", side_effect=PermissionError("destination locked")))
    with patch:
        worker.run()
    assert finished == [(0, 1)]
    assert manifests == [{}]
    assert source.read_bytes() == original
    assert sorted(path.name for path in queue_root.iterdir()) == ["source.png"]


@pytest.mark.parametrize("kind", ["alpha", "converter"])
@pytest.mark.parametrize("phase,code", [
    ("create", errno.ENOSPC), ("create", errno.EACCES),
    ("encode", errno.ENOSPC), ("encode", errno.EIO),
    ("replace", errno.EACCES), ("replace", errno.EIO),
    ("missing_input", errno.ENOENT),
])
def test_queue_storage_failure_continues_and_failed_input_can_retry(queue_root, kind, phase, code):
    files = []
    for name in ("first.png", "second.png"):
        source = queue_root / name
        with Image.new("RGBA", (3, 3), (10, 20, 30, 120)) as image:
            image.save(source)
        files.append(str(source))
    original = Path(files[0]).read_bytes()
    output = queue_root / "out"
    output.mkdir()
    target = output / "first.png"
    target.write_bytes(b"previous output")
    if phase == "missing_input":
        Path(files[0]).unlink()

    def make_worker(paths):
        return (AlphaWorker(paths, output_dir=str(output), manual_params={"invert": True})
                if kind == "alpha" else
                ConverterWorker(paths, "PNG", ".png", output_dir=str(output), resize=(4, 4)))

    worker = make_worker(files)
    finished, manifests, messages = [], [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)
    worker.file_done.connect(lambda *args: messages.append(args))
    failure = OSError(code, os.strerror(code))
    real_create, real_save, real_replace = tempfile.NamedTemporaryFile, Image.Image.save, os.replace
    calls = 0

    def fail_first(operation, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            if phase == "encode":
                Path(args[1]).write_bytes(b"partial encoding")
            raise failure
        return operation(*args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(ConverterWorker, "_recommend_worker_count", return_value=1))
        if phase == "create":
            stack.enter_context(mock.patch(
                "tempfile.NamedTemporaryFile", side_effect=lambda *a, **kw: fail_first(real_create, *a, **kw)))
        elif phase == "encode":
            stack.enter_context(mock.patch.object(
                Image.Image, "save", lambda *a, **kw: fail_first(real_save, *a, **kw)))
        elif phase == "replace":
            stack.enter_context(mock.patch(
                "os.replace", side_effect=lambda *a, **kw: fail_first(real_replace, *a, **kw)))
        worker.run()
    assert finished == [(1, 1)]
    assert manifests == [{files[1]: str(output / "second.png")}]
    assert [(row[0], row[1]) for row in messages] == [(files[0], False), (files[1], True)]
    assert f"[Errno {code}]" in messages[0][2]
    assert target.read_bytes() == b"previous output"
    assert not list(output.glob(".alpha_fixer_save_*"))
    Path(files[0]).write_bytes(original)
    retry = make_worker([files[0]])
    retried, retry_outputs = [], []
    retry.finished.connect(lambda *args: retried.append(args))
    retry.output_manifest.connect(retry_outputs.append)
    retry.run()
    assert retried == [(1, 0)]
    assert retry_outputs == [{files[0]: str(target)}]
    assert Path(files[0]).read_bytes() == original
    with Image.open(target) as image:
        assert image.size == ((3, 3) if kind == "alpha" else (4, 4))
        assert image.getpixel((0, 0))[3] == (135 if kind == "alpha" else 120)
    assert not list(output.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("kind", ["alpha", "converter"])
def test_thousand_input_cancel_does_not_admit_more_and_cleans_up(queue_root, kind):
    app = QCoreApplication.instance()
    source = queue_root / "source.png"
    with Image.new("RGBA", (2, 2), (10, 20, 30, 120)) as image:
        image.save(source)
    files = [str(queue_root / f"source-{index}.png") for index in range(1000)]
    for path in files:
        Path(path).write_bytes(source.read_bytes())
    for cycle in range(3):
        output = queue_root / f"cancel-{cycle}"
        worker = (AlphaWorker(files, output_dir=str(output)) if kind == "alpha" else
                  ConverterWorker(files, "PNG", ".png", output_dir=str(output)))
        finished, manifests, calls = [], [], []
        worker.finished.connect(lambda *args: finished.append(args))
        worker.output_manifest.connect(manifests.append)
        name = "save_image" if kind == "alpha" else "convert_file"
        operation = getattr(worker_module, name)

        def stop_after_three(*args, **kwargs):
            calls.append(args)
            operation(*args, **kwargs)
            if len(calls) == 3:
                worker.stop()

        with mock.patch.object(worker_module, name, side_effect=stop_after_three), \
                mock.patch.object(ConverterWorker, "_recommend_worker_count", return_value=1):
            worker.run()
        assert len(calls) == 3
        assert finished == [(3, 0)]
        assert list(manifests[0]) == files[:3]
        assert len(list(output.iterdir())) == 3
        assert not list(output.glob(".alpha_fixer_save_*"))


@pytest.mark.parametrize("kind", ["alpha", "converter"])
def test_effective_extension_cannot_overwrite_another_queued_source(queue_root, kind):
    jpg = queue_root / "same.jpg"
    png = queue_root / "same.png"
    with Image.new("RGB", (2, 2), (10, 20, 30)) as image:
        image.save(jpg)
    with Image.new("RGBA", (2, 2), (80, 90, 100, 120)) as image:
        image.save(png)
    original = png.read_bytes()
    files = [str(jpg), str(png)]
    worker = (AlphaWorker(files) if kind == "alpha" else
              ConverterWorker(files, "PNG", ".png"))
    finished, manifests, failures = [], [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)
    worker.file_done.connect(lambda *args: failures.append(args) if not args[1] else None)
    worker.run()
    assert finished == [(1, 1)]
    assert manifests == [{str(png): str(png)}]
    assert failures[0][0] == str(jpg)
    assert "collision" in failures[0][2].lower()
    assert png.read_bytes() == original


def test_converter_alpha_fallback_destination_is_collision_checked(queue_root):
    files = []
    for index in range(2):
        src = queue_root / str(index) / "same.png"
        src.parent.mkdir()
        with Image.new("RGBA", (2, 2), (10, 20, 30, 120)) as image:
            image.save(src)
        files.append(str(src))
    output = queue_root / "out"
    worker = ConverterWorker(files, "JPEG", ".jpg", output_dir=str(output))
    finished, manifests = [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)
    worker.run()
    assert finished == [(1, 1)]
    assert len(manifests[0]) == 1
    assert sorted(path.name for path in output.iterdir()) == ["same.png"]
    with Image.open(output / "same.png") as image:
        assert image.getpixel((0, 0))[3] == 120


@pytest.mark.parametrize("backend", ["cairosvg", "svglib"])
@pytest.mark.parametrize("fails", [False, True])
def test_alternate_svg_loader_closes_decoded_source(queue_root, backend, fails):
    import io
    import sys
    import types

    with io.BytesIO() as buffer:
        with Image.new("RGBA", (2, 2), (10, 20, 30, 120)) as image:
            image.save(buffer, format="PNG")
        png_bytes = buffer.getvalue()
    opened = []
    real_open = Image.open

    def track(*args, **kwargs):
        image = real_open(*args, **kwargs)
        opened.append(image)
        return image

    modules = {
        "cairosvg": types.SimpleNamespace(svg2png=lambda **_kwargs: png_bytes),
        "svglib.svglib": types.SimpleNamespace(svg2rlg=lambda _path: object()),
        "reportlab.graphics": types.SimpleNamespace(
            renderPM=types.SimpleNamespace(drawToString=lambda *_args, **_kwargs: png_bytes)),
    }
    with mock.patch.dict(sys.modules, modules), \
            mock.patch.object(file_converter, "_has_qt_svg", return_value=False), \
            mock.patch.object(file_converter, "_has_cairosvg", return_value=backend == "cairosvg"), \
            mock.patch.object(file_converter, "_has_svglib", return_value=True), \
            mock.patch.object(Image, "open", side_effect=track):
        if fails:
            with mock.patch.object(Image.Image, "convert", side_effect=MemoryError("conversion")), \
                    pytest.raises(MemoryError, match="conversion"):
                file_converter._load_svg("unused.svg")
        else:
            with file_converter._load_svg("unused.svg") as image:
                assert image.getpixel((0, 0)) == (10, 20, 30, 120)
    assert len(opened) == 1
    with pytest.raises(ValueError, match="closed image"):
        opened[0].getpixel((0, 0))


def test_alpha_duplicate_collision_does_not_back_up_already_modified_source(queue_root):
    source = queue_root / "source.png"
    with Image.new("RGBA", (2, 2), (10, 20, 30, 120)) as image:
        image.save(source)
    original = source.read_bytes()
    worker = AlphaWorker([str(source), str(source)], manual_params={"invert": True},
                         overwrite=True, backup_dir=str(queue_root / "backup"))
    finished, backups = [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.backup_manifest.connect(backups.append)
    worker.run()
    assert finished == [(1, 1)]
    assert len(backups[0]) == 1
    assert Path(backups[0][0][1]).read_bytes() == original


def test_converter_can_write_beside_its_own_virtual_source_alias(queue_root):
    source = queue_root / "extracted.png"
    with Image.new("RGBA", (2, 2), (10, 20, 30, 120)) as image:
        image.save(source)
    logical = queue_root / "original" / "animation_frame0001.png"
    worker = ConverterWorker([str(source)], "PNG", ".png",
                             source_aliases={str(source): str(logical)})
    finished, manifests = [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)
    worker.run()
    assert finished == [(1, 0)]
    assert manifests == [{str(source): str(logical)}]
    with Image.open(logical) as image:
        assert image.getpixel((0, 0)) == (10, 20, 30, 120)


@pytest.mark.parametrize("reverse_queue", [False, True])
def test_converter_alias_cannot_overwrite_another_physical_queue_source(queue_root, reverse_queue):
    extracted = queue_root / "extracted.png"
    original = queue_root / "original.png"
    for path, red in ((extracted, 10), (original, 80)):
        with Image.new("RGBA", (2, 2), (red, 20, 30, 120)) as image:
            image.save(path)
    original_bytes = original.read_bytes()
    files = [str(extracted), str(original)]
    if reverse_queue:
        files.reverse()
    worker = ConverterWorker(files, "PNG", ".png",
                             source_aliases={str(extracted): str(original)})
    finished, manifests, failures = [], [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)
    worker.file_done.connect(lambda *args: failures.append(args) if not args[1] else None)
    worker.run()
    assert finished == [(1, 1)]
    assert manifests == [{str(original): str(original)}]
    assert failures[0][0] == str(extracted)
    assert "collision" in failures[0][2].lower()
    assert original.read_bytes() == original_bytes


@pytest.mark.skipif(os.name != "posix", reason="POSIX destination mode preservation")
@pytest.mark.parametrize("kind", ["alpha", "converter"])
def test_worker_staging_preserves_existing_mode_but_keeps_new_outputs_private(queue_root, kind):
    source = queue_root / "source.png"
    with Image.new("RGBA", (2, 2), (10, 20, 30, 120)) as image:
        image.save(source)
    output = queue_root / "out"
    output.mkdir()
    destination = output / source.name
    for existing_mode in (0o640, 0o751, None):
        destination.unlink(missing_ok=True)
        if existing_mode is not None:
            destination.write_bytes(b"previous output")
            destination.chmod(existing_mode)
        worker = (AlphaWorker([str(source)], output_dir=str(output)) if kind == "alpha" else
                  ConverterWorker([str(source)], "PNG", ".png", output_dir=str(output)))
        finished = []
        worker.finished.connect(lambda *args: finished.append(args))
        worker.run()
        assert finished == [(1, 0)]
        assert stat.S_IMODE(destination.stat().st_mode) == (
            0o600 if existing_mode is None else existing_mode)
        with Image.open(destination) as image:
            assert image.getpixel((0, 0)) == (10, 20, 30, 120)
        assert not list(output.glob(".alpha_fixer_save_*"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX destination symlink replacement")
@pytest.mark.parametrize("kind", ["alpha", "converter"])
def test_replaced_destination_symlink_cannot_bypass_reserved_name(queue_root, kind):
    files = []
    for index, red in enumerate((10, 80)):
        path = queue_root / str(index) / "same.png"
        path.parent.mkdir()
        with Image.new("RGBA", (2, 2), (red, 20, 30, 120)) as image:
            image.save(path)
        files.append(str(path))
    target = queue_root / "existing.png"
    target.write_bytes(b"existing symlink target")
    output = queue_root / "out"
    output.mkdir()
    destination = output / "same.png"
    destination.symlink_to(target.resolve())
    worker = (AlphaWorker(files, output_dir=str(output)) if kind == "alpha" else
              ConverterWorker(files, "PNG", ".png", output_dir=str(output)))
    finished, manifests, failures = [], [], []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.output_manifest.connect(manifests.append)
    worker.file_done.connect(lambda *args: failures.append(args) if not args[1] else None)
    with mock.patch.object(ConverterWorker, "_recommend_worker_count", return_value=1):
        worker.run()
    assert finished == [(1, 1)]
    assert manifests == [{files[0]: str(destination)}]
    assert failures[0][0] == files[1]
    assert "collision" in failures[0][2].lower()
    assert not destination.is_symlink()
    assert target.read_bytes() == b"existing symlink target"
    with Image.open(destination) as image:
        assert image.getpixel((0, 0)) == (10, 20, 30, 120)
