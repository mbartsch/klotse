"""Upgrade preflight (pure, over data already read; no network, no Qt): what could go wrong when the control plane moves to a newer
Kubernetes minor version, or when nodes are drained for it. Findings have the shape of the Health check's (`severity`, `kind`,
`namespace`, `name`, `problem`, `hint`) so the same table can show them; `kind` names the check.

Four checks, like kubent / pluto, k8s-preflight and similar tools:
  * **Deprecated APIs.** The API server counts requests to deprecated API versions in `apiserver_requested_deprecated_apis` (read from
    /metrics when the account may); that is the exact list of what was *used* since it started. Without it, `served_deprecated`
    compares the versions the cluster serves with `REMOVALS`, a table of removals taken from the Kubernetes deprecation guide (up to
    1.32; later removals are not in it). A served version only tells that manifests or charts using it would break, not that any do.
  * **PodDisruptionBudgets** that allow no disruption: a drain waits on them for ever.
  * **Version skew** of the kubelets against the control plane before and after the upgrade (a kubelet may not be newer than the API
    server, and at most three minors older (two before 1.25)).
  * **Certificate dates** of cert-manager Certificates (their `status.notAfter`). Control plane certificates, kubelet certificates and
    plain TLS Secrets are not read: no Secret is ever fetched, and kubeadm/managed certificates are not visible through the API."""
import re
from datetime import datetime, timezone

ERROR, WARN, INFO = "error", "warn", "info"

# (group, version) -> the Kubernetes minor release that stops serving it (kubernetes.io/docs/reference/using-api/deprecation-guide)
REMOVALS = {
    ("admissionregistration.k8s.io", "v1beta1"): "1.22", ("apiextensions.k8s.io", "v1beta1"): "1.22",
    ("apiregistration.k8s.io", "v1beta1"): "1.22", ("authentication.k8s.io", "v1beta1"): "1.22",
    ("authorization.k8s.io", "v1beta1"): "1.22", ("certificates.k8s.io", "v1beta1"): "1.22",
    ("coordination.k8s.io", "v1beta1"): "1.22", ("extensions", "v1beta1"): "1.22", ("networking.k8s.io", "v1beta1"): "1.22",
    ("rbac.authorization.k8s.io", "v1beta1"): "1.22", ("scheduling.k8s.io", "v1beta1"): "1.22",
    ("autoscaling", "v2beta1"): "1.25", ("batch", "v1beta1"): "1.25", ("discovery.k8s.io", "v1beta1"): "1.25",
    ("events.k8s.io", "v1beta1"): "1.25", ("node.k8s.io", "v1beta1"): "1.25", ("policy", "v1beta1"): "1.25",
    ("autoscaling", "v2beta2"): "1.26", ("flowcontrol.apiserver.k8s.io", "v1beta1"): "1.26",
    ("storage.k8s.io", "v1beta1"): "1.27", ("flowcontrol.apiserver.k8s.io", "v1beta2"): "1.29",
    ("flowcontrol.apiserver.k8s.io", "v1beta3"): "1.32",
}
REPLACEMENTS = {  # what to move to
    ("autoscaling", "v2beta1"): "autoscaling/v2", ("autoscaling", "v2beta2"): "autoscaling/v2", ("batch", "v1beta1"): "batch/v1",
    ("policy", "v1beta1"): "policy/v1", ("discovery.k8s.io", "v1beta1"): "discovery.k8s.io/v1", ("events.k8s.io", "v1beta1"): "events.k8s.io/v1",
    ("node.k8s.io", "v1beta1"): "node.k8s.io/v1", ("networking.k8s.io", "v1beta1"): "networking.k8s.io/v1",
    ("extensions", "v1beta1"): "networking.k8s.io/v1 or apps/v1", ("rbac.authorization.k8s.io", "v1beta1"): "rbac.authorization.k8s.io/v1",
    ("storage.k8s.io", "v1beta1"): "storage.k8s.io/v1", ("flowcontrol.apiserver.k8s.io", "v1beta1"): "flowcontrol.apiserver.k8s.io/v1",
    ("flowcontrol.apiserver.k8s.io", "v1beta2"): "flowcontrol.apiserver.k8s.io/v1", ("flowcontrol.apiserver.k8s.io", "v1beta3"): "flowcontrol.apiserver.k8s.io/v1",
}

