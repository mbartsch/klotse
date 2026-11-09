import gzip
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.plugins import ActionRequest, EnrichRequest, ResourceInfo
from klotse.plugins.manager import BUNDLED_MODULES, PluginManager
from klotse_plugins.aws import AwsPlugin, console_url, per_hour, per_month, when
from klotse_plugins.aws import ec2 as ec2_module
from klotse_plugins.aws import pricing
from klotse_plugins.aws.ec2 import Ec2, Ec2Error, Ec2Unavailable
from klotse_plugins.aws.nodeinfo import parse_node
from klotse_plugins.aws.pricing import PriceError, REGION_NAMES, on_demand_prices, parse_price_file, price_url


def node(name, instance="i-0abc123", zone="eu-west-1a", itype="m5.large", capacity=None, extra=None, os_name=None,
         provider=True, region_label=True):
    labels = {"node.kubernetes.io/instance-type": itype, "topology.kubernetes.io/zone": zone}
    if region_label:
        labels["topology.kubernetes.io/region"] = zone[:-1]
    if capacity:
        labels["karpenter.sh/capacity-type"] = capacity
    if os_name:
        labels["kubernetes.io/os"] = os_name
    labels.update(extra or {})
    spec = {"providerID": f"aws:///{zone}/{instance}"} if provider else {}
    return ResourceInfo(name, "", {"metadata": {"name": name, "uid": "u-" + name, "labels": labels}, "spec": spec})


def price_file(prices, sql=None):
    entries = {f"{t} k": {"Instance Type": t, "price": f"{p:.10f}", "Pre Installed S/W": "NA"} for t, p in prices.items()}
    for t, p in (sql or {}).items():
        entries[f"{t} sql"] = {"Instance Type": t, "price": f"{p:.10f}", "Pre Installed S/W": "SQL Std"}
    return gzip.compress(json.dumps({"manifest": {}, "regions": {"EU (Ireland)": entries}}).encode())


# ---- reading the node ---------------------------------------------------------------------
def test_a_node_says_its_instance_zone_region_type_capacity_and_os():
    info = parse_node(node("n1", "i-0123456789abcdef0", "eu-west-1b", "c6g.xlarge", capacity="spot"))
    assert (info.instance_id, info.zone, info.region, info.instance_type) == ("i-0123456789abcdef0", "eu-west-1b", "eu-west-1", "c6g.xlarge")
    assert info.capacity == "spot" and info.os == "Linux" and info.ami == ""


def test_capacity_comes_from_karpenter_or_eks_labels_and_defaults_to_on_demand():
    assert parse_node(node("a", extra={"eks.amazonaws.com/capacityType": "SPOT"})).capacity == "spot"
    assert parse_node(node("a", extra={"eks.amazonaws.com/capacityType": "ON_DEMAND"})).capacity == "on-demand"
    assert parse_node(node("a", capacity="reserved")).capacity == "reserved"
    assert parse_node(node("a")).capacity == "on-demand"
    assert parse_node(node("a", capacity="mystery")).capacity == "on-demand"


def test_the_ami_label_windows_and_a_region_taken_from_the_zone():
    info = parse_node(node("a", extra={"eks.amazonaws.com/nodegroup-image": "ami-0abc"}, os_name="windows"))
    assert info.ami == "ami-0abc" and info.os == "Windows"
    assert parse_node(node("a", zone="us-west-2-lax-1a", region_label=False)).region == "us-west-2"
    assert parse_node(node("a", zone="us-gov-east-1a", region_label=False)).region == "us-gov-east-1"
    legacy = node("a", extra={"beta.kubernetes.io/instance-type": "t3.micro"})
    legacy.raw["metadata"]["labels"].pop("node.kubernetes.io/instance-type")
    assert parse_node(legacy).instance_type == "t3.micro"


def test_nodes_that_are_not_ec2_instances_are_ignored():
    assert parse_node(node("a", provider=False)) is None
    other = node("a")
    for provider_id in ("gce://project/zone/name", "aws:///eu-west-1a/fargate-ip-10-0-0-1", "kind://docker/x/y", ""):
        other.raw["spec"] = {"providerID": provider_id}
        assert parse_node(other) is None


