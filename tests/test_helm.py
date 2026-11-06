"""Helm releases: read from the cluster's storage Secrets (labels for the list, data only for the release asked about), values
hidden until shown, history, and uninstall through the helm command."""
import base64
import gzip
import json
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s import helm
from klotse.k8s.client import KubeService
from klotse.k8s.resources import map_helm_release
from klotse.ui.helm_dialog import HelmReleaseDialog
from klotse.ui.status_tone import status_tone
from klotse.ui.workspace import ClusterWorkspace


def release(name="web", revision=3, status="deployed", config=None, defaults=None, notes="Visit http://example", namespace="shop"):
    return {"name": name, "namespace": namespace, "version": revision,
            "info": {"status": status, "description": "Upgrade complete", "first_deployed": "2026-01-01T00:00:00Z",
                     "last_deployed": f"2026-01-0{revision}T00:00:00Z", "notes": notes},
            "chart": {"metadata": {"name": "nginx", "version": "15.1.0", "appVersion": "1.25.3"},
                      "values": defaults if defaults is not None else {"replicas": 1, "image": {"tag": "1.25"}, "adminPassword": "changeme"}},
            "config": config if config is not None else {"replicas": 3, "image": {"tag": "1.26"}}, "manifest": "kind: Secret"}


def helm_layer(rel):
    return base64.b64encode(gzip.compress(json.dumps(rel).encode()))


def secret_data(rel):
    """What the API returns in Secret.data['release']: Helm's base64 text, base64-encoded again by Kubernetes."""
    return base64.b64encode(helm_layer(rel)).decode()


def configmap_data(rel):
    return helm_layer(rel).decode()  # no Kubernetes layer: the ConfigMap driver stores Helm's own base64 text


# ---- decoding ----------------------------------------------------------------------------------------------------
def test_a_secret_and_a_configmap_release_both_decode():
    rel = release()
    assert helm.decode_release(secret_data(rel)) == rel
    assert helm.decode_release(configmap_data(rel)) == rel
    assert helm.decode_release(base64.b64encode(gzip.compress(json.dumps(rel).encode())).decode()) == rel


@pytest.mark.parametrize("value, message", [
    ("", "not a Helm release"), ("not base64 at all!!", "not a Helm release"),
    (base64.b64encode(b"plain text").decode(), "not a Helm release"),
    (base64.b64encode(gzip.compress(b"[1,2]")).decode(), "no chart or info"),
    (base64.b64encode(gzip.compress(b'{"name": "x"}')).decode(), "no chart or info")])
def test_anything_else_is_refused_with_a_reason(value, message):
    with pytest.raises(helm.HelmError, match=message):
        helm.decode_release(value)


def test_a_release_that_is_too_large_once_decompressed_is_refused(monkeypatch):
    monkeypatch.setattr(helm, "MAX_RELEASE_BYTES", 100)
    with pytest.raises(helm.HelmError, match="too large"):
        helm.decode_release(secret_data(release(notes="x" * 5000)))


# ---- what is shown -----------------------------------------------------------------------------------------------
def test_the_summary_has_the_facts_but_neither_values_nor_manifest():
    s = helm.summarize(release())
    assert s == {"name": "web", "namespace": "shop", "revision": 3, "status": "deployed", "chart": "nginx", "chart_version": "15.1.0",
                 "app_version": "1.25.3", "description": "Upgrade complete", "first_deployed": "2026-01-01T00:00:00Z",
                 "last_deployed": "2026-01-03T00:00:00Z", "notes": "Visit http://example"}
    assert helm.summarize({})["name"] == "" and helm.summarize({"chart": None, "info": None})["chart"] == ""


def test_all_values_are_the_charts_defaults_with_the_users_on_top():
    user, everything = helm.values_of(release())
    assert user == {"replicas": 3, "image": {"tag": "1.26"}}
    assert everything == {"replicas": 3, "image": {"tag": "1.26"}, "adminPassword": "changeme"}


def test_merging_follows_helm_nested_mappings_merge_lists_replace_and_null_removes():
    base = {"a": {"b": 1, "c": 2}, "l": [1, 2], "gone": 1}
    assert helm.deep_merge(base, {"a": {"b": 9}, "l": [3], "gone": None}) == {"a": {"b": 9, "c": 2}, "l": [3]}
    assert base["a"]["b"] == 1  # the input is not changed
    assert helm.deep_merge(None, {"x": 1}) == {"x": 1}


