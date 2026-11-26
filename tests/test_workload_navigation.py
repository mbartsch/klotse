"""Workload navigation uses the immediate owner's identity and namespace."""
from types import SimpleNamespace

import pytest
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from PySide6.QtWidgets import QMenu

from klotse.k8s import workload_navigation as navigation
from klotse.k8s.client import KubeService, map_replicaset
from klotse.k8s.models import PodRow
from test_related import make_ws


def metadata(name, uid, namespace="shop", refs=None):
    return client.V1ObjectMeta(name=name, uid=uid, namespace=namespace, owner_references=refs)


def reference(kind, name, uid, *, group="apps", controls=True):
    return client.V1OwnerReference(api_version=f"{group}/v1", kind=kind, name=name, uid=uid, controller=controls)


def replicaset(name, uid, refs=None, namespace="shop"):
    return client.V1ReplicaSet(metadata=metadata(name, uid, namespace, refs),
                               spec=client.V1ReplicaSetSpec(selector=client.V1LabelSelector(), replicas=1),
                               status=client.V1ReplicaSetStatus(replicas=1))


@pytest.mark.parametrize(("child", "owner_kind", "group"), [
    ("Pod", "ReplicaSet", "apps"), ("Pod", "DaemonSet", "apps"),
    ("Pod", "StatefulSet", "apps"), ("Pod", "Job", "batch"),
    ("ReplicaSet", "Deployment", "apps"), ("Job", "CronJob", "batch"),
])
def test_immediate_workload_pairs(child, owner_kind, group):
    unrelated = reference(owner_kind, "wrong-group", "other", group="example.com")
    controller = reference(owner_kind, "owner", "owner-uid", group=group)
    raw = client.ApiClient().sanitize_for_serialization(
        {"metadata": metadata("child", "child-uid", refs=[unrelated, controller])})
    owner = navigation.Identity(owner_kind, "shop", "owner", "owner-uid")
    assert navigation.controller(child, raw) == owner
    assert navigation.CHILD_KIND[owner_kind] == child
    assert navigation.directly_owned(child, raw, owner)
    assert not navigation.directly_owned(child, raw, owner._replace(uid="replacement"))
    assert not navigation.directly_owned(child, raw, owner._replace(namespace="other"))


def test_children_are_uid_matched_across_all_pages():
    owner = navigation.Identity("Deployment", "shop", "web", "original")
    owned = reference("Deployment", "web", "original")
    replica_sets = [
        replicaset("web-old", "rs1", [owned]),
        replicaset("same-name-owner", "other", [reference("Deployment", "web", "replacement")]),
        replicaset("cross-namespace", "other-namespace", [owned], namespace="other"),
        replicaset("web-new", "rs2", [owned]),
    ]
    calls = []

    def list_replicasets(namespace, **kwargs):
        token = kwargs["_continue"]
        calls.append((namespace, token))
        return client.V1ReplicaSetList(items=replica_sets[3:] if token else replica_sets[:3],
                                       metadata=client.V1ListMeta(_continue=None if token else "page2"))

    service = KubeService()
    service._apis = {"apps": SimpleNamespace(list_namespaced_replica_set=list_replicasets)}
    assert [row.name for row in service.list_workload_children(owner)] == ["web-old", "web-new"]
    assert calls == [("shop", None), ("shop", "page2")]
    assert service.list_workload_children(owner._replace(uid="gone")) == []


def test_owner_read_rejects_a_replacement_and_handles_api_errors():
    owner = navigation.Identity("ReplicaSet", "shop", "web", "rs-uid")
    service = KubeService()
    apps = SimpleNamespace(read_namespaced_replica_set=lambda name, ns, **_: replicaset(name, "rs-uid", namespace=ns))
    service._apis = {"apps": apps}
    assert service.read_workload_owner(owner)[0].namespace == "shop"
    with pytest.raises(ValueError, match="referenced UID"):
        service.read_workload_owner(owner._replace(uid="old-uid"))

    def read_missing(*_, **__):
        raise ApiException(status=404)

    def read_forbidden(*_, **__):
        raise ApiException(status=403)

    apps.read_namespaced_replica_set = read_missing
    with pytest.raises(ValueError, match="no longer exists"):
        service.read_workload_owner(owner)
    apps.read_namespaced_replica_set = read_forbidden
    with pytest.raises(ApiException) as exc:
        service.read_workload_owner(owner)
    assert exc.value.status == 403


