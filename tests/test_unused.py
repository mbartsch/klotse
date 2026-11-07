import json
from types import SimpleNamespace

from klotse.k8s import unused
from klotse.k8s.client import KubeService
from klotse.ui.unused_dialog import UnusedDialog, age_of
from klotse.ui.workspace import ClusterWorkspace


def obj(name, ns="shop", **meta):
    return {"metadata": {"name": name, "namespace": ns, "creationTimestamp": "2026-01-01T00:00:00Z", **meta}}


def pod(name, ns="shop", **spec):
    return {"metadata": {"name": name, "namespace": ns}, "spec": {"containers": [{"name": "c"}], **spec}}


def names(found):
    return [(r["kind"], r["namespace"], r["name"]) for r in found]


def test_objects_named_by_pods_templates_ingresses_and_accounts_are_used():
    data = {
        "configmaps": [obj(n) for n in ("cm-vol", "cm-env", "cm-from", "cm-proj", "cm-tmpl", "cm-cron", "cm-free")],
        "secrets": [obj(n) for n in ("s-vol", "s-key", "s-pull", "s-tls", "s-anno", "s-sa", "s-free")],
        "pvcs": [obj("data-claim"), obj("free-claim")],
        "pods": [pod("a", volumes=[{"configMap": {"name": "cm-vol"}}, {"secret": {"secretName": "s-vol"}},
                                   {"persistentVolumeClaim": {"claimName": "data-claim"}},
                                   {"projected": {"sources": [{"configMap": {"name": "cm-proj"}}]}}],
                           imagePullSecrets=[{"name": "s-pull"}],
                           containers=[{"name": "c", "envFrom": [{"configMapRef": {"name": "cm-from"}}],
                                        "env": [{"name": "X", "valueFrom": {"configMapKeyRef": {"name": "cm-env"}, "secretKeyRef": {"name": "s-key"}}}]}])],
        "deployments": [{"metadata": {"name": "d", "namespace": "shop"}, "spec": {"replicas": 0, "template": {"spec": {
            "volumes": [{"configMap": {"name": "cm-tmpl"}}], "containers": [{"name": "c"}]}}}}],
        "cronjobs": [{"metadata": {"name": "c", "namespace": "shop"}, "spec": {"jobTemplate": {"spec": {"template": {"spec": {
            "volumes": [{"configMap": {"name": "cm-cron"}}], "containers": [{"name": "c"}]}}}}}}],
        "ingresses": [{"metadata": {"name": "i", "namespace": "shop", "annotations": {"nginx.ingress.kubernetes.io/auth-secret": "s-anno"}},
                       "spec": {"tls": [{"secretName": "s-tls"}]}}],
        "serviceaccounts": [{"metadata": {"name": "sa", "namespace": "shop"}, "secrets": [{"name": "s-sa"}]}],
    }
    assert names(unused.find_unused(data)) == [("ConfigMap", "shop", "cm-free"), ("PersistentVolumeClaim", "shop", "free-claim"), ("Secret", "shop", "s-free")]


def test_namespaces_are_kept_apart_and_cross_namespace_annotation_secrets_count():
    data = {"configmaps": [obj("cfg", "a"), obj("cfg", "b")], "pods": [pod("p", "a", volumes=[{"configMap": {"name": "cfg"}}])],
            "secrets": [obj("cert", "certs")],
            "ingresses": [{"metadata": {"name": "i", "namespace": "web", "annotations": {"nginx.ingress.kubernetes.io/auth-tls-secret": "certs/cert"}}, "spec": {}}]}
    assert names(unused.find_unused(data)) == [("ConfigMap", "b", "cfg")]


def test_what_the_cluster_itself_uses_is_never_listed():
    data = {"configmaps": [obj("kube-root-ca.crt")],
            "secrets": [obj("tok", annotations={"kubernetes.io/service-account.name": "sa"}), obj("sh.helm.release.v1.web.v1", labels={"owner": "helm"})],
            "pvcs": [obj("data-db-0"), obj("data-db-12"), obj("data-other-0"), obj("data-db-x")],
            "statefulsets": [{"metadata": {"name": "db", "namespace": "shop"}, "spec": {"replicas": 0, "volumeClaimTemplates": [{"metadata": {"name": "data"}}]}}]}
    assert names(unused.find_unused(data)) == [("PersistentVolumeClaim", "shop", "data-db-x"), ("PersistentVolumeClaim", "shop", "data-other-0")]


def test_notes_say_who_owns_or_manages_an_unused_object():
    data = {"configmaps": [obj("a", ownerReferences=[{"kind": "Certificate", "name": "web"}]),
                           obj("b", annotations={"meta.helm.sh/release-name": "traefik"}), obj("c"), obj("d", ns="kube-system")]}
    notes = {r["name"]: r["note"] for r in unused.find_unused(data)}
    assert notes["a"] == "owned by Certificate/web" and notes["b"] == "part of Helm release traefik" and notes["c"] == ""
    assert "system namespace" in notes["d"]