# ---- prices -------------------------------------------------------------------------------------
def test_the_price_url_uses_the_regions_price_list_name_and_the_os():
    assert price_url("eu-west-1", "Linux").endswith("/EU%20%28Ireland%29/Linux/index.json") or price_url("eu-west-1", "Linux").endswith("/EU%20(Ireland)/Linux/index.json")
    assert "/Windows/" in price_url("us-east-1", "Windows")
    with pytest.raises(PriceError, match="no price list is known for the region ap-nowhere-9"):
        price_url("ap-nowhere-9", "Linux")
    assert REGION_NAMES["eu-central-2"] == "EU (Zurich)" and REGION_NAMES["sa-east-1"] == "South America (Sao Paulo)"


def test_parsing_a_price_file_reads_gzip_or_plain_and_skips_extras():
    raw = price_file({"m5.large": 0.096, "t3.nano": 0.0052}, sql={"m5.large": 0.5})
    assert parse_price_file(raw) == {"m5.large": 0.096, "t3.nano": 0.0052}  # the SQL Server variant is not the node's price
    assert parse_price_file(gzip.decompress(raw)) == {"m5.large": 0.096, "t3.nano": 0.0052}
    junk = json.dumps({"regions": {"X": {"a": {"Instance Type": "bad", "price": "n/a"}, "b": {"price": "1"}}}}).encode()
    assert parse_price_file(junk) == {}


def test_prices_are_downloaded_once_and_then_come_from_the_cache(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return price_file({"m5.large": 0.096})

    clock = [1000.0]
    first = on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=fetch, now=lambda: clock[0])
    assert first.prices == {"m5.large": 0.096} and first.note == "" and len(calls) == 1
    clock[0] += 3 * 86400
    again = on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=fetch, now=lambda: clock[0])
    assert again.prices == first.prices and len(calls) == 1  # under a week old: no download
    clock[0] += 5 * 86400
    on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=fetch, now=lambda: clock[0])
    assert len(calls) == 2  # now it is stale


def test_a_failed_refresh_keeps_using_the_old_copy_and_says_so(tmp_path):
    clock = [1000.0]
    on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=lambda url: price_file({"m5.large": 0.096}), now=lambda: clock[0])
    clock[0] += 9 * 86400

    def down(url):
        raise OSError("network is down")

    stale = on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=down, now=lambda: clock[0])
    assert stale.prices == {"m5.large": 0.096} and "9 days ago" in stale.note and "network is down" in stale.note


def test_with_no_copy_a_failed_download_is_an_error(tmp_path):
    def down(url):
        raise OSError("network is down")

    with pytest.raises(PriceError, match="could not download the prices for eu-west-1: network is down"):
        on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=down)
    with pytest.raises(PriceError, match="no prices"):
        on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=lambda url: price_file({}))
    with pytest.raises(PriceError):
        on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=lambda url: b"<html>not json</html>")


def test_a_corrupt_cache_file_is_replaced_and_no_cache_dir_still_works(tmp_path):
    (tmp_path / "ondemand-eu-west-1-Linux.json").write_text("{broken")
    table = on_demand_prices("eu-west-1", "Linux", tmp_path, fetch=lambda url: price_file({"m5.large": 0.1}))
    assert table.prices == {"m5.large": 0.1}
    assert json.loads((tmp_path / "ondemand-eu-west-1-Linux.json").read_text())["prices"] == {"m5.large": 0.1}
    assert on_demand_prices("eu-west-1", "Linux", None, fetch=lambda url: price_file({"x": 1.0})).prices == {"x": 1.0}


def test_the_download_refuses_a_huge_answer(monkeypatch):
    class Big:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return b"x" * limit

    monkeypatch.setattr(pricing.urllib.request, "urlopen", lambda request, timeout: Big())
    with pytest.raises(PriceError, match="unexpectedly large"):
        pricing.download("https://example.invalid/x")


