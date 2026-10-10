import csv
import datetime
import json
from html.parser import HTMLParser
from unittest.mock import patch

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QColor, QMovie, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.settings_manager import SettingsManager
from src.ui.history_tab import HistoryTab, _HistoryItem, _load_thumb
from src.ui.theme_engine import PRESET_THEMES, build_stylesheet


TOOLS = ("converter", "alpha", "selective_alpha", "gif_builder", "video_builder")
TREES = ("_conv_tree", "_alpha_tree", "_sel_tree", "_gif_tree", "_vid_tree")
SEARCHES = ("_conv_search", "_alpha_search", "_sel_search", "_gif_search", "_vid_search")


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def history(app, tmp_path):
    with patch("src.core.settings_manager._settings_ini_path",
               return_value=str(tmp_path / "settings.ini")):
        settings = SettingsManager()
        widget = HistoryTab(settings)
        yield widget
        widget.close()
        sip.delete(widget)
        settings.sync()
        sip.delete(settings._qs)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def seed(settings, tool, entries):
    settings._qs.setValue(f"{tool}_history", json.dumps(entries, ensure_ascii=False))
    settings.sync()
    assert getattr(settings, f"get_{tool}_history")() == entries


def entry(index, tmp_path):
    name = f"画像_{index:05d}_" + "long-name-" * 25 + "<&>.png"
    timestamp = datetime.datetime(2026, 10, 1) + datetime.timedelta(seconds=index)
    return {
        "timestamp": timestamp.isoformat(),
        "format": "PNG" if index % 2 == 0 else "WEBP",
        "mode": "manual",
        "file_count": 12, "frame_count": 12, "clip_count": 12,
        "success": 2, "errors": index % 2,
        "files": [name, "second,source.png"],
        "first_file": str(tmp_path / "missing-source.png"),
        "source": str(tmp_path / "missing-source.png"),
        "output": str(tmp_path / "2026" / f"missing-{index:05d}.gif"),
        "fps": "24", "delay": "100 ms", "loop": "∞", "optimize": "off",
        "resize": "original", "largest_frame": "640×480",
        "alpha_summary": "1 source", "sources": "PNG images",
        "canvas": "640×480", "filter": "none", "audio": "off",
        "recovery": "direct only", "streams": "auto/default",
        "clips": "one: direct; two: direct", "notes": "Unicode 画像 <&> notes",
        "stopped": index % 2 == 0, "not_processed": 10 if index % 2 == 0 else 0,
    }


def visible(tree):
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
            if not tree.topLevelItem(i).isHidden()]


@pytest.mark.parametrize("count", [10, 50, 5000])
def test_five_real_history_views_at_retention_limits(history, app, tmp_path, count):
    settings = history._settings
    settings.set("history_max_entries", count)
    records = [entry(i, tmp_path) for i in range(count)]
    for tool in TOOLS:
        seed(settings, tool, records)
        getattr(settings, f"add_{tool}_history")(entry(count, tmp_path))
        saved = getattr(settings, f"get_{tool}_history")()
        assert len(saved) == count
        assert saved[0]["timestamp"] == entry(count, tmp_path)["timestamp"]
    history.refresh()
    history.resize(1000, 800)
    history.show()
    for tab, (name, search_name) in enumerate(zip(TREES, SEARCHES)):
        history._sub_tabs.setCurrentIndex(tab)
        app.processEvents()
        tree = getattr(history, name)
        search = getattr(history, search_name)
        assert tree.topLevelItemCount() == count
        timestamps = [item.data(0, _HistoryItem._SORT_ROLE) for item in visible(tree)]
        assert timestamps == sorted(timestamps, reverse=True)
        search.setText("errors:0 ok:2")
        assert len(visible(tree)) == count // 2 + 1
        search.setText('file:"画像_00002_"')
        assert len(visible(tree)) == 1
        search.setText("file:never-present")
        assert visible(tree) == []
        assert f"0/{count} shown" in history.get_status_bar_text()
        search.clear()
        assert len(visible(tree)) == count
        assert all(item.icon(0).isNull() for item in visible(tree))
    assert history._gif_anim_delegate._movies == {}
    assert not history._gif_anim_delegate._tick_timer.isActive()


@pytest.mark.parametrize("tab", range(5))
def test_status_numeric_and_path_filters_use_exact_fields(history, tmp_path, tab):
    records = [entry(i, tmp_path) for i in range(6)]
    records[0]["output"] = r"C:\exports\2026\画像 target.gif"
    records[1]["output"] = "/archive/2026/unrelated.gif"
    seed(history._settings, TOOLS[tab], records)
    history.refresh()
    tree = getattr(history, TREES[tab])
    search = getattr(history, SEARCHES[tab])
    search.setText("ok:2 errors:0")
    assert len(visible(tree)) == 3
    search.setText("success:20")
    assert visible(tree) == []
    search.setText("success:>=2 errors:<1")
    assert len(visible(tree)) == 3
    search.setText("status:stopped" if tab < 2 else "status:ok")
    assert len(visible(tree)) == 3
    if tab >= 3:
        search.setText("fps:2")
        assert visible(tree) == []
        search.setText("fps:24|30")
        assert len(visible(tree)) == 6
        search.setText("output:2026/target.gif")
        assert visible(tree) == []
        search.setText(r'output:"C:\exports\2026\画像 target.gif"')
        assert len(visible(tree)) == 1
    search.setText("file:*.png")
    assert len(visible(tree)) == 6
    search.setText("errors:0")
    history.refresh()
    assert search.text() == "errors:0"
    assert len(visible(tree)) == 3


