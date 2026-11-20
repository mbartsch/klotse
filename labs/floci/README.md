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
Recommended, not required: configure that daemon so both its default bridge
and new bridge networks default to publishing on IPv4 loopback. Without it the
k3s APIs (where Floci's token webhook gives any token admin access) can be
reachable from other machines unless a firewall blocks them; the launcher and
the identity helper warn about it and continue. Example daemon configuration
(merge these options into the dedicated daemon's JSON config):

```json
{
  "ip": "127.0.0.1",
  "default-network-opts": {
    "bridge": {"com.docker.network.bridge.host_binding_ipv4": "127.0.0.1"}
  }
}
```

The second option matters because EKS can publish k3s ports on its own
Docker VPC bridge, not just the default bridge or the Compose network.
Do not change the Docker daemon used for your normal workloads. For a
dedicated daemon using a nonstandard Unix socket, point `DOCKER_HOST` to that
socket. The launcher mounts the selected Unix socket into Floci so the
emulators and the CLI use the same daemon.

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

## One command: lab.py

`lab.py` runs the steps below for you, with the same isolation rules:

```sh
python3 lab.py up                      # start.py checks, wait for the health endpoints, prepare .local/, init+plan+apply each root
python3 lab.py up -v                   # the same, logging every health attempt, command and prepared file (--debug too)
python3 lab.py up -v --tf-log DEBUG    # also Terraform's own log (it can contain the lab's fixture credentials)
python3 lab.py up --providers aws,gcp  # a subset; --identities also creates the Klotse kubeconfigs (identity.py create)
eval "$(python3 lab.py env)"           # export the lab variables in this shell (PowerShell: python lab.py env --shell powershell | iex)
python3 lab.py down                    # identity.py remove, destroy oci/gcp/azure/aws, then docker compose down --volumes
```

When an emulator does not become healthy, `up` prints the last answer of each health URL and the
`docker compose logs` command for it. `up` keeps what already exists in `.local/` and stops at the first failing root (for AWS, see the 409
recovery below, then run `up --providers` for the rest). `down` leaves the emulators running when a destroy
fails or when only some providers are given. `env` prints the Terraform variables (OCI profile and override,
the CA bundle once `up` wrote it) and the cloud CLI settings for the emulators: `AWS_ENDPOINT_URL` and test keys,
`OCI_CLI_CONFIG_FILE`/`OCI_CLI_ENDPOINT`, and for gcloud a private `CLOUDSDK_CONFIG`, the container endpoint
override, project `klotse-local` and a fixture access-token file. `AZURE_CONFIG_DIR` is private too, but the
`az` CLI still needs `az cloud register` for Floci (not done here). Use these variables only in the lab shell:
they point every cloud CLI started from it at the emulators.

The manual steps follow.

## Start and provision

From this directory:

```sh
python3 start.py
```

The launcher checks the default bridge and creates and removes an empty
probe bridge to check the configuration for newly created networks; it
prints a warning when either publishes beyond 127.0.0.1, then starts the lab.
Wait for each emulator to respond at its loopback management endpoint's
`/_floci/health` (GCP: `/_floci-gcp/health`, as floci-gcp's own Docker health check uses).
Use HTTP for the health checks (including Azure). Check that
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

### AWS: recover a cluster created before a conflict response

Floci 2.5.0 can create an EKS k3s sidecar but report `409 Cluster already
exists` to Terraform on the initial apply. Do not retry blindly. Confirm the
matching sidecar belongs to this lab, that the lab EKS cluster has finished
starting, and that its Terraform address is missing from
`terraform -chdir=aws state list`. Import **only** each missing address (the
example is for cluster 1; substitute `klotse-eks-2` for cluster 2 if needed)
and review and apply a fresh plan to finish creating the node groups:

```sh
docker ps --filter label=floci_namespace=klotse793 --filter label=io.floci.service=eks
terraform -chdir=aws import 'aws_eks_cluster.lab["klotse-eks-1"]' klotse-eks-1
terraform -chdir=aws plan -out=lab.tfplan
terraform -chdir=aws apply lab.tfplan
```

### Connect to Kubernetes

After applying, let the k3s APIs finish starting; EKS may restart its
underlying k3s containers when the node groups are added. If bootstrap
credentials are not yet readable, wait for the corresponding k3s API to be
ready before rerunning the identity helper.

Check the k3s API bindings shown by Docker; 127.0.0.1 only is recommended.
The identity helper warns about a sidecar whose API is not loopback-bound and
still connects through 127.0.0.1.
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

Terraform 1.9.8 validation and mock plans cover two two-node pools per
provider. A separate Docker Engine 29.3.1 in a WSL2 Linux network namespace
with no external route was used to provision, authenticate and remove the
lab. Azure, GCP and OCI applied directly; AWS needed the import recovery
above. All eight APIs were loopback-bound. Klotse's Kubernetes client
connected and listed Nodes and Pods in both contexts for all four providers.
Client certificates were rotated, the old usernames lost lab access, and
the new users could not read Secrets. Native Klotse GUI interaction was not
tested. Run `python3 -m unittest discover -v` from this directory for the
offline guard tests.

These are k3s-backed fixtures, not EKS, AKS, GKE or OKE. Each API reported
one Ready node in the run above. The two-node pools are management metadata;
they do not guarantee that Floci launches two workers per cluster. Add-on
pods may remain Pending without a local image cache or registry.
Cloud IAM, networking, VM sizes, costs and provider node metadata are not
validated. GKE removes its bootstrap pool and retains a separate two-node
pool. OCI uses synthetic network IDs as in the emulator fixture.

Upstream: [EKS](https://github.com/floci-io/floci/blob/2.5.0/docs/services/eks.md),
[Azure TLS and Terraform](https://github.com/floci-io/floci-az/blob/0.17.0/docs/terraform.md),
[GKE](https://github.com/floci-io/floci-gcp/blob/0.13.0/docs/services/gke.md),
[OCI fixtures](https://github.com/floci-io/floci-oci/tree/0.7.0/compatibility-tests/compat-terraform),
[Docker default published IP](https://docs.docker.com/engine/network/port-publishing/#default-bridge).
