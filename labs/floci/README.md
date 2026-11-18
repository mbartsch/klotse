# Local Floci Terraform lab (#793)

This lab declares two clusters and one two-node pool per cluster for each
provider. It never needs an account at AWS, Microsoft, Google or Oracle.

| Terraform root / Compose service | Cluster names | Management endpoint | k3s API port range |
| --- | --- | --- | --- |
| aws | klotse-eks-1, klotse-eks-2 | http://127.0.0.1:4566 | 16443-16452 |
| azure | klotse-aks-1, klotse-aks-2 | https://localhost:4577 | 16543-16552 |
| gcp | klotse-gke-1, klotse-gke-2 | http://container.localhost:4588/ | 16643-16652 |
| oci | klotse-oke-1, klotse-oke-2 | http://127.0.0.1:4599 | 16743-16752 |

## Prerequisites and isolation

Use a disposable local Linux VM or dedicated workstation with a rootful
Docker Engine running Linux containers, Docker Compose v2+, Terraform 1.9+,
kubectl, Python 3.10+, curl and OpenSSL. Run Terraform, Docker and Klotse on
the same host. Do not use a remote Docker context. Eight k3s servers have
substantial CPU, memory and disk needs. The Compose images have release tags;
the provider dependencies are locked in each Terraform root.

Floci uses the Docker socket to create privileged k3s containers. **Use a
dedicated Docker daemon** with no unrelated workloads or credentials.
Before starting the lab, the daemon's default bridge must publish ports only
on IPv4 loopback (Docker daemon setting `"ip": "127.0.0.1"`). Verify that the
bridge reports the configured address. Floci publishes the child APIs itself;
the explicit loopback mapping on the Compose services does not cover them.
Do not change the Docker daemon used for your normal workloads.

```sh
docker network inspect bridge --format '{{index .Options "com.docker.network.bridge.host_binding_ipv4"}}'
```

Use an isolated OS user with no cloud credentials, profiles or kubeconfig.
Disable HTTP proxies and Terraform tracing. Permit registry downloads during
setup; block host/provider egress to cloud APIs during plan/apply/destroy
(e.g. run Terraform offline in the disposable VM after caching providers and
container images). Do not route traffic from a cloud CLI to its real service.
The Terraform endpoint overrides and fixture credentials are not a firewall.
Do not reuse these roots or state files in a production environment.

## Start and provision

From this directory:

```sh
python3 start.py
```

The launcher refuses to start with a non-loopback Docker default bridge.
Wait for each emulator to respond at its loopback management endpoint's
`/_floci/health`. Use HTTP for the health checks (including Azure). Check that
`container.localhost` resolves to 127.0.0.1; configure that exact loopback
alias in the disposable host if it does not. GKE Terraform uses host routing,
not a /container/ path.

On Linux, fetch Azure's generated CA into a private local directory and use a
per-shell bundle, without changing the OS trust store. Generate an OCI signing
key locally; the OCI SDK consumes the exact environment variable below.
Do not pass the override via -var or tfvars; doing so does not configure the SDK.

```sh
umask 077
mkdir -p .local
curl -fsS http://127.0.0.1:4577/_floci/tls-cert -o .local/floci-az.crt
cat /etc/ssl/certs/ca-certificates.crt .local/floci-az.crt > .local/roots.pem
export SSL_CERT_FILE="$PWD/.local/roots.pem"
openssl genrsa -out .local/oci.pem 2048
fingerprint=$(openssl rsa -in .local/oci.pem -pubout -outform DER | openssl md5 -c | awk '{print $NF}')
cat > .local/oci-config <<EOF
[DEFAULT]
tenancy=ocid1.tenancy.oc1..flocilocaltenancy0000000000000000000000000000000000000000
user=ocid1.user.oc1..flociiacuser0000000000000000000000000000000000000000
fingerprint=$fingerprint
key_file=$PWD/.local/oci.pem
region=us-ashburn-1
EOF
export TF_VAR_oci_config_file="$PWD/.local/oci-config"
export TF_VAR_CLIENT_HOST_OVERRIDES='oci_containerengine.ContainerEngineClient=http://127.0.0.1:4599'
for provider in aws azure gcp oci; do
  terraform -chdir="$provider" init -lockfile=readonly || break
done
```

After initializing, with the lab isolation in place, provision one root at a
time. For example, review the AWS plan then apply it; repeat with azure, gcp
and oci. Run only if the planned operations are the fixtures in this directory.
Do not upload plan, state, kubeconfig or private key files.

```sh
terraform -chdir=aws plan -out=lab.tfplan
terraform -chdir=aws apply lab.tfplan
```

Check that the k3s API bindings shown by Docker are exclusively on 127.0.0.1.
The identity helper refuses to use a sidecar whose API is not loopback-bound.
Then follow [Local identities and Klotse access](IDENTITIES.md) to create
one distinct Kubernetes identity per provider across its two clusters and
connect Klotse without an external cloud login.

## Tear down

1. While the emulators are still up, remove the lab identities using
   [the identity helper](IDENTITIES.md) and restore the previous Klotse config.
2. With the same CA, OCI profile and endpoint override environment as above and the same egress
   isolation, run `terraform -chdir=oci destroy`, then gcp, azure and aws.
   Confirm each destroy completes before stopping its emulator.
3. Run `docker compose down --volumes` from this directory. Inspect
   `docker ps -a --filter label=floci_namespace=klotse793` and
   `docker volume ls --filter label=floci_namespace=klotse793`. If a provider
   left children behind, verify they belong to this exact lab before removing
   them individually. Never run a broad Docker prune.
4. After verifying destruction, delete the now-unused lab-only .local files,
   plan files and local Terraform state/backups. Close the
   isolated shell. Never delete state while resources still exist.

## Verification and limitations

Terraform 1.9.8 validation and mocked plan assertions cover two two-node
pools per provider. They do not start an emulator, prove live Terraform API
compatibility, count real workers, or verify Klotse. Compose configuration is
validated. Run `python3 -m unittest discover -v` from this directory to check
loopback guards and credential-file handling offline. The current Docker daemon advertises 0.0.0.0 for implicit
published ports, so runtime access, authentication in Klotse and teardown
have not been exercised here.

These are k3s-backed fixtures, not EKS, AKS, GKE or OKE. Requested node pool
size is emulator management metadata, not proof of two running workers.
Cloud IAM, networking, VM sizes, costs and provider node metadata are not
validated. GKE removes its bootstrap pool and retains a separate two-node
pool. OCI uses synthetic network IDs as in the emulator fixture.

Upstream: [EKS](https://github.com/floci-io/floci/blob/2.5.0/docs/services/eks.md),
[Azure TLS and Terraform](https://github.com/floci-io/floci-az/blob/0.17.0/docs/terraform.md),
[GKE](https://github.com/floci-io/floci-gcp/blob/0.13.0/docs/services/gke.md),
[OCI fixtures](https://github.com/floci-io/floci-oci/tree/0.7.0/compatibility-tests/compat-terraform),
[Docker default published IP](https://docs.docker.com/engine/network/port-publishing/#default-bridge).
