"""Read-only EBS/ELB enrichment, with exact identifiers and no account-wide scans."""
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone

from .awscreds import CredentialSource
from .native import AwsHttpError, _strip_namespaces, _text, post
from .safe import parse_xml, valid_region
from .sigv4 import sign

KINDS = ("PersistentVolume", "PersistentVolumeClaim", "Service", "Ingress")
VOLUME = re.compile(r"(?:aws://[^/]+/)?(vol-[0-9a-f]+)$")
REGION = re.compile(r"([a-z]{2}(?:-gov)?-[a-z]+-\d+)")
DNS = re.compile(r"^([a-z0-9-]+)\.(?:([a-z0-9-]+)\.elb|elb\.([a-z0-9-]+))\.amazonaws\.com(?:\.cn)?$", re.I)


def default_region(context, settings):
    value = str(settings.get("cluster_region") or "").strip()
    if not value and str(context).startswith("arn:"):
        parts = context.split(":")
        value = parts[3] if len(parts) > 3 else ""
    return value if valid_region(value) else ""


def volume_ref(raw, fallback=""):
    spec, labels = raw.get("spec") or {}, (raw.get("metadata") or {}).get("labels") or {}
    csi, legacy = spec.get("csi") or {}, spec.get("awsElasticBlockStore") or {}
    value = csi.get("volumeHandle", "") if csi.get("driver") == "ebs.csi.aws.com" else legacy.get("volumeID", "")
    match = VOLUME.fullmatch(str(value))
    if not match:
        return None
    zone = labels.get("topology.kubernetes.io/zone") or labels.get("failure-domain.beta.kubernetes.io/zone") or str(value)
    region = labels.get("topology.kubernetes.io/region") or labels.get("failure-domain.beta.kubernetes.io/region")
    found = REGION.search(zone)
    region = region or (found.group(1) if found else fallback)
    return (region, match.group(1)) if valid_region(region) else None