def test_values_that_look_secret_are_masked_wholesale():
    masked = helm.mask_values({"adminPassword": "p", "db": {"password": "x", "host": "h"}, "tls": {"cert": "c", "key": "k"},
                               "apiKey": {"a": 1}, "token": "", "items": [{"secretName": "s", "name": "n"}], "replicas": 3,
                               "monkey": "banana", "keyboard": "us"})
    assert masked["adminPassword"] == "<hidden>" and masked["db"] == {"password": "<hidden>", "host": "h"}
    assert masked["tls"] == {"cert": "<hidden>", "key": "<hidden>"} and masked["apiKey"] == "<hidden>"  # a whole sub-tree under such a key
    assert masked["token"] == ""  # nothing to hide
    assert masked["items"] == [{"secretName": "<hidden>", "name": "n"}] and masked["replicas"] == 3
    assert masked["monkey"] == "banana" and masked["keyboard"] == "us"  # only a name that ends in key is a key


def test_the_list_takes_the_newest_revision_of_each_release_from_labels_alone():
    def item(name, version, status="superseded", owner="helm", namespace="shop"):
        return {"name": f"sh.helm.release.v1.{name}.v{version}", "namespace": namespace, "creationTimestamp": f"2026-01-0{version}T00:00:00Z",
                "labels": {"name": name, "owner": owner, "status": status, "version": str(version)}}
    rows = helm.release_rows([item("web", 1), item("web", 3, "deployed"), item("web", 2), item("api", 1, "failed"),
                              item("web", 1, namespace="other"), item("x", 1, owner="someone"), {"labels": {"owner": "helm"}},
                              {"labels": {"owner": "helm", "name": "bad", "version": "x"}}])
    assert [(r["namespace"], r["name"], r["revision"], r["status"], r["revisions"]) for r in rows] == [
        ("other", "web", 1, "superseded", 1), ("shop", "api", 1, "failed", 1), ("shop", "web", 3, "deployed", 3)]
    row = map_helm_release(rows[2])
    assert (row.name, row.namespace, row.revision, row.status, row.revisions) == ("web", "shop", 3, "deployed", 3) and row.updated


def test_helm_statuses_get_the_right_tone():
    assert status_tone("deployed") == "ok" and status_tone("failed") == "bad"
    assert status_tone("pending-upgrade") == "warn" and status_tone("uninstalling") == "warn" and status_tone("superseded") is None


def test_the_uninstall_command_has_an_explicit_context_and_only_safe_names():
    assert helm.helm_uninstall_args("prod", "shop", "web") == ["helm", "--kube-context", "prod", "uninstall", "web", "--namespace", "shop"]
    assert helm.helm_uninstall_args("prod", "shop", "web", "/k/c")[:3] == ["helm", "--kubeconfig", "/k/c"]
    for bad in ("", "a b", "x;rm -rf", "-flag", "a/b"):
        with pytest.raises(helm.HelmError):
            helm.helm_uninstall_args("prod", "shop", bad)


# ---- the service -------------------------------------------------------------------------------------------------
def labels_page(*releases, cont=None):
    items = [{"metadata": {"name": f"sh.helm.release.v1.{n}.v{v}", "namespace": ns, "creationTimestamp": "2026-01-01T00:00:00Z",
                           "labels": {"name": n, "owner": "helm", "status": st, "version": str(v)}}} for n, v, st, ns in releases]
    return json.dumps({"items": items, "metadata": {"continue": cont} if cont else {}})


def test_the_list_asks_for_metadata_only_and_pages_through_every_namespace():
    seen = []
    pages = [labels_page(("web", 1, "superseded", "shop"), cont="c1"), labels_page(("web", 2, "deployed", "shop"), ("api", 1, "failed", "dev"))]

    def listing(**kw):
        seen.append(kw)
        return SimpleNamespace(data=pages.pop(0))

    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(list_secret_for_all_namespaces=listing)
    rows = svc.list_helm_releases()
    assert [(r.namespace, r.name, r.revision, r.status) for r in rows] == [("dev", "api", 1, "failed"), ("shop", "web", 2, "deployed")]
    assert seen[0]["label_selector"] == "owner=helm" and seen[0]["_preload_content"] is False  # no release data is requested
    assert "PartialObjectMetadataList" in seen[0]["_headers"]["Accept"] and seen[1]["_continue"] == "c1"


def test_one_namespace_uses_the_namespaced_call():
    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(list_namespaced_secret=lambda ns, **kw: SimpleNamespace(data=labels_page(("web", 1, "deployed", ns))),
                                list_secret_for_all_namespaces=lambda **kw: pytest.fail("not all namespaces"))
    assert [r.namespace for r in svc.list_helm_releases("shop")] == ["shop"]


