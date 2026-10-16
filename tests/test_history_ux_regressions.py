from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QMovie
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from src.core.settings_manager import SettingsManager
from src.ui.history_tab import HistoryTab, _AnimatedGifDelegate
from src.ui.theme_engine import PRESET_THEMES, build_stylesheet


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


@pytest.mark.parametrize("sequence,key,modifiers,expected", [
    ("Ctrl+Tab", Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier, 1),
    ("Ctrl+Shift+Tab", Qt.Key.Key_Backtab,
     Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, 4),
])
@pytest.mark.parametrize("focus_target", ["_conv_search", "_conv_tree", "tab_bar"])
def test_saved_global_tab_shortcut_wins_and_local_navigation_still_works(
        history, app, sequence, key, modifiers, expected, focus_target):
    from src.ui.main_window import MainWindow

    host = QWidget()
    host._settings = history._settings
    host._shortcut_map = {}
    activations = []
    host._open_settings = lambda: None
    host._show_shortcuts = lambda: activations.append("help")
    layout = QVBoxLayout(host)
    layout.addWidget(history)
    settings = history._settings
    settings.set_shortcut_binding("help", sequence, "F1")
    settings.sync()
    reloaded = SettingsManager()
    host._settings = reloaded
    MainWindow._setup_keyboard_shortcuts(host)
    try:
        host.show()
        host.activateWindow()
        app.processEvents()
        target = (history._sub_tabs.tabBar() if focus_target == "tab_bar"
                  else getattr(history, focus_target))
        target.setFocus()
        app.processEvents()
        assert app.focusWidget() is target
        QTest.keyClick(target, key, modifiers)
        app.processEvents()
        assert activations == ["help"]
        assert history._sub_tabs.currentIndex() == 0
        host._shortcut_map["help"]["sc"].setEnabled(False)
        QTest.keyClick(target, key, modifiers)
        app.processEvents()
        assert activations == ["help"]
        assert history._sub_tabs.currentIndex() == expected
    finally:
        history.setParent(None)
        host.close()
        sip.delete(host)
        sip.delete(reloaded._qs)


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


def test_alpha_history_search_copy_uses_consistent_wording(history):
    assert history._alpha_search.accessibleName() == "Filter Alpha & RGBA Adjuster history"
    assert "Alpha & RGBA Adjuster" in history._sub_tabs.tabToolTip(1)
    assert "Alpha & RGBA Adjuster" in history._capability_lbl.toolTip()


def test_history_overview_labels_have_screen_reader_copy(history):
    assert history._capability_lbl.accessibleName() == "History capability summary"
    assert "browse, filter, preview, export" in history._capability_lbl.accessibleDescription().lower()
    assert history._session_status_lbl.accessibleName() == "History status summary"
    assert "rows are visible" in history._session_status_lbl.accessibleDescription().lower()
    assert history._next_step_lbl.accessibleName() == "History next step guidance"
    assert "next action" in history._next_step_lbl.accessibleDescription().lower()
    assert "create history entries" in history._next_step_lbl.text().lower()
    assert history._sub_tabs.accessibleName() == "History category tabs"
    assert "Converter" in history._sub_tabs.accessibleDescription()
    for summary, name in [
        (history._conv_summary, "Converter history summary"),
        (history._alpha_summary, "Alpha and RGBA history summary"),
        (history._sel_summary, "Alpha Painter history summary"),
        (history._gif_summary, "GIF Builder history summary"),
        (history._vid_summary, "Video Builder history summary"),
    ]:
        assert summary.accessibleName() == name
        assert summary.accessibleDescription()


def test_history_clear_button_copy_and_status_label_are_more_actionable(history):
    assert "confirm the prompt" in history._btn_clear.accessibleDescription().lower()
    history._vid_tree.clear()
    history._settings.add_video_builder_history({
        "timestamp": "2026-10-09T12:00:00",
        "output": "export.mp4",
        "format": "mp4",
        "clip_count": 1,
        "success": 1,
        "errors": 0,
        "recovery": "remux ×1",
        "notes": "temporary ffmpeg remux fallback active",
        "files": ["clip.iso"],
    })
    history.refresh()
    assert history._vid_tree.topLevelItem(0).text(13) == "Recovered"


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