# ---- the AWS API tier -----------------------------------------------------------------------------
class FakeClient:
    def __init__(self, instances=None, spot=None, error=None, missing=()):
        self.instances, self.spot, self.error, self.missing = instances or {}, spot or [], error, set(missing)
        self.calls = []

    def describe_instances(self, InstanceIds):
        self.calls.append(list(InstanceIds))
        if self.error:
            raise self.error
        gone = [i for i in InstanceIds if i in self.missing]
        if gone:
            error = Exception("InvalidInstanceID.NotFound")
            error.response = {"Error": {"Code": "InvalidInstanceID.NotFound"}}
            raise error
        found = [self.instances[i] for i in InstanceIds if i in self.instances]
        return {"Reservations": [{"Instances": found}]}

    def get_paginator(self, name):
        assert name == "describe_spot_price_history"
        client = self

        class Paginator:
            def paginate(self, **kwargs):
                client.spot_args = kwargs
                if client.error:
                    raise client.error
                return [{"SpotPriceHistory": client.spot[:2]}, {"SpotPriceHistory": client.spot[2:]}]

        return Paginator()


def instance(iid, ami="ami-1", launched=1_700_000_000, lifecycle=None, zone="eu-west-1a"):
    return {"InstanceId": iid, "ImageId": ami, "LaunchTime": datetime.fromtimestamp(launched, timezone.utc),
            "InstanceLifecycle": lifecycle, "Placement": {"AvailabilityZone": zone}}


def test_instance_details_are_read_in_chunks_and_a_missing_instance_is_skipped():
    client = FakeClient(instances={"i-a": instance("i-a", "ami-a"), "i-b": instance("i-b", lifecycle="spot")}, missing={"i-gone"})
    details = Ec2(lambda region: client).instance_details("eu-west-1", ["i-a", "i-b", "i-gone"])
    assert details["i-a"] == {"ami": "ami-a", "launched": 1_700_000_000.0, "spot": False, "zone": "eu-west-1a"}
    assert details["i-b"]["spot"] is True and "i-gone" not in details
    assert client.calls[0] == ["i-a", "i-b", "i-gone"] and ["i-gone"] in client.calls  # retried one by one
    many = Ec2(lambda region: FakeClient()).instance_details("eu-west-1", [f"i-{n}" for n in range(250)])
    assert many == {}


def test_spot_prices_keep_the_latest_per_type_and_zone():
    old, new = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)
    client = FakeClient(spot=[
        {"InstanceType": "m5.large", "AvailabilityZone": "eu-west-1a", "SpotPrice": "0.05", "Timestamp": old},
        {"InstanceType": "m5.large", "AvailabilityZone": "eu-west-1a", "SpotPrice": "0.04", "Timestamp": new},
        {"InstanceType": "m5.large", "AvailabilityZone": "eu-west-1b", "SpotPrice": "0.06", "Timestamp": old}])
    prices = Ec2(lambda region: client).spot_prices("eu-west-1", ["m5.large", "m5.large", ""])
    assert prices == {("m5.large", "eu-west-1a"): 0.04, ("m5.large", "eu-west-1b"): 0.06}
    assert client.spot_args["InstanceTypes"] == ["m5.large"] and client.spot_args["ProductDescriptions"] == ["Linux/UNIX"]
    assert Ec2(lambda region: client).spot_prices("eu-west-1", []) == {}


def test_aws_errors_become_readable_ec2_errors():
    boom = Exception("Unable to locate credentials\nsecond line")
    with pytest.raises(Ec2Error, match="Unable to locate credentials"):
        Ec2(lambda region: FakeClient(error=boom)).instance_details("eu-west-1", ["i-a"])
    with pytest.raises(Ec2Error, match="Unable to locate credentials"):
        Ec2(lambda region: FakeClient(error=boom)).spot_prices("eu-west-1", ["m5.large"])


