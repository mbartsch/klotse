"""#815: clusters added from the cloud can be removed from Klotse's own kubeconfig (klotse-cloud.klotse). Only that file
changes, with a .bak copy; entries other contexts still use stay; nothing is offered for contexts from other files."""
import os
from pathlib import Path

import yaml
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s import cloud_clusters as cloud
from klotse.ui import main_window as main_window_module
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.navigator import Navigator
from test_cloud_clusters import kubeconfig


def read(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ---- core: remove_contexts ----------------------------------------------------------------------------------------------
def test_removes_the_contexts_with_the_clusters_and_users_only_they_used(tmp_path):
    path = kubeconfig(tmp_path / "klotse-cloud.klotse", ["eks-1", "eks-2", "aks-1"], current="eks-1")
    before = path.read_bytes()
    assert cloud.remove_contexts(path, ["eks-1", "aks-1", "not-there"]) == ["eks-1", "aks-1"]
    data = read(path)
    assert [c["name"] for c in data["contexts"]] == ["eks-2"]
    assert [c["name"] for c in data["clusters"]] == ["eks-2"]
    assert [u["name"] for u in data["users"]] == ["user-eks-2"]
    assert data["current-context"] == "eks-2"  # the removed current context moves to a remaining one
    assert data["contexts"][0] == {"name": "eks-2", "context": {"cluster": "eks-2", "user": "user-eks-2"}}  # kept as it was
    assert Path(f"{path}.bak").read_bytes() == before
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0


def test_a_cluster_or_user_another_context_still_names_is_kept(tmp_path):
    path = tmp_path / "klotse-cloud.klotse"
    path.write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config", "current-context": "other",
        "clusters": [{"name": "shared", "cluster": {"server": "https://shared.example.test"}},
                     {"name": "unrelated", "cluster": {"server": "https://unrelated.example.test"}}],
        "users": [{"name": "admin", "user": {"token": "x"}}],
        "contexts": [{"name": "gone", "context": {"cluster": "shared", "user": "admin"}},
                     {"name": "other", "context": {"cluster": "shared", "user": "admin", "namespace": "dev"}}],
    }, sort_keys=False), encoding="utf-8")
    assert cloud.remove_contexts(path, ["gone"]) == ["gone"]
    data = read(path)
    assert [c["name"] for c in data["clusters"]] == ["shared", "unrelated"]  # unrelated: not used by the removed context
    assert [u["name"] for u in data["users"]] == ["admin"]
    assert data["current-context"] == "other"


def test_removing_the_last_context_clears_current_context(tmp_path):
    path = kubeconfig(tmp_path / "k", ["only"], current="only")
    cloud.remove_contexts(path, ["only"])
    data = read(path)
    assert data["contexts"] == [] and data["clusters"] == [] and data["users"] == [] and data["current-context"] == ""


def test_nothing_to_remove_leaves_the_file_and_makes_no_backup(tmp_path):
    path = kubeconfig(tmp_path / "k", ["a"])
    before = path.read_bytes()
    assert cloud.remove_contexts(path, ["b"]) == []
    assert cloud.remove_contexts(tmp_path / "missing", ["a"]) == []
    assert path.read_bytes() == before and not Path(f"{path}.bak").exists()


# ---- navigator: the menu entry ------------------------------------------------------------------------------------------
def cluster_item(nav, name):
    stack = [nav.topLevelItem(i) for i in range(nav.topLevelItemCount())]
    while stack:
        node = stack.pop()
        if node.data(0, nav.ROLE_KIND) == "cluster" and node.data(0, nav.ROLE_KEY) == name:
            return node
        stack.extend(node.child(i) for i in range(node.childCount()))
    raise AssertionError(name)


def remove_action(menu):
    return next((a for a in menu.actions() if a.text().startswith("Remove from Klotse")), None)


def test_the_entry_is_offered_only_for_removable_disconnected_clusters(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["cloud-1", "cloud-2", "main"])
    assert remove_action(nav.menu_for(cluster_item(nav, "cloud-1"))) is None  # no owner said what is removable
    nav.removable = lambda ctx: ctx.startswith("cloud")
    assert remove_action(nav.menu_for(cluster_item(nav, "main"))) is None
    action = remove_action(nav.menu_for(cluster_item(nav, "cloud-1")))
    assert action.text() == "Remove from Klotse…"
    with qtbot.waitSignal(nav.remove_requested) as signal:
        action.trigger()
    assert signal.args == [("cloud-1",)]
    nav.set_state("cloud-2", "connected")
    assert remove_action(nav.menu_for(cluster_item(nav, "cloud-2"))) is None  # disconnect first


