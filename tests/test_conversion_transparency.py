"""Generated color-key/palette fixtures for lossless image conversion."""

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms, features

from src.core.file_converter import convert_file


class TestColorKeyConversion(unittest.TestCase):
    def _make_source(self, directory, mode):
        source = Path(directory) / f"source-{mode}.png"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        exif = Image.Exif()
        exif[274] = 6
        exif[305] = "generated alpha fixture"
        with Image.new(mode, (6, 4)) as image:
            indices = [(x + y) % 3 for y in range(4) for x in range(6)]
            if mode == "P":
                image.putpalette([10, 20, 30, 80, 100, 120, 180, 200, 220] + [0] * 759)
                image.putdata(indices)
                transparency = bytes([0, 73, 255])
            elif mode == "RGB":
                colors = [(10, 20, 30), (80, 100, 120), (180, 200, 220)]
                image.putdata([colors[index] for index in indices])
                transparency = colors[0]
            else:
                values = [10, 100, 220]
                image.putdata([values[index] for index in indices])
                transparency = values[0]
            image.save(source, transparency=transparency, exif=exif,
                       icc_profile=profile, dpi=(144, 72))
        with Image.open(source) as original:
            with original.convert("RGBA") as rgba:
                expected = rgba.transpose(Image.Transpose.ROTATE_270)
        return source, expected, profile

    def test_lossless_outputs_preserve_display_pixels_and_alpha(self):
        formats = [("TIFF", ".tiff"), ("TIFF", ".tif"), ("TGA", ".tga"),
                   ("QOI", ".qoi"), ("PNG", ".png")]
        if features.check("jpg_2000"):
            formats.append(("JPEG2000", ".jp2"))
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for mode in ("P", "RGB", "L"):
                source, expected, profile = self._make_source(directory, mode)
                try:
                    for target, extension in formats:
                        for keep in (False, True):
                            for resize in (None, (9, 7)):
                                with self.subTest(mode=mode, target=extension, keep=keep,
                                                  resize=resize):
                                    output = Path(directory) / f"result{extension}"
                                    convert_file(str(source), str(output), target, quality=100,
                                                 resize=resize, keep_metadata=keep)
                                    with Image.open(output) as saved, (
                                        expected.resize(resize, Image.LANCZOS) if resize
                                        else expected.copy()
                                    ) as reference, saved.convert("RGBA") as actual:
                                        self.assertEqual(actual.size, reference.size)
                                        self.assertEqual(actual.tobytes(), reference.tobytes())
                                        with actual.getchannel("A") as alpha:
                                            self.assertLess(alpha.getextrema()[0], 255)
                                        self.assertNotIn(274, saved.getexif())
                                        if extension in (".png", ".tif", ".tiff"):
                                            if mode == "L" and (resize or extension != ".png"):
                                                self.assertEqual(saved.mode, "LA")
                                            if keep:
                                                self.assertEqual(saved.getexif()[305],
                                                                 "generated alpha fixture")
                                                self.assertEqual(saved.info["icc_profile"], profile)
                                                for actual_dpi, expected_dpi in zip(
                                                        saved.info["dpi"], (144, 72)):
                                                    self.assertAlmostEqual(actual_dpi, expected_dpi,
                                                                           delta=0.02)
                                            else:
                                                self.assertNotIn(305, saved.getexif())
                                                self.assertNotIn("icc_profile", saved.info)
                finally:
                    expected.close()

    def test_opaque_native_modes_remain_unchanged_for_tiff(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for mode in ("P", "RGB", "L"):
                source, output = Path(directory) / "opaque.png", Path(directory) / "opaque.tiff"
                with Image.new(mode, (6, 4)) as image:
                    if mode == "P":
                        image.putpalette([value for value in range(256) for _ in range(3)])
                    image.save(source)
                convert_file(str(source), str(output), "TIFF")
                with Image.open(source) as original, Image.open(output) as saved:
                    self.assertEqual(saved.mode, mode)
                    self.assertEqual(saved.tobytes(), original.tobytes())
                    if mode == "P":
                        self.assertEqual(saved.getpalette(), original.getpalette())
