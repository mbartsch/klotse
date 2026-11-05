# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue: on the repository page open **Security** ->
**Report a vulnerability** (GitHub private vulnerability reporting). If you do not see that option, open an issue titled
"Security contact request" **without any details** and a way to reach you will be arranged.

Include what you found, how to reproduce it, and the Klotse version (Help -> About Klotse). You can expect an answer
within a few days; this is a small project maintained by one person.

## Supported versions

Only the latest release receives fixes.

## What Klotse trusts, and what it does not

- Klotse runs with **your** permissions: your kubeconfig, your `kubectl` and cloud-login helpers, and your cloud
  credentials. It has no server component and sends nothing anywhere except to the clusters and the AWS endpoints you use.
- **Plugins are trusted code** that runs inside Klotse with the same permissions. They are off until you enable them; enable
  only plugins you trust. The bundled AWS Plugin uses AWS credentials only to call EC2 and STS (and, if you turn it on, the
  console sign-in).
- **A cluster is not trusted input.** Names, labels and other data from a cluster are displayed and, where Klotse builds an
  address or a command from them, validated first (for example the AWS region from a node label). A cluster that makes Klotse
  misbehave, leak a credential or run something unexpected is a vulnerability: please report it.
- Secret values are masked in the UI until you ask to decode them, and are never logged.

## How the project is checked

Every pull request and a weekly schedule run: CodeQL, bandit (static analysis), pip-audit (known vulnerable dependencies),
zizmor (workflow security) and dependency review; Dependabot keeps dependencies and the pinned GitHub Actions current. GitHub
secret scanning with push protection is on for the repository. Release builds are made by GitHub Actions from a tagged commit.
