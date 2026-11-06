"""The Karpenter group in the navigator: only for clusters that serve Karpenter's API, with one list per kind."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.k8s.api_index import ApiResource
from klotse.k8s.client import KubeService
from klotse.k8s.resources import Row, map_custom_object
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.navigator import Navigator
from klotse.ui.workspace import ClusterWorkspace
from test_app import FakeService, open_active

NODEPOOL = ApiResource("karpenter.sh", "nodepools", "NodePool", False, "v1")
NODECLAIM = ApiResource("karpenter.sh", "nodeclaims", "NodeClaim", False, "v1")
NODECLASS = ApiResource("karpenter.k8s.aws", "ec2nodeclasses", "EC2NodeClass", False, "v1")


# ---- finding Karpenter -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("group, expected", [("karpenter.sh", True), ("karpenter.k8s.aws", True), ("karpenter.azure.com", True),
                                             ("apps", False), ("", False), (None, False), ("notkarpenter.io", False),
                                             ("example.com", False)])
def test_which_groups_belong_to_karpenter(group, expected):
    assert KubeService.is_karpenter_group(group) is expected


def test_the_service_lists_karpenters_kinds_without_subresources_nodepool_and_nodeclaim_first():
    svc = object.__new__(KubeService)
    svc.discovery_groups = lambda: [("", "v1"), ("apps", "v1"), ("karpenter.k8s.aws", "v1"), ("karpenter.sh", "v1"), ("example.com", "v1")]
    served = {"karpenter.sh": [ApiResource("karpenter.sh", "nodeoverlays", "NodeOverlay", False, "v1"),
                               ApiResource("karpenter.sh", "nodeclaims", "NodeClaim", False, "v1"),
                               ApiResource("karpenter.sh", "nodeclaims/status", "NodeClaim", False, "v1"),
                               ApiResource("karpenter.sh", "nodepools", "NodePool", False, "v1")],
              "karpenter.k8s.aws": [NODECLASS]}
    asked = []
    svc.discovery_resources = lambda group, version: asked.append(group) or served[group]
    assert [r.kind for r in svc.karpenter_resources()] == ["NodePool", "NodeClaim", "EC2NodeClass", "NodeOverlay"]
    assert sorted(asked) == ["karpenter.k8s.aws", "karpenter.sh"]  # other groups are never asked about


def test_a_cluster_without_karpenter_gives_nothing():
    svc = object.__new__(KubeService)
    svc.discovery_groups = lambda: [("", "v1"), ("apps", "v1")]
    svc.discovery_resources = lambda *a: pytest.fail("no Karpenter group to ask about")
    assert svc.karpenter_resources() == []


# ---- the workspace -----------------------------------------------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_found_kinds_become_views_and_the_navigator_is_told(qtbot):
    ws = make_ws(qtbot, karpenter_resources=lambda: [NODEPOOL, NODECLAIM, NODECLASS])
    got = []
    ws.karpenter_found.connect(got.append)
    ws._find_karpenter()
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got == [[("cr:nodepools.karpenter.sh", "NodePools"), ("cr:nodeclaims.karpenter.sh", "NodeClaims"),
                    ("cr:ec2nodeclasses.karpenter.k8s.aws", "EC2NodeClasses")]]
    pool = ws.views["cr:nodepools.karpenter.sh"][0]
    assert [c[1] for c in pool.base_columns][:3] == ["name", "nodeclass", "nodes"]  # the Karpenter columns


def test_a_view_from_the_navigator_and_from_the_crd_list_is_the_same_tab(qtbot):
    ws = make_ws(qtbot, list_custom_objects=lambda *a: [])
    key = ws._custom_view("karpenter.sh", "v1", "nodepools", "NodePool", False)
    crd = Row("nodepools.karpenter.sh", "", {}, group="karpenter.sh", kind="NodePool", scope="Cluster", plural="nodepools",
              version="v1", versions="v1", age="1d")
    before = ws.views[key]
    ws.load_view = lambda k: None
    ws.open_custom_resources(crd)
    assert ws.views[key] is before and ws.tabs.count() == 1


def test_a_namespaced_kind_keeps_the_namespace_selector_behaviour(qtbot):
    ws = make_ws(qtbot)
    key = ws._custom_view("karpenter.azure.com", "v1", "things", "Thing", True)
    assert ws.loaders[key][1] is True and ws.views[key][0].base_columns[1][1] == "namespace"


@pytest.mark.parametrize("service", [{}, {"karpenter_resources": lambda: []},
                                     {"karpenter_resources": lambda: (_ for _ in ()).throw(RuntimeError("forbidden"))}])
def test_no_karpenter_a_service_that_cannot_say_or_a_failure_is_silent(qtbot, monkeypatch, service):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: pytest.fail("a failed Karpenter check must not interrupt"))
    ws = make_ws(qtbot, **service)
    got = []
    ws.karpenter_found.connect(got.append)
    ws._find_karpenter()
    qtbot.waitUntil(lambda: not ws._workers, timeout=5000)
    assert got == [] and not any(k.startswith("cr:") for k in ws.views)


# ---- the navigator -----------------------------------------------------------------------------------------------
def nav_with_cluster(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a"])
    nav.set_state("a", "connected")
    nav.show_views("a")
    return nav


def top_labels(nav):
    item = nav.cluster_item("a")
    return [item.child(i).text(0) for i in range(item.childCount())]


def test_the_group_sits_just_above_custom_resources_and_clicking_a_leaf_opens_the_view(qtbot):
    nav = nav_with_cluster(qtbot)
    assert "Karpenter" not in top_labels(nav)
    nav.show_karpenter("a", [("cr:nodepools.karpenter.sh", "NodePools"), ("cr:nodeclaims.karpenter.sh", "NodeClaims")])
    labels = top_labels(nav)
    assert labels.index("Karpenter") == labels.index("Custom Resources") - 1 and labels[-1] == "Custom Resources"
    group = nav.cluster_item("a").child(labels.index("Karpenter"))
    assert [group.child(i).text(0) for i in range(group.childCount())] == ["NodePools", "NodeClaims"] and group.isExpanded()
    asked = []
    nav.view_requested.connect(lambda ctx, key: asked.append((ctx, key)))
    nav.itemClicked.emit(group.child(1), 0)
    assert asked == [("a", "cr:nodeclaims.karpenter.sh")]


def test_showing_it_again_replaces_the_group_and_nothing_is_added_for_an_empty_list(qtbot):
    nav = nav_with_cluster(qtbot)
    before = top_labels(nav)
    nav.show_karpenter("a", [])
    assert top_labels(nav) == before
    nav.show_karpenter("a", [("k1", "NodePools")])
    nav.show_karpenter("a", [("k2", "NodeClaims")])
    assert top_labels(nav).count("Karpenter") == 1
    group = nav.cluster_item("a").child(top_labels(nav).index("Karpenter"))
    assert group.child(0).text(0) == "NodeClaims" and group.childCount() == 1


def test_a_cluster_that_is_not_expanded_or_unknown_gets_nothing(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a"])
    nav.show_karpenter("a", [("k", "NodePools")])  # not connected: no views to put it among
    nav.show_karpenter("zzz", [("k", "NodePools")])
    assert nav.cluster_item("a").childCount() == 0


# ---- the whole window --------------------------------------------------------------------------------------------
class WithKarpenter(FakeService):
    def karpenter_resources(self):
        return [NODEPOOL, NODECLAIM]

    def list_custom_objects(self, group, version, plural, namespaced, namespace=None, kind=None):
        return [map_custom_object({"metadata": {"name": "default"}, "spec": {}}, group=group, kind=kind)]


def test_a_cluster_with_karpenter_shows_the_group_and_opens_its_lists(qtbot, tmp_path):
    win = MainWindow(WithKarpenter, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    item = win.nav.cluster_item("ctx")
    labels = lambda: [item.child(i).text(0) for i in range(item.childCount())]
    qtbot.waitUntil(lambda: "Karpenter" in labels(), timeout=5000)
    group = item.child(labels().index("Karpenter"))
    win.nav.itemClicked.emit(group.child(0), 0)
    table = ws.views["cr:nodepools.karpenter.sh"][0]
    qtbot.waitUntil(lambda: table.model.rowCount() == 1, timeout=5000)
    assert ws.tabs.tabText(ws.tabs.currentIndex()) == "NodePool"


def test_a_cluster_without_karpenter_has_no_group(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    open_active(win)
    qtbot.waitUntil(lambda: win.workspace("ctx")._ready, timeout=5000)
    item = win.nav.cluster_item("ctx")
    assert "Karpenter" not in [item.child(i).text(0) for i in range(item.childCount())]
