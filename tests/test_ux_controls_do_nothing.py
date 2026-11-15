"""UX review batch 1 (#495): controls that did nothing, and messages or colours that said the wrong thing."""
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QMessageBox

from klotse.k8s.models import PodRow
from klotse.ui.compare_dialog import CompareDialog
from klotse.ui.detail_panel import DetailPanel
from klotse.ui.metrics_dialog import NOTE_PROMETHEUS
from klotse.ui.navigator import Navigator
from klotse.ui.prometheus_source_dialog import AUTO, PROXY, PrometheusSourceDialog
from klotse.ui.resource_table import ResourceTable
from klotse.ui.status_tone import status_tone
from klotse.ui.wording import counted, menu_text, plural_label
from klotse.ui.workspace import ClusterWorkspace
from klotse.ui.overview import POD_COLUMNS
from klotse.k8s.trivy import summary_text


def pod(name="web", status="Running"):
    return PodRow(name, "team", "1/1", status, 0, "node-a", "1d", {
        "kind": "Pod", "metadata": {"name": name, "namespace": "team", "uid": "u1"},
        "spec": {"containers": [{"name": "app", "image": "app:1"}]},
        "status": {"containerStatuses": [{"name": "app", "ready": True, "restartCount": 0, "state": {"running": {}}}]}})


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(NS(**service), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    ws._ready = True
    return ws


def find_item(tree, name):
    """The navigator item of a cluster, found anywhere in the tree."""
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item.data(0, tree.ROLE_KIND) == "cluster" and item.data(0, tree.ROLE_KEY) == name:
            return item
        stack.extend(item.child(i) for i in range(item.childCount()))
    raise AssertionError(name)


def test_clicking_a_connected_cluster_brings_it_to_the_front(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod", "dev"])
    nav.set_state("prod", "connected")
    seen = []
    nav.context_clicked.connect(seen.append)
    nav._on_click(find_item(nav, "prod"), 0)
    assert seen == ["prod"]
    seen.clear()
    nav._on_click(find_item(nav, "dev"), 0)  # not connected: nothing to bring forward
    assert seen == []


def test_node_and_pending_pod_lists_get_the_usage_log_and_compare_entries(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    calls = []
    for name in ("show_metrics", "show_metrics_query", "show_metrics_many", "multi_logs", "compare_objects"):
        monkeypatch.setattr(ws, name, lambda *args, n=name: calls.append(n))
    table = ResourceTable(POD_COLUMNS, "Pod", customizable=False)
    ws._wire_pod_list_extras(table)
    table.metrics_requested.emit("Pod", None)
    table.metrics_query_requested.emit("Pod", None)
    table.metrics_many_requested.emit("Pod", [])
    table.multi_logs_requested.emit("Pod", [])
    table.compare_requested.emit("Pod", [])
    assert calls == ["show_metrics", "show_metrics_query", "show_metrics_many", "multi_logs", "compare_objects"]


def test_the_pod_tab_refresh_button_reloads_the_tab(qtbot):
    ws = make_ws(qtbot)
    reloaded = []
    ws.load_view = lambda key: reloaded.append(key)
    ws.open_pod_tab(pod())
    table = ws._pod_tabs["pod:team/web"]["table"]
    table.refresh_requested.emit()
    assert reloaded[-1] == "pod:team/web"


def test_the_palette_namespace_entry_reloads_the_list_in_front(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws.ns_combo.addItems(["All namespaces", "shop"])
    loads = []
    monkeypatch.setattr(ws, "load_scoped", lambda: loads.append(ws.namespace()))
    assert ws.choose_namespace("shop") is True
    assert ws.namespace() == "shop" and loads == ["shop"]


def test_the_source_dialog_saves_automatic_search(qtbot, monkeypatch):
    dialog = PrometheusSourceDialog("", PROXY, 0, "", "", False, False)
    qtbot.addWidget(dialog)
    dialog.auto.setChecked(True)
    warnings, accepted = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(dialog, "accept", lambda: accepted.append(True))
    assert dialog.mode() == AUTO
    dialog._accept()
    assert accepted == [True] and warnings == []


def test_a_saved_action_says_what_happened(qtbot):
    ws = make_ws(qtbot)
    messages = []
    ws.message.connect(messages.append)
    ws.refresh = lambda: None
    ws._saved("Pod", "web-1", "Terminated")
    ws._saved("CronJob", "nightly", "Suspended")
    assert messages == ["Terminated Pod web-1", "Suspended CronJob nightly"]


def test_status_words_get_the_table_colours_in_the_details_header(qtbot):
    for word in ("ImagePullBackOff", "ErrImagePull", "OOMKilled", "Evicted"):
        assert status_tone(word) == "bad"
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel._set_identity(pod(status="ImagePullBackOff"))
    assert "#" in panel.identity_status.styleSheet()
    from klotse.ui import theme
    assert theme.BAD in panel.identity_status.styleSheet()


def test_plurals_and_counts_are_spelled_out():
    assert plural_label("Ingress") == "Ingresses"
    assert plural_label("NetworkPolicy") == "NetworkPolicies"
    assert plural_label("NodePool") == "NodePools"
    assert counted(1, "Ingress") == "1 Ingress"
    assert counted(3, "Ingress") == "3 Ingresses"
    assert counted(1, "finding") == "1 finding"


def test_compare_dialog_title_uses_the_plural(qtbot):
    row = NS(name="web", namespace="team", raw={"metadata": {"name": "web"}})
    dialog = CompareDialog("ctx", "Ingress", row, row)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Compare Ingresses"


def test_menu_captions_keep_a_double_ampersand():
    assert menu_text("Logs && activity") == "Logs & activity"
    assert menu_text("&Logs") == "Logs"


def test_usage_history_note_does_not_claim_there_is_no_port_forward():
    assert "no port-forward" not in NOTE_PROMETHEUS.lower()


def test_empty_trivy_report_list_reads_correctly():
    assert summary_text([], "RBAC") == "No RBAC reports."
    assert summary_text([], "Config audit") == "No config audit reports."


def test_multi_cluster_refresh_reads_again(qtbot, monkeypatch):
    from klotse.ui.multi_cluster import MultiClusterDialog
    dialog = MultiClusterDialog(NS(table_kind=lambda key: "Pod", columns=lambda key: [], kinds=lambda: [], contexts=lambda: []))
    qtbot.addWidget(dialog)
    reads = []
    monkeypatch.setattr(dialog, "read", lambda: reads.append(True))
    dialog._new_table()
    dialog.table.refresh_requested.emit()
    assert reads == [True]
