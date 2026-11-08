import dataclasses
from types import SimpleNamespace

from PySide6.QtCore import QSettings

from klotse.k8s.models import PodRow
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow, _MultiSource
from klotse.ui.multi_cluster import MultiClusterDialog
from klotse.ui.overview import POD_COLUMNS
from test_app import FakeService, open_active


def pod(name, ns="shop", restarts=0, status="Running"):
    return PodRow(name, ns, "1/1", status, restarts, "n1", "5m", {"metadata": {"name": name, "namespace": ns, "uid": f"{ns}-{name}"}})


class Source:
    """Two fake clusters; `eu` answers, `us` can be made to fail or answer late."""

    def __init__(self):
        self.calls, self.fail = [], set()
        self.data = {"eu": [pod("a"), pod("b", restarts=7)], "us": [pod("a"), pod("c", ns="data")]}

    def contexts(self):
        return ["eu", "us"]

    def kinds(self):
        return [("pods", "Pods"), ("deployments", "Deployments")]

    def columns(self, key):
        return list(POD_COLUMNS) if key == "pods" else [("Name", "name"), ("Namespace", "namespace")]

    def table_kind(self, key):
        return "Pod" if key == "pods" else "Deployment"

    def fetch(self, context, key):
        def read(namespace):
            self.calls.append((context, key, namespace))
            if context in self.fail:
                raise PermissionError("forbidden")
            return [r for r in self.data[context] if namespace in (None, r.namespace)]
        return read


def shown(dialog):
    t = dialog.table
    return sorted((t.proxy.index(i, 0).data(), t.proxy.index(i, 1).data()) for i in range(t.proxy.rowCount()))


def test_rows_of_several_clusters_land_in_one_list_with_a_cluster_column(qtbot):
    source = Source()
    dialog = MultiClusterDialog(source)
    qtbot.addWidget(dialog)
    assert [dialog.kind_box.itemText(i) for i in range(2)] == ["Pods", "Deployments"] and set(dialog.boxes) == {"eu", "us"}
    dialog.read()
    qtbot.waitUntil(lambda: dialog.table.model.rowCount() == 4 and dialog.read_button.isEnabled(), timeout=5000)
    assert shown(dialog) == [("eu", "a"), ("eu", "b"), ("us", "a"), ("us", "c")]  # the Cluster column first, then Name
    assert "4 rows from 2 of 2 clusters" in dialog.status.text()
    assert sorted(source.calls) == [("eu", "pods", None), ("us", "pods", None)]


def test_one_cluster_failing_leaves_the_others_and_is_named(qtbot):
    source = Source()
    source.fail.add("us")
    dialog = MultiClusterDialog(source)
    qtbot.addWidget(dialog)
    dialog.read()
    qtbot.waitUntil(lambda: dialog.read_button.isEnabled() and "could not read" in dialog.status.text(), timeout=5000)
    assert dialog.table.model.rowCount() == 2 and "us (PermissionError: forbidden)" in dialog.status.text() and "1 of 2 clusters" in dialog.status.text()


def test_unticked_clusters_are_not_read_and_a_namespace_narrows_the_read(qtbot):
    source = Source()
    dialog = MultiClusterDialog(source)
    qtbot.addWidget(dialog)
    dialog.boxes["eu"].setChecked(False)
    dialog.namespace.setText("data")
    dialog.read()
    qtbot.waitUntil(lambda: dialog.read_button.isEnabled(), timeout=5000)
    assert source.calls == [("us", "pods", "data")] and shown(dialog) == [("us", "c")]
    for box in dialog.boxes.values():
        box.setChecked(False)
    dialog.read()
    assert "Tick at least one cluster" in dialog.status.text()


