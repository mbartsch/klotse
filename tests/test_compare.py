from types import SimpleNamespace

from PySide6.QtWidgets import QMenu

from klotse.k8s import compare
from klotse.ui.compare_dialog import CompareDialog, render
from klotse.ui.resource_table import ResourceTable
from klotse.ui.workspace import ClusterWorkspace


def dep(name, image, replicas=2, ns="shop", uid="u"):
    return SimpleNamespace(name=name, namespace=ns, raw={
        "metadata": {"name": name, "namespace": ns, "uid": uid, "resourceVersion": "9", "generation": 3,
                     "creationTimestamp": "t", "managedFields": [{"x": 1}],
                     "annotations": {compare.LAST_APPLIED: "{...}"}},
        "spec": {"replicas": replicas, "image": image}, "status": {"readyReplicas": replicas}})


def test_cleaned_drops_noise_status_and_optionally_identity():
    raw = dep("a", "x:1").raw
    body = compare.cleaned(raw)
    assert body["metadata"] == {"name": "a", "namespace": "shop"} and "status" not in body and body["spec"]["image"] == "x:1"
    assert "status" in compare.cleaned(raw, status=True)
    assert "uid" in compare.cleaned(raw, noise=False)["metadata"]
    assert compare.cleaned(raw, identity=True)["metadata"] == {}
    assert raw["metadata"]["uid"] == "u"  # the original is untouched


def test_diff_and_stats():
    lines = compare.diff_lines("a: 1\nb: 2\n", "a: 1\nb: 3\n", "L", "R")
    assert compare.stats(lines) == (1, 1) and lines[0] == "--- L" and lines[1] == "+++ R"
    assert compare.diff_lines("same\n", "same\n", "L", "R") == []


def test_render_escapes_cluster_text_and_keeps_the_signs():
    html = render(["--- a", "+++ b", "@@ -1 +1 @@", "-image: <script>x</script>", "+image: y"])
    assert "<script>" not in html and "&lt;script&gt;" in html and "-image:" in html and "+image: y" in html


def test_dialog_shows_only_real_differences_and_reacts_to_options(qtbot):
    dialog = CompareDialog("ctx", "Deployment", dep("a", "x:1", uid="u1"), dep("b", "x:2", uid="u2"))
    qtbot.addWidget(dialog)
    text = dialog.view.toPlainText()
    assert "-  image: x:1" in text and "+  image: x:2" in text and "uid" not in text and "name:" not in text
    assert "1 line only on the left" in dialog.summary.text()
    dialog.noise.setChecked(False)
    assert "uid: u1" in dialog.view.toPlainText()
    dialog.noise.setChecked(True)
    dialog.identity.setChecked(False)
    assert "name: a" in dialog.view.toPlainText()
    same = CompareDialog("ctx", "Deployment", dep("a", "x:1"), dep("b", "x:1"))
    qtbot.addWidget(same)
    assert "No differences" in same.summary.text()


def test_menu_offers_compare_for_exactly_two_rows(qtbot):
    table = ResourceTable([("Name", "name")], "Deployment")
    qtbot.addWidget(table)
    got = []
    table.compare_requested.connect(lambda kind, rows: got.append((kind, [r.name for r in rows])))
    menu = QMenu()
    table._bulk_menu(menu, [dep("a", "x"), dep("b", "x")])
    actions = {a.text(): a for a in menu.actions() if not a.isSeparator()}
    actions["Compare these two…"].trigger()
    assert got == [("Deployment", ["a", "b"])]
    three = QMenu()
    table._bulk_menu(three, [dep("a", "x"), dep("b", "x"), dep("c", "x")])
    assert "Compare these two…" not in [a.text() for a in three.actions()]


def test_workspace_opens_the_dialog(qtbot, monkeypatch):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    seen = []
    monkeypatch.setattr(CompareDialog, "exec", lambda self: seen.append(self.windowTitle()))
    ws.compare_objects("Deployment", [dep("a", "x"), dep("b", "y")])
    assert seen == ["Compare Deployments"]