def test_without_boto3_the_session_says_so(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_boto(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_boto)
    with pytest.raises(Ec2Unavailable):
        ec2_module.boto3_session("")


# ---- the plugin ---------------------------------------------------------------------------------------
def make_plugin(prices=None, ec2=None, down=False):
    """An AwsPlugin whose price list and AWS API are fakes."""
    plugin = AwsPlugin()
    if ec2 is not None:
        plugin._api = lambda profile: ec2
    else:
        def no_boto(profile):
            raise Ec2Unavailable("boto3 is not installed")

        plugin._api = no_boto
    return plugin, prices or {"m5.large": 0.096, "c6g.xlarge": 0.136}


@pytest.fixture
def fake_prices(monkeypatch):
    table = {"m5.large": 0.096, "c6g.xlarge": 0.136}

    def fake(region, os_name, cache_dir, **kwargs):
        if region == "eu-nowhere-1":
            raise PriceError("no price list is known for the region eu-nowhere-1")
        return pricing.PriceTable(dict(table), "")

    monkeypatch.setattr("klotse_plugins.aws.on_demand_prices", fake)
    return table


def collect(plugin, resources, settings=None, hints=False):
    """(values, warnings); the "no AMI without boto3" hint is left out unless a test is about it."""
    warnings = []
    values = plugin.collect(resources, {"use_aws_api": True, "aws_profile": "", **(settings or {})}, None, warnings.append)
    return values, warnings if hints else [w for w in warnings if "node group image label" not in w]


def test_without_any_login_the_columns_come_from_the_node_and_the_public_prices(fake_prices):
    plugin, _ = make_plugin()
    values, warnings = collect(plugin, [node("n1", "i-1"), node("n2", "i-2", itype="c6g.xlarge",
                                                                 extra={"eks.amazonaws.com/nodegroup-image": "ami-xyz"}),
                                        node("other", provider=False)])
    assert warnings == [] and set(values) == {"n1", "n2"}  # a non-AWS node is left out
    assert values["n1"] == {"instance_id": "i-1", "instance_type": "m5.large", "capacity": "on-demand", "ami": None,
                            "price_od": 0.096, "price_now": 0.096, "price_month": pytest.approx(0.096 * 730), "launched": None}
    assert values["n2"]["ami"] == "ami-xyz" and values["n2"]["price_od"] == 0.136


def test_a_spot_node_has_no_current_price_without_aws_but_still_shows_the_on_demand_one(fake_prices):
    plugin, _ = make_plugin()
    values, _ = collect(plugin, [node("s1", capacity="spot")])
    assert values["s1"]["capacity"] == "spot" and values["s1"]["price_od"] == 0.096
    assert values["s1"]["price_now"] is None and values["s1"]["price_month"] is None  # not billed at the on-demand rate


def test_with_the_aws_api_spot_prices_launch_time_and_amis_come_in(fake_prices):
    old, new = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)
    client = FakeClient(
        instances={"i-1": instance("i-1", "ami-real", 1_700_000_000), "i-2": instance("i-2", "ami-spot", lifecycle="spot")},
        spot=[{"InstanceType": "m5.large", "AvailabilityZone": "eu-west-1a", "SpotPrice": "0.031", "Timestamp": new}])
    plugin, _ = make_plugin(ec2=Ec2(lambda region: client))
    values, warnings = collect(plugin, [node("n1", "i-1"), node("n2", "i-2")])  # n2 is not labelled spot; AWS knows
    assert warnings == []
    assert values["n1"]["ami"] == "ami-real" and values["n1"]["launched"] == 1_700_000_000.0 and values["n1"]["price_now"] == 0.096
    assert values["n2"]["capacity"] == "spot" and values["n2"]["price_now"] == 0.031  # AWS said spot: spot price
    assert values["n2"]["price_month"] == pytest.approx(0.031 * 730) and values["n2"]["price_od"] == 0.096


def test_the_aws_api_can_be_turned_off(fake_prices):
    client = FakeClient(instances={"i-1": instance("i-1", "ami-real")})
    plugin, _ = make_plugin(ec2=Ec2(lambda region: client))
    values, _ = collect(plugin, [node("n1", "i-1")], settings={"use_aws_api": False})
    assert client.calls == [] and values["n1"]["ami"] is None and values["n1"]["launched"] is None