@pytest.mark.parametrize("theme", ["Panda Dark", "Panda Light"])
@pytest.mark.parametrize("size", [(480, 400), (640, 480)])
@pytest.mark.parametrize("initial_pixels", [13, 24, 32])
def test_compact_history_keeps_guidance_rows_and_keyboard_actions_available(
        history, app, theme, size, initial_pixels):
    tools = ("converter", "alpha", "selective_alpha", "gif_builder", "video_builder")
    trees = (history._conv_tree, history._alpha_tree, history._sel_tree,
             history._gif_tree, history._vid_tree)
    searches = (history._conv_search, history._alpha_search, history._sel_search,
                history._gif_search, history._vid_search)
    record = {"timestamp": "2026-10-09T12:00:00", "format": "PNG", "mode": "manual",
              "file_count": 1, "frame_count": 1, "clip_count": 1,
              "success": 1, "errors": 0, "files": ["first.png"]}
    for tool in tools:
        getattr(history._settings, f"add_{tool}_history")(record)
    history.refresh()
    before = [getattr(history._settings, f"get_{tool}_history")() for tool in tools]
    history.resize(*size)
    history.show()

    def settle():
        for _ in range(20):
            app.processEvents()

    def focused_in_view(widget):
        assert app.focusWidget() is widget
        point = widget.mapTo(history._scroll.viewport(), QPoint(0, 0))
        rect = widget.rect().translated(point)
        viewport = history._scroll.viewport().rect()
        assert viewport.intersects(rect)
        if widget not in trees:
            assert viewport.contains(rect)

    for pixels in (initial_pixels, 32, 13, 24):
        history.setStyleSheet(build_stylesheet(PRESET_THEMES[theme])
                             + f"\nQWidget {{ font-size: {pixels}px; }}")
        settle()
        assert (history.width(), history.height()) == size
        assert history._scroll.horizontalScrollBar().maximum() == 0
        for index, (tree, search) in enumerate(zip(trees, searches)):
            history._sub_tabs.setCurrentIndex(index)
            settle()
            assert tree.viewport().height() >= 2 * max(32, tree.fontMetrics().height() + 8)
            assert search.height() >= search.minimumSizeHint().height()
            for label in history.findChildren(QLabel):
                if label.isVisible() and label.wordWrap():
                    assert label.height() >= label.heightForWidth(label.width())
            history._sub_tabs.tabBar().setFocus()
            for _ in range(5):
                QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
                settle()
                if app.focusWidget() is search:
                    break
            focused_in_view(search)
            QTest.keyClicks(search, "file:first.png")
            assert not tree.topLevelItem(0).isHidden()
            QTest.keyClick(search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClicks(search, "file:missing.png")
            settle()
            assert tree.topLevelItem(0).isHidden()
            assert history._session_status_lbl.height() >= history._session_status_lbl.heightForWidth(
                history._session_status_lbl.width())
            assert history._next_step_lbl.height() >= history._next_step_lbl.heightForWidth(
                history._next_step_lbl.width())
            QTest.keyClick(search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(search, Qt.Key.Key_Backspace)
            settle()
            assert not tree.topLevelItem(0).isHidden()
            QTest.keyClick(search, Qt.Key.Key_Tab)
            settle()
            focused_in_view(tree)
            QTest.keyClick(tree, Qt.Key.Key_Down)
            assert tree.currentItem() is tree.topLevelItem(0)
            QTest.keyClick(tree, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
            settle()
            assert history._sub_tabs.currentIndex() == (index + 1) % 5
            QTest.keyClick(app.focusWidget(), Qt.Key.Key_Backtab,
                           Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            settle()
            assert history._sub_tabs.currentIndex() == index
            history._sub_tabs.tabBar().setFocus()
            if index < 4:
                QTest.keyClick(history._sub_tabs.tabBar(), Qt.Key.Key_Right)
                assert history._sub_tabs.currentIndex() == index + 1
                QTest.keyClick(history._sub_tabs.tabBar(), Qt.Key.Key_Left)
                assert history._sub_tabs.currentIndex() == index
            search.clear()

        # Tab traversal, rather than programmatic focus, must reach both actions.
        reached = set()
        for _ in range(30):
            focus = app.focusWidget()
            if focus in (history._btn_export, history._btn_clear):
                focused_in_view(focus)
                reached.add(focus)
                if focus is history._btn_export:
                    with patch("src.ui.history_tab.QFileDialog.getSaveFileName",
                               return_value=("", "")) as dialog:
                        QTest.keyClick(focus, Qt.Key.Key_Space)
                        dialog.assert_called_once()
                else:
                    with patch("src.ui.history_tab.QMessageBox.question",
                               return_value=QMessageBox.StandardButton.No) as confirm:
                        QTest.keyClick(focus, Qt.Key.Key_Space)
                        confirm.assert_called_once()
            if len(reached) == 2:
                break
            QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
            settle()
        assert reached == {history._btn_export, history._btn_clear}
    assert [getattr(history._settings, f"get_{tool}_history")() for tool in tools] == before
