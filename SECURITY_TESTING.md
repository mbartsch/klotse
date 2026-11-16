# Security assessment and regression checks

This assessment protects the operator's credentials, local files and intended Kubernetes actions from malicious input. It is a
repeatable offline assessment, not a certification, penetration test of a live cluster, or guarantee that every defect is found.
Tracking: [security parent #490](https://github.com/mbartsch/klotse/issues/490), under
[release requirements #218](https://github.com/mbartsch/klotse/issues/218); this assessment is
[#489](https://github.com/mbartsch/klotse/issues/489). Undisclosed findings follow [SECURITY.md](SECURITY.md).

## Threats and boundaries

| Input / attacker | Protected asset and expected boundary | Evidence |
| --- | --- | --- |
| Redirecting or compromised metrics endpoint | Bearer/basic credentials stay on the configured scheme, host and effective port; no HTTPS downgrade. Direct HTTP authentication remains supported, without transport encryption. | `test_security_network.py`: real loopback HTTP/TLS, two origins, chained redirects, header capture. |
| Untrusted TLS peer | Default trust/hostname verification remains enabled. | Ephemeral local CA and certificate, trusted and untrusted client paths. No global trust-store changes. |
| Faulty/malicious HTTP responses | Request timeout and body limits fail without quoting credentials or server error bodies. | Oversize, stalled, malformed, refused and looping responses; synthetic sentinel assertions. |
| Cluster log endpoint | Diagnostics requests a server byte bound and applies a local bound before allocating the response. | `test_security_boundaries.py`: response stub rejects unbounded reads and checks cleanup. Existing diagnostics tests exercise cancellation and zip contents. |
| Separate local user / writable-directory attacker | New credential files and backups are exclusive private temporary files before writing; atomic replace avoids predictable-file symlink clobbering. | `test_cloud_clusters.py`: Linux umask 022, pre-write mode checks, symlink sentinel and failure cleanup. Windows tests do not establish ACL confidentiality. |
| Cluster metadata and accidental stale actions | Mutation guards remain enforced; UID-conflicting node deletion makes one attempt and surfaces the conflict. | Read-only/locked-action suites and explicit conflict test. This does not establish identity safety for every action. |
| Scoped plugin variables and secret-bearing objects | Variables reach the intended child only, never the parent's environment or command argv; secret fields stay out of representations/errors. | Real local child argv/env capture, plugin-environment tests, federation tests, audit and secret-display tests. |

Kubeconfig exec helpers and enabled plugins are trusted executable code. Read-only mode is a convenience guard, not an OS sandbox.
An intentional privileged node shell still depends on API-server authorization and admission. Diagnostics intentionally retain logs,
command arguments, labels and ordinary annotations; inspect bundles before sharing. Private POSIX session scripts intentionally contain
scoped variables while alive: their permission and cleanup requirements differ from logs and command argv.

## Sensitive-data inventory

| Source | Intended destinations | Places checked for unwanted disclosure |
| --- | --- | --- |
| Metrics environment/file token or basic password | Authorization header to configured origin | Redirect targets, settings (source name/path only), user-visible transport errors and logs |
| Kubeconfig inline credentials and cloud CLI output | Private merged kubeconfig, private backup, selected context's authentication helper | Temporary-file creation/write/replace failures, symlink targets and permissions |
| AWS secret key, session token and federation sign-in token | Signed AWS requests / browser sign-in handoff | Credential repr/str, error messages, displayed links |
| Kubernetes Secret data and inline env/ConfigMap values | Explicit display/edit operation; default diagnostics redacts env/ConfigMap values and never reads Secrets | Masked rows, metadata-only reads, archive objects, audit identity-only records |
| Per-cluster process variables | Explicit child environment or private POSIX session script | Parent/other-cluster environment, argv, plugin validation errors |

Each row has selected regression evidence, not exhaustive tracing of every sink. New paths handling these values must be reviewed and
given failure-path tests as well as successful-path tests. HTTP proxies and kubeconfig authentication helpers need their own trust review.

## Running and maintaining the checks

Install `requirements-dev.txt` (or the `dev` extra) and run from the repository root:

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q -m security --strict-markers
```

On POSIX, use `PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q -m security --strict-markers`.
The explicit module inventory in `tests/conftest.py` reuses existing boundary suites; new attack suites use `pytest.mark.security`.
The marker leaves full-suite discovery intact. Marked tests forbid external socket connections; local servers use ephemeral ports,
synthetic credentials, bounded shutdown and temporary test certificates. Tests substitute all Kubernetes/cloud endpoints. Child-process
tests run harmless local Python programs, never real cloud or cluster mutations. The socket guard does not sandbox child processes.

The **Security regressions** workflow runs direct pytest, without retries, on Ubuntu and Windows for ready PRs, master pushes,
weekly runs and manual dispatch. Its short-lived JUnit artifacts contain synthetic data. POSIX-only checks must run on Linux;
their Windows skips are intentional. The existing Security and CodeQL workflows remain the scanner authorities: Bandit, pip-audit,
zizmor, dependency review and security-extended CodeQL. Scanner success is separate from behavioral evidence.

## Recording assessment results

For each assessed head, record the commit, environment, exact command, pass/skip/fail counts and process exit status in its PR.
Distinguish source inspection from executed tests and platform-specific evidence. Reproduce known failures first, then land their
regression tests with the fixes; do not hide them behind permanent expected failures. Record newly discovered vulnerabilities privately
and track their remediation. A separate reviewer checks the evidence and the changes before merge; require all checks to be green.

The assessment adds no assurance about real RBAC/admission, real cloud sign-in, Okta/OIDC browser login, graphical terminal behavior,
macOS runtime behavior, packaged binaries, production cluster performance or Windows ACL confidentiality. None is exercised by this suite.