def test_summary_counts_per_kind():
    found = unused.find_unused({"configmaps": [obj("x"), obj("y")], "secrets": [obj("z")]})
    assert unused.summary(found, {"ConfigMap": 5, "Secret": 3, "PersistentVolumeClaim": 0}) == \
        "Nothing refers to: 2 of 5 ConfigMaps, 1 of 3 Secrets, 0 of 0 claims."


# ---- the service reads metadata only for ConfigMaps and Secrets, and refuses to judge without the references --------
class MetaList:
    def __init__(self, names):
        self.names, self.headers = names, []

    def __call__(self, *args, limit=500, _continue=None, _request_timeout=None, _preload_content=True, _headers=None):
        self.headers.append(_headers)
        items = [{"metadata": {"name": n, "namespace": "shop"}, "data": {"secret": "must-not-be-kept"}} for n in self.names]
        return SimpleNamespace(data=json.dumps({"items": items, "metadata": {}}).encode())


def unused_service(fail=()):
    service = KubeService()
    pods = [SimpleNamespace(raw=pod("p", volumes=[{"configMap": {"name": "used"}}]))]
    service.list_pods_for_analysis = lambda ns=None, full=False: pods
    for method in ("list_deployments", "list_statefulsets"):
        setattr(service, method, lambda ns=None: [])

    def list_resource(key, ns=None):
        if key in fail:
            raise PermissionError("no")
        return []

    service.list_resource = list_resource
    maps, secrets = MetaList(["used", "unused"]), MetaList(["s"])
    service._core = SimpleNamespace(list_namespaced_config_map=maps, list_config_map_for_all_namespaces=maps,
                                    list_namespaced_secret=secrets, list_secret_for_all_namespaces=secrets)
    return service, maps, secrets


def test_the_service_asks_for_metadata_only_and_keeps_no_secret_data():
    service, maps, secrets = unused_service()
    data, problems, missing = service.unused_data("shop")
    assert problems == [] and missing == []
    assert "PartialObjectMetadataList" in secrets.headers[0]["Accept"] and "PartialObjectMetadataList" in maps.headers[0]["Accept"]
    assert "must-not-be-kept" not in json.dumps(data)
    assert names(unused.find_unused(data)) == [("ConfigMap", "shop", "unused"), ("Secret", "shop", "s")]


def test_a_missing_reference_source_means_no_verdict_but_a_missing_candidate_kind_does_not():
    service, _m, _s = unused_service(fail=("ingresses",))
    _data, problems, missing = service.unused_data(None)
    assert missing == ["ingresses"] and problems == ["ingresses (PermissionError)"]
    service, _m, _s = unused_service(fail=("persistentvolumeclaims",))
    data, problems, missing = service.unused_data(None)
    assert missing == [] and problems == ["pvcs (PermissionError)"] and "pvcs" not in data


# ---- the window ----------------------------------------------------------------------------------------------------
def test_the_dialog_lists_filters_and_goes_to_the_object(qtbot):
    dialog = UnusedDialog("ctx", "namespace shop")
    qtbot.addWidget(dialog)
    rows = unused.find_unused({"configmaps": [obj("cfg-a"), obj("cfg-b")], "secrets": [obj("tok")], "pvcs": [obj("vol")]})
    dialog.show_result(rows, {"ConfigMap": 4, "Secret": 2, "PersistentVolumeClaim": 1}, ["jobs (PermissionError)"])
    assert dialog.table.rowCount() == 4 and "2 of 4 ConfigMaps" in dialog.summary.text() and "jobs (PermissionError)" in dialog.summary.text()
    dialog.kind_boxes["Secret"].setChecked(False)
    assert [dialog.table.isRowHidden(i) for i in range(4)] == [False, False, False, True]  # sorted: 2 ConfigMaps, the claim, the Secret
    dialog.filter.setText("cfg-b")
    assert [dialog.table.isRowHidden(i) for i in range(4)] == [True, False, True, True]
    seen = []
    dialog.go_to_requested.connect(lambda *a: seen.append(a))
    dialog.table.cellDoubleClicked.emit(1, 0)
    assert seen == [("ConfigMap", "shop", "cfg-b")]
    dialog.show_refused(["ingresses"], ["ingresses (PermissionError)"])
    assert dialog.table.rowCount() == 0 and "Cannot tell" in dialog.summary.text()
    assert age_of("2026-01-01T00:00:00Z").endswith("d") and age_of("") == ""


def test_the_workspace_reads_on_a_worker_and_fills_the_dialog(qtbot):
    service = SimpleNamespace(unused_data=lambda namespace=None: ({"configmaps": [obj("cfg")], "secrets": [], "pvcs": [], "pods": []}, [], []))
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_unused()
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 1, timeout=5000)
    assert "1 of 1 ConfigMaps" in dialog.summary.text() and dialog.table.item(0, 2).text() == "cfg"
    service.unused_data = lambda namespace=None: ({}, ["pods (PermissionError)"], ["pods"])
    dialog.reload_button.click()
    qtbot.waitUntil(lambda: "Cannot tell" in dialog.summary.text(), timeout=5000)
    ws.shutdown()