def test_several_selected_clusters_are_removed_together_skipping_the_others(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["cloud-1", "cloud-2", "main"])
    nav.removable = lambda ctx: ctx.startswith("cloud")
    for name in ("cloud-1", "cloud-2", "main"):
        cluster_item(nav, name).setSelected(True)
    action = remove_action(nav.menu_for(cluster_item(nav, "cloud-2")))
    assert action.text() == "Remove from Klotse (2)…"
    with qtbot.waitSignal(nav.remove_requested) as signal:
        action.trigger()
    assert sorted(signal.args[0]) == ["cloud-1", "cloud-2"]


# ---- main window: confirmation, removal and the refreshed list ---------------------------------------------------------
class FileService:
    """Reads context names from the kubeconfig file it is pointed at (none for the default one)."""

    def __init__(self):
        self.kubeconfig = None
        self.proxy, self.no_proxy = "", []

    def load_contexts(self):
        if self.kubeconfig and Path(self.kubeconfig).exists():
            return [c["name"] for c in read(self.kubeconfig).get("contexts") or []], None
        return ["main-cluster"], None


def window(qtbot, tmp_path, monkeypatch):
    (tmp_path / ".kube").mkdir()
    generated =kubeconfig(tmp_path / ".kube" / "klotse-cloud.klotse", ["eks-1", "eks-2"], current="eks-1")
    monkeypatch.setattr(main_window_module, "default_generated_path", lambda home=None: generated)
    win = MainWindow(FileService, LabelSettings(QSettings(str(tmp_path / "Klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    win.kubeconfig_settings.add_extra(str(generated))
    win._reload_cluster_list()
    return win, generated


def test_only_clusters_from_klotses_file_without_an_open_tab_are_removable(qtbot, tmp_path, monkeypatch):
    win, _ = window(qtbot, tmp_path, monkeypatch)
    assert win.is_removable_cloud_cluster("eks-1")
    assert not win.is_removable_cloud_cluster("main-cluster")  # the main kubeconfig is never offered
    assert not win.is_removable_cloud_cluster("unknown")
    win.workspaces["eks-1"] = object()  # an open tab: disconnect first
    try:
        assert not win.is_removable_cloud_cluster("eks-1")
    finally:
        del win.workspaces["eks-1"]


def test_cancel_changes_nothing(qtbot, tmp_path, monkeypatch):
    win, generated = window(qtbot, tmp_path, monkeypatch)
    before = generated.read_bytes()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.No))
    monkeypatch.setattr(main_window_module.Path, "home", staticmethod(lambda: tmp_path / "elsewhere"))
    assert win.remove_cloud_clusters(("eks-1",)) is False
    assert "Nothing changes in the cloud" in asked[0] and "eks-1" in asked[0] and str(generated) in asked[0]  # not under home
    monkeypatch.setattr(main_window_module.Path, "home", staticmethod(lambda: tmp_path))
    win.remove_cloud_clusters(("eks-1",))
    assert str(Path("~") / ".kube" / "klotse-cloud.klotse") in asked[1]  # under home: shortened to ~
    assert generated.read_bytes() == before and "eks-1" in win.contexts


def test_confirmed_removal_updates_the_file_the_list_alias_and_group(qtbot, tmp_path, monkeypatch):
    win, generated = window(qtbot, tmp_path, monkeypatch)
    win.aliases.set("eks-1", "Lab EKS")
    win.groups.assign("eks-1", "lab")
    win._save_groups()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert win.remove_cloud_clusters(("eks-1", "main-cluster")) is True  # main-cluster is not removable: skipped
    assert [c["name"] for c in read(generated)["contexts"]] == ["eks-2"]
    assert win.contexts == ["main-cluster", "eks-2"]
    assert win.aliases.display("eks-1") == "eks-1" and win.groups.group_of("eks-1") == ""
    assert "Removed 1 cluster(s)" in win.status.currentMessage()
