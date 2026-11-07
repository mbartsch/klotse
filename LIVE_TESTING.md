# Testing Klotse against a real cluster

Everything added since 0.2.0 was tested with fake services and made-up objects only. This page lists what still has to be tried on a
real cluster, and how, so that each feature can be marked verified (or fixed). Tick a box only after you saw the result yourself, and
write what you saw in the PR or issue.

## Ground rules

- **Use a throw-away cluster** (kind, minikube, k3d, a dev namespace on a dev cluster). Never start with production.
- Start with **View > Read-only mode** switched on and do all the "looking" checks first. Switch it off only for the changing checks.
- Create a namespace `klotse-test` and keep everything in it:
  ```sh
  kubectl create namespace klotse-test
  kubectl -n klotse-test create deployment web --image=nginx:1.27 --replicas=2
  kubectl -n klotse-test expose deployment web --port=80
  kubectl -n klotse-test create configmap cfg --from-literal=a=1
  kubectl -n klotse-test create job hello --image=busybox -- sh -c 'echo hi; sleep 5'
  kubectl -n klotse-test create cronjob tick --image=busybox --schedule="*/5 * * * *" -- date
  kubectl -n klotse-test run crash --image=busybox -- sh -c 'exit 1'     # CrashLoopBackOff
  kubectl -n klotse-test run big --image=busybox -- sleep 3600           # no limits, no probes
  ```
- Compare Klotse with `kubectl` for every check ("same answer?"). Cleanup at the end: `kubectl delete namespace klotse-test`.
- Optional extras (only where a check says so): metrics-server, cert-manager, Gateway API CRDs, Karpenter, a Helm release.
- Clean-up of the change log: View > Change log… lists what Klotse changed, which is a quick way to confirm what you did.

## Looking only (safe, do these first)