@pytest.mark.parametrize("theme_name", ["Panda Light", "Panda Dark"])
def test_error_rows_inherit_themed_foreground_without_losing_diagnostics(
        history, app, tmp_path, theme_name):
    for tool in TOOLS:
        seed(history._settings, tool, [entry(1, tmp_path)])
    history.refresh()
    history.show()
    names = [theme_name, "Panda Dark" if theme_name == "Panda Light" else "Panda Light"]
    for name in names:
        theme = PRESET_THEMES[name]
        history.setStyleSheet(build_stylesheet(theme))
        for tab, tree_name in enumerate(TREES):
            history._sub_tabs.setCurrentIndex(tab)
            app.processEvents()
            tree = getattr(history, tree_name)
            item = tree.topLevelItem(0)
            assert tree.palette().color(QPalette.ColorRole.Text) == QColor(theme["text"])
            for col in range(tree.columnCount()):
                assert item.data(col, Qt.ItemDataRole.ForegroundRole) is None
                assert item.foreground(col).style() == Qt.BrushStyle.NoBrush
                assert "Errors: 1" in item.toolTip(col)
            fields = item.data(0, _HistoryItem._FILTER_FIELDS_ROLE)
            assert fields["errors"] == "1"
            assert fields["status"] == "issues"
            if tab >= 3:
                assert item.text(13) == "Issues"
            elif tab < 2:
                assert item.data(0, _HistoryItem._BATCH_STATUS_ROLE) == ["Issues", "0"]


@pytest.mark.parametrize("tab", range(5))
def test_numeric_filename_substrings_do_not_change_numeric_count_equality(
        history, tmp_path, tab):
    records = [entry(i, tmp_path) for i in range(2)]
    records[0].update(files=["photo_20261009.png"], success=2)
    records[1].update(files=["photo_20121009.png"], success=12)
    seed(history._settings, TOOLS[tab], records)
    history.refresh()
    tree = getattr(history, TREES[tab])
    search = getattr(history, SEARCHES[tab])
    for query in ("file:2026", 'file:"2026"', "file:20261009"):
        search.setText(query)
        assert len(visible(tree)) == 1
        assert "photo_20261009.png" in visible(tree)[0].text(tree.columnCount() - 1)
    search.setText("file:12")
    assert len(visible(tree)) == 1
    assert "photo_20121009.png" in visible(tree)[0].text(tree.columnCount() - 1)
    for query in ("ok:2", "success:2", 'success:"2"'):
        search.setText(query)
        assert len(visible(tree)) == 1
        fields = visible(tree)[0].data(0, _HistoryItem._FILTER_FIELDS_ROLE)
        assert fields["success"] == "2"
    search.setText("success:>=2")
    assert len(visible(tree)) == 2


@pytest.mark.parametrize("field", ["output", "path", "time"])
def test_year_substrings_in_output_paths_and_timestamps(history, tmp_path, field):
    records = [entry(i, tmp_path) for i in range(2)]
    records[0].update(timestamp="2026-10-09T12:00:00",
                      output=str(tmp_path / "archive_20261009" / "photo.gif"))
    records[1].update(timestamp="2012-10-09T12:00:00",
                      output=str(tmp_path / "archive_20121009" / "photo.gif"))
    seed(history._settings, "gif_builder", records)
    history.refresh()
    history._gif_search.setText(f"{field}:2026")
    assert len(visible(history._gif_tree)) == 1
    assert visible(history._gif_tree)[0].text(0).startswith("2026")


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = ""

    def handle_data(self, text):
        if self.cell is not None:
            self.cell += text

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr":
            self.rows.append(self.row)
            self.row = None


