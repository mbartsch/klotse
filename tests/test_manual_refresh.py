"""Manual refresh only for rarely changing lists (CRDs, Access Control, classes, webhooks): no timer, no watch, no reload when the
tab comes to the front; they load once when opened and again from the toolbar Refresh or the list's right-click Refresh."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.models import PodRow
from klotse.ui.customizations import LabelSettings
from klotse.ui.refresh_policy import DEFAULT_AUTO, RefreshSettings, default_manual, is_manual
from klotse.ui.workspace import ClusterWorkspace


def store(tmp_path):
    return QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)


# ---- the policy --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["workloads", "pods", "deployments", "daemonsets", "statefulsets", "replicasets", "jobs", "cronjobs",
                                 "horizontalpodautoscalers", "nodes", "overview", "node-pods:n1", "pod:shop/web-1"])
def test_only_workloads_and_nodes_reload_by_themselves_by_default(key):
    assert not default_manual(key) and not is_manual(key, {})


@pytest.mark.parametrize("key", ["crds", "crd-groups", "crd-group:cert-manager.io", "storageclasses", "priorityclasses",
                                 "validatingwebhookconfigurations", "mutatingwebhookconfigurations", "serviceaccounts", "roles",
                                 "rolebindings", "clusterroles", "clusterrolebindings", "events", "services", "ingresses", "namespaces",
                                 "configmaps", "secrets", "persistentvolumes", "persistentvolumeclaims", "networkpolicies",
                                 "cr:nodepools.karpenter.sh", "helm-releases", "limitranges", "endpoints", "leases"])
def test_every_other_list_is_manual_by_default(key):
    assert default_manual(key) and is_manual(key, {})


def test_the_automatic_set_is_exactly_the_workloads_nodes_and_overview():
    assert DEFAULT_AUTO == {"overview", "nodes", "workloads", "pods", "deployments", "daemonsets", "statefulsets", "replicasets", "jobs",
                            "cronjobs", "horizontalpodautoscalers"}


def test_the_users_choice_wins_over_the_default():
    assert is_manual("pods", {"pods": True}) and not is_manual("roles", {"roles": False})


def test_choices_are_kept_and_a_choice_equal_to_the_default_is_not_stored(tmp_path):
    settings = RefreshSettings(store(tmp_path))
    assert settings.overrides() == {}
    settings.set_manual("pods", True)
    settings.set_manual("roles", False)
    settings.set_manual("crds", True)  # the default: nothing to remember
    assert RefreshSettings(store(tmp_path)).overrides() == {"pods": True, "roles": False}
    settings.set_manual("pods", False)  # back to the default
    settings.set_manual("roles", True)
    assert RefreshSettings(store(tmp_path)).overrides() == {}
    assert not [k for k in store(tmp_path).allKeys() if k.startswith("refresh/")]  # nothing is left in the file


def test_a_failed_save_raises_and_keeps_the_old_choice(tmp_path):
    settings_store = store(tmp_path)
    settings = RefreshSettings(settings_store)
    settings.set_manual("pods", True)
    settings_store.status = lambda: QSettings.AccessError
    with pytest.raises(OSError):
        settings.set_manual("events", True)
    settings_store.status = lambda: QSettings.NoError
    assert RefreshSettings(store(tmp_path)).overrides().get("pods") is True


# ---- the workspace -----------------------------------------------------------------------------------------------
class Counting:
    def __init__(self):
        self.calls = {}

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls[name] = self.calls.get(name, 0) + 1
            return []
        return call


def make_ws(qtbot, tmp_path):
    service = Counting()
    ws = ClusterWorkspace(service, "ctx", label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(ws)
    ws._ready = True
    ws.resize(1000, 600)
    ws.show()
    return ws, service


def settle(qtbot, ws):
    qtbot.waitUntil(lambda: not ws._workers and not ws._loading, timeout=5000)


def test_a_manual_list_loads_once_when_opened_and_not_again_when_reopened(qtbot, tmp_path):
    ws, service = make_ws(qtbot, tmp_path)
    ws.open_view("roles")
    settle(qtbot, ws)
    assert service.calls["list_resource"] == 1
    ws.open_view("pods")
    settle(qtbot, ws)
    ws.open_view("roles")  # a click on the navigator entry again: the list is already there
    settle(qtbot, ws)
    assert service.calls["list_resource"] == 1


def test_an_automatic_list_still_reloads_every_time_it_is_opened(qtbot, tmp_path):
    ws, service = make_ws(qtbot, tmp_path)
    ws.open_view("deployments")
    settle(qtbot, ws)
    ws.open_view("deployments")
    settle(qtbot, ws)
    assert service.calls["list_deployments"] == 2


def test_the_timer_and_the_tab_coming_to_the_front_do_not_reload_a_manual_list(qtbot, tmp_path):
    ws, service = make_ws(qtbot, tmp_path)
    ws.open_view("clusterroles")
    settle(qtbot, ws)
    ws._auto_refresh()  # what the timer does
    ws.load_foreground()  # what a tab change does
    settle(qtbot, ws)
    assert service.calls["list_resource"] == 1


def test_an_automatic_list_is_reloaded_by_the_timer(qtbot, tmp_path):
    ws, service = make_ws(qtbot, tmp_path)
    ws.open_view("deployments")
    settle(qtbot, ws)
    ws._auto_refresh()
    settle(qtbot, ws)
    assert service.calls["list_deployments"] == 2


def test_no_change_watch_is_started_for_a_manual_list(qtbot, tmp_path):
    ws, _ = make_ws(qtbot, tmp_path)
    ws.service = SimpleNamespace(watch_targets=lambda key, ns=None: pytest.fail("a manual list must not be watched"),
                                 list_resource=lambda *a: [])
    ws.open_view("roles")
    ws._sync_watch()
    assert ws._watched is None and ws._watchers == []


def test_the_toolbar_refresh_and_the_right_click_refresh_do_reload_a_manual_list(qtbot, tmp_path):
    ws, service = make_ws(qtbot, tmp_path)
    ws.open_view("roles")
    settle(qtbot, ws)
    ws.refresh()  # the toolbar button
    settle(qtbot, ws)
    assert service.calls["list_resource"] > 1  # the roles were loaded again
    table = ws.views["roles"][0]
    before = service.calls["list_resource"]
    table.refresh_requested.emit()  # the right-click entry
    settle(qtbot, ws)
    assert service.calls["list_resource"] == before + 1


def test_the_status_line_says_manual_refresh_and_when_it_was_loaded(qtbot, tmp_path):
    ws, _ = make_ws(qtbot, tmp_path)
    ws.open_view("roles")
    settle(qtbot, ws)
    text = ws.views["roles"][0].status.text()
    assert "manual refresh, loaded " in text and text.rsplit(" ", 1)[1].count(":") == 2
    ws.open_view("deployments")
    settle(qtbot, ws)
    assert "manual" not in ws.views["deployments"][0].status.text()


def test_turning_automatic_refresh_on_for_a_manual_list_is_remembered(qtbot, tmp_path):
    ws, service = make_ws(qtbot, tmp_path)
    ws.open_view("roles")
    settle(qtbot, ws)
    table = ws.views["roles"][0]
    assert table.refresh_mode() is True
    table.refresh_mode_requested.emit(True)  # Refresh automatically ticked
    settle(qtbot, ws)
    assert not ws.is_manual("roles") and "manual" not in table.status.text()
    assert RefreshSettings(store(tmp_path)).overrides() == {"roles": False}
    before = service.calls["list_resource"]
    ws._auto_refresh()
    settle(qtbot, ws)
    assert service.calls["list_resource"] == before + 1  # now the timer reloads it
    again = ClusterWorkspace(Counting(), "ctx", label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(again)
    assert not again.is_manual("roles")  # a new window remembers


def test_turning_it_off_for_an_automatic_list_stops_the_reloads(qtbot, tmp_path):
    ws, service = make_ws(qtbot, tmp_path)
    ws.open_view("deployments")
    settle(qtbot, ws)
    table = ws.views["deployments"][0]
    table.refresh_mode_requested.emit(False)  # Refresh automatically unticked
    assert ws.is_manual("deployments") and "manual refresh" in table.status.text()
    before = service.calls["list_deployments"]
    ws._auto_refresh()
    settle(qtbot, ws)
    assert service.calls["list_deployments"] == before


def test_a_failed_save_is_shown_and_the_mode_stays(qtbot, tmp_path, monkeypatch):
    ws, _ = make_ws(qtbot, tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: shown.append(a[2])))
    ws.refresh_settings.store.status = lambda: QSettings.AccessError
    assert ws.set_manual_refresh("roles", False) is False
    assert shown and ws.is_manual("roles")


def test_every_list_gets_the_menu_entries_but_not_the_drill_down_tables(qtbot, tmp_path):
    ws, _ = make_ws(qtbot, tmp_path)
    for key in ("pods", "roles", "crds", "crd-groups", "nodes"):
        assert ws.views[key][0].refresh_mode is not None
    assert all(t.refresh_mode is None for t in ws.overview.tables())  # their data comes with the Overview


def test_the_tabs_made_later_are_wired_too(qtbot, tmp_path):
    ws, _ = make_ws(qtbot, tmp_path)
    ws.open_crd_group("karpenter.sh")
    assert ws.views["crd-group:karpenter.sh"][0].refresh_mode() is True
    key = ws._custom_view("karpenter.sh", "v1", "nodepools", "NodePool", False)
    assert ws.views[key][0].refresh_mode() is True  # custom resource instances are not workloads: manual like the rest
    pod = PodRow("p", "ns", "1/1", "Running", 0, "n1", "1d", {})
    ws.node_table.set_rows([])
    ws.open_node_pods("n1", None)
    assert ws.views["node-pods:n1"][0].refresh_mode() is False and pod  # a node's pods keep reloading


def test_a_manual_list_that_shows_another_namespace_is_reloaded_when_it_comes_back(qtbot, tmp_path):
    """Changing the namespace is the user's own action: a manual list loaded for the old one must not stay on screen."""
    from klotse.k8s.models import NamespaceRow
    ws, service = make_ws(qtbot, tmp_path)
    ws._set_namespaces([NamespaceRow("default", "Active", "1d", ""), NamespaceRow("shop", "Active", "1d", "")])
    ws.open_view("services")
    settle(qtbot, ws)
    assert service.calls["list_resource"] == 1 and not ws._stale_scope("services")
    ws.open_view("deployments")
    settle(qtbot, ws)
    ws.set_namespace("shop")  # while another tab is in front
    assert ws._stale_scope("services")
    before = service.calls["list_resource"]
    ws.open_view("services")  # back to it: it shows default, the toolbar says shop
    settle(qtbot, ws)
    assert service.calls["list_resource"] == before + 1 and not ws._stale_scope("services")
    ws.open_view("services")  # nothing has changed since: left alone again
    settle(qtbot, ws)
    assert service.calls["list_resource"] == before + 1


def test_a_cluster_wide_manual_list_is_never_stale_because_of_the_namespace(qtbot, tmp_path):
    from klotse.k8s.models import NamespaceRow
    ws, service = make_ws(qtbot, tmp_path)
    ws._set_namespaces([NamespaceRow("default", "Active", "1d", ""), NamespaceRow("shop", "Active", "1d", "")])
    ws.open_view("storageclasses")
    settle(qtbot, ws)
    ws.set_namespace("shop")
    assert not ws._stale_scope("storageclasses")
    ws.open_view("storageclasses")
    settle(qtbot, ws)
    assert service.calls["list_resource"] == 1
