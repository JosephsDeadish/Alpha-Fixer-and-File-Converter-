"""Strict native-runtime collection shared by both PyInstaller release specs."""

from __future__ import annotations

import ctypes.util
from functools import lru_cache
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


LINUX_QT_LIBS = (
    "libEGL.so.1", "libGL.so.1", "libGLESv2.so.2", "libpulse.so.0",
    # PyInstaller treats these as host graphics-stack libraries, but the
    # portable bundle also needs their generic loaders on a minimal target OS.
    "libGLX.so.0", "libGLdispatch.so.0", "libxcb.so.1",
    "libdrm.so.2", "libwayland-client.so.0", "libwayland-cursor.so.0",
    "libwayland-egl.so.1", "libxcb-dri3.so.0",
    "libxcb-keysyms.so.1", "libxcb-image.so.0", "libxcb-icccm.so.4",
    "libxcb-xkb.so.1", "libxcb-shape.so.0", "libxcb-cursor.so.0",
    "libxcb-render-util.so.0", "libxkbcommon-x11.so.0", "libxcb-util.so.1",
)


def resolve_library(name: str, roots=()) -> Path:
    """Locate a library while preserving its loader-visible SONAME filename."""
    path = Path(name)
    if path.is_file():
        return path.absolute()
    directories = [Path(p) for p in roots]
    for variable in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        directories.extend(Path(p) for p in os.environ.get(variable, "").split(os.pathsep) if p)
    if sys.platform == "linux":
        result = subprocess.run(["/sbin/ldconfig", "-p"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if "=>" in line and line.strip().split()[0] == path.name:
                candidate = Path(line.rsplit("=>", 1)[1].strip())
                if candidate.is_file():
                    return candidate.absolute()
        directories.extend([Path("/usr/lib"), Path("/lib")])
    elif sys.platform == "darwin":
        directories.extend([Path("/opt/homebrew/lib"), Path("/usr/local/lib")])
    for directory in directories:
        candidate = directory / path.name
        if candidate.is_file():
            return candidate.absolute()
    raise RuntimeError(f"Mandatory runtime library not found: {name}")


def find_executable(name: str) -> Path:
    variables = (
        ("IMAGEIO_FFMPEG_EXE",) if name == "ffmpeg"
        else ("ALPHA_FIXER_FFPROBE_EXE", "IMAGEIO_FFPROBE_EXE", "FFPROBE_EXE")
    )
    for variable in variables:
        if os.environ.get(variable):
            candidate = Path(os.environ[variable])
            if not candidate.is_file():
                raise RuntimeError(f"{variable} does not name a file: {candidate}")
            return candidate.resolve()
    if sys.platform == "win32":
        # Chocolatey's bin/*.exe are launch shims, not redistributable FFmpeg.
        chocolatey = Path(os.environ.get("ChocolateyInstall", r"C:\ProgramData\chocolatey"))
        for package in sorted((chocolatey / "lib").glob("ffmpeg*")):
            candidates = sorted(package.glob(f"tools/**/{name}.exe"))
            if candidates:
                return candidates[0].resolve()
    candidate = shutil.which(name)
    if candidate:
        return Path(candidate).resolve()
    if name == "ffmpeg":
        import imageio_ffmpeg
        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"Mandatory runtime executable not found: {name}; install FFmpeg first")


def validate_executable(executable: Path, name: str):
    result = subprocess.run(
        [str(executable), "-version"], capture_output=True, text=True, timeout=30,
    )
    if result.returncode or f"{name} version" not in result.stdout.lower():
        raise RuntimeError(f"Mandatory {name} runtime is not executable: {executable}")


def relocate_libtool_descriptor(descriptor: Path, output: Path) -> Path:
    """Prevent IM's libltdl loader from preferring absolute build-host paths."""
    generated = output / "coders" / descriptor.name
    generated.parent.mkdir(parents=True, exist_ok=True)
    text = descriptor.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^libdir=.*$", "libdir=''", text)
    text = re.sub(r"(?m)^installed=.*$", "installed=no", text)
    text = re.sub(r"(?m)^dependency_libs=.*$", "dependency_libs=''", text)
    generated.write_text(text, encoding="utf-8")
    return generated


def vtracer_native_extension() -> Path:
    try:
        import vtracer.vtracer as native
    except ImportError as exc:
        raise RuntimeError("Mandatory SVG vectorization runtime vtracer is missing") from exc
    extension = Path(native.__file__)
    if not extension.is_file() or not callable(getattr(native, "convert_image_to_svg_py", None)):
        raise RuntimeError("Mandatory vtracer native extension is incomplete")
    return extension


def imagemagick_layout(library: Path, home: Path | None = None):
    """Find IM's versioned config/module trees without scanning unrelated files."""
    roots = []
    if home:
        roots.append(home)
    roots.extend([library.parent, library.parent.parent])
    if sys.platform == "linux":
        roots.extend([Path("/etc"), Path("/usr/share"), Path("/usr/lib")])
    configs, coders = [], []
    for root in dict.fromkeys(roots):
        if not root.is_dir():
            continue
        # Installed Unix layout, Homebrew formula prefix, and Windows portable IM.
        configs.extend(root.glob("ImageMagick*/**/*.xml"))
        configs.extend(root.glob("etc/ImageMagick*/**/*.xml"))
        configs.extend(root.glob("share/ImageMagick*/**/*.xml"))
        coders.extend(root.glob("ImageMagick*/modules*/coders/*"))
        coders.extend(root.glob("lib/ImageMagick*/modules*/coders/*"))
        if root == home:
            configs.extend(root.glob("*.xml"))
            coders.extend(root.glob("modules/coders/*"))
            # Windows installers keep dynamically loaded IM_MOD DLLs beside CORE DLLs.
            coders.extend(root.glob("IM_MOD_*.dll"))
    configs = sorted({p.resolve() for p in configs if p.is_file()})
    coders = sorted({
        p.resolve() for p in coders
        if p.is_file() and (".so" in p.name or p.suffix.lower() in (".dll", ".dylib", ".la"))
    })
    if not configs:
        raise RuntimeError("Mandatory ImageMagick configuration resources were not found")
    if not any(p.suffix != ".la" for p in coders):
        raise RuntimeError("Mandatory ImageMagick coder modules were not found")
    return configs, coders


@lru_cache(maxsize=1)
def _wheel_native_licenses():
    result = {}
    for distribution in importlib.metadata.distributions():
        files = distribution.files or ()
        licenses = [
            Path(distribution.locate_file(p)) for p in files
            if any(token in str(p).lower() for token in ("license", "copying", "copyright", "notice"))
            and Path(distribution.locate_file(p)).is_file()
        ]
        for relative in files:
            name = str(relative).lower()
            if ".so" in name or name.endswith((".dll", ".dylib", ".exe")):
                result[Path(distribution.locate_file(relative)).resolve()] = licenses
    return result


@lru_cache(maxsize=None)
def _native_license_files(source: Path):
    roots = [source.parent, source.parent.parent]
    home = os.environ.get("MAGICK_HOME") or os.environ.get("IMAGEMAGICK_HOME")
    if home:
        roots.append(Path(home))
    found = []
    for root in dict.fromkeys(roots):
        for pattern in (
            "*LICENSE*", "*license*", "*COPYING*", "*copyright*", "licenses/**/*",
            "share/licenses/**/*", "share/doc/**/LICENSE*", "share/doc/**/COPYING*",
            "share/*/LICENSE*", "share/*/COPYING*",
        ):
            if pattern.startswith("share/") and root in (
                Path("/usr"), Path("/usr/local"), Path("/opt/homebrew"),
            ):
                continue
            found.extend(p for p in root.glob(pattern) if p.is_file())
    # Native Qt wheel licenses live in dist-info, not beside Qt6/lib/*.so.
    found.extend(_wheel_native_licenses().get(source.resolve(), ()))
    if sys.platform == "linux" and shutil.which("dpkg-query"):
        # Debian copyright files are the authoritative installed native license source.
        result = subprocess.run(
            ["dpkg-query", "-S", str(source), str(source).replace("/usr/lib/", "/lib/")],
            capture_output=True, text=True,
        )
        packages = {line.split(": ", 1)[0].split(":")[0] for line in result.stdout.splitlines() if ": " in line}
        for package in packages:
            candidate = Path("/usr/share/doc") / package / "copyright"
            if candidate.is_file():
                found.append(candidate)
    for notice in list(found):
        text = notice.read_text(encoding="utf-8", errors="replace")
        for reference in re.findall(r"/usr/share/common-licenses/[A-Za-z0-9_.+-]+", text):
            license_file = Path(reference)
            if license_file.is_file():
                found.append(license_file)
    return sorted(set(found))


def write_notices(binaries, output: Path):
    """Archive actual installed license files and metadata; never guess licenses."""
    output.mkdir(parents=True, exist_ok=True)
    metadata = []
    datas = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "unknown")
        entry = {
            "name": name, "version": distribution.version,
            "license": distribution.metadata.get("License"),
            "license_expression": distribution.metadata.get("License-Expression"),
            "license_files": [],
        }
        for relative in distribution.files or ():
            if any(token in str(relative).lower() for token in ("license", "copying", "copyright", "notice")):
                source = Path(distribution.locate_file(relative))
                if source.is_file():
                    destination = f"licenses/python/{name}/{Path(relative).parent}"
                    datas.append((str(source), destination))
                    entry["license_files"].append(f"{destination}/{source.name}")
        metadata.append(entry)
    native = []
    for source, _ in binaries:
        source = Path(source)
        licenses = _native_license_files(source)
        destination = f"licenses/native/{source.name}"
        for index, license_file in enumerate(licenses):
            # A separate directory prevents identically named copyright files colliding.
            datas.append((str(license_file), f"{destination}/{index}"))
        native.append({
            "file": source.name, "source": str(source),
            "license_sources": [str(p) for p in licenses],
        })
        # Homebrew records upstream license declarations in installed formula
        # receipts even when the formula does not install the full license text.
        receipts = list(source.parent.parent.glob(".brew/*.rb"))
        for receipt in receipts:
            datas.append((str(receipt), f"licenses/native/{source.name}/package-metadata"))
        if receipts:
            native[-1]["package_metadata_sources"] = [str(p) for p in receipts]
    notice = output / "THIRD_PARTY_NOTICES.json"
    notice.write_text(json.dumps({"python": metadata, "native": native}, indent=2), encoding="utf-8")
    datas.append((str(notice), "licenses"))
    return datas


