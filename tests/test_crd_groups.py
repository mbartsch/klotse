"""Custom Resources > Groups: the CRDs listed by API group, next to Definitions."""
from types import SimpleNamespace

from PySide6.QtCore import QSettings

from klotse.k8s.resources import KINDS_SHOWN, Row, crd_groups
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.navigator import VIEWS
from klotse.ui.workspace import ClusterWorkspace
from test_app import FakeService, open_active


def crd(group, kind, scope="Namespaced"):
    plural = kind.lower() + "s"
    return Row(f"{plural}.{group}", "", {}, group=group, kind=kind, scope=scope, plural=plural, version="v1", versions="v1", age="1d")


CRDS = [crd("cert-manager.io", "Certificate"), crd("cert-manager.io", "Issuer"), crd("cert-manager.io", "ClusterIssuer", "Cluster"),
        crd("karpenter.sh", "NodePool", "Cluster"), crd("karpenter.sh", "NodeClaim", "Cluster"), crd("argoproj.io", "Application")]


# ---- the rows ----------------------------------------------------------------------------------------------------
def test_one_row_per_group_sorted_with_count_scope_and_kinds():
    rows = crd_groups(CRDS)
    assert [r.name for r in rows] == ["argoproj.io", "cert-manager.io", "karpenter.sh"]
    argo, cert, karpenter = rows
    assert (argo.count, argo.scope, argo.kinds) == (1, "Namespaced", "Application")
    assert (cert.count, cert.scope, cert.kinds) == (3, "Mixed", "Certificate, ClusterIssuer, Issuer")
    assert (karpenter.count, karpenter.scope) == (2, "Cluster")


def test_a_group_with_many_kinds_names_the_first_and_counts_the_rest():
    many = [crd("big.io", f"Kind{i:02d}") for i in range(KINDS_SHOWN + 3)]
    [row] = crd_groups(many)
    assert row.count == KINDS_SHOWN + 3 and row.kinds.endswith("+3 more") and row.kinds.count(",") == KINDS_SHOWN - 1


def test_the_details_of_a_group_list_its_crds():
    [row] = crd_groups([crd("karpenter.sh", "NodePool", "Cluster")])
    assert row.raw == {"group": "karpenter.sh", "crds": [{"kind": "NodePool", "scope": "Cluster", "plural": "nodepools",
                                                          "name": "nodepools.karpenter.sh"}]}


def test_nothing_gives_no_rows_and_a_missing_group_is_its_own_row():
    assert crd_groups([]) == []
    assert [r.name for r in crd_groups([SimpleNamespace(group=None, kind="X", scope="Cluster", plural="xs", name="xs")])] == [""]


# ---- the navigator and the workspace -----------------------------------------------------------------------------
def test_groups_sits_at_the_same_level_as_definitions():
    group = next(entry for entry in VIEWS if len(entry) == 3 and entry[0] == "Custom Resources")
    assert group[1] == [("crds", "Definitions"), ("crd-groups", "Groups")]


def make_ws(qtbot):
    asked = []
    service = SimpleNamespace(list_resource=lambda key, ns=None: asked.append((key, ns)) or list(CRDS))
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    return ws, asked


def test_the_groups_view_loads_from_the_crd_list(qtbot):
    ws, asked = make_ws(qtbot)
    ws.open_view("crd-groups")
    table = ws.views["crd-groups"][0]
    qtbot.waitUntil(lambda: table.model.rowCount() == 3, timeout=5000)
    assert asked[0][0] == "crds" and [c[1] for c in table.base_columns] == ["name", "count", "scope", "kinds"]
    assert not ws.loaders["crd-groups"][1]  # not namespaced: the namespace selector is hidden
    assert table.customize_button is None  # a fixed list, like the other derived lists


def test_double_click_and_the_menu_open_the_crds_of_the_group(qtbot):
    ws, _ = make_ws(qtbot)
    ws.open_view("crd-groups")
    table = ws.views["crd-groups"][0]
    qtbot.waitUntil(lambda: table.model.rowCount() == 3, timeout=5000)
    assert table.DOUBLE_CLICK["CRDGroup"] == "open-crd-group"
    ws._crd_action("open-crd-group", table.model.rows[1])  # cert-manager.io
    key = "crd-group:cert-manager.io"
    assert ws.foreground_key() == key and ws.tabs.tabText(ws.tabs.currentIndex()) == "CRDs: cert-manager.io"
    crds = ws.views[key][0]
    qtbot.waitUntil(lambda: crds.model.rowCount() == 3, timeout=5000)
    assert sorted(r.kind for r in crds.model.rows) == ["Certificate", "ClusterIssuer", "Issuer"]
    ws.open_crd_group("cert-manager.io")  # a second open focuses the same tab
    assert ws.tabs.count() == 2


def test_the_group_tab_opens_instances_like_definitions(qtbot):
    ws, _ = make_ws(qtbot)
    opened = []
    ws.open_custom_resources = lambda row: opened.append(row.kind)
    ws.open_crd_group("karpenter.sh")
    crds = ws.views["crd-group:karpenter.sh"][0]
    qtbot.waitUntil(lambda: crds.model.rowCount() == 2, timeout=5000)
    crds.action_requested.emit("open-crd", crds.model.rows[0])
    assert opened == [crds.model.rows[0].kind]


def test_a_double_click_on_a_group_opens_its_crds(qtbot):
    ws, _ = make_ws(qtbot)
    table = ws.views["crd-groups"][0]
    table.set_rows(crd_groups(CRDS))
    table._on_double_click(table.proxy.index(0, 0))  # argoproj.io, the real double-click path
    assert ws.foreground_key() == "crd-group:argoproj.io"


def test_the_navigator_leaf_opens_the_view_in_a_window(qtbot, tmp_path):
    class Service(FakeService):
        def list_resource(self, key, namespace=None):
            return list(CRDS) if key == "crds" else []

    win = MainWindow(Service, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws._ready, timeout=5000)
    win._open_view("ctx", "crd-groups")
    table = ws.views["crd-groups"][0]
    qtbot.waitUntil(lambda: table.model.rowCount() == 3, timeout=5000)
    assert ws.foreground_key() == "crd-groups"
