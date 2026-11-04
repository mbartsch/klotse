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

from klotse.plugins import Action, Column, Copy, Message, OpenUrl, OverviewCard, Plugin, Setting, ShowText

from .console import account_for, console_results, profile_for
from .ec2 import Ec2Error, Ec2Unavailable, boto3_session, default_ec2
from .identity import caller_account
from .federation import FederationError, console_credentials, fetch_json, login_url
from .instances import InstanceCache
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
    requires_api = 6
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
    overview_cards = (
        OverviewCard("cost_hour", "Cost per hour"),
        OverviewCard("cost_month", "Cost per month"),
        OverviewCard("priced", "Nodes priced"),
    )
    settings = (
        Setting("use_aws_api", "Use the AWS API when credentials are available", True, kind="bool",
                help="Looks up, by instance ID, the AMI the node booted from and its launch time, and current spot prices. "
                     "It uses boto3 if installed, otherwise the credentials of the AWS CLI (or the AWS_* variables). Untick "
                     "to use only the cluster and the public price list."),
        Setting("aws_profile", "AWS profile (default for all clusters)", "",
                help="A profile from your AWS config. Empty uses the default credentials."),
        Setting("profile_map", "AWS profile for each cluster", "", kind="multiline",
                help="Clusters in other accounts need their own profile. One per line, as context = profile; the context is "
                     "the cluster's name in your kubeconfig. A cluster that is not listed uses the default profile above."),
        Setting("multi_account", "I use several AWS accounts in the console", False, kind="bool",
                help="Then \"Open in the EC2 console\" has to say which account to open. Fill in the three settings below."),
        Setting("sso_start_url", "IAM Identity Center start URL", "",
                help="For example https://d-1234567890.awsapps.com/start . The link then signs in to the cluster's account "
                     "and opens the instance page."),
        Setting("role_name", "Role name", "",
                help="With IAM Identity Center: the permission set to sign in with. Without it: a role to switch to "
                     "(the instance link is put on the clipboard to paste after switching)."),
        Setting("auto_login", "Sign in to the console automatically (needs boto3 and credentials)", False, kind="bool",
                help="Uses your AWS credentials (the profile above) to ask AWS for a one-time sign-in link and opens it in "
                     "your browser, so there is no login page. The link is never shown, logged or kept. With several "
                     "accounts it assumes the role named above in the cluster's account. Not used with IAM Identity "
                     "Center, whose portal signs you in."),
        Setting("account_map", "Account for each cluster", "", kind="multiline",
                help="Which account a cluster is in, one per line as context = 123456789012. Only needed when the "
                     "context is not the EKS ARN that 'aws eks update-kubeconfig' creates (it contains the account id)."),
    )
    cluster_settings = (  # Plugins menu -> AWS nodes -> settings for the cluster in front
        Setting("cluster_profile", "AWS profile", "",
                help="The profile from your AWS config used for this cluster's EC2 lookups, cost estimate and console "
                     "sign-in. Empty uses the entry in the plugin's settings, or the default profile."),
        Setting("cluster_account", "AWS account ID", "", pattern=r"\d{12}",
                help="Usually leave empty: the account is taken from the plugin's per-cluster list, the EKS ARN in the "
                     "context name, or else asked from AWS (sts get-caller-identity) for the profile above. Fill it in "
                     "only to override that."),
    )
    session_factory = staticmethod(boto3_session)  # for the sign-in (STS needs boto3); replaced in tests
    ec2_factory = staticmethod(default_ec2)  # boto3 if installed, else the built-in client; replaced in tests
    federation_fetch = staticmethod(fetch_json)  # replaced in tests
    account_lookup = staticmethod(caller_account)  # (profile, region) -> account id; replaced in tests

    def __init__(self):
        self._instances = InstanceCache()  # what AWS said about each instance: asked once, kept for good
        self._ec2 = {}  # profile -> Ec2
        self._accounts = {}  # profile -> account id it signs in to (STS GetCallerIdentity)
        self._lock = threading.Lock()

    # ---- the AWS API (optional) --------------------------------------------------------------
    def _api(self, profile):
        with self._lock:
            if profile not in self._ec2:
                self._ec2[profile] = self.ec2_factory(profile)
            return self._ec2[profile]

    def _from_aws(self, nodes, settings, warn, cache_dir=None, profile=None):
        """(instance details, spot prices, boto3 missing) from AWS; empty when it is off, boto3 is missing or AWS says no.

        The details (AMI, launch time, zone, spot or not) never change for an instance, so each instance is asked about
        once and then kept for good (see instances.py); later rounds only ask for new instances, and not at all (so
        without credentials too) when all are known. Only spot prices are fetched again every time."""
        if not settings.get("use_aws_api", True):
            return {}, {}, False
        profile = str(settings.get("aws_profile") or "") if profile is None else profile
        state = {"api": None, "no_boto3": False, "failed": False}

        def api():
            """The AWS client, made on first need; None when boto3 is missing or the profile is unusable."""
            if state["api"] is None and not state["no_boto3"] and not state["failed"]:
                try:
                    state["api"] = self._api(profile)
                except Ec2Unavailable:
                    state["no_boto3"] = True  # an optional extra: only worth a word when it leaves something blank
                except Ec2Error as error:
                    warn(f"AWS API: {error} (using the cluster data and the public prices instead)")
                    state["failed"] = True
            return state["api"]

        by_region, details, spot = defaultdict(list), {}, {}
        for node in nodes.values():
            if node.region:
                by_region[node.region].append(node)
        for region, group in sorted(by_region.items()):
            known, missing = self._instances.lookup(cache_dir, region, [n.instance_id for n in group])
            details.update(known)
            client = api() if missing else None
            if client is not None:
                try:
                    fresh = client.instance_details(region, missing)
                except Ec2Error as error:
                    warn(f"AWS API ({region}): {error} (using the cluster data and the public prices instead)")
                    fresh = {}
                self._instances.store(cache_dir, region, fresh)
                details.update(fresh)
            spot_types = [n.instance_type for n in group
                          if n.capacity == "spot" or details.get(n.instance_id, {}).get("spot")]
            client = api() if spot_types else None
            if client is not None:
                try:
                    for (itype, zone), price in client.spot_prices(region, spot_types).items():
                        spot[(region, itype, zone)] = price
                except Ec2Error as error:
                    warn(f"AWS API ({region}): {error} (using the cluster data and the public prices instead)")
        return details, spot, state["no_boto3"]

    # ---- the numbers --------------------------------------------------------------------------
    def collect(self, resources, settings, cache_dir, warn, context=""):
        """{resource key: {column key: value}} for the AWS nodes among `resources` (the others are left out). `context`
        is the cluster they come from: it decides which AWS profile is used."""
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
        details, spot, no_boto3 = self._from_aws(nodes, settings, warn, cache_dir, profile_for(context, settings))
        without_ami = [n for n in nodes.values() if not n.ami and not (details.get(n.instance_id) or {}).get("ami")]
        if no_boto3 and without_ami:  # these AMIs can only come from EC2, which needs boto3 and credentials
            warn(f"{len(without_ami)} node(s) have no node group image label, so their AMI can only be looked up in "
                 f"AWS, which needs boto3 or the AWS CLI with credentials")
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
        return self.collect(request.resources, request.settings, request.cache_dir, request.warn, request.context)

    def overview(self, request):
        """The cluster's total cost, like eks-node-viewer's header: the current price of every node that has one."""
        values = self.collect(request.nodes, request.settings, request.cache_dir, request.warn, request.context)
        if not values:
            return {}  # not an AWS cluster: no cards
        priced = [v["price_now"] for v in values.values() if v["price_now"] is not None]
        total = sum(priced)
        return {"cost_hour": per_hour(total), "cost_month": per_month(total * HOURS_PER_MONTH),
                "priced": f"{len(priced)} of {len(values)}"}

    def applies(self, action, resource):
        return parse_node(resource) is not None

    def run(self, request):
        infos = [(r, parse_node(r)) for r in request.resources]
        infos = [(r, info) for r, info in infos if info is not None]
        if request.action == "copy-id":
            return Copy("\n".join(info.instance_id for _, info in infos))
        if request.action == "console":
            return self.open_console(request, infos[0][1])
        if request.action == "cost":
            return ShowText("AWS node cost estimate", self.cost_report(request, infos))
        return None

    def with_account(self, request, region):
        """The settings, with `cluster_account` filled in from the profile's identity (STS GetCallerIdentity) when the
        account is not known any other way (menu, account map, EKS ARN) and several accounts are in use. A failed lookup
        leaves the settings alone: the usual "cannot tell which account" message then follows."""
        settings = request.settings
        if not settings.get("multi_account") or account_for(request.context, settings):
            return settings
        profile = profile_for(request.context, settings)
        with self._lock:
            known = self._accounts.get(profile)
        if known is None:
            try:
                known = self.account_lookup(profile, region)
            except Ec2Error as error:
                request.log and request.log(f"Could not get the account of the AWS profile {profile or '(default)'}: {error}")
                return settings
            with self._lock:
                self._accounts[profile] = known
        return {**settings, "cluster_account": known}

    def open_console(self, request, info):
        """The results for "Open in the EC2 console": a signed-in link when asked and possible, else the normal link."""
        settings, destination = self.with_account(request, info.region), console_url(info)
        gov = info.region.startswith("us-gov-")

        def normal():
            return console_results(destination, request.context, settings, gov=gov)

        if not settings.get("auto_login") or str(settings.get("sso_start_url") or "").strip():
            return normal()  # IAM Identity Center signs in through its own portal
        try:
            multi = bool(settings.get("multi_account"))
            account = account_for(request.context, settings) if multi else None
            role = str(settings.get("role_name") or "").strip() if multi else ""
            if multi and not (account and role):
                raise FederationError("with several accounts it needs the cluster's account and a role name to assume")
            session = self.session_factory(profile_for(request.context, settings))
            url = login_url(console_credentials(session, account, role, gov), destination, gov, fetch=self.federation_fetch)
        except Ec2Unavailable:
            return [Message("Automatic sign-in needs boto3, which is not installed here. Opening the normal link.",
                            "warning"), *normal()]
        except (FederationError, Ec2Error) as error:
            return [Message(f"Automatic sign-in did not work: {error}. Opening the normal link instead.", "warning"),
                    *normal()]
        return [OpenUrl(url)]  # a one-time secret: straight to the browser, never logged or shown

    def cost_report(self, request, infos):
        warnings = []
        values = self.collect([r for r, _ in infos], request.settings, request.cache_dir,
                              lambda text: warnings.append(text) if text not in warnings else None, request.context)
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
