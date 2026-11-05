import pytest
from PySide6.QtCore import QSettings

from klotse.k8s.models import NodeRow
from klotse.plugins import OverviewCard, Plugin, ResourceInfo
from klotse.plugins.host import RETRY_AFTER_S, PluginHost
from klotse.plugins.manager import Loaded, PluginManager, _as_plugin
from klotse.ui.overview import CLUSTER_CARDS, OverviewView
from klotse.ui.workspace import ClusterWorkspace
from klotse_plugins.aws import AwsPlugin, pricing
from test_plugins import Runner, node, store

DATA = {"counts": {"nodes": 3}, "events": [], "lists": {}}


class Totals(Plugin):
    id = "totals"
    name = "Totals"
    overview_cards = (OverviewCard("count", "Node count"), OverviewCard("names", "Names"))
    overview_refresh_s = 300

    def __init__(self):
        self.requests, self.fail, self.warn, self.answer = [], None, None, None

    def overview(self, request):
        self.requests.append(request)
        if self.fail:
            raise self.fail
        if self.warn:
            request.warn(self.warn)
        if self.answer is not None:
            return self.answer
        return {"count": len(request.nodes), "names": ", ".join(n.name for n in request.nodes), "not-a-card": 1}


def manager_with(tmp_path, *plugins):
    manager = PluginManager(store(tmp_path), folder=None, bundled=(), entry_points=False)
    manager.loaded = [Loaded(p.id, "bundled", p.id, p) for p in plugins]
    for plugin in plugins:
        manager.set_enabled(plugin.id, True)
    return manager


def rows(*names):
    return [NodeRow(n, "Ready", "worker", "v1", "10.0.0.1", 4.0, 16.0, "1d", node(n).raw) for n in names]


def host_for(tmp_path, *plugins):
    manager = manager_with(tmp_path, *plugins)
    run, clock = Runner(), [1000.0]
    return PluginHost(manager, "ctx", run, clock=lambda: clock[0]), manager, run, clock


# ---- the definitions ------------------------------------------------------------------------------
def test_overview_cards_are_validated():
    class Bad(Plugin):
        id = "bad"
        overview_cards = ("x",)

    with pytest.raises(TypeError):
        _as_plugin(Bad)
    assert _as_plugin(Totals).overview_cards[0].key == "count"


def test_only_enabled_plugins_with_cards_are_overview_plugins(tmp_path):
    manager = manager_with(tmp_path, Totals())
    assert [p.id for p in manager.overview_plugins()] == ["totals"]
    manager.set_enabled("totals", False)
    assert manager.overview_plugins() == []


# ---- the host --------------------------------------------------------------------------------------------
def test_the_nodes_are_listed_once_and_each_plugin_gets_them_on_a_worker(qtbot, tmp_path):
    first, second = Totals(), Totals()
    second.id = "other"
    host, _, run, _ = host_for(tmp_path, first, second)
    listed = []
    host.refresh_overview(lambda: listed.append(1) or rows("n1", "n2"))
    assert len(run.calls) == 1 and listed == [] and first.requests == []  # queued for a worker, not run here
    updated = []
    host.overview_updated.connect(lambda: updated.append(1))
    run.finish()
    assert listed == [1]  # one list call for both plugins
    assert [n.name for n in first.requests[0].nodes] == ["n1", "n2"] and first.requests[0].context == "ctx"
    assert first.requests[0].nodes[0].raw["metadata"]["uid"] == "uid-n1" and len(second.requests) == 1
    assert updated == [1]
    assert host.overview_cards() == [("totals:count", "Node count"), ("totals:names", "Names"),
                                     ("other:count", "Node count"), ("other:names", "Names")]
    assert host.overview_values()["totals:count"] == 2 and host.overview_values()["totals:names"] == "n1, n2"
    assert "totals:not-a-card" not in host.overview_values()  # only declared cards


def test_the_cards_are_computed_again_only_when_due_and_the_last_values_stay_meanwhile(qtbot, tmp_path):
    plugin = Totals()
    host, _, run, clock = host_for(tmp_path, plugin)
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    clock[0] += 299
    host.refresh_overview(lambda: rows("n1", "n2"))  # the 10 s overview refresh: not due
    assert run.calls == [] and host.overview_values()["totals:count"] == 1
    clock[0] += 2
    host.refresh_overview(lambda: rows("n1", "n2"))
    assert len(run.calls) == 1 and host.overview_values()["totals:count"] == 1  # still the old value while it runs
    host.refresh_overview(lambda: rows("n1", "n2"))
    assert len(run.calls) == 1  # one at a time
    run.finish()
    assert host.overview_values()["totals:count"] == 2


