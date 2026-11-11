import json

from klotse.k8s import timing
from test_plugins import host_for, node
from test_plugin_overview import Totals, host_for as overview_host, rows


def entries(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_enrichment_timing_has_only_safe_metadata(qtbot, tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    path = tmp_path / "timings.log"
    timing.timings.configure(path, True)
    try:
        host, plugin, run, _, _ = host_for(tmp_path)
        host.context = "private-cluster"
        host.rows_loaded("Node", [node("private-node")])
        run.finish()
        event = next(e for e in entries(path) if e.get("label") == "enrich")
        assert (event["plugin"], event["resource_kind"], event["resources"], event["outcome"]) == (plugin.id, "Node", 1, "ok")
        assert "private-node" not in path.read_text(encoding="utf-8")
        assert "private-cluster" not in path.read_text(encoding="utf-8")
    finally:
        timing.timings.configure(None, False)


def test_overview_timings_identify_each_plugin_even_on_failure(qtbot, tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    path = tmp_path / "timings.log"
    first, second = Totals(), Totals()
    second.id = "other"
    second.fail = ValueError("private error")
    host, _, run, _ = overview_host(tmp_path, first, second)
    timing.timings.configure(path, True)
    try:
        host.refresh_overview(lambda: rows("private-node"))
        run.finish()
        logged = entries(path)
        assert next(e for e in logged if e["label"] == "overview.nodes")["resources"] == 1
        events = [e for e in logged if e["label"] == "overview"]
        assert [(e["plugin"], e["outcome"]) for e in events] == [("totals", "ok"), ("other", "error")]
        text = path.read_text(encoding="utf-8")
        assert "private-node" not in text and "private error" not in text
    finally:
        timing.timings.configure(None, False)


def test_failed_enrichment_records_outcome_without_error_message(qtbot, tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    path = tmp_path / "timings.log"
    host, plugin, run, _, _ = host_for(tmp_path)

    def fail(_request):
        raise ValueError("private credentials")

    plugin.enrich = fail
    timing.timings.configure(path, True)
    try:
        host.rows_loaded("Node", [node("private-node")])
        run.finish()
        event = next(e for e in entries(path) if e.get("label") == "enrich")
        assert event["plugin"] == plugin.id and event["outcome"] == "error"
        assert "private credentials" not in path.read_text(encoding="utf-8")
    finally:
        timing.timings.configure(None, False)