def test_aws_failures_warn_but_the_columns_still_fill_in(fake_prices):
    plugin, _ = make_plugin(ec2=Ec2(lambda region: FakeClient(error=Exception("Unable to locate credentials"))))
    values, warnings = collect(plugin, [node("n1")])
    assert len(warnings) == 1 and "AWS API (eu-west-1): Unable to locate credentials" in warnings[0]
    assert values["n1"]["price_now"] == 0.096  # the public data is still there

    def broken(profile):
        raise Ec2Error("The config profile (nope) could not be found")

    plugin = AwsPlugin()
    plugin._api = broken
    values, warnings = collect(plugin, [node("n1")])
    assert warnings == ["AWS API: The config profile (nope) could not be found (using the cluster data and the public prices instead)"]


def test_a_missing_boto3_is_only_mentioned_when_it_leaves_an_ami_blank(fake_prices):
    plugin, _ = make_plugin()  # no boto3
    labelled = node("n1", extra={"eks.amazonaws.com/nodegroup-image": "ami-label"})
    _, warnings = collect(plugin, [labelled], hints=True)
    assert warnings == []  # every AMI is known from the label: nothing is missing
    values, warnings = collect(plugin, [labelled, node("n2", "i-2"), node("n3", "i-3")], hints=True)
    assert values["n1"]["ami"] == "ami-label" and values["n2"]["ami"] is None
    assert len(warnings) == 1 and warnings[0].startswith("2 node(s) have no node group image label")
    assert "boto3 or the AWS CLI" in warnings[0]
    _, off = collect(plugin, [node("n2", "i-2")], settings={"use_aws_api": False}, hints=True)
    assert off == []  # the user turned the AWS API off: not a problem to report


def test_a_node_without_the_image_label_gets_its_ami_from_ec2(fake_prices):
    client = FakeClient(instances={"i-2": instance("i-2", "ami-from-ec2"), "i-1": instance("i-1", "ami-from-ec2-too")})
    plugin, _ = make_plugin(ec2=Ec2(lambda region: client))
    values, warnings = collect(plugin, [node("n1", "i-1", extra={"eks.amazonaws.com/nodegroup-image": "ami-label"}),
                                        node("n2", "i-2")], hints=True)
    assert values["n2"]["ami"] == "ami-from-ec2" and warnings == []
    assert values["n1"]["ami"] == "ami-from-ec2-too"  # what AWS says wins over the label
    assert client.calls == [["i-1", "i-2"]]  # one call for the region, however many nodes


def test_when_ec2_cannot_say_the_label_is_kept_and_a_gone_instance_just_has_no_ami(fake_prices):
    client = FakeClient(instances={}, missing=set())
    plugin, _ = make_plugin(ec2=Ec2(lambda region: client))
    values, warnings = collect(plugin, [node("n1", "i-1", extra={"eks.amazonaws.com/nodegroup-image": "ami-label"}),
                                        node("n2", "i-2")], hints=True)
    assert values["n1"]["ami"] == "ami-label" and values["n2"]["ami"] is None and warnings == []


def test_a_region_without_a_price_list_warns_and_leaves_the_prices_blank(fake_prices):
    plugin, _ = make_plugin()
    values, warnings = collect(plugin, [node("n1", zone="eu-nowhere-1a")])
    assert warnings == ["no price list is known for the region eu-nowhere-1"]
    assert values["n1"]["price_od"] is None and values["n1"]["instance_id"] == "i-0abc123"


def test_a_stale_price_table_is_a_warning(monkeypatch):
    monkeypatch.setattr("klotse_plugins.aws.on_demand_prices",
                        lambda *a, **k: pricing.PriceTable({"m5.large": 0.1}, "using prices from 9 days ago: down"))
    plugin, _ = make_plugin()
    values, warnings = collect(plugin, [node("n1")])
    assert warnings == ["eu-west-1: using prices from 9 days ago: down"] and values["n1"]["price_od"] == 0.1