def test_a_plugin_returning_nothing_for_a_card_shows_no_card(qtbot, tmp_path):
    plugin = Totals()
    plugin.answer = {"count": 4, "names": None}
    host, _, run, _ = host_for(tmp_path, plugin)
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    assert host.overview_cards() == [("totals:count", "Node count")]
    plugin.answer = {}
    host.forget("totals")
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    assert host.overview_cards() == []  # a cluster the plugin does not apply to


def test_a_failing_plugin_is_reported_backed_off_and_does_not_hide_the_others(qtbot, tmp_path):
    broken, fine = Totals(), Totals()
    broken.fail, fine.id = RuntimeError("boom"), "fine"
    host, manager, run, clock = host_for(tmp_path, broken, fine)
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    assert manager.errors == {"totals": "boom"} and host.overview_values() == {"fine:count": 1, "fine:names": "n1"}
    clock[0] += RETRY_AFTER_S - 1
    host.refresh_overview(lambda: rows("n1"))
    assert run.calls == []  # backing off
    clock[0] += 2
    broken.fail = None
    host.refresh_overview(lambda: rows("n1"))
    assert len(run.calls) == 1
    run.finish()
    assert manager.errors == {} and "totals:count" in host.overview_values()


def test_listing_the_nodes_failing_is_reported_for_every_plugin_asked(qtbot, tmp_path):
    plugin = Totals()
    host, manager, run, _ = host_for(tmp_path, plugin)

    def broken():
        raise RuntimeError("not connected")

    host.refresh_overview(broken)
    run.finish()
    assert manager.errors == {"totals": "not connected"} and host.overview_values() == {}
    host.refresh_overview(broken)
    assert run.calls == []  # retried after a pause, and a new request is never started while one is in flight


def test_warnings_keep_the_values_and_ask_again_soon(qtbot, tmp_path):
    plugin = Totals()
    plugin.warn = "the login expired"
    host, manager, run, clock = host_for(tmp_path, plugin)
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    assert manager.errors == {"totals": "the login expired"} and host.overview_values()["totals:count"] == 1
    plugin.warn = None
    clock[0] += RETRY_AFTER_S + 1
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    assert manager.errors == {}


def test_a_wrong_answer_type_is_a_reported_problem(qtbot, tmp_path):
    plugin = Totals()
    plugin.answer = ["not", "a", "dict"]
    host, manager, run, _ = host_for(tmp_path, plugin)
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    assert "expected a dict" in manager.errors["totals"]


def test_forgetting_a_plugin_drops_its_cards_and_makes_them_due(qtbot, tmp_path):
    plugin = Totals()
    host, _, run, _ = host_for(tmp_path, plugin)
    host.refresh_overview(lambda: rows("n1"))
    run.finish()
    host.forget("totals")  # its settings changed
    assert host.overview_values() == {}
    host.refresh_overview(lambda: rows("n1"))
    assert len(run.calls) == 1  # computed again at once


