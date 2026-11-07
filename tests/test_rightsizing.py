from types import SimpleNamespace

from klotse.k8s import rightsizing as rs
from klotse.k8s.models import PodRow
from klotse.ui.rightsizing_dialog import RightSizingDialog, cells
from klotse.ui.workspace import ClusterWorkspace


def pod(name, ns="shop", cpu=("500m", "1"), mem=("512Mi", "1Gi"), used=(0.4, 400.0), owner=("ReplicaSet", "api-7d9f"), phase="Running"):
    resources = {}
    if cpu[0] or mem[0]:
        resources["requests"] = {k: v for k, v in (("cpu", cpu[0]), ("memory", mem[0])) if v}
    if cpu[1] or mem[1]:
        resources["limits"] = {k: v for k, v in (("cpu", cpu[1]), ("memory", mem[1])) if v}
    meta = {"name": name, "namespace": ns}
    if owner:
        meta["ownerReferences"] = [{"kind": owner[0], "name": owner[1], "controller": True}]
    raw = {"metadata": meta, "spec": {"containers": [{"name": "c", "resources": resources}]}, "status": {"phase": phase}}
    return PodRow(name, ns, "1/1", phase, 0, "n1", "1d", raw, used[0], used[1])


def by_name(rows):
    return {r["name"]: r for r in rows}


def test_owner_names_the_deployment_cronjob_or_the_bare_pod():
    assert rs.owner_of(pod("a", owner=("ReplicaSet", "api-7d9f"))) == ("Deployment", "api")
    assert rs.owner_of(pod("a", owner=("StatefulSet", "db"))) == ("StatefulSet", "db")
    assert rs.owner_of(pod("a", owner=("Job", "nightly-29012345"))) == ("CronJob", "nightly")
    assert rs.owner_of(pod("a", owner=("Job", "one-off"))) == ("Job", "one-off")
    assert rs.owner_of(pod("lone", owner=None)) == ("Pod", "lone")


def test_rounding_and_texts():
    assert rs.round_up(0.123, rs.CPU_STEP) - 0.13 < 1e-9 and rs.round_up(40.0, rs.MEM_STEP) == 48.0
    assert rs.cpu_text(0.25) == "250m" and rs.cpu_text(2) == "2" and rs.cpu_text(-1) == "" and rs.cpu_text(None) == ""
    assert rs.mem_text(512) == "512Mi" and rs.mem_text(2048) == "2.0Gi" and rs.mem_text(-1) == ""


def test_verdicts_for_one_resource():
    judge = lambda request, limit, used: rs.judge(request, limit, used, rs.CPU_FLOOR, rs.CPU_STEP)
    assert judge(0.5, 1.0, 0.25) == (rs.OK, None)                     # 50% of the request
    assert judge(0.5, 1.0, 0.05) == (rs.OVER, 0.07)                   # 0.05 * 1.25 = 0.0625, rounded up to 10m steps
    assert judge(0.5, 1.0, 0.05)[0] == rs.OVER
    assert judge(0.5, 1.0, 0.6)[0] == rs.UNDER                        # above 110% of the request
    assert judge(0.5, 1.0, 0.95)[0] == rs.NEAR                        # above 90% of the limit
    assert judge(0.0, 0.0, 0.2) == (rs.NO_REQUEST, 0.25)
    assert judge(0.5, 1.0, -1.0) == (rs.NO_DATA, None)
    assert judge(0.0, 0.0, 0.0)[1] == rs.CPU_FLOOR                    # never advise below the floor
    assert judge(0.02, 0.0, 0.001) == (rs.OK, None)                   # already near the floor: not "over"