def test_menu_opens_selected_owner_and_browses_back_to_children(qtbot, monkeypatch):
    rs = map_replicaset(replicaset("web", "rs-uid"))
    pod = PodRow("web-a", "shop", "1/1", "Running", 0, "node", "1d", {
        "metadata": {"name": "web-a", "namespace": "shop", "uid": "pod-uid",
                     "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": "web",
                                          "uid": "rs-uid", "controller": True}]},
    })
    requests = []
    ws = make_ws(qtbot, read_workload_owner=lambda target: requests.append(("owner", target)) or [rs],
                 list_workload_children=lambda target: requests.append(("children", target)) or [pod])
    ws._show_view("pods")
    ws.show()
    ws.pod_table.set_rows([pod])
    chosen_namespace = ws.namespace()

    def click_menu(table, title):
        labels = []

        class Menu(QMenu):
            def exec(self, *_):
                labels.extend(None if action.isSeparator() else action.text() for action in self.actions())
                action = next((action for action in self.actions() if action.text() == title), None)
                if action is not None:
                    action.trigger()

        monkeypatch.setattr("klotse.ui.resource_table.QMenu", Menu)
        index = table.proxy.index(0, 0)
        table._context_menu(table.view.visualRect(index).center())
        assert title in labels
        return labels

    labels = click_menu(ws.pod_table, "Go to ReplicaSet web")
    owner_index = labels.index("Go to ReplicaSet web")
    assert labels[owner_index - 1:owner_index + 4] == [
        None, "Go to ReplicaSet web", "Go to node node", "Go to namespace shop", None,
    ]
    owner_key = ws.foreground_key()
    assert owner_key.startswith("workload-navigation:owner:")
    owner_table = ws.views[owner_key][0]
    qtbot.waitUntil(lambda: owner_table.selected_rows() == [rs])
    assert ws._detail_row is rs

    click_menu(owner_table, "Show owned Pods")
    child_key = ws.foreground_key()
    children = ws.views[child_key][0]
    qtbot.waitUntil(lambda: children.model.rows == [pod])
    assert ws.namespace() == chosen_namespace
    owner = navigation.Identity("ReplicaSet", "shop", "web", "rs-uid")
    assert requests == [("owner", owner), ("children", owner)]
    ws._close_view(ws.tabs.indexOf(children))
    assert child_key not in ws.views


def test_standalone_pod_has_no_owner_action(qtbot):
    ws = make_ws(qtbot)
    pod = PodRow("standalone", "shop", "1/1", "Running", 0, "node", "1d", {
        "metadata": {"name": "standalone", "namespace": "shop", "uid": "pod-uid"},
    })
    assert not any(action.key.startswith("workload-") for action in ws.pod_table.row_actions(pod))


@pytest.mark.parametrize(("status", "expected"), [(None, "No resources found"), (403, "403 Forbidden")])
def test_empty_and_forbidden_child_lists_are_distinct(qtbot, status, expected):
    owner = navigation.Identity("Deployment", "shop", "web", "web-uid")

    def list_children(target):
        assert target == owner
        if status is not None:
            raise ApiException(status=status, reason="Forbidden")
        return []

    ws = make_ws(qtbot, list_workload_children=list_children)
    ws.open_workload_navigation(owner, True)
    key = ws.foreground_key()
    table = ws.views[key][0]
    qtbot.waitUntil(lambda: key not in ws._loading)
    assert expected in table.status.text()
    assert table.model.rows == []