| Feature | How to test | Expect |
| --- | --- | --- |
| Lists, Details, Related, Events | Open Pods, Deployments, Services. Select `web`: Details > Related, Events | Owners (ReplicaSet > Deployment), pods, service; Events match `kubectl describe` |
| Permission hints | Connect with a limited account (`kubectl create sa viewer`, bind `view`, use its token) and right-click a Deployment | "(not allowed for you)" on Edit/Scale/Delete; admin account shows none. Check View > My permissions… against `kubectl auth can-i --list -n klotse-test` |
| Refused lists | With the limited account, open Secrets | "Refused: you may not list Secrets (403)…", not an empty list |
| Manual refresh | Open Services, change one with kubectl, wait 30 s, then press Refresh | No timer reload; Refresh shows the change. Workloads do reload by themselves |
| CRD groups, Karpenter, Gateway API | Custom Resources > Groups; clusters with Karpenter / `kubectl apply -f` Gateway API standard CRDs | Group sub items listed; Karpenter / Gateway API groups appear only when installed; columns look right; compare with `kubectl get gateways,httproutes` |
| Node overview | Open a node's pods tab, with and without metrics-server | Request/usage bars match `kubectl describe node` and `kubectl top node`; "unknown (no metrics)" without metrics-server |
| Capacity (View > Capacity…) | Open it | Per-node and per-namespace requests vs allocatable match `kubectl describe node` "Allocated resources" |
| Health check (View > Health check…) | Namespace `klotse-test` | `crash` (CrashLoopBackOff), `big` (no limits/probes), `web` (`:latest` is not used, so no tag note) are reported; a healthy system namespace is not flooded with noise |
| Right-sizing | Needs metrics-server; `kubectl -n klotse-test run busy --image=busybox -- sh -c 'while :; do :; done'` | Usage matches `kubectl top pod`; advice makes sense; remember it is one sample |
| RBAC lookup | Who can `delete pods` in `klotse-test`; effective permissions of a service account | Compare with `kubectl auth can-i --list --as=system:serviceaccount:klotse-test:viewer` and `kubectl auth can-i delete pods --as=...` |
| Search objects (Ctrl+Shift+F) | `kind:pod ns:klotse-test web`, `app=web` | Same objects as `kubectl get -A -l app=web`; try a large cluster for timing |
| Compare two objects | Select two Deployments or ConfigMaps > Compare these two… | Diff equals `diff <(kubectl get … -o yaml) …` ignoring server fields |
| Logs: options | Pod logs with Timestamps, Wrap, Previous instance (use `crash`) | Previous shows the last crashed container's log, like `kubectl logs -p` |
| Logs of all pods | Right-click `web` > Logs of all pods…; select two pods; View > Logs by label selector… (`app=web`); scale `web` to 4 while open | Lines prefixed per pod, regex filter works, new pods join within ~10 s, closing the tab stops the streams |
| Secret expiry column | Create a TLS secret (`openssl req -x509 -newkey rsa:2048 -nodes -keyout k -out c -days 30 -subj /CN=t` then `kubectl create secret tls`) | Expires/Subject equal `openssl x509 -noout -enddate -subject`. Also try an EC key and a chain |
| Certificates (cert-manager) | Install cert-manager, create a self-signed Issuer + Certificate | Ready, Expires and Renewal match `kubectl get certificate -o yaml` |
| Helm releases | `helm install demo bitnami/nginx -n klotse-test` | Release listed with status/revisions; Details shows values only after Show values; secret-looking keys stay hidden |
| Drain preview | Node > Drain… > Preview | Prediction matches what `kubectl drain --dry-run=server` says (create a PDB with `minAvailable` to test the budget warning) |
| Browse files (pod and PVC) | Pod > Browse files…; a PVC mounted by a running pod; try a distroless image | Folders/links/files like `kubectl exec … ls -la`; a minimal image without `ls` gives the explanation, not a crash. **Run it from the packaged app / pythonw as well** (the WinError 6 fix) |
| Diagnostics export | Namespace > Export diagnostics… | Zip has objects, events, nodes, logs; open it and check there is **no Secret data and no env values** unless ticked; cancel mid-way deletes the file |
| Port forward | Service `web` > Port forward…; open the address in a browser | Page loads; Stop closes the kubectl process; saved forwards restart it |
| Connection errors | Break the kubeconfig on purpose: expired `aws sso` session, a missing helper command, an unreachable server, a bad `proxy-url` | Message names the helper / proxy and the fix (see #113); a working `proxy-url` through a real proxy connects |

## Changing things (read-only mode off, test namespace only)

| Feature | How to test | Expect |
| --- | --- | --- |
| Rollout restart, history, undo | Deployment `web` > Restart…; change the image; Rollout history / Undo… | Pods replaced one by one; history matches `kubectl rollout history`; undo equals `kubectl rollout undo` |
| Scale (single, bulk, relative `*2`, `/2`) | Scale `web` | Replica counts match `kubectl get deploy` |
| Apply YAML | File > Apply YAML…: Check (dry run) then Apply with two documents, one invalid | Dry run changes nothing; the valid one is applied (field manager `klotse`, see `kubectl get -o yaml --show-managed-fields`); the invalid one is reported without stopping the other |
| Edit as form / YAML (all kinds) | Edit a Deployment, Service, Ingress, NetworkPolicy, ConfigMap, Secret, Role, binding | Saved object equals what the form showed; Secret edit sends only changed keys; a concurrent edit gives a conflict, not an overwrite |
| Copy as new… | Pod, Deployment, CronJob > Copy as new… > Create | Copy runs beside the original, has no owner references, pod labels removed, CronJob suspended |
| CronJob Run now / Suspend | `tick` > Run now…; Suspend… | A Job `tick-manual-…` owned by the CronJob; `spec.suspend` flips |
| Debug container | Pod > Debug (ephemeral container)…; share processes with a container (`ps`); then Remove debug containers | Needs Kubernetes 1.25+; `ps` shows the target's processes; pod recreated after removal; a bare pod is refused |
| Copy files | Upload to and download from a pod; use Browse… to pick the path; a folder; a name with spaces | Same bytes (`sha256sum`); needs `tar` in the image |
| Gateway API edit | With CRDs and a controller: New… Gateway/HTTPRoute, Edit…, Delete… | Created objects equal the YAML; stale edit gives a conflict; delete uses the UID |
| Pinned namespaces, saved filters, saved forwards | Pin a namespace, save a filter, remember a forward, restart Klotse | Still there; unpinned order restored |
| Reconnect + namespace | Connect to two clusters, choose different namespaces, Exit > Yes, start again | Same clusters and namespaces; a deleted namespace falls back sanely |
| Change log | Do any change above, View > Change log… | One line per change, only identity (kind/namespace/name), never object bodies or Secret data |
| Read-only mode | Switch on, try Edit, Delete, Scale, Apply, Shell, Drain, Copy as new | Every one is refused with an explanation; Apply Check still works; looking works |

## Needs a special setup

- **Embedded terminal** (Preferences > Terminal): Shell/Attach/Debug in the Klotse window with a real `kubectl exec`; try `vim`, `top`,
  resize, copy/paste (Ctrl+Shift+C/V), Dock/Pop out; on Linux and macOS too (the POSIX pty was never run).
- **Drain / nsenter node shell**: on a test node only; check `kubectl get pods -A` for a leftover node-shell pod after closing.
- **AWS plugin**: real credentials, EC2 lookups, pricing, console links, federation sign-in (see `PLUGINS.md`).
- **Helm uninstall**: on the demo release only.
- **Packaged apps** (Windows, macOS, Linux pipx): install a release build on a clean machine and repeat the looking checks; Finder-started macOS app (PATH).
- **Large clusters**: timing of Search, Capacity, Health check, Right-sizing, Diagnostics and Logs of many pods (hundreds of nodes / thousands of pods).
- **Other versions**: one old (1.25-1.27) and one recent cluster; EKS, GKE, AKS exec-auth helpers (the `aws`, `gke-gcloud-auth-plugin`, `kubelogin` flows).

## Reporting

For each row: PASS / FAIL / not tried, the Kubernetes version and distribution, and what differed from `kubectl`. File a bug per FAIL.
When a row passes, edit the "not run against a live cluster" sentence for that feature in `AGENTS.md` to say what was verified and when.

## Results so far

### 2026-11-07, `docker-desktop` context (kind-style, 3 nodes, Kubernetes v1.36.4, no metrics-server)

Run headlessly: `KubeService` and the pure modules were driven by scripts against the real API server, not the Qt windows, in a
`klotse-test` namespace (deleted afterwards). The windows, menus, dialogs and the terminal were **not** exercised.

| Result | What |
| --- | --- |
| Verified | Connect; list pods/secrets/events/helm releases/CRD groups; TLS Expires and Subject (date equals `openssl x509 -enddate`); Health check (a CrashLoopBackOff pod is an error; missing limits/requests/probes/untagged image found); drain preview on a worker node; capacity per node; right-sizing without metrics (no crash); RBAC data and "who can delete pods" (cluster-admin groups and controller service accounts found, none missed); search by kind/namespace/name; pods by label selector; related objects; `can_i`; Gateway API and Karpenter correctly absent; read-only mode refuses a scale |
| Verified | Changing: scale, rollout restart, history (revisions 2/1, current marked), rollback via JSON patch, CronJob Run now and Suspend, Apply YAML (dry run stores nothing; a real apply works and records field manager `klotse`; an unknown kind fails alone without stopping the other document), Copy as new for a Pod (new name, no labels, created) |
| Verified | Files and logs: remote directory listing through `kubectl exec ls` on BusyBox and nginx (Debian) images; a missing directory gives "No such directory in the container."; log stream with timestamps; previous-instance log |
| Finding | A pod that fails and restarts (`Error`, 1-4 restarts) is not reported by Health check until Kubernetes shows `CrashLoopBackOff` or 5 restarts. Not a crash, but early failures are missed |
| Not tried | Everything with a window or terminal; metrics-server dependent views (usage, right-sizing advice, node bars); Helm with a real release; cert-manager Certificates; Gateway API objects; debug containers; copy files; port forward; multi-pod log tab; Diagnostics zip leak check beyond "no `tls.key` text, no Secret file" (done: counts and file list only); embedded terminal; packaged apps; other clusters and Kubernetes versions; the exec-helper and proxy error paths |
