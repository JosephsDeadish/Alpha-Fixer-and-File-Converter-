import ctypes
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bundle_dependencies as bundle
from scripts import runtime_hook_dependencies as hook


def make_file(path, content="runtime"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_missing_ffprobe_is_fatal(monkeypatch):
    for variable in ("ALPHA_FIXER_FFPROBE_EXE", "IMAGEIO_FFPROBE_EXE", "FFPROBE_EXE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(bundle.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Mandatory runtime executable.*ffprobe"):
        bundle.find_executable("ffprobe")


def test_invalid_configured_executable_is_fatal(monkeypatch):
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "missing-runtime/ffmpeg")
    with pytest.raises(RuntimeError, match="does not name a file"):
        bundle.find_executable("ffmpeg")


def test_explicit_ffprobe_wins_over_host_path(tmp_path, monkeypatch):
    executable = make_file(tmp_path / "ffprobe")
    monkeypatch.setenv("ALPHA_FIXER_FFPROBE_EXE", str(executable))
    monkeypatch.setattr(bundle.shutil, "which", lambda name: "wrong")
    assert bundle.find_executable("ffprobe") == executable.resolve()


def test_library_resolution_matches_exact_soname(tmp_path, monkeypatch):
    library = make_file(tmp_path / "libMagickWand.so.7")
    monkeypatch.setattr(bundle.sys, "platform", "linux")
    monkeypatch.setattr(bundle.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=(
        f"libMagickWand.so.70 (libc6) => {tmp_path / 'unrelated'}\n"
        f"libMagickWand.so.7 (libc6) => {library}\n"
    )))
    assert bundle.resolve_library(library.name) == library.resolve()


def test_missing_library_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("DYLD_LIBRARY_PATH", raising=False)
    with pytest.raises(RuntimeError, match="Mandatory runtime library"):
        bundle.resolve_library("not-a-runtime.dll", [tmp_path])


def test_imagemagick_layout_keeps_config_and_coders_only(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.sys, "platform", "darwin")
    library = make_file(tmp_path / "lib/libMagickWand.dylib")
    config = make_file(tmp_path / "etc/ImageMagick-7/policy.xml")
    coder = make_file(tmp_path / "lib/ImageMagick-7/modules-Q16HDRI/coders/dds.so")
    make_file(tmp_path / "lib/unrelated.so")
    descriptor = make_file(coder.with_suffix(".la"))
    configs, coders = bundle.imagemagick_layout(library, tmp_path)
    assert configs == [config.resolve()]
    assert coders == sorted([coder.resolve(), descriptor.resolve()])


@pytest.mark.parametrize("missing", ["config", "coder"])
def test_incomplete_imagemagick_is_fatal(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(bundle.sys, "platform", "win32")
    library = make_file(tmp_path / "CORE_RL_MagickWand_.dll")
    if missing != "config":
        make_file(tmp_path / "policy.xml")
    if missing != "coder":
        make_file(tmp_path / "IM_MOD_RL_DDS_.dll")
    with pytest.raises(RuntimeError, match="Mandatory ImageMagick"):
        bundle.imagemagick_layout(library, tmp_path)


def test_windows_installer_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.sys, "platform", "win32")
    library = make_file(tmp_path / "CORE_RL_MagickWand_.dll")
    config = make_file(tmp_path / "policy.xml")
    coder = make_file(tmp_path / "IM_MOD_RL_DDS_.dll")
    assert bundle.imagemagick_layout(library, tmp_path) == ([config], [coder])


def test_runtime_hook_overrides_build_host_paths(tmp_path, monkeypatch):
    make_file(tmp_path / "runtime-layout.json", json.dumps({
        "imagemagick_library": "libMagickWand.so.7",
        "imagemagick_home": ".",
        "imagemagick_config": ["imagemagick/config", "imagemagick/config/config-Q16"],
        "imagemagick_coders": "imagemagick/modules/coders",
        "ffmpeg": "ffmpeg-bundled", "ffprobe": "ffprobe",
    }))
    monkeypatch.setenv("MAGICK_HOME", "/host")
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "/host/ffmpeg")
    calls = []
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: calls.append((a, k)))
    hook.configure_bundle(tmp_path)
    assert os.environ["MAGICK_HOME"] == str(tmp_path)
    assert os.environ["MAGICK_CONFIGURE_PATH"] == os.pathsep.join([
        str(tmp_path / "imagemagick/config"), str(tmp_path / "imagemagick/config/config-Q16"),
    ])
    assert os.environ["MAGICK_CODER_MODULE_PATH"] == str(tmp_path / "imagemagick/modules/coders")
    assert os.environ["IMAGEIO_FFMPEG_EXE"] == str(tmp_path / "imageio_ffmpeg/binaries/ffmpeg-bundled")
    assert os.environ["ALPHA_FIXER_FFPROBE_EXE"] == str(tmp_path / "imageio_ffmpeg/binaries/ffprobe")
    assert calls[0][0][0] == str(tmp_path / "libMagickWand.so.7")


def test_notices_copy_real_installed_license(tmp_path, monkeypatch):
    source = make_file(tmp_path / "package/demo.dist-info/licenses/LICENSE", "actual upstream license")
    distribution = SimpleNamespace(
        metadata={"Name": "demo", "License-Expression": "MIT"},
        version="1.2.3", files=[Path("demo.dist-info/licenses/LICENSE")],
        locate_file=lambda relative: tmp_path / "package" / relative,
    )
    monkeypatch.setattr(bundle.importlib.metadata, "distributions", lambda: [distribution])
    datas = bundle.write_notices([], tmp_path / "generated")
    assert any(p == str(source) for p, _ in datas)
    notice = json.loads((tmp_path / "generated/THIRD_PARTY_NOTICES.json").read_text())
    assert notice["python"][0]["license_expression"] == "MIT"
    assert notice["python"][0]["license"] is None


def test_both_specs_use_strict_shared_collector_and_hook():
    root = Path(__file__).resolve().parents[1]
    for name in ("alpha_fixer.spec", "alpha_fixer_onefile.spec"):
        text = (root / name).read_text()
        assert "collect_release_dependencies()" in text
        assert 'runtime_hooks=["scripts/runtime_hook_dependencies.py"]' in text
        assert "_optional_" not in text