def test_each_region_and_os_is_priced_from_its_own_table(monkeypatch):
    asked = []

    def fake(region, os_name, cache_dir, **kwargs):
        asked.append((region, os_name))
        return pricing.PriceTable({"m5.large": 0.5 if os_name == "Windows" else 0.1})

    monkeypatch.setattr("klotse_plugins.aws.on_demand_prices", fake)
    plugin, _ = make_plugin()
    values, _ = collect(plugin, [node("a", zone="eu-west-1a"), node("b", "i-2", zone="us-east-1b"),
                                 node("c", "i-3", zone="eu-west-1a", os_name="windows")])
    assert sorted(asked) == [("eu-west-1", "Linux"), ("eu-west-1", "Windows"), ("us-east-1", "Linux")]
    assert (values["a"]["price_od"], values["c"]["price_od"]) == (0.1, 0.5)


def test_enrich_uses_the_request(fake_prices):
    plugin, _ = make_plugin()
    request = EnrichRequest("aws", "Node", "ctx", [node("n1")], {"use_aws_api": False}, cache_dir=None)
    assert plugin.enrich(request)["n1"]["price_od"] == 0.096 and request.warnings == []


# ---- columns, formats and actions --------------------------------------------------------------------------
def test_value_formats_and_the_console_link():
    assert per_hour(0.096) == "$0.0960" and per_month(70.08) == "$70.08" and per_month(1234.5) == "$1,234.50"
    assert when(1_700_000_000) == "2023-11-14 22:13 UTC"
    assert console_url(parse_node(node("n"))).startswith(
        "https://eu-west-1.console.aws.amazon.com/ec2/home?region=eu-west-1#InstanceDetails:instanceId=i-0abc123")
    gov = parse_node(node("n", zone="us-gov-east-1a"))
    assert console_url(gov).startswith("https://console.amazonaws-us-gov.com/ec2/home?region=us-gov-east-1")


def test_actions_are_only_offered_for_ec2_nodes():
    plugin = AwsPlugin()
    assert plugin.applies(plugin.actions[0], node("n1")) and not plugin.applies(plugin.actions[0], node("n", provider=False))
    assert [(a.key, a.multi) for a in plugin.actions] == [("copy-id", True), ("console", False), ("cost", True)]


def request_for(action, resources, **kwargs):
    return ActionRequest("aws", action, "Node", "ctx", resources, {"use_aws_api": False, "aws_profile": ""}, **kwargs)


def test_copy_and_console_actions():
    plugin = AwsPlugin()
    copied = plugin.run(request_for("copy-id", [node("a", "i-1"), node("b", "i-2"), node("c", provider=False)]))
    assert copied.text == "i-1\ni-2"
    link = plugin.run(request_for("console", [node("a", "i-9")]))
    assert len(link) == 1 and link[0].url.endswith("instanceId=i-9")  # one account: the plain console link
    assert plugin.run(request_for("nothing", [node("a")])) is None


def test_the_cost_action_totals_the_nodes_and_says_what_it_could_not_price(fake_prices):
    plugin = AwsPlugin()
    shown = plugin.run(request_for("cost", [node("a", "i-1"), node("b", "i-2", itype="c6g.xlarge"),
                                            node("s", "i-3", capacity="spot"), node("odd", "i-4", itype="zz9.huge")]))
    text = shown.text
    assert shown.title == "AWS node cost estimate" and "Cost estimate for 4 node(s)" in text
    assert "$0.2320/hour" in text and "$169.36/month" in text  # (0.096 + 0.136) * 730
    assert "Not counted (no price known): s, odd" in text
    assert "no EBS" in text


def test_the_cost_report_includes_the_notes(monkeypatch):
    monkeypatch.setattr("klotse_plugins.aws.on_demand_prices",
                        lambda *a, **k: pricing.PriceTable({"m5.large": 0.1}, "using prices from 9 days ago: down"))
    shown = AwsPlugin().run(request_for("cost", [node("a")]))
    assert "Notes:" in shown.text and "using prices from 9 days ago" in shown.text