def load_balancer_refs(raw):
    result = []
    for item in ((raw.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []:
        host = str(item.get("hostname") or "").lower().rstrip(".")
        # ELBv2 dual-stack DNS prepends a separate label before the ordinary
        # load-balancer DNS name.
        lookup_host = host.removeprefix("dualstack.")
        match = DNS.fullmatch(lookup_host)
        if match and valid_region(match.group(2) or match.group(3)):
            result.append((match.group(2) or match.group(3), lookup_host))
    return list(dict.fromkeys(result))


def lb_name(host):
    stem = host.split(".")[0].removeprefix("internal-")
    name, separator, suffix = stem.rpartition("-")
    return name if separator and re.fullmatch(r"[a-z0-9]+", suffix) and 1 <= len(name) <= 32 else ""


def console_url(region, kind, identifier):
    host = "console.amazonaws-us-gov.com" if region.startswith("us-gov-") else (
        f"{region}.console.amazonaws.cn" if region.startswith("cn-") else f"{region}.console.aws.amazon.com")
    part = "Volumes:volumeId=" if kind == "volume" else "LoadBalancers:search="
    return f"https://{host}/ec2/home?region={region}#{part}{urllib.parse.quote(identifier, safe='')}"


class InfrastructureApi:
    def __init__(self, profile, http=post, now=time.time):
        self.source, self.http, self.now = CredentialSource(profile), http, now

    def call(self, service, region, version, action, params):
        if not valid_region(region):
            raise ValueError("Invalid AWS region")
        creds = self.source.get()
        url = f"https://{service}.{region}.amazonaws.com{'.cn' if region.startswith('cn-') else ''}/"
        body = urllib.parse.urlencode(sorted({"Action": action, "Version": version, **params}.items())).encode()
        stamp = datetime.fromtimestamp(self.now(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        headers = sign("POST", url, {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}, body,
                       creds.access_key, creds.secret_key, region, service, stamp, creds.token or None)
        status, answer = self.http(url, headers, body)
        root = _strip_namespaces(parse_xml(answer))
        if status >= 400:
            error = root.find(".//Error")
            raise AwsHttpError(_text(error, "Code") if error is not None else "", _text(error, "Message") if error is not None else f"HTTP {status}")
        return root

    def volume(self, region, volume_id):
        root = self.call("ec2", region, "2016-11-15", "DescribeVolumes", {"VolumeId.1": volume_id})
        item = next((i for i in root.findall("./volumeSet/item") if _text(i, "volumeId") == volume_id), None)
        if item is None:
            return {}
        fields = {key: _text(item, tag) for key, tag in (
            ("id", "volumeId"), ("type", "volumeType"), ("state", "status"), ("size_gib", "size"),
            ("zone", "availabilityZone"), ("encrypted", "encrypted"), ("iops", "iops"), ("throughput_mib_s", "throughput"))}
        fields["attachments"] = "; ".join(f"{_text(a, 'instanceId')} {_text(a, 'device')} ({_text(a, 'status')})" for a in item.findall("./attachmentSet/item")) or "None"
        return fields

    def load_balancer(self, region, host, detailed=False):
        name = lb_name(host)
        if not name:
            return {}
        for version, param, path, kind in (("2015-12-01", "Names.member.1", ".//LoadBalancers/member", "v2"),
                                            ("2012-06-01", "LoadBalancerNames.member.1", ".//LoadBalancerDescriptions/member", "classic")):
            try:
                root = self.call("elasticloadbalancing", region, version, "DescribeLoadBalancers", {param: name})
            except AwsHttpError as error:
                if error.response["Error"]["Code"] in ("LoadBalancerNotFound", "LoadBalancerNotFoundException"):
                    continue
                raise
            item = next((i for i in root.findall(path)
                         if _text(i, "DNSName").lower().rstrip(".").removeprefix("dualstack.") == host), None)
            if item is None:
                continue
            arn = _text(item, "LoadBalancerArn")
            result = {"id": arn or name, "name": name, "dns": host, "type": _text(item, "Type") or kind,
                      "state": _text(item, "State/Code") or ("" if kind == "classic" else "Unknown"),
                      "scheme": _text(item, "Scheme"), "vpc": _text(item, "VpcId") or _text(item, "VPCId")}
            if kind == "classic":
                result["listeners"] = "; ".join(f"{_text(i, 'Listener/Protocol')}:{_text(i, 'Listener/LoadBalancerPort')} → {_text(i, 'Listener/InstanceProtocol')}:{_text(i, 'Listener/InstancePort')}" for i in item.findall("./ListenerDescriptions/member"))
                if detailed:
                    health = self.call("elasticloadbalancing", region, version, "DescribeInstanceHealth", {"LoadBalancerName": name})
                    result["target_health"] = "; ".join(f"{_text(i, 'InstanceId')}: {_text(i, 'State')}" for i in health.findall(".//InstanceStates/member"))
            elif detailed:
                listeners = self.call("elasticloadbalancing", region, version, "DescribeListeners", {"LoadBalancerArn": arn, "PageSize": "100"})
                result["listeners"] = "; ".join(f"{_text(i, 'Protocol')}:{_text(i, 'Port')}" for i in listeners.findall(".//Listeners/member"))
                groups = self.call("elasticloadbalancing", region, version, "DescribeTargetGroups", {"LoadBalancerArn": arn, "PageSize": "100"})
                group_items = groups.findall(".//TargetGroups/member")
                lines = []
                for group in group_items[:10]:
                    health = self.call("elasticloadbalancing", region, version, "DescribeTargetHealth", {"TargetGroupArn": _text(group, "TargetGroupArn")})
                    states = [f"{_text(i, 'Target/Id')}: {_text(i, 'TargetHealth/State')}" for i in health.findall(".//TargetHealthDescriptions/member")]
                    lines.append(f"{_text(group, 'TargetGroupName')}: " + ", ".join(states))
                result["target_health"] = "\n".join(lines)
                if len(group_items) > 10 or _text(groups, "DescribeTargetGroupsResult/NextMarker"):
                    result["note"] = "Only the first 10 target groups are inspected."
            return result
        return {}


class InfrastructureCache:
    def __init__(self, factory=InfrastructureApi, now=time.monotonic):
        self.factory, self.now, self.values, self.clients = factory, now, {}, {}
        self.lock = threading.Lock()

    def lookup(self, profile, region, identifier, kind, detailed=False):
        key = (profile, region, identifier, kind, detailed)
        with self.lock:
            cached = self.values.get(key)
            if cached and self.now() < cached[0]:
                return cached[1]
            if profile not in self.clients:
                self.clients[profile] = self.factory(profile)
            api = self.clients[profile]
        answer = api.volume(region, identifier) if kind == "volume" else api.load_balancer(region, identifier, detailed)
        with self.lock:
            if len(self.values) >= 2000:
                self.values.clear()
            self.values[key] = (self.now() + (300 if answer else 60), answer)
        return answer


def report(details):
    return "\n".join(f"{key.replace('_', ' ').capitalize()}: {value}" for key, value in details.items() if value not in (None, ""))
