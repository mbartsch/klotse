"""Turn an existing workload's manifest into the starting point for a new, separate one (pure, no Qt, no network).

The copy is meant for trying something: a quick pod like another one, a Deployment with one setting changed. So it is made to be
safe by default: nothing the cluster filled in comes along, a copy cannot be adopted by (or compete with) the original's
controller, and nothing starts running in bulk or on a schedule unless the user turns that on in the form."""
import copy

COPYABLE = ("Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
SERVER_METADATA = ("uid", "resourceVersion", "creationTimestamp", "generation", "managedFields", "ownerReferences", "finalizers",
                   "selfLink", "deletionTimestamp", "deletionGracePeriodSeconds", "generateName")
DROP_ANNOTATIONS = ("kubectl.kubernetes.io/last-applied-configuration", "deployment.kubernetes.io/revision")
CONTROLLER_LABELS = ("pod-template-hash", "controller-revision-hash", "statefulset.kubernetes.io/pod-name",
                     "apps.kubernetes.io/pod-index", "controller-uid", "batch.kubernetes.io/controller-uid", "job-name",
                     "batch.kubernetes.io/job-name")
COPIED_FROM = "klotse.io/copied-from"


def copy_name(name, kind):
    """`<name>-copy`, cut so it stays valid (63 characters; a CronJob's 52)."""
    limit = 52 if kind == "CronJob" else 63
    suffix = "-copy"
    return name[:limit - len(suffix)].rstrip("-.") + suffix


def _clean_template_labels(template):
    labels = ((template.get("metadata") or {}).get("labels"))
    if isinstance(labels, dict):
        for key in CONTROLLER_LABELS:
            labels.pop(key, None)


def _clean_pod_spec(spec):
    spec.pop("nodeName", None)  # let the scheduler choose; the original's node may be the thing being investigated
    spec.pop("serviceAccount", None)  # the deprecated twin of serviceAccountName
    spec.pop("priority", None)  # derived from priorityClassName
    spec.pop("ephemeralContainers", None)
    injected = {v.get("name") for v in spec.get("volumes") or [] if str(v.get("name", "")).startswith("kube-api-access-")}
    if injected:
        spec["volumes"] = [v for v in spec["volumes"] if v.get("name") not in injected]
        for group in ("containers", "initContainers"):
            for container in spec.get(group) or []:
                mounts = [m for m in container.get("volumeMounts") or [] if m.get("name") not in injected]
                if mounts:
                    container["volumeMounts"] = mounts
                else:
                    container.pop("volumeMounts", None)
        if not spec["volumes"]:
            spec.pop("volumes")


def clone_manifest(kind, raw):
    """(body, notes): the new object's manifest and a list of short sentences saying what was changed from the original on
    purpose. Raises ValueError for a kind that cannot be copied."""
    if kind not in COPYABLE:
        raise ValueError(f"A {kind} cannot be copied this way.")
    body = copy.deepcopy(raw or {})
    body.pop("status", None)
    meta = body.setdefault("metadata", {})
    original = meta.get("name", "")
    for key in SERVER_METADATA:
        meta.pop(key, None)
    annotations = meta.get("annotations") or {}
    for key in DROP_ANNOTATIONS:
        annotations.pop(key, None)
    meta["name"] = copy_name(original, kind)
    notes = [f"named {meta['name']} (change it in the form)"]
    spec = body.setdefault("spec", {})
    if kind == "Pod":
        _clean_pod_spec(spec)
        if meta.pop("labels", None):
            notes.append("labels removed, so the original's ReplicaSet or Service does not adopt or feed the copy")
        annotations[COPIED_FROM] = original
        notes.append("node and token volumes dropped")
    else:
        template = spec.get("template") if kind != "CronJob" else ((spec.get("jobTemplate") or {}).get("spec") or {}).get("template")
        if kind == "CronJob":
            spec["suspend"] = True
            notes.append("suspended, so it does not run on its schedule until you turn that on")
            job_spec = (spec.get("jobTemplate") or {}).get("spec") or {}
            job_spec.pop("selector", None)
            job_spec.pop("manualSelector", None)
        if kind == "Job":
            spec.pop("selector", None)
            spec.pop("manualSelector", None)
        if template is not None:
            _clean_template_labels(template)
            _clean_pod_spec(template.setdefault("spec", {}))
        if kind in ("Deployment", "StatefulSet") and int(spec.get("replicas", 1) or 0) > 1:
            notes.append(f"replicas set to 1 (the original has {spec['replicas']})")
            spec["replicas"] = 1
        if kind in ("Deployment", "StatefulSet", "DaemonSet"):
            notes.append("selector and the app label follow the new name; other pod labels are kept, so a Service may send "
                         "traffic to the copy")
    if not annotations:
        meta.pop("annotations", None)
    else:
        meta["annotations"] = annotations
    return body, notes