# ---- how it is bundled ---------------------------------------------------------------------------------------
def test_it_is_a_bundled_plugin_that_loads_and_is_off_until_enabled(qtbot, tmp_path):
    assert "klotse_plugins.aws" in BUNDLED_MODULES
    manager = PluginManager(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat), entry_points=False)
    found = next(f for f in manager.loaded if f.id == "aws")
    assert found.source == "bundled" and found.error == "" and not manager.is_enabled("aws")
    assert manager.columns_for("Node") == []
    manager.set_enabled("aws", True)
    qtbot.waitUntil(lambda: manager.is_enabled("aws"))
    assert [c[0] for c in manager.columns_for("Node")] == [
        "Instance ID", "Instance type", "Capacity", "AMI", "On-demand $/h", "Price $/h", "Price $/month", "Launched"]
    assert [a.key for _, actions in manager.actions_for("Node") for a in actions] == ["copy-id", "console", "cost"]
    assert manager.settings_for("aws") == {"use_aws_api": True, "aws_profile": "", "multi_account": False,
                                                 "sso_start_url": "", "role_name": "", "auto_login": False, "account_map": "", "profile_map": "", "keys_warning": "", "access_key_id": "", "secret_access_key": "", "session_token": ""}


def test_the_cache_folder_is_next_to_the_settings_file(tmp_path):
    manager = PluginManager(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat), entry_points=False)
    assert manager.cache_dir("aws") == tmp_path / "plugin-cache" / "aws"


def test_the_host_warns_in_the_plugins_page_but_still_uses_the_values(qtbot, tmp_path, fake_prices, monkeypatch):
    from klotse.plugins.host import PluginHost
    from klotse.k8s.models import NodeRow
    manager = PluginManager(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat), entry_points=False)
    manager.set_enabled("aws", True)
    qtbot.waitUntil(lambda: manager.is_enabled("aws"))
    monkeypatch.setattr(manager.get("aws").plugin, "_api", lambda profile: (_ for _ in ()).throw(Ec2Error("denied")))
    calls = []
    host = PluginHost(manager, "ctx", lambda fn, ok, *a, on_error=None: calls.append((fn, ok, on_error)))
    row = NodeRow("n1", "Ready", "worker", "v1", "10.0.0.1", 4.0, 16.0, "1d", node("n1").raw)
    host.rows_loaded("Node", [row])
    fn, ok, _ = calls.pop()
    ok(fn())
    host.apply("Node", [row])
    assert row.extra["plugin:aws:price_od"] == 0.096
    assert "denied" in manager.errors["aws"]


# ---- several AWS accounts ---------------------------------------------------------------------------------------
from klotse.plugins import Copy, Message, OpenUrl  # noqa: E402
from klotse_plugins.aws import console  # noqa: E402

ARN = "arn:aws:eks:eu-west-1:123456789012:cluster/prod"
PLAIN = "https://eu-west-1.console.aws.amazon.com/ec2/home?region=eu-west-1#InstanceDetails:instanceId=i-0abc123"


def settings(**values):
    return {"multi_account": True, "sso_start_url": "", "role_name": "", "account_map": "", **values}


def test_the_account_comes_from_an_eks_arn_context_or_the_map():
    assert console.account_from_context(ARN) == "123456789012"
    assert console.account_from_context("arn:aws-us-gov:eks:us-gov-west-1:210987654321:cluster/x") == "210987654321"
    assert console.account_from_context("minikube") is None and console.account_from_context(None) is None
    assert console.account_from_context("arn:aws:iam::123456789012:role/x") is None  # not a cluster
    mapping = console.parse_account_map("prod-eu=111111111111; staging = 222222222222,\nbad=12345\n=333333333333\nnoequals")
    assert mapping == {"prod-eu": "111111111111", "staging": "222222222222"}
    assert console.account_for("prod-eu", {"account_map": "prod-eu=111111111111"}) == "111111111111"
    assert console.account_for(ARN, {"account_map": f"{ARN}=999999999999"}) == "999999999999"  # the map wins
    assert console.account_for(ARN, {}) == "123456789012" and console.account_for("minikube", {}) is None


