# Local identities and Klotse access

The Terraform credentials are fixture credentials for the cloud-management
APIs, not Kubernetes login credentials. Each provider instead gets a dedicated
Kubernetes user with the prefix below, independently authorized on its two
real-k3s APIs. Each API issues its own short-lived client certificate and each
certificate uses its own freshly generated private key. This gives Klotse a
real Kubernetes identity without launching a cloud CLI or saving a cloud login.

| Provider | Kubernetes username prefix | File to select in Klotse |
| --- | --- | --- |
| AWS | klotse-lab-aws | .local/aws.kubeconfig |
| Azure | klotse-lab-azure | .local/azure.kubeconfig |
| GCP | klotse-lab-gcp | .local/gcp.kubeconfig |
| OCI | klotse-lab-oci | .local/oci.kubeconfig |

From the lab directory, after Terraform has applied all four roots, run:

```sh
python3 identity.py create aws
python3 identity.py create azure
python3 identity.py create gcp
terraform -chdir=oci output -json clusters
python3 identity.py create oci --oci-ids <klotse-oke-1-id> <klotse-oke-2-id>
```

Replace the OCI placeholders with the two non-secret OCIDs in the order shown
above. The helper discovers the matching lab-owned k3s containers, verifies
their published ports, reads bootstrap certificates into temporary private
files and removes those files when finished. The emulator metadata API
does not provide the least-privilege credentials used by Klotse.

The client certificates avoid the cloud-token webhooks. The roles allow listing
and watching nodes, namespaces, pods and services; other Klotse views may
correctly report permission errors. Use the generated
file, never the admin file, in Klotse.

1. In **Preferences > General > Kubeconfig**, browse to the applicable file
   from the table. Do not use **Add clusters from the cloud** or **Sign in**.
2. Connect to both listed contexts. Open Nodes and Pods for each and verify the
   views load. Cross-check with these read-only commands (repeat for every
   generated file and both contexts):

   ```powershell
   kubectl --kubeconfig .local/aws.kubeconfig --context klotse-eks-1 auth whoami
   kubectl --kubeconfig .local/aws.kubeconfig --context klotse-eks-1 get nodes
   kubectl --kubeconfig .local/aws.kubeconfig --context klotse-eks-1 auth can-i get secrets --all-namespaces
   ```

   For AWS the first response should identify
   a username beginning with `klotse-lab-aws-`, and the last should say
   `no`. Check the corresponding identity for each other provider as well.
3. Repeat the create command with **reset** instead of **create** to revoke the
   old user's lab RBAC and issue new client certificates for a fresh username.
   Close and reopen the contexts in Klotse. Eight hours is requested; the
   API can shorten it. Old certificates may still authenticate until expiry,
   but the old username no longer has the lab permissions.
4. Before destroying the emulators, repeat with **remove** to revoke the
   lab RBAC and delete its generated kubeconfig. In Klotse restore the previous
   kubeconfig.

Do not copy private keys, client kubeconfigs, bootstrap certificates, Terraform state or
raw kubeconfig output into logs, screenshots, issues or Git. Do not set
`TF_LOG`, `kubectl -v`, or shell tracing during these steps.

For a repeatable headless check with Klotse's runtime dependencies installed,
run `PYTHONPATH=../../src python3 check_klotse.py <provider>` from this
directory. It connects with Klotse's own Kubernetes service, lists Nodes
and Pods on both contexts, and reports only counts. This does not check
native window rendering or interaction.

## What this does not authenticate

Klotse uses plain Kubernetes kubeconfig authentication for all four files.
No AWS SSO/MFA, Entra interactive sign-in, Google OAuth, or OCI browser/MFA
session is configured. The built-in cloud discovery/sign-in integrations
are not redirected or verified by this lab. Never run them with dummy lab
secrets or real cloud credentials while testing the lab. Emulator cloud
authentication is not an isolation boundary; in particular Floci GKE's
cloud-token webhook can accept arbitrary nonempty bearer tokens. Keep the
emulators and k3s API ports on a private local host, not on a shared network.
