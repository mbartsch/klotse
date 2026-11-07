import datetime
import functools
import itertools
import json
import time
from concurrent.futures import ThreadPoolExecutor
from heapq import heappush, heapreplace

from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity
from urllib3.exceptions import MaxRetryError

from . import connect_help, helm, related, rollout, search
from .timing import name_of, timings
from .models import DeploymentRow, NamespaceRow, NodeRow, PodRow, ReplicaSetRow, StatefulSetRow
from .api_index import ApiResource
from .resources import MIN_TS, SPEC_BY_KEY, SPEC_BY_KIND, map_custom_object, map_helm_release
from .util import format_age, to_dict as _raw  # format_age is re-exported for callers/tests


# pod states that mean a pod will not become ready without intervention
FAILING_STATES = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "InvalidImageName", "CreateContainerConfigError",
                  "CreateContainerError", "RunContainerError", "Error", "OOMKilled", "Failed", "Evicted"}


HUGE_PODS = 5000  # big-cluster mode: from this many pods the Overview counts by pod phase instead of reading every pod
OVERVIEW_LIST_CAP = 500  # most rows the overview keeps per drill-down list (pending pods, failed pods, warnings)


def pod_bucket(status):
    """running | pending | failed | done for a mapped pod status; completed pods are 'done'."""
    if status == "Running":
        return "running"
    if status in ("Succeeded", "Completed"):
        return "done"
    if status in ("Pending", "ContainerCreating", "PodInitializing", "Terminating") or status.startswith("Init:"):
        return "pending"
    return "failed"


def pod_buckets(pods):
    """(running, pending, failed) from mapped pod rows; completed pods count as none of them."""
    counts = {"running": 0, "pending": 0, "failed": 0, "done": 0}
    for p in pods:
        counts[pod_bucket(p.status)] += 1
    return counts["running"], counts["pending"], counts["failed"]


def keep_newest(heap, cap, ts, seq, item):
    """Bounded min-heap of the `cap` newest (ts, seq, item); seq breaks ties so items are never compared."""
    entry = (ts, seq, item)
    if len(heap) < cap:
        heappush(heap, entry)
    elif ts > heap[0][0]:
        heapreplace(heap, entry)


def map_namespace(ns):
    labels = ",".join(f"{k}={v}" for k, v in (ns.metadata.labels or {}).items())
    return NamespaceRow(ns.metadata.name, ns.status.phase or "", format_age(ns.metadata.creation_timestamp), labels, _raw(ns))


def map_pod(p, include_raw=True):
    statuses = p.status.container_statuses or []
    ready = sum(1 for c in statuses if c.ready)
    total = len(p.spec.containers or [])
    status = p.status.phase or ""
    for c in statuses:
        w = c.state.waiting if c.state else None
        if w and w.reason:
            status = w.reason
            break
    if p.metadata.deletion_timestamp:
        status = "Terminating"
    return PodRow(
        p.metadata.name, p.metadata.namespace, f"{ready}/{total}", status,
        sum(c.restart_count for c in statuses), p.spec.node_name or "",
        format_age(p.metadata.creation_timestamp), _raw(p) if include_raw else {},
    )


