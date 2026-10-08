"""
Tests for file converter utilities.
"""
import sys
import os
import json
import hashlib
import io
import tempfile
import unittest
from unittest import mock
import types
import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.corpus_helpers import (
    _iter_corpus_files,
    _optional_corpus_roots,
    _optional_manifest_entries,
)

from src.core.file_converter import (
    convert_file,
    build_output_path,
    SUPPORTED_OUTPUT_FORMATS,
    DDS_VARIANT_OPTIONS,
    _open_image_for_preview,
    _flatten_alpha,
    output_format_discards_alpha,
    output_format_available,
    output_format_unavailable_reason,
)
from src.core.alpha_processor import SUPPORTED_WRITE, detect_atlas_cells, save_image
from src.core.runtime_validation import (
    execute_dds_manifest,
    execute_disc_video_manifest,
    execute_format_matrix_manifest,
    load_manifest_entries,
    materialize_manifest_entries,
)
from src.core.worker import AlphaWorker, ConverterWorker
from src.version import APP_NAME


def _make_png(path: str, w=8, h=8, alpha=200):
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 0] = 200
    arr[:, :, 1] = 100
    arr[:, :, 2] = 50
    arr[:, :, 3] = alpha
    img = Image.fromarray(arr, "RGBA")
    img.save(path)


def _make_rgb_png(path: str, w=8, h=8):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = 200
    arr[:, :, 1] = 100
    arr[:, :, 2] = 50
    img = Image.fromarray(arr, "RGB")
    img.save(path)


def _make_palette_png(path: str, w=8, h=8):
    img = Image.new("P", (w, h))
    img.putpalette([i % 256 for i in range(256 * 3)])
    img.save(path)


