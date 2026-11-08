# Klotse roadmap

Features other Kubernetes desktop tools have and Klotse does not yet. Items marked **done** are merged; the others are not implemented. The list comes from comparing Klotse's source with Aptakube, Headlamp, Lens/Freelens and k9s (the first two from their public pages and release notes, the last two from memory, not re-checked). Each item is tracked as a GitHub issue labelled `enhancement` and is meant to be done gradually, one pull request each.

**Status on 2026-11-06.** Done and merged: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 19, 30, 33, 34, 35 and 36 (including the automatic-refresh defaults: only workloads and nodes refresh by themselves). Open issues: #61 (11, metrics charts), #62 (12, several clusters side by side), #63 and #64 (the Azure and GCP plugins). Ideas 14-18, 20-29, 31, 32 and 36-38 have issues #96-#115 in that order (14 = #96, 15 = #97, 16 = #98, 17 = #99, 18 = #100, 20-29 = #101-#110, 31 = #115, 32 = #111, 36-38 = #112-#114). A new idea gets an issue with an effort label before it is started. "Done" means merged with tests; none of it has been run against a live cluster. Every issue carries `enhancement` and an effort label.

| # | Item | Seen in | Effort |
| --- | --- | --- | --- |
| 1 | Rollout restart (**done**, #66) for Deployments, StatefulSets and DaemonSets; history and undo (**done** for Deployments only; StatefulSets and DaemonSets would need ControllerRevisions) | Lens, k9s, Aptakube | Small (patch an annotation) |
| 2 | More resource kinds: HPA, PDB, ResourceQuota, LimitRange, Endpoints/EndpointSlice, PriorityClass, webhook configurations, Leases (**done**, #52) | all | Small each (a `Spec` entry plus a mapper) |
| 3 | Apply YAML / create any resource (**done**, #69) | Lens, Headlamp | Small |
| 4 | "Can I?" permission check for the current user (**done**: check and rules list #68; menu entries the rules forbid carry a "(not allowed for you)" note, not hidden or disabled, #54) | Headlamp, k9s | Small |
| 5 | Port-forward (pod or service) with a list of active forwards (**done**, #55) | Aptakube, Lens, Headlamp, k9s | Medium |
| 6 | Related objects and owners (Deployment, ReplicaSet, Pods; Service, Endpoints) (**done**, #56) | k9s, Lens | Medium |
| 7 | Search across kinds and namespaces (**done**, #57) | Lens, k9s | Medium |
| 8 | Helm releases: list, values, history, uninstall (**done**, #58; rollback and install are not) | Aptakube, Lens | Medium (release Secrets or the `helm` CLI) |
| 9 | File copy to and from a pod (**done**, #59) | Lens, k9s | Small to medium |
| 10 | Embedded terminal with xterm.js (MIT) in QtWebEngine (**done**, #60; opt-in in Preferences > Terminal) | Lens, Aptakube | Medium to large |
| 11 | Metrics history charts (needs Prometheus; today only current values) (**done**, #61: charts from metrics-server sampled while the window is open, and #114: real history from Prometheus / VictoriaMetrics through the API server proxy; the Prometheus side has not been run against a real server) | Lens, Aptakube | Large |
| 12 | Several clusters side by side in one view (**done** as a merged list of one kind, #62: View > Several clusters…; not side-by-side panes, not run with two real clusters) | Aptakube | Large |

GitHub issues: items 1-12 are #51-#62 in table order; the Azure plugin is #63 and the GCP plugin #64.

Separate, also not built: GCP and Azure plugins like the AWS one (see "Not built: GCP and Azure plugins" in `PLUGINS.md`).

## More ideas (second investigation)

Also not implemented and **no issue filed yet**; effort and value are estimates. Ideas come from k9s, Lens, Headlamp and Aptakube (public pages; some from memory). Numbering continues the table above.

### Safety and productivity

| # | Item | Effort |
| --- | --- | --- |
| 13 | Read-only / "production" mode per cluster (**done**: banner, tab mark, every change refused with an explanation, saved per cluster; greying out the buttons and the Navigator menu entry are not done) | Small to medium |
| 14 | Server-side dry-run and a diff before saving in the form and YAML editors (**done**, #96: View > Review changes before saving) | Medium |
| 15 | Command palette (Ctrl+K): jump to any view, namespace, cluster or action (**done**, #97) | Medium |
| 16 | Favourites, saved filters and pinned namespaces (**pinned namespaces and saved filters done**, #98: a ☆/★ button beside the selector, pinned ones listed first per cluster; a ★ Filters menu on the lists keeps named filters per cluster and kind; pinned views are not done) | Small |
| 17 | Add clusters from the cloud (list EKS/GKE/AKS clusters, write kubeconfig entries), as a plugin | Medium |

### Debugging

| # | Item | Effort |
| --- | --- | --- |
| 18 | Logs from many pods at once (Deployment or label selector, like `stern`), regex filter, time range, save. **Top priority**: logs per workload is among the most upvoted requests in Lens (36) and Freelens (12) (**done**, #100) | Medium |
| 19 | Events timeline in Details for the selected object (**done**, #67) | Small |
| 20 | Debug container (`kubectl debug`, ephemeral container) for pods (**done**, #101) | Small to medium |
| 21 | Health check view (like k9s's Popeye): no limits or probes, `latest` tags, crash-looping pods, unbound PVCs; reuse the editor hints (**done**, #102) | Medium |
| 22 | Right-sizing view: requests and limits against actual usage (the `%req`/`%lim` columns exist) (**done**, #103: a snapshot view; history-based advice needs the metrics source of item 38) | Medium |
| 23 | Drain preview: which pods would be evicted, which PodDisruptionBudgets would block (**done**, #104) | Small to medium |

### Finding things and security

| # | Item | Effort |
| --- | --- | --- |
| 24 | "Used by" for Secrets, ConfigMaps and PVCs (**done**: the Related view), and a list of unused objects (**done**, #105: View > Unused objects…) | Medium |
| 25 | RBAC reverse lookup ("who can do X?") and a ServiceAccount's effective permissions (**done**, #106) | Medium |
| 26 | Certificate expiry (**done**, #107: cert-manager Certificates get Ready, Expires, Renewal columns from their own status; TLS Secrets get Expires and Subject columns read from the public `tls.crt`) | Small to medium |
| 27 | Pod security findings across the cluster (privileged, hostPath, root) (**done**, #108: View > Pod security…) | Medium |
| 28 | Trivy Operator reports, read-only (**done**, #109: View > Trivy reports…; not run against a real operator) | Medium |

### Newer Kubernetes objects

| # | Item | Effort |
| --- | --- | --- |
| 29 | Gateway API (Gateway, HTTPRoute) views and editors (**done**, #110: lists with their own columns and YAML editing; there is no form editor) | Medium |
| 30 | Karpenter (NodePool, NodeClaim) views (**done**: extra columns, and a Karpenter group in the navigator, shown only when the cluster serves Karpenter's API groups, with NodePools, NodeClaims and the provider's node classes; field names unverified against a running Karpenter) | Small to medium |
| 31 | GitOps: read-only Argo CD and Flux views with Sync and Refresh (needs cluster-wide plugin actions, which are not designed yet) | Medium |
| 32 | Schema help in the YAML editor: field docs and completion from the cluster's OpenAPI | Large |
| 33 | Custom Resources > Groups: list CRDs by API group, next to Definitions (**done**, issue #74; the groups are also sub items in the navigator) | Small to medium |
| 34 | Node tab: overview of status, CPU and memory at the top, beside the pod list (**done**, issue #84) | Small to medium |
| 35 | Manual refresh only for CRDs, Access Control and other rarely changing lists: no timer or watch, refresh by the toolbar button or a right-click entry (**done**, issue #86; now the default for every list except workloads and nodes, which stay automatic) | Small to medium |
| 36 | Log viewer options: wrap lines (Lens users' second most upvoted request, 61 votes), show timestamps, previous container instance (**done**, #112) | Small |
| 37 | (**done**, #113: failures explained with the fix, helper stdin guard, `proxy-url` verified; the helper is not run to test it, no in-app sign-in) Sign-in check for exec auth helpers (AWS SSO, `aws-iam-authenticator` with an MFA prompt, OIDC): the Python client runs the helper without a terminal, so a prompt can fail; test and report a clear error. Also honour the kubeconfig `proxy-url`. Requested often in Lens (72 votes) and Headlamp (SSO 16, 15; proxy 14) | Medium |
| 38 | Metrics from Prometheus or VictoriaMetrics and clearer metrics-server messages: the most upvoted Freelens requests (40, 21, 16). Overlaps item 11 (charts); do the data source first (**Prometheus / VictoriaMetrics part done**, #114; the clearer metrics-server messages are not) | Medium to large |

### Undecided or not planned

- **AI assistant** ("explain this error", an MCP server): Lens offers one. Raises privacy and key-handling questions; only if the owner wants it.
- **Benchmarking** (k9s embeds an HTTP load generator on port-forwards): not planned.

## Ideas from the collabnix/kubetools list (2026-11-07)

| # | Item | Effort | Issue |
| --- | --- | --- | --- |
| 39 | Upgrade preflight: deprecated APIs, PodDisruptionBudgets at zero, version skew, certificate dates (kubent, pluto, k8s-preflight, Shoalcheck) (**done**, #122: View > Upgrade preflight…) | Medium | #122 |
| 40 | Compare two objects side by side (kbu, kubediff) (**done** for two objects of one list, #123; across clusters or namespaces is still open) | Small to medium | #123 |
| 41 | CronJob: run now, suspend and resume (Kronic) (**done**, #124) | Small | #124 |
| 42 | Saved port-forwards that can be started again (kftray) (**done**, #125) | Small to medium | #125 |
| 43 | Diagnostics bundle export: logs, events, YAML in a zip, no secrets (groot, k8shark) (**done**, #126) | Medium | #126 |
| 44 | Browse files in a pod or PVC (kubectl-browse-pvc) (**done**, #127: pods, and claims through a pod that already mounts them) | Medium | #127 |
| 45 | Say clearly when a list is refused (403) instead of showing it empty (Rubick) (**done**, #128) | Small | #128 |
| 46 | Local audit log of changes made from Klotse (Lumovi) (**done**, #129) | Small to medium | #129 |
| 47 | Capacity overview: requests, limits and utilization per node and namespace (kube-capacity) (**done**, #130) | Small to medium | #130 |
| 48 | Ownership topology graph (Radar, KubeView) | Large | #131 |
| 51 | Filter expressions on columns (`Restarts > 3`, `CPULim = 2*CPUReq`, `Kind = Deployment`, `Name ~ regex`, and/or) (**done**, #165) | Medium | #165 |
| 50 | Big-cluster mode (**done**): clusters with 50+ nodes or 1,500+ pods are marked and read more lightly (Warning events only on the Overview, 2,000-item pages, parallel Health/Capacity/metrics reads, heavy lists reload at most every 30 s) | Medium | |
| 49 | Timing log (debug option, **done**): `timings.log` shows where load time goes, to measure big clusters | Small | |

Also seen there and already planned: unused objects (kor, item 24), right-sizing (Goldilocks, item 22), RBAC reverse lookup (kube-who-can, item 25), health check (item 21), Trivy reports. Seen and not planned: AI assistants and MCP servers, chaos testing, packet capture, local-development tools (Tilt, Telepresence) and cluster provisioning.

Recommended first picks from this group: 13, 14, 18 and 15.

Done so far is marked in the tables; none of it has been tried against a live cluster. Issues #51 (undo and history left) and #54 (greying out left) stay open.

Keep this file and the issues in step: when an item is merged, mark it done here and close its issue.
