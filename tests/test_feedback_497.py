"""#497: the screen says when Find finds nothing, when something was copied, when a log stream ended or lines are dropped,
and a failed Overview load can be retried."""
from types import SimpleNamespace as NS

from klotse.ui.detail_panel import DetailPanel
from klotse.ui.log_view import LogView
from klotse.ui.overview import CLUSTER_CARDS, OverviewView


def test_find_in_yaml_says_no_match_and_clears_when_found(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.editor.setPlainText("kind: ConfigMap\nmetadata:\n  name: web\n")
    panel.find_edit.setText("nothing-like-this")
    panel._find_next()
    assert not panel.find_note.isHidden()
    panel.find_edit.setText("web")
    panel._find_next()
    assert panel.find_note.isHidden()


def test_copying_an_identity_value_says_what_was_copied(qtbot):
    from PySide6.QtWidgets import QApplication
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel._identity_uid = "uid-123"
    panel._copy_id_value("uid-123", "UID")
    assert QApplication.clipboard().text() == "uid-123"
    assert not panel.copied_note.isHidden() and panel.copied_note.text() == "Copied UID"


def test_a_log_stream_that_ended_can_be_reconnected(qtbot, monkeypatch):
    starts = []
    monkeypatch.setattr(LogView, "start", lambda self: starts.append(True))  # no cluster: count the (re)starts only
    view = LogView(NS(), "shop", "web-1", ["app"])
    qtbot.addWidget(view)
    view.show()
    assert view.reconnect.isHidden()
    view._append("\n[stream ended]\n")
    assert not view.reconnect.isHidden()
    before = len(starts)
    view.reconnect.click()
    assert len(starts) == before + 1


def test_clearing_a_log_search_clears_the_no_match_notice(qtbot, monkeypatch):
    monkeypatch.setattr(LogView, "start", lambda self: None)
    view = LogView(NS(), "shop", "web-1", ["app"])
    qtbot.addWidget(view)
    view.show()
    view.text.setPlainText("ready\n")
    view.search.setText("missing")
    view.find_next()
    assert not view.no_match.isHidden()
    view.search.clear()
    assert view.no_match.isHidden()


def test_the_log_view_says_once_when_older_lines_start_to_fall_off(qtbot, monkeypatch):
    monkeypatch.setattr(LogView, "start", lambda self: None)
    view = LogView(NS(), "shop", "web-1", ["app"])
    qtbot.addWidget(view)
    view.show()
    view.text.setMaximumBlockCount(5)
    assert view.drop_note.isHidden()
    for i in range(8):
        view._append(f"line {i}\n")
    assert not view.drop_note.isHidden() and "newest 5" in view.drop_note.text()


def test_the_overview_load_failure_has_a_retry(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    asked = []
    view.retry_requested.connect(lambda: asked.append(True))
    view.set_error("ApiException: (503)\nReason: unavailable")
    view.loading_page.buttons["Retry"].click()
    assert asked == [True]


def test_the_existing_identity_copy_still_copies_the_object_reference(qtbot):
    # master already had _copy_identity() for the identity button; the new helper must not shadow it
    from PySide6.QtWidgets import QApplication
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel._identity_text = "shop/web"
    panel._copy_identity()
    assert QApplication.clipboard().text() == "shop/web"