def map_node(n, include_raw=True):
    labels = n.metadata.labels or {}
    roles = ",".join(sorted(k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/"))) or "<none>"
    conditions = n.status.conditions or []
    ready = next((c.status for c in conditions if c.type == "Ready"), "Unknown")
    statuses = [{"True": "Ready", "False": "NotReady"}.get(ready, "Unknown")]
    if n.spec and n.spec.unschedulable:
        statuses.append("SchedulingDisabled")
    for condition in conditions:
        if condition.type == "Ready":
            continue
        if condition.status == "True":
            statuses.append(condition.type)
        elif condition.status not in ("True", "False"):
            statuses.append(f"{condition.type}=Unknown")
    ip = next((a.address for a in n.status.addresses or [] if a.type == "InternalIP"), "")
    alloc = n.status.allocatable or {}
    return NodeRow(
        n.metadata.name, ",".join(statuses), roles,
        n.status.node_info.kubelet_version if n.status.node_info else "", ip,
        float(parse_quantity(alloc.get("cpu", 0))), float(parse_quantity(alloc.get("memory", 0))) / 2**30,
        format_age(n.metadata.creation_timestamp), _raw(n) if include_raw else {},
    )


def map_replicaset(r):
    owner = next((f"{o.kind}/{o.name}" for o in r.metadata.owner_references or []), "")
    return ReplicaSetRow(
        r.metadata.name, r.metadata.namespace, r.spec.replicas or 0, r.status.replicas or 0,
        r.status.ready_replicas or 0, owner, format_age(r.metadata.creation_timestamp), _raw(r),
    )


def map_deployment(d):
    st = d.status
    return DeploymentRow(
        d.metadata.name, d.metadata.namespace, f"{st.ready_replicas or 0}/{d.spec.replicas or 0}",
        st.updated_replicas or 0, st.available_replicas or 0, format_age(d.metadata.creation_timestamp), _raw(d),
    )


def map_statefulset(s):
    return StatefulSetRow(
        s.metadata.name, s.metadata.namespace, f"{s.status.ready_replicas or 0}/{s.spec.replicas or 0}",
        format_age(s.metadata.creation_timestamp), _raw(s),
    )


class ReadOnlyError(RuntimeError):
    """A change was asked of a cluster that is in read-only mode."""


READ_ONLY_MESSAGE = "This cluster is in read-only mode, so nothing was changed. View > Read-only mode turns it off."


def mutating(method):
    """Marks a KubeService method that changes the cluster: it refuses to run while `read_only` is set. This is the safety
    net under the UI, which already refuses earlier and explains; nothing that reaches the service can slip past it."""
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        if getattr(self, "read_only", False):
            raise ReadOnlyError(READ_ONLY_MESSAGE)
        try:
            result = method(self, *args, **kwargs)
        except Exception as error:
            _audit(self, method.__name__, args, "failed", f"{type(error).__name__}: {error}")
            raise
        _audit(self, method.__name__, args, "ok")
        return result
    return guarded


def _audit(service, action, args, result, detail=""):
    """Tell the change log (service.audit, set by the workspace) what was done. Only the action and the objects' identity go
    in: see ui/audit_log.describe. Never raises."""
    record = getattr(service, "audit", None)
    if record is None:
        return
    try:
        from .audit import target_of
        record(action, target_of(args), result, detail)
    except Exception:  # noqa: BLE001 - the log must never break or hide a change
        pass


PAGE_SIZE = 500
BIG_PAGE_SIZE = 2000  # big-cluster mode: fewer round trips (each costs 0.3 to 0.4 s through a proxy)


class _PagedList:
    """`KubeService._iter_list`: `self._iter_list(fn, ...)` pages with the service's own page size (bigger in big-cluster mode);
    `KubeService._iter_list(fn, ...)` (no instance) uses the default."""

    def __get__(self, obj, owner=None):
        return functools.partial(KubeService._paged, getattr(obj, "page_size", PAGE_SIZE))


class KubeService:
    """Thin wrapper over the kubernetes client; returns plain row objects the UI can render."""

    big = False  # class defaults, so a service made without __init__ (tests) still works
    page_size = PAGE_SIZE

    def __init__(self, kubeconfig=None):
        self.read_only = False  # set by the workspace: every @mutating method refuses while this is true
        self.big = False  # big-cluster mode (see set_big): fewer, larger and parallel reads; the same results, a lighter Overview
        self.page_size = PAGE_SIZE
        self.audit = None  # set by the workspace: fn(action, target, result, detail) writes the local change log
        self.kubeconfig = kubeconfig or None  # path of a custom kubeconfig; None = KUBECONFIG / ~/.kube/config
        self._core = None
        self._apps = None
        self._custom = None
        self._api_client = None

    def set_big(self, on):
        """Big-cluster mode: bigger list pages, the Overview reads only Warning events (server-side filter) instead of every event,
        and Health check, Capacity and pod lists read their kinds side by side. Small clusters keep the plain behaviour."""
        self.big = bool(on)
        self.page_size = BIG_PAGE_SIZE if on else PAGE_SIZE

    def api_client(self):
        """The kubernetes ApiClient of the connected cluster (for plugins, which may use the official client)."""
        if self._api_client is None:
            raise RuntimeError("not connected to a cluster")
        return self._api_client

    def load_contexts(self):
        """Returns (context names, active context name or None)."""
        try:
            contexts, active = config.list_kube_config_contexts(config_file=self.kubeconfig)
        except config.ConfigException:
            return [], None
        return [c["name"] for c in contexts], (active or {}).get("name")

    def connect(self, context):
        """Connect to a context. A failure is put into plain words (`connect_help.explain`): a sign-in helper that needs a terminal,
        a helper that is not installed, or an unreachable server (naming the kubeconfig's proxy-url when there is one)."""
        connect_help.guard_helper_stdin()
        try:
            with timings.timed("connect", "connect"):
                self._connect(context)
        except Exception as error:  # noqa: BLE001 - re-raised, only the wording changes
            text = connect_help.explain(error, connect_help.auth_info(self.kubeconfig, context))
            if text == str(error):
                raise
            raise ConnectionError(text) from error

    def _connect(self, context):
        cfg = client.Configuration()
        config.load_kube_config(config_file=self.kubeconfig, context=context, client_configuration=cfg)
        cfg.retries = 1  # the default (3 retries with backoff) makes a dead cluster take ~9s to report
        api = client.ApiClient(cfg)
        # Pod manifests (especially managedFields) can make list responses huge.
        # urllib3 decodes gzip automatically, but does not request it by default.
        api.set_default_header("Accept-Encoding", "gzip")
        core = client.CoreV1Api(api)
        try:
            core.get_api_resources(_request_timeout=10)  # fail fast on bad creds/unreachable
        except MaxRetryError as e:
            raise ConnectionError(f"Cannot reach the API server at {cfg.host}") from e
        self._api_client = api
        self._core = core
        self._apps = client.AppsV1Api(api)
        self._apis = {"core": core, "apps": self._apps, "batch": client.BatchV1Api(api),
                      "net": client.NetworkingV1Api(api), "storage": client.StorageV1Api(api),
                      "ext": client.ApiextensionsV1Api(api), "rbac": client.RbacAuthorizationV1Api(api),
                      "autoscaling": client.AutoscalingV2Api(api), "policy": client.PolicyV1Api(api),
                      "scheduling": client.SchedulingV1Api(api), "admission": client.AdmissionregistrationV1Api(api),
                      "discovery": client.DiscoveryV1Api(api), "coordination": client.CoordinationV1Api(api)}
        self._custom = client.CustomObjectsApi(api)

    @mutating
    def create_namespace(self, body):
        return map_namespace(self._core.create_namespace(body))

    @mutating
    def create(self, kind, body):
        """Create a Deployment, DaemonSet, StatefulSet, Pod, ConfigMap or Secret from a manifest (like `kubectl create`);
        returns the stored row."""
        ns = (body.get("metadata") or {}).get("namespace") or "default"
        if kind == "Pod":
            return map_pod(self._core.create_namespaced_pod(ns, body))
        if kind == "Deployment":
            return map_deployment(self._apps.create_namespaced_deployment(ns, body))
        if kind == "StatefulSet":
            return map_statefulset(self._apps.create_namespaced_stateful_set(ns, body))
        if kind == "DaemonSet":
            return SPEC_BY_KIND["DaemonSet"].mapper(self._apps.create_namespaced_daemon_set(ns, body))
        if kind == "Service":
            return SPEC_BY_KIND["Service"].mapper(self._core.create_namespaced_service(ns, body))
        if kind in ("Ingress", "NetworkPolicy"):
            net = self._apis["net"]
            create = net.create_namespaced_ingress if kind == "Ingress" else net.create_namespaced_network_policy
            return SPEC_BY_KIND[kind].mapper(create(ns, body))
        if kind in ("Job", "CronJob"):
            batch = self._apis["batch"]
            create = batch.create_namespaced_job if kind == "Job" else batch.create_namespaced_cron_job
            return SPEC_BY_KIND[kind].mapper(create(ns, body))
        if kind == "ConfigMap":
            return SPEC_BY_KIND["ConfigMap"].mapper(self._core.create_namespaced_config_map(ns, body))
        if kind == "Secret":
            return SPEC_BY_KIND["Secret"].mapper(self._core.create_namespaced_secret(ns, body))
        raise ValueError(f"Creating {kind} is not supported")

    @mutating
    def patch_secret(self, namespace, name, patch):
        """Change a Secret with a merge patch that holds only what changed (a key set to None is removed). The values that
        were not touched are never sent back."""
        return SPEC_BY_KIND["Secret"].mapper(self._core.patch_namespaced_secret(name, namespace, patch))

    @mutating
    def replace(self, kind, body):
        """Replace a resource with an edited manifest (like `kubectl replace`); returns the stored row."""
        meta, api = body["metadata"], self._core
        if kind == "Namespace":
            return map_namespace(api.replace_namespace(meta["name"], body))
        if kind == "Pod":
            return map_pod(api.replace_namespaced_pod(meta["name"], meta["namespace"], body))
        if kind == "Node":
            return map_node(api.replace_node(meta["name"], body))
        apps = self._apps
        ns = meta.get("namespace")
        if kind == "ReplicaSet":
            return map_replicaset(apps.replace_namespaced_replica_set(meta["name"], ns, body))
        if kind == "Deployment":
            return map_deployment(apps.replace_namespaced_deployment(meta["name"], ns, body))
        if kind == "StatefulSet":
            return map_statefulset(apps.replace_namespaced_stateful_set(meta["name"], ns, body))
        spec = SPEC_BY_KIND.get(kind)
        if spec is None or not spec.editable:
            raise ValueError(f"Editing {kind} is not supported")
        api = self._apis[spec.api]
        if spec.namespaced:
            return spec.mapper(getattr(api, f"replace_namespaced_{spec.snake}")(meta["name"], ns, body))
        return spec.mapper(getattr(api, f"replace_{spec.snake}")(meta["name"], body))

    @mutating
    def scale(self, kind, name, namespace, replicas):
        """Set spec.replicas via the scale subresource."""
        body = {"spec": {"replicas": replicas}}
        apps = self._apps
        fn = {"Deployment": apps.patch_namespaced_deployment_scale,
              "ReplicaSet": apps.patch_namespaced_replica_set_scale,
              "StatefulSet": apps.patch_namespaced_stateful_set_scale}.get(kind)
        if fn is None:
            raise ValueError(f"Scaling {kind} is not supported")
        fn(name, namespace, body)

    FIELD_MANAGER = "klotse"

    def _dynamic_client(self):
        """A dynamic client for the connected cluster (it discovers the served kinds once, so it is built on first use and
        kept for as long as the connection is the same)."""
        from kubernetes import dynamic
        cached = getattr(self, "_dynamic", None)
        if cached is None or cached[0] is not self._api_client:
            self._dynamic = cached = (self._api_client, dynamic.DynamicClient(self._api_client))
        return cached[1]

    def apply_documents(self, docs, default_namespace="", dry_run=False):
        """Server-side apply of each manifest (what `kubectl apply --server-side` does) under the field manager `klotse`;
        conflicts with another manager are reported, never forced. A namespaced object without a namespace goes to
        `default_namespace`. dry_run=True asks the server to validate and report without storing anything.
        Returns [(kind, namespace, name, ok, message)], one per document in order: one failing document does not stop
        the others."""
        if not dry_run and getattr(self, "read_only", False):
            raise ReadOnlyError(READ_ONLY_MESSAGE)  # a dry run changes nothing, so a read-only cluster still allows it
        dyn = self._dynamic_client()
        results = []
        for doc in docs:
            meta = doc.get("metadata") or {}
            kind, name, ns = doc.get("kind", "?"), meta.get("name", "?"), meta.get("namespace") or ""
            try:
                resource = dyn.resources.get(api_version=doc["apiVersion"], kind=doc["kind"])
                ns = (ns or default_namespace) if resource.namespaced else ""
                options = {"dry_run": "All"} if dry_run else {}
                dyn.server_side_apply(resource, body=doc, name=name, namespace=ns or None, force_conflicts=False,
                                      field_manager=self.FIELD_MANAGER, **options)
                results.append((kind, ns, name, True, "valid (dry run, nothing stored)" if dry_run else "applied"))
                if not dry_run:
                    _audit(self, "apply", [f"{kind} {ns + '/' if ns else ''}{name}"], "ok")
            except Exception as e:  # report every document's own outcome; the server's message says what is wrong
                results.append((kind, ns, name, False, getattr(e, "summary", None) and e.summary() or f"{type(e).__name__}: {e}"))
                if not dry_run:
                    _audit(self, "apply", [f"{kind} {ns + '/' if ns else ''}{name}"], "failed", results[-1][4])
        return results

    def my_permissions(self, namespace):
        """What the signed-in user may do in `namespace` (SelfSubjectRulesReview). The answer can be incomplete: some
        authorizers cannot list their rules, so `incomplete` is passed on and the "Can I?" check is the definite one."""
        api = client.AuthorizationV1Api(self._api_client)
        review = client.V1SelfSubjectRulesReview(spec=client.V1SelfSubjectRulesReviewSpec(namespace=namespace))
        status = api.create_self_subject_rules_review(review, _request_timeout=30).status
        return {
            "resources": [{"verbs": r.verbs or [], "groups": r.api_groups or [], "resources": r.resources or [],
                           "names": r.resource_names or []} for r in status.resource_rules or []],
            # the client renamed non_resource_ur_ls to non_resource_urls (v30-ish): accept both spellings
            "urls": [{"verbs": r.verbs or [],
                      "urls": getattr(r, "non_resource_urls", None) or getattr(r, "non_resource_ur_ls", None) or []}
                     for r in status.non_resource_rules or []],
            "incomplete": bool(status.incomplete), "error": status.evaluation_error or ""}

    def can_i(self, verb, group, resource, namespace, name=""):
        """(allowed, reason) for one verb on one resource (`pods`, or `pods/log` for a subresource) in `namespace`
        (SelfSubjectAccessReview)."""
        resource, _, subresource = resource.partition("/")
        attributes = client.V1ResourceAttributes(namespace=namespace or None, verb=verb, group=group, resource=resource,
                                                 subresource=subresource or None, name=name or None)
        review = client.V1SelfSubjectAccessReview(spec=client.V1SelfSubjectAccessReviewSpec(resource_attributes=attributes))
        status = client.AuthorizationV1Api(self._api_client).create_self_subject_access_review(review, _request_timeout=30).status
        return bool(status.allowed), status.reason or status.evaluation_error or ""

    RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"

    @mutating
    def restart(self, kind, name, namespace):
        """Rollout restart, like `kubectl rollout restart`: stamp the pod template so the controller replaces the pods
        one by one with its normal update strategy."""
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"spec": {"template": {"metadata": {"annotations": {self.RESTART_ANNOTATION: stamp}}}}}
        method = {"Deployment": "patch_namespaced_deployment", "StatefulSet": "patch_namespaced_stateful_set",
                  "DaemonSet": "patch_namespaced_daemon_set"}.get(kind)
        if method is None:
            raise ValueError(f"Restarting {kind} is not supported")
        getattr(self._apps, method)(name, namespace, body)

    @staticmethod
    def manual_job_body(cronjob, stamp):
        """The Job `kubectl create job --from=cronjob/NAME` would make, as a manifest dict: the CronJob's job template, owned by
        the CronJob and marked as started by hand. The name is cut so it stays within Kubernetes' 63 characters."""
        meta, spec = cronjob.get("metadata") or {}, cronjob.get("spec") or {}
        template = spec.get("jobTemplate") or {}
        suffix = f"-manual-{stamp}"
        tmeta = template.get("metadata") or {}
        annotations = dict(tmeta.get("annotations") or {})
        annotations["cronjob.kubernetes.io/instantiate"] = "manual"
        body_meta = {"name": meta.get("name", "")[:63 - len(suffix)] + suffix, "namespace": meta.get("namespace"),
                     "labels": dict(tmeta.get("labels") or {}), "annotations": annotations}
        if meta.get("uid"):
            body_meta["ownerReferences"] = [{"apiVersion": "batch/v1", "kind": "CronJob", "name": meta.get("name"),
                                             "uid": meta["uid"], "controller": True, "blockOwnerDeletion": True}]
        return {"apiVersion": "batch/v1", "kind": "Job", "metadata": body_meta, "spec": template.get("spec") or {}}

    @mutating
    def trigger_cronjob(self, name, namespace):
        """Run a CronJob once now. Returns the new Job's name."""
        batch = self._apis["batch"]
        cronjob = _raw(batch.read_namespaced_cron_job(name, namespace))
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%y%m%d%H%M%S")
        body = self.manual_job_body(cronjob, stamp)
        batch.create_namespaced_job(namespace, body)
        return body["metadata"]["name"]

    @mutating
    def set_cronjob_suspended(self, name, namespace, suspend):
        self._apis["batch"].patch_namespaced_cron_job(name, namespace, {"spec": {"suspend": bool(suspend)}})

    def deployment_history(self, namespace, name):
        """The revisions of a Deployment, newest first (see k8s.rollout.history): its ReplicaSets, found through the
        Deployment's selector and kept only if it owns them."""
        deployment = _raw(self._apps.read_namespaced_deployment(name, namespace))
        selector = rollout.match_selector(((deployment.get("spec") or {}).get("selector") or {}).get("matchLabels"))
        sets = [_raw(rs) for rs in self._iter_list(self._apps.list_namespaced_replica_set, namespace, label_selector=selector)]
        return rollout.history(deployment, sets)

    @mutating
    def rollback_deployment(self, namespace, name, replica_set_name):
        """`kubectl rollout undo --to-revision`: put the pod template of one of the Deployment's own ReplicaSets back on the
        Deployment (a JSON patch replacing /spec/template, as kubectl does), which starts a normal rollout and becomes a
        new revision. A ReplicaSet the Deployment does not own is refused."""
        deployment = _raw(self._apps.read_namespaced_deployment(name, namespace))
        replica_set = _raw(self._apps.read_namespaced_replica_set(replica_set_name, namespace))
        if not rollout.owned_by(replica_set, deployment):
            raise ValueError(f"ReplicaSet {replica_set_name} does not belong to Deployment {name}.")
        template = rollout.rollback_template(replica_set)
        self._apps.patch_namespaced_deployment(name, namespace, [{"op": "replace", "path": "/spec/template", "value": template}])

    def pods_by_selector(self, namespace, selector):
        """[(pod name, [container names], phase)] of the pods in `namespace` that match a label selector, for the multi-pod log
        view. Regular containers only. An empty selector is refused: it would match every pod."""
        if not str(selector).strip():
            raise ValueError("A label selector is needed.")
        found = []
        for pod in self._iter_list(self._core.list_namespaced_pod, namespace, label_selector=selector):
            names = [c.name for c in (pod.spec.containers or [])]
            found.append((pod.metadata.name, names, (pod.status.phase if pod.status else "") or ""))
        return sorted(found)

    def open_log_stream(self, namespace, pod, container=None, tail=500, timestamps=False, previous=False, since_seconds=None,
                        follow=True):
        """Returns a streaming urllib3 response; the caller reads and closes it. Following is off for the previous
        instance (that container has ended, there is nothing more to follow)."""
        extra = {"since_seconds": int(since_seconds)} if since_seconds else {}
        return self._core.read_namespaced_pod_log(pod, namespace, container=container, follow=follow and not previous,
                                                  tail_lines=tail, timestamps=timestamps, previous=previous,
                                                  _preload_content=False, **extra)

    @mutating
    def delete_pod(self, name, namespace, force=False):
        options = {"body": client.V1DeleteOptions(grace_period_seconds=0, propagation_policy="Background")} if force else {}
        self._core.delete_namespaced_pod(name, namespace, _request_timeout=15, **options)

    # ---- API discovery (for the role editor's pickers) --------------------
    def discovery_groups(self):
        """[(group, preferred version)] for the core group and every API group the cluster serves (one call)."""
        groups = client.ApisApi(self._api_client).get_api_versions(_request_timeout=30).groups or []
        return [("", "v1")] + [(g.name, g.preferred_version.version) for g in groups if g.preferred_version]

    def discovery_resources(self, group, version):
        """ApiResource entries of one group/version, subresources (`pods/log`) included."""
        if group:
            found = self._custom.get_api_resources(group, version, _request_timeout=30)
        else:
            found = self._core.get_api_resources(_request_timeout=30)
        return [ApiResource(group, r.name, r.kind or "", bool(r.namespaced), version) for r in found.resources or []]

    @staticmethod
    def is_karpenter_group(group):
        """karpenter.sh and the cloud providers' groups (karpenter.k8s.aws, karpenter.azure.com, ...)."""
        return "karpenter" in (group or "").split(".")

    def karpenter_resources(self):
        """The Karpenter kinds this cluster serves, [ApiResource] (no subresources), NodePool and NodeClaim first. Empty
        when Karpenter is not installed. Two cheap discovery calls per Karpenter group, nothing else."""
        found = []
        for group, version in self.discovery_groups():
            if self.is_karpenter_group(group):
                found += [r for r in self.discovery_resources(group, version) if "/" not in r.name and r.kind]
        first = {"NodePool": 0, "NodeClaim": 1}
        return sorted(found, key=lambda r: (first.get(r.kind, 2), r.kind))

    def gateway_resources(self):
        """The Gateway API kinds this cluster serves, [ApiResource] (no subresources), GatewayClass, Gateway, HTTPRoute and GRPCRoute
        first. Empty when the Gateway API is not installed. One discovery call per version of the group, nothing else."""
        from .gateway import GROUP, order
        found = []
        for group, version in self.discovery_groups():
            if group == GROUP:
                found += [r for r in self.discovery_resources(group, version) if "/" not in r.name and r.kind]
        return order(found)

    def list_service_account_names(self):
        """[(namespace, name)] of every service account, for the binding editor's picker (names only, paged)."""
        return [(a.metadata.namespace or "", a.metadata.name)
                for a in self._iter_list(self._core.list_service_account_for_all_namespaces)]

    def list_secret_names(self, namespace):
        """Names of the secrets in a namespace, for the service account editor's pickers. Only metadata is requested
        from the API server (PartialObjectMetadataList), so no secret data is transferred. A client that cannot send
        the header makes this fail rather than download the secrets."""
        return self._metadata_names(self._core.list_namespaced_secret, namespace)

    def list_configmap_names(self, namespace):
        """Names of the config maps in a namespace (metadata only, like the secret names)."""
        return self._metadata_names(self._core.list_namespaced_config_map, namespace)

    @staticmethod
    def _metadata_names(list_fn, namespace):
        accept = "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1,application/json"
        names, token = [], None
        while True:
            reply = list_fn(namespace, limit=500, _continue=token, _request_timeout=30,
                            _preload_content=False, _headers={"Accept": accept})
            page = json.loads(reply.data)
            names += [item["metadata"]["name"] for item in page.get("items") or []]
            token = (page.get("metadata") or {}).get("continue")
            if not token:
                return names

    def crd_group_names(self):
        """{API group: number of CRDs} for the navigator's Custom Resources > Groups sub items. Only metadata is requested
        (PartialObjectMetadataList): a CRD object carries its whole OpenAPI schema and can be very large, but its name is
        `<plural>.<group>`, which is all this needs."""
        api = self._apis["ext"]
        names = self._metadata_names(lambda _ns, **kw: api.list_custom_resource_definition(**kw), None)
        groups = {}
        for name in names:
            group = name.partition(".")[2]
            if group:
                groups[group] = groups.get(group, 0) + 1
        return groups

    def list_helm_releases(self, namespace=None):
        """The Helm releases of a namespace (or all), one row each with its newest revision, from the labels of their storage
        Secrets only: the Secrets are requested as PartialObjectMetadataList, so no release data (values, manifests) is
        transferred. Needs permission to list secrets."""
        accept = "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1,application/json"
        fn, args = ((self._core.list_namespaced_secret, (namespace,)) if namespace else (self._core.list_secret_for_all_namespaces, ()))
        items, token = [], None
        while True:
            reply = fn(*args, label_selector=helm.LABEL_SELECTOR, limit=500, _continue=token, _request_timeout=30,
                       _preload_content=False, _headers={"Accept": accept})
            page = json.loads(reply.data)
            for item in page.get("items") or []:
                meta = item.get("metadata") or {}
                items.append({"name": meta.get("name"), "namespace": meta.get("namespace", ""), "labels": meta.get("labels") or {},
                              "creationTimestamp": meta.get("creationTimestamp", "")})
            token = (page.get("metadata") or {}).get("continue")
            if not token:
                return [map_helm_release(row) for row in helm.release_rows(items)]

    def search_objects(self, text, namespace=None, cancel=None, limit=search.HIT_LIMIT):
        """Find objects by name, label, kind or namespace across the common kinds (see `k8s/search.py` for the query words).
        Only metadata is requested (PartialObjectMetadataList), so no Secret data and no pod spec is transferred, a label term
        is also sent as a label selector, and nothing is kept. Bounded: at most `limit` hits, `search.MAX_PAGES` pages of
        `search.PAGE_LIMIT` objects per kind. Returns ([Hit], [note]); a kind that cannot be listed (no permission) is
        left out with a note, never an error. `cancel` is an object with is_set(), checked between pages."""
        query = search.parse_query(text)
        notes = [f"'{w}' is not a kind that is searched." for w in query.unknown_kinds]
        if query.empty:
            return [], notes
        if namespace:
            query.namespaces = [namespace] if not query.namespaces else query.namespaces
        accept = "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1,application/json"
        hits = []
        for kind, api, infix, namespaced in search.kinds_for(query):
            if cancel is not None and cancel.is_set():
                break
            client_api = self._apis[api]
            if not namespaced:
                calls = [(getattr(client_api, f"list_{infix}"), ())]
            elif len(query.namespaces) == 1:
                calls = [(getattr(client_api, f"list_namespaced_{infix}"), (query.namespaces[0],))]
            else:  # all namespaces; several `ns:` terms are filtered here
                calls = [(getattr(client_api, f"list_{infix}_for_all_namespaces"), ())]
            extra = {"label_selector": query.label_selector} if query.labels else {}
            for fn, args in calls:
                token, pages = None, 0
                try:
                    while True:
                        reply = fn(*args, limit=search.PAGE_LIMIT, _continue=token, _request_timeout=30, _preload_content=False,
                                   _headers={"Accept": accept}, **extra)
                        page = json.loads(reply.data)
                        for item in page.get("items") or []:
                            meta = item.get("metadata") or {}
                            if search.matches(meta, query):
                                hits.append(search.Hit(kind, meta.get("namespace", ""), meta.get("name", ""),
                                                       meta.get("creationTimestamp", ""), meta.get("labels") or {}))
                                if len(hits) >= limit:
                                    notes.append(f"Showing the first {limit} matches: narrow the search to see others.")
                                    return hits, notes
                        token = (page.get("metadata") or {}).get("continue")
                        pages += 1
                        if not token:
                            break
                        if pages >= search.MAX_PAGES or (cancel is not None and cancel.is_set()):
                            if pages >= search.MAX_PAGES:
                                notes.append(f"{kind}: only the first {pages * search.PAGE_LIMIT} objects were searched.")
                            break
                except ApiException as e:
                    notes.append(f"{kind}: not searched ({'no permission' if e.status == 403 else f'error {e.status}'}).")
                except (ValueError, OSError) as e:
                    notes.append(f"{kind}: not searched ({type(e).__name__}).")
        return hits, notes

    def helm_release(self, namespace, name):
        """One release in detail: {summary of the newest revision, values_user, values_all, history (newest first), skipped}.
        Reads the storage Secrets of this release only (its few revisions) and decodes them here; nothing is logged and the
        values are returned for the dialog to hide until the user reveals them."""
        if not helm.VALID_NAME.fullmatch(name or ""):
            raise helm.HelmError(f"'{name}' is not a release name.")
        found, skipped = [], 0
        for secret in self._iter_list(self._core.list_namespaced_secret, namespace, label_selector=f"{helm.LABEL_SELECTOR},name={name}"):
            try:
                found.append(helm.decode_release((secret.data or {}).get("release", "")))
            except helm.HelmError:
                skipped += 1
        if not found:
            raise helm.HelmError(f"No readable revision of release {name} in {namespace}.")
        found.sort(key=lambda r: r.get("version", 0), reverse=True)
        user, everything = helm.values_of(found[0])
        return {"summary": helm.summarize(found[0]), "values_user": user, "values_all": everything,
                "history": [helm.summarize(r) for r in found], "skipped": skipped}

    # (api, method infix, namespaced) of the kinds the Related view looks at; the rest comes from the registry
    _RELATED_BUILT_IN = {"Pod": ("core", "pod", True), "Node": ("core", "node", False), "Deployment": ("apps", "deployment", True),
                         "ReplicaSet": ("apps", "replica_set", True), "StatefulSet": ("apps", "stateful_set", True),
                         "DaemonSet": ("apps", "daemon_set", True)}

    def _related_api(self, kind):
        if kind in self._RELATED_BUILT_IN:
            api, snake, namespaced = self._RELATED_BUILT_IN[kind]
            return self._apis[api], snake, namespaced
        spec = SPEC_BY_KIND.get(kind)
        return (self._apis[spec.api], spec.snake, spec.namespaced) if spec is not None else None

    def related_objects(self, kind, raw):
        """The objects related to one object (owners, what it owns, uses or is used by; see k8s.related), found by reading the
        manifests of the objects around it. Secrets are never fetched: only which pods name them is looked at, so no secret data
        is read. A kind or call that cannot be read (no permission, gone) just leaves its relations out."""
        service = self

        class Fetch:
            def get(self, kind, namespace, name):
                found = service._related_api(kind) if kind != "Secret" else None
                if found is None or not name:
                    return None
                api, snake, namespaced = found
                try:
                    reader = getattr(api, f"read_namespaced_{snake}") if namespaced else getattr(api, f"read_{snake}")
                    return _raw(reader(name, namespace, _request_timeout=15) if namespaced else reader(name, _request_timeout=15))
                except ApiException:
                    return None

            def list(self, kind, namespace, label_selector=None, field_selector=None):
                found = service._related_api(kind) if kind != "Secret" else None
                if found is None:
                    return []
                api, snake, namespaced = found
                options = {k: v for k, v in (("label_selector", label_selector), ("field_selector", field_selector)) if v}
                try:
                    if not namespaced:
                        fn, args = getattr(api, f"list_{snake}"), ()
                    elif namespace:
                        fn, args = getattr(api, f"list_namespaced_{snake}"), (namespace,)
                    else:
                        fn, args = getattr(api, f"list_{snake}_for_all_namespaces"), ()
                    return [_raw(i) for i in itertools.islice(service._iter_list(fn, *args, **options), related.SCAN_LIMIT + 1)]
                except ApiException:
                    return []

        return related.related(kind, raw, Fetch())

    def list_service_names(self, namespace):
        """Names of the services of a namespace, for the Ingress editor's backend boxes."""
        return [s.metadata.name for s in self._iter_list(self._core.list_namespaced_service, namespace)]

    def list_pod_labels(self, namespace):
        """[(key, value)] of the labels on the pods of a namespace, for the selector boxes (metadata only, paged)."""
        accept = "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1,application/json"
        pairs, token = set(), None
        while True:
            reply = self._core.list_namespaced_pod(namespace, limit=500, _continue=token, _request_timeout=30,
                                                   _preload_content=False, _headers={"Accept": accept})
            page = json.loads(reply.data)
            for item in page.get("items") or []:
                pairs.update(((item.get("metadata") or {}).get("labels") or {}).items())
            token = (page.get("metadata") or {}).get("continue")
            if not token:
                return sorted(pairs)

    def list_ingress_class_names(self):
        """[(name, is_default)] of the cluster's IngressClasses, for the Ingress editor's class box."""
        flag = "ingressclass.kubernetes.io/is-default-class"
        found = self._apis["net"].list_ingress_class(_request_timeout=30)
        return [(c.metadata.name, ((c.metadata.annotations or {}).get(flag) == "true"))
                for c in found.items or []]

    @mutating
    def delete_resource(self, kind, name, namespace, uid):
        """Delete a deletable registry kind (the RBAC ones). The UID precondition keeps a replacement object that
        reused the name from being deleted."""
        spec = SPEC_BY_KIND.get(kind)
        if spec is None or not spec.deletable:
            raise ValueError(f"Deleting {kind} is not supported")
        if not uid:
            raise ValueError(f"{kind} UID is missing; refresh the list first")
        api = self._apis[spec.api]
        body = client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=uid))
        if spec.namespaced:
            getattr(api, f"delete_namespaced_{spec.snake}")(name, namespace, body=body, _request_timeout=15)
        else:
            getattr(api, f"delete_{spec.snake}")(name, body=body, _request_timeout=15)

    @mutating
    def create_custom(self, group, version, plural, namespaced, body):
        """Create an instance of a custom resource from a manifest (the Gateway API kinds are edited this way)."""
        namespace = (body.get("metadata") or {}).get("namespace") or "default"
        if namespaced:
            return self._custom.create_namespaced_custom_object(group, version, namespace, plural, body)
        return self._custom.create_cluster_custom_object(group, version, plural, body)

    @mutating
    def replace_custom(self, group, version, plural, namespaced, namespace, name, body):
        """Replace an instance with the edited manifest. The manifest carries the resourceVersion it was read with, so an
        object changed by someone else meanwhile is refused (409) instead of overwritten."""
        if namespaced:
            return self._custom.replace_namespaced_custom_object(group, version, namespace, plural, name, body)
        return self._custom.replace_cluster_custom_object(group, version, plural, name, body)

    @mutating
    def delete_custom(self, group, version, plural, namespaced, namespace, name, uid):
        """Delete an instance of a custom resource; the UID precondition keeps a replacement with the same name from being deleted."""
        if not uid:
            raise ValueError("The UID is missing; refresh the list first")
        body = client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=uid))
        if namespaced:
            return self._custom.delete_namespaced_custom_object(group, version, namespace, plural, name, body=body, _request_timeout=15)
        return self._custom.delete_cluster_custom_object(group, version, plural, name, body=body, _request_timeout=15)

    @mutating
    def cordon_node(self, name, unschedulable):
        self._core.patch_node(name, {"spec": {"unschedulable": unschedulable}}, _request_timeout=15)

    @mutating
    def delete_node(self, name, uid, force=False):
        # Do not delete a replacement node that reused the selected node's name.
        self._core.delete_node(name, body=client.V1DeleteOptions(
            preconditions=client.V1Preconditions(uid=uid),
            grace_period_seconds=0 if force else None,
            propagation_policy="Background" if force else None), _request_timeout=15)

    _iter_list = _PagedList()

    @staticmethod
    def _paged(size, fn, *args, **kwargs):
        """Bound each API response and release each page before fetching the next."""
        token = None
        pages = items = 0
        spent = 0.0
        while True:
            began = time.perf_counter()
            page = fn(*args, limit=size, _continue=token, _request_timeout=30, **kwargs)
            spent += time.perf_counter() - began
            pages += 1
            items += len(page.items or [])
            yield from page.items
            token = page.metadata._continue if page.metadata else None
            del page
            if not token:
                break
        timings.record("api-list", getattr(fn, "__name__", "list"), spent, pages=pages, items=items)

    # ---- change watches: the UI reloads the visible list when one of these fires -------
    def watch_targets(self, key, namespace=None):
        """List calls to watch for a view: [(list_fn, args)]. Empty for views that are only polled."""
        core, apps = self._core, self._apps
        scoped = lambda api, snake: ((getattr(api, f"list_namespaced_{snake}"), (namespace,)) if namespace
                                     else (getattr(api, f"list_{snake}_for_all_namespaces"), ()))
        if key == "overview":
            return [(core.list_node, ()), scoped(core, "pod")]
        if key == "workloads":
            return [scoped(core, "pod")]
        if key == "nodes":
            return [(core.list_node, ())]
        if key == "namespaces":
            return [(core.list_namespace, ())]
        if key == "pods":
            return [scoped(core, "pod")]
        simple = {"deployments": "deployment", "replicasets": "replica_set", "statefulsets": "stateful_set"}
        if key in simple:
            return [scoped(apps, simple[key])]
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            return []
        api = self._apis[spec.api]
        if not spec.namespaced:
            return [(getattr(api, f"list_{spec.snake}"), ())]
        return [scoped(api, spec.snake)]

    @staticmethod
    def watch_changes(fn, args, stop, timeout=20):
        """Yield one item per change event of fn(*args) until `stop` is set. Starts from the current list
        version, resumes after each server-side timeout, and restarts from a fresh version after a 410 Gone."""
        version = None
        while not stop.is_set():
            try:
                if version is None:
                    version = fn(*args, limit=1, _request_timeout=15).metadata.resource_version
                stream = watch.Watch()
                for event in stream.stream(fn, *args, resource_version=version, timeout_seconds=timeout,
                                           _request_timeout=timeout + 10):
                    if stop.is_set():
                        stream.stop()
                        return
                    obj = event.get("object")
                    if event.get("type") == "ERROR" or isinstance(obj, dict):
                        version = None
                        break
                    version = obj.metadata.resource_version or version
                    if event["type"] != "BOOKMARK":
                        yield event["type"]
            except ApiException:
                version = None
                if stop.wait(2):
                    return
            except Exception:
                version = None
                if stop.wait(5):  # network trouble: back off, then start over
                    return

    # ---- metrics-server (optional): every call degrades to "no data" ------
    def _metrics(self, plural, namespace=None):
        """Items of metrics.k8s.io/v1beta1 <plural>, or [] when metrics-server is missing or not permitted."""
        try:
            if namespace:
                page = self._custom.list_namespaced_custom_object(
                    "metrics.k8s.io", "v1beta1", namespace, plural, _request_timeout=10)
            else:
                page = self._custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", plural, _request_timeout=10)
            return page.get("items") or []
        except Exception:
            return []

    def node_usage(self):
        """{node name: (cores, GiB)} currently in use."""
        return {i["metadata"]["name"]: (float(parse_quantity(i["usage"].get("cpu", 0))),
                                        float(parse_quantity(i["usage"].get("memory", 0))) / 2**30)
                for i in self._metrics("nodes")}

    def pod_usage(self, namespace=None):
        """{(namespace, pod): (cores, MiB)} summed over containers."""
        usage = {}
        for i in self._metrics("pods", namespace):
            containers = i.get("containers") or []
            usage[(i["metadata"].get("namespace", ""), i["metadata"]["name"])] = (
                sum(float(parse_quantity(c["usage"].get("cpu", 0))) for c in containers),
                sum(float(parse_quantity(c["usage"].get("memory", 0))) for c in containers) / 2**20)
        return usage

    def list_namespaces(self):
        return [map_namespace(n) for n in self._iter_list(self._core.list_namespace)]

    def list_pods(self, namespace=None):
        api = self._core
        items = (self._iter_list(api.list_namespaced_pod, namespace) if namespace
                 else self._iter_list(api.list_pod_for_all_namespaces))
        if self.big:  # the metrics call does not depend on the pod list: ask for it while the pages come in
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="usage") as pool:
                usage_f = pool.submit(self.pod_usage, namespace)
                rows = [map_pod(p) for p in items]
                return self._with_usage(rows, namespace, usage_f.result())
        return self._with_usage([map_pod(p) for p in items], namespace)

    def _with_usage(self, rows, namespace=None, usage=None):
        """Fill cpu_used/mem_used from metrics-server where it has data; rows it does not know stay -1."""
        usage = self.pod_usage(namespace) if usage is None else usage
        if usage:
            for r in rows:
                if (r.namespace, r.name) in usage:
                    r.cpu_used, r.mem_used = usage[(r.namespace, r.name)]
        return rows

    def list_nodes(self):
        rows = [map_node(n) for n in self._iter_list(self._core.list_node)]
        usage = self.node_usage()
        for r in rows:
            if r.name in usage:
                r.cpu_used, r.mem_used = usage[r.name]
        return rows

    def all_node_pod_counts(self):
        """{node: unfinished pods} for every node that has any, from one pass over all pods (nothing is serialized)."""
        counts = {}
        for pod in self._iter_list(self._core.list_pod_for_all_namespaces,
                                   field_selector="status.phase!=Succeeded,status.phase!=Failed"):
            node = pod.spec.node_name
            if node:
                counts[node] = counts.get(node, 0) + 1
        return counts

    def node_pod_counts(self, names):
        """{node: number of pods that are not finished} for just these nodes (one small query per node)."""
        counts = {}
        for name in names:
            selector = f"spec.nodeName={name},status.phase!=Succeeded,status.phase!=Failed"
            try:
                counts[name] = sum(1 for _ in self._iter_list(self._core.list_pod_for_all_namespaces,
                                                              field_selector=selector))
            except Exception:
                continue  # leave that node uncounted rather than failing the others
        return counts

    def read_pod(self, name, namespace):
        """One pod, freshly read (for a pod's own tab)."""
        row = map_pod(self._core.read_namespaced_pod(name, namespace, _request_timeout=15))
        try:  # just this pod's metrics; any failure leaves usage unknown
            item = self._custom.get_namespaced_custom_object(
                "metrics.k8s.io", "v1beta1", namespace, "pods", name, _request_timeout=10)
            containers = item.get("containers") or []
            row.cpu_used = sum(float(parse_quantity(c["usage"].get("cpu", 0))) for c in containers)
            row.mem_used = sum(float(parse_quantity(c["usage"].get("memory", 0))) for c in containers) / 2**20
        except Exception:
            pass
        return row

    def list_node_pods(self, node):
        rows = [map_pod(p) for p in self._iter_list(self._core.list_pod_for_all_namespaces,
                                                    field_selector=f"spec.nodeName={node}")]
        return self._with_usage(rows) if rows else rows  # metrics have no node filter; skip the call for empty nodes

    def read_pod_log(self, namespace, pod, container, tail=500, previous=False):
        """The last `tail` lines of one container's log as text (not streamed), or of its previous run. Looking only."""
        return self._core.read_namespaced_pod_log(pod, namespace, container=container, tail_lines=tail, previous=previous,
                                                  _request_timeout=60) or ""

    def rbac_data(self):
        """{roles, clusterroles, rolebindings, clusterrolebindings: [manifests], missing: [kinds that could not be read]} for the
        RBAC lookup, across all namespaces. Looking only."""
        keys = {"roles": "roles", "clusterroles": "clusterroles", "rolebindings": "rolebindings",
                "clusterrolebindings": "clusterrolebindings"}
        data, missing = {}, []
        for name, key in keys.items():
            try:
                data[name] = [row.raw for row in self.list_resource(key)]
            except Exception as error:  # noqa: BLE001 - one forbidden kind must not hide the others
                data[name] = []
                missing.append(f"{name} ({type(error).__name__})")
        data["missing"] = missing
        return data

    def health_data(self, namespace=None):
        """({kind key: [manifests]}, [what could not be read]) for `k8s.health.check`: pods, nodes, Deployments, StatefulSets,
        DaemonSets, claims and Services of one namespace (or all). Looking only; a kind the user may not list is left out and
        named, never fatal."""
        readers = {"pods": lambda: self.list_pods(namespace), "nodes": self.list_nodes,
                   "deployments": lambda: self.list_deployments(namespace), "statefulsets": lambda: self.list_statefulsets(namespace),
                   "daemonsets": lambda: self.list_resource("daemonsets", namespace),
                   "pvcs": lambda: self.list_resource("persistentvolumeclaims", namespace),
                   "services": lambda: self.list_resource("services", namespace)}
        data, problems = {}, []

        def run(read):
            try:
                return [row.raw for row in read()], None
            except Exception as error:  # noqa: BLE001 - one forbidden kind must not hide the others
                return None, type(error).__name__

        if self.big:  # seven reads that only wait for the API server: side by side (19.7 s one after another on 103 nodes / 2,700 pods)
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="health") as pool:
                answers = {key: pool.submit(run, read) for key, read in readers.items()}
                results = {key: future.result() for key, future in answers.items()}
        else:
            results = {key: run(read) for key, read in readers.items()}
        for key, (rows, error) in results.items():
            if error is None:
                data[key] = rows
            else:
                problems.append(f"{key} ({error})")
        return data, problems

    def capacity_data(self):
        """(nodes, pods) for the Capacity window; in big-cluster mode the two lists are read side by side."""
        if not self.big:
            return self.list_nodes(), self.list_pods(None)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="capacity") as pool:
            nodes_f, pods_f = pool.submit(self.list_nodes), pool.submit(self.list_pods, None)
            return nodes_f.result(), pods_f.result()

    def drain_preview_data(self, node):
        """(pod manifests on the node, PodDisruptionBudget manifests of the cluster) for `k8s.drain_plan.plan`. Looking only."""
        pods = [r.raw for r in self.list_node_pods(node)]
        try:
            budgets = [r.raw for r in self.list_resource("poddisruptionbudgets")]
        except Exception:  # noqa: BLE001 - no permission to read budgets: the preview then just cannot name them
            budgets = []
        return pods, budgets

    def _list_apps(self, all_fn, ns_fn, mapper, namespace):
        items = self._iter_list(ns_fn, namespace) if namespace else self._iter_list(all_fn)
        return [mapper(i) for i in items]

    def list_replicasets(self, namespace=None):
        a = self._apps
        return self._list_apps(a.list_replica_set_for_all_namespaces, a.list_namespaced_replica_set, map_replicaset, namespace)

    def list_deployments(self, namespace=None):
        a = self._apps
        return self._list_apps(a.list_deployment_for_all_namespaces, a.list_namespaced_deployment, map_deployment, namespace)

    def list_statefulsets(self, namespace=None):
        a = self._apps
        return self._list_apps(a.list_stateful_set_for_all_namespaces, a.list_namespaced_stateful_set, map_statefulset, namespace)

    def list_resource(self, key, namespace=None):
        """Generic list for the specs in resources.py; events come back newest first."""
        spec = SPEC_BY_KEY[key]
        rows = [spec.mapper(i) for i in self._resource_items(key, namespace)]
        if key == "events":
            rows.sort(key=lambda r: r.ts, reverse=True)
        return rows

    EVENTS_SHOWN = 200

    def list_object_events(self, namespace, uid):
        """The events about one object (matched by its UID, so a recreated object does not show its predecessor's),
        newest first, at most EVENTS_SHOWN. `namespace` is empty for cluster-scoped objects (Node events live in
        `default`, so those are searched in every namespace)."""
        if not uid:
            raise ValueError("The object has no UID; refresh the list first.")
        core = self._core
        fn, args = ((core.list_namespaced_event, (namespace,)) if namespace else (core.list_event_for_all_namespaces, ()))
        rows = [SPEC_BY_KEY["events"].mapper(e) for e in self._iter_list(fn, *args, field_selector=f"involvedObject.uid={uid}")]
        rows.sort(key=lambda r: r.ts, reverse=True)
        return rows[:self.EVENTS_SHOWN]

    def _resource_items(self, key, namespace=None, **options):
        spec = SPEC_BY_KEY[key]
        api = self._apis[spec.api]
        if not spec.namespaced:
            return self._iter_list(getattr(api, f"list_{spec.snake}"), **options)
        if namespace:
            return self._iter_list(getattr(api, f"list_namespaced_{spec.snake}"), namespace, **options)
        return self._iter_list(getattr(api, f"list_{spec.snake}_for_all_namespaces"), **options)

    def list_custom_objects(self, group, version, plural, namespaced, namespace=None, kind=None):
        """Instances of a custom resource: one namespace, or every namespace for a namespaced kind. `kind` lets a
        well-known resource (Karpenter's NodePool, NodeClaim) fill the extra columns it has."""
        api = self._custom
        if namespaced and namespace:
            fn, args = api.list_namespaced_custom_object, (group, version, namespace, plural)
        else:  # cluster-scoped kinds, or a namespaced kind across all namespaces
            fn, args = api.list_cluster_custom_object, (group, version, plural)
        return [map_custom_object(i, group=group, kind=kind) for i in self._iter_dicts(fn, *args)]

    @staticmethod
    def _iter_dicts(fn, *args):
        """Like _iter_list, but custom objects come back as plain dicts."""
        token = None
        while True:
            page = fn(*args, limit=500, _continue=token, _request_timeout=30)
            yield from page.get("items") or []
            token = (page.get("metadata") or {}).get("continue")
            if not token:
                break

    def _overview_events(self, namespace=None):
        """One streaming pass: (newest 25 events, warning count, newest warnings for the drill-down)."""
        mapper = SPEC_BY_KEY["events"].mapper
        recent, warned = [], []
        count = 0
        # big-cluster mode asks the server for Warning events only (a cluster with ~8,000 events took ~5 s to list); the recent
        # list then shows the newest warnings, not every type
        selector = {"field_selector": "type=Warning"} if self.big else {}
        for seq, event in enumerate(self._resource_items("events", namespace, **selector)):
            ts = event.last_timestamp or event.event_time or event.metadata.creation_timestamp or MIN_TS
            keep_newest(recent, 25, ts, seq, event)
            if event.type == "Warning":
                count += 1
                keep_newest(warned, OVERVIEW_LIST_CAP, ts, seq, event)

        def rows(heap, **kw):
            return [mapper(e, **kw) for _, _, e in sorted(heap, key=lambda x: (x[0], x[1]), reverse=True)]

        # the 25 recent rows are shown at once; the (up to 500) warnings only serialize when a row is selected
        return rows(recent), count, rows(warned, lazy=True)

    def _overview_pods_by_phase(self, namespace=None):
        """Overview pod numbers for a huge cluster without decoding every pod, or None when this does not apply. The API server
        gives the exact total for an unfiltered `limit=1` list (`remainingItemCount`), so only the pods that are Pending, Failed or
        Succeeded (few) are listed, with a field selector, and the rest is "running". This counts by pod **phase**: a pod that
        is crash-looping or terminating is still phase Running here (the Health check finds those). `counts["by_phase"]` is set so the
        UI can say so. Returns None (use the full pass) when the cluster is smaller than HUGE_PODS or the server gives no count."""
        api = self._core
        fn, args = (api.list_namespaced_pod, (namespace,)) if namespace else (api.list_pod_for_all_namespaces, ())
        first = fn(*args, limit=1, _request_timeout=30)
        remaining = getattr(first.metadata, "remaining_item_count", None) if first.metadata else None
        if remaining is None:
            return None
        total = len(first.items or []) + remaining
        if total < HUGE_PODS:
            return None
        counts = {"pods": total, "running": 0, "pending": 0, "failed": 0, "by_phase": True}
        lists = {}
        for bucket, phase in (("pending", "Pending"), ("failed", "Failed")):
            kept, number = [], 0
            for pod in self._iter_list(fn, *args, field_selector=f"status.phase={phase}"):
                number += 1
                if len(kept) < OVERVIEW_LIST_CAP:
                    kept.append(map_pod(pod))
            counts[bucket], lists[bucket] = number, kept
        finished = sum(1 for _ in self._iter_list(fn, *args, field_selector="status.phase=Succeeded"))
        counts["running"] = max(0, total - counts["pending"] - counts["failed"] - finished)
        return counts, lists

    def _overview_pods(self, namespace=None):
        """One streaming pass: (counts, {"pending": [...], "failed": [...]}); only problem pods keep their raw object."""
        if self.big:
            counted = self._overview_pods_by_phase(namespace)
            if counted is not None:
                return counted
        api = self._core
        items = (self._iter_list(api.list_namespaced_pod, namespace) if namespace
                 else self._iter_list(api.list_pod_for_all_namespaces))
        counts = {"pods": 0, "running": 0, "pending": 0, "failed": 0}
        problems = {"pending": [], "failed": []}
        for pod in items:
            counts["pods"] += 1
            bucket = pod_bucket(map_pod(pod, include_raw=False).status)
            if bucket in counts:
                counts[bucket] += 1
            if bucket in problems and len(problems[bucket]) < OVERVIEW_LIST_CAP:
                problems[bucket].append(pod)
        return counts, {b: [map_pod(p) for p in pods] for b, pods in problems.items()}

    def overview_cluster(self):
        # The five reads are independent and each is mostly waiting for the API server, so they run side by side: on a large
        # cluster (about 100 nodes, 2,700 pods) the one-after-another version took three times as long as `kubectl get pods -A`.
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="overview") as pool:
            def phase(label, fn):
                def run():
                    with timings.timed("phase", label):
                        return fn()
                return pool.submit(run)

            nodes_f = phase("overview.nodes", lambda: [map_node(n, include_raw=False) for n in self._iter_list(self._core.list_node)])
            pods_f = phase("overview.pods", self._overview_pods)
            events_f = phase("overview.events", self._overview_events)
            spaces_f = phase("overview.namespaces", lambda: sum(1 for _ in self._iter_list(self._core.list_namespace)))
            usage_f = phase("overview.metrics", self.node_usage)
            nodes = nodes_f.result()
            counts, lists = pods_f.result()
            events, warnings, warning_rows = events_f.result()
            namespaces = spaces_f.result()
            used = usage_f.result()
        lists["warnings"] = warning_rows
        cpu = sum(n.cpu for n in nodes)
        mem = sum(n.memory for n in nodes)
        counts.update({
            "nodes": len(nodes), "nodes_ready": sum(1 for n in nodes if n.is_ready),
            "namespaces": namespaces,
            "cpu": f"{cpu:.1f} cores", "memory": f"{mem:.2f} GiB",
            "warnings": warnings,
        })
        if used:  # metrics-server present: the overview draws usage bars against allocatable
            counts["usage"] = {"cpu": (sum(u[0] for u in used.values()), cpu),
                               "memory": (sum(u[1] for u in used.values()), mem)}
        return {"counts": counts, "events": events, "lists": lists}

    def overview_workloads(self, namespace=None):
        counts, lists = self._overview_pods(namespace)
        events, warnings, warning_rows = self._overview_events(namespace)
        lists["warnings"] = warning_rows
        apps = self._apps
        for key, all_fn, ns_fn in (
            ("deployments", apps.list_deployment_for_all_namespaces, apps.list_namespaced_deployment),
            ("replicasets", apps.list_replica_set_for_all_namespaces, apps.list_namespaced_replica_set),
            ("statefulsets", apps.list_stateful_set_for_all_namespaces, apps.list_namespaced_stateful_set),
        ):
            items = self._iter_list(ns_fn, namespace) if namespace else self._iter_list(all_fn)
            counts[key] = sum(1 for _ in items)
        for key in ("daemonsets", "jobs", "cronjobs"):
            counts[key] = sum(1 for _ in self._resource_items(key, namespace))
        counts["warnings"] = warnings
        return {"counts": counts, "events": events, "lists": lists}

    def scale_status(self, kind, name, namespace):
        """Snapshot used by the scale monitor: replica counts, per-pod state with failure reasons, warnings."""
        apps = self._apps
        read = {"Deployment": apps.read_namespaced_deployment, "ReplicaSet": apps.read_namespaced_replica_set,
                "StatefulSet": apps.read_namespaced_stateful_set}[kind]
        obj = read(name, namespace, _request_timeout=15)
        st = obj.status
        selector = ",".join(f"{k}={v}" for k, v in (obj.spec.selector.match_labels or {}).items())
        items = self._core.list_namespaced_pod(namespace, label_selector=selector, _request_timeout=15).items if selector else []
        pods = []
        for p in items:
            row = map_pod(p)
            issue = row.status if row.status in FAILING_STATES else ""
            for c in p.status.conditions or []:
                if c.type == "PodScheduled" and c.status == "False":
                    issue = f"Unschedulable: {(c.message or c.reason or '')[:120]}"
            for cs in p.status.container_statuses or []:
                t = cs.last_state.terminated if cs.last_state else None
                if t and t.reason in ("OOMKilled", "Error") and cs.restart_count and not issue:
                    issue = f"{t.reason} (restarted {cs.restart_count}x)"
            pods.append({"name": row.name, "status": row.status, "ready": row.ready, "restarts": row.restarts,
                         "issue": issue, "deleting": bool(p.metadata.deletion_timestamp)})
        names = {p["name"] for p in pods} | {name}
        warnings = []
        try:  # events are best effort; a failure here must not break the monitor
            evs = self._core.list_namespaced_event(namespace, field_selector="type=Warning", _request_timeout=15).items
            evs = [e for e in evs if e.involved_object and e.involved_object.name in names]
            evs.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp, reverse=True)
            warnings = [f"{e.involved_object.name}: {e.reason}: {(e.message or '')[:160]}" for e in evs[:5]]
        except Exception:
            pass
        deadline = any(c.type == "Progressing" and c.reason == "ProgressDeadlineExceeded" for c in st.conditions or [])
        return {"desired": obj.spec.replicas or 0, "ready": st.ready_replicas or 0,
                "available": getattr(st, "available_replicas", None) or 0, "pods": pods, "warnings": warnings,
                "observed": (st.observed_generation or 0) >= (obj.metadata.generation or 0), "deadline_exceeded": deadline}