def _make_raw_dds(
    path: str,
    width: int,
    height: int,
    bits: int,
    pixel_data: bytes,
    *,
    pf_flags: int = 0x41,
    r_mask: int = 0x00FF0000,
    g_mask: int = 0x0000FF00,
    b_mask: int = 0x000000FF,
    a_mask: int = 0xFF000000,
):
    def dword(n: int) -> bytes:
        return int(n).to_bytes(4, "little")

    header = bytearray(128)
    header[0:4] = b"DDS "
    header[4:8] = dword(124)
    header[8:12] = dword(0x000A1007)
    header[12:16] = dword(height)
    header[16:20] = dword(width)
    header[20:24] = dword(width * max(1, bits // 8))
    header[76:80] = dword(32)
    header[80:84] = dword(pf_flags)
    header[88:92] = dword(bits)
    header[92:96] = dword(r_mask)
    header[96:100] = dword(g_mask)
    header[100:104] = dword(b_mask)
    header[104:108] = dword(a_mask)
    header[108:112] = dword(0x1000)
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(pixel_data)


def _make_compressed_dds(
    path: str,
    width: int,
    height: int,
    fourcc: bytes,
    pixel_data: bytes,
):
    def dword(n: int) -> bytes:
        return int(n).to_bytes(4, "little")

    header = bytearray(128)
    header[0:4] = b"DDS "
    header[4:8] = dword(124)
    header[8:12] = dword(0x000A1007)
    header[12:16] = dword(height)
    header[16:20] = dword(width)
    header[76:80] = dword(32)
    header[80:84] = dword(0x4)
    header[84:88] = fourcc[:4].ljust(4, b"\x00")
    header[108:112] = dword(0x1000)
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(pixel_data)


def _make_dx10_dds(
    path: str,
    width: int,
    height: int,
    dxgi_format: int,
    pixel_data: bytes,
    *,
    array_size: int = 1,
    resource_dimension: int = 3,
    misc_flag: int = 0,
    mipmap_count: int = 0,
    caps2: int = 0,
    depth: int = 0,
):
    def dword(n: int) -> bytes:
        return int(n).to_bytes(4, "little")

    header = bytearray(148)
    header[0:4] = b"DDS "
    header[4:8] = dword(124)
    header[8:12] = dword(0x000A1007)
    header[12:16] = dword(height)
    header[16:20] = dword(width)
    header[24:28] = dword(depth)
    header[28:32] = dword(mipmap_count)
    header[76:80] = dword(32)
    header[80:84] = dword(0x4)
    header[84:88] = b"DX10"
    header[108:112] = dword(0x1000)
    header[112:116] = dword(caps2)
    header[128:132] = dword(dxgi_format)
    header[132:136] = dword(resource_dimension)
    header[136:140] = dword(misc_flag)
    header[140:144] = dword(array_size)
    header[144:148] = dword(0)   # misc flags 2
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(pixel_data)


class TestBuildOutputPath(unittest.TestCase):

    def test_same_dir(self):
        result = build_output_path("/some/dir/file.png", ".dds")
        self.assertEqual(result, "/some/dir/file.dds")

    def test_output_dir(self):
        result = build_output_path("/some/dir/file.png", ".jpg", output_dir="/out")
        self.assertEqual(result, "/out/file.jpg")

    def test_output_dir_with_root(self):
        result = build_output_path(
            "/src/sub/file.png", ".jpg",
            output_dir="/out",
            input_root="/src",
        )
        self.assertEqual(result, "/out/sub/file.jpg")


class TestCorpusHelperInputs(unittest.TestCase):
    def test_built_in_public_disc_video_manifest_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "public_disc_video_manifest.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertGreaterEqual(len(entries), 11)
        self.assertTrue(any(str(entry.get("download_url") or "").startswith("https://raw.githubusercontent.com/") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".iso") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".avi") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".mov") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".mpg") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".mkv") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".webm") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".aac") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".ac3") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".wma") for entry in entries))
        self.assertTrue(any("odd container" in str(entry.get("group") or "").lower() or "legacy container" in str(entry.get("group") or "").lower() for entry in entries))
        self.assertTrue(any("audio-only" in str(entry.get("group") or "").lower() for entry in entries))
        self.assertTrue(any(str(entry.get("expect") or "").lower() == "fail" for entry in entries))

    def test_built_in_public_format_matrix_manifest_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "public_format_matrix_manifest.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertGreaterEqual(len(entries), 3)
        self.assertTrue(all(str(entry.get("download_url") or "").startswith("https://raw.githubusercontent.com/") for entry in entries))
        self.assertEqual({str(entry.get("target_format") or "").upper() for entry in entries}, {"DDS", "PNG", "BMP"})

    def test_built_in_public_dds_dx10_manifest_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "public_dds_dx10_manifest.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertGreaterEqual(len(entries), 10)
        self.assertTrue(all(str(entry.get("download_url") or "").startswith("https://raw.githubusercontent.com/") for entry in entries))
        self.assertTrue(any("bc6h" in str(entry.get("path") or "").lower() for entry in entries))
        self.assertTrue(any("dxgi" in str(entry.get("path") or "").lower() for entry in entries))
        self.assertTrue(any("mipmap" in str(entry.get("group") or "").lower() or "mipmaps" in str(entry.get("path") or "").lower() for entry in entries))
        self.assertTrue(any("bc4" in str(entry.get("group") or "").lower() or "ati1" in str(entry.get("path") or "").lower() for entry in entries))
        self.assertTrue(any("bc5" in str(entry.get("group") or "").lower() or "ati2" in str(entry.get("path") or "").lower() for entry in entries))
        self.assertTrue(any(isinstance(entry.get("expect_size"), list) and len(entry.get("expect_size")) == 2 for entry in entries))
        self.assertTrue(any(str(entry.get("expect") or "").lower() == "fail" for entry in entries))

    def test_private_psp_ps1_ps2_disc_manifest_template_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "private_psp_ps1_ps2_disc_manifest_template.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertEqual(len(entries), 3)
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("psp", "sample.umd.iso")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("ps1", "sample.bin")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("ps2", "sample.iso")) for entry in entries))
        ps1_entry = next(entry for entry in entries if str(entry.get("path") or "").endswith(os.path.join("ps1", "sample.bin")))
        companions = ps1_entry.get("companions")
        self.assertIsInstance(companions, list)
        self.assertTrue(any(str(item.get("path") or "").endswith(os.path.join("ps1", "sample.cue")) for item in companions if isinstance(item, dict)))
        self.assertTrue(all("Disc-image video inputs are experimental" in (entry.get("hint_contains") or [""])[0] for entry in entries))

    def test_private_odd_container_manifest_template_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "private_odd_container_manifest_template.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertEqual(len(entries), 5)
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.pss")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.str")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.vob")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "movie.vob.001")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.dat")) for entry in entries))
        self.assertTrue(any("segmented / multipart video set" in " ".join(entry.get("hint_contains") or []) for entry in entries if isinstance(entry, dict)))

    def test_private_dds_complex_manifest_template_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "private_dds_complex_manifest_template.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertEqual(len(entries), 5)
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("dds", "bc6h-hdr.dds")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("dds", "mipmap-chain.dds")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("dds", "cubemap.dds")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("dds", "texture-array.dds")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("dds", "volume-texture.dds")) for entry in entries))
        self.assertTrue(any(str(entry.get("expect") or "").lower() == "load_or_fail_clearly" for entry in entries))
        self.assertTrue(any(str(entry.get("expect") or "").lower() == "fail" for entry in entries))

    def test_private_odd_container_video_manifest_template_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "private_odd_container_video_manifest_template.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertEqual(len(entries), 5)
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "video_sample.dat")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "audio_only.wma")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "multi_stream.mkv")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "clip.001.vob")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "cover_art_container.mkv")) for entry in entries))
        self.assertTrue(any("audio but no playable video stream" in " ".join(entry.get("hint_contains") or []) for entry in entries))
        self.assertTrue(any("Multiple video streams were detected" in " ".join(entry.get("hint_contains") or []) for entry in entries))

    def test_optional_manifest_entries_accepts_manifest_file_and_resolves_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = os.path.join(tmpdir, "samples")
            os.makedirs(sample_dir, exist_ok=True)
            sample_path = os.path.join(sample_dir, "clip.dds")
            with open(sample_path, "wb") as handle:
                handle.write(b"dds")
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump([{"path": "samples/clip.dds", "expect": "load"}], handle)
            with mock.patch.dict(os.environ, {"ALPHA_FIXER_REAL_DDS_DX10_MANIFEST": manifest_path}, clear=False):
                entries = _optional_manifest_entries("ALPHA_FIXER_REAL_DDS_DX10_MANIFEST")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], sample_path)
        self.assertEqual(entries[0]["expect"], "load")

    def test_optional_manifest_entries_accepts_samples_wrapper_and_base_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = os.path.join(tmpdir, "sample.bc7.dds")
            with open(sample_path, "wb") as handle:
                handle.write(b"dds")
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "base_dir": ".",
                        "samples": [{"path": "sample.bc7.dds", "expect": "fail"}],
                    },
                    handle,
                )
            with mock.patch.dict(os.environ, {"ALPHA_FIXER_REAL_DDS_DX10_MANIFEST": manifest_path}, clear=False):
                entries = _optional_manifest_entries("ALPHA_FIXER_REAL_DDS_DX10_MANIFEST")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], sample_path)
        self.assertEqual(entries[0]["expect"], "fail")

    def test_load_manifest_entries_resolves_companion_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "manifest.json")
            os.makedirs(os.path.join(tmpdir, "samples"), exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "base_dir": ".",
                        "entries": [
                            {
                                "path": "samples/disc.bin",
                                "companions": [{"path": "samples/disc.cue"}],
                            }
                        ],
                    },
                    handle,
                )
            entries = load_manifest_entries(manifest_path)
        companions = entries[0].get("companions")
        self.assertIsInstance(companions, list)
        self.assertEqual(companions[0]["path"], os.path.join(tmpdir, "samples", "disc.cue"))

    def test_runtime_manifest_loader_resolves_input_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = os.path.join(tmpdir, "sample.png")
            with open(sample_path, "wb") as handle:
                handle.write(b"png")
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "base_dir": ".",
                        "entries": [{"input": "sample.png", "target_format": "DDS"}],
                    },
                    handle,
                )
            entries = load_manifest_entries(manifest_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["input"], sample_path)
        self.assertEqual(entries[0]["target_format"], "DDS")

    def test_materialize_manifest_entries_downloads_missing_url_backed_sample(self):
        sample_bytes = b"odd-container-sample"
        digest = hashlib.sha256(sample_bytes).hexdigest()
        entries = [
            {
                "path": "missing/sample.iso",
                "url": "https://example.invalid/sample.iso",
                "sha256": digest,
                "download_name": "sample.iso",
                "expect": "load_or_explain",
            }
        ]

        class _FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.close()
                return False

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("src.core.runtime_validation.urlopen", return_value=_FakeResponse(sample_bytes)):
                materialized = materialize_manifest_entries(entries, cache_dir=tmpdir, allow_download=True)
            self.assertEqual(len(materialized), 1)
            self.assertTrue(os.path.isfile(materialized[0]["path"]))
            with open(materialized[0]["path"], "rb") as handle:
                self.assertEqual(handle.read(), sample_bytes)

    def test_materialize_manifest_entries_copies_existing_local_samples_when_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "source.dds")
            cache_dir = os.path.join(tmpdir, "cache")
            with open(source_path, "wb") as handle:
                handle.write(b"dds")
            materialized = materialize_manifest_entries(
                [{"path": source_path, "expect": "load"}],
                cache_dir=cache_dir,
                copy_local=True,
            )
            copied_path = materialized[0]["path"]
            self.assertNotEqual(os.path.abspath(copied_path), os.path.abspath(source_path))
            self.assertTrue(os.path.isfile(copied_path))
            with open(copied_path, "rb") as handle:
                self.assertEqual(handle.read(), b"dds")

    def test_materialize_manifest_entries_copy_companions_with_primary_sample(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "sample.bin")
            cue_path = os.path.join(tmpdir, "sample.cue")
            cache_dir = os.path.join(tmpdir, "cache")
            with open(source_path, "wb") as handle:
                handle.write(b"bin")
            with open(cue_path, "w", encoding="utf-8") as handle:
                handle.write('FILE "sample.bin" BINARY\n')
            materialized = materialize_manifest_entries(
                [{"path": source_path, "companions": [{"path": cue_path}]}],
                cache_dir=cache_dir,
                copy_local=True,
            )
            copied_path = materialized[0]["path"]
            companions = materialized[0].get("companions")
            self.assertTrue(os.path.isfile(copied_path))
            self.assertIsInstance(companions, list)
            self.assertTrue(os.path.isfile(companions[0]["path"]))
            self.assertEqual(os.path.dirname(companions[0]["path"]), os.path.dirname(copied_path))

    def test_materialize_manifest_entries_rejects_bad_checksum(self):
        sample_bytes = b"sample"

        class _FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.close()
                return False

        entries = [
            {
                "path": "missing/sample.dds",
                "url": "https://example.invalid/sample.dds",
                "sha256": "0" * 64,
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("src.core.runtime_validation.urlopen", return_value=_FakeResponse(sample_bytes)):
                with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                    materialize_manifest_entries(entries, cache_dir=tmpdir, allow_download=True)

    def test_execute_disc_video_manifest_enforces_required_missing_samples(self):
        entries = [{"path": "/tmp/missing-disc.iso", "required": True, "platform": "PSP"}]
        ok, detail = execute_disc_video_manifest(entries, types.SimpleNamespace())
        self.assertFalse(ok)
        self.assertIn("required sample missing", detail)

    def test_execute_disc_video_manifest_checks_probe_and_clip_expectations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = os.path.join(tmpdir, "sample.iso")
            with open(sample_path, "wb") as handle:
                handle.write(b"disc")

            class _FakeClip:
                def __init__(self):
                    self.source_path = sample_path
                    self.total_frames = 48
                    self.fps = 24.0
                    self.frame_size = (320, 240)
                    self.has_audio = True
                    self.load_note = "temporary ffmpeg transcode fallback active"
                    self.load_strategy = self.load_note
                    self.preferred_video_stream_index = 4
                    self.preferred_audio_stream_index = 9

                def close(self):
                    pass

            fake_tool = types.SimpleNamespace(
                _probe_media_details=lambda *_args, **_kwargs: {
                    "has_video": True,
                    "has_audio": True,
                    "video_codec": "mpeg2video",
                    "audio_codec": "ac3",
                    "format_name": "mpeg,iso",
                    "width": 320,
                    "height": 240,
                    "video_stream_count": 2,
                    "audio_stream_count": 3,
                    "video_stream_index": 4,
                    "audio_stream_index": 9,
                },
                _load_video_clip=lambda *_args, **_kwargs: _FakeClip(),
                _video_load_failure_hint=lambda *_args, **_kwargs: "Disc-image video inputs are experimental",
            )
            entries = [{
                "platform": "PS2",
                "sample_id": "main-stream",
                "path": sample_path,
                "required": True,
                "expect": "load",
                "min_frames": 10,
                "min_duration_seconds": 1.5,
                "expect_has_audio": True,
                "expect_frame_size": [320, 240],
                "expect_probe_has_video": True,
                "expect_probe_has_audio": True,
                "expect_video_codec_contains": ["mpeg2"],
                "expect_audio_codec_contains": ["ac3"],
                "expect_format_name_contains": ["iso"],
                "expect_video_stream_count": 2,
                "expect_audio_stream_count": 3,
                "expect_video_stream_index": 4,
                "expect_audio_stream_index": 9,
                "expect_preferred_video_stream_index": 4,
                "expect_preferred_audio_stream_index": 9,
                "require_recovery": True,
                "expect_load_note_contains": ["transcode fallback"],
                "hint_contains": ["experimental"],
            }]

            ok, detail = execute_disc_video_manifest(entries, fake_tool)
        self.assertTrue(ok, msg=detail)
        self.assertIn("platforms=PS2:1", detail)

    def test_execute_dds_manifest_enforces_required_sample_and_expected_size(self):
        entries = [{"path": "/tmp/missing.bc6h.dds", "required": True, "group": "BC6H"}]
        ok, detail = execute_dds_manifest(entries, lambda _path: None)
        self.assertFalse(ok)
        self.assertIn("required sample missing", detail)

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = os.path.join(tmpdir, "sample.dds")
            with open(sample_path, "wb") as handle:
                handle.write(b"dds")

            class _FakeImage:
                size = (64, 32)

                def close(self):
                    pass

            ok, detail = execute_dds_manifest(
                [{"path": sample_path, "group": "DX10", "expect": "load", "expect_size": [64, 32]}],
                lambda _path: _FakeImage(),
            )
        self.assertTrue(ok, msg=detail)
        self.assertIn("groups=DX10:1", detail)

    def test_populate_sample_manifest_script_materializes_and_writes_manifest(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "populate_sample_manifest.py")
        spec = importlib.util.spec_from_file_location("populate_sample_manifest", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = os.path.join(tmpdir, "sample.bin")
            cache_dir = os.path.join(tmpdir, "cache")
            out_manifest = os.path.join(tmpdir, "materialized.json")
            with open(sample_path, "wb") as handle:
                handle.write(b"media")
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump({"entries": [{"path": sample_path}]}, handle)
            rc = script.main(
                [
                    manifest_path,
                    "--cache-dir",
                    cache_dir,
                    "--copy-local",
                    "--output-manifest",
                    out_manifest,
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(Path(out_manifest).read_text(encoding="utf-8"))
            self.assertEqual(Path(payload["base_dir"]).resolve(), Path(cache_dir).resolve())
            self.assertTrue(os.path.isfile(payload["entries"][0]["path"]))


class TestRuntimeFormatMatrixManifest(unittest.TestCase):
    def test_execute_format_matrix_manifest_validates_successful_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = os.path.join(tmpdir, "sample.png")
            _make_png(sample_path)
            entries = [{"input": sample_path, "target_format": "PNG"}]

            def _fake_convert(src, dst, target_format, **_kwargs):
                self.assertEqual(src, sample_path)
                self.assertEqual(target_format, "PNG")
                _make_png(dst, w=4, h=4)
                return dst

            ok, detail = execute_format_matrix_manifest(
                entries,
                convert_file=_fake_convert,
                load_dds=lambda path: Image.open(path),
                image_module=Image,
                output_formats={"PNG": ".png"},
                tmpdir=tmpdir,
            )

        self.assertTrue(ok)
        self.assertIn("converted=1", detail)

    def test_execute_format_matrix_manifest_checks_expected_failure_tokens(self):
        entries = [
            {
                "input": "/tmp/missing.png",
                "target_format": "DDS",
                "expect": "fail",
                "detail_contains": ["ImageMagick"],
            }
        ]

        def _fake_convert(_src, _dst, _target_format, **_kwargs):
            raise ValueError("ImageMagick runtime unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = os.path.join(tmpdir, "sample.png")
            _make_png(sample_path)
            entries[0]["input"] = sample_path
            ok, detail = execute_format_matrix_manifest(
                entries,
                convert_file=_fake_convert,
                load_dds=lambda path: Image.open(path),
                image_module=Image,
                output_formats={"DDS": ".dds"},
                tmpdir=tmpdir,
            )

        self.assertTrue(ok)
        self.assertIn("expected_failures=1", detail)


class TestOptionalRealCorpus(unittest.TestCase):
    def test_optional_real_dds_corpus_samples_decode_or_fail_clearly(self):
        from src.core.alpha_processor import _load_dds_raw

        roots = _optional_corpus_roots("ALPHA_FIXER_REAL_DDS_CORPUS", "ALPHA_FIXER_DDS_CORPUS_DIR")
        samples = _iter_corpus_files(roots, (".dds",))
        if not samples:
            self.skipTest("No optional real DDS corpus configured")

        decoded = 0
        explained = 0
        with tempfile.TemporaryDirectory() as tmpdir:
            for sample_path in samples:
                try:
                    img = _load_dds_raw(sample_path)
                except Exception as exc:
                    explained += 1
                    detail = str(exc).lower()
                    self.assertTrue(detail)
                    self.assertTrue(
                        any(
                            token in detail
                            for token in (
                                "dds",
                                "dxgi",
                                "cubemap",
                                "array",
                                "volume",
                                "mipmap",
                                "truncated",
                                "unsupported",
                                "block",
                            )
                        ),
                        msg=f"Unexpected DDS failure detail for {sample_path}: {exc}",
                    )
                    continue
                decoded += 1
                try:
                    self.assertGreater(img.size[0], 0)
                    self.assertGreater(img.size[1], 0)
                    roundtrip_path = os.path.join(tmpdir, os.path.basename(sample_path) + ".png")
                    img.save(roundtrip_path)
                    self.assertTrue(os.path.exists(roundtrip_path))
                finally:
                    img.close()
        self.assertGreaterEqual(decoded + explained, len(samples))

    def test_optional_real_dx10_dds_corpus_samples_decode_or_fail_clearly(self):
        from src.core.alpha_processor import _load_dds_raw

        roots = _optional_corpus_roots(
            "ALPHA_FIXER_REAL_DDS_DX10_CORPUS",
            "ALPHA_FIXER_REAL_DDS_CORPUS",
            "ALPHA_FIXER_DDS_CORPUS_DIR",
        )
        samples = _iter_corpus_files(roots, (".dds",))
        if not samples:
            self.skipTest("No optional real DX10 DDS corpus configured")

        matched = 0
        for sample_path in samples:
            lower_name = os.path.basename(sample_path).lower()
            if not any(token in lower_name for token in ("dx10", "bc6", "bc7", "cubemap", "array", "volume", "mip")):
                continue
            matched += 1
            try:
                img = _load_dds_raw(sample_path)
            except Exception as exc:
                detail = str(exc).lower()
                self.assertTrue(
                    any(
                        token in detail
                        for token in ("dxgi", "bc6h", "bc7", "cubemap", "array", "volume", "mip", "unsupported")
                    ),
                    msg=f"Unexpected DX10 DDS failure detail for {sample_path}: {exc}",
                )
                continue
            else:
                try:
                    self.assertGreater(img.size[0], 0)
                    self.assertGreater(img.size[1], 0)
                finally:
                    img.close()
        if matched == 0:
            self.skipTest("No DX10/BC6H/BC7-style DDS samples found in optional corpus")

    def test_optional_real_dx10_dds_manifest_samples_match_expected_support(self):
        from src.core.alpha_processor import _load_dds_raw

        manifest = _optional_manifest_entries("ALPHA_FIXER_REAL_DDS_DX10_MANIFEST")
        if not manifest:
            self.skipTest("No optional real DX10 DDS manifest configured")

        exercised = 0
        for entry in manifest:
            sample_path = str(entry.get("path") or "").strip()
            if not os.path.isfile(sample_path):
                continue
            expected = str(entry.get("expect") or "load_or_fail_clearly").strip().lower()
            exercised += 1
            try:
                img = _load_dds_raw(sample_path)
            except Exception as exc:
                if expected == "load":
                    self.fail(f"Expected {sample_path} to decode, but failed with: {exc}")
                detail = str(exc)
                required_tokens = entry.get("detail_contains") or []
                if isinstance(required_tokens, str):
                    required_tokens = [required_tokens]
                for token in required_tokens:
                    self.assertIn(str(token).lower(), detail.lower(), msg=f"Missing DDS failure token for {sample_path}: {token}")
            else:
                try:
                    self.assertGreater(img.size[0], 0)
                    self.assertGreater(img.size[1], 0)
                    if expected == "fail":
                        self.fail(f"Expected {sample_path} to fail, but it decoded successfully")
                finally:
                    img.close()
        if exercised == 0:
            self.skipTest("Configured real DX10 DDS manifest paths were unavailable")


class TestAlphaWorkerOutputCompatibility(unittest.TestCase):

    def test_alpha_worker_promotes_non_alpha_outputs_to_png_when_not_in_place(self):
        worker = AlphaWorker(
            files=["/tmp/input.jpg"],
            manual_params={"threshold": 0},
            output_dir="/tmp/out",
            overwrite=True,
            suffix="",
        )
        self.assertEqual(worker._effective_output_ext("/tmp/input.jpg"), ".png")
        self.assertEqual(worker._resolve_output("/tmp/input.jpg", worker._effective_output_ext("/tmp/input.jpg")), "/tmp/out/input.png")

    def test_alpha_worker_keeps_original_ext_for_in_place_non_alpha_overwrite(self):
        worker = AlphaWorker(
            files=["/tmp/input.jpg"],
            manual_params={"threshold": 0},
            output_dir=None,
            overwrite=True,
            suffix="",
        )
        self.assertEqual(worker._effective_output_ext("/tmp/input.jpg"), ".jpg")


class TestConverterWorkerSizing(unittest.TestCase):

    def test_worker_count_drops_to_one_for_very_large_files(self):
        count = ConverterWorker._recommend_worker_count(
            2,
            [300 * 1024 * 1024, 8 * 1024 * 1024],
            cpu_count=8,
        )
        self.assertEqual(count, 1)

    def test_worker_count_is_capped_for_heavy_batches(self):
        count = ConverterWorker._recommend_worker_count(
            12,
            [200 * 1024 * 1024] * 6,
            cpu_count=8,
        )
        self.assertEqual(count, 2)

    def test_worker_auto_falls_back_to_png_for_transparent_alpha_incompatible_targets(self):
        worker = ConverterWorker(
            files=["/tmp/input.png"],
            target_format="JPEG",
            target_ext=".jpg",
        )
        with mock.patch.object(worker, "_source_has_meaningful_alpha", return_value=True):
            fmt, ext, note = worker._resolve_effective_target("/tmp/input.png")
        self.assertEqual((fmt, ext), ("PNG", ".png"))
        self.assertIn("auto-saved as PNG", note)

    def test_worker_keeps_requested_target_when_source_is_opaque(self):
        worker = ConverterWorker(
            files=["/tmp/input.png"],
            target_format="JPEG",
            target_ext=".jpg",
        )
        with mock.patch.object(worker, "_source_has_meaningful_alpha", return_value=False):
            fmt, ext, note = worker._resolve_effective_target("/tmp/input.png")
        self.assertEqual((fmt, ext, note), ("JPEG", ".jpg", ""))


class TestAtlasDetection(unittest.TestCase):

    def test_detect_atlas_cells_tolerates_near_transparent_seams(self):
        alpha = np.zeros((20, 20), dtype=np.uint8)
        alpha[1:9, 1:9] = 255
        alpha[1:9, 11:19] = 255
        alpha[11:19, 1:9] = 255
        alpha[11:19, 11:19] = 255
        alpha[10, 5] = 6
        alpha[14, 10] = 4
        cells = detect_atlas_cells(alpha)
        self.assertEqual(cells, [(1, 1, 8, 8), (11, 1, 8, 8), (1, 11, 8, 8), (11, 11, 8, 8)])

    def test_detect_atlas_cells_still_returns_empty_without_seams(self):
        alpha = np.full((16, 16), 255, dtype=np.uint8)
        self.assertEqual(detect_atlas_cells(alpha), [])


class TestConvertFile(unittest.TestCase):
    def test_output_format_discards_alpha_for_jpeg(self):
        self.assertTrue(output_format_discards_alpha("JPEG"))
        self.assertFalse(output_format_discards_alpha("PNG"))

    def test_output_format_available_uses_pillow_save_registry(self):
        with mock.patch.object(Image, "registered_extensions", return_value={".avif": "AVIF"}):
            with mock.patch.dict(Image.SAVE, {"AVIF": object()}, clear=False):
                self.assertTrue(output_format_available("AVIF"))
                self.assertEqual(output_format_unavailable_reason("AVIF"), "")

    def test_output_format_unavailable_reason_reports_missing_avif_support(self):
        with mock.patch.object(Image, "registered_extensions", return_value={}):
            with mock.patch.dict(Image.SAVE, {}, clear=True):
                self.assertIn("libavif", output_format_unavailable_reason("AVIF"))
                self.assertFalse(output_format_available("AVIF"))

    def test_png_to_jpeg(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jpg")
            _make_png(src)
            convert_file(src, dst, "JPEG")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.format, "JPEG")

    def test_png_to_bmp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.bmp")
            _make_png(src)
            convert_file(src, dst, "BMP")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_tim(self):
        from src.core.tim_handler import load_tim

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.tim")
            _make_png(src, w=4, h=4, alpha=200)
            convert_file(src, dst, "TIM")
            self.assertTrue(os.path.isfile(dst))
            img = load_tim(dst)
            try:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (4, 4))
                self.assertEqual(img.getpixel((0, 0))[3], 128)
            finally:
                img.close()

    def test_save_dds_raw_uses_rgb_header_for_opaque_images(self):
        from src.core.alpha_processor import _save_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, "opaque.dds")
            img = Image.new("RGBA", (4, 4), (9, 8, 7, 255))
            try:
                _save_dds_raw(img, dst)
            finally:
                img.close()
            with open(dst, "rb") as f:
                data = f.read(128)
            self.assertEqual(int.from_bytes(data[80:84], "little"), 0x40)
            self.assertEqual(int.from_bytes(data[88:92], "little"), 24)
            self.assertEqual(int.from_bytes(data[104:108], "little"), 0)

    def test_save_dds_raw_keeps_rgba_header_when_alpha_present(self):
        from src.core.alpha_processor import _save_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, "alpha.dds")
            img = Image.new("RGBA", (4, 4), (9, 8, 7, 128))
            try:
                _save_dds_raw(img, dst)
            finally:
                img.close()
            with open(dst, "rb") as f:
                data = f.read(128)
            self.assertEqual(int.from_bytes(data[80:84], "little"), 0x41)
            self.assertEqual(int.from_bytes(data[88:92], "little"), 32)
            self.assertEqual(int.from_bytes(data[104:108], "little"), 0xFF000000)

    def test_save_dds_raw_variant_rgb_discards_alpha(self):
        from src.core.alpha_processor import _save_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, "forced_rgb.dds")
            img = Image.new("RGBA", (4, 4), (9, 8, 7, 128))
            try:
                _save_dds_raw(img, dst, variant="rgb")
            finally:
                img.close()
            with open(dst, "rb") as f:
                data = f.read(128)
            self.assertEqual(int.from_bytes(data[80:84], "little"), 0x40)
            self.assertEqual(int.from_bytes(data[88:92], "little"), 24)
            self.assertEqual(int.from_bytes(data[104:108], "little"), 0)

    def test_save_dds_raw_variant_rgba_keeps_opaque_image_32bit(self):
        from src.core.alpha_processor import _save_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            dst = os.path.join(tmpdir, "forced_rgba.dds")
            img = Image.new("RGBA", (4, 4), (9, 8, 7, 255))
            try:
                _save_dds_raw(img, dst, variant="rgba")
            finally:
                img.close()
            with open(dst, "rb") as f:
                data = f.read(128)
            self.assertEqual(int.from_bytes(data[80:84], "little"), 0x41)
            self.assertEqual(int.from_bytes(data[88:92], "little"), 32)
            self.assertEqual(int.from_bytes(data[104:108], "little"), 0xFF000000)

    def test_png_to_tiff(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.tiff")
            _make_png(src)
            convert_file(src, dst, "TIFF")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.format, "TIFF")

    def test_png_to_webp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.webp")
            _make_png(src)
            convert_file(src, dst, "WEBP")
            self.assertTrue(os.path.isfile(dst))

    def test_resize(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.png")
            _make_png(src, w=64, h=64)
            convert_file(src, dst, "PNG", resize=(32, 32))
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.size, (32, 32))

    def test_output_dir_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            new_sub = os.path.join(tmpdir, "sub", "output.png")
            _make_png(src)
            convert_file(src, new_sub, "PNG")
            self.assertTrue(os.path.isfile(new_sub))

    def test_supported_output_formats_includes_dds(self):
        self.assertIn("DDS", SUPPORTED_OUTPUT_FORMATS)

    def test_dds_variant_options_include_auto_rgb_and_rgba(self):
        self.assertEqual(
            [value for _label, value in DDS_VARIANT_OPTIONS],
            ["auto", "rgb", "rgba", "dxt1", "dxt3", "dxt5"],
        )

    def test_convert_file_passes_dds_variant_to_writer(self):
        from src.core import file_converter as fc

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.dds")
            _make_png(src)
            with mock.patch.object(fc, "_save_dds") as save_dds:
                convert_file(src, dst, "DDS", dds_variant="rgba")
            self.assertTrue(save_dds.called)
            self.assertEqual(save_dds.call_args.kwargs["variant"], "rgba")

    def test_app_name_has_new_branding(self):
        self.assertEqual(APP_NAME, "FORMATOMANCER: Alpha & Media Alchemy")

    def test_dds_to_png_uses_native_loader_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            dst = os.path.join(tmpdir, "output.png")
            Image.new("RGBA", (8, 8), (10, 20, 30, 40)).save(src, format="DDS")
            convert_file(src, dst, "PNG")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst).convert("RGBA")
            self.assertEqual(img.size, (8, 8))

    def test_save_dds_tries_pillow_before_raw_fallback(self):
        from src.core import alpha_processor as ap
        img = Image.new("RGBA", (4, 4), (1, 2, 3, 4))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.dds")
                with mock.patch.object(ap.Image.Image, "save", wraps=Image.Image.save) as save_mock:
                    ap._save_dds(img, dst)
                self.assertTrue(save_mock.called)
                self.assertTrue(os.path.isfile(dst))
        finally:
            img.close()

    def test_save_dds_falls_back_when_pillow_save_fails(self):
        from src.core import alpha_processor as ap
        img = Image.new("RGBA", (4, 4), (9, 8, 7, 6))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.dds")
                with mock.patch.object(ap, "_has_wand", return_value=False):
                    with mock.patch.object(ap.Image.Image, "save", side_effect=OSError("save failed")):
                        ap._save_dds(img, dst)
                self.assertTrue(os.path.isfile(dst))
                with Image.open(dst) as result:
                    self.assertEqual(result.size, (4, 4))
        finally:
            img.close()

    def test_save_dds_dxt1_uses_wand_compression_option(self):
        from src.core import alpha_processor as ap

        calls: dict[str, object] = {}

        class FakeWandImage:
            def __init__(self, *args, **kwargs):
                calls["init_kwargs"] = kwargs
                self.options = {}
                self.format = None

            def __enter__(self):
                calls["image"] = self
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def save(self, filename=None):
                calls["filename"] = filename
                with open(filename, "wb") as f:
                    f.write(b"dds")

        fake_wand_image_module = types.SimpleNamespace(Image=FakeWandImage)
        fake_wand_module = types.SimpleNamespace(image=fake_wand_image_module)

        img = Image.new("RGBA", (4, 4), (9, 8, 7, 6))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.dds")
                with mock.patch.object(ap, "_has_wand", return_value=True):
                    original_save = ap.Image.Image.save

                    def _fake_save(image_self, fp, format=None, **kwargs):
                        if format == "DDS":
                            raise OSError("save failed")
                        return original_save(image_self, fp, format=format, **kwargs)

                    with mock.patch.object(ap.Image.Image, "save", autospec=True, side_effect=_fake_save):
                        with mock.patch.dict(sys.modules, {
                            "wand": fake_wand_module,
                            "wand.image": fake_wand_image_module,
                        }):
                            ap._save_dds(img, dst, variant="dxt1")
                self.assertEqual(calls["image"].options["dds:compression"], "dxt1")
                self.assertEqual(calls["filename"], dst)
        finally:
            img.close()

    def test_save_dds_dxt1_requires_wand(self):
        from src.core import alpha_processor as ap

        img = Image.new("RGBA", (4, 4), (9, 8, 7, 6))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.dds")
                with mock.patch.object(ap, "_has_wand", return_value=False):
                    with self.assertRaises(RuntimeError):
                        ap._save_dds(img, dst, variant="dxt1")
        finally:
            img.close()

    def test_save_dds_dxt3_uses_wand_compression_option(self):
        from src.core import alpha_processor as ap

        calls: dict[str, object] = {}

        class FakeWandImage:
            def __init__(self, *args, **kwargs):
                calls["init_kwargs"] = kwargs
                self.options = {}
                self.format = None

            def __enter__(self):
                calls["image"] = self
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def save(self, filename=None):
                calls["filename"] = filename
                with open(filename, "wb") as f:
                    f.write(b"dds")

        fake_wand_image_module = types.SimpleNamespace(Image=FakeWandImage)
        fake_wand_module = types.SimpleNamespace(image=fake_wand_image_module)

        img = Image.new("RGBA", (4, 4), (9, 8, 7, 6))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.dds")
                with mock.patch.object(ap, "_has_wand", return_value=True):
                    original_save = ap.Image.Image.save

                    def _fake_save(image_self, fp, format=None, **kwargs):
                        if format == "DDS":
                            raise OSError("save failed")
                        return original_save(image_self, fp, format=format, **kwargs)

                    with mock.patch.object(ap.Image.Image, "save", autospec=True, side_effect=_fake_save):
                        with mock.patch.dict(sys.modules, {
                            "wand": fake_wand_module,
                            "wand.image": fake_wand_image_module,
                        }):
                            ap._save_dds(img, dst, variant="dxt3")
                self.assertEqual(calls["image"].options["dds:compression"], "dxt3")
                self.assertEqual(calls["filename"], dst)
        finally:
            img.close()

    def test_open_image_for_preview_reduces_large_images_before_full_decode(self):
        class FakeImage:
            def __init__(self):
                self.mode = "RGB"
                self.size = (8192, 8192)
                self.draft_calls = []
                self.reduce_calls = []
                self.closed = False
                self.loaded = False

            def draft(self, mode, size):
                self.draft_calls.append((mode, size))

            def reduce(self, factor):
                self.reduce_calls.append(factor)
                reduced = FakeImage()
                reduced.size = (1024, 1024)
                return reduced

            def load(self):
                self.loaded = True

            def close(self):
                self.closed = True

        opened = FakeImage()
        with mock.patch("src.core.file_converter.Image.open", return_value=opened):
            preview = _open_image_for_preview("/tmp/huge.png", 512)
        self.assertEqual(opened.draft_calls, [(None, (1024, 1024))])
        self.assertTrue(opened.reduce_calls)
        self.assertTrue(opened.closed)
        self.assertEqual(preview.size, (1024, 1024))

    def test_load_dds_supports_16bit_argb1555_masks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            pixels = (
                (0xFC00).to_bytes(2, "little")
                + (0x83E0).to_bytes(2, "little")
                + (0x801F).to_bytes(2, "little")
                + (0xFFFF).to_bytes(2, "little")
            )
            _make_raw_dds(
                src,
                2,
                2,
                16,
                pixels,
                pf_flags=0x41,
                r_mask=0x7C00,
                g_mask=0x03E0,
                b_mask=0x001F,
                a_mask=0x8000,
            )
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (2, 2))
                self.assertGreaterEqual(img.getpixel((0, 0))[0], 240)
                self.assertEqual(img.getpixel((0, 0))[3], 255)
                self.assertGreaterEqual(img.getpixel((1, 0))[1], 240)
                self.assertGreaterEqual(img.getpixel((0, 1))[2], 240)
            finally:
                img.close()

    def test_load_dds_supports_luminance_alpha_masks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            pixels = (0x4080).to_bytes(2, "little")
            _make_raw_dds(
                src,
                1,
                1,
                16,
                pixels,
                pf_flags=0x20001,
                r_mask=0x00FF,
                g_mask=0x0000,
                b_mask=0x0000,
                a_mask=0xFF00,
            )
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (128, 128, 128, 64))
            finally:
                img.close()

    def test_load_dds_supports_custom_32bit_channel_masks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.dds")
            pixels = bytes((10, 20, 30, 40))
            _make_raw_dds(
                src,
                1,
                1,
                32,
                pixels,
                pf_flags=0x41,
                r_mask=0x000000FF,
                g_mask=0x0000FF00,
                b_mask=0x00FF0000,
                a_mask=0xFF000000,
            )
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (10, 20, 30, 40))
            finally:
                img.close()

    def test_load_dds_supports_dxt3_blocks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dxt3.dds")
            alpha_block = bytes([0xFF] * 8)
            color0 = (0xF800).to_bytes(2, "little")  # red in RGB565
            color1 = (0x0000).to_bytes(2, "little")
            indices = (0).to_bytes(4, "little")
            _make_compressed_dds(src, 4, 4, b"DXT3", alpha_block + color0 + color1 + indices)
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (4, 4))
                self.assertGreaterEqual(img.getpixel((0, 0))[0], 240)
                self.assertEqual(img.getpixel((0, 0))[3], 255)
            finally:
                img.close()

    def test_load_dds_supports_bc4_blocks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_bc4.dds")
            _make_compressed_dds(src, 4, 4, b"ATI1", bytes([255, 0]) + bytes(6))
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.size, (4, 4))
                self.assertEqual(img.getpixel((0, 0)), (255, 0, 0, 255))
            finally:
                img.close()

    def test_load_dds_supports_bc5_blocks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_bc5.dds")
            block = (bytes([255, 0]) + bytes(6)) * 2
            _make_compressed_dds(src, 4, 4, b"ATI2", block)
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.size, (4, 4))
                self.assertEqual(img.getpixel((0, 0)), (255, 255, 0, 255))
            finally:
                img.close()

    def test_load_dds_supports_dx10_bc4_blocks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dx10_bc4.dds")
            _make_dx10_dds(src, 4, 4, 80, bytes([200, 0]) + bytes(6))
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (200, 0, 0, 255))
            finally:
                img.close()

    def test_load_dds_supports_dx10_rgba8_unorm(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dx10_rgba8.dds")
            _make_dx10_dds(src, 2, 1, 28, bytes([10, 20, 30, 40, 50, 60, 70, 80]))
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (10, 20, 30, 40))
                self.assertEqual(img.getpixel((1, 0)), (50, 60, 70, 80))
            finally:
                img.close()

    def test_load_dds_supports_dx10_bgra8_unorm(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dx10_bgra8.dds")
            _make_dx10_dds(src, 2, 1, 87, bytes([30, 20, 10, 40, 90, 80, 70, 60]))
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (10, 20, 30, 40))
                self.assertEqual(img.getpixel((1, 0)), (70, 80, 90, 60))
            finally:
                img.close()

    def test_load_dds_supports_dx10_bgrx8_unorm_as_opaque(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dx10_bgrx8.dds")
            _make_dx10_dds(src, 1, 1, 91, bytes([30, 20, 10, 0]))
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (10, 20, 30, 255))
            finally:
                img.close()

    def test_load_dds_supports_dx10_b5g6r5_unorm(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dx10_b5g6r5.dds")
            _make_dx10_dds(src, 1, 1, 85, (0xF800).to_bytes(2, "little"))
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (255, 0, 0, 255))
            finally:
                img.close()

    def test_load_dds_supports_dx10_r8_unorm_as_greyscale(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dx10_r8.dds")
            _make_dx10_dds(src, 1, 1, 61, bytes([77]))
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.getpixel((0, 0)), (77, 77, 77, 255))
            finally:
                img.close()

    def test_load_dds_bc7_requires_real_decoder(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_bc7.dds")
            _make_dx10_dds(src, 4, 4, 98, bytes(16))
            with self.assertRaisesRegex(ValueError, "BC6H/BC7 decoder"):
                _load_dds_raw(src)

    def test_load_dds_bc7_uses_wand_fallback_when_available(self):
        from src.core import alpha_processor as ap

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_bc7.dds")
            _make_dx10_dds(src, 4, 4, 98, bytes(16))
            fallback_img = Image.new("RGBA", (4, 4), (1, 2, 3, 255))
            with mock.patch.object(ap, "_has_wand", return_value=True):
                with mock.patch.object(ap, "_load_dds_via_wand", return_value=fallback_img):
                    img = ap._load_dds_raw(src)
            try:
                self.assertEqual(img.size, (4, 4))
                self.assertEqual(img.getpixel((0, 0)), (1, 2, 3, 255))
            finally:
                img.close()

    def test_load_dds_bc6h_requires_real_decoder(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_bc6h.dds")
            _make_dx10_dds(src, 4, 4, 95, bytes(16))
            with self.assertRaisesRegex(ValueError, "BC6H/BC7 decoder"):
                _load_dds_raw(src)

    def test_load_dds_rejects_unknown_dxgi_format(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_unknown_dxgi.dds")
            _make_dx10_dds(src, 4, 4, 130, bytes(16))
            with self.assertRaisesRegex(ValueError, "unknown/unsupported DXGI=130"):
                _load_dds_raw(src)

    def test_load_dds_rejects_truncated_compressed_blocks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_truncated.dds")
            _make_compressed_dds(src, 4, 4, b"DXT5", bytes(12))
            with self.assertRaisesRegex(ValueError, "Truncated DDS block data"):
                _load_dds_raw(src)

    def test_load_dds_rejects_truncated_dx10_header(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_truncated_dx10.dds")
            _make_compressed_dds(src, 4, 4, b"DX10", b"")
            with self.assertRaisesRegex(ValueError, "DDS DX10 header truncated"):
                _load_dds_raw(src)

    def test_load_dds_supports_dxt4_alias_blocks(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_dxt4.dds")
            alpha_block = bytes([255, 0]) + bytes(6)
            color0 = (0xF800).to_bytes(2, "little")
            color1 = (0x0000).to_bytes(2, "little")
            indices = (0).to_bytes(4, "little")
            _make_compressed_dds(src, 4, 4, b"DXT4", alpha_block + color0 + color1 + indices)
            img = _load_dds_raw(src)
            try:
                self.assertGreaterEqual(img.getpixel((0, 0))[0], 240)
                self.assertEqual(img.getpixel((0, 0))[3], 255)
            finally:
                img.close()

    def test_load_dds_ignores_extra_mip_levels_on_base_surface(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_mipmap.dds")
            _make_dx10_dds(src, 4, 4, 80, bytes([180, 0]) + bytes(6), mipmap_count=3)
            img = _load_dds_raw(src)
            try:
                self.assertEqual(img.size, (4, 4))
                self.assertEqual(img.getpixel((0, 0)), (180, 0, 0, 255))
            finally:
                img.close()

    def test_load_dds_rejects_dx10_texture_arrays(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_array.dds")
            _make_dx10_dds(src, 4, 4, 80, bytes([180, 0]) + bytes(6), array_size=2)
            with self.assertRaisesRegex(ValueError, "texture array"):
                _load_dds_raw(src)

    def test_load_dds_uses_wand_fallback_for_texture_arrays_when_available(self):
        from src.core import alpha_processor as ap

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_array.dds")
            _make_dx10_dds(src, 4, 4, 80, bytes([180, 0]) + bytes(6), array_size=2)
            fallback_img = Image.new("RGBA", (4, 4), (4, 5, 6, 255))
            with mock.patch.object(ap, "_has_wand", return_value=True):
                with mock.patch.object(ap, "_load_dds_via_wand", return_value=fallback_img):
                    img = ap._load_dds_raw(src)
            try:
                self.assertEqual(img.size, (4, 4))
                self.assertEqual(img.getpixel((0, 0)), (4, 5, 6, 255))
            finally:
                img.close()

    def test_load_dds_rejects_legacy_cubemaps(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_cubemap.dds")
            _make_dx10_dds(src, 4, 4, 80, bytes([180, 0]) + bytes(6), caps2=0x00000200)
            with self.assertRaisesRegex(ValueError, "cubemap"):
                _load_dds_raw(src)

    def test_load_dds_rejects_volume_textures(self):
        from src.core.alpha_processor import _load_dds_raw

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input_volume.dds")
            _make_dx10_dds(
                src,
                4,
                4,
                80,
                bytes([180, 0]) + bytes(6),
                resource_dimension=4,
                depth=2,
            )
            with self.assertRaisesRegex(ValueError, "volume texture"):
                _load_dds_raw(src)

    def test_supported_output_formats_includes_png(self):
        self.assertIn("PNG", SUPPORTED_OUTPUT_FORMATS)

    # ------------------------------------------------------------------
    # New format tests
    # ------------------------------------------------------------------

    def test_rgba_png_to_jpeg_has_no_black_areas(self):
        """RGBA → JPEG should composite onto white, not produce a black image."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jpg")
            # semi-transparent red image
            _make_png(src, alpha=128)
            convert_file(src, dst, "JPEG")
            img = Image.open(dst).convert("RGB")
            arr = np.array(img)
            # If alpha were dropped the result would be very dark; compositing
            # onto white makes it noticeably bright.
            self.assertGreater(arr[:, :, 2].mean(), 100)  # blue channel bright from white bg

    def test_rgba_png_to_bmp_has_no_alpha(self):
        """BMP should not contain alpha (must be composited onto white)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.bmp")
            _make_png(src, alpha=128)
            convert_file(src, dst, "BMP")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertNotIn("A", img.mode)

    def test_rgba_png_to_gif(self):
        """GIF from RGBA source should save without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.gif")
            _make_png(src)
            convert_file(src, dst, "GIF")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_ppm(self):
        """PPM output should be RGB with no alpha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.ppm")
            _make_png(src)
            convert_file(src, dst, "PPM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertIn(img.mode, ("RGB", "L"))

    def test_jfif_input_converts_to_png(self):
        """JFIF input should be treated like JPEG."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.jfif")
            dst = os.path.join(tmpdir, "output.png")
            Image.new("RGB", (8, 8), (12, 34, 56)).save(src, format="JPEG")
            convert_file(src, dst, "PNG")
            self.assertTrue(os.path.isfile(dst))
            with Image.open(dst) as img:
                self.assertEqual(img.size, (8, 8))
                self.assertIn(img.mode, ("RGBA", "RGB"))

    def test_alpha_processor_loads_jpe_alias(self):
        from src.core.alpha_processor import load_image, SUPPORTED_READ, CONVERT_TO_RGBA

        self.assertIn(".jpe", SUPPORTED_READ)
        self.assertIn(".jfif", SUPPORTED_READ)
        self.assertIn(".jpe", CONVERT_TO_RGBA)
        self.assertIn(".jfif", CONVERT_TO_RGBA)
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.jpe")
            Image.new("RGB", (4, 4), (10, 20, 30)).save(src, format="JPEG")
            img = load_image(src)
            try:
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (4, 4))
            finally:
                img.close()

    def test_alpha_processor_saves_jfif_and_jpe_aliases(self):
        from src.core.alpha_processor import save_image

        with tempfile.TemporaryDirectory() as tmpdir:
            img = Image.new("RGBA", (4, 4), (10, 20, 30, 120))
            try:
                for ext in (".jfif", ".jpe"):
                    dst = os.path.join(tmpdir, f"output{ext}")
                    save_image(img, dst, ext)
                    self.assertTrue(os.path.isfile(dst))
                    with Image.open(dst) as saved:
                        self.assertEqual(saved.format, "JPEG")
                        self.assertEqual(saved.size, (4, 4))
            finally:
                img.close()

    def test_alpha_processor_supported_write_includes_tim(self):
        self.assertIn(".tim", SUPPORTED_WRITE)
        self.assertIn(".svg", SUPPORTED_WRITE)
        self.assertIn(".xnb", SUPPORTED_WRITE)

    def test_converter_supported_output_includes_tim(self):
        self.assertEqual(SUPPORTED_OUTPUT_FORMATS["TIM"], ".tim")

    def test_alpha_processor_save_svg_uses_svg_writer(self):
        from src.core import alpha_processor as ap

        img = Image.new("RGBA", (4, 4), (10, 20, 30, 40))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.svg")
                with mock.patch("src.core.file_converter._save_svg") as save_svg:
                    save_image(img, dst, ".svg")
                save_svg.assert_called_once()
        finally:
            img.close()

    def test_alpha_processor_save_xnb_uses_xnb_writer(self):
        img = Image.new("RGBA", (4, 4), (10, 20, 30, 40))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.xnb")
                with mock.patch("src.core.xnb_handler.save_xnb") as save_xnb:
                    save_image(img, dst, ".xnb")
                save_xnb.assert_called_once()
        finally:
            img.close()

    def test_alpha_processor_save_tim_uses_tim_writer(self):
        img = Image.new("RGBA", (4, 4), (10, 20, 30, 40))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "output.tim")
                with mock.patch("src.core.tim_handler.save_tim") as save_tim:
                    save_image(img, dst, ".tim")
                save_tim.assert_called_once()
        finally:
            img.close()

    def test_tim_roundtrip_preserves_basic_transparency_states(self):
        from src.core.tim_handler import load_tim, save_tim

        img = Image.new("RGBA", (2, 2))
        img.putdata([
            (0, 0, 0, 0),
            (255, 0, 0, 120),
            (0, 0, 0, 255),
            (0, 255, 0, 255),
        ])
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "roundtrip.tim")
                save_tim(img, dst)
                out = load_tim(dst)
                try:
                    self.assertEqual(out.getpixel((0, 0))[3], 0)
                    self.assertEqual(out.getpixel((1, 0))[3], 128)
                    self.assertEqual(out.getpixel((0, 1)), (0, 0, 0, 255))
                    self.assertEqual(out.getpixel((1, 1))[1], 248)
                    self.assertEqual(out.getpixel((1, 1))[3], 255)
                finally:
                    out.close()
        finally:
            img.close()

    def test_xnb_rgba64_reorders_channels_to_rgba(self):
        from src.core.xnb_handler import _decode_texture2d, _FMT_RGBA64

        data = bytes.fromhex("0011002200330044")
        with _decode_texture2d(data, 1, 1, _FMT_RGBA64) as img:
            self.assertEqual(img.getpixel((0, 0)), (0x33, 0x22, 0x11, 0x44))

    def test_xnb_rgba1010102_uses_full_10bit_channels(self):
        from src.core.xnb_handler import _decode_texture2d, _FMT_RGBA1010102

        packed = (1023 << 22) | (512 << 12) | (256 << 2) | 0x3
        data = packed.to_bytes(4, "little")
        with _decode_texture2d(data, 1, 1, _FMT_RGBA1010102) as img:
            r, g, b, a = img.getpixel((0, 0))
            self.assertEqual(r, 255)
            self.assertEqual(g, 128)
            self.assertEqual(b, 64)
            self.assertEqual(a, 255)

    def test_png_to_pgm(self):
        """PGM output should be grayscale with no alpha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pgm")
            _make_png(src)
            convert_file(src, dst, "PGM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.mode, "L")

    def test_png_to_pbm(self):
        """PBM output should be 1-bit with no alpha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pbm")
            _make_png(src)
            convert_file(src, dst, "PBM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertEqual(img.mode, "1")

    def test_png_to_pnm(self):
        """PNM output should save successfully through the Netpbm path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pnm")
            _make_png(src)
            convert_file(src, dst, "PNM")
            self.assertTrue(os.path.isfile(dst))
            img = Image.open(dst)
            self.assertIn(img.mode, ("RGB", "L"))

    def test_png_to_pcx(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.pcx")
            _make_png(src)
            convert_file(src, dst, "PCX")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_avif(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.avif")
            _make_png(src)
            convert_file(src, dst, "AVIF")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_qoi(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.qoi")
            _make_png(src)
            convert_file(src, dst, "QOI")
            self.assertTrue(os.path.isfile(dst))

    def test_png_to_jpeg2000(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jp2")
            _make_png(src)
            convert_file(src, dst, "JPEG2000")
            self.assertTrue(os.path.isfile(dst))
            self.assertGreater(os.path.getsize(dst), 0)

    def test_png_to_jpeg2000_lossless(self):
        """quality=100 should produce a lossless JPEG2000 file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.jp2")
            _make_png(src)
            convert_file(src, dst, "JPEG2000", quality=100)
            self.assertTrue(os.path.isfile(dst))
            result = Image.open(dst)
            self.assertIn(result.mode, ("RGBA", "RGB"))

    def test_rgb_png_preserved_as_png(self):
        """RGB source → PNG should not be needlessly upcast to RGBA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.png")
            _make_rgb_png(src)
            convert_file(src, dst, "PNG")
            img = Image.open(dst)
            self.assertEqual(img.mode, "RGB")

    def test_supported_output_formats_includes_new(self):
        for fmt in ("PBM", "PGM", "PNM", "PPM", "PCX", "AVIF", "QOI", "JPEG2000"):
            with self.subTest(fmt=fmt):
                self.assertIn(fmt, SUPPORTED_OUTPUT_FORMATS)

    def test_supported_output_formats_includes_svg(self):
        self.assertIn("SVG", SUPPORTED_OUTPUT_FORMATS)

    def test_png_to_svg_creates_valid_svg(self):
        """PNG → SVG (fallback mode) should produce a file with embedded base64 PNG."""
        import unittest.mock as mock
        from src.core import file_converter as fc
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                src = os.path.join(tmpdir, "input.png")
                dst = os.path.join(tmpdir, "output.svg")
                _make_png(src)
                convert_file(src, dst, "SVG")
                self.assertTrue(os.path.isfile(dst))
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("<svg", content)
                self.assertIn("data:image/png;base64,", content)
                self.assertIn("</svg>", content)

    def test_png_to_svg_vtracer_creates_valid_svg(self):
        """PNG → SVG (vtracer mode) should produce a true vector SVG with <path> elements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "input.png")
            dst = os.path.join(tmpdir, "output.svg")
            _make_png(src)
            convert_file(src, dst, "SVG")
            self.assertTrue(os.path.isfile(dst))
            with open(dst, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("<svg", content)

    def test_rgba_png_to_svg_preserves_size(self):
        """SVG output should encode the correct width/height attributes."""
        import unittest.mock as mock
        from src.core import file_converter as fc
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                src = os.path.join(tmpdir, "input.png")
                dst = os.path.join(tmpdir, "output.svg")
                _make_png(src, w=16, h=8)
                convert_file(src, dst, "SVG")
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn('width="16"', content)
                self.assertIn('height="8"', content)

    def test_svg_output_roundtrip(self):
        """The base64 PNG embedded in the fallback SVG should decode to a valid image."""
        import base64
        import re
        import unittest.mock as mock
        from src.core import file_converter as fc
        from io import BytesIO
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                src = os.path.join(tmpdir, "input.png")
                dst = os.path.join(tmpdir, "output.svg")
                _make_png(src, w=8, h=8)
                convert_file(src, dst, "SVG")
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                match = re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)', content)
                self.assertIsNotNone(match)
                png_data = base64.b64decode(match.group(1))
                rt_img = Image.open(BytesIO(png_data))
                self.assertEqual(rt_img.size, (8, 8))


class TestFlattenAlpha(unittest.TestCase):

    def test_rgba_flattened_to_rgb(self):
        img = Image.new("RGBA", (4, 4), (100, 100, 100, 128))
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "RGB")

    def test_rgb_unchanged(self):
        img = Image.new("RGB", (4, 4), (200, 100, 50))
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "RGB")

    def test_la_flattened_to_l(self):
        img = Image.new("LA", (4, 4), (100, 128))
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "L")

    def test_palette_flattened_to_rgb(self):
        img = Image.new("P", (4, 4))
        img.putpalette([i % 256 for i in range(256 * 3)])
        result = _flatten_alpha(img)
        self.assertEqual(result.mode, "RGB")


class TestSupportedRead(unittest.TestCase):
    """Verify SUPPORTED_READ in alpha_processor includes all expected extensions."""

    def setUp(self):
        from src.core.alpha_processor import SUPPORTED_READ
        self.exts = SUPPORTED_READ

    def test_jp2_in_supported_read(self):
        self.assertIn(".jp2", self.exts)

    def test_svg_in_supported_read(self):
        self.assertIn(".svg", self.exts)

    def test_png_in_supported_read(self):
        self.assertIn(".png", self.exts)

    def test_avif_in_supported_read(self):
        self.assertIn(".avif", self.exts)

    def test_netpbm_family_in_supported_read(self):
        for ext in (".pbm", ".pgm", ".pnm"):
            with self.subTest(ext=ext):
                self.assertIn(ext, self.exts)

    def test_jpeg2000_aliases_in_supported_read(self):
        for ext in (".j2k", ".j2c"):
            with self.subTest(ext=ext):
                self.assertIn(ext, self.exts)


class TestSvgVtracerFallback(unittest.TestCase):
    """_save_svg uses base64 fallback when vtracer is not available."""

    def test_fallback_produces_base64_svg(self):
        """Without vtracer the SVG must contain an embedded base64 PNG."""
        import unittest.mock as mock
        from src.core import file_converter as fc
        with mock.patch.object(fc, "_has_vtracer", return_value=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                dst = os.path.join(tmpdir, "out.svg")
                img = Image.new("RGBA", (8, 8), (200, 100, 50, 128))
                fc._save_svg(img, dst)
                with open(dst, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("<svg", content)
                self.assertIn("data:image/png;base64,", content)

    def test_vtracer_path_called(self):
        """When vtracer is available, vtracer.convert_image_to_svg_py is invoked."""
        import unittest.mock as mock
        from src.core import file_converter as fc

        mock_vtracer = mock.MagicMock()
        mock_vtracer.convert_image_to_svg_py = mock.MagicMock()

        with mock.patch.object(fc, "_has_vtracer", return_value=True):
            with mock.patch.dict("sys.modules", {"vtracer": mock_vtracer}):
                with tempfile.TemporaryDirectory() as tmpdir:
                    dst = os.path.join(tmpdir, "out.svg")
                    img = Image.new("RGB", (4, 4), (200, 100, 50))
                    fc._save_svg(img, dst)
                    mock_vtracer.convert_image_to_svg_py.assert_called_once()
                    args = mock_vtracer.convert_image_to_svg_py.call_args
                    self.assertEqual(args[1]["colormode"], "color")


if __name__ == "__main__":
    unittest.main()