RESOURCE_HINTS = {  # a resource that is deprecated even though its version is current
    ("", "v1", "endpoints"): "Endpoints is deprecated (Kubernetes 1.33): read EndpointSlices (discovery.k8s.io/v1) instead.",
}


def minor_of(version):
    """The minor number of `v1.29.3-eks-abc`, `1.29` or `v1.29+` as an int, or None."""
    match = re.search(r"(\d+)\.(\d+)", str(version or ""))
    return int(match[2]) if match else None


def _release_key(text):
    match = re.search(r"(\d+)\.(\d+)", str(text))
    return (int(match[1]), int(match[2])) if match else (0, 0)


def finding(severity, kind, namespace, name, problem, hint=""):
    return {"severity": severity, "kind": kind, "namespace": namespace, "name": name, "problem": problem, "hint": hint}


def parse_deprecated_metrics(text):
    """[{group, version, resource, removed}] from the Prometheus text of /metrics: the lines of
    `apiserver_requested_deprecated_apis{group="policy",removed_release="1.25",resource="podsecuritypolicies",version="v1beta1"} 1`."""
    out = []
    for line in str(text).splitlines():
        if not line.startswith("apiserver_requested_deprecated_apis{"):
            continue
        labels = dict(re.findall(r'(\w+)="([^"]*)"', line.split("}", 1)[0]))
        out.append({"group": labels.get("group", ""), "version": labels.get("version", ""), "resource": labels.get("resource", ""),
                    "removed": labels.get("removed_release", "")})
    return sorted(out, key=lambda r: (_release_key(r["removed"]), r["group"], r["resource"]))


def _api_name(group, version):
    return f"{group}/{version}" if group else version


def deprecated_requested(requests, target):
    """Findings for the deprecated APIs the server saw requests for. Removed in or before the target: an error (the request will fail
    after the upgrade); later: a note."""
    out = []
    for r in requests:
        api, resource = _api_name(r["group"], r["version"]), r["resource"]
        replacement = REPLACEMENTS.get((r["group"], r["version"]))
        hint = (RESOURCE_HINTS.get((r["group"], r["version"], resource))
                or (f"Move to {replacement}." if replacement else "Move to the current version of this API."))
        if r["removed"] and _release_key(r["removed"]) <= (1, target):
            out.append(finding(ERROR, "Deprecated API", "", f"{resource} {api}",
                               f"requested; removed in {r['removed']}, which is not later than the target 1.{target}", hint))
        else:
            out.append(finding(INFO, "Deprecated API", "", f"{resource} {api}",
                               f"requested; deprecated, removed in {r['removed'] or 'a later release'}", hint))
    return out


def served_deprecated(served, target):
    """Findings from the (group, version) pairs the cluster serves when the server's request counts are not available: a served
    version that `REMOVALS` says is gone by the target is a warning (manifests, charts or controllers that still use it will fail)."""
    out = []
    for group, version in sorted(set(served)):
        removed = REMOVALS.get((group, version))
        if not removed:
            continue
        api = _api_name(group, version)
        replacement = REPLACEMENTS.get((group, version))
        hint = f"Check manifests, Helm charts and controllers for apiVersion {api}" + (f"; move to {replacement}." if replacement else ".")
        if _release_key(removed) <= (1, target):
            out.append(finding(WARN, "Deprecated API", "", api, f"still served; removed in {removed}, not later than the target 1.{target}", hint))
        else:
            out.append(finding(INFO, "Deprecated API", "", api, f"still served; removed in {removed}", hint))
    return out


def pdb_findings(pdbs):
    """PodDisruptionBudgets that allow no disruption: a drain or an upgrade waits on them for ever."""
    out = []
    for pdb in pdbs:
        meta, status = pdb.get("metadata") or {}, pdb.get("status") or {}
        if status.get("disruptionsAllowed", 1) != 0:
            continue
        healthy, desired, expected = status.get("currentHealthy", 0), status.get("desiredHealthy", 0), status.get("expectedPods", 0)
        problem = f"allows no disruption ({healthy} healthy, {desired} needed, {expected} pods)"
        hint = ("Fewer pods are healthy than the budget needs, so no pod can be evicted." if healthy < desired else
                "The budget needs all its pods, or there is only one: raise maxUnavailable or add replicas before draining.")
        out.append(finding(ERROR if healthy < desired else WARN, "PodDisruptionBudget", meta.get("namespace", ""), meta.get("name", ""), problem, hint))
    return out


