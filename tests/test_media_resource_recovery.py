import io
import sys
import types

import pytest
from PIL import Image

from src.core import alpha_processor, file_converter


@pytest.fixture
def rendered_png():
    with io.BytesIO() as buffer:
        with Image.new("RGBA", (8, 8), (20, 40, 60, 128)) as image:
            image.save(buffer, format="PNG")
        return buffer.getvalue()


@pytest.fixture(params=["cairosvg", "svglib", "wand"])
def renderer_bridge(request, monkeypatch):
    payload = {}
    monkeypatch.setattr(file_converter, "_has_qt_svg", lambda: False)
    monkeypatch.setattr(file_converter, "_has_cairosvg", lambda: request.param == "cairosvg")
    monkeypatch.setattr(file_converter, "_has_svglib", lambda: request.param == "svglib")
    if request.param == "cairosvg":
        monkeypatch.setitem(sys.modules, "cairosvg", types.SimpleNamespace(
            svg2png=lambda **kwargs: payload["png"],
        ))
    elif request.param == "svglib":
        monkeypatch.setitem(sys.modules, "svglib", types.ModuleType("svglib"))
        monkeypatch.setitem(sys.modules, "svglib.svglib", types.SimpleNamespace(
            svg2rlg=lambda path: object(),
        ))
        monkeypatch.setitem(sys.modules, "reportlab", types.ModuleType("reportlab"))
        monkeypatch.setitem(sys.modules, "reportlab.graphics", types.SimpleNamespace(
            renderPM=types.SimpleNamespace(drawToString=lambda *args, **kwargs: payload["png"]),
        ))
    else:
        class WandImage:
            def __init__(self, **kwargs):
                self.closed = False
                payload.setdefault("wand_images", []).append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.closed = True

            def make_blob(self):
                return payload["png"]

        monkeypatch.setitem(sys.modules, "wand", types.ModuleType("wand"))
        monkeypatch.setitem(sys.modules, "wand.image", types.SimpleNamespace(Image=WandImage))
        monkeypatch.setattr(alpha_processor, "_has_wand", lambda: True)
        # Exercise the production DDS fallback without native ImageMagick.
        return payload, ".dds"
    return payload, ".svg"


@pytest.fixture
def owned_buffers(monkeypatch):
    real_bytes_io = io.BytesIO
    buffers = []

    def tracked_bytes_io(*args, **kwargs):
        buffer = real_bytes_io(*args, **kwargs)
        buffers.append(buffer)
        return buffer

    tracked_io = types.SimpleNamespace(BytesIO=tracked_bytes_io)
    monkeypatch.setattr(file_converter, "io", tracked_io)
    monkeypatch.setattr(alpha_processor, "io", tracked_io)
    yield buffers
    for buffer in buffers:
        buffer.close()


@pytest.mark.parametrize("operation", ["import", "preview", "conversion"])
@pytest.mark.parametrize("corruption", ["header", "pixels"])
def test_renderer_decode_failure_releases_buffers_and_valid_retry(
    tmp_path, renderer_bridge, rendered_png, owned_buffers, operation, corruption,
):
    payload, extension = renderer_bridge
    source = tmp_path / ("source" + extension)
    source.write_bytes(b"malformed source routed through controlled renderer")
    output = tmp_path / "output.png"

    def run():
        if operation == "conversion":
            file_converter.convert_file(str(source), str(output), "PNG")
            with Image.open(output) as image:
                assert image.getpixel((0, 0)) == (20, 40, 60, 128)
        else:
            opener = (alpha_processor.load_image if operation == "import"
                      else lambda path: file_converter._open_image_for_preview(path, 32))
            with opener(str(source)) as image:
                assert image.mode == "RGBA"
                assert image.size == (8, 8)
                assert image.getpixel((0, 0)) == (20, 40, 60, 128)

    # Retain each buffer so CPython refcounting cannot conceal missing cleanup.
    payload["png"] = b"not a PNG" if corruption == "header" else rendered_png[:45]
    for _ in range(6):
        with pytest.raises(ValueError if extension == ".dds" else OSError):
            run()
        assert owned_buffers and all(buffer.closed for buffer in owned_buffers)
        assert not output.exists()
        payload["png"] = rendered_png
        run()
        assert all(buffer.closed for buffer in owned_buffers)
        output.unlink(missing_ok=True)
        payload["png"] = b"not a PNG" if corruption == "header" else rendered_png[:45]
    assert all(image.closed for image in payload.get("wand_images", []))


def test_renderer_open_memory_error_closes_buffer_without_fallback(
    renderer_bridge, rendered_png, owned_buffers, monkeypatch,
):
    payload, extension = renderer_bridge
    payload["png"] = rendered_png
    failure = MemoryError("controlled allocation failure")
    def fail_open(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Image, "open", fail_open)
    opener = (file_converter._load_svg if extension == ".svg"
              else alpha_processor._try_load_dds_via_wand)
    with pytest.raises(MemoryError) as raised:
        opener("source" + extension)
    assert raised.value is failure
    assert len(owned_buffers) == 1
    assert owned_buffers[0].closed
    assert all(image.closed for image in payload.get("wand_images", []))


@pytest.mark.parametrize("image_format", ["PNG", "TIFF"])
@pytest.mark.parametrize("operation", ["import", "preview", "conversion"])
def test_truncated_file_closes_images_and_handles_before_valid_retry(
    tmp_path, monkeypatch, image_format, operation,
):
    source = tmp_path / ("source." + image_format.lower())
    output = tmp_path / "output.png"
    with Image.new("RGBA", (16, 16), (20, 40, 60, 128)) as image:
        image.save(source, format=image_format)
    valid = source.read_bytes()
    with Image.open(source) as image:
        if image_format == "TIFF":
            cutoff = image.tag_v2[273][0] + image.tag_v2[279][0] // 2
        else:
            cutoff = valid.index(b"IDAT") + 5
    truncated = valid[:cutoff]
    opened_images, handles = [], []
    real_open = Image.open

    def tracked_open(*args, **kwargs):
        image = real_open(*args, **kwargs)
        opened_images.append(image)
        handles.append(image.fp)
        return image

    monkeypatch.setattr(Image, "open", tracked_open)

    def run():
        if operation == "conversion":
            file_converter.convert_file(str(source), str(output), "PNG")
        else:
            opener = (alpha_processor.load_image if operation == "import"
                      else lambda path: file_converter._open_image_for_preview(path, 4))
            image = opener(str(source))
            try:
                pixel = image.getpixel((0, 0))
                assert pixel[3] == 128
                assert all(abs(actual - expected) <= 1
                           for actual, expected in zip(pixel[:3], (20, 40, 60)))
            finally:
                image.close()

    for _ in range(6):
        source.write_bytes(truncated)
        with pytest.raises((OSError, ValueError)):
            run()
        assert handles and all(handle.closed for handle in handles)
        assert not output.exists()
        source.write_bytes(valid)
        run()
        output.unlink(missing_ok=True)
        assert handles and all(handle.closed for handle in handles)
        for image in opened_images:
            # TIFF page-count probes close their file without allocating pixels.
            try:
                pixels = image.im
            except (ValueError, AssertionError):
                continue
            if pixels is not None:
                with pytest.raises(ValueError):
                    image.getpixel((0, 0))
