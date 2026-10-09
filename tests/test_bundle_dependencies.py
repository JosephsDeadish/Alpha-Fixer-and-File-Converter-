import ctypes
import json
import os
from pathlib import Path
import sys
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


def test_nonfunctional_native_executable_is_fatal(tmp_path, monkeypatch):
    executable = make_file(tmp_path / "ffprobe")
    monkeypatch.setattr(bundle.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="not ffprobe",
    ))
    with pytest.raises(RuntimeError, match="not executable"):
        bundle.validate_executable(executable, "ffprobe")


def test_windows_chocolatey_runtime_not_launcher_shim(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.sys, "platform", "win32")
    for variable in ("ALPHA_FIXER_FFPROBE_EXE", "IMAGEIO_FFPROBE_EXE", "FFPROBE_EXE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("ChocolateyInstall", str(tmp_path))
    native = make_file(tmp_path / "lib/ffmpeg/tools/ffmpeg/bin/ffprobe.exe")
    shim = make_file(tmp_path / "bin/ffprobe.exe")
    monkeypatch.setattr(bundle.shutil, "which", lambda name: str(shim))
    assert bundle.find_executable("ffprobe") == native.resolve()


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


def test_libtool_descriptor_does_not_load_from_build_host(tmp_path):
    descriptor = make_file(tmp_path / "installed/png.la", (
        "dlname='png.so'\ninstalled=yes\nlibdir='/build-host/lib/coders'\n"
        "dependency_libs='/build-host/lib/libpng.so'\n"
    ))
    generated = bundle.relocate_libtool_descriptor(descriptor, tmp_path / "generated")
    text = generated.read_text()
    assert "dlname='png.so'" in text
    assert "installed=no" in text
    assert "libdir=''" in text
    assert "dependency_libs=''" in text
    assert "/build-host" not in text
    assert "installed=yes" in descriptor.read_text()


def test_collector_fails_when_linux_qt_native_library_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", SimpleNamespace(
        collect_data_files=lambda *a, **k: [], collect_dynamic_libs=lambda *a: [],
        copy_metadata=lambda *a: [],
    ))
    library = make_file(tmp_path / "libMagickWand.so.7")
    monkeypatch.setitem(sys.modules, "wand.api", SimpleNamespace(library=SimpleNamespace(_name=str(library))))
    monkeypatch.setattr(bundle, "imagemagick_layout", lambda *a: ([tmp_path / "policy.xml"], [tmp_path / "png.so"]))

    def resolve(name, roots=()):
        if name == str(library):
            return library
        raise RuntimeError(f"Mandatory runtime library not found: {name}")

    monkeypatch.setattr(bundle, "resolve_library", resolve)
    with pytest.raises(RuntimeError, match="libEGL.so.1"):
        bundle.collect_release_dependencies(tmp_path / "generated")


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


def test_runtime_hook_retains_windows_dll_directory(tmp_path, monkeypatch):
    make_file(tmp_path / "runtime-layout.json", json.dumps({
        "imagemagick_library": "CORE_RL_MagickWand_.dll", "imagemagick_home": ".",
        "imagemagick_config": ["imagemagick/config"],
        "imagemagick_coders": "imagemagick/modules/coders",
        "ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe",
    }))
    monkeypatch.setattr(hook.sys, "platform", "win32")
    monkeypatch.setattr(hook, "_dll_directory", None, raising=False)
    handle = object()
    calls = []

    def add_directory(path):
        calls.append(("directory", path))
        return handle

    monkeypatch.setattr(os, "add_dll_directory", add_directory, raising=False)
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: calls.append(("library", a[0])))
    hook.configure_bundle(tmp_path)
    assert hook._dll_directory is handle
    assert calls[0] == ("directory", str(tmp_path))


def test_collector_preserves_versioned_config_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.sys, "platform", "darwin")
    monkeypatch.setenv("MAGICK_HOME", str(tmp_path))
    library = make_file(tmp_path / "lib/libMagickWand-7.Q16HDRI.dylib")
    config = make_file(tmp_path / "etc/ImageMagick-7/nested/policy.xml")
    coder = make_file(tmp_path / "lib/ImageMagick-7/modules-Q16HDRI/coders/dds.so")
    monkeypatch.setitem(sys.modules, "wand.api", SimpleNamespace(library=SimpleNamespace(_name=str(library))))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", SimpleNamespace(
        collect_data_files=lambda *a, **k: [], collect_dynamic_libs=lambda *a: [],
        copy_metadata=lambda *a: [],
    ))
    monkeypatch.setitem(sys.modules, "PyQt6", SimpleNamespace(QtCore=None, QtGui=None, QtSvg=None, QtWidgets=None))
    monkeypatch.setattr(bundle, "find_executable", lambda name: tmp_path / name)
    monkeypatch.setattr(bundle, "validate_executable", lambda *a: None)
    extension = make_file(tmp_path / "vtracer/vtracer.native.so")
    monkeypatch.setattr(bundle, "vtracer_native_extension", lambda: extension)

    def notices(binaries, output):
        output.mkdir(parents=True, exist_ok=True)
        return []

    monkeypatch.setattr(bundle, "write_notices", notices)
    output = tmp_path / "generated"
    datas, binaries, hidden = bundle.collect_release_dependencies(output)
    assert (str(config), "imagemagick/config/nested") in datas
    assert (str(coder), "imagemagick/modules/coders") in binaries
    assert (str(library), "lib") in binaries
    layout = json.loads((output / "runtime-layout.json").read_text())
    assert layout["imagemagick_config"] == ["imagemagick/config/nested"]
    assert layout["ffprobe"] == "ffprobe"
    assert "wand.api" in hidden
    assert "vtracer.vtracer" in hidden
    assert (str(extension), "vtracer") in binaries


def test_missing_vtracer_native_runtime_is_fatal(monkeypatch):
    monkeypatch.setitem(sys.modules, "vtracer.vtracer", None)
    with pytest.raises(RuntimeError, match="Mandatory SVG vectorization runtime"):
        bundle.vtracer_native_extension()


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


def test_native_copyright_includes_referenced_full_license(tmp_path, monkeypatch):
    common = Path("/usr/share/common-licenses/GPL-3")
    if not common.is_file():
        pytest.skip("Debian common-license source is not installed on this host")
    source = make_file(tmp_path / "runtime/ffmpeg")
    copyright_file = make_file(tmp_path / "runtime/copyright", f"Installed license: {common}")
    monkeypatch.setattr(bundle.sys, "platform", "win32")
    monkeypatch.setattr(bundle, "_wheel_native_licenses", lambda: {})
    bundle._native_license_files.cache_clear()
    licenses = bundle._native_license_files(source)
    assert copyright_file in licenses
    assert common in licenses
    bundle._native_license_files.cache_clear()


def test_both_specs_use_strict_shared_collector_and_hook():
    root = Path(__file__).resolve().parents[1]
    for name in ("alpha_fixer.spec", "alpha_fixer_onefile.spec"):
        text = (root / name).read_text()
        assert "collect_release_dependencies()" in text
        assert 'runtime_hooks=["scripts/runtime_hook_dependencies.py"]' in text
        assert '"unittest"' not in text
        assert "_optional_" not in text
