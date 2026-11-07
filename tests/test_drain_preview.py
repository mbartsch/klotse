from klotse.k8s import drain_plan as dp
from klotse.ui.node_dialogs import DrainDialog


def pod(name, ns="shop", owner=None, labels=None, phase="Running", annotations=None, volumes=None):
    meta = {"name": name, "namespace": ns, "labels": labels or {}}
    if owner:
        meta["ownerReferences"] = [{"kind": owner, "name": "x", "controller": True}]
    if annotations:
        meta["annotations"] = annotations
    return {"metadata": meta, "spec": {"volumes": volumes or []}, "status": {"phase": phase}}


def budget(name, selector, allowed, ns="shop"):
    return {"metadata": {"name": name, "namespace": ns}, "spec": {"selector": selector},
            "status": {"disruptionsAllowed": allowed}}


def actions(entries):
    return {e["name"]: e["action"] for e in entries}


def test_default_filters_follow_kubectl():
    pods = [pod("web", owner="ReplicaSet"), pod("agent", owner="DaemonSet"), pod("lone"), pod("done", phase="Succeeded"),
            pod("static", annotations={"kubernetes.io/config.mirror": "abc"}, owner="Node"),
            pod("cache", owner="ReplicaSet", volumes=[{"name": "c", "emptyDir": {}}])]
    got = actions(dp.plan(pods, []))
    assert got == {"web": dp.EVICT, "agent": dp.SKIP, "lone": dp.STOP, "done": dp.DELETE, "static": dp.SKIP, "cache": dp.STOP}


def test_options_lift_the_refusals():
    pods = [pod("agent", owner="DaemonSet"), pod("lone"), pod("cache", owner="ReplicaSet", volumes=[{"emptyDir": {}}])]
    assert actions(dp.plan(pods, [], ignore_daemonsets=False)) ["agent"] == dp.STOP
    got = actions(dp.plan(pods, [], force=True, delete_emptydir=True))
    assert got == {"agent": dp.SKIP, "lone": dp.EVICT, "cache": dp.EVICT}


def test_budget_with_too_few_disruptions_is_named():
    pods = [pod("a", owner="ReplicaSet", labels={"app": "api"}), pod("b", owner="ReplicaSet", labels={"app": "api"}),
            pod("c", owner="ReplicaSet", labels={"app": "web"})]
    budgets = [budget("api", {"matchLabels": {"app": "api"}}, 1), budget("web", {"matchLabels": {"app": "web"}}, 1),
               budget("other-ns", {"matchLabels": {"app": "api"}}, 0, ns="elsewhere")]
    entries = {e["name"]: e for e in dp.plan(pods, budgets)}
    assert "api allows 1 disruption for 2 pods" in entries["a"]["pdb"] and entries["a"]["pdb"] == entries["b"]["pdb"]
    assert entries["c"]["pdb"] is None
    assert "Budgets that may hold the drain up" in dp.summary(list(entries.values()))


def test_selectors():
    labels = {"app": "api", "tier": "be"}
    assert dp.selector_matches({}, labels) and not dp.selector_matches(None, labels)
    assert dp.selector_matches({"matchExpressions": [{"key": "tier", "operator": "In", "values": ["be"]}]}, labels)
    assert not dp.selector_matches({"matchExpressions": [{"key": "tier", "operator": "NotIn", "values": ["be"]}]}, labels)
    assert dp.selector_matches({"matchExpressions": [{"key": "x", "operator": "DoesNotExist"}]}, labels)
    assert not dp.selector_matches({"matchExpressions": [{"key": "x", "operator": "Exists"}]}, labels)


def test_dialog_previews_and_rerenders_with_the_options(qtbot):
    asked = []
    data = ([pod("lone"), pod("web", owner="ReplicaSet")], [])
    dialog = DrainDialog("ctx", "n1", preview_loader=lambda ok, bad: (asked.append(1), ok(data)))
    qtbot.addWidget(dialog)
    dialog.preview_button.click()
    text = dialog.preview_text.toPlainText()
    assert "shop/web: evicted" in text and "REFUSED" in text and "shop/lone" in text
    dialog.force.setChecked(True)
    assert "REFUSED" not in dialog.preview_text.toPlainText() and asked == [1]


def test_dialog_shows_a_failed_preview_and_works_without_a_loader(qtbot):
    dialog = DrainDialog("ctx", "n1", preview_loader=lambda ok, bad: bad("boom"))
    qtbot.addWidget(dialog)
    dialog.preview_button.click()
    assert "boom" in dialog.preview_text.toPlainText() and dialog.preview_button.isEnabled()
    plain = DrainDialog("ctx", "n1")
    qtbot.addWidget(plain)
    assert not plain.preview_button.isVisibleTo(plain) and "drain" in plain.command()