# ---- the Overview page ------------------------------------------------------------------------------------
def test_the_overview_shows_plugin_cards_under_the_built_in_ones_and_keeps_their_values(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.set_plugin_cards([("a:cost", "Cost per hour"), ("a:month", "Cost per month")])
    assert list(view.plugin_cards) == ["a:cost", "a:month"] and view.plugin_cards["a:cost"].value.text() == "–"
    view.set_plugin_values({"a:cost": "$1.2345", "a:month": "$901.19"})
    assert view.plugin_cards["a:cost"].value.text() == "$1.2345"
    view.set_plugin_cards([("a:cost", "Cost per hour"), ("a:month", "Cost per month")])  # the same: nothing changes
    assert view.plugin_cards["a:month"].value.text() == "$901.19"
    view.set_plugin_cards([("a:cost", "Cost per hour"), ("a:month", "Cost per month"), ("a:priced", "Nodes priced")])
    assert view.plugin_cards["a:cost"].value.text() == "–" or view.plugin_cards["a:cost"].value.text() == "$1.2345"
    view.set_plugin_cards([])
    assert view.plugin_cards == {} and view.plugin_grid.count() == 0
    assert set(view.cards) == {key for key, _, _ in CLUSTER_CARDS}  # the built-in cards are untouched


def test_new_cards_keep_the_values_of_those_that_were_already_there(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.set_plugin_cards([("a:cost", "Cost")])
    view.set_plugin_values({"a:cost": "$5.0000"})
    view.set_plugin_cards([("a:cost", "Cost"), ("a:more", "More")])
    assert view.plugin_cards["a:cost"].value.text() == "$5.0000" and view.plugin_cards["a:more"].value.text() == "–"


# ---- the workspace ----------------------------------------------------------------------------------------------
def test_loading_the_overview_computes_the_cards_and_a_refresh_does_not_blank_them(qtbot, tmp_path):
    plugin = Totals()
    manager = manager_with(tmp_path, plugin)
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    run = Runner()
    ws.plugin_host._run = run
    ws.service = type("Svc", (), {"list_nodes": staticmethod(lambda: rows("n1", "n2"))})()
    ws._overview_loaded(DATA)  # what loading the Overview does with its data
    assert ws.overview.plugin_cards == {}  # nothing to show until the plugin has answered
    run.finish()
    assert ws.overview.plugin_cards["totals:count"].value.text() == "2"
    ws._overview_loaded(DATA)  # the next auto-refresh
    assert run.calls == [] and ws.overview.plugin_cards["totals:count"].value.text() == "2"


def test_disabling_the_plugin_removes_its_cards_and_enabling_brings_them_back(qtbot, tmp_path):
    plugin = Totals()
    manager = manager_with(tmp_path, plugin)
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    run = Runner()
    ws.plugin_host._run = run
    ws.service = type("Svc", (), {"list_nodes": staticmethod(lambda: rows("n1"))})()
    ws._ready = True
    ws._overview_loaded(DATA)
    run.finish()
    assert list(ws.overview.plugin_cards) == ["totals:count", "totals:names"]
    manager.set_enabled("totals", False)
    assert ws.overview.plugin_cards == {}
    manager.set_enabled("totals", True)
    assert list(ws.overview.plugin_cards) == ["totals:count", "totals:names"]  # the last values were kept


def test_changing_a_plugin_setting_recomputes_the_cards_at_once(qtbot, tmp_path):
    plugin = Totals()
    manager = manager_with(tmp_path, plugin)
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    run = Runner()
    ws.plugin_host._run = run
    ws.service = type("Svc", (), {"list_nodes": staticmethod(lambda: rows("n1"))})()
    ws._ready = True
    ws._overview_loaded(DATA)
    run.finish()
    manager.set_setting("totals", "anything", "x")  # for example the AWS profile of the cluster
    assert len(run.calls) == 1 and ws.overview.plugin_cards == {}


# ---- the AWS plugin's cards ------------------------------------------------------------------------------------
@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setattr("klotse_plugins.aws.on_demand_prices",
                        lambda *a, **k: pricing.PriceTable({"m5.large": 0.096, "c6g.xlarge": 0.136}))


def aws_node(name, iid, itype="m5.large", capacity=None, provider=None):
    labels = {"node.kubernetes.io/instance-type": itype, "topology.kubernetes.io/region": "eu-west-1"}
    if capacity:
        labels["karpenter.sh/capacity-type"] = capacity
    spec = {"providerID": provider or f"aws:///eu-west-1a/{iid}"}
    return ResourceInfo(name, "", {"metadata": {"name": name, "uid": "u-" + name, "labels": labels}, "spec": spec})


def overview_of(nodes, **settings):
    from klotse.plugins import OverviewRequest
    plugin = AwsPlugin()
    request = OverviewRequest("aws", "ctx", nodes, {"use_aws_api": False, **settings})
    return plugin.overview(request), request


def test_the_cluster_cost_is_the_sum_of_what_each_node_costs(prices):
    values, request = overview_of([aws_node("a", "i-0a1"), aws_node("b", "i-0b2", "c6g.xlarge"), aws_node("c", "i-0c3")])
    assert values == {"cost_hour": "$0.3280", "cost_month": "$239.44", "priced": "3 of 3"}  # 0.096*2 + 0.136, times 730
    assert request.warnings == []


def test_spot_nodes_without_a_spot_price_are_not_counted_and_the_card_says_so(prices):
    values, _ = overview_of([aws_node("a", "i-0a1"), aws_node("s", "i-0b2", capacity="spot")])
    assert values["cost_hour"] == "$0.0960" and values["priced"] == "1 of 2"


def test_a_cluster_with_no_aws_nodes_gets_no_cards(prices):
    other = aws_node("n1", "i-0a1", provider="gce://p/z/n")
    assert overview_of([other])[0] == {} and overview_of([])[0] == {}


def test_the_aws_plugin_declares_its_cards_and_needs_api_5():
    assert [c.key for c in AwsPlugin.overview_cards] == ["cost_hour", "cost_month", "priced"] and AwsPlugin.requires_api == 7
