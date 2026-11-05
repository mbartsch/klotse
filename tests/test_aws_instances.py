import json
from datetime import datetime, timezone

import pytest

from klotse.plugins import ResourceInfo
from klotse_plugins.aws import AwsPlugin
from klotse_plugins.aws import pricing
from klotse_plugins.aws.ec2 import Ec2, Ec2Error, Ec2Unavailable
from klotse_plugins.aws.instances import TOUCH_AFTER_S, UNSEEN_DAYS, InstanceCache

DAY = 86400


def detail(ami="ami-1", launched=1_700_000_000.0, spot=False, zone="eu-west-1a"):
    return {"ami": ami, "launched": launched, "spot": spot, "zone": zone}


# ---- the cache itself ---------------------------------------------------------------------
def test_details_are_kept_in_memory_and_on_disk_and_survive_a_restart(tmp_path):
    cache = InstanceCache()
    cache.store(tmp_path, "eu-west-1", {"i-a": detail("ami-a"), "i-b": detail("ami-b", spot=True)})
    found, missing = cache.lookup(tmp_path, "eu-west-1", ["i-b", "i-a", "i-new", "i-new"])
    assert found == {"i-a": detail("ami-a"), "i-b": detail("ami-b", spot=True)} and missing == ["i-new"]
    restarted = InstanceCache()  # a new process: nothing in memory
    found, missing = restarted.lookup(tmp_path, "eu-west-1", ["i-a"])
    assert found == {"i-a": detail("ami-a")} and missing == []
    assert restarted.lookup(tmp_path, "us-east-1", ["i-a"]) == ({}, ["i-a"])  # a file per region
    assert (tmp_path / "instances-eu-west-1.json").is_file()


def test_nothing_is_stored_for_instances_aws_did_not_know(tmp_path):
    cache = InstanceCache()
    cache.store(tmp_path, "eu-west-1", {})
    assert not (tmp_path / "instances-eu-west-1.json").exists()
    assert cache.lookup(tmp_path, "eu-west-1", ["i-0dead1"]) == ({}, ["i-0dead1"])  # asked again next time


def test_using_an_entry_refreshes_its_last_used_time_at_most_once_a_day(tmp_path):
    clock = [1_000_000.0]
    cache = InstanceCache(now=lambda: clock[0])
    cache.store(tmp_path, "r", {"i-a": detail()})
    path = tmp_path / "instances-r.json"
    seen = lambda: json.loads(path.read_text())["i-a"]["seen"]  # noqa: E731
    assert seen() == 1_000_000.0
    clock[0] += TOUCH_AFTER_S - 10
    cache.lookup(tmp_path, "r", ["i-a"])
    assert seen() == 1_000_000.0  # used the same day: no write
    clock[0] += 20
    cache.lookup(tmp_path, "r", ["i-a"])
    assert seen() == clock[0]  # a day later: marked as used now


def test_entries_nobody_used_for_months_are_dropped_when_the_file_is_next_written(tmp_path):
    clock = [1_000_000.0]
    cache = InstanceCache(now=lambda: clock[0])
    cache.store(tmp_path, "r", {"i-old": detail("ami-old"), "i-live": detail("ami-live")})
    clock[0] += (UNSEEN_DAYS - 1) * DAY
    cache.lookup(tmp_path, "r", ["i-live"])  # i-live is still around
    clock[0] += 2 * DAY
    cache.store(tmp_path, "r", {"i-new": detail("ami-new")})
    assert sorted(json.loads((tmp_path / "instances-r.json").read_text())) == ["i-live", "i-new"]
    assert cache.lookup(tmp_path, "r", ["i-old"]) == ({}, ["i-old"])


def test_a_damaged_or_unwritable_cache_only_costs_the_lookups(tmp_path):
    (tmp_path / "instances-r.json").write_text("{not json")
    cache = InstanceCache()
    assert cache.lookup(tmp_path, "r", ["i-a"]) == ({}, ["i-a"])
    cache.store(tmp_path, "r", {"i-a": detail()})  # replaces the damaged file
    assert InstanceCache().lookup(tmp_path, "r", ["i-a"])[0] == {"i-a": detail()}
    (tmp_path / "instances-q.json").write_text(json.dumps({"i-a": {"ami": "x"}, "i-b": "junk"}))
    assert InstanceCache().lookup(tmp_path, "q", ["i-a", "i-b"]) == ({}, ["i-a", "i-b"])  # incomplete entries are ignored
    blocked = tmp_path / "file"
    blocked.write_text("x")
    cache = InstanceCache()
    cache.store(blocked / "below-a-file", "r", {"i-a": detail()})  # cannot be saved: no error
    assert cache.lookup(blocked / "below-a-file", "r", ["i-a"])[0] == {"i-a": detail()}  # still known in memory