def allowed_skew(minor):
    return 3 if minor is not None and minor >= 25 else 2


def skew_findings(server_version, nodes, target):
    """Kubelet versions against the API server now and after the control plane is upgraded to 1.<target>."""
    server = minor_of(server_version)
    out = []
    if server is None:
        return [finding(INFO, "Version skew", "", "API server", f"could not read the server version ({server_version})")]
    if target <= server:
        out.append(finding(INFO, "Version skew", "", "API server", f"is 1.{server}; the target 1.{target} is not newer"))
    for node in nodes:
        info = (node.get("status") or {}).get("nodeInfo") or {}
        name = (node.get("metadata") or {}).get("name", "")
        kubelet = minor_of(info.get("kubeletVersion"))
        if kubelet is None:
            continue
        limit = allowed_skew(kubelet)
        if kubelet > server:
            out.append(finding(ERROR, "Version skew", "", name, f"kubelet {info.get('kubeletVersion')} is newer than the API server 1.{server}",
                               "A kubelet must not be newer than the API server."))
        elif server - kubelet > limit:
            out.append(finding(ERROR, "Version skew", "", name, f"kubelet {info.get('kubeletVersion')} is already more than {limit} minors older "
                                                               f"than the API server 1.{server}", "Upgrade this node first."))
        elif target > server and target - kubelet > limit:
            out.append(finding(ERROR, "Version skew", "", name, f"kubelet {info.get('kubeletVersion')} would be more than {limit} minors older "
                                                               f"than the API server after the upgrade to 1.{target}", "Upgrade or replace this node before the control plane."))
        elif target > server and target - kubelet == limit:
            out.append(finding(WARN, "Version skew", "", name, f"kubelet {info.get('kubeletVersion')} would be exactly {limit} minors behind 1.{target}",
                               "Allowed, but there is no room for another control plane upgrade before the node is upgraded."))
    return out


def _parse_time(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def certificate_findings(certificates, now=None, warn_days=30):
    """cert-manager Certificates that are expired, near the end of their life, or not Ready (from their `status`; no Secret is read)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for cert in certificates:
        meta, status = cert.get("metadata") or {}, cert.get("status") or {}
        namespace, name = meta.get("namespace", ""), meta.get("name", "")
        not_after = _parse_time(status.get("notAfter"))
        ready = next((c.get("status") for c in status.get("conditions") or [] if c.get("type") == "Ready"), None)
        if not_after is not None:
            days = (not_after - now).total_seconds() / 86400
            if days < 0:
                out.append(finding(ERROR, "Certificate", namespace, name, f"expired {int(-days)} days ago", "cert-manager has not renewed it."))
                continue
            if days < warn_days:
                out.append(finding(WARN, "Certificate", namespace, name, f"expires in {int(days)} days",
                                   "It should have been renewed by now: check the Issuer and the cert-manager logs."
                                   if (_parse_time(status.get("renewalTime")) or not_after) < now else "Renewal is due soon."))
        if ready == "False":
            out.append(finding(WARN, "Certificate", namespace, name, "is not Ready", "See the Certificate's conditions."))
    return out


def check(data, target):
    """All findings for `data` (see `KubeService.preflight_data`) and the upgrade target minor, errors first.

    `data` keys: server_version (text), served ([(group, version)]), deprecated (the parsed metrics, or None when they could not be
    read), pdbs, nodes, certificates (manifests)."""
    out = []
    if data.get("deprecated") is not None:
        out += deprecated_requested(data["deprecated"], target)
    out += served_deprecated(data.get("served") or [], target) if data.get("deprecated") is None else []
    out += pdb_findings(data.get("pdbs") or [])
    out += skew_findings(data.get("server_version"), data.get("nodes") or [], target)
    out += certificate_findings(data.get("certificates") or [])
    order = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(out, key=lambda f: (order[f["severity"]], f["kind"], f["namespace"], f["name"]))


def default_target(server_version):
    """The next minor after the API server's (1.29 -> 30); None when the version cannot be read."""
    minor = minor_of(server_version)
    return None if minor is None else minor + 1
