"""Pods in the form (almost everything is read-only), creating objects, and the form <-> YAML hand-over of pending changes."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s.client import KubeService
from klotse.ui import workload_model as wm
from klotse.ui.edit_dialog import EditDialog
from klotse.ui.workload_editor import WorkloadEditDialog
from klotse.ui.workspace import ClusterWorkspace

POD = {"apiVersion": "v1", "kind": "Pod",
       "metadata": {"name": "web-1", "namespace": "shop", "uid": "u", "labels": {"app": "web"}},
       "spec": {"serviceAccountName": "default", "securityContext": {}, "restartPolicy": "Always",
                "tolerations": [{"key": "node.kubernetes.io/not-ready", "operator": "Exists", "effect": "NoExecute",
                                 "tolerationSeconds": 300}],
                "volumes": [{"name": "kube-api-access", "projected": {"sources": []}}],
                "containers": [{"name": "web", "image": "nginx:1.25", "resources": {}, "terminationMessagePath": "/dev/termination-log",
                                "volumeMounts": [{"name": "kube-api-access", "mountPath": "/var/run/secrets"}]}]},
       "status": {"phase": "Running"}}


def pod_form(qtbot, raw=None):
    dlg = WorkloadEditDialog("Pod", deepcopy(raw or POD))
    qtbot.addWidget(dlg)
    return dlg


def create_form(qtbot, kind, namespace="shop"):
    dlg = WorkloadEditDialog(kind, wm.skeleton(kind, namespace), create=True, namespaces=["default", "shop"])
    qtbot.addWidget(dlg)
    return dlg


# ---- a running Pod ----------------------------------------------------------------------
def test_a_running_pod_comes_back_identical_when_nothing_changes(qtbot):
    dlg = pod_form(qtbot)
    dlg._save()
    assert dlg.result_body == POD


def test_a_pod_form_shows_only_what_a_pod_has_and_locks_the_rest(qtbot):
    dlg = pod_form(qtbot)
    assert [dlg.sections.tabText(i) for i in range(3)] == ["Pod", "Pod settings", "Containers"]
    assert [dlg.pod.sub.tabText(i) for i in range(dlg.pod.sub.count())][-1].startswith("Security")
    assert "Pod labels" not in [dlg.pod.sub.tabText(i) for i in range(dlg.pod.sub.count())]
    assert dlg.strategy is None and dlg.running and "can only change" in dlg.head.text()
    form = dlg.forms[0]
    assert form.image.isEnabled() and not form.pull.isEnabled() and not form.command.isEnabled()
    assert not form.sub.widget(2).isEnabled()  # environment
    assert not dlg.pod.service_account.isEnabled() and not dlg.pod.volumes.isEnabled()
    assert dlg.pod.tolerations.isEnabled() and dlg.deadline.isEnabled()


def test_a_running_pod_changes_only_what_kubernetes_allows(qtbot):
    dlg = pod_form(qtbot)
    dlg.forms[0].image.setText("nginx:1.26")
    dlg.deadline.setText("600")
    dlg.pod.tolerations._add()
    dlg.pod.tolerations.table.item(1, 0).setText("dedicated")
    dlg.pod.tolerations.table.item(1, 1).setText("Exists")
    dlg.labels.key_box.setEditText("tier")
    dlg.labels.value_edit.setText("front")
    dlg.labels._add()
    dlg._save()
    body = dlg.result_body
    assert body["spec"]["containers"][0]["image"] == "nginx:1.26" and body["spec"]["activeDeadlineSeconds"] == 600
    assert body["spec"]["tolerations"][1] == {"key": "dedicated", "operator": "Exists"}
    assert body["metadata"]["labels"] == {"app": "web", "tier": "front"}
    # everything else is exactly as the server returned it, including the defaulted empty fields
    assert body["spec"]["securityContext"] == {} and body["spec"]["containers"][0]["resources"] == {}
    assert body["spec"]["volumes"] == POD["spec"]["volumes"] and body["status"] == POD["status"]


# ---- creating ----------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["Deployment", "DaemonSet", "StatefulSet", "Pod"])
def test_skeletons_have_one_container_and_the_namespace(kind):
    sk = wm.skeleton(kind, "shop")
    assert sk["kind"] == kind and sk["metadata"] == {"name": "", "namespace": "shop"}
    assert [c["name"] for _f, _i, c in wm.container_lists(sk, kind)] == ["app"]
    assert ("replicas" in sk["spec"]) == (kind in ("Deployment", "StatefulSet"))


def test_a_new_object_needs_a_valid_name_and_namespace(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = create_form(qtbot, "Deployment")
    assert dlg.windowTitle() == "New Deployment" and dlg.namespace_edit.currentText() == "shop"
    dlg._save()
    assert dlg.result_body is None and "Enter a name" in shown[0]
    dlg.name_edit.setText("Bad_Name")
    assert any("lowercase" in p for p in wm.validate("Deployment", dlg.values(), dlg._check_raw()))
    dlg.name_edit.setText("api")
    dlg.namespace_edit.setCurrentText("")
    assert any("namespace" in p for p in wm.validate("Deployment", dlg.values(), dlg._check_raw()))


def test_a_new_deployment_ties_its_selector_and_labels_to_the_name(qtbot):
    dlg = create_form(qtbot, "Deployment")
    dlg.name_edit.setText("api")
    dlg.forms[0].container_name.setText("server")
    dlg.forms[0].image.setText("reg/api:2")
    dlg.replicas.setValue(3)
    dlg.pod.template_labels.table.selectAll()
    dlg.pod.template_labels._remove()  # even without the app label the selector still matches
    dlg._save()
    body = dlg.result_body
    assert body["metadata"] == {"name": "api", "namespace": "shop"}
    assert body["spec"]["replicas"] == 3 and body["spec"]["selector"] == {"matchLabels": {"app": "api"}}
    assert body["spec"]["template"]["metadata"]["labels"] == {"app": "api"}
    assert body["spec"]["template"]["spec"]["containers"] == [{"name": "server", "image": "reg/api:2"}]
    assert "strategy" not in body["spec"]  # nothing was chosen: Kubernetes' default applies


def test_a_new_statefulset_gets_its_service_name_and_a_new_pod_no_selector(qtbot):
    dlg = create_form(qtbot, "StatefulSet")
    dlg.name_edit.setText("db")
    dlg._save()
    assert dlg.result_body["spec"]["serviceName"] == "db"
    dlg = create_form(qtbot, "StatefulSet")
    dlg.name_edit.setText("db")
    dlg.service_edit.setText("db-headless")
    dlg._save()
    assert dlg.result_body["spec"]["serviceName"] == "db-headless"
    pod = create_form(qtbot, "Pod")
    pod.name_edit.setText("job-1")
    pod.deadline.setText("30")
    pod._save()
    assert pod.result_body["metadata"]["name"] == "job-1" and "selector" not in pod.result_body["spec"]
    assert pod.result_body["spec"]["activeDeadlineSeconds"] == 30 and not pod.running
    assert pod.forms[0].pull.isEnabled()  # a new pod is fully editable


# ---- the form and the YAML editor share the pending changes --------------------------------------
def test_edit_as_yaml_carries_the_form_values_over(qtbot):
    dlg = WorkloadEditDialog("Deployment", {"metadata": {"name": "web", "namespace": "shop"},
                                            "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "c", "image": "i:1"}]}}}})
    qtbot.addWidget(dlg)
    dlg.replicas.setValue(4)
    dlg.forms[0].image.setText("i:2")
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.result() == QDialog.Rejected
    assert dlg.pending["spec"]["replicas"] == 4 and dlg.pending["spec"]["template"]["spec"]["containers"][0]["image"] == "i:2"


def test_a_form_that_cannot_be_turned_into_a_manifest_asks_before_switching(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[1]) or QMessageBox.No)
    dlg = pod_form(qtbot)
    dlg.deadline.setText("soon")
    assert dlg.pending_body() is None
    dlg._as_yaml()
    assert asked and not dlg.wants_yaml
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.pending is None  # leaving the form's changes behind, as agreed


def test_pending_changes_are_shown_even_when_the_form_is_not_valid_yet(qtbot):
    dlg = create_form(qtbot, "Deployment")  # the name is still empty
    dlg.forms[0].image.setText("x:1")
    assert dlg.pending_body()["spec"]["template"]["spec"]["containers"][0]["image"] == "x:1"


def test_the_yaml_editor_goes_back_to_the_form_with_its_text(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = EditDialog("Edit", {"metadata": {"name": "a"}}, can_form=True)
    qtbot.addWidget(dlg)
    assert not dlg.form_button.isHidden() or dlg.form_button.isVisibleTo(dlg)
    dlg.editor.setPlainText("metadata:\n  name: b\nspec: {replicas: 2}\n")
    dlg._to_form()
    assert dlg.wants_form and dlg.pending_body == {"metadata": {"name": "b"}, "spec": {"replicas": 2}}
    broken = EditDialog("Edit", {"metadata": {"name": "a"}}, can_form=True)
    qtbot.addWidget(broken)
    broken.editor.setPlainText("metadata: [")
    broken._to_form()
    assert not broken.wants_form and shown  # invalid YAML stays in the YAML editor
    assert EditDialog("Edit", {"metadata": {"name": "a"}}).form_button.isHidden()


# ---- the workspace flow -------------------------------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(list_secret_names=lambda ns: [], list_configmap_names=lambda ns: [], **service), "ctx")
    qtbot.addWidget(ws)
    ws.refresh = lambda: None
    return ws


def test_form_to_yaml_to_form_to_save_passes_the_changes_along(qtbot, monkeypatch):
    saved, forms, yamls = [], [], []
    ws = make_ws(qtbot, replace=lambda kind, body: saved.append((kind, body)))

    class Form:
        def __init__(self, kind, raw, parent=None, **kw):
            forms.append(deepcopy(raw))
            first = len(forms) == 1
            self.wants_yaml, self.pending = first, {"metadata": {"name": "web"}, "spec": {"replicas": 7}} if first else None
            self.result_body = None if first else {"metadata": {"name": "web"}, "spec": {"replicas": 9}}

        def exec(self):
            return QDialog.Rejected if self.wants_yaml else QDialog.Accepted

        def deleteLater(self):
            pass

    class Yaml:
        Accepted = 1

        def __init__(self, title, raw, parent=None, note="", can_form=False):
            yamls.append((title, deepcopy(raw), can_form))
            self.wants_form, self.pending_body = True, {"metadata": {"name": "web"}, "spec": {"replicas": 8}}

        def exec(self):
            return 0

    monkeypatch.setattr("klotse.ui.workspace.WorkloadEditDialog", Form)
    monkeypatch.setattr("klotse.ui.workspace.EditDialog", Yaml)
    from klotse.k8s.resources import Row
    ws.edit_resource("Deployment", Row("web", "shop", {"metadata": {"name": "web", "namespace": "shop"}, "spec": {"replicas": 1}}))
    qtbot.waitUntil(lambda: bool(saved), timeout=3000)
    assert yamls[0][1]["spec"]["replicas"] == 7 and yamls[0][2] is True  # the form's pending changes, with a way back
    assert forms[1]["spec"]["replicas"] == 8  # the YAML as typed, shown in the form
    assert saved == [("Deployment", {"metadata": {"name": "web"}, "spec": {"replicas": 9}})]


def test_a_yaml_the_form_cannot_show_stays_in_the_yaml_editor(qtbot, monkeypatch):
    warned, yamls = [], []
    ws = make_ws(qtbot)

    class Form:
        def __init__(self, kind, raw, parent=None, **kw):
            if yamls:
                raise ValueError("containers is not a list")
            self.wants_yaml, self.pending = True, None

        def exec(self):
            return QDialog.Rejected

        def deleteLater(self):
            pass

    class Yaml:
        Accepted = 1

        def __init__(self, title, raw, parent=None, note="", can_form=False):
            yamls.append(raw)
            self.wants_form, self.pending_body = len(yamls) == 1, {"metadata": {"name": "web"}}

        def exec(self):
            return 0

    monkeypatch.setattr("klotse.ui.workspace.WorkloadEditDialog", Form)
    monkeypatch.setattr("klotse.ui.workspace.EditDialog", Yaml)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    from klotse.k8s.resources import Row
    ws.edit_resource("Deployment", Row("web", "shop", {"metadata": {"name": "web", "namespace": "shop"}}))
    assert warned and "cannot show" in warned[0] and len(yamls) == 2


def test_new_creates_through_the_service_with_the_chosen_namespace(qtbot, monkeypatch):
    created, seen = [], []
    ws = make_ws(qtbot, create=lambda kind, body: created.append((kind, body)))
    ws.set_namespace("default")

    class Form:
        def __init__(self, kind, raw, parent=None, create=False, namespaces=(), **_kw):
            seen.append((kind, raw["metadata"]["namespace"], create))
            self.wants_yaml, self.result_body = False, {"metadata": {"name": "api", "namespace": "default"}}

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.WorkloadEditDialog", Form)
    ws.create_resource("Deployment")
    qtbot.waitUntil(lambda: bool(created), timeout=3000)
    assert seen == [("Deployment", "default", True)] and created[0][0] == "Deployment"


def test_the_lists_of_creatable_kinds_have_a_new_button(qtbot):
    ws = make_ws(qtbot)
    for key, kind in (("deployments", "Deployment"), ("pods", "Pod"), ("statefulsets", "StatefulSet"), ("daemonsets", "DaemonSet")):
        table = ws.views[key][0]
        assert table.kind == kind and table.new_button is not None, key
    assert ws.views["persistentvolumes"][0].new_button is None and ws.views["replicasets"][0].new_button is None


def test_the_service_creates_each_kind():
    made = []
    api = lambda name: (lambda ns, body: made.append((name, ns, body["metadata"]["name"])) or SimpleNamespace(metadata=SimpleNamespace(name="x", namespace=ns, uid="u", creation_timestamp=None, labels=None, annotations=None, generation=1, resource_version="1"), spec=SimpleNamespace(replicas=1, selector=None, template=SimpleNamespace(spec=SimpleNamespace(containers=[], node_name=None)), service_name="", update_strategy=None, strategy=None), status=SimpleNamespace(replicas=0, ready_replicas=0, available_replicas=0, updated_replicas=0, current_number_scheduled=0, desired_number_scheduled=0, number_ready=0, number_available=0, updated_number_scheduled=0, phase="Pending", container_statuses=None, pod_ip=None, conditions=None, current_replicas=0)))
    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(create_namespaced_pod=api("pod"))
    svc._apps = SimpleNamespace(create_namespaced_deployment=api("deployment"), create_namespaced_stateful_set=api("sts"),
                                create_namespaced_daemon_set=api("ds"))
    svc._apis = {"apps": svc._apps}
    for kind in ("Pod", "Deployment", "StatefulSet", "DaemonSet"):
        try:
            svc.create(kind, {"metadata": {"name": "n", "namespace": "ns"}})
        except AttributeError:
            pass  # the fake API object does not carry every field the row mapper reads; the call itself is what is checked
    assert [m[0] for m in made] == ["pod", "deployment", "sts", "ds"] and all(m[1:] == ("ns", "n") for m in made)
    with pytest.raises(ValueError):
        svc.create("PersistentVolume", {"metadata": {"name": "n"}})