def test_without_a_cache_folder_details_are_kept_in_memory_only():
    cache = InstanceCache()
    cache.store(None, "r", {"i-a": detail()})
    assert cache.lookup(None, "r", ["i-a"])[0] == {"i-a": detail()}
    assert InstanceCache().lookup(None, "r", ["i-a"]) == ({}, ["i-a"])


# ---- the plugin uses it --------------------------------------------------------------------------
class FakeClient:
    def __init__(self, instances=None, spot=None, error=None):
        self.instances, self.spot, self.error = instances or {}, spot or [], error
        self.describe_calls, self.spot_calls = [], 0

    def describe_instances(self, InstanceIds):
        self.describe_calls.append(list(InstanceIds))
        if self.error:
            raise self.error
        return {"Reservations": [{"Instances": [self.instances[i] for i in InstanceIds if i in self.instances]}]}

    def get_paginator(self, name):
        client = self

        class Paginator:
            def paginate(self, **kwargs):
                client.spot_calls += 1
                return [{"SpotPriceHistory": client.spot}]

        return Paginator()


def instance(iid, ami="ami-1", lifecycle=None, zone="eu-west-1a"):
    return {"InstanceId": iid, "ImageId": ami, "LaunchTime": datetime.fromtimestamp(1_700_000_000, timezone.utc),
            "InstanceLifecycle": lifecycle, "Placement": {"AvailabilityZone": zone}}


def node(name, iid, capacity=None, itype="m5.large", zone="eu-west-1a"):
    labels = {"node.kubernetes.io/instance-type": itype, "topology.kubernetes.io/region": zone[:-1]}
    if capacity:
        labels["karpenter.sh/capacity-type"] = capacity
    return ResourceInfo(name, "", {"metadata": {"name": name, "uid": "u-" + name, "labels": labels},
                                   "spec": {"providerID": f"aws:///{zone}/{iid}"}})


@pytest.fixture(autouse=True)
def public_prices(monkeypatch):
    monkeypatch.setattr("klotse_plugins.aws.on_demand_prices",
                        lambda *a, **k: pricing.PriceTable({"m5.large": 0.096}))


def plugin_with(client=None, boto3_missing=False):
    plugin = AwsPlugin()

    def api(profile):
        if boto3_missing:
            raise Ec2Unavailable("boto3 is not installed")
        return Ec2(lambda region: client)

    plugin._api = api
    return plugin


def collect(plugin, nodes, cache_dir, **settings):
    warnings = []
    values = plugin.collect(nodes, {"use_aws_api": True, "aws_profile": "", **settings}, cache_dir, warnings.append)
    return values, warnings


def test_each_instance_is_asked_about_once_and_never_again(tmp_path):
    client = FakeClient({"i-1": instance("i-1", "ami-one"), "i-2": instance("i-2", "ami-two")})
    plugin = plugin_with(client)
    values, _ = collect(plugin, [node("n1", "i-1")], tmp_path)
    assert values["n1"]["ami"] == "ami-one" and values["n1"]["launched"] == 1_700_000_000.0
    assert client.describe_calls == [["i-1"]]
    collect(plugin, [node("n1", "i-1")], tmp_path)
    collect(plugin, [node("n1", "i-1")], tmp_path)
    assert client.describe_calls == [["i-1"]]  # every later round is answered from the cache
    values, _ = collect(plugin, [node("n1", "i-1"), node("n2", "i-2")], tmp_path)
    assert client.describe_calls == [["i-1"], ["i-2"]]  # only the new instance goes to AWS
    assert values["n1"]["ami"] == "ami-one" and values["n2"]["ami"] == "ami-two"


def test_the_details_survive_a_restart_and_need_no_credentials(tmp_path):
    first = FakeClient({"i-1": instance("i-1", "ami-one")})
    collect(plugin_with(first), [node("n1", "i-1")], tmp_path)
    restarted = plugin_with(boto3_missing=True)  # a new process, and now without boto3 (or logged out)
    values, warnings = collect(restarted, [node("n1", "i-1")], tmp_path)
    assert values["n1"]["ami"] == "ami-one" and values["n1"]["launched"] == 1_700_000_000.0
    assert warnings == []  # nothing is missing, so there is nothing to complain about


