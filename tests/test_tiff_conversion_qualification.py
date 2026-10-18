"""Generated TIFF qualification; comparisons concern decoded Pillow pixels."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageCms, TiffImagePlugin

from src.core.file_converter import convert_file


class TestTiffConversionQualification(unittest.TestCase):
    MODES = ("RGBA", "LA", "PA", "RGB", "P", "L", "1", "I;16",
             "I", "F", "CMYK", "LAB", "RGBX")

    def _pattern(self, mode, index):
        image = Image.new(mode, (9 + index % 3, 7 + index % 2))
        if mode in ("P", "PA"):
            image.putpalette([component for value in range(256)
                              for component in (value, 255 - value, (value * 3) % 256)])
        for y in range(image.height):
            for x in range(image.width):
                value = (x * 31 + y * 57 + index * 13) % 256
                alpha = (0, 73, 128, 255)[(x + y) % 4]
                if mode == "RGBA":
                    pixel = (value, 255 - value, (value * 3) % 256, alpha)
                elif mode in ("LA", "PA"):
                    pixel = (value, alpha)
                elif mode in ("RGB", "LAB"):
                    pixel = (value, 255 - value, (value * 3) % 256)
                elif mode in ("CMYK", "RGBX"):
                    pixel = (value, 255 - value, (value * 3) % 256, 255)
                elif mode == "1":
                    pixel = 255 if (x + y) % 2 else 0
                elif mode == "I;16":
                    pixel = value * 257
                elif mode == "I":
                    pixel = (value - 128) * 10000
                elif mode == "F":
                    pixel = (value - 128) / 7
                else:
                    pixel = value
                image.putpixel((x, y), pixel)
        return image

    def _write_pages(self, path, compression="raw", modes=None):
        modes = self.MODES if modes is None else modes
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        with TiffImagePlugin.AppendingTiffWriter(str(path), True) as writer:
            for index, mode in enumerate(modes):
                exif = Image.Exif()
                exif[274] = index % 8 + 1
                if index % 2 == 0:
                    exif[270] = f"generated page {index}"
                    exif[305] = f"writer {index}"
                metadata = ({"icc_profile": profile, "dpi": (90 + index, 110 + index)}
                            if index % 3 == 0 else {})
                with self._pattern(mode, index) as image:
                    image.save(writer, format="TIFF", compression=compression,
                               exif=exif, **metadata)
                writer.newFrame()
        return len(modes)

    def test_patterned_mixed_modes_alpha_orientation_and_per_page_metadata(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, output = Path(directory) / "source.tif", Path(directory) / "output.tif"
            for compression in ("raw", "tiff_lzw"):
                count = self._write_pages(source, compression)
                # Decode each page independently: seeking an existing Pillow
                # image can retain metadata/palettes from the preceding page.
                for keep in (False, True):
                    for resize in (None, (5, 4)):
                        with self.subTest(compression=compression, keep=keep, resize=resize):
                            convert_file(str(source), str(output), "TIFF",
                                         keep_metadata=keep, resize=resize)
                            with Image.open(output) as pages:
                                self.assertEqual(pages.n_frames, count)
                            for index in range(count):
                                with self.subTest(page=index, mode=self.MODES[index]), \
                                        Image.open(source) as original, Image.open(output) as saved:
                                    if index:
                                        original.info.clear()
                                        original.palette = None
                                    original.seek(index)
                                    original.load()
                                    if index:
                                        saved.info.clear()
                                        saved.palette = None
                                    saved.seek(index)
                                    saved.load()
                                    with (original.convert("RGBA") if resize and original.mode == "PA"
                                          else original.copy()) as native, \
                                            (native.resize(resize, Image.Resampling.LANCZOS)
                                             if resize else native.copy()) as reference:
                                        self.assertEqual(saved.mode, reference.mode)
                                        self.assertEqual(saved.size, reference.size)
                                        self.assertEqual(saved.tobytes(), reference.tobytes())
                                        if reference.mode in ("P", "PA"):
                                            self.assertEqual(saved.getpalette(), reference.getpalette())
                                        if reference.mode in ("P", "PA", "LA", "RGBA"):
                                            with saved.convert("RGBA") as actual_rgba, \
                                                    reference.convert("RGBA") as expected_rgba:
                                                self.assertEqual(actual_rgba.tobytes(), expected_rgba.tobytes())
                                    self.assertIn(saved.getexif().get(274), (None, 1))
                                    for tag in (270, 305):
                                        self.assertEqual(saved.getexif().get(tag),
                                                         original.getexif().get(tag) if keep else None)
                                    self.assertEqual(saved.info.get("icc_profile"),
                                                     original.info.get("icc_profile") if keep else None)
                                    if keep and index % 3 == 0:
                                        self.assertEqual(tuple(map(float, saved.info["dpi"])),
                                                         (90 + index, 110 + index))

    def _next_pointer(self, data, offset):
        count = struct.unpack_from("<H", data, offset)[0]
        return offset + 2 + 12 * count

    def test_palette_alpha_first_page_exports_have_decoded_colors_and_alpha(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "source.tif"
            self._write_pages(source, modes=("PA", "RGB"))
            with Image.open(source) as original:
                original.load()
                with original.convert("RGBA") as expected:
                    with expected.getchannel("A") as alpha:
                        self.assertEqual(alpha.getextrema(), (0, 255))
                    for target in ("PNG", "TGA"):
                        for resize in (None, (5, 4)):
                            for keep in (False, True):
                                output = Path(directory) / f"output.{target.lower()}"
                                with self.subTest(target=target, resize=resize, keep=keep):
                                    with self.assertWarnsRegex(UserWarning, "page 1 of 2 only"):
                                        convert_file(str(source), str(output), target,
                                                     resize=resize, keep_metadata=keep)
                                    with Image.open(output) as saved, \
                                            (expected.resize(resize, Image.Resampling.LANCZOS)
                                             if resize else expected.copy()) as reference:
                                        saved.load()
                                        self.assertEqual(saved.format, target)
                                        self.assertEqual(saved.mode, "RGBA")
                                        self.assertEqual(saved.size, reference.size)
                                        self.assertEqual(saved.tobytes(), reference.tobytes())

    def test_cyclic_trailing_directory_is_rejected_without_replacing_destination(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, output = Path(directory) / "source.tif", Path(directory) / "output.tif"
            for modes in (("RGB",), ("RGB", "RGBA", "L")):
                for backward_link in ("first", "self"):
                    self._write_pages(source, modes=modes)
                    data = bytearray(source.read_bytes())
                    first = offset = struct.unpack_from("<I", data, 4)[0]
                    while struct.unpack_from("<I", data, self._next_pointer(data, offset))[0]:
                        offset = struct.unpack_from("<I", data, self._next_pointer(data, offset))[0]
                    struct.pack_into("<I", data, self._next_pointer(data, offset),
                                     first if backward_link == "first" else offset)
                    source.write_bytes(data)
                    for destination in (output, source):
                        with self.subTest(modes=modes, link=backward_link, in_place=destination == source):
                            if destination == output:
                                output.write_bytes(b"existing output")
                            previous = destination.read_bytes()
                            with self.assertRaisesRegex(OSError, "TIFF.*directory"):
                                convert_file(str(source), str(destination), "TIFF")
                            self.assertEqual(destination.read_bytes(), previous)
                            self.assertEqual(set(Path(directory).iterdir()), {source, output})

    def test_real_truncated_trailing_strip_closes_resources_and_rolls_back(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, output = Path(directory) / "source.tif", Path(directory) / "output.tif"
            self._write_pages(source, modes=("RGB", "RGBA", "LA"))
            with Image.open(source) as pages:
                self.assertEqual(pages.n_frames, 3)
                pages.seek(2)
                offset = pages.tag_v2[273][0]
                length = pages.tag_v2[279][0]
            source.write_bytes(source.read_bytes()[:offset + length // 2])
            with Image.open(source) as pages:
                self.assertEqual(pages.n_frames, 3)
                pages.seek(0)
                pages.load()
                pages.seek(2)
                with self.assertRaises(OSError):
                    pages.load()
            real_open, real_copy = Image.open, Image.Image.copy
            for existing in (False, True):
                handles, copies = [], []
                if existing:
                    output.write_bytes(b"existing output")

                def tracked_open(*args, **kwargs):
                    image = real_open(*args, **kwargs)
                    handles.append(image.fp)
                    return image

                def tracked_copy(image):
                    page = real_copy(image)
                    copies.append(page)
                    return page

                with self.subTest(existing=existing), \
                        mock.patch("src.core.file_converter.Image.open", side_effect=tracked_open), \
                        mock.patch.object(Image.Image, "copy", tracked_copy):
                    with self.assertRaises(OSError):
                        convert_file(str(source), str(output), "TIFF", keep_metadata=True)
                self.assertTrue(handles)
                self.assertTrue(all(handle.closed for handle in handles))
                self.assertEqual(len(copies), 2)
                for page in copies:
                    with self.assertRaises(ValueError):
                        page.getpixel((0, 0))
                if existing:
                    self.assertEqual(output.read_bytes(), b"existing output")
                else:
                    self.assertFalse(output.exists())
                self.assertEqual(set(Path(directory).iterdir()),
                                 {source, output} if existing else {source})


if __name__ == "__main__":
    unittest.main()