def collect_release_dependencies(output: Path = Path("build/runtime-resources")):
    from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata
    from wand.api import library

    # Wand can import successfully while resolving IM from a build-host-only location.
    home_text = os.environ.get("MAGICK_HOME") or os.environ.get("IMAGEMAGICK_HOME")
    home = Path(home_text) if home_text else None
    roots = [home, home / "bin", home / "lib"] if home else []
    wand_library = resolve_library(str(library._name), roots)
    configs, coders = imagemagick_layout(wand_library, home)
    binaries = [(str(wand_library), ".")]
    if sys.platform == "darwin":
        # Wand searches MAGICK_HOME/lib for unversioned Homebrew dylib names.
        for pattern in ("libMagickWand*.dylib", "libMagickCore*.dylib"):
            binaries.extend((str(p), "lib") for p in wand_library.parent.glob(pattern) if p.is_file())
    # MagickCore is loaded dynamically on some hosts; keep the sibling IM libraries.
    patterns = ("*MagickCore*.so*", "*MagickCore*.dylib", "CORE_RL_*.dll", "libMagick*.dll")
    for pattern in patterns:
        binaries.extend((str(p.resolve()), ".") for p in wand_library.parent.glob(pattern) if p.is_file())
    if sys.platform == "linux":
        # Wand probes MAGICK_HOME/lib for unversioned .so names before using
        # host ldconfig/compilers. Production installs often provide only .so.N.
        magick_libraries = [wand_library] + [
            Path(source) for source, _ in binaries if "MagickCore" in Path(source).name
        ]
        staging = output / "imagemagick-lib"
        staging.mkdir(parents=True, exist_ok=True)
        for source in magick_libraries:
            name = source.name.partition(".so")[0] + ".so"
            target = staging / name
            shutil.copy2(source, target)
            binaries.append((str(target), "lib"))
    if sys.platform == "linux":
        binaries.extend((str(resolve_library(name)), ".") for name in LINUX_QT_LIBS)
    for name in ("ffmpeg", "ffprobe"):
        executable = find_executable(name)
        validate_executable(executable, name)
        binaries.append((str(executable), "imageio_ffmpeg/binaries"))
    binaries.extend((str(p), "imagemagick/modules/coders") for p in coders if p.suffix != ".la")
    datas = []
    for descriptor in (p for p in coders if p.suffix == ".la"):
        # libltdl otherwise prefers the absolute build-host libdir. An uninstalled
        # descriptor falls back to its own directory after trying .libs.
        generated = relocate_libtool_descriptor(descriptor, output)
        datas.append((str(generated), "imagemagick/modules/coders"))
    config_paths = set()
    for config in configs:
        tree = next((p for p in config.parents if p.name.startswith("ImageMagick")), config.parent)
        destination = Path("imagemagick/config") / config.relative_to(tree).parent
        datas.append((str(config), destination.as_posix()))
        config_paths.add(destination.as_posix())
    # Qt's PyInstaller hooks collect the libraries, platform/image plugins and
    # translations used by imported modules. Copying the entire wheel also adds
    # unrelated QML/WebEngine plugins whose dependencies are not part of this app.
    datas.extend(copy_metadata("PyQt6"))
    for package in ("wand", "imageio", "imageio_ffmpeg", "vtracer"):
        datas.extend(collect_data_files(
            package, excludes=["binaries/ffmpeg*"] if package == "imageio_ffmpeg" else None,
        ))
        datas.extend(copy_metadata(package))
        binaries.extend(collect_dynamic_libs(package))
    binaries.append((str(vtracer_native_extension()), "vtracer"))
    # Qt hooks collect plugin dependencies; fail early if its mandatory SVG library is absent.
    from PyQt6 import QtCore, QtGui, QtSvg, QtWidgets  # noqa: F401
    binaries = list(dict.fromkeys(binaries))
    datas.extend(write_notices(binaries, output))
    manifest = output / "runtime-layout.json"
    manifest.write_text(json.dumps({
        "imagemagick_library": wand_library.name,
        "imagemagick_home": ".",
        "imagemagick_config": sorted(config_paths),
        "imagemagick_coders": "imagemagick/modules/coders",
        "ffmpeg": next(Path(p).name for p, d in binaries if d == "imageio_ffmpeg/binaries" and "ffmpeg" in Path(p).name),
        "ffprobe": next(Path(p).name for p, d in binaries if d == "imageio_ffmpeg/binaries" and "ffprobe" in Path(p).name),
    }), encoding="utf-8")
    datas.append((str(manifest), "."))
    return datas, binaries, [
        "wand", "wand.api", "wand.image", "wand.resource", "PyQt6.QtSvg",
        "vtracer", "vtracer.vtracer",
    ]


def add_analysis_notices(analysis):
    """Include licenses for the native dependency closure resolved by PyInstaller."""
    notices = write_notices(
        [(source, str(Path(destination).parent)) for destination, source, _ in analysis.binaries],
        Path("build/runtime-resources"),
    )
    existing = {destination for destination, _, _ in analysis.datas}
    for source, directory in notices:
        destination = str(Path(directory) / Path(source).name)
        if destination not in existing:
            analysis.datas.append((destination, source, "DATA"))
            existing.add(destination)


if __name__ == "__main__":
    datas, binaries, _ = collect_release_dependencies()
    print(f"Mandatory release runtimes ready: {len(binaries)} binaries, {len(datas)} resources")