def secret(rel=None, data=None):
    return SimpleNamespace(data={"release": data if data is not None else secret_data(rel)})


def detail_service(secrets):
    seen = []
    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(list_namespaced_secret=lambda ns, **kw: seen.append((ns, kw)) or SimpleNamespace(
        items=secrets, metadata=SimpleNamespace(_continue=None)))
    return svc, seen


def test_a_release_in_detail_has_summary_values_and_history_newest_first():
    svc, seen = detail_service([secret(release(revision=1, status="superseded")), secret(release(revision=3)), secret(release(revision=2, status="superseded"))])
    got = svc.helm_release("shop", "web")
    assert seen[0][1]["label_selector"] == "owner=helm,name=web"
    assert got["summary"]["revision"] == 3 and [h["revision"] for h in got["history"]] == [3, 2, 1] and got["skipped"] == 0
    assert got["values_user"] == {"replicas": 3, "image": {"tag": "1.26"}} and got["values_all"]["adminPassword"] == "changeme"
    assert "manifest" not in json.dumps(got)  # the rendered manifest (which can hold Secrets) is not kept


def test_unreadable_revisions_are_counted_and_none_readable_is_an_error():
    svc, _ = detail_service([secret(release()), secret(data="garbage")])
    assert svc.helm_release("shop", "web")["skipped"] == 1
    svc, _ = detail_service([secret(data="garbage")])
    with pytest.raises(helm.HelmError, match="No readable revision"):
        svc.helm_release("shop", "web")


@pytest.mark.parametrize("name", ["", "a,b", "x=y", "a b"])
def test_a_name_that_could_alter_the_selector_is_refused(name):
    svc, seen = detail_service([])
    with pytest.raises(helm.HelmError, match="not a release name"):
        svc.helm_release("shop", name)
    assert seen == []


# ---- the dialog --------------------------------------------------------------------------------------------------
def data(skipped=0):
    rel3, rel2 = release(), release(revision=2, status="superseded")
    user, everything = helm.values_of(rel3)
    return {"summary": helm.summarize(rel3), "values_user": user, "values_all": everything,
            "history": [helm.summarize(rel3), helm.summarize(rel2)], "skipped": skipped}


def dialog(qtbot, can_change=True, skipped=0):
    d = HelmReleaseDialog("ctx", data(skipped), can_change=can_change)
    qtbot.addWidget(d)
    return d


def test_the_summary_tab_shows_the_facts_and_the_notes(qtbot):
    d = dialog(qtbot)
    assert "Status: deployed" in d.facts.text() and "Chart: nginx 15.1.0" in d.facts.text() and "App version: 1.25.3" in d.facts.text()
    assert d.notes.toPlainText() == "Visit http://example"


def test_the_history_tab_lists_the_revisions(qtbot):
    d = dialog(qtbot)
    assert d.history.rowCount() == 2 and d.history.item(0, 0).text() == "3" and d.history.item(1, 1).text() == "superseded"
    assert d.history.item(0, 3).text() == "nginx 15.1.0"


def test_values_are_hidden_until_shown_and_secret_looking_ones_until_revealed(qtbot):
    d = dialog(qtbot)
    assert d.values.toPlainText() == "" and d.values.placeholderText().startswith("Values are hidden")
    d.show_button.click()
    assert "replicas: 3" in d.values.toPlainText() and d.show_button.text() == "Hide values"
    d.which.setCurrentIndex(1)  # all values
    assert "adminPassword: <hidden>" in d.values.toPlainText() and "changeme" not in d.values.toPlainText()
    d.reveal.setChecked(True)
    assert "adminPassword: changeme" in d.values.toPlainText()
    d.reveal.setChecked(False)
    d.show_button.click()  # hide again: nothing stays on screen
    assert d.values.toPlainText() == "" and d.show_button.text() == "Show values"


def test_no_values_say_so(qtbot):
    d = HelmReleaseDialog("ctx", {**data(), "values_user": {}}, can_change=True)
    qtbot.addWidget(d)
    d.show_button.click()
    assert "no values" in d.values.toPlainText()


def test_uninstall_is_offered_unless_the_cluster_is_read_only_and_unreadable_revisions_are_noted(qtbot):
    d = dialog(qtbot)
    got = []
    d.uninstall_requested.connect(lambda: got.append(True))
    d.uninstall_button.click()
    assert got == [True]
    ro = dialog(qtbot, can_change=False, skipped=2)
    assert not ro.uninstall_button.isEnabled() and "read-only" in ro.uninstall_button.toolTip()
    ro.show()
    assert any("2 revision(s) could not be read" in label.text() for label in ro.findChildren(type(ro.status)))