def test_without_boto3_only_the_instances_not_yet_known_are_reported_as_missing_an_ami(tmp_path):
    collect(plugin_with(FakeClient({"i-1": instance("i-1", "ami-one")})), [node("n1", "i-1")], tmp_path)
    values, warnings = collect(plugin_with(boto3_missing=True), [node("n1", "i-1"), node("n2", "i-2")], tmp_path)
    assert values["n1"]["ami"] == "ami-one" and values["n2"]["ami"] is None
    assert len(warnings) == 1 and warnings[0].startswith("1 node(s) have no node group image label")


def test_an_instance_aws_does_not_know_is_not_remembered_as_missing(tmp_path):
    client = FakeClient({})
    plugin = plugin_with(client)
    collect(plugin, [node("n1", "i-0dead1")], tmp_path)
    collect(plugin, [node("n1", "i-0dead1")], tmp_path)
    assert client.describe_calls == [["i-0dead1"], ["i-0dead1"]]  # asked again: it may just not be visible yet
    client.instances["i-0dead1"] = instance("i-0dead1", "ami-now")
    values, _ = collect(plugin, [node("n1", "i-0dead1")], tmp_path)
    assert values["n1"]["ami"] == "ami-now"


def test_a_failed_call_stores_nothing_and_the_next_round_tries_again(tmp_path):
    client = FakeClient({"i-1": instance("i-1", "ami-one")}, error=Exception("ExpiredToken"))
    plugin = plugin_with(client)
    values, warnings = collect(plugin, [node("n1", "i-1")], tmp_path)
    assert values["n1"]["ami"] is None and any("ExpiredToken" in w for w in warnings)
    client.error = None  # the user signed in again
    values, warnings = collect(plugin, [node("n1", "i-1")], tmp_path)
    assert values["n1"]["ami"] == "ami-one" and warnings == []


def test_with_the_aws_api_switched_off_the_cache_is_not_used(tmp_path):
    client = FakeClient({"i-1": instance("i-1", "ami-one")})
    plugin = plugin_with(client)
    collect(plugin, [node("n1", "i-1")], tmp_path)
    values, _ = collect(plugin, [node("n1", "i-1")], tmp_path, use_aws_api=False)
    assert values["n1"]["ami"] is None and values["n1"]["launched"] is None  # "only the cluster and the public prices"


def test_spot_prices_are_still_fetched_every_round_while_the_details_stay_cached(tmp_path):
    spot = [{"InstanceType": "m5.large", "AvailabilityZone": "eu-west-1a", "SpotPrice": "0.03",
             "Timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    client = FakeClient({"i-1": instance("i-1", lifecycle="spot")}, spot=spot)
    plugin = plugin_with(client)
    for _ in range(3):
        values, _ = collect(plugin, [node("n1", "i-1", capacity="spot")], tmp_path)
        assert values["n1"]["price_now"] == 0.03
    assert len(client.describe_calls) == 1 and client.spot_calls == 3  # the price moves, the instance does not


def test_a_cached_spot_flag_alone_is_enough_to_look_up_a_spot_price(tmp_path):
    spot = [{"InstanceType": "m5.large", "AvailabilityZone": "eu-west-1a", "SpotPrice": "0.04",
             "Timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    client = FakeClient({"i-1": instance("i-1", lifecycle="spot")}, spot=spot)
    collect(plugin_with(client), [node("n1", "i-1")], tmp_path)  # not labelled spot; AWS knows it is
    values, _ = collect(plugin_with(client), [node("n1", "i-1")], tmp_path)  # a restart: the flag comes from the cache
    assert values["n1"]["capacity"] == "spot" and values["n1"]["price_now"] == 0.04
    assert len(client.describe_calls) == 1


def test_two_regions_are_cached_separately(tmp_path):
    client = FakeClient({"i-1": instance("i-1", "ami-eu"), "i-2": instance("i-2", "ami-us", zone="us-east-1a")})
    plugin = plugin_with(client)
    nodes = [node("n1", "i-1"), node("n2", "i-2", zone="us-east-1a")]
    collect(plugin, nodes, tmp_path)
    collect(plugin, nodes, tmp_path)
    assert len(client.describe_calls) == 2  # one per region, and nothing on the second round
    assert sorted(p.name for p in tmp_path.glob("instances-*.json")) == ["instances-eu-west-1.json", "instances-us-east-1.json"]