def test_workloads_are_grouped_and_use_the_largest_pod():
    pods = [pod("api-1", used=(0.05, 60.0)), pod("api-2", used=(0.08, 100.0)), pod("api-3", used=(0.02, 40.0)),
            pod("db-0", owner=("StatefulSet", "db"), cpu=("1", "2"), mem=("1Gi", "2Gi"), used=(0.9, 900.0))]
    rows = by_name(rs.analyze(pods))
    api = rows["api"]
    assert api["kind"] == "Deployment" and api["pods"] == 3 and api["cpu_used"] == 0.08 and api["mem_used"] == 100.0
    assert api["cpu_verdict"] == rs.OVER and api["mem_verdict"] == rs.OVER
    assert abs(api["cpu_suggest"] - 0.10) < 1e-9 and api["mem_suggest"] == 128.0  # 0.08*1.25 -> 100m; 100*1.25=125 -> 128Mi
    assert abs(api["cpu_free"] - (0.5 - 0.10) * 3) < 1e-9 and abs(api["mem_free"] - (512 - 128) * 3 / 1024) < 1e-9
    assert rows["db"]["cpu_verdict"] == rs.OK and rows["db"]["cpu_free"] == 0.0


def test_finished_pods_are_ignored_and_attention_comes_first():
    pods = [pod("fine", owner=("ReplicaSet", "ok-1"), used=(0.25, 256.0)), pod("hog", owner=("ReplicaSet", "hog-1"), used=(0.99, 1000.0)),
            pod("done", phase="Succeeded", owner=("Job", "j"))]
    rows = rs.analyze(pods)
    assert [r["name"] for r in rows] == ["hog", "ok"] and not any(r["name"] == "j" for r in rows)
    assert rows[0]["cpu_verdict"] == rs.NEAR and rs.needs_attention(rows[0]) and not rs.needs_attention(rows[1])


def test_summary_text():
    assert rs.summary([]) == "No pods."
    no_metrics = rs.analyze([pod("a", used=(-1.0, -1.0))])
    assert "metrics-server did not answer" in rs.summary(no_metrics)
    text = rs.summary(rs.analyze([pod("api-1", used=(0.05, 60.0))]))
    assert "1 of 1 workloads need a look" in text and "would free about" in text and "one sample" in text


def test_dialog_sorts_numbers_filters_and_navigates(qtbot):
    dialog = RightSizingDialog("ctx", "namespace shop")
    qtbot.addWidget(dialog)
    pods = [pod("api-1", used=(0.05, 60.0)), pod("web-1", owner=("ReplicaSet", "web-1x"), used=(0.25, 256.0)),
            pod("hog-1", owner=("ReplicaSet", "hog-9"), used=(0.99, 1000.0))]
    dialog.show_pods(pods)
    shown = [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r)]
    assert sorted(shown) == ["Deployment/api", "Deployment/hog"]  # "web" is fine and hidden by default
    dialog.only_attention.setChecked(False)
    assert all(not dialog.table.isRowHidden(r) for r in range(3))
    dialog.filter.setText("hog")
    assert [dialog.table.item(r, 0).text() for r in range(3) if not dialog.table.isRowHidden(r)] == ["Deployment/hog"]
    dialog.filter.setText("")
    dialog.table.sortItems(4)  # CPU used, ascending: numbers, not text
    assert [dialog.table.item(r, 0).text() for r in range(3)] == ["Deployment/api", "Deployment/web", "Deployment/hog"]
    got = []
    dialog.go_to_requested.connect(lambda *a: got.append(a))
    dialog._open_row(2, 0)
    assert got == [("Deployment", "shop", "hog")]
    assert "✖ Near its limit" in [c[0] for c in cells(next(r for r in dialog.rows if r["name"] == "hog"))]
    dialog.show_error("boom")
    assert "boom" in dialog.summary.text()


def test_workspace_reads_pods_in_a_worker(qtbot):
    service = SimpleNamespace(list_pods=lambda ns: [pod("api-1", used=(0.05, 60.0))])
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_rightsizing()
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 1, timeout=5000)
    assert ws.show_rightsizing() is dialog
    ws.shutdown()
    qtbot.waitUntil(lambda: not ws._workers)
