# Klotse roadmap

Features other Kubernetes desktop tools have and Klotse does not yet. **None of these is implemented.** The list comes from comparing Klotse's source with Aptakube, Headlamp, Lens/Freelens and k9s (the first two from their public pages and release notes, the last two from memory, not re-checked). Each item is tracked as a GitHub issue labelled `enhancement` and is meant to be done gradually, one pull request each.

Suggested order: items 1-4 are small, 5 is the biggest gain in daily use, the rest follow.

| # | Item | Seen in | Effort |
| --- | --- | --- | --- |
| 1 | Rollout restart (**done**, #66) for Deployments, StatefulSets and DaemonSets; history and undo (**done** for Deployments only; StatefulSets and DaemonSets would need ControllerRevisions) | Lens, k9s, Aptakube | Small (patch an annotation) |
| 2 | More resource kinds: HPA, PDB, ResourceQuota, LimitRange, Endpoints/EndpointSlice, PriorityClass, webhook configurations, Leases (**done**, #52) | all | Small each (a `Spec` entry plus a mapper) |
| 3 | Apply YAML / create any resource (**done**, #69) | Lens, Headlamp | Small |
| 4 | "Can I?" permission check for the current user (**done**: check and rules list #68; menu entries the rules forbid carry a "(not allowed for you)" note, not hidden or disabled, #54) | Headlamp, k9s | Small |
| 5 | Port-forward (pod or service) with a list of active forwards (**done**, #55) | Aptakube, Lens, Headlamp, k9s | Medium |
| 6 | Related objects and owners (Deployment, ReplicaSet, Pods; Service, Endpoints) | k9s, Lens | Medium |
| 7 | Search across kinds and namespaces | Lens, k9s | Medium |
| 8 | Helm releases: list, values, history, uninstall (**done**, #58; rollback and install are not) | Aptakube, Lens | Medium (release Secrets or the `helm` CLI) |
| 9 | File copy to and from a pod | Lens, k9s | Small to medium |
| 10 | Embedded terminal (termqt or xterm.js: the owner's decision is open) | Lens, Aptakube | Medium to large |
| 11 | Metrics history charts (needs Prometheus; today only current values) | Lens, Aptakube | Large |
| 12 | Several clusters side by side in one view | Aptakube | Large |

GitHub issues: items 1-12 are #51-#62 in table order; the Azure plugin is #63 and the GCP plugin #64.

Separate, also not built: GCP and Azure plugins like the AWS one (see "Not built: GCP and Azure plugins" in `PLUGINS.md`).

## More ideas (second investigation)

Also not implemented and **no issue filed yet**; effort and value are estimates. Ideas come from k9s, Lens, Headlamp and Aptakube (public pages; some from memory). Numbering continues the table above.

### Safety and productivity

| # | Item | Effort |
| --- | --- | --- |
| 13 | Read-only / "production" mode per cluster (**done**: banner, tab mark, every change refused with an explanation, saved per cluster; greying out the buttons and the Navigator menu entry are not done) | Small to medium |
| 14 | Server-side dry-run and a diff before saving in the form and YAML editors | Medium |
| 15 | Command palette (Ctrl+K): jump to any view, namespace, cluster or action | Medium |
| 16 | Favourites, saved filters and pinned namespaces | Small |
| 17 | Add clusters from the cloud (list EKS/GKE/AKS clusters, write kubeconfig entries), as a plugin | Medium |

### Debugging

| # | Item | Effort |
| --- | --- | --- |
| 18 | Logs from many pods at once (Deployment or label selector, like `stern`), regex filter, time range, save | Medium |
| 19 | Events timeline in Details for the selected object (**done**, #67) | Small |
| 20 | Debug container (`kubectl debug`, ephemeral container) for pods | Small to medium |
| 21 | Health check view (like k9s's Popeye): no limits or probes, `latest` tags, crash-looping pods, unbound PVCs; reuse the editor hints | Medium |
| 22 | Right-sizing view: requests and limits against actual usage (the `%req`/`%lim` columns exist) | Medium |
| 23 | Drain preview: which pods would be evicted, which PodDisruptionBudgets would block | Small to medium |

### Finding things and security

| # | Item | Effort |
| --- | --- | --- |
| 24 | "Used by" for Secrets, ConfigMaps and PVCs, and a list of unused objects | Medium |
| 25 | RBAC reverse lookup ("who can do X?") and a ServiceAccount's effective permissions | Medium |
| 26 | Certificate expiry (TLS Secrets, cert-manager Certificates) | Small to medium |
| 27 | Pod security findings across the cluster (privileged, hostPath, root) | Medium |
| 28 | Trivy Operator reports, read-only | Medium |

### Newer Kubernetes objects

| # | Item | Effort |
| --- | --- | --- |
| 29 | Gateway API (Gateway, HTTPRoute) views and editors | Medium |
| 30 | Karpenter (NodePool, NodeClaim) views (**done**: extra columns, and a Karpenter group in the navigator, shown only when the cluster serves Karpenter's API groups, with NodePools, NodeClaims and the provider's node classes; field names unverified against a running Karpenter) | Small to medium |
| 31 | GitOps: read-only Argo CD and Flux views with Sync and Refresh (needs cluster-wide plugin actions, which are not designed yet) | Medium |
| 32 | Schema help in the YAML editor: field docs and completion from the cluster's OpenAPI | Large |
| 33 | Custom Resources > Groups: list CRDs by API group, next to Definitions (**done**, issue #74; the groups are also sub items in the navigator) | Small to medium |
| 34 | Node tab: overview of status, CPU and memory at the top, beside the pod list (**done**, issue #84) | Small to medium |
| 35 | Manual refresh only for CRDs, Access Control and other rarely changing lists: no timer or watch, refresh by the toolbar button or a right-click entry (**done**, issue #86) | Small to medium |

### Undecided or not planned

- **AI assistant** ("explain this error", an MCP server): Lens offers one. Raises privacy and key-handling questions; only if the owner wants it.
- **Benchmarking** (k9s embeds an HTTP load generator on port-forwards): not planned.

Recommended first picks from this group: 13, 14, 18 and 15.

Done so far is marked in the tables; none of it has been tried against a live cluster. Issues #51 (undo and history left) and #54 (greying out left) stay open.

Keep this file and the issues in step: when an item is merged, mark it done here and close its issue.