@pytest.mark.parametrize("tab", range(5))
@pytest.mark.parametrize("extension,selected_filter", [
    ("txt", "Text Files (*.txt)"), ("csv", "CSV Files (*.csv)"),
    ("json", "JSON Files (*.json)"), ("html", "HTML Files (*.html *.htm)"),
])
def test_reopened_exports_align_filtered_sorted_view(
        history, tmp_path, tab, extension, selected_filter):
    seed(history._settings, TOOLS[tab], [entry(i, tmp_path) for i in range(6)])
    history.refresh()
    history._sub_tabs.setCurrentIndex(tab)
    tree = getattr(history, TREES[tab])
    getattr(history, SEARCHES[tab]).setText("errors:0 ok:2")
    tree.sortItems(0, Qt.SortOrder.AscendingOrder)
    items = visible(tree)
    assert len(items) == 3
    rows = [[item.text(col) for col in range(tree.columnCount())] for item in items]
    if tab < 2:
        for row, item in zip(rows, items):
            row.extend(item.data(0, _HistoryItem._BATCH_STATUS_ROLE))
    path = tmp_path / f"history.{extension}"
    with patch("src.ui.history_tab.QFileDialog.getSaveFileName",
               return_value=(str(path), selected_filter)), \
            patch.object(QMessageBox, "information") as success:
        history._export_history()
    success.assert_called_once()
    text = path.read_text(encoding="utf-8")
    if extension == "csv":
        with path.open(encoding="utf-8", newline="") as stream:
            exported = list(csv.reader(stream))
        assert exported[1:] == rows
        assert all(len(row) == len(exported[0]) for row in exported)
    elif extension == "json":
        exported = json.loads(text)
        assert [list(row.values()) for row in exported] == rows
        if tab < 2:
            assert all(row["Status"] == "Stopped" and row["Not processed"] == "10"
                       for row in exported)
    elif extension == "html":
        reader = TableReader()
        reader.feed(text)
        assert reader.rows[1:] == rows
        assert all(len(row) == len(reader.rows[0]) for row in reader.rows)
        assert "&lt;&amp;&gt;" in text
    else:
        lines = text.splitlines()
        assert len(lines) == len(rows) + 2
        # Full cell content and ordering must survive the padded text table.
        for line, row in zip(lines[2:], rows):
            offset = 0
            for value in row:
                found = line.find(value, offset)
                assert found >= offset
                offset = found + len(value)
        assert all("画像_00001_" not in line for line in lines)
    assert not list(tmp_path.glob(".alpha_fixer_save_*"))


def animation(tmp_path):
    path = tmp_path / "output.gif"
    with Image.new("RGB", (12, 8), "red") as first, \
            Image.new("RGB", (12, 8), "blue") as second:
        first.save(path, save_all=True, append_images=[second], duration=100, loop=0)
    return path


def test_refresh_filter_hide_and_close_clean_thumbnail_resources(history, app, tmp_path):
    path = animation(tmp_path)
    records = [entry(0, tmp_path), entry(1, tmp_path)]
    for record in records:
        record["output"] = str(path)
        record["first_file"] = str(path)
    for tool in TOOLS:
        seed(history._settings, tool, records)
    history.refresh()
    history.show()
    delegate = history._gif_anim_delegate
    for _ in range(4):
        history._sub_tabs.setCurrentIndex(3)
        app.processEvents()
        assert len(delegate._movies) == 1
        movie = delegate._movies[str(path)]
        assert movie.state() == QMovie.MovieState.Running
        assert delegate._tick_timer.isActive()
        tree = history._gif_tree
        tree.setCurrentItem(tree.topLevelItem(0))
        history._gif_search.setText("file:absent")
        assert not delegate._tick_timer.isActive()
        assert movie.state() == QMovie.MovieState.Paused
        history.hide()
        history.show()
        app.processEvents()
        assert not delegate._tick_timer.isActive()
        history._gif_search.clear()
        assert movie.state() == QMovie.MovieState.Running
        history._sub_tabs.setCurrentIndex(0)
        app.processEvents()
        assert not delegate._tick_timer.isActive()
        assert movie.state() == QMovie.MovieState.Paused
        history.refresh()
        assert not tree.selectedItems()
        assert tree.currentItem() is None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert sip.isdeleted(movie)
        assert len(delegate.findChildren(QMovie)) == 1
        history.close()
        app.processEvents()
        assert not delegate._tick_timer.isActive()
        history.show()
    path.unlink()
    history.refresh()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert delegate._movies == {}
    assert delegate.findChildren(QMovie) == []
    assert not delegate._tick_timer.isActive()
    for name in TREES:
        assert all(item.icon(0).isNull() for item in visible(getattr(history, name)))


def test_corrupt_gif_uses_real_source_fallback(history, tmp_path):
    corrupt = tmp_path / "bad.gif"
    corrupt.write_bytes(b"invalid GIF")
    source = tmp_path / "source.png"
    with Image.new("RGB", (12, 8), "red") as image:
        image.save(source)
    record = entry(0, tmp_path)
    record.update(output=str(corrupt), first_file=str(source))
    seed(history._settings, "gif_builder", [record])
    history.refresh()
    item = history._gif_tree.topLevelItem(0)
    assert not item.icon(0).isNull()
    assert "first input thumbnail shown" in item.toolTip(0)
    assert "animated GIF thumbnail shown" not in item.toolTip(0)
    assert history._gif_anim_delegate._movies == {}
    assert not history._gif_anim_delegate._tick_timer.isActive()


def test_failed_pillow_thumbnail_conversion_closes_input(app, tmp_path):
    path = tmp_path / "source.tga"
    with Image.new("RGBA", (12, 8), "red") as image:
        image.save(path)
    with Image.open(path) as image:
        with patch("src.ui.history_tab.QPixmap", return_value=QPixmap()), \
                patch("PIL.Image.open", return_value=image), \
                patch.object(image, "convert", side_effect=ValueError("decode failed")):
            assert _load_thumb(str(path)).isNull()
        assert image.fp is None
