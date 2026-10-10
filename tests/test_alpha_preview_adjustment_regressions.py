from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.core.alpha_processor import apply_alpha_preset, apply_manual_alpha
from src.core.presets import AlphaPreset, PresetManager
from src.core.settings_manager import SettingsManager
from src.core.worker import AlphaWorker
from src.ui.alpha_tool import AlphaFixerTab, _AlphaPreviewLoader


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pixels():
    return np.array([[[10, 240, 100, a] for a in (0, 64, 128, 255)]],
                    dtype=np.uint8)


def qimage_pixels(image):
    return np.array([[image.pixelColor(x, y).getRgb()
                      for x in range(image.width())]
                     for y in range(image.height())], dtype=np.uint8)


@pytest.mark.parametrize("use_preset", [False, True])
@pytest.mark.parametrize("threshold", [0, 128, 255])
def test_binary_cut_boundaries_preview_stats_and_export(app, tmp_path, use_preset, threshold):
    source = tmp_path / "source.png"
    with Image.fromarray(pixels(), "RGBA") as image:
        image.save(source)
    params = {"binary_cut": True, "threshold": threshold}
    preset = AlphaPreset("cut", "", **params) if use_preset else None
    manual = {} if use_preset else params.copy()
    manual["rgb"] = {"r": -30, "g": 30, "b": 7, "a": -10}
    expected = pixels()
    expected[:, :, :3] = (0, 255, 107)
    expected[:, :, 3] = np.where(pixels()[:, :, 3] >= threshold, 245, 0)

    loader = _AlphaPreviewLoader(str(source), preset=preset, manual_params=manual)
    previews, stats, failures = [], [], []
    loader.preview_ready.connect(lambda before, after: previews.append((before, after)))
    loader.stats_ready.connect(lambda before, after: stats.append((before, after)))
    loader.failed.connect(failures.append)
    loader.run()
    assert not failures
    assert len(previews) == len(stats) == 1
    np.testing.assert_array_equal(qimage_pixels(previews[0][0]), pixels())
    np.testing.assert_array_equal(qimage_pixels(previews[0][1]), expected)
    assert stats[0][0] == {"min": 0, "max": 255, "mean": 111.75, "percent_nonzero": 75.0}
    alpha = expected[:, :, 3]
    assert stats[0][1] == {
        "min": int(alpha.min()), "max": int(alpha.max()),
        "mean": float(alpha.mean()),
        "percent_nonzero": float(np.count_nonzero(alpha) / alpha.size * 100),
    }

    worker = AlphaWorker([str(source)], preset=preset, manual_params=manual,
                         output_dir=str(tmp_path / "export"), suffix="_fixed")
    results = []
    worker.file_done.connect(lambda path, ok, message: results.append(ok))
    worker.run()
    assert results == [True]
    with Image.open(tmp_path / "export" / "source_fixed.png") as exported:
        np.testing.assert_array_equal(np.array(exported), expected)
    sip.delete(loader)
    sip.delete(worker)


@pytest.mark.parametrize("use_preset", [False, True])
def test_zero_binary_threshold_uniform_and_varied_alpha(use_preset):
    for alphas in ((0,), (64,), (255,), (0, 64, 128, 255)):
        data = np.array([[[20, 40, 60, a] for a in alphas]], dtype=np.uint8)
        with Image.fromarray(data, "RGBA") as image:
            if use_preset:
                result = apply_alpha_preset(image, AlphaPreset("cut", "", binary_cut=True))
            else:
                result = apply_manual_alpha(image, binary_cut=True)
            with result:
                expected = data.copy()
                expected[:, :, 3] = 255
                np.testing.assert_array_equal(np.array(result), expected)


