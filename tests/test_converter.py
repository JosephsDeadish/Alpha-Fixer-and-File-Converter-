"""
Tests for file converter utilities.
"""
import sys
import os
import json
import hashlib
import io
import tempfile
import warnings
from contextlib import nullcontext
import unittest
from unittest import mock
import types
import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps, TiffImagePlugin

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
    _open_image,
    _open_image_for_preview,
    _flatten_alpha,
    output_format_discards_alpha,
    output_format_available,
    output_format_unavailable_reason,
)
from src.core.alpha_processor import SUPPORTED_WRITE, detect_atlas_cells, save_image
from src.core.runtime_validation import (
    build_private_local_manifests_from_env,
    corpus_roots_from_env,
    enrich_dds_manifest_entries,
    execute_dds_manifest,
    execute_dds_manifest_report,
    execute_disc_video_manifest,
    execute_format_matrix_manifest,
    iter_corpus_files,
    load_manifest_entries,
    manifest_coverage_review,
    manifest_group_check_suffix,
    manifest_grouped_entries,
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
    def test_runtime_validation_corpus_root_helpers_discover_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root_a = os.path.join(tmpdir, "a")
            root_b = os.path.join(tmpdir, "b")
            os.makedirs(root_a, exist_ok=True)
            os.makedirs(root_b, exist_ok=True)
            first = os.path.join(root_a, "sample.iso")
            second = os.path.join(root_b, "texture.dds")
            Path(first).write_bytes(b"iso")
            Path(second).write_bytes(b"dds")
            with mock.patch.dict(
                os.environ,
                {"AF_TEST_ROOTS": os.pathsep.join([root_a, root_b])},
                clear=False,
            ):
                roots = corpus_roots_from_env("AF_TEST_ROOTS")
            self.assertEqual(roots, [os.path.abspath(root_a), os.path.abspath(root_b)])
            self.assertEqual(
                iter_corpus_files(roots, (".iso", ".dds")),
                [os.path.abspath(first), os.path.abspath(second)],
            )

    def test_manifest_group_helpers_slug_and_preserve_order(self):
        entries = [
            {"platform": "PSP", "path": "/tmp/a.iso"},
            {"group": "BC7 mipmap", "path": "/tmp/a.dds"},
            {"platform": "PS1", "path": "/tmp/b.bin"},
            {"platform": "PSP", "path": "/tmp/c.iso"},
        ]
        self.assertEqual(manifest_group_check_suffix("PS1 / BIN+CUE"), "ps1_bin_cue")
        grouped = manifest_grouped_entries(entries, "platform", "group")
        self.assertEqual(
            [(suffix, label, len(group_entries)) for suffix, label, group_entries in grouped],
            [("psp", "PSP", 2), ("bc7_mipmap", "BC7 mipmap", 1), ("ps1", "PS1", 1)],
        )

    def test_manifest_coverage_review_matches_required_labels_case_insensitively(self):
        entries = [
            {"platform": "PSP", "path": "/tmp/a.iso"},
            {"platform": "PS1", "path": "/tmp/b.bin"},
            {"group": "BC7 mipmap", "path": "/tmp/c.dds"},
        ]
        review = manifest_coverage_review(entries, ["psp", "PS2"], "platform")
        self.assertEqual(review["present_labels"], ["PSP", "PS1"])
        self.assertEqual(review["matched_labels"]["psp"], "PSP")
        self.assertEqual(review["missing_labels"], ["PS2"])
        self.assertFalse(review["ok"])

        group_review = manifest_coverage_review(entries, ["bc7 MIPMAP"], "group")
        self.assertEqual(group_review["matched_labels"]["bc7 MIPMAP"], "BC7 mipmap")
        self.assertEqual(group_review["missing_labels"], [])
        self.assertTrue(group_review["ok"])

    def test_built_in_public_disc_video_manifest_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "public_disc_video_manifest.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertGreaterEqual(len(entries), 12)
        self.assertTrue(any(str(entry.get("download_url") or "").startswith("https://raw.githubusercontent.com/") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".iso") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".pmf") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".avi") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".mov") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".mpg") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".mkv") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".webm") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".aac") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".ac3") for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(".wma") for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "disc image" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "pmf movie asset" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "odd container" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "legacy container" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "program stream" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "matroska family" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "audio-only" for entry in entries))
        self.assertTrue(any(str(entry.get("platform") or "").upper() == "PSP" for entry in entries))
        audio_only = [entry for entry in entries if str(entry.get("group") or "").lower() == "audio-only"]
        self.assertTrue(audio_only)
        self.assertTrue(all(str(entry.get("expect") or "").lower() == "fail" for entry in audio_only))
        self.assertTrue(all(entry.get("expect_probe_has_video") is False for entry in audio_only))
        self.assertTrue(all(entry.get("expect_probe_has_audio") is True for entry in audio_only))
        videoish = [entry for entry in entries if str(entry.get("group") or "").lower() != "audio-only"]
        self.assertTrue(any(entry.get("expect_probe_has_video") is True for entry in videoish))

    def test_built_in_public_format_matrix_manifest_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "public_format_matrix_manifest.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertGreaterEqual(len(entries), 8)
        self.assertTrue(all(str(entry.get("download_url") or "").startswith("https://raw.githubusercontent.com/") for entry in entries))
        self.assertEqual(
            {str(entry.get("target_format") or "").upper() for entry in entries},
            {"DDS", "GIF", "ICO", "PNG", "JPEG", "TIFF", "BMP", "TGA"},
        )
        self.assertTrue(any(str(entry.get("input") or "").endswith("sample.png") for entry in entries))
        self.assertTrue(any(str(entry.get("input") or "").endswith("sample.jpg") for entry in entries))
        self.assertTrue(any(str(entry.get("input") or "").endswith("sample.webp") for entry in entries))

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
        self.assertEqual(len(entries), 8)
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("psp", "sample.umd.iso")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("psp", "sample.umd")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("psp", "sample.pmf")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("ps1", "sample.bin")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("ps1", "sample.str")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("ps2", "sample.iso")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("ps2", "sample.bin")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("ps2", "sample.pss")) for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "umd disc image" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "raw umd container" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "pmf movie asset" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "bin/cue disc image" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "dvd disc image" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "pss movie asset" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "str movie asset" for entry in entries))
        ps1_entry = next(entry for entry in entries if str(entry.get("path") or "").endswith(os.path.join("ps1", "sample.bin")))
        companions = ps1_entry.get("companions")
        self.assertIsInstance(companions, list)
        self.assertTrue(any(str(item.get("path") or "").endswith(os.path.join("ps1", "sample.cue")) for item in companions if isinstance(item, dict)))
        ps2_entry = next(entry for entry in entries if str(entry.get("path") or "").endswith(os.path.join("ps2", "sample.bin")))
        ps2_companions = ps2_entry.get("companions")
        self.assertIsInstance(ps2_companions, list)
        self.assertTrue(any(str(item.get("path") or "").endswith(os.path.join("ps2", "sample.cue")) for item in ps2_companions if isinstance(item, dict)))
        disc_image_entries = [
            entry
            for entry in entries
            if "disc image" in str(entry.get("group") or "").lower()
        ]
        self.assertTrue(disc_image_entries)
        self.assertTrue(
            all(
                "Disc-image video inputs are experimental" in (entry.get("hint_contains") or [""])[0]
                for entry in disc_image_entries
            )
        )
        self.assertTrue(all(entry.get("expect_probe_has_video") is True for entry in entries))

    def test_private_odd_container_manifest_template_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "private_odd_container_manifest_template.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertEqual(len(entries), 9)
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.pmf")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.pss")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.str")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.vob")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "movie.vob.001")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.asf")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.wmv")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.mxf")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("video", "sample.dat")) for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "").lower() == "pmf movie asset" for entry in entries))
        self.assertTrue(any(str(entry.get("platform") or "").lower() == "psp" for entry in entries))
        self.assertTrue(any(str(entry.get("platform") or "").lower() == "windows legacy" for entry in entries))
        self.assertTrue(any(entry.get("expect_probe_has_video") is True for entry in entries))
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
        self.assertTrue(any(str(entry.get("decode_policy") or "") == "base-level-only" for entry in entries))
        self.assertTrue(any(str(entry.get("export_policy") or "") == "complex-surface-not-supported" for entry in entries))
        self.assertTrue(any(str(entry.get("policy_status") or "") == "pending-design" for entry in entries))

    def test_build_private_local_manifests_from_env_discovers_real_sample_layouts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_root = os.path.join(tmpdir, "video")
            dds_root = os.path.join(tmpdir, "dds")
            os.makedirs(os.path.join(video_root, "ps1"), exist_ok=True)
            os.makedirs(os.path.join(video_root, "ps2"), exist_ok=True)
            os.makedirs(os.path.join(video_root, "odd"), exist_ok=True)
            os.makedirs(dds_root, exist_ok=True)
            Path(os.path.join(video_root, "ps1", "movie.bin")).write_bytes(b"bin")
            Path(os.path.join(video_root, "ps1", "movie.cue")).write_text('FILE "movie.bin" BINARY\n', encoding="utf-8")
            Path(os.path.join(video_root, "ps2", "disc.iso")).write_bytes(b"iso")
            Path(os.path.join(video_root, "odd", "sample.wmv")).write_bytes(b"wmv")
            Path(os.path.join(video_root, "odd", "audio_only.wma")).write_bytes(b"wma")
            Path(os.path.join(dds_root, "sky_cubemap.dds")).write_bytes(b"dds")
            Path(os.path.join(dds_root, "scene_bc6h.dds")).write_bytes(b"dds")
            with mock.patch.dict(
                os.environ,
                {
                    "ALPHA_FIXER_REAL_DISC_VIDEO_CORPUS": video_root,
                    "ALPHA_FIXER_REAL_DDS_CORPUS": dds_root,
                },
                clear=False,
            ):
                manifests = build_private_local_manifests_from_env(limit=16)
            disc_entries = manifests["disc_video"]
            odd_entries = manifests["odd_video"]
            dds_entries = manifests["dds"]
            self.assertTrue(any(str(entry.get("group")) == "bin/cue disc image" for entry in disc_entries))
            self.assertTrue(any(str(entry.get("group")) == "dvd disc image" for entry in disc_entries))
            bin_entry = next(entry for entry in disc_entries if str(entry.get("group")) == "bin/cue disc image")
            self.assertTrue(any(str(item.get("path") or "").endswith("movie.cue") for item in bin_entry.get("companions", []) if isinstance(item, dict)))
            self.assertTrue(any(str(entry.get("group")) == "legacy container" for entry in odd_entries))
            self.assertTrue(any(str(entry.get("group")) == "audio-only" for entry in odd_entries))
            self.assertTrue(any(str(entry.get("group")) == "cubemap" for entry in dds_entries))
            self.assertTrue(any(str(entry.get("group")) == "BC6H" for entry in dds_entries))
            cubemap_entry = next(entry for entry in dds_entries if str(entry.get("group")) == "cubemap")
            self.assertEqual(cubemap_entry.get("surface_kind"), "cubemap")
            self.assertEqual(cubemap_entry.get("decode_policy"), "fail-clearly")
            self.assertEqual(cubemap_entry.get("policy_status"), "pending-design")
            bc6h_entry = next(entry for entry in dds_entries if str(entry.get("group")) == "BC6H")
            self.assertEqual(bc6h_entry.get("surface_kind"), "2d")
            self.assertEqual(bc6h_entry.get("decode_policy"), "runtime-dependent")
            self.assertEqual(bc6h_entry.get("export_policy"), "2d-only")

    def test_enrich_dds_manifest_entries_infers_complex_surface_policy_fields(self):
        entries = enrich_dds_manifest_entries(
            [
                {"path": "/tmp/sky_cubemap.dds"},
                {"path": "/tmp/mipmap_chain.dds"},
                {"path": "/tmp/scene_bc6h.dds"},
                {"path": "/tmp/dx10_texture.dds"},
            ]
        )
        self.assertEqual(entries[0]["surface_kind"], "cubemap")
        self.assertEqual(entries[0]["decode_policy"], "fail-clearly")
        self.assertEqual(entries[0]["export_policy"], "complex-surface-not-supported")
        self.assertEqual(entries[1]["surface_kind"], "mipmap")
        self.assertEqual(entries[1]["decode_policy"], "base-level-only")
        self.assertEqual(entries[1]["policy_status"], "implemented-with-limits")
        self.assertEqual(entries[2]["surface_kind"], "2d")
        self.assertEqual(entries[2]["decode_policy"], "runtime-dependent")
        self.assertEqual(entries[2]["policy_status"], "runtime-dependent")
        self.assertEqual(entries[3]["decode_policy"], "load-or-fail-clearly")

    def test_execute_dds_manifest_report_tracks_policy_scaffolding_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cube_path = os.path.join(tmpdir, "sky_cubemap.dds")
            mip_path = os.path.join(tmpdir, "terrain_mipmap.dds")
            Path(cube_path).write_bytes(b"dds")
            Path(mip_path).write_bytes(b"dds")

            class _FakeImage:
                size = (64, 64)

                def close(self):
                    pass

            def _fake_load(path):
                if path == cube_path:
                    raise ValueError("Unsupported DDS cubemap surface.")
                return _FakeImage()

            report = execute_dds_manifest_report(
                [
                    {"path": cube_path, "expect": "fail", "detail_contains": ["cubemap"]},
                    {"path": mip_path, "expect": "load", "expect_size": [64, 64]},
                ],
                _fake_load,
            )
        self.assertTrue(report["ok"], msg=report["detail"])
        self.assertEqual(report["surface_counts"]["cubemap"], 1)
        self.assertEqual(report["surface_counts"]["mipmap"], 1)
        self.assertEqual(report["decode_policy_counts"]["fail-clearly"], 1)
        self.assertEqual(report["decode_policy_counts"]["base-level-only"], 1)
        self.assertEqual(report["export_policy_counts"]["complex-surface-not-supported"], 1)
        self.assertEqual(report["policy_status_counts"]["pending-design"], 1)
        self.assertEqual(report["policy_status_counts"]["implemented-with-limits"], 1)
        self.assertIn("surfaces=", report["detail"])
        self.assertIn("decode_policies=", report["detail"])

    def test_private_odd_container_video_manifest_template_loads(self):
        manifest_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "sample_manifests",
            "private_odd_container_video_manifest_template.json",
        )
        entries = load_manifest_entries(manifest_path)
        self.assertEqual(len(entries), 11)
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "video_sample.dat")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "audio_only.wma")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "multi_stream.mkv")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "clip.001.vob")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "cover_art_container.mkv")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "sample.wmv")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "sample.asf")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "sample.vob")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "sample.mxf")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "edge_case.mov")) for entry in entries))
        self.assertTrue(any(str(entry.get("path") or "").endswith(os.path.join("odd", "edge_case.avi")) for entry in entries))
        self.assertTrue(any("audio but no playable video stream" in " ".join(entry.get("hint_contains") or []) for entry in entries))
        self.assertTrue(any("Multiple video streams were detected" in " ".join(entry.get("hint_contains") or []) for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "") == "program stream" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "") == "quicktime edge case" for entry in entries))
        self.assertTrue(any(str(entry.get("group") or "") == "legacy index container" for entry in entries))

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

    def test_load_manifest_entries_logs_and_recovers_from_malformed_payload(self):
        with self.assertLogs("src.core.runtime_validation", level="WARNING") as logs:
            entries = load_manifest_entries('{"entries": "broken"}')
        self.assertEqual(entries, [])
        self.assertTrue(any("Ignoring malformed manifest" in entry for entry in logs.output))

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
                "group": "dvd disc image",
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
        self.assertIn("groups=dvd disc image:1", detail)
        self.assertIn("platforms=PS2:1", detail)
        self.assertIn("recoveries=transcode:1", detail)

    def test_execute_disc_video_manifest_summarizes_expected_failures_and_explanations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_only_path = os.path.join(tmpdir, "sample.aac")
            explain_path = os.path.join(tmpdir, "sample.iso")
            for path in (audio_only_path, explain_path):
                with open(path, "wb") as handle:
                    handle.write(b"media")

            probes = {
                audio_only_path: {
                    "has_video": False,
                    "has_audio": True,
                    "format_name": "aac",
                    "audio_codec": "aac",
                },
                explain_path: {
                    "has_video": False,
                    "has_audio": False,
                    "format_name": "iso",
                    "video_codec": "",
                },
            }
            hints = {
                audio_only_path: "ffprobe detected audio but no playable video stream",
                explain_path: "Disc-image video inputs are experimental",
            }
            fake_tool = types.SimpleNamespace(
                _probe_media_details=lambda path, **_kwargs: probes[path],
                _load_video_clip=lambda *_args, **_kwargs: None,
                _video_load_failure_hint=lambda path, **_kwargs: hints[path],
            )
            entries = [
                {
                    "path": audio_only_path,
                    "group": "audio-only",
                    "expect": "fail",
                    "expect_probe_has_video": False,
                    "expect_probe_has_audio": True,
                    "hint_contains": ["audio but no playable video stream"],
                },
                {
                    "path": explain_path,
                    "platform": "PSP",
                    "group": "disc image",
                    "expect": "load_or_explain",
                    "expect_probe_has_video": False,
                    "hint_contains": ["Disc-image video inputs are experimental"],
                },
            ]

            ok, detail = execute_disc_video_manifest(entries, fake_tool)
        self.assertTrue(ok, msg=detail)
        self.assertIn("entries=2", detail)
        self.assertIn("loaded=0", detail)
        self.assertIn("expected_failures=1", detail)
        self.assertIn("explained=1", detail)
        self.assertIn("groups=audio-only:1,disc image:1", detail)
        self.assertIn("platforms=PSP:1", detail)

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

    def test_populate_sample_manifest_script_can_emit_private_local_discovery_manifests(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "populate_sample_manifest.py")
        spec = importlib.util.spec_from_file_location("populate_sample_manifest", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)

        with tempfile.TemporaryDirectory() as tmpdir:
            disc_out = os.path.join(tmpdir, "disc.json")
            odd_out = os.path.join(tmpdir, "odd.json")
            dds_out = os.path.join(tmpdir, "dds.json")
            summary_out = os.path.join(tmpdir, "summary.json")
            fake_private = {
                "disc_video": [{"platform": "PSP", "path": "/samples/psp/sample.iso"}],
                "odd_video": [{"group": "legacy container", "path": "/samples/odd/sample.wmv"}],
                "dds": [{"group": "cubemap", "path": "/samples/dds/cube.dds"}],
            }
            with mock.patch.object(script, "build_private_local_manifests_from_env", return_value=fake_private):
                rc = script.main(
                    [
                        "--discover-private-local",
                        "--private-limit",
                        "9",
                        "--output-disc-manifest",
                        disc_out,
                        "--output-odd-video-manifest",
                        odd_out,
                        "--output-dds-manifest",
                        dds_out,
                        "--output-manifest",
                        summary_out,
                    ]
                )
            self.assertEqual(rc, 0)
            disc_payload = json.loads(Path(disc_out).read_text(encoding="utf-8"))
            odd_payload = json.loads(Path(odd_out).read_text(encoding="utf-8"))
            dds_payload = json.loads(Path(dds_out).read_text(encoding="utf-8"))
            summary_payload = json.loads(Path(summary_out).read_text(encoding="utf-8"))
            self.assertEqual(len(disc_payload["entries"]), 1)
            self.assertEqual(len(odd_payload["entries"]), 1)
            self.assertEqual(len(dds_payload["entries"]), 1)
            self.assertEqual(summary_payload["summary"]["disc_video_entries"], 1)
            self.assertEqual(summary_payload["summary"]["odd_video_entries"], 1)
            self.assertEqual(summary_payload["summary"]["dds_entries"], 1)

    def test_populate_private_manifests_script_writes_generated_files(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "populate_private_manifests.py")
        spec = importlib.util.spec_from_file_location("populate_private_manifests", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)

        with tempfile.TemporaryDirectory() as tmpdir:
            video_root = os.path.join(tmpdir, "video")
            dds_root = os.path.join(tmpdir, "dds")
            out_dir = os.path.join(tmpdir, "out")
            os.makedirs(os.path.join(video_root, "psp"), exist_ok=True)
            os.makedirs(os.path.join(video_root, "odd"), exist_ok=True)
            os.makedirs(dds_root, exist_ok=True)
            Path(os.path.join(video_root, "psp", "sample.umd.iso")).write_bytes(b"iso")
            Path(os.path.join(video_root, "odd", "sample.wmv")).write_bytes(b"wmv")
            Path(os.path.join(dds_root, "texture_bc7.dds")).write_bytes(b"dds")
            with mock.patch.dict(
                os.environ,
                {
                    "ALPHA_FIXER_REAL_VIDEO_CORPUS": video_root,
                    "ALPHA_FIXER_REAL_DDS_CORPUS": dds_root,
                },
                clear=False,
            ):
                rc = script.main(["--output-dir", out_dir, "--limit", "8"])
            self.assertEqual(rc, 0)
            disc_entries = load_manifest_entries(os.path.join(out_dir, "private_real_disc_video_manifest.json"))
            odd_entries = load_manifest_entries(os.path.join(out_dir, "private_real_odd_container_manifest.json"))
            dds_entries = load_manifest_entries(os.path.join(out_dir, "private_real_dds_complex_manifest.json"))
            self.assertTrue(any(str(entry.get("group")) == "umd disc image" for entry in disc_entries))
            self.assertTrue(any(str(entry.get("group")) == "legacy container" for entry in odd_entries))
            self.assertTrue(any(str(entry.get("group")) == "BC7" for entry in dds_entries))

    def test_run_private_packaged_validation_script_populates_and_invokes_verifier(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_private_packaged_validation.py")
        spec = importlib.util.spec_from_file_location("run_private_packaged_validation", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)

        with tempfile.TemporaryDirectory() as tmpdir:
            video_root = os.path.join(tmpdir, "video")
            dds_root = os.path.join(tmpdir, "dds")
            out_dir = os.path.join(tmpdir, "out")
            launch_target = os.path.join(tmpdir, "AlphaFixerConverter")
            os.makedirs(os.path.join(video_root, "ps1"), exist_ok=True)
            os.makedirs(dds_root, exist_ok=True)
            Path(os.path.join(video_root, "ps1", "sample.bin")).write_bytes(b"bin")
            Path(os.path.join(video_root, "ps1", "sample.cue")).write_text('FILE "sample.bin" BINARY\n', encoding="utf-8")
            Path(os.path.join(dds_root, "sky_cubemap.dds")).write_bytes(b"dds")
            Path(launch_target).write_text("stub", encoding="utf-8")
            os.chmod(launch_target, 0o755)
            captured = {}

            def _fake_verify(argv):
                captured["argv"] = list(argv)
                return 0

            with mock.patch.dict(
                os.environ,
                {
                    "ALPHA_FIXER_REAL_VIDEO_CORPUS": video_root,
                    "ALPHA_FIXER_REAL_DDS_CORPUS": dds_root,
                },
                clear=False,
            ), mock.patch.object(script.verify_packaged_app_script, "main", side_effect=_fake_verify):
                rc = script.main(
                    [
                        launch_target,
                        "--output-dir",
                        out_dir,
                        "--manifest-limit",
                        "8",
                        "--repeat",
                        "2",
                        "--repeat-selftest-runs",
                        "3",
                        "--selftest-stress-loops",
                        "2",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(os.path.join(out_dir, "manifests", "private_real_disc_video_manifest.json")))
            self.assertTrue(os.path.isfile(os.path.join(out_dir, "manifests", "private_real_dds_complex_manifest.json")))
            argv = captured["argv"]
            self.assertEqual(argv[0], launch_target)
            self.assertIn("--disc-video-manifest", argv)
            self.assertIn("--dds-manifest", argv)
            self.assertIn("--require-disc-manifest-group-checks", argv)
            self.assertIn("--require-dds-manifest-group-checks", argv)
            self.assertIn("--require-stress-selftest-checks", argv)
            self.assertIn("--max-smoke-elapsed-growth-seconds", argv)
            self.assertIn("--max-smoke-elapsed-spread-seconds", argv)
            summary_path = os.path.join(out_dir, "private-runtime-validation-summary.json")
            self.assertTrue(os.path.isfile(summary_path))
            summary_payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["status"], "completed")
            self.assertEqual(summary_payload["preflight"]["discovered_manifest_entry_counts"]["disc_video"], 1)
            self.assertEqual(summary_payload["preflight"]["discovered_manifest_entry_counts"]["dds"], 1)

    def test_run_private_packaged_validation_script_writes_preflight_summary_when_no_corpus_exists(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_private_packaged_validation.py")
        spec = importlib.util.spec_from_file_location("run_private_packaged_validation", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = os.path.join(tmpdir, "out")
            launch_target = os.path.join(tmpdir, "AlphaFixerConverter")
            Path(launch_target).write_text("stub", encoding="utf-8")
            os.chmod(launch_target, 0o755)
            with mock.patch.dict(
                os.environ,
                {
                    "ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST": "",
                    "ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST": "",
                    "ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST": "",
                    "ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST": "",
                    "ALPHA_FIXER_REAL_DDS_DX10_MANIFEST": "",
                    "ALPHA_FIXER_REAL_DISC_VIDEO_CORPUS": "",
                    "ALPHA_FIXER_REAL_VIDEO_CORPUS": "",
                    "ALPHA_FIXER_VIDEO_CORPUS_DIR": "",
                    "ALPHA_FIXER_REAL_DDS_DX10_CORPUS": "",
                    "ALPHA_FIXER_REAL_DDS_CORPUS": "",
                    "ALPHA_FIXER_DDS_CORPUS_DIR": "",
                },
                clear=False,
            ), mock.patch.object(script.verify_packaged_app_script, "main") as verify_main:
                rc = script.main([launch_target, "--output-dir", out_dir])
            self.assertEqual(rc, 1)
            verify_main.assert_not_called()
            summary_path = os.path.join(out_dir, "private-runtime-validation-summary.json")
            self.assertTrue(os.path.isfile(summary_path))
            summary_payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["status"], "preflight_failed")
            self.assertEqual(summary_payload["preflight"]["total_discovered_entries"], 0)
            self.assertIn("ALPHA_FIXER_REAL_DISC_VIDEO_CORPUS", summary_payload["preflight"]["corpus_env"])
            self.assertIn("No eligible private corpus samples were discovered", summary_payload["error"])

    def test_run_private_packaged_validation_summary_propagates_verifier_review_fields(self):
        module_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_private_packaged_validation.py")
        spec = importlib.util.spec_from_file_location("run_private_packaged_validation", module_path)
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)

        with tempfile.TemporaryDirectory() as tmpdir:
            video_root = os.path.join(tmpdir, "video")
            dds_root = os.path.join(tmpdir, "dds")
            out_dir = os.path.join(tmpdir, "out")
            launch_target = os.path.join(tmpdir, "AlphaFixerConverter")
            os.makedirs(os.path.join(video_root, "ps2"), exist_ok=True)
            os.makedirs(dds_root, exist_ok=True)
            Path(os.path.join(video_root, "ps2", "sample.iso")).write_bytes(b"iso")
            Path(os.path.join(dds_root, "texture_array.dds")).write_bytes(b"dds")
            Path(launch_target).write_text("stub", encoding="utf-8")
            os.chmod(launch_target, 0o755)

            def _fake_verify(argv):
                json_out = Path(argv[argv.index("--json-out") + 1])
                json_out.parent.mkdir(parents=True, exist_ok=True)
                json_out.write_text(
                    json.dumps(
                        {
                            "manifest_inputs": {"disc_video": {"provided": True}},
                            "runtime_selftest": {
                                "checks": {
                                    "external_disc_video_manifest_ps2": {"ok": True},
                                    "external_dds_manifest_array": {"ok": False, "detail": "array failed"},
                                },
                                "manifest_results": {
                                    "disc_video": {"ok": True, "detail": "disc ok", "sample_results": []},
                                    "dds": {"ok": False, "detail": "dds failed", "sample_results": [{"status": "failed", "label": "texture_array.dds", "detail": "unsupported array"}]},
                                },
                            },
                            "runtime_selftest_repeat_summary": {"runs": 2},
                            "runtime_selftest_check_repeat_summary": {
                                "runs": 2,
                                "checks": {"external_dds_manifest_array": {"stable_ok": False, "failed_runs": 1, "ok_runs": 1, "missing_runs": 0, "details": ["array failed"]}},
                                "unstable_checks": ["external_dds_manifest_array"],
                            },
                            "runtime_selftest_failed_checks": [{"name": "external_dds_manifest_array", "detail": "array failed"}],
                            "runtime_selftest_interesting_sample_outcomes": [{"manifest": "dds", "status": "failed", "label": "texture_array.dds", "detail": "unsupported array"}],
                            "runtime_selftest_manifest_review": {"dds": {"ok": False, "sample_status_counts": {"failed": 1}}},
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                return 0

            with mock.patch.dict(
                os.environ,
                {
                    "ALPHA_FIXER_REAL_VIDEO_CORPUS": video_root,
                    "ALPHA_FIXER_REAL_DDS_CORPUS": dds_root,
                },
                clear=False,
            ), mock.patch.object(script.verify_packaged_app_script, "main", side_effect=_fake_verify):
                rc = script.main([launch_target, "--output-dir", out_dir, "--manifest-limit", "8"])
            self.assertEqual(rc, 0)
            summary_path = os.path.join(out_dir, "private-runtime-validation-summary.json")
            summary_payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["runtime_selftest_repeat_summary"]["runs"], 2)
            self.assertIn("external_dds_manifest_array", summary_payload["runtime_selftest_check_repeat_summary"]["unstable_checks"])
            self.assertEqual(summary_payload["runtime_selftest_failed_checks"][0]["name"], "external_dds_manifest_array")
            self.assertEqual(summary_payload["runtime_selftest_interesting_sample_outcomes"][0]["label"], "texture_array.dds")
            self.assertEqual(summary_payload["runtime_selftest_manifest_review"]["dds"]["sample_status_counts"]["failed"], 1)


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


class TestConversionMetadata(unittest.TestCase):
    def test_all_exif_orientations_match_display_pixels_and_alpha(self):
        from src.core.alpha_processor import load_image

        methods = {
            2: Image.Transpose.FLIP_LEFT_RIGHT,
            3: Image.Transpose.ROTATE_180,
            4: Image.Transpose.FLIP_TOP_BOTTOM,
            5: Image.Transpose.TRANSPOSE,
            6: Image.Transpose.ROTATE_270,
            7: Image.Transpose.TRANSVERSE,
            8: Image.Transpose.ROTATE_90,
        }
        formats = [fmt for fmt in ("PNG", "JPEG", "TIFF", "WEBP", "AVIF")
                   if output_format_available(fmt)]
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for fmt in formats:
                for orientation in range(1, 9):
                    source = Path(directory) / ("source" + SUPPORTED_OUTPUT_FORMATS[fmt])
                    exif = Image.Exif()
                    exif[274] = orientation
                    exif[305] = "all orientations"
                    with Image.new("RGB", (40, 24), (240, 20, 20)) as raster:
                        raster.paste((20, 20, 240), (20, 0, 40, 24))
                        raster.paste((20, 240, 20), (0, 12, 20, 24))
                        raster.save(source, exif=exif.tobytes(), quality=100)
                    # Independently decode source pixels. TIFF's decoder already
                    # applies the orientation; other formats retain native pixels.
                    with Image.open(source) as raw:
                        raw.load()
                        expected = (raw.transpose(methods[orientation])
                                    if fmt != "TIFF" and orientation in methods else raw.copy())
                    try:
                        for opener in (_open_image, lambda p: _open_image_for_preview(p, 512),
                                       load_image):
                            with self.subTest(source=fmt, orientation=orientation, opener=opener):
                                with opener(str(source)) as loaded, loaded.convert("RGB") as rgb, expected.convert("RGB") as wanted_rgb:
                                    self.assertEqual(rgb.size, expected.size)
                                    self.assertEqual(rgb.tobytes(), wanted_rgb.tobytes())
                                    self.assertIn(loaded.getexif().get(274), (None, 1))
                        for target in formats + ["BMP"]:
                            for keep in (False, True):
                                for resize in (None, (20, 12)):
                                    with self.subTest(source=fmt, orientation=orientation,
                                                      target=target, keep=keep, resize=resize):
                                        output = Path(directory) / ("out" + SUPPORTED_OUTPUT_FORMATS[target])
                                        convert_file(str(source), str(output), target, quality=100,
                                                     resize=resize, keep_metadata=keep)
                                        wanted = expected.resize(resize, Image.LANCZOS) if resize else expected.copy()
                                        try:
                                            with Image.open(output) as encoded:
                                                encoded.load()
                                                self.assertIn(encoded.getexif().get(274), (None, 1))
                                                with encoded.convert("RGB") as decoded:
                                                    self.assertEqual(decoded.size, wanted.size)
                                                    for point in ((0, 0), (decoded.width - 1, 0),
                                                                  (0, decoded.height - 1),
                                                                  (decoded.width - 1, decoded.height - 1)):
                                                        self.assertLess(max(abs(a - b) for a, b in zip(
                                                            decoded.getpixel(point), wanted.getpixel(point))), 25)
                                                if keep and target != "BMP":
                                                    self.assertEqual(encoded.getexif()[305], "all orientations")
                                                if not keep:
                                                    self.assertNotIn(305, encoded.getexif())
                                        finally:
                                            wanted.close()
                    finally:
                        expected.close()

    def test_reduced_preview_normalizes_before_reduction(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "large.png"
            for orientation in range(1, 9):
                exif = Image.Exif()
                exif[274] = orientation
                with Image.new("RGB", (81, 49), "red") as raster:
                    raster.paste("blue", (40, 0, 81, 49))
                    raster.paste("green", (0, 24, 40, 49))
                    raster.save(source, exif=exif)
                with Image.open(source) as raw, ImageOps.exif_transpose(raw) as expected:
                    with expected.reduce(5) as reduced, _open_image_for_preview(str(source), 8) as preview:
                        self.assertEqual(preview.size, reduced.size)
                        self.assertEqual(preview.tobytes(), reduced.tobytes())
                        self.assertIn(preview.getexif().get(274), (None, 1))

    def test_dual_exif_xmp_reduced_preview_is_normalized_once(self):
        methods = {
            2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
            4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
            6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
            8: Image.Transpose.ROTATE_90,
        }

        def pillow10_transpose(image, *, in_place=False):
            self.assertTrue(in_place)
            image.load()
            exif = image.getexif()
            method = methods.get(exif.get(274))
            if method is not None:
                image.im = image.im.transpose(method)
                image._size = image.im.size
                del exif[274]
                # Pillow 10 uses an if/elif chain: EXIF excludes XMP cleanup.
                if "exif" in image.info:
                    image.info["exif"] = exif.tobytes()

        original_open = Image.open
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "dual.png"
            with Image.new("RGB", (81, 49), "red") as raster:
                raster.paste("blue", (40, 0, 81, 49))
                raster.paste("green", (0, 24, 40, 49))
                for orientation in range(1, 9):
                    exif = Image.Exif()
                    exif[274] = orientation
                    exif[305] = "retain EXIF software"
                    raster.save(source, exif=exif)
                    packet = (f'<rdf:Description tiff:Orientation="{orientation}" '
                              f'dc:description="retain XMP"><tiff:Orientation>{orientation}'
                              '</tiff:Orientation></rdf:Description>')
                    clean = '<rdf:Description  dc:description="retain XMP"></rdf:Description>'
                    representations = [
                        (packet, clean), (packet.encode(), clean.encode()),
                        ((packet.encode(), b"other XMP"), (clean.encode(), b"other XMP")),
                        ((packet, b"other XMP"), (clean, b"other XMP")),
                    ]
                    with (raster.transpose(methods[orientation]) if orientation in methods
                          else raster.copy()) as expected, expected.reduce(5) as reduced:
                        for legacy in (False, True):
                            for key in ("xmp", "XML:com.adobe.xmp"):
                                for value, cleaned in representations:
                                    def open_with_xmp(*args, **kwargs):
                                        image = original_open(*args, **kwargs)
                                        image.info[key] = value
                                        return image

                                    with self.subTest(orientation=orientation, legacy=legacy,
                                                      key=key, value_type=type(value)), mock.patch.object(
                                            Image, "open", side_effect=open_with_xmp), (
                                            mock.patch.object(ImageOps, "exif_transpose", pillow10_transpose)
                                            if legacy else nullcontext()):
                                        with _open_image_for_preview(str(source), 8) as preview:
                                            self.assertEqual(preview.size, reduced.size)
                                            self.assertEqual(preview.tobytes(), reduced.tobytes())
                                            self.assertIn(preview.getexif().get(274), (None, 1))
                                            self.assertEqual(preview.getexif()[305], "retain EXIF software")
                                            self.assertEqual(preview.info[key], cleaned)

    def test_xmp_only_orientation_is_cached_before_cleanup(self):
        from PIL.PngImagePlugin import PngInfo

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "xmp.png"
            metadata = PngInfo()
            metadata.add_text("XML:com.adobe.xmp",
                              '<rdf:Description tiff:Orientation="6" dc:title="keep"/>')
            with Image.new("RGB", (81, 49), "red") as raster:
                raster.paste("blue", (40, 0, 81, 49))
                raster.save(source, pnginfo=metadata)
                with raster.transpose(Image.Transpose.ROTATE_270) as expected, expected.reduce(5) as reduced:
                    with _open_image_for_preview(str(source), 8) as preview:
                        self.assertEqual(preview.size, reduced.size)
                        self.assertEqual(preview.tobytes(), reduced.tobytes())
                        self.assertNotIn(274, preview.getexif())
                        self.assertIn('dc:title="keep"', preview.info["XML:com.adobe.xmp"])

    def test_xmp_orientation_forms_and_representations_match_pixels(self):
        methods = TestTiffXmpOrientation._methods
        original_open = Image.open
        cases = 0
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for fmt in ("PNG", "JPEG", "TIFF", "WEBP"):
                if not output_format_available(fmt):
                    continue
                source = Path(directory) / ("forms" + SUPPORTED_OUTPUT_FORMATS[fmt])
                with Image.new("RGB", (31, 19), "red") as raster:
                    raster.paste("blue", (15, 0, 31, 19))
                    raster.paste("green", (0, 9, 15, 19))
                    raster.save(source, format=fmt)
                with original_open(source) as raw:
                    raw.load()
                    for orientation in range(1, 9):
                        for form in (
                            f"tiff:Orientation='{orientation}'",
                            f'tiff:Orientation = " {orientation} "',
                            f"<tiff:Orientation> {orientation} </tiff:Orientation>",
                        ):
                            packet = f'<rdf:Description dc:title="keep">{form}</rdf:Description>'
                            clean = '<rdf:Description dc:title="keep"></rdf:Description>'
                            for value, cleaned in (
                                (packet, clean), (packet.encode(), clean.encode()),
                                ((packet, b"other XMP"), (clean, b"other XMP")),
                                ((packet.encode(), "other XMP"), (clean.encode(), "other XMP")),
                            ):
                                for key in ("xmp", "XML:com.adobe.xmp"):
                                    def open_with_xmp(*args, **kwargs):
                                        image = original_open(*args, **kwargs)
                                        if image.format == "TIFF":
                                            image.getexif()
                                        image.info[key] = value
                                        return image

                                    with self.subTest(fmt=fmt, orientation=orientation,
                                                      form=form, key=key, representation=type(value)), (
                                            mock.patch.object(Image, "open", side_effect=open_with_xmp)):
                                        method = methods.get(orientation)
                                        with (raw.transpose(method) if method is not None else raw.copy()) as expected:
                                            with _open_image(str(source)) as actual:
                                                self.assertEqual(actual.size, expected.size)
                                                self.assertEqual(actual.tobytes(), expected.tobytes())
                                                self.assertIn(actual.getexif().get(274), (None, 1))
                                                self.assertEqual(actual.info[key], cleaned)
                                            with (expected.copy() if fmt == "JPEG" else expected.reduce(2)) as reduced, (
                                                    _open_image_for_preview(str(source), 16 if fmt == "JPEG" else 6)) as preview:
                                                self.assertEqual(preview.size, reduced.size)
                                                self.assertEqual(preview.tobytes(), reduced.tobytes())
                                        cases += 1
        self.assertGreaterEqual(cases, 576)

    def test_xmp_orientation_precedence_and_invalid_values(self):
        from src.core.file_converter import _normalize_image_orientation

        cases = [
            (None, "tiff:Orientation='6'", None, 6),
            (1, "tiff:Orientation='6'", None, 1),
            (2, "tiff:Orientation='6'", None, 2),
            (9, "tiff:Orientation='6'", None, 1),
            (None, "tiff:Orientation='0'", None, 1),
            (None, "tiff:Orientation='9'", None, 1),
            (None, "tiff:Orientation='16'", None, 1),
            (None, "tiff:Orientation='999999999999999999999999'", None, 1),
            (None, "tiff:Orientation='6'<tiff:Orientation> 8 </tiff:Orientation>", None, 6),
            (None, "tiff:Orientation='6'", "tiff:Orientation='8'", 8),
            (None, "tiff:Orientation='06'", None, 6),
        ]
        with Image.new("RGB", (31, 19), "red") as raster:
            raster.paste("blue", (15, 0, 31, 19))
            for explicit, xmp, xml, wanted in cases:
                with self.subTest(explicit=explicit, xmp=xmp, xml=xml), raster.copy() as actual:
                    exif = Image.Exif()
                    if explicit is not None:
                        exif[274] = explicit
                    actual.info["exif"] = exif.tobytes()
                    actual.info["xmp"] = xmp
                    if xml is not None:
                        actual.info["XML:com.adobe.xmp"] = xml
                    _normalize_image_orientation(actual)
                    method = TestTiffXmpOrientation._methods.get(wanted)
                    with (raster.transpose(method) if method is not None else raster.copy()) as expected:
                        self.assertEqual(actual.size, expected.size)
                        self.assertEqual(actual.tobytes(), expected.tobytes())
                    self.assertNotIn("tiff:Orientation", actual.info["xmp"])

    def test_all_orientations_ui_preview_matches_resized_exports(self):
        from src.ui.preview_pane import _ConverterPreviewLoader, _ThumbLoader, _pil_to_qimage

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "oriented.png"
            for orientation in range(1, 9):
                exif = Image.Exif()
                exif[274] = orientation
                with Image.new("RGB", (40, 24), "red") as raster:
                    raster.paste("blue", (20, 0, 40, 24))
                    raster.paste("green", (0, 12, 20, 24))
                    raster.save(source, exif=exif)
                with Image.open(source) as raw, ImageOps.exif_transpose(raw) as expected:
                    thumbs, errors = [], []
                    loader = _ThumbLoader(str(source))
                    loader.loaded.connect(lambda *args: thumbs.append(args))
                    loader.failed.connect(errors.append)
                    loader.run()
                    self.assertFalse(errors)
                    self.assertEqual(thumbs[0][0], _pil_to_qimage(expected))
                    for target in ("PNG", "JPEG", "TIFF", "WEBP"):
                        if not output_format_available(target):
                            continue
                        for resize in (None, (20, 12)):
                            results = []
                            loader = _ConverterPreviewLoader(str(source), target, 90, resize=resize)
                            loader.ready.connect(lambda *args: results.append(args))
                            loader.failed.connect(errors.append)
                            loader.run()
                            self.assertFalse(errors)
                            self.assertEqual(len(results), 1)
                            self.assertEqual(results[0][0], _pil_to_qimage(expected))
                            for keep in (False, True):
                                with self.subTest(orientation=orientation, target=target, resize=resize, keep=keep):
                                    output = Path(directory) / ("export" + SUPPORTED_OUTPUT_FORMATS[target])
                                    convert_file(str(source), str(output), target, quality=90,
                                                 resize=resize, keep_metadata=keep)
                                    with Image.open(output) as decoded:
                                        self.assertEqual(results[0][1], _pil_to_qimage(decoded))

    def test_orientation_normalization_does_not_accept_truncated_pixels(self):
        from PIL import ImageFile
        from src.core.alpha_processor import load_image

        with tempfile.TemporaryDirectory(dir=".") as directory:
            for fmt, mode in (("PNG", "RGBA"), ("JPEG", "RGB")):
                source = Path(directory) / ("damaged" + SUPPORTED_OUTPUT_FORMATS[fmt])
                exif = Image.Exif()
                exif[274] = 6
                with Image.new(mode, (80, 48), "red") as raster:
                    raster.paste("blue", (40, 0, 80, 48))
                    raster.save(source, exif=exif.tobytes())
                data = source.read_bytes()
                source.write_bytes(data[:len(data) // 2] if fmt == "PNG" else data[:-60])
                with mock.patch.object(ImageFile, "LOAD_TRUNCATED_IMAGES", False):
                    for opener in (_open_image, lambda p: _open_image_for_preview(p, 8), load_image):
                        with self.subTest(format=fmt, opener=opener), self.assertRaises(OSError):
                            opener(str(source))
                    output = Path(directory) / "protected.png"
                    output.write_bytes(b"existing output")
                    with self.assertRaises(OSError):
                        convert_file(str(source), str(output), "PNG", keep_metadata=False)
                    self.assertEqual(output.read_bytes(), b"existing output")

    def test_legacy_tiff_applied_orientation_is_not_exported_twice(self):
        methods = {
            2: Image.Transpose.FLIP_LEFT_RIGHT,
            3: Image.Transpose.ROTATE_180,
            4: Image.Transpose.FLIP_TOP_BOTTOM,
            5: Image.Transpose.TRANSPOSE,
            6: Image.Transpose.ROTATE_270,
            7: Image.Transpose.TRANSVERSE,
            8: Image.Transpose.ROTATE_90,
        }

        def legacy_load_end(image):
            # Pillow 10.0 load_end: rotate the core, retaining EXIF/tag_v2.
            orientation = image.tag_v2.get(274)
            image._tile_orientation = orientation
            if orientation in methods:
                image.im = image.im.transpose(methods[orientation])
                image._size = image.im.size

        with tempfile.TemporaryDirectory(dir=".") as directory:
            for size in ((40, 24), (24, 24)):
                with Image.new("RGB", size, "red") as raster:
                    raster.paste("blue", (size[0] // 2, 0, size[0], size[1]))
                    raster.paste("green", (0, size[1] // 2, size[0] // 2, size[1]))
                    for orientation, method in methods.items():
                        for compression in ("raw", "tiff_lzw"):
                            source = Path(directory) / "legacy.tif"
                            exif = Image.Exif()
                            exif[274] = orientation
                            exif[305] = "legacy TIFF regression"
                            if compression == "raw":
                                exif[34665] = {40962: size[0], 40963: size[1]}
                            raster.save(source, exif=exif.tobytes(), compression=compression)
                            with self.subTest(size=size, orientation=orientation,
                                              compression=compression), mock.patch.object(
                                    TiffImagePlugin.TiffImageFile, "load_end", legacy_load_end):
                                with raster.transpose(method) as expected:
                                    for opener in (_open_image, lambda path: _open_image_for_preview(path, 512)):
                                        with opener(str(source)) as loaded:
                                            self.assertEqual(loaded.size, expected.size)
                                            self.assertEqual(loaded.tobytes(), expected.tobytes())
                                            self.assertNotIn(274, loaded.getexif())
                                            with ImageOps.exif_transpose(loaded) as displayed:
                                                self.assertEqual(displayed.tobytes(), expected.tobytes())
                                    with _open_image_for_preview(str(source), 8) as preview:
                                        factor = max(1, max(expected.size) // 16)
                                        with expected.reduce(factor) as reduced:
                                            self.assertEqual(preview.size, reduced.size)
                                            self.assertEqual(preview.tobytes(), reduced.tobytes())
                                            self.assertNotIn(274, preview.getexif())
                                    targets = ("PNG", "JPEG", "TIFF")
                                    for target in targets:
                                        for resize in (None, (20, 12)):
                                            output = Path(directory) / ("output" + SUPPORTED_OUTPUT_FORMATS[target])
                                            convert_file(str(source), str(output), target, quality=100,
                                                         resize=resize, keep_metadata=True)
                                            wanted = expected.resize(resize, Image.LANCZOS) if resize else expected.copy()
                                            try:
                                                with _open_image(str(output)) as saved:
                                                    self.assertEqual(saved.size, wanted.size)
                                                    self.assertNotIn(274, saved.getexif())
                                                    self.assertEqual(saved.getexif()[305], "legacy TIFF regression")
                                                    details = saved.getexif().get_ifd(34665)
                                                    if compression == "raw":
                                                        self.assertEqual((details[40962], details[40963]), wanted.size)
                                                    for point in ((0, 0), (wanted.width - 1, wanted.height - 1)):
                                                        self.assertLess(max(abs(a - b) for a, b in zip(
                                                            saved.getpixel(point), wanted.getpixel(point))), 25)
                                            finally:
                                                wanted.close()

    def test_unapplied_tiff_orientation_is_normalized(self):
        def unrotated_load_end(image):
            image._tile_orientation = image.tag_v2.get(274)
            image._size = image.im.size

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "unrotated.tif"
            exif = Image.Exif()
            exif[274] = 6
            with Image.new("RGB", (40, 24), "red") as raster:
                raster.paste("blue", (20, 0, 40, 24))
                raster.save(source, exif=exif)
                with mock.patch.object(TiffImagePlugin.TiffImageFile, "load_end", unrotated_load_end):
                    with _open_image(str(source)) as loaded:
                        with raster.transpose(Image.Transpose.ROTATE_270) as expected:
                            self.assertEqual(loaded.size, expected.size)
                            self.assertEqual(loaded.tobytes(), expected.tobytes())
                        self.assertNotIn(274, loaded.getexif())
                    output = Path(directory) / "unrotated.png"
                    convert_file(str(source), str(output), "PNG", keep_metadata=True)
                    with Image.open(output) as saved:
                        saved.load()
                        with raster.transpose(Image.Transpose.ROTATE_270) as expected:
                            self.assertEqual(saved.size, expected.size)
                            self.assertEqual(saved.tobytes(), expected.tobytes())
                        self.assertNotIn(274, saved.getexif())

    def test_oriented_metadata_encode_decode_matrix(self):
        formats = [fmt for fmt in ("PNG", "JPEG", "TIFF", "WEBP", "AVIF")
                   if output_format_available(fmt)]
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            for source_format in formats:
                source = root / ("source" + SUPPORTED_OUTPUT_FORMATS[source_format])
                with Image.new("RGB", (40, 24), (240, 30, 20)) as raster:
                    raster.paste((20, 30, 240), (20, 0, 40, 24))
                    exif = Image.Exif()
                    exif[274] = 6
                    exif[305] = "generated metadata regression"
                    exif[34665] = {40962: 40, 40963: 24}
                    if source_format != "TIFF":
                        exif[256], exif[257] = raster.size
                    raster.save(source, exif=exif.tobytes(), icc_profile=profile,
                                dpi=(144, 144), quality=100)
                with _open_image(str(source)) as original:
                    for target_format in formats:
                        for keep in (False, True):
                            for resize in (None, (20, 12)):
                                with self.subTest(source=source_format, target=target_format,
                                                  keep=keep, resize=resize):
                                    destination = root / ("output" + SUPPORTED_OUTPUT_FORMATS[target_format])
                                    convert_file(str(source), str(destination), target_format,
                                                 quality=100, resize=resize, keep_metadata=keep)
                                    expected = original.resize(resize, Image.LANCZOS) if resize else original.copy()
                                    try:
                                        native_size = expected.size
                                        with _open_image(str(destination)) as decoded:
                                            self.assertEqual(decoded.size, expected.size)
                                            with decoded.convert("RGB") as rgb:
                                                for x, y in ((0, 0), (decoded.width - 1, decoded.height - 1)):
                                                    actual = rgb.getpixel((x, y))
                                                    wanted = expected.getpixel((x, y))
                                                    self.assertLess(max(abs(a - b) for a, b in zip(actual, wanted)), 25)
                                            saved_exif = decoded.getexif()
                                            if keep:
                                                self.assertEqual(saved_exif[305], "generated metadata regression")
                                                self.assertEqual(decoded.info.get("icc_profile"), profile)
                                                self.assertEqual(saved_exif.get_ifd(34665)[40962], native_size[0])
                                                self.assertEqual(saved_exif.get_ifd(34665)[40963], native_size[1])
                                                for tag, value in ((256, native_size[0]), (257, native_size[1])):
                                                    if tag in saved_exif:
                                                        self.assertEqual(saved_exif[tag], value)
                                                if target_format in ("PNG", "JPEG", "TIFF") and "dpi" in original.info:
                                                    for value in decoded.info["dpi"]:
                                                        self.assertAlmostEqual(value, 144, delta=0.1)
                                                self.assertNotIn(274, saved_exif)
                                            else:
                                                self.assertNotIn(305, saved_exif)
                                                self.assertNotIn(274, saved_exif)
                                                self.assertNotIn("icc_profile", decoded.info)
                                                self.assertNotIn("exif", decoded.info)
                                    finally:
                                        expected.close()

    def test_nested_exif_roundtrip_preserves_interoperability_and_dimensions(self):
        interop = {1: "R98", 2: b"0100", 4096: "nested interoperability"}
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for source_format in ("PNG", "JPEG", "TIFF", "WEBP", "AVIF"):
                if not output_format_available(source_format):
                    continue
                source = Path(directory) / ("nested" + SUPPORTED_OUTPUT_FORMATS[source_format])
                exif = Image.Exif()
                exif[305] = "nested EXIF regression"
                exif[256], exif[257] = 40, 24
                exif[34665] = {
                    36867: "2026:10:11 03:00:00",
                    40962: 40, 40963: 24, 40965: interop,
                }
                source_metadata = exif.tobytes()
                if source_format == "TIFF":
                    exif.load(source_metadata)
                    exif.get_ifd(34665)[40965] = exif.get_ifd(40965)
                    source_metadata = exif
                with Image.new("RGB", (40, 24), "red") as raster:
                    raster.save(source, exif=source_metadata, quality=100)
                with Image.open(source) as original:
                    self.assertEqual(original.getexif().get_ifd(40965), interop)
                for target_format in ("PNG", "JPEG", "TIFF", "WEBP", "AVIF"):
                    if not output_format_available(target_format):
                        continue
                    for resize in (None, (20, 12)):
                        with self.subTest(source=source_format, target=target_format, resize=resize):
                            destination = Path(directory) / ("output" + SUPPORTED_OUTPUT_FORMATS[target_format])
                            convert_file(str(source), str(destination), target_format,
                                         quality=100, resize=resize, keep_metadata=True)
                            with Image.open(destination) as decoded:
                                # TIFF closes its file on load before caching the
                                # nested IFD, so resolve it while the file is open.
                                saved_exif = decoded.getexif()
                                self.assertEqual(saved_exif.get_ifd(40965), interop)
                                details = saved_exif.get_ifd(34665)
                                self.assertEqual(details[36867], "2026:10:11 03:00:00")
                                self.assertEqual(saved_exif[305], "nested EXIF regression")
                                size = resize or (40, 24)
                                self.assertEqual((details[40962], details[40963]), size)
                                self.assertEqual((saved_exif[256], saved_exif[257]), size)
                                decoded.load()
                                self.assertEqual(decoded.size, size)

    def test_malformed_exif_falls_back_to_pixels_and_other_metadata(self):
        from src.core.alpha_processor import load_image

        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "malformed.png"
            with Image.new("RGB", (40, 24), "red") as raster:
                raster.save(source, exif=b"Exif\x00\x00invalid TIFF header",
                            icc_profile=profile, dpi=(144, 144))
            for opener in (_open_image, lambda p: _open_image_for_preview(p, 512), load_image):
                with self.assertWarnsRegex(UserWarning, "Malformed EXIF"):
                    with opener(str(source)) as loaded:
                        self.assertEqual(loaded.size, (40, 24))
                        self.assertFalse(loaded.getexif())
                        self.assertEqual(loaded.info.get("icc_profile"), profile)
            for fmt in ("PNG", "JPEG", "TIFF", "WEBP", "AVIF"):
                if not output_format_available(fmt):
                    continue
                with self.subTest(format=fmt):
                    destination = Path(directory) / ("output" + SUPPORTED_OUTPUT_FORMATS[fmt])
                    convert_file(str(source), str(destination), fmt, quality=100,
                                 resize=(20, 12), keep_metadata=True)
                    with _open_image(str(destination)) as decoded:
                        self.assertEqual(decoded.size, (20, 12))
                        self.assertEqual(decoded.info.get("icc_profile"), profile)
                        self.assertFalse(decoded.getexif().get(34665))
                        with decoded.convert("RGB") as rgb:
                            self.assertLess(max(abs(a - b) for a, b in
                                                zip(rgb.getpixel((0, 0)), (255, 0, 0))), 25)

    def test_preview_normalizes_orientation_without_double_rotation(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for fmt in ("PNG", "JPEG", "TIFF", "WEBP", "AVIF"):
                if not output_format_available(fmt):
                    continue
                with self.subTest(format=fmt):
                    source = Path(directory) / ("oriented" + SUPPORTED_OUTPUT_FORMATS[fmt])
                    exif = Image.Exif()
                    exif[274] = 6
                    with Image.new("RGB", (40, 24), "red") as raster:
                        raster.paste("blue", (20, 0, 40, 24))
                        raster.save(source, exif=exif.tobytes(), quality=100)
                    with _open_image(str(source)) as native, _open_image_for_preview(str(source), 512) as preview:
                        self.assertEqual(preview.size, native.size)
                        self.assertEqual(preview.tobytes(), native.tobytes())
                        self.assertEqual(preview.getexif().get(274), native.getexif().get(274))
                        self.assertNotIn(274, preview.getexif())
                        with ImageOps.exif_transpose(preview) as displayed:
                            self.assertEqual(displayed.size, preview.size)
                            self.assertEqual(displayed.tobytes(), preview.tobytes())

    def test_converter_preview_matches_native_pixel_export(self):
        from src.ui.preview_pane import _ConverterPreviewLoader, _pil_to_qimage

        with tempfile.TemporaryDirectory(dir=".") as directory:
            for source_format in ("PNG", "TIFF"):
                source = Path(directory) / ("oriented" + SUPPORTED_OUTPUT_FORMATS[source_format])
                exif = Image.Exif()
                exif[274] = 6
                with Image.new("RGB", (40, 24), "red") as raster:
                    raster.paste("blue", (20, 0, 40, 24))
                    raster.save(source, exif=exif.tobytes())
                for target_format in ("PNG", "JPEG", "TIFF", "WEBP"):
                    if not output_format_available(target_format):
                        continue
                    for resize in (None, (20, 12)):
                        with self.subTest(source=source_format, target=target_format, resize=resize):
                            results, errors = [], []
                            loader = _ConverterPreviewLoader(str(source), target_format, 90, resize=resize)
                            loader.ready.connect(lambda *args: results.append(args))
                            loader.failed.connect(errors.append)
                            loader.run()
                            self.assertFalse(errors)
                            self.assertEqual(len(results), 1)
                            destination = Path(directory) / ("export" + SUPPORTED_OUTPUT_FORMATS[target_format])
                            convert_file(str(source), str(destination), target_format, quality=90, resize=resize)
                            with _open_image(str(destination)) as decoded:
                                self.assertEqual(results[0][1], _pil_to_qimage(decoded))


class TestTiffXmpOrientation(unittest.TestCase):
    _methods = {
        2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }

    def test_tag_only_xmp_without_decoder_transpose_matches_normalized_pixels(self):
        real_open = Image.open
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "tag-only.tiff"
            output = Path(directory) / "normalized.tiff"
            with Image.new("RGBA", (31, 19), (255, 0, 0, 255)) as raster:
                raster.paste((0, 0, 255, 128), (15, 0, 31, 19))
                raster.paste((0, 255, 0, 0), (0, 9, 15, 19))
                for orientation in range(1, 9):
                    packet = (
                        f'<rdf:Description tiff:Orientation="{orientation}" dc:title="keep"/>'
                    ).encode()
                    exif = Image.Exif()
                    exif[700] = packet
                    raster.save(source, exif=exif)
                    for cached_exif in (True, False):
                        def open_without_info_xmp(*args, **kwargs):
                            image = real_open(*args, **kwargs)
                            if Path(args[0]).resolve() == source.resolve():
                                cached = image.getexif()
                                # Simulate Pillow 10: no synthesized orientation
                                # and no decoder transpose from an XMP-only tag.
                                cached.pop(274, None)
                                image.tag_v2.pop(274, None)
                                image._tile_orientation = None
                                for key in ("xmp", "XML:com.adobe.xmp"):
                                    image.info.pop(key, None)
                                if not cached_exif:
                                    cached.pop(700, None)
                            return image

                        method = self._methods.get(orientation)
                        with self.subTest(orientation=orientation, cached_exif=cached_exif), (
                                raster.transpose(method) if method is not None else raster.copy()) as expected:
                            with mock.patch.object(Image, "open", side_effect=open_without_info_xmp):
                                for opener in (_open_image, lambda path: _open_image_for_preview(path, 64)):
                                    with opener(str(source)) as actual:
                                        self.assertEqual(actual.size, expected.size)
                                        self.assertEqual(actual.tobytes(), expected.tobytes())
                                        self.assertIn(actual.getexif().get(274), (None, 1))
                                        self.assertEqual(
                                            actual.tag_v2[700], b'<rdf:Description  dc:title="keep"/>',
                                        )
                                convert_file(str(source), str(output), "TIFF", keep_metadata=True)
                            with _open_image(str(output)) as actual:
                                self.assertEqual(actual.size, expected.size)
                                self.assertEqual(actual.tobytes(), expected.tobytes())
                                self.assertIn(actual.getexif().get(274), (None, 1))
                                if cached_exif:
                                    self.assertEqual(
                                        actual.getexif()[700], b'<rdf:Description  dc:title="keep"/>',
                                    )

    def test_native_xmp_single_quotes_and_element_whitespace(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "native.tiff"
            output = Path(directory) / "normalized.tiff"
            with Image.new("RGB", (31, 19), "red") as raster:
                raster.paste("blue", (15, 0, 31, 19))
                raster.paste("green", (0, 9, 15, 19))
                for orientation in range(1, 9):
                    for form in (f"tiff:Orientation='{orientation}'",
                                 f"<tiff:Orientation> {orientation} </tiff:Orientation>"):
                        packet = f'<rdf:Description dc:title="keep">{form}</rdf:Description>'.encode()
                        exif = Image.Exif()
                        exif[700] = packet
                        raster.save(source, exif=exif)
                        method = self._methods.get(orientation)
                        with self.subTest(orientation=orientation, form=form), (
                                raster.transpose(method) if method is not None else raster.copy()) as expected:
                            convert_file(str(source), str(output), "TIFF", keep_metadata=True)
                            for path in (source, output):
                                with _open_image(str(path)) as actual:
                                    self.assertEqual(actual.size, expected.size)
                                    self.assertEqual(actual.tobytes(), expected.tobytes())
                                    self.assertIn(actual.getexif().get(274), (None, 1))
                                    self.assertEqual(actual.getexif()[700],
                                                     b'<rdf:Description dc:title="keep"></rdf:Description>')

    def _write_source(self, source, specs):
        expected = []
        with TiffImagePlugin.AppendingTiffWriter(str(source), True) as writer:
            for index, (exif_orientation, xmp_orientation) in enumerate(specs):
                packet = (
                    f'<rdf:Description tiff:Orientation="{xmp_orientation}" '
                    f'dc:description="page-{index}"><tiff:Orientation>{xmp_orientation}'
                    '</tiff:Orientation></rdf:Description>'
                ).encode()
                exif = Image.Exif()
                exif[700] = packet
                exif[305] = f"software-{index}"
                if exif_orientation is not None:
                    exif[274] = exif_orientation
                with Image.new("RGB", (17 + index, 11 + index), "red") as raster:
                    raster.paste("blue", (8, 0, raster.width, raster.height))
                    raster.paste("green", (0, 5, 8, raster.height))
                    raster.save(writer, format="TIFF", exif=exif)
                    orientation = exif_orientation or xmp_orientation
                    method = self._methods.get(orientation)
                    expected.append(raster.transpose(method) if method is not None else raster.copy())
                writer.newFrame()
        return expected

    def _assert_metadata(self, image, index):
        self.assertIn(image.getexif().get(274), (None, 1))
        self.assertEqual(image.getexif()[305], f"software-{index}")
        clean = (
            f'<rdf:Description  dc:description="page-{index}"></rdf:Description>'
        ).encode()
        self.assertEqual(image.getexif()[700], clean)
        self.assertEqual(image.tag_v2[700], clean)
        for key in ("xmp", "XML:com.adobe.xmp"):
            if key in image.info:
                value = image.info[key]
                self.assertEqual(value.encode() if isinstance(value, str) else value, clean)

    def _assert_roundtrip(self, source, directory, specs, resize):
        expected = self._write_source(source, specs)
        try:
            with _open_image(str(source)) as normalized:
                self._assert_metadata(normalized, 0)
                self.assertEqual(normalized.size, expected[0].size)
                self.assertEqual(normalized.tobytes(), expected[0].tobytes())
            for generation in range(2):
                output = Path(directory) / f"output-{generation}.tiff"
                convert_file(str(source), str(output), "TIFF",
                             keep_metadata=True, resize=resize)
                with Image.open(output) as pages:
                    self.assertEqual(pages.n_frames, len(specs))
                    for index, wanted in enumerate(expected):
                        pages.seek(index)
                        # Inspect before loading: stale XMP must not synthesize
                        # another Orientation tag when the output is reopened.
                        self._assert_metadata(pages, index)
                        pages.load()
                        self._assert_metadata(pages, index)
                        with (wanted.resize(resize, Image.LANCZOS) if resize
                              else wanted.copy()) as display:
                            self.assertEqual(pages.size, display.size)
                            self.assertEqual(pages.tobytes(), display.tobytes())
                with _open_image(str(output)) as reopened:
                    self._assert_metadata(reopened, 0)
                    with (expected[0].resize(resize, Image.LANCZOS) if resize
                          else expected[0].copy()) as display:
                        self.assertEqual(reopened.size, display.size)
                        self.assertEqual(reopened.tobytes(), display.tobytes())
                source = output
                # Re-encoding an already resized page must not resample again.
                if resize:
                    resized = [image.resize(resize, Image.LANCZOS) for image in expected]
                    for image in expected:
                        image.close()
                    expected = resized
                    resize = None
        finally:
            for image in expected:
                image.close()

    def test_single_page_xmp_only_and_dual_orientation_keep_metadata_reopen(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "source.tiff"
            for orientation in range(1, 9):
                for exif_orientation, xmp_orientation in (
                    (None, orientation), (orientation, orientation),
                    (orientation, 9 - orientation),
                ):
                    for resize in (None, (9, 7)):
                        with self.subTest(exif=exif_orientation, xmp=xmp_orientation,
                                          resize=resize):
                            self._assert_roundtrip(
                                source, directory, [(exif_orientation, xmp_orientation)], resize,
                            )

    def test_multipage_mixed_xmp_only_and_dual_orientation_keep_metadata_reopen(self):
        specs = [
            spec for orientation in range(1, 9)
            for spec in ((None, orientation), (orientation, orientation),
                         (orientation, 9 - orientation))
        ]
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for resize in (None, (9, 7)):
                with self.subTest(resize=resize):
                    self._assert_roundtrip(
                        Path(directory) / "source.tiff", directory, specs, resize,
                    )


class TestMultipageTiffBoundary(unittest.TestCase):
    def _make_source(self, directory, profiles=None, specs=None):
        source = Path(directory) / "multipage.tiff"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        specs = specs if specs is not None else [
            ((40, 24), "red", "blue", 6),
            ((18, 30), "green", "yellow", 3),
            ((22, 16), "purple", "white", 1),
        ]
        profiles = (profile,) * len(specs) if profiles is None else profiles
        class FixtureWriter(TiffImagePlugin.AppendingTiffWriter):
            # Nested dimensions use inline values; relocate their parent pointer.
            Tags = TiffImagePlugin.AppendingTiffWriter.Tags | {34665}

        with FixtureWriter(str(source), True) as writer:
            for index, (size, left, right, orientation) in enumerate(specs):
                exif = Image.Exif()
                exif[274] = orientation
                exif[297] = (index, len(specs))
                exif[305] = f"page-{index + 1}-metadata"
                exif[34665] = {40962: size[0], 40963: size[1]}
                with Image.new("RGB", size, left) as page:
                    page.paste(right, (size[0] // 2, 0, *size))
                    page.save(writer, format="TIFF", exif=exif.tobytes(),
                              icc_profile=profiles[index], dpi=(100 + index, 100 + index))
                writer.newFrame()
        with Image.open(source) as pages:
            self.assertEqual(pages.n_frames, len(specs))
            for index, (size, _, _, orientation) in enumerate(specs):
                pages.seek(index)
                self.assertEqual((pages.tag_v2[256], pages.tag_v2[257]), size)
                self.assertEqual(pages.getexif()[274], orientation)
                self.assertEqual(pages.getexif()[305], f"page-{index + 1}-metadata")
        return source, profiles[0]

    def test_mixed_page_metadata_does_not_contaminate_export_or_preview(self):
        from src.ui.preview_pane import _ThumbLoader

        first_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        later_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
        with tempfile.TemporaryDirectory(dir=".") as directory:
            for profile in (None, first_profile):
                source, _ = self._make_source(directory, (profile, later_profile, None))
                for opener in (
                    lambda: _open_image(str(source)),
                    lambda: _open_image_for_preview(str(source), 512),
                    lambda: _open_image_for_preview(str(source), 4),
                ):
                    with self.subTest(first_icc=profile is not None, opener=opener):
                        with opener() as image:
                            self.assertEqual(image.info.get("icc_profile"), profile)
                            self.assertEqual("icc_profile" in image.info, profile is not None)
                            self.assertEqual(image.info["tiff_page_count"], 3)
                            if image.format == "TIFF":
                                self.assertEqual(image.getexif()[305], "page-1-metadata")
                            self.assertEqual(tuple(map(float, image.info["dpi"])), (100, 100))
                thumbnails, errors = [], []
                loader = _ThumbLoader(str(source), max_size=4)
                loader.loaded.connect(lambda *args: thumbnails.append(args))
                loader.failed.connect(errors.append)
                loader.run()
                self.assertFalse(errors)
                self.assertEqual(len(thumbnails), 1)
                self.assertEqual("ICC" in thumbnails[0][1], profile is not None)
                for fmt in ("PNG", "TIFF"):
                    for keep in (False, True):
                        for resize in (None, (12, 20)):
                            with self.subTest(first_icc=profile is not None, fmt=fmt,
                                              keep=keep, resize=resize):
                                output = Path(directory) / ("mixed" + SUPPORTED_OUTPUT_FORMATS[fmt])
                                with (self.assertWarns(UserWarning) if fmt != "TIFF"
                                      else nullcontext()):
                                    convert_file(str(source), str(output), fmt,
                                                 keep_metadata=keep, resize=resize)
                                with Image.open(output) as saved:
                                    self.assertEqual(saved.info.get("icc_profile"), profile if keep else None)
                                    self.assertEqual("icc_profile" in saved.info, keep and profile is not None)
                                    if keep:
                                        self.assertEqual(saved.getexif()[305], "page-1-metadata")

    def test_page_count_handles_close_on_success_and_failure(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            real_open = Image.open
            for opener in (
                lambda: _open_image(str(source)),
                lambda: _open_image_for_preview(str(source), 4),
            ):
                for failure in (None, OSError, SyntaxError, MemoryError):
                    handles = []

                    def tracked_open(*args, **kwargs):
                        image = real_open(*args, **kwargs)
                        handles.append(image.fp)
                        return image

                    with self.subTest(opener=opener, failure=failure):
                        with mock.patch("src.core.file_converter.Image.open", side_effect=tracked_open):
                            if failure is None:
                                with opener():
                                    self.assertEqual(len(handles), 2)
                                    self.assertTrue(handles[1].closed)
                            else:
                                with mock.patch.object(TiffImagePlugin.TiffImageFile, "n_frames",
                                                       new_callable=mock.PropertyMock,
                                                       side_effect=failure("page count failed")):
                                    if failure is MemoryError:
                                        with self.assertRaises(MemoryError):
                                            opener()
                                    else:
                                        with opener() as image:
                                            self.assertIsNone(image.info["tiff_page_count"])
                                self.assertEqual(len(handles), 2)
                        self.assertTrue(all(handle.closed for handle in handles))

    def test_unknown_page_count_preserves_first_page_and_discloses_limit(self):
        from src.core.file_converter import get_tiff_page_count
        from src.ui.converter_tool import ConverterTab
        from src.ui.preview_pane import _ConverterPreviewLoader, _ThumbLoader, _pil_to_qimage

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, profile = self._make_source(directory)
            with _open_image(str(source)) as native:
                expected = native.copy()
            try:
                with mock.patch.object(TiffImagePlugin.TiffImageFile, "n_frames",
                                       new_callable=mock.PropertyMock,
                                       side_effect=SyntaxError("unsupported later page")):
                    self.assertIsNone(get_tiff_page_count(str(source)))
                    for opener in (_open_image, lambda path: _open_image_for_preview(path, 512)):
                        with opener(str(source)) as image:
                            self.assertEqual(image.tobytes(), expected.tobytes())
                            self.assertEqual(image.getexif()[305], "page-1-metadata")
                            self.assertEqual(image.info["icc_profile"], profile)
                            self.assertIsNone(image.info["tiff_page_count"])
                    output = Path(directory) / "first.png"
                    with self.assertWarnsRegex(UserWarning, "page 1 only.*total page count unavailable"):
                        convert_file(str(source), str(output), "PNG", keep_metadata=True)
                    with Image.open(output) as saved:
                        self.assertEqual(saved.tobytes(), expected.tobytes())
                        self.assertEqual(saved.getexif()[305], "page-1-metadata")
                    results, thumbnails, errors = [], [], []
                    loader = _ConverterPreviewLoader(str(source), "PNG", 90)
                    loader.ready.connect(lambda *args: results.append(args))
                    loader.failed.connect(errors.append)
                    loader.run()
                    thumb = _ThumbLoader(str(source), max_size=4)
                    thumb.loaded.connect(lambda *args: thumbnails.append(args))
                    thumb.failed.connect(errors.append)
                    thumb.run()
                    self.assertFalse(errors)
                    self.assertEqual(len(results), 1)
                    self.assertEqual(len(thumbnails), 1)
                    self.assertEqual(results[0][1], _pil_to_qimage(expected))
                    for text in (*results[0][2:], thumbnails[0][1]):
                        self.assertIn("page 1 only", text)
                        self.assertIn("total page count unavailable", text)
                    for threshold in (1, 100):
                        done = []
                        worker = ConverterWorker([str(source)], "PNG", ".png",
                                                 output_dir=str(Path(directory) / f"converted-{threshold}"))
                        worker.file_done.connect(lambda *args: done.append(args))
                        with mock.patch("src.core.worker._LARGE_BATCH_THRESHOLD", threshold), \
                                self.assertWarnsRegex(UserWarning, "total page count unavailable"):
                            worker.run()
                        self.assertEqual(len(done), 1)
                        self.assertTrue(done[0][1], done[0][2])
                        destination, note = done[0][2].split("\n", 1)
                        with Image.open(destination) as saved:
                            self.assertEqual(saved.tobytes(), expected.tobytes())
                        logs = []
                        ConverterTab._on_file_done(types.SimpleNamespace(_log_msg=logs.append), *done[0])
                        self.assertIn("page 1 only", logs[0])
                        self.assertIn("total page count unavailable", logs[0])
            finally:
                expected.close()

    def test_page_count_probe_closes_and_propagates_resource_and_open_failures(self):
        from src.core.file_converter import get_tiff_page_count

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            real_open = Image.open
            for failure in (SyntaxError, MemoryError):
                handles = []

                def tracked_open(*args, **kwargs):
                    image = real_open(*args, **kwargs)
                    handles.append(image.fp)
                    return image

                with self.subTest(failure=failure), \
                        mock.patch("src.core.file_converter.Image.open", side_effect=tracked_open), \
                        mock.patch.object(TiffImagePlugin.TiffImageFile, "n_frames",
                                          new_callable=mock.PropertyMock,
                                          side_effect=failure("count failed")):
                    if failure is MemoryError:
                        with self.assertRaises(MemoryError):
                            get_tiff_page_count(str(source))
                    else:
                        self.assertIsNone(get_tiff_page_count(str(source)))
                self.assertEqual(len(handles), 1)
                self.assertTrue(handles[0].closed)
            with mock.patch("src.core.file_converter.Image.open",
                            side_effect=SyntaxError("invalid first page")):
                with self.assertRaisesRegex(SyntaxError, "invalid first page"):
                    get_tiff_page_count(str(source))

    def test_unknown_count_does_not_hide_first_page_decode_failure(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            for opener in (_open_image, lambda path: _open_image_for_preview(path, 4)):
                for failure in (SyntaxError, OSError, MemoryError):
                    with self.subTest(opener=opener, failure=failure), \
                            mock.patch.object(TiffImagePlugin.TiffImageFile, "n_frames",
                                              new_callable=mock.PropertyMock,
                                              side_effect=SyntaxError("unsupported later page")), \
                            mock.patch("src.core.file_converter._load_image_pixels",
                                       side_effect=failure("first page decode failed")):
                        with self.assertRaises(failure):
                            opener(str(source))

    def test_export_warns_and_keeps_only_first_page_pixels_and_metadata(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, profile = self._make_source(directory)
            with _open_image(str(source)) as native:
                expected = native.copy()
            try:
                for fmt in ("PNG",):
                    for keep in (False, True):
                        for resize in (None, (12, 20)):
                            with self.subTest(fmt=fmt, keep=keep, resize=resize):
                                output = Path(directory) / ("output" + SUPPORTED_OUTPUT_FORMATS[fmt])
                                with self.assertWarnsRegex(UserWarning, "page 1 of 3 only"):
                                    convert_file(str(source), str(output), fmt,
                                                 keep_metadata=keep, resize=resize)
                                with _open_image(str(output)) as saved:
                                    self.assertEqual(getattr(saved, "n_frames", 1), 1)
                                    reference = expected.resize(resize, Image.LANCZOS) if resize else expected.copy()
                                    try:
                                        self.assertEqual(saved.size, reference.size)
                                        self.assertEqual(saved.tobytes(), reference.tobytes())
                                    finally:
                                        reference.close()
                                    self.assertNotIn(297, saved.getexif())
                                    if keep:
                                        self.assertEqual(saved.getexif()[305], "page-1-metadata")
                                        self.assertEqual(saved.info["icc_profile"], profile)
                                        for dpi in saved.info["dpi"]:
                                            self.assertAlmostEqual(float(dpi), 100, delta=0.01)
                                    else:
                                        self.assertNotIn(305, saved.getexif())
                                        self.assertNotIn("icc_profile", saved.info)
            finally:
                expected.close()

    def test_preview_discloses_page_limit_and_matches_single_page_export(self):
        from src.ui.preview_pane import _ConverterPreviewLoader, _ThumbLoader, _pil_to_qimage

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            for fmt in ("PNG", "JPEG", "TIFF", "WEBP"):
                for resize in (None, (12, 20)):
                    with self.subTest(fmt=fmt, resize=resize):
                        results, errors = [], []
                        loader = _ConverterPreviewLoader(str(source), fmt, 90, resize=resize)
                        loader.ready.connect(lambda *args: results.append(args))
                        loader.failed.connect(errors.append)
                        loader.run()
                        self.assertFalse(errors)
                        self.assertEqual(len(results), 1)
                        for text in results[0][2:]:
                            self.assertIn("page 1 of 3 only", text)
                            self.assertIn("TIFF export preserves" if fmt == "TIFF"
                                          else "metadata are not exported", text)
                        output = Path(directory) / ("export" + SUPPORTED_OUTPUT_FORMATS[fmt])
                        with (self.assertWarns(UserWarning) if fmt != "TIFF"
                              else nullcontext()):
                            convert_file(str(source), str(output), fmt, resize=resize)
                        with _open_image(str(output)) as saved:
                            self.assertEqual(results[0][1], _pil_to_qimage(saved))
            thumbnails, errors = [], []
            loader = _ThumbLoader(str(source), max_size=4)
            loader.loaded.connect(lambda *args: thumbnails.append(args))
            loader.failed.connect(errors.append)
            loader.run()
            self.assertFalse(errors)
            self.assertEqual(len(thumbnails), 1)
            self.assertIn("page 1 of 3 only", thumbnails[0][1])

    def test_worker_tiff_success_preserves_all_pages_without_page_loss_note(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            worker = ConverterWorker([str(source)], "TIFF", ".tiff",
                                     output_dir=str(Path(directory) / "converted"),
                                     keep_metadata=True)
            results = []
            worker.file_done.connect(lambda *args: results.append(args))
            with mock.patch("src.core.worker._LARGE_BATCH_THRESHOLD", 100):
                worker.run()
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0][1])
            destination = results[0][2]
            self.assertTrue(Path(destination).is_file())
            self.assertNotIn("\n", destination)
            with Image.open(destination) as saved:
                self.assertEqual(saved.n_frames, 3)

    def test_frame_seek_after_normalization_with_distinct_orientations(self):
        from src.core.file_converter import _load_image_pixels, _normalize_image_orientation

        methods = {
            2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
            4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
            6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
            8: Image.Transpose.ROTATE_90,
        }
        specs = [((40, 24) if index % 2 == 0 else (24, 40),
                  "red", "blue", orientation)
                 for index, orientation in enumerate((6, 2, 8, 3, 5, 4, 7, 1))]

        def legacy_load_end(image):
            image._tile_orientation = image.tag_v2.get(274)
            method = methods.get(image._tile_orientation)
            if method is not None:
                image.im = image.im.transpose(method)
                image._size = image.im.size

        def unrotated_load_end(image):
            image._tile_orientation = image.tag_v2.get(274)
            image._size = image.im.size

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory, specs=specs)
            for decoder in (None, legacy_load_end, unrotated_load_end):
                with (mock.patch.object(TiffImagePlugin.TiffImageFile, "load_end", decoder)
                      if decoder else nullcontext()):
                    # Revisit pages in both directions, including rotations that
                    # make one page's display size equal the next page's raw size.
                    with Image.open(source) as pages:
                        for index in [*range(8), *range(7, -1, -1)]:
                            size, left, right, orientation = specs[index]
                            pages.seek(index)
                            self.assertEqual((pages.tag_v2[256], pages.tag_v2[257]), size)
                            self.assertEqual(pages._tile_size, size)
                            pages.getexif()
                            _load_image_pixels(pages)
                            _normalize_image_orientation(pages)
                            with Image.new("RGB", size, left) as raster:
                                raster.paste(right, (size[0] // 2, 0, *size))
                                with (raster.transpose(methods[orientation]) if orientation in methods
                                      else raster.copy()) as expected:
                                    with self.subTest(decoder=decoder, page=index):
                                        self.assertEqual(pages.size, expected.size)
                                        self.assertEqual(pages.tobytes(), expected.tobytes())
                                        self.assertIn(pages.getexif().get(274), (None, 1))
                    for resize in (None, (12, 20)):
                        output = Path(directory) / "distinct.tiff"
                        convert_file(str(source), str(output), "TIFF",
                                     keep_metadata=True, resize=resize)
                        with Image.open(output) as saved:
                            self.assertEqual(saved.n_frames, 8)
                            for index, (size, left, right, orientation) in enumerate(specs):
                                saved.seek(index)
                                saved.getexif()
                                saved.load()
                                with Image.new("RGB", size, left) as raster:
                                    raster.paste(right, (size[0] // 2, 0, *size))
                                    with (raster.transpose(methods[orientation]) if orientation in methods
                                          else raster.copy()) as oriented:
                                        with (oriented.resize(resize, Image.LANCZOS) if resize
                                              else oriented.copy()) as expected:
                                            with self.subTest(decoder=decoder, resize=resize, page=index):
                                                self.assertEqual(saved.size, expected.size)
                                                self.assertEqual(saved.tobytes(), expected.tobytes())
                                                self.assertIn(saved.getexif().get(274), (None, 1))
                                                self.assertEqual(saved.getexif()[305],
                                                                 f"page-{index + 1}-metadata")

    def test_all_pages_preserve_pixels_orientation_metadata_and_resize(self):
        first_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        later_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
        with tempfile.TemporaryDirectory(dir=".") as directory:
            profiles = (first_profile, later_profile, None)
            source, _ = self._make_source(directory, profiles)
            for keep in (False, True):
                for resize in (None, (12, 20)):
                    output = Path(directory) / "preserved.tiff"
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        convert_file(str(source), str(output), "TIFF", keep_metadata=keep, resize=resize)
                    self.assertFalse(caught)
                    with Image.open(source) as original, Image.open(output) as saved:
                        self.assertEqual(saved.n_frames, 3)
                        for index in range(3):
                            with self.subTest(keep=keep, resize=resize, page=index):
                                original.seek(index)
                                original.load()
                                saved.seek(index)
                                saved.load()
                                with (original.resize(resize, Image.LANCZOS) if resize
                                      else original.copy()) as reference:
                                    self.assertEqual(saved.size, reference.size)
                                    self.assertEqual(saved.mode, reference.mode)
                                    self.assertEqual(saved.tobytes(), reference.tobytes())
                                self.assertIn(saved.getexif().get(274), (None, 1))
                                if keep:
                                    self.assertEqual(saved.getexif()[305], f"page-{index + 1}-metadata")
                                    details = saved.getexif().get_ifd(34665)
                                    self.assertEqual((details[40962], details[40963]), saved.size)
                                    self.assertEqual(saved.tag_v2.get(34675), profiles[index])
                                    self.assertEqual(tuple(map(float, saved.info["dpi"])), (100 + index,) * 2)
                                    self.assertEqual((saved.tag_v2[256], saved.tag_v2[257]), saved.size)
                                else:
                                    self.assertNotIn(305, saved.getexif())
                                    self.assertNotIn(34675, saved.tag_v2)

    def test_mixed_native_modes_and_compressed_pages(self):
        specs = [("1", (11, 9), 1), ("L", (13, 7), 97),
                 ("RGB", (8, 15), (20, 80, 160)), ("RGBA", (14, 6), (40, 50, 60, 73)),
                 ("I;16", (7, 12), 12345), ("F", (9, 8), 1.25),
                 ("I", (8, 6), -123456),
                 ("LA", (6, 11), (120, 81)), ("CMYK", (10, 7), (15, 25, 35, 45)),
                 ("P", (12, 9), 2)]
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, output = Path(directory) / "mixed.tif", Path(directory) / "out.tif"
            with TiffImagePlugin.AppendingTiffWriter(str(source), True) as writer:
                for mode, size, color in specs:
                    with Image.new(mode, size, color) as page:
                        if mode == "P":
                            page.putpalette([value for value in range(256) for _ in range(3)])
                        page.save(writer, format="TIFF", compression="tiff_lzw")
                    writer.newFrame()
            for resize in (None, (5, 4)):
                convert_file(str(source), str(output), "TIFF", resize=resize)
                with Image.open(source) as original, Image.open(output) as saved:
                    self.assertEqual(saved.n_frames, len(specs))
                    for index, (mode, size, _) in enumerate(specs):
                        with self.subTest(page=index, resize=resize):
                            original.palette = None
                            saved.palette = None
                            original.seek(index)
                            original.load()
                            saved.seek(index)
                            saved.load()
                            with (original.resize(resize, Image.LANCZOS) if resize
                                  else original.copy()) as reference:
                                self.assertEqual(saved.mode, mode)
                                self.assertEqual(saved.size, resize or size)
                                self.assertEqual(saved.tobytes(), reference.tobytes())
                                if mode == "P":
                                    self.assertEqual(saved.getpalette(), reference.getpalette())

    def test_mixed_bigendian_pages_preserve_unsigned16_samples(self):
        samples = [0, 1, 255, 256, 12345, 32768, 65535, 4097] * 3
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, output = Path(directory) / "bigendian.tif", Path(directory) / "out.tif"
            for modes in (("I;16B", "RGB", "L"), ("RGB", "L", "I;16B")):
                with TiffImagePlugin.AppendingTiffWriter(str(source), True) as writer:
                    for index, mode in enumerate(modes):
                        with Image.new(mode, (8, 3)) as page:
                            if mode == "I;16B":
                                for position, value in enumerate(samples):
                                   page.putpixel((position % 8, position // 8), value)
                            else:
                                page.paste((20, 80, 160) if mode == "RGB" else 97,
                                          (0, 0, 8, 3))
                            # Construct genuine MM pages with Pillow's IFD
                            # serializer, including its strip-offset adjustment.
                            ifd = TiffImagePlugin.ImageFileDirectory_v2(prefix=b"MM")
                            ifd[256], ifd[257] = page.size
                            ifd[258] = (16,) if mode == "I;16B" else (
                                (8, 8, 8) if mode == "RGB" else (8,))
                            ifd[259], ifd[262] = 1, 2 if mode == "RGB" else 1
                            ifd[273], ifd[278], ifd[279] = (0,), page.height, len(page.tobytes())
                            if mode == "RGB":
                                ifd[277], ifd[284] = 3, 1
                            ifd[305] = f"bigendian-page-{index}"
                            ifd.save(writer)
                            writer.write(page.tobytes())
                        writer.newFrame()
                self.assertEqual(source.read_bytes()[:4], b"MM\x00\x2a")
                for keep in (False, True):
                    for resize in (None, (5, 4)):
                        convert_file(str(source), str(output), "TIFF",
                                    keep_metadata=keep, resize=resize)
                        self.assertEqual(output.read_bytes()[:4], b"II\x2a\x00")
                        with Image.open(source) as original, Image.open(output) as saved:
                            self.assertEqual(original.n_frames, len(modes))
                            self.assertEqual(saved.n_frames, len(modes))
                            for index, mode in enumerate(modes):
                                with self.subTest(modes=modes, keep=keep, resize=resize, page=index):
                                   original.seek(index)
                                   original.load()
                                   saved.seek(index)
                                   saved.load()
                                   self.assertEqual(original.mode, mode)
                                   self.assertEqual(saved.mode, "I;16" if mode == "I;16B" else mode)
                                   with (original.resize(resize, Image.LANCZOS) if resize
                                         else original.copy()) as reference:
                                       self.assertEqual(saved.size, reference.size)
                                       for y in range(reference.height):
                                           for x in range(reference.width):
                                               self.assertEqual(saved.getpixel((x, y)),
                                                                reference.getpixel((x, y)))
                                       if mode == "I;16B":
                                           self.assertEqual(saved.tag_v2[258], (16,))
                                           self.assertEqual(saved.tag_v2.get(339, (1,)), (1,))
                                           if resize is None:
                                               self.assertEqual(
                                                   [original.getpixel((x, y))
                                                    for y in range(3) for x in range(8)], samples)
                                   self.assertEqual(saved.getexif().get(305),
                                                    f"bigendian-page-{index}" if keep else None)

    def test_tiff_cancel_and_later_decode_failure_close_resources_and_rollback(self):
        from src.core.file_converter import _load_image_pixels
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            output = Path(directory) / "existing.tiff"
            original_bytes = b"existing destination"
            output.write_bytes(original_bytes)
            real_open, real_copy = Image.open, Image.Image.copy
            for failure in ("cancel", "decode", "save", "memory"):
                handles, pages = [], []

                def tracked_open(*args, **kwargs):
                    image = real_open(*args, **kwargs)
                    handles.append(image.fp)
                    return image

                def tracked_copy(image):
                    page = real_copy(image)
                    pages.append(page)
                    return page

                def decode(image):
                    if image.tell() == 1:
                        raise (MemoryError if failure == "memory" else OSError)("later page failed")
                    return _load_image_pixels(image)

                checks = iter((False, False, False, True))
                extra = {"cancel_check": lambda: next(checks)} if failure == "cancel" else {}
                patch = (mock.patch("src.core.file_converter._load_image_pixels", side_effect=decode)
                         if failure in ("decode", "memory") else
                         mock.patch.object(Image.Image, "save", side_effect=OSError("save failed"))
                         if failure == "save" else nullcontext())
                with self.subTest(failure=failure), \
                        mock.patch("src.core.file_converter.Image.open", side_effect=tracked_open), \
                        mock.patch.object(Image.Image, "copy", tracked_copy), patch:
                    with self.assertRaises((OSError, MemoryError)):
                        convert_file(str(source), str(output), "TIFF", **extra)
                self.assertTrue(all(handle.closed for handle in handles))
                for page in pages:
                    with self.assertRaises(ValueError):
                        page.getpixel((0, 0))
                self.assertEqual(output.read_bytes(), original_bytes)
                self.assertEqual(set(Path(directory).iterdir()), {source, output})

    def test_direct_tiff_overwrite_is_safe_and_preserves_all_pages(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            self.assertEqual(convert_file(str(source), str(source), "TIFF", resize=(10, 8)),
                             str(source))
            with Image.open(source) as pages:
                self.assertEqual(pages.n_frames, 3)
                for index in range(3):
                    pages.seek(index)
                    pages.load()
                    self.assertEqual(pages.size, (10, 8))
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_pages_are_closed_before_decoding_the_next_page(self):
        from src.core.file_converter import _load_image_pixels
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            pages = []
            real_copy = Image.Image.copy

            def copy(image):
                page = real_copy(image)
                pages.append(page)
                return page

            def decode(image):
                for page in pages:
                    with self.assertRaises(ValueError):
                        page.getpixel((0, 0))
                return _load_image_pixels(image)

            with mock.patch.object(Image.Image, "copy", copy), \
                    mock.patch("src.core.file_converter._load_image_pixels", side_effect=decode):
                convert_file(str(source), str(Path(directory) / "out.tiff"), "TIFF")
            self.assertEqual(len(pages), 3)

    def test_worker_tiff_abort_does_not_replace_existing_output(self):
        from src.core.file_converter import _load_image_pixels
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            for threshold in (1, 100):
                output_dir = Path(directory) / f"converted-{threshold}"
                output_dir.mkdir()
                destination = output_dir / source.name
                destination.write_bytes(b"existing destination")
                worker = ConverterWorker([str(source)], "TIFF", ".tiff", output_dir=str(output_dir))
                results = []
                worker.file_done.connect(lambda *args: results.append(args))

                def decode(image):
                    _load_image_pixels(image)
                    worker._abort = True

                with mock.patch("src.core.worker._LARGE_BATCH_THRESHOLD", threshold), \
                        mock.patch("src.core.file_converter._load_image_pixels", side_effect=decode):
                    worker.run()
                self.assertEqual(len(results), 1)
                self.assertFalse(results[0][1])
                self.assertIn("cancelled", results[0][2])
                self.assertEqual(destination.read_bytes(), b"existing destination")
                self.assertEqual(list(output_dir.iterdir()), [destination])

    def test_unknown_page_count_tiff_fails_instead_of_silent_page_loss(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            output = Path(directory) / "out.tiff"
            with mock.patch.object(TiffImagePlugin.TiffImageFile, "n_frames",
                                   new_callable=mock.PropertyMock,
                                   side_effect=SyntaxError("unsupported later IFD")):
                with self.assertRaisesRegex(SyntaxError, "unsupported later IFD"):
                    convert_file(str(source), str(output), "TIFF")
            self.assertFalse(output.exists())

    def test_page_loss_warning_as_error_closes_source_without_writing(self):
        import warnings
        from src.core.file_converter import tiff_first_page_note

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source, _ = self._make_source(directory)
            destination = Path(directory) / "converted.png"
            with _open_image(str(source)) as image:
                expected_warning = tiff_first_page_note(image.info["tiff_page_count"])
                with mock.patch("src.core.file_converter._open_image", return_value=image), \
                        mock.patch.object(image, "close", wraps=image.close) as close, \
                        warnings.catch_warnings():
                    warnings.simplefilter("error", UserWarning)
                    with self.assertRaises(UserWarning) as raised:
                        convert_file(str(source), str(destination), "PNG")
                    self.assertEqual(str(raised.exception), expected_warning)
                    close.assert_called_once_with()
                    with self.assertRaises(ValueError):
                        image.getpixel((0, 0))
                    self.assertFalse(destination.exists())

    def test_single_page_tiff_has_no_page_loss_warning(self):
        import warnings
        from src.ui.preview_pane import _ConverterPreviewLoader

        with tempfile.TemporaryDirectory(dir=".") as directory:
            source = Path(directory) / "single.tiff"
            with Image.new("RGB", (8, 6), "red") as image:
                image.save(source)
            results = []
            loader = _ConverterPreviewLoader(str(source), "TIFF", 90)
            loader.ready.connect(lambda *args: results.append(args))
            loader.run()
            self.assertEqual(len(results), 1)
            self.assertNotIn("page 1", results[0][2] + results[0][3])
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                convert_file(str(source), str(Path(directory) / "single.png"), "PNG")
            self.assertFalse(caught)


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

    def test_open_image_for_preview_drafts_and_reduces_large_images(self):
        class FakeImage:
            def __init__(self):
                self.format = "PNG"
                self.mode = "RGB"
                self.size = (8192, 8192)
                self.info = {}
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

            def getexif(self):
                return Image.Exif()

            def close(self):
                self.closed = True

        opened = FakeImage()
        with mock.patch("src.core.file_converter.Image.open", return_value=opened):
            preview = _open_image_for_preview("./huge.png", 512)
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
