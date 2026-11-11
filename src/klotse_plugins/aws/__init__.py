"""AWS Plugin: what the nodes of an EKS (or other AWS) cluster are and cost, as columns and right-click actions.

From Node objects it shows the instance ID, type and capacity; it reads on-demand prices from AWS price lists (China's
Price List Query API requires AWS credentials with `pricing:GetProducts`). When AWS credentials work, it can also show
the real launch time, AMI and current spot price. Prices are list prices for the instance only: no EBS, data transfer,
EKS fee, savings plans or reserved instances.
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
from .pricing import HOURS_PER_MONTH, Price, PriceError, currency_for_region, on_demand_prices
from .awscreds import CredentialsError
from .infrastructure import (KINDS as INFRA_KINDS, InfrastructureCache, default_region,
                             load_balancer_refs, volume_ref, report, console_url as infrastructure_console_url)


def per_hour(value):
    return f"{_currency_symbol(value)}{value:.4f}"


def per_month(value):
    return f"{_currency_symbol(value)}{value:,.2f}"


def _currency_symbol(value):
    return {"CNY": "¥"}.get(getattr(value, "currency", "USD"), "$")


def _price_totals(values):
    totals = defaultdict(float)
    for price in values:
        if price is not None:
            totals[getattr(price, "currency", "USD")] += float(price)
    return totals


def _format_totals(totals, monthly=False):
    formatted = []
    for currency, total in sorted(totals.items()):
        value = Price(total * HOURS_PER_MONTH, currency) if monthly else Price(total, currency)
        formatted.append(per_month(value) if monthly else per_hour(value))
    if formatted:
        return " / ".join(formatted)
    zero = Price(0)
    return per_month(zero) if monthly else per_hour(zero)


def when(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def console_url(info):
    host = "console.amazonaws-us-gov.com" if info.region.startswith("us-gov-") else f"{info.region}.console.aws.amazon.com"
    return f"https://{host}/ec2/home?region={info.region}#InstanceDetails:instanceId={info.instance_id}"


class AwsPlugin(Plugin):
    id = "aws"
    name = "AWS Plugin"
    version = "0.1"
    requires_api = 9
    refresh_s = 1800  # spot prices move; the rest is cheap to ask again
    description = ("Instance ID, type, capacity, AMI, on-demand and spot price and launch time of AWS nodes. "
                   "China region prices use the AWS Price List API and need AWS credentials with "
                   "pricing:GetProducts permission.")
    columns = (
        Column("aws_id", "AWS resource", kinds=INFRA_KINDS),
        Column("aws_type", "AWS type", kinds=INFRA_KINDS),
        Column("aws_state", "AWS state", kinds=INFRA_KINDS),
        Column("aws_region", "AWS region", kinds=INFRA_KINDS),
        Column("aws_details", "AWS details", kinds=INFRA_KINDS),
        Column("instance_id", "Instance ID"),
        Column("instance_type", "Instance type"),
        Column("capacity", "Capacity"),
        Column("ami", "AMI"),
        Column("price_od", "On-demand price/h", format=per_hour),
        Column("price_now", "Price/h", format=per_hour),
        Column("price_month", "Price/month", format=per_month),
        Column("launched", "Launched", format=when),
    )
    actions = (
        Action("resource-details", "AWS infrastructure details…", kinds=INFRA_KINDS),
        Action("resource-console", "Open AWS infrastructure console", kinds=INFRA_KINDS),
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
                     "China region prices also use the AWS Price List API and need pricing:GetProducts permission. "
                     "It uses boto3 if installed, otherwise the credentials of the AWS CLI (or the AWS_* variables). Untick "
                     "to skip EC2 metadata and Spot lookups; list-price lookup continues."),
        Setting("aws_profile", "AWS profile (default for all clusters)", "",
                help="A profile from your AWS config. Empty uses the default credentials."),
        Setting("profile_map", "AWS profile for each cluster", "", kind="multiline", section="Per-cluster overrides",
                help="Clusters in other accounts need their own profile. One per line, as context = profile; the context is "
                     "the cluster's name in your kubeconfig. A cluster that is not listed uses the default profile above."),
        Setting("keys_warning", "<b style=\"font-size:16px\">Warning: a typed access key is stored unprotected</b><br>The key is saved as <b>plain text</b> in Klotse's settings file on this computer. Anyone who can read that file, this computer, or a backup or synced copy of it can use the key. <b>The Klotse authors accept no responsibility for any loss, cost or damage that results from using it</b>: this is your choice and your risk.<br>Prefer an AWS profile or IAM Identity Center (the AWS CLI keeps those safer). If you do use a key, make it a dedicated read-only one (EC2 describe and STS only), never a root or administrator key, and prefer temporary keys with a session token.", kind="note", section="Credentials"),
        Setting("access_key_id", "AWS access key ID (optional)", "", section="Credentials",
                help="Instead of a profile. Needs the secret access key too. When both are filled in they are used for "
                     "every cluster and replace all profiles. Read the storage warning below."),
        Setting("secret_access_key", "AWS secret access key", "", kind="password", section="Credentials",
                help="Saved as plain text in the settings file. See the access key storage warning."),
        Setting("session_token", "AWS session token (optional)", "", kind="password", section="Credentials",
                help="Only for temporary keys (access key IDs starting with ASIA). Leave empty for long-lived keys. "
                     "Temporary keys expire: paste new ones here when they do."),
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
        Setting("account_map", "Account for each cluster", "", kind="multiline", section="Per-cluster overrides",
                help="Which account a cluster is in, one per line as context = 123456789012. Only needed when the "
                     "context is not the EKS ARN that 'aws eks update-kubeconfig' creates (it contains the account id)."),
    )
    cluster_settings = (  # Plugins menu -> AWS Plugin -> settings for the cluster in front
        Setting("cluster_region", "AWS region", "", pattern=r"[a-z]{2}(?:-gov)?-[a-z]+-\d+",
                help="For AWS volumes without a region in their topology labels. Empty uses an EKS ARN context's region. Load-balancer hostnames supply their own region."),
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
        self._infrastructure = InfrastructureCache()
        self._pv_manifests = {}

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
        profile = profile_for(context, settings)
        tables = {}
        for region, os_name in sorted({(n.region, n.os) for n in nodes.values()}):
            try:
                table = on_demand_prices(region, os_name, cache_dir, profile=profile)
            except PriceError as error:
                warn(str(error))
                continue
            if table.note:
                warn(f"{region}: {table.note}")
            tables[(region, os_name)] = table
        details, spot, no_boto3 = self._from_aws(nodes, settings, warn, cache_dir, profile)
        without_ami = [n for n in nodes.values() if not n.ami and not (details.get(n.instance_id) or {}).get("ami")]
        if no_boto3 and without_ami:  # these AMIs can only come from EC2, which needs boto3 and credentials
            warn(f"{len(without_ami)} node(s) have no node group image label, so their AMI can only be looked up in "
                 f"AWS, which needs boto3 or the AWS CLI with credentials")
        for key, node in nodes.items():
            extra = details.get(node.instance_id) or {}
            capacity = "spot" if extra.get("spot") else node.capacity
            table = tables.get((node.region, node.os))
            currency = getattr(table, "currency", currency_for_region(node.region))
            on_demand = (table.prices.get(node.instance_type) if table else None)
            if on_demand is not None:
                on_demand = Price(on_demand, currency)
            if capacity == "spot":  # a spot node is not billed at the on-demand rate: unknown without AWS's answer
                price = spot.get((node.region, node.instance_type, extra.get("zone") or node.zone))
            else:
                price = on_demand
            if price is not None:
                price = Price(price, currency)
            result[key].update(
                capacity=capacity, ami=extra.get("ami") or node.ami or None, price_od=on_demand, price_now=price,
                price_month=Price(price * HOURS_PER_MONTH, currency) if price is not None else None,
                launched=extra.get("launched"))
        return result

    # ---- the plugin interface -------------------------------------------------------------------
    def enrich(self, request):
        if request.kind in INFRA_KINDS:
            return self.collect_infrastructure(request)
        return self.collect(request.resources, request.settings, request.cache_dir, request.warn, request.context)

    def _infrastructure_refs(self, request, resource):
        if request.kind in ("Ingress", "Service"):
            return [(region, host, "load-balancer") for region, host in load_balancer_refs(resource.raw)]
        raw = resource.raw
        if request.kind == "PersistentVolumeClaim":
            name = (raw.get("spec") or {}).get("volumeName")
            factory = getattr(request, "api_client", None)
            if not name or factory is None:
                return []
            from time import monotonic
            key = (request.context, resource.key, resource.uid, name)
            with self._lock:
                cached = self._pv_manifests.get(key)
            if cached and monotonic() < cached[0]:
                raw = cached[1]
            else:
                from kubernetes.client import CoreV1Api
                client = factory()
                raw = client.sanitize_for_serialization(CoreV1Api(client).read_persistent_volume(name))
                claim = (raw.get("spec") or {}).get("claimRef") or {}
                if claim and (claim.get("name") != resource.name or claim.get("namespace") != resource.namespace or
                              (claim.get("uid") and resource.uid and claim["uid"] != resource.uid)):
                    return []
                with self._lock:
                    if len(self._pv_manifests) >= 2000:
                        self._pv_manifests.clear()
                    self._pv_manifests[key] = (monotonic() + 300, raw)
        ref = volume_ref(raw, default_region(request.context, request.settings))
        if ref:
            return [(*ref, "volume")]
        spec = raw.get("spec") or {}
        if (spec.get("csi") or {}).get("driver") == "ebs.csi.aws.com" or spec.get("awsElasticBlockStore"):
            raise ValueError("Cannot determine the EBS volume or AWS region; set the cluster's AWS region if topology labels are absent")
        return []

    def collect_infrastructure(self, request):
        result = {}
        if not request.settings.get("use_aws_api", True):
            return result
        profile = profile_for(request.context, request.settings)
        if len(request.resources) > 100:
            request.warn("AWS infrastructure: inspecting the first 100 resources in this batch")
        for resource in request.resources[:100]:
            try:
                refs = self._infrastructure_refs(request, resource)
                values = []
                for region, identifier, kind in refs[:5]:
                    data = self._infrastructure.lookup(profile, region, identifier, kind)
                    if data:
                        values.append({**data, "region": region})
                if values:
                    result[resource.key] = {"aws_id": "; ".join(v["id"] for v in values),
                        "aws_type": "; ".join(v.get("type", "") for v in values),
                        "aws_state": "; ".join(v.get("state", "") for v in values),
                        "aws_region": "; ".join(dict.fromkeys(v["region"] for v in values)),
                        "aws_details": "\n\n".join(report(v) for v in values)}
            except Exception as error:
                # Kubernetes/API exceptions may include a request or authentication output.
                # Report a bounded error class/code, never credentials or full response text.
                code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
                status = getattr(error, "status", "")
                detail = str(error)[:200] if isinstance(error, CredentialsError) else str(code or status)
                request.warn(f"AWS infrastructure lookup for {resource.key} failed ({type(error).__name__}"
                             f"{': ' + detail if detail else ''})")
        return result

    def overview(self, request):
        """The cluster's total cost, like eks-node-viewer's header: the current price of every node that has one."""
        values = self.collect(request.nodes, request.settings, request.cache_dir, request.warn, request.context)
        if not values:
            return {}  # not an AWS cluster: no cards
        priced = [v["price_now"] for v in values.values() if v["price_now"] is not None]
        totals = _price_totals(priced)
        return {"cost_hour": _format_totals(totals), "cost_month": _format_totals(totals, monthly=True),
                "priced": f"{len(priced)} of {len(values)}"}

    def applies(self, action, resource):
        if action in ("resource-details", "resource-console"):
            raw = resource.raw or {}
            return bool(load_balancer_refs(raw) or volume_ref(raw, "us-east-1") or (raw.get("spec") or {}).get("volumeName"))
        return parse_node(resource) is not None

    def run(self, request):
        if request.action in ("resource-details", "resource-console"):
            if not request.settings.get("use_aws_api", True):
                return Message("AWS API lookups are disabled in the plugin settings.")
            results = []
            profile = profile_for(request.context, request.settings)
            try:
                for resource in request.resources[:1]:
                    refs = self._infrastructure_refs(request, resource)
                    for region, identifier, kind in refs[:5]:
                        data = self._infrastructure.lookup(profile, region, identifier, kind, detailed=request.action == "resource-details")
                        if not data:
                            continue
                        if request.action == "resource-console":
                            results.extend(console_results(infrastructure_console_url(region, kind, data["id"]), request.context,
                                                           self.with_account(request, region), gov=region.startswith("us-gov-")))
                        else:
                            results.append(ShowText(f"AWS infrastructure: {resource.key}", report({"region": region, **data})))
            except Exception as error:
                code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
                return Message(f"Could not read AWS infrastructure ({type(error).__name__}{': ' + code if code else ''}). Check AWS credentials, region, and read permissions.", "warning")
            return results or Message("No matching AWS infrastructure was found for this resource. It may no longer exist, or its AWS identifier/region is unavailable.")
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
        rows, total_hour, unpriced = [], defaultdict(float), []
        for resource, info in infos:
            v = values[resource.key]
            if v["price_now"] is None:
                unpriced.append(resource.name)
                rows.append(f"{resource.name}  {info.instance_type or '?'}  {v['capacity']}  no price")
                continue
            currency = getattr(v["price_now"], "currency", "USD")
            total_hour[currency] += float(v["price_now"])
            rows.append(f"{resource.name}  {info.instance_type or '?'}  {v['capacity']}  "
                        f"{per_hour(v['price_now'])}/h  {per_month(v['price_month'])}/month")
        lines = [f"Cost estimate for {len(infos)} node(s)", ""] + rows + [
            "", f"Total: {_format_totals(total_hour)}/hour, {_format_totals(total_hour, monthly=True)}/month "
                f"({HOURS_PER_MONTH} hours)"]
        if unpriced:
            lines.append(f"Not counted (no price known): {', '.join(unpriced)}")
        lines += ["", "List prices for the instances only: no EBS, data transfer, EKS fee, savings plans or reserved "
                      "instances. Spot nodes are counted at AWS's current spot price when it could be fetched."]
        if warnings:
            lines += ["", "Notes:"] + [f"- {w}" for w in warnings]
        return "\n".join(lines)


plugin = AwsPlugin()
