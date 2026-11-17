# Local cloud Terraform lab (#793) — code-first stage

Four independent Terraform roots, each declaring exactly two clusters:

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
No plan, apply, destroy, cluster connectivity or Klotse UI verification yet.
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
  Generate a throwaway RSA key at oci/test_key.pem (openssl genrsa -out
  test_key.pem 2048, from that directory). Do not use an existing cloud key.
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
