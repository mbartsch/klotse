# Local cloud Terraform lab (#793) — code-first stage

Four independent Terraform roots, each declaring exactly two clusters with one
two-node pool per cluster (16 requested worker nodes total):

| Directory | Cluster names | Floci endpoint |
| --- | --- | --- |
| aws | klotse-eks-1, klotse-eks-2 | http://127.0.0.1:4566 |
| azure | klotse-aks-1, klotse-aks-2 | https://localhost:4577 |
| gcp | klotse-gke-1, klotse-gke-2 | http://container.localhost:4588/ |
| oci | klotse-oke-1, klotse-oke-2 | http://127.0.0.1:4599 |

These are emulator fixtures, not production cloud configurations. Use only
throwaway credentials and an isolated shell with no real cloud profiles.
State and kubeconfig outputs contain credentials; they are ignored locally.
Provider lock files are committed inputs. Never commit signing keys or state.

## Current verification boundary

Terraform 1.9.8: init and validate pass for all four roots; fmt check passes.
Mock-provider plan tests pass for all four roots, asserting two pools with two
nodes each. These tests do not contact emulators or create resources. No real
plan, apply, destroy, cluster connectivity or Klotse UI verification yet.
Emulator startup/teardown orchestration and kubeconfig export instructions are
still pending; this code-first stage does not complete issue #793.

Before applying, start the corresponding Floci emulator in real k3s mode with
Docker access. Eight real k3s clusters can consume substantial local resources.
Do not use mock mode when testing Kubernetes connectivity.

- Azure requires TLS enabled and the local Floci CA trusted by the process
  running Terraform. This lab does not change your OS trust store.
- GCP requires container.localhost to resolve to loopback; the container.* host
  routing is required by the provider. Do not replace it with a /container path.
- OCI uses synthetic network IDs, as upstream's emulator Terraform fixture does.
  main.tf reads tenancy, user, fingerprint, key_file and region from
  `~/.oci/floci-config` (override with `oci_config_file`). Create that profile
  and its throwaway RSA key locally (for example `openssl genrsa -out
  ~/.oci/floci.pem 2048`). Do not use an existing cloud key or config.
  The provider has no native HCL endpoint setting: its SDK reads an environment
  variable. Set this in the same PowerShell process running Terraform:

  ```powershell
  $env:TF_VAR_CLIENT_HOST_OVERRIDES = 'oci_containerengine.ContainerEngineClient=http://127.0.0.1:4599'
  ```

  The required HCL variable validates the expected value, but passing it through
  -var or tfvars does NOT configure the SDK. Never do that. This guard is not a
  network sandbox: use local-only network isolation for execution.

Safe schema checks (from the repository root), repeated for each directory:

```powershell
terraform -chdir=labs/floci/aws init -backend=false
terraform -chdir=labs/floci/aws validate
terraform fmt -check -recursive labs/floci
```

## Upstream implementation references

- AWS fixture: https://github.com/floci-io/floci/tree/main/compatibility-tests/compat-terraform
- Azure provider/TLS: https://github.com/floci-io/floci-az/blob/main/docs/terraform.md
- GKE host routing and node-pool drift: https://github.com/floci-io/floci-gcp/blob/main/docs/services/gke.md
- OCI resource fixtures and SDK overrides: https://github.com/floci-io/floci-oci/tree/main/compatibility-tests/compat-terraform

Real k3s is not real EKS/AKS/GKE/OKE: cloud IAM, networking, VM sizing, costs,
provider-specific node metadata and live-cloud integration are not verified.

Node counts express the requested cloud API configuration, not proof of actual
k3s worker count: emulator versions may model pool sizes without starting a
separate worker container per requested node. GKE's bootstrap default pool is
removed; the retained pool requests two nodes in a single zone. AKS uses its
required default pool, EKS a managed node group, and OKE a node pool with
synthetic local placement. Run `terraform test` in each root for offline checks.