def test_with_one_account_the_link_is_the_plain_console_link():
    assert console.console_results(PLAIN, "anything", {"multi_account": False, "sso_start_url": "https://x.awsapps.com/start"}) == [OpenUrl(PLAIN)]
    assert console.console_results(PLAIN, "anything", {}) == [OpenUrl(PLAIN)]


def test_identity_center_signs_in_to_the_clusters_account_and_lands_on_the_instance():
    [link] = console.console_results(PLAIN, ARN, settings(sso_start_url="https://d-1234567890.awsapps.com/start", role_name="Admin Access"))
    assert isinstance(link, OpenUrl)
    assert link.url == ("https://d-1234567890.awsapps.com/start/#/console?account_id=123456789012&role_name=Admin%20Access"
                        "&destination=" + __import__("urllib.parse", fromlist=["quote"]).quote(PLAIN, safe=""))
    for typed in ("https://d-1234567890.awsapps.com", "https://d-1234567890.awsapps.com/", "https://d-1234567890.awsapps.com/start/"):
        [again] = console.console_results(PLAIN, ARN, settings(sso_start_url=typed, role_name="Admin Access"))
        assert again.url == link.url  # the /start part is added or tidied


def test_identity_center_needs_https_and_a_role_and_the_account():
    [message] = console.console_results(PLAIN, ARN, settings(sso_start_url="http://d-1.awsapps.com/start", role_name="R"))
    assert message.level == "warning" and "https://" in message.text
    [message] = console.console_results(PLAIN, ARN, settings(sso_start_url="https://d-1.awsapps.com/start"))
    assert message.level == "warning" and "role name" in message.text
    [message] = console.console_results(PLAIN, "my-renamed-context", settings(sso_start_url="https://d-1.awsapps.com/start", role_name="R"))
    assert message.level == "warning" and "my-renamed-context" in message.text and "my-renamed-context=123456789012" in message.text
    ok = console.console_results(PLAIN, "my-renamed-context", settings(sso_start_url="https://d-1.awsapps.com/start", role_name="R",
                                                                       account_map="my-renamed-context=444444444444"))
    assert ok[0].url.startswith("https://d-1.awsapps.com/start/#/console?account_id=444444444444")


def test_switching_role_opens_the_switch_page_and_puts_the_link_on_the_clipboard():
    copy, link, message = console.console_results(PLAIN, ARN, settings(role_name="deploy/Ops"))
    assert copy == Copy(PLAIN)
    assert link.url == "https://signin.aws.amazon.com/switchrole?account=123456789012&roleName=deploy/Ops&displayName=Klotse%20123456789012"
    assert isinstance(message, Message) and "deploy/Ops" in message.text and "clipboard" in message.text
    gov = console.console_results(PLAIN, "arn:aws-us-gov:eks:us-gov-west-1:210987654321:cluster/x", settings(role_name="R"), gov=True)
    assert gov[1].url.startswith("https://signin.amazonaws-us-gov.com/switchrole?account=210987654321")


def test_several_accounts_without_a_way_to_sign_in_says_what_to_set():
    [message] = console.console_results(PLAIN, ARN, settings())
    assert message.level == "warning" and "IAM Identity Center start URL" in message.text


def test_the_plugin_action_uses_the_context_and_its_settings():
    plugin = AwsPlugin()
    request = ActionRequest("aws", "console", "Node", ARN, [node("a", "i-9")],
                            {"multi_account": True, "sso_start_url": "https://d-1.awsapps.com/start", "role_name": "R",
                             "account_map": "", "use_aws_api": False, "aws_profile": ""})
    [link] = plugin.run(request)
    assert link.url.startswith("https://d-1.awsapps.com/start/#/console?account_id=123456789012&role_name=R&destination=")
    gov_request = ActionRequest("aws", "console", "Node", "arn:aws-us-gov:eks:us-gov-east-1:210987654321:cluster/x",
                                [node("a", "i-9", zone="us-gov-east-1a")],
                                {"multi_account": True, "role_name": "R", "account_map": "", "sso_start_url": ""})
    assert "signin.amazonaws-us-gov.com" in plugin.run(gov_request)[1].url