def test_filter_expressions_work_across_clusters(qtbot):
    source = Source()
    dialog = MultiClusterDialog(source)
    qtbot.addWidget(dialog)
    dialog.read()
    qtbot.waitUntil(lambda: dialog.table.model.rowCount() == 4, timeout=5000)
    def filtered(text):
        dialog.table.filter.setText(text)
        qtbot.waitUntil(lambda: not dialog.table._filter_timer.isActive(), timeout=3000)  # the 200 ms debounce has run
        return shown(dialog)

    assert filtered("Cluster = us and Namespace = data") == [("us", "c")]
    assert filtered("Restarts > 3") == [("eu", "b")]
    assert filtered("Cluster = e*") == [("eu", "a"), ("eu", "b")]


def test_changing_the_kind_starts_a_fresh_list_and_drops_late_answers(qtbot):
    source = Source()
    dialog = MultiClusterDialog(source)
    qtbot.addWidget(dialog)
    dialog.read()
    dialog.kind_box.setCurrentIndex(1)  # before the answers are processed
    qtbot.wait(300)
    assert dialog.table.model.rowCount() == 0 and [c[0] for c in dialog.table.model.columns] == ["Cluster", "Name", "Namespace"]


def test_double_click_asks_to_go_to_the_object_in_its_cluster(qtbot):
    source = Source()
    dialog = MultiClusterDialog(source)
    qtbot.addWidget(dialog)
    dialog.read()
    qtbot.waitUntil(lambda: dialog.table.model.rowCount() == 4, timeout=5000)
    wanted = []
    dialog.go_to_requested.connect(lambda *a: wanted.append(a))
    dialog.table.view.selectRow(0)
    dialog._open_selected()
    row = dialog.table.selected_rows()[0]
    assert wanted == [(row.cluster, "Pod", row.namespace, row.name)]


def test_pod_columns_that_are_hidden_in_the_pods_list_are_hidden_here_too(qtbot):
    dialog = MultiClusterDialog(Source())
    qtbot.addWidget(dialog)
    attrs = [c[1] for c in dialog.table.model.columns]
    assert attrs[0] == "cluster" and "restarts" in attrs and "cpu_req" not in attrs and "cpu_lim_pct" not in attrs


# ---- the main window's adapter ---------------------------------------------------------------------------------------
def window(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    return win


def test_the_menu_entry_needs_an_open_cluster_and_offers_the_lists_of_the_workspace(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    assert win.show_multi() is False
    open_active(win)
    ws = win.clusters.currentWidget()
    source = _MultiSource(win)
    keys = [k for k, _t in source.kinds()]
    assert source.contexts() == [ws.context] and {"pods", "deployments", "nodes", "namespaces", "services"} <= set(keys)
    assert "overview" not in keys and not any(":" in k for k in keys) and source.table_kind("pods") == "Pod"
    assert source.columns("pods")[0] == ("Name", "name")
    assert win.show_multi() is True and win.multi_action.shortcut().toString() == "Ctrl+Shift+M"
    win._multi_dialog.close()


def test_the_adapter_reads_with_the_workspaces_own_loader_and_survives_a_closed_cluster(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    ws.loaders["pods"] = (lambda ns: [pod("x")], True, lambda rows: None)
    source = _MultiSource(win)
    assert [r.name for r in source.fetch(ws.context, "pods")(None)] == ["x"]
    try:
        source.fetch("gone", "pods")(None)
        raise AssertionError("should have failed")
    except RuntimeError as error:
        assert "no longer open" in str(error)


def test_a_huge_cluster_reads_its_first_page_without_touching_the_workspaces_pager(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    ws.big = ws.huge = True
    pager = SimpleNamespace(next=lambda: [pod("first-page")])
    ws.service = SimpleNamespace(pod_pager=lambda namespace: pager)
    ws._pod_pager = "the list's own pager"
    assert [r.name for r in _MultiSource(win).fetch(ws.context, "pods")(None)] == ["first-page"]
    assert ws._pod_pager == "the list's own pager"


def test_going_to_an_object_brings_its_cluster_to_the_front(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    went = []
    ws.go_to_object = lambda *a: went.append(a)
    win._goto_cluster_object(ws.context, "Pod", "shop", "a")
    assert went == [("Pod", "shop", "a")]
    win._goto_cluster_object("closed", "Pod", "shop", "a")  # no error
    assert "no longer open" in win.status.currentMessage()
