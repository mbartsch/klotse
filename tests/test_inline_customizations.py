"""Label-column customization lives on the Nodes / Pods tabs; uncounted pod numbers show as unknown."""
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QMenuBar

from klotse.k8s.models import NodeRow
from klotse.ui import main_window as main_module
from klotse.ui.main_window import MainWindow
from klotse.ui.resource_table import ResourceTable
from klotse.ui.workspace import ClusterWorkspace

from test_app import FakeService


def test_resource_lists_have_a_columns_button_but_drilldowns_do_not(qtbot):
    ws = ClusterWorkspace(NS(), "ctx")
    qtbot.addWidget(ws)
    assert ws.node_table.customize_button.text() == "Columns…" and ws.pod_table.customize_button is not None
    for key in ("deployments", "daemonsets", "services", "namespaces", "events"):
        assert ws.views[key][0].customize_button is not None, key
    assert ws.overview.pods_table.customize_button is None  # drill-down tables stay compact
    ws.load_view = lambda key: None
    ws.open_node_pods("n1")
    assert ws.views["node-pods:n1"][0].customize_button is not None  # pods on a node have their own button


def test_button_requests_customization_for_its_kind(qtbot):
    ws = ClusterWorkspace(NS(), "ctx")
    qtbot.addWidget(ws)
    ws.load_view = lambda key: None
    asked = []
    ws.customize_requested.connect(asked.append)
    ws.node_table.customize_button.click()
    ws.pod_table.customize_button.click()
    ws.open_node_pods("n1")
    ws.views["node-pods:n1"][0].customize_button.click()
    assert asked == ["Node", "Pod", "Pod"]


def test_main_window_has_no_global_customizations_menu_and_opens_dialog_from_tab(qtbot, monkeypatch):
    shown = []

    class FakeDialog:
        def __init__(self, kind, settings, available, builtin, parent):
            shown.append((kind, sorted(available)))

        def exec(self):
            return 0

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_module, "LabelColumnsDialog", FakeDialog)
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    expected_menus = ["File", "Edit", "View", "Settings", "Help"] if main_module.sys.platform == "darwin" else ["File", "Edit", "View", "Help"]
    assert [a.text() for a in win.menuBar().actions()] == expected_menus
    assert not hasattr(win, "customizations_button")
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws._ready, timeout=5000)
    ws.open_view("nodes")
    ws.node_table.customize_button.click()
    assert shown and shown[0][0] == "Node"


def test_pod_dialog_lists_request_and_limit_columns_not_the_overview_subset(qtbot, monkeypatch):
    from klotse.ui.overview import POD_COLUMNS, RESOURCE_COLUMNS

    got = []

    class FakeDialog:
        def __init__(self, kind, settings, available, builtin, parent):
            got.append([c[1] for c in builtin])

        def exec(self):
            return 0

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_module, "LabelColumnsDialog", FakeDialog)
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    win.workspace("ctx")
    win.customize_labels("Pod")
    assert got == [[c[1] for c in POD_COLUMNS]] and RESOURCE_COLUMNS <= set(got[0])


def test_uncounted_pods_show_unknown_then_the_number(qtbot):
    ws = ClusterWorkspace(NS(), "ctx")
    qtbot.addWidget(ws)
    row = NodeRow("a", "Ready", "w", "v", "ip", 4.0, 8.0, "1d", {})
    ws.node_table.set_rows([row])
    col = [c[0] for c in ws.node_table.model.columns].index("Pods")
    index = ws.node_table.model.index(0, col)
    assert index.data() == "--"
    row.pods = 0
    ws.node_table.model.refresh_column("pods")
    assert index.data() == "0"  # zero is a real count, not unknown
    assert ws.node_table.model.index(0, col).data(2) == 0  # sort value stays numeric
