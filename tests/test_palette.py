from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from klotse.ui import palette
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.palette import Entry, fuzzy_score, rank
from klotse.ui.palette_dialog import PaletteDialog
from test_app import FakeService, open_active


def labels(entries):
    return [e.label for e in entries]


ENTRIES = [Entry("Go to", "Pods", "Workloads"), Entry("Go to", "Persistent Volumes", "Storage"), Entry("Action", "Health check", "View menu"),
           Entry("Action", "Hide managed fields", "View menu"), Entry("Namespace", "kube-system"), Entry("Namespace", "shop"),
           Entry("Cluster", "prod-eu", "connect")]


def test_fuzzy_matching_needs_the_letters_in_order_and_prefers_words_and_runs():
    assert fuzzy_score("hc", "Health check") is not None and fuzzy_score("xz", "Health check") is None
    assert fuzzy_score("pods", "Pods") > fuzzy_score("pods", "Persistent volumes of the other disks")
    assert fuzzy_score("heal", "Health check") > fuzzy_score("hlth", "Health check")
    assert fuzzy_score("", "anything") == 0


def test_every_word_has_to_match_and_the_best_match_comes_first():
    assert labels(rank(ENTRIES, "pods"))[0] == "Pods"
    assert labels(rank(ENTRIES, "hc"))[0] == "Health check"
    assert labels(rank(ENTRIES, "check view")) == ["Health check"]  # one word in the label, one in the detail
    assert rank(ENTRIES, "zzzz") == []
    assert labels(rank(ENTRIES, "shop"))[0] == "shop"
    assert labels(rank(ENTRIES, "storage")) == ["Persistent Volumes"]  # the detail counts too
    assert rank(ENTRIES, "e", limit=2).__len__() == 2


def test_an_empty_query_lists_everything_by_category():
    ordered = rank(ENTRIES, "")
    assert [e.category for e in ordered] == ["Go to", "Go to", "Namespace", "Namespace", "Action", "Action", "Cluster"]


def test_the_dialog_filters_moves_with_the_arrow_keys_and_runs_the_choice(qtbot):
    ran = []
    entries = [Entry("Go to", "Pods", "", lambda: ran.append("pods")), Entry("Go to", "Nodes", "", lambda: ran.append("nodes")),
               Entry("Action", "Health check", "", lambda: ran.append("health"))]
    dialog = PaletteDialog(entries, free_text=lambda text: Entry("Search", f"Search for {text}", "", lambda: ran.append("search " + text)))
    qtbot.addWidget(dialog)
    assert dialog.list.count() == 3 and dialog.list.currentRow() == 0
    dialog.box.setText("node")
    assert [dialog.shown[i].label for i in range(len(dialog.shown))] == ["Nodes", "Search for node"]
    dialog.box.setText("")
    QApplication.sendEvent(dialog.box, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
    assert dialog.list.currentRow() == 1
    QApplication.sendEvent(dialog.box, QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
    QApplication.sendEvent(dialog.box, QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
    assert dialog.list.currentRow() == 0  # stops at the top
    dialog.box.setText("zzz")
    assert labels(dialog.shown) == ["Search for zzz"]
    QApplication.sendEvent(dialog.box, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert ran == ["search zzz"] and not dialog.isVisible()


def window(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    return win


def test_the_main_window_offers_views_namespaces_clusters_and_enabled_actions(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    before = win.palette_entries()
    assert "Command palette" not in labels(before) and all(e.category in ("Cluster", "Action") for e in before)  # nothing is connected yet
    assert any(e.category == "Cluster" and e.label == "ctx" and e.detail == "connect" for e in before)
    assert "Health check" not in labels(before)  # disabled without a cluster in front
    open_active(win)
    ws = win.clusters.currentWidget()
    ws._ns_names = ["shop", "blog"]
    entries = win.palette_entries()
    by = lambda category: [e.label for e in entries if e.category == category]
    assert "Pods" in by("Go to") and "Deployments" in by("Go to") and {"shop", "blog", "All namespaces"} <= set(by("Namespace"))
    assert "Health check" in by("Action") and "Search objects" in by("Action") and "Preferences" in by("Action")
    assert any(e.category == "Cluster" and e.label == "ctx" and e.detail == "switch to it" for e in entries)
    opened = []
    ws.open_view = opened.append
    next(e for e in entries if e.category == "Go to" and e.label == "Pods").run()
    assert opened == ["pods"]
    chosen = []
    ws.set_namespace = chosen.append
    next(e for e in entries if e.category == "Namespace" and e.label == "blog").run()
    assert chosen == ["blog"]
    assert win.palette_action.shortcut().toString() == "Ctrl+K"


def test_a_menu_action_runs_through_the_palette(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    open_active(win)
    win.managed_fields_action.setChecked(False)
    entry = next(e for e in win.palette_entries() if e.label == "Show managedFields in details")
    entry.run()
    assert win.managed_fields_action.isChecked()
    again = next(e for e in win.palette_entries() if e.label == "Show managedFields in details")
    assert "checked" in again.detail
