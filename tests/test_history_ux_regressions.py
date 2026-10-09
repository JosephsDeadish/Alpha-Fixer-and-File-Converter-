from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtGui import QMovie
from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

from src.core.settings_manager import SettingsManager
from src.ui.history_tab import HistoryTab, _AnimatedGifDelegate


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def history(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        manager = SettingsManager()
        widget = HistoryTab(manager)
        yield widget
        widget.close()
        sip.delete(widget)
        manager.sync()
        sip.delete(manager._qs)


def test_text_export_retains_full_filenames_and_visible_order(history, tmp_path):
    long_name = "画像_" + "long-source-" * 30 + ".png"
    first = ["2026-10-09", "PNG", "1", "1", "0", long_name]
    second = ["2026-10-08", "PNG", "1", "1", "0", "second.png"]
    tree = history._conv_tree
    tree.setSortingEnabled(False)
    tree.addTopLevelItem(QTreeWidgetItem(first))
    tree.addTopLevelItem(QTreeWidgetItem(second))
    hidden = QTreeWidgetItem(["2026-10-07", "PNG", "1", "1", "0", "hidden.png"])
    tree.addTopLevelItem(hidden)
    hidden.setHidden(True)
    path = tmp_path / "export.txt"
    with patch("src.ui.history_tab.QFileDialog.getSaveFileName",
               return_value=(str(path), "Text Files (*.txt)")):
        with patch("src.ui.history_tab.QMessageBox.information"):
            history._export_history()
    output = path.read_text(encoding="utf-8")
    assert long_name in output
    assert output.index(long_name) < output.index("second.png")
    assert "hidden.png" not in output


def test_directory_creation_failure_is_reported_without_crashing(history, tmp_path):
    path = tmp_path / "protected" / "export.json"
    with patch("src.ui.history_tab.QFileDialog.getSaveFileName",
               return_value=(str(path), "JSON Files (*.json)")):
        with patch("src.ui.history_tab.os.makedirs", side_effect=PermissionError("read only")):
            with patch("src.ui.history_tab.QMessageBox.warning") as warning:
                with patch("src.ui.history_tab.QMessageBox.information") as success:
                    history._export_history()
    warning.assert_called_once()
    assert "read only" in warning.call_args.args[-1]
    success.assert_not_called()


def test_gif_column_help_matches_optimize_and_loop_fields(history):
    header = history._gif_tree.headerItem()
    assert "optimization" in header.toolTip(5)
    assert "Loop count" in header.toolTip(6)


def test_animation_only_runs_while_history_tree_is_visible(app, tmp_path):
    path = tmp_path / "animation.gif"
    images = [Image.new("RGB", (12, 8), color) for color in ("red", "blue")]
    images[0].save(path, save_all=True, append_images=images[1:], duration=100, loop=0)
    tree = QTreeWidget()
    tree.setColumnCount(1)
    delegate = _AnimatedGifDelegate(tree, tree)
    tree.setItemDelegate(delegate)
    item = QTreeWidgetItem(["animated"])
    tree.addTopLevelItem(item)
    try:
        assert not delegate._tick_timer.isActive()
        delegate.set_gif_path(item, str(path))
        movie = delegate._movies[str(path)]
        assert movie.state() == QMovie.MovieState.NotRunning
        tree.show()
        app.processEvents()
        assert delegate._tick_timer.isActive()
        assert movie.state() == QMovie.MovieState.Running
        tree.hide()
        app.processEvents()
        assert not delegate._tick_timer.isActive()
        assert movie.state() == QMovie.MovieState.Paused
        tree.show()
        app.processEvents()
        assert delegate._tick_timer.isActive()
        assert movie.state() == QMovie.MovieState.Running
        delegate.clear_movies()
        assert not delegate._tick_timer.isActive()
        assert not delegate._movies
    finally:
        tree.close()
        sip.delete(tree)


def test_invalid_gif_keeps_static_fallback_without_idle_timer(app, tmp_path):
    path = tmp_path / "corrupt.gif"
    path.write_bytes(b"not a gif")
    tree = QTreeWidget()
    delegate = _AnimatedGifDelegate(tree, tree)
    try:
        delegate.set_gif_path(QTreeWidgetItem(tree, ["bad"]), str(path))
        assert delegate._movies == {}
        assert not delegate._tick_timer.isActive()
    finally:
        sip.delete(tree)