@pytest.mark.parametrize("use_preset", [False, True])
@pytest.mark.parametrize("params, expected_alpha", [
    ({"clamp_min": 128, "clamp_max": 128}, [128, 128, 128, 128]),
    ({"clamp_min": 128, "clamp_max": 0}, [0, 32, 64, 128]),
    ({"invert": True}, [255, 191, 127, 0]),
    ({"threshold": 128, "clamp_max": 100}, [0, 100, 128, 255]),
])
def test_manual_and_preset_range_preview_export_parity(app, tmp_path, use_preset,
                                                     params, expected_alpha):
    source = tmp_path / "range.png"
    with Image.fromarray(pixels(), "RGBA") as image:
        image.save(source)
    preset = AlphaPreset("range", "", **params) if use_preset else None
    manual = None if use_preset else params
    expected = pixels()
    expected[:, :, 3] = expected_alpha
    loader = _AlphaPreviewLoader(str(source), preset=preset, manual_params=manual)
    previews, stats, failures = [], [], []
    loader.preview_ready.connect(lambda before, after: previews.append(after))
    loader.stats_ready.connect(lambda before, after: stats.append(after))
    loader.failed.connect(failures.append)
    loader.run()
    assert not failures
    assert len(previews) == len(stats) == 1
    np.testing.assert_array_equal(qimage_pixels(previews[0]), expected)
    with Image.fromarray(expected, "RGBA") as image:
        assert stats[0] == _AlphaPreviewLoader._alpha_stats(image)
    worker = AlphaWorker([str(source)], preset=preset, manual_params=manual,
                         output_dir=str(tmp_path / "export"), suffix="_fixed")
    results = []
    worker.file_done.connect(lambda path, ok, message: results.append(ok))
    worker.run()
    assert results == [True]
    with Image.open(tmp_path / "export" / "range_fixed.png") as exported:
        np.testing.assert_array_equal(np.array(exported), expected)
    sip.delete(loader)
    sip.delete(worker)


@pytest.fixture
def alpha(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        manager = SettingsManager()
        widget = AlphaFixerTab(PresetManager(manager), manager)
        yield widget, manager
        widget._preview_debounce.stop()
        loaders = tuple(widget._retired_preview_loaders) + (
            (widget._preview_loader,) if widget._preview_loader else ()
        )
        for loader in loaders:
            loader.stop()
            assert loader.wait(2000)
        widget._stop_preview_loader()
        app.processEvents()
        widget.close()
        sip.delete(widget)
        manager.sync()
        sip.delete(manager._qs)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait_for_preview(widget, app, expected):
    for _ in range(200):
        app.processEvents()
        raw = widget._compare.after_image()
        if raw is not None and np.array_equal(qimage_pixels(raw), expected):
            if widget._after_stats_lbl.text():
                return
        QTest.qWait(10)
    pytest.fail("live preview did not reach the expected pixels/statistics")


def test_live_binary_and_rgba_controls(alpha, app, tmp_path):
    widget, _ = alpha
    source = tmp_path / "live.png"
    with Image.fromarray(pixels(), "RGBA") as image:
        image.save(source)
    widget._file_list.addItem(str(source))
    widget._file_list.setCurrentRow(0)
    wait_for_preview(widget, app, pixels())
    widget._binary_cut_check.setChecked(True)
    expected = pixels()
    expected[:, :, 3] = 255
    wait_for_preview(widget, app, expected)
    assert "mean<br><b>255.0</b>" in widget._after_stats_lbl.text()
    widget._alpha_delta_spin.setValue(-10)
    widget._red_spin.setValue(-30)
    widget._apply_rgb_check.setChecked(True)
    expected[:, :, 0] = 0
    expected[:, :, 3] = 245
    wait_for_preview(widget, app, expected)
    assert "mean<br><b>245.0</b>" in widget._after_stats_lbl.text()
    widget._apply_rgb_check.setChecked(False)
    expected[:, :, 0] = 10
    expected[:, :, 3] = 255
    wait_for_preview(widget, app, expected)
    widget._binary_cut_check.setChecked(False)
    wait_for_preview(widget, app, pixels())


def test_saved_preview_helpers_and_batch_preferences(alpha, app):
    widget, manager = alpha
    widget._alpha_vis_check.setChecked(True)
    widget._atlas_detect_check.setChecked(True)
    widget._recursive_check.setChecked(False)
    widget._suffix_edit.setText("_preview_checked")
    manager.sync()
    reopened_manager = SettingsManager()
    reopened = AlphaFixerTab(PresetManager(reopened_manager), reopened_manager)
    try:
        assert reopened._alpha_vis_check.isChecked()
        assert reopened._atlas_detect_check.isChecked()
        assert not reopened._recursive_check.isChecked()
        assert reopened._suffix_edit.text() == "_preview_checked"
    finally:
        reopened.close()
        sip.delete(reopened)
        sip.delete(reopened_manager._qs)
        app.processEvents()