# ---- the workspace -----------------------------------------------------------------------------------------------
def row(name="web", namespace="shop"):
    return SimpleNamespace(name=name, namespace=namespace, raw={})


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    ws.refresh = lambda: None
    return ws


def test_the_helm_view_lists_releases_from_the_service(qtbot):
    seen = []
    ws = make_ws(qtbot, list_helm_releases=lambda ns: seen.append(ns) or [map_helm_release(
        {"name": "web", "namespace": "shop", "revision": 3, "status": "deployed", "updated": "2026-01-01T00:00:00Z", "revisions": 3})])
    table = ws.views["helm-releases"][0]
    assert table.kind == "HelmRelease" and [c[1] for c in table.base_columns] == ["name", "namespace", "revision", "status", "updated", "revisions"]
    assert ws.loaders["helm-releases"][1] is True and table.DOUBLE_CLICK["HelmRelease"] == "helm-details"
    ws.open_view("helm-releases")
    qtbot.waitUntil(lambda: table.model.rowCount() == 1, timeout=5000)
    assert seen == ["default"] and table.customize_button is None


def test_details_open_a_dialog_from_the_releases_data(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(HelmReleaseDialog, "exec", lambda self: shown.append(self.history.rowCount()) or QDialog.Accepted)
    ws = make_ws(qtbot, helm_release=lambda ns, name: data())
    ws._helm_action("helm-details", row())
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert shown == [2]


def runner(returncode=0, stdout="release \"web\" uninstalled", stderr="", error=None):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        if error is not None:
            raise error
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    run.calls = calls
    return run


def answer(monkeypatch, button):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: asked.append(a[2]) or button))
    return asked


def test_uninstall_asks_first_and_runs_helm_with_the_clusters_context(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws.helm_runner = runner()
    asked = answer(monkeypatch, QMessageBox.No)
    ws.uninstall_helm_release(row())
    assert ws.helm_runner.calls == [] and "Cluster: ctx" in asked[0] and "shop/web" in asked[0]
    answer(monkeypatch, QMessageBox.Yes)
    refreshed = []
    ws.refresh = lambda: refreshed.append(True)
    ws.uninstall_helm_release(row())
    qtbot.waitUntil(lambda: bool(refreshed), timeout=5000)
    [(argv, kw)] = ws.helm_runner.calls
    assert argv == ["helm", "--kube-context", "ctx", "uninstall", "web", "--namespace", "shop"] and kw["timeout"] == 300 and kw["capture_output"]


def test_a_dialog_closes_after_a_successful_uninstall(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws.helm_runner = runner()
    answer(monkeypatch, QMessageBox.Yes)
    d = ws.helm_dialog(row(), data())
    qtbot.addWidget(d)
    d.show()
    d.uninstall_button.click()
    qtbot.waitUntil(lambda: d.result() == QDialog.Accepted, timeout=5000)


@pytest.mark.parametrize("kwargs, text", [({"error": FileNotFoundError()}, "helm command was not found"),
                                          ({"error": subprocess.TimeoutExpired("helm", 300)}, "did not finish"),
                                          ({"returncode": 1, "stdout": "", "stderr": "Error: uninstall: Release not loaded: web\nsecond"}, "second")])
def test_helm_failures_are_explained_in_the_dialog(qtbot, monkeypatch, kwargs, text):
    ws = make_ws(qtbot)
    ws.helm_runner = runner(**kwargs)
    answer(monkeypatch, QMessageBox.Yes)
    d = ws.helm_dialog(row(), data())
    qtbot.addWidget(d)
    d.uninstall_button.click()
    qtbot.waitUntil(lambda: text in d.status.text(), timeout=5000)
    assert d.status.text().startswith("✖") and d.result() != QDialog.Accepted


def test_a_read_only_cluster_cannot_uninstall_but_can_look(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws.helm_runner = runner()
    ws.set_read_only(True)
    said = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a: said.append(a[2])))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: pytest.fail("no question while read-only")))
    ws.uninstall_helm_release(row())
    assert ws.helm_runner.calls == [] and "read-only" in said[0]
    d = ws.helm_dialog(row(), data())
    qtbot.addWidget(d)
    assert not d.uninstall_button.isEnabled()


def test_a_release_name_helm_would_not_have_made_is_refused_before_anything_runs(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws.helm_runner = runner()
    errors = []
    ws._on_error = errors.append
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: pytest.fail("refused before asking")))
    ws.uninstall_helm_release(row("a; rm -rf /"))
    assert ws.helm_runner.calls == [] and "not a release name" in errors[0]
