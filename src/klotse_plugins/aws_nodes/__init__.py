"""AWS nodes: what the nodes of an EKS (or other AWS) cluster are and cost, as columns and right-click actions.

Without any login it shows, from the Node objects, the instance ID, instance type, capacity (on-demand or spot) and, on
managed node groups, the AMI; and, from AWS's public price list, the on-demand price per hour and per month. If boto3 is
installed and AWS credentials work it also shows the real launch time and AMI of every node (Karpenter nodes carry no
AMI label) and current spot prices. Prices are list prices for the instance only: no EBS, data transfer, EKS fee,
savings plans or reserved instances.
"""
import threading
from collections import defaultdict
from datetime import datetime, timezone

from klotse.plugins import Action, Column, Copy, OpenUrl, Plugin, Setting, ShowText

from .ec2 import Ec2, Ec2Error, Ec2Unavailable, boto3_session
from .nodeinfo import parse_node
from .pricing import HOURS_PER_MONTH, PriceError, on_demand_prices


def per_hour(value):
    return f"${value:.4f}"


def per_month(value):
    return f"${value:,.2f}"


def when(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def console_url(info):
    host = "console.amazonaws-us-gov.com" if info.region.startswith("us-gov-") else f"{info.region}.console.aws.amazon.com"
    return f"https://{host}/ec2/home?region={info.region}#InstanceDetails:instanceId={info.instance_id}"


class AwsNodes(Plugin):
    id = "aws-nodes"
    name = "AWS nodes"
    version = "0.1"
    requires_api = 3
    refresh_s = 1800  # spot prices move; the rest is cheap to ask again
    description = ("Instance ID, type, capacity, AMI, on-demand and spot price and launch time of AWS nodes. "
                   "Works without a login (the cluster's own data plus AWS's public price list); with boto3 and AWS "
                   "credentials it also shows spot prices, the real launch time and the AMI of every node.")
    columns = (
        Column("instance_id", "Instance ID"),
        Column("instance_type", "Instance type"),
        Column("capacity", "Capacity"),
        Column("ami", "AMI"),
        Column("price_od", "On-demand $/h", format=per_hour),
        Column("price_now", "Price $/h", format=per_hour),
        Column("price_month", "Price $/month", format=per_month),
        Column("launched", "Launched", format=when),
    )
    actions = (
        Action("copy-id", "Copy instance ID", multi=True),
        Action("console", "Open in the EC2 console"),
        Action("cost", "Estimate the cost of the selected nodes", multi=True),
    )
    settings = (
        Setting("use_aws_api", "Use the AWS API when boto3 and credentials are available", True, kind="bool",
                help="Adds spot prices, the real launch time and the AMI of every node. Untick to use only the cluster "
                     "and the public price list."),
        Setting("aws_profile", "AWS profile", "",
                help="A profile from your AWS config. Empty uses the default credentials."),
    )
    session_factory = staticmethod(boto3_session)  # replaced in tests

    def __init__(self):
        self._ec2 = {}  # profile -> Ec2
        self._lock = threading.Lock()

    # ---- the AWS API (optional) --------------------------------------------------------------
    def _api(self, profile):
        with self._lock:
            if profile not in self._ec2:
                session = self.session_factory(profile)
                self._ec2[profile] = Ec2(lambda region: session.client("ec2", region_name=region))
            return self._ec2[profile]

    def _from_aws(self, nodes, settings, warn):
        """(instance details, spot prices) from AWS; empty when it is off, boto3 is missing or AWS says no."""
        if not settings.get("use_aws_api", True):
            return {}, {}
        try:
            api = self._api(str(settings.get("aws_profile") or ""))
        except Ec2Unavailable:
            return {}, {}  # an optional extra: nothing to complain about
        except Ec2Error as error:
            warn(f"AWS API: {error} (using the cluster data and the public prices instead)")
            return {}, {}
        by_region, details, spot = defaultdict(list), {}, {}
        for node in nodes.values():
            if node.region:
                by_region[node.region].append(node)
        for region, group in sorted(by_region.items()):
            try:
                details.update(api.instance_details(region, [n.instance_id for n in group]))
                spot_types = [n.instance_type for n in group
                              if n.capacity == "spot" or details.get(n.instance_id, {}).get("spot")]
                for (itype, zone), price in api.spot_prices(region, spot_types).items():
                    spot[(region, itype, zone)] = price
            except Ec2Error as error:
                warn(f"AWS API ({region}): {error} (using the cluster data and the public prices instead)")
        return details, spot

    # ---- the numbers --------------------------------------------------------------------------
    def collect(self, resources, settings, cache_dir, warn):
        """{resource key: {column key: value}} for the AWS nodes among `resources` (the others are left out)."""
        nodes = {}
        for resource in resources:
            info = parse_node(resource)
            if info is not None:
                nodes[resource.key] = info
        result = {key: {"instance_id": n.instance_id, "instance_type": n.instance_type or None,
                        "capacity": n.capacity, "ami": n.ami or None} for key, n in nodes.items()}
        if not nodes:
            return result
        tables = {}
        for region, os_name in sorted({(n.region, n.os) for n in nodes.values()}):
            try:
                table = on_demand_prices(region, os_name, cache_dir)
            except PriceError as error:
                warn(str(error))
                continue
            if table.note:
                warn(f"{region}: {table.note}")
            tables[(region, os_name)] = table.prices
        details, spot = self._from_aws(nodes, settings, warn)
        for key, node in nodes.items():
            extra = details.get(node.instance_id) or {}
            capacity = "spot" if extra.get("spot") else node.capacity
            on_demand = tables.get((node.region, node.os), {}).get(node.instance_type)
            if capacity == "spot":  # a spot node is not billed at the on-demand rate: unknown without AWS's answer
                price = spot.get((node.region, node.instance_type, extra.get("zone") or node.zone))
            else:
                price = on_demand
            result[key].update(
                capacity=capacity, ami=extra.get("ami") or node.ami or None, price_od=on_demand, price_now=price,
                price_month=price * HOURS_PER_MONTH if price is not None else None, launched=extra.get("launched"))
        return result

    # ---- the plugin interface -------------------------------------------------------------------
    def enrich(self, request):
        return self.collect(request.resources, request.settings, request.cache_dir, request.warn)

    def applies(self, action, resource):
        return parse_node(resource) is not None

    def run(self, request):
        infos = [(r, parse_node(r)) for r in request.resources]
        infos = [(r, info) for r, info in infos if info is not None]
        if request.action == "copy-id":
            return Copy("\n".join(info.instance_id for _, info in infos))
        if request.action == "console":
            return OpenUrl(console_url(infos[0][1]))
        if request.action == "cost":
            return ShowText("AWS node cost estimate", self.cost_report(request, infos))
        return None

    def cost_report(self, request, infos):
        warnings = []
        values = self.collect([r for r, _ in infos], request.settings, request.cache_dir,
                              lambda text: warnings.append(text) if text not in warnings else None)
        rows, total_hour, unpriced = [], 0.0, []
        for resource, info in infos:
            v = values[resource.key]
            if v["price_now"] is None:
                unpriced.append(resource.name)
                rows.append(f"{resource.name}  {info.instance_type or '?'}  {v['capacity']}  no price")
                continue
            total_hour += v["price_now"]
            rows.append(f"{resource.name}  {info.instance_type or '?'}  {v['capacity']}  "
                        f"{per_hour(v['price_now'])}/h  {per_month(v['price_month'])}/month")
        lines = [f"Cost estimate for {len(infos)} node(s)", ""] + rows + [
            "", f"Total: {per_hour(total_hour)}/hour, {per_month(total_hour * HOURS_PER_MONTH)}/month "
                f"({HOURS_PER_MONTH} hours)"]
        if unpriced:
            lines.append(f"Not counted (no price known): {', '.join(unpriced)}")
        lines += ["", "List prices for the instances only: no EBS, data transfer, EKS fee, savings plans or reserved "
                      "instances. Spot nodes are counted at AWS's current spot price when it could be fetched."]
        if warnings:
            lines += ["", "Notes:"] + [f"- {w}" for w in warnings]
        return "\n".join(lines)


plugin = AwsNodes()
