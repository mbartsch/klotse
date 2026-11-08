"""Pod security findings across a cluster (pure, over pod manifests; no network, no Qt): which workloads break the Kubernetes Pod
Security Standards (kubernetes.io/docs/concepts/security/pod-security-standards), the rules a namespace's Pod Security Admission
enforces. Two profiles are checked:
  * **baseline**, what a pod must never do: privileged containers, host namespaces and host ports, hostPath volumes, capabilities beyond the
    default set, unconfined seccomp or AppArmor, custom SELinux, unmasked /proc, unsafe sysctls;
  * **restricted**, the hardened set on top of it: no privilege escalation, running as non-root with a non-zero user, a seccomp profile,
    all capabilities dropped (NET_BIND_SERVICE may be added) and only the safe volume types.
Pods are grouped by what runs them (a Deployment with 300 pods is one finding with a count). Advice, not a verdict: networking and
storage components in kube-system and similar namespaces legitimately need more. Only manifests are read."""
from .rightsizing import owner_of

ERROR, WARN, INFO = "error", "warn", "info"
BASELINE_CAPS = {"AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD", "NET_BIND_SERVICE", "SETFCAP", "SETGID",
                 "SETPCAP", "SETUID", "SYS_CHROOT"}
SAFE_SYSCTLS = {"kernel.shm_rmid_forced", "net.ipv4.ip_local_port_range", "net.ipv4.ip_unprivileged_port_start", "net.ipv4.tcp_syncookies",
                "net.ipv4.ping_group_range", "net.ipv4.ip_local_reserved_ports", "net.ipv4.tcp_keepalive_time", "net.ipv4.tcp_fin_timeout",
                "net.ipv4.tcp_keepalive_intvl", "net.ipv4.tcp_keepalive_probes"}
RESTRICTED_VOLUMES = {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "persistentVolumeClaim", "projected", "secret", "name"}
SELINUX_TYPES = {"", "container_t", "container_init_t", "container_kvm_t", "container_engine_t"}
SYSTEM_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease")
ENFORCE_LABEL = "pod-security.kubernetes.io/enforce"
APPARMOR_ANNOTATION = "container.apparmor.security.beta.kubernetes.io"  # the part before the "/<container name>"
# reasons that are about the node being reachable from the pod: the serious ones
SERIOUS = ("privileged", "host network", "host PID", "host IPC", "hostPath", "host port", "added capability")


def _containers(spec):
    return [c for key in ("containers", "initContainers", "ephemeralContainers") for c in spec.get(key) or []]


def baseline_reasons(pod):
    """What a pod does that the baseline profile forbids, as short phrases (no duplicates, stable order)."""
    spec, meta = pod.get("spec") or {}, pod.get("metadata") or {}
    reasons = []
    for flag, text in (("hostNetwork", "host network"), ("hostPID", "host PID"), ("hostIPC", "host IPC")):
        if spec.get(flag):
            reasons.append(text)
    psc = spec.get("securityContext") or {}
    if (psc.get("seccompProfile") or {}).get("type") == "Unconfined" or (psc.get("appArmorProfile") or {}).get("type") == "Unconfined":
        reasons.append("unconfined seccomp or AppArmor")
    if (psc.get("seLinuxOptions") or {}).get("user") or (psc.get("seLinuxOptions") or {}).get("role") or \
            (psc.get("seLinuxOptions") or {}).get("type", "") not in SELINUX_TYPES:
        reasons.append("custom SELinux options")
    if any(s.get("name") not in SAFE_SYSCTLS for s in psc.get("sysctls") or []):
        reasons.append("unsafe sysctl")
    if any("hostPath" in v for v in spec.get("volumes") or []):
        reasons.append("hostPath volume")
    for annotation, value in (meta.get("annotations") or {}).items():
        if annotation.partition("/")[0] == APPARMOR_ANNOTATION and value != "runtime/default" and value.partition("/")[0] != "localhost":
            reasons.append("unconfined seccomp or AppArmor")
    for c in _containers(spec):
        sc = c.get("securityContext") or {}
        if sc.get("privileged"):
            reasons.append("privileged container")
        added = {str(cap).upper() for cap in (sc.get("capabilities") or {}).get("add") or []}
        if added - BASELINE_CAPS:
            reasons.append("added capability " + ", ".join(sorted(added - BASELINE_CAPS)))
        if any(p.get("hostPort") for p in c.get("ports") or []):
            reasons.append("host port")
        if (sc.get("seccompProfile") or {}).get("type") == "Unconfined" or (sc.get("appArmorProfile") or {}).get("type") == "Unconfined":
            reasons.append("unconfined seccomp or AppArmor")
        if sc.get("procMount") not in (None, "Default"):
            reasons.append("unmasked /proc")
        selinux = sc.get("seLinuxOptions") or {}
        if selinux.get("user") or selinux.get("role") or selinux.get("type", "") not in SELINUX_TYPES:
            reasons.append("custom SELinux options")
    return list(dict.fromkeys(reasons))


