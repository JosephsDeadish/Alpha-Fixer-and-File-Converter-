"""Pytest bootstrap for headless Qt imports."""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _load_existing_egl() -> bool:
    try:
        ctypes.CDLL("libEGL.so.1", mode=ctypes.RTLD_GLOBAL)
        return True
    except OSError:
        return False


def _build_egl_shim(target: Path) -> bool:
    source = target.with_suffix(".c")
    source.write_text(
        """
#include <stdint.h>
#define EGL_TRUE 1
void *eglGetDisplay(void *display_id) { return (void*)1; }
void *eglGetCurrentDisplay(void) { return (void*)1; }
void *eglGetCurrentContext(void) { return (void*)1; }
void *eglGetCurrentSurface(int readdraw) { return (void*)1; }
int eglInitialize(void *dpy, int *major, int *minor) { if (major) *major=1; if (minor) *minor=5; return EGL_TRUE; }
int eglTerminate(void *dpy) { return EGL_TRUE; }
int eglBindAPI(unsigned int api) { return EGL_TRUE; }
int eglReleaseThread(void) { return EGL_TRUE; }
int eglWaitClient(void) { return EGL_TRUE; }
int eglWaitGL(void) { return EGL_TRUE; }
int eglSwapInterval(void *dpy, int interval) { return EGL_TRUE; }
int eglChooseConfig(void *dpy, const int *attrib_list, void *configs, int config_size, int *num_config) { if (num_config) *num_config = 1; return EGL_TRUE; }
int eglGetConfigs(void *dpy, void *configs, int config_size, int *num_config) { if (num_config) *num_config = 1; return EGL_TRUE; }
int eglGetConfigAttrib(void *dpy, void *config, int attribute, int *value) { if (value) *value = 0; return EGL_TRUE; }
void *eglCreateContext(void *dpy, void *config, void *share_context, const int *attrib_list) { return (void*)1; }
int eglDestroyContext(void *dpy, void *ctx) { return EGL_TRUE; }
void *eglCreateWindowSurface(void *dpy, void *config, void *native_window, const int *attrib_list) { return (void*)1; }
void *eglCreatePbufferSurface(void *dpy, void *config, const int *attrib_list) { return (void*)1; }
void *eglCreatePixmapSurface(void *dpy, void *config, void *pixmap, const int *attrib_list) { return (void*)1; }
int eglDestroySurface(void *dpy, void *surface) { return EGL_TRUE; }
int eglQuerySurface(void *dpy, void *surface, int attribute, int *value) { if (value) *value = 0; return EGL_TRUE; }
int eglQueryContext(void *dpy, void *ctx, int attribute, int *value) { if (value) *value = 0; return EGL_TRUE; }
int eglSurfaceAttrib(void *dpy, void *surface, int attribute, int value) { return EGL_TRUE; }
int eglBindTexImage(void *dpy, void *surface, int buffer) { return EGL_TRUE; }
int eglReleaseTexImage(void *dpy, void *surface, int buffer) { return EGL_TRUE; }
int eglMakeCurrent(void *dpy, void *draw, void *read, void *ctx) { return EGL_TRUE; }
int eglSwapBuffers(void *dpy, void *surface) { return EGL_TRUE; }
void *eglGetProcAddress(const char *procname) { return 0; }
const char *eglQueryString(void *dpy, int name) { return ""; }
int eglGetError(void) { return 0x3000; }
void *eglGetPlatformDisplay(unsigned int platform, void *native_display, const int *attrib_list) { return (void*)1; }
void *eglGetPlatformDisplayEXT(unsigned int platform, void *native_display, const int *attrib_list) { return (void*)1; }
        """.strip()
    )
    try:
        subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                "-Wl,-soname,libEGL.so.1",
                "-o",
                str(target),
                str(source),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    finally:
        try:
            source.unlink()
        except OSError:
            pass
    return target.exists()


def _ensure_qt_egl_runtime() -> None:
    if os.name != "posix" or _load_existing_egl():
        return
    cache_dir = Path(tempfile.gettempdir()) / "alpha_fixer_qt_shims"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    shim = cache_dir / "libEGL.so.1"
    if not shim.exists() and not _build_egl_shim(shim):
        return
    try:
        ctypes.CDLL(str(shim), mode=ctypes.RTLD_GLOBAL)
    except OSError:
        return


_ensure_qt_egl_runtime()