def restricted_reasons(pod):
    """What a pod does that the restricted profile (on top of baseline) forbids."""
    spec = pod.get("spec") or {}
    psc = spec.get("securityContext") or {}
    reasons = []
    if any(key not in RESTRICTED_VOLUMES and key != "hostPath" for v in spec.get("volumes") or [] for key in v):
        reasons.append("volume type outside the restricted set")
    containers = _containers(spec)
    for c in containers:
        sc = c.get("securityContext") or {}
        if sc.get("allowPrivilegeEscalation") is not False:
            reasons.append("privilege escalation allowed")
        non_root = sc.get("runAsNonRoot", psc.get("runAsNonRoot"))
        if non_root is not True:
            reasons.append("may run as root")
        if sc.get("runAsUser", psc.get("runAsUser")) == 0:
            reasons.append("runs as user 0")
        seccomp = (sc.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type")
        if seccomp not in ("RuntimeDefault", "Localhost"):
            reasons.append("no seccomp profile")
        caps = sc.get("capabilities") or {}
        if "ALL" not in {str(d).upper() for d in caps.get("drop") or []}:
            reasons.append("does not drop all capabilities")
        if {str(a).upper() for a in caps.get("add") or []} - {"NET_BIND_SERVICE"}:
            reasons.append("adds capabilities beyond NET_BIND_SERVICE")
    return list(dict.fromkeys(reasons))


def finding(severity, kind, namespace, name, problem, hint=""):
    return {"severity": severity, "kind": kind, "namespace": namespace, "name": name, "problem": problem, "hint": hint}


def _serious(reasons):
    return any(r.startswith(SERIOUS) for r in reasons)


def check(data):
    """Findings for `data` = {"pods": [manifests], "namespaces": [manifests]}, one per workload and set of reasons, errors first.

    error: baseline violation that reaches the node (privileged, host namespaces, hostPath, host port, extra capabilities);
    warn: any other baseline violation; info: only the restricted profile is broken; namespaces without enforcement are notes."""
    groups = {}
    with_pods = set()
    for pod in data.get("pods") or []:
        meta = pod.get("metadata") or {}
        namespace = meta.get("namespace", "")
        with_pods.add(namespace)
        if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        row = _Row(pod, meta.get("name", ""), namespace)
        base, hardened = baseline_reasons(pod), restricted_reasons(pod)
        if not base and not hardened:
            continue
        kind, name = owner_of(row)
        if kind == "Node":  # a static pod (the control plane's own): owned by the node it runs on
            kind, name = "Static pods", f"on {name}"
        key = (namespace, kind, name, tuple(base), tuple(hardened))
        groups[key] = groups.get(key, 0) + 1
    out = []
    for (namespace, kind, name, base, hardened), count in groups.items():
        system = namespace in SYSTEM_NAMESPACES
        note = "System components often need this; check it is expected. " if system else ""
        pods = f" ({count} pods)" if count > 1 else ""
        if base:
            severity = ERROR if _serious(base) else WARN
            out.append(finding(severity, kind, namespace, name, "breaks the baseline profile: " + "; ".join(base) + pods,
                               note + "Pod Security Admission at baseline or restricted would refuse it."))
        elif hardened:
            out.append(finding(INFO, kind, namespace, name, "breaks the restricted profile: " + "; ".join(hardened) + pods,
                               note + "Fine under baseline; harden it to run in a namespace that enforces restricted."))
    for ns in data.get("namespaces") or []:
        meta = ns.get("metadata") or {}
        name = meta.get("name", "")
        if name in with_pods and ENFORCE_LABEL not in (meta.get("labels") or {}):
            out.append(finding(INFO, "Namespace", "", name, "has no Pod Security Admission enforcement",
                               f"Label it {ENFORCE_LABEL}=baseline (or restricted) to refuse such pods."))
    order = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(out, key=lambda f: (order[f["severity"]], f["namespace"], f["kind"], f["name"]))


class _Row:
    """The little `owner_of` needs: a pod's manifest and name."""

    def __init__(self, raw, name, namespace):
        self.raw, self.name, self.namespace = raw, name, namespace
