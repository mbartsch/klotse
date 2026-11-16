# Contributing to Klotse

Thanks for helping. Klotse is a native Kubernetes desktop manager (Python, PySide6/Qt Widgets, the Kubernetes Python client and `kubectl`). Bug reports, feature ideas, documentation fixes and code are all welcome.

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). For security problems, do not open a public issue: follow [SECURITY.md](SECURITY.md).

## AI-assisted contributions

AI-written code is welcome. The rule is the same as for any other change: **a human must have run and tested it.**

- Before opening a PR, run the change yourself against the behaviour it claims to fix or add. Say what you ran (the screen, the cluster or the test) in the PR.
- Say in the PR when AI wrote most of the change. This is not a penalty; it tells reviewers where to look harder.
- You are responsible for every line you submit. Understand it well enough to answer review questions without re-running a model.
- Do not paste secrets, kubeconfig data, real Secret values, or cluster output that identifies real systems, into issues, PRs, prompts or test fixtures.

## Before you start

- **Search first.** Look for an existing issue. If one exists, comment there instead of opening a duplicate.
- **Open an issue for anything non-trivial.** Use the `enhancement` label for features. An `effort:` label (`small`, `small-medium`, `medium`, `medium-large` or `large`) is optional; add one if you can estimate it. Describe the observed behaviour or the requested one, the platform, and the source location if you know it.
- **Agree before large work.** For a big change, comment on the issue first so the approach can be checked before you spend the time.

## Setting up

Use the scripts at the repo root:

```sh
./run.sh --install                # macOS/Linux: creates .venv and installs requirements.txt
run.bat --install                 # Windows
.venv/bin/python -m pip install -r requirements-dev.txt
```

Run from source with `PYTHONPATH=src .venv/bin/python -m klotse`. `kubectl` must be on the PATH for drain and terminals. The unit tests need no live cluster.

Optional but recommended: install the local hooks once with `pre-commit install`. They check for private keys, bandit findings in `src` and `scripts`, merge conflicts and whitespace problems. They never rewrite files.

## Making a change

1. Branch from an up-to-date `master`. Never commit to `master` directly.
2. Keep the change to what the issue asks for. Unrelated cleanups go in a separate PR.
3. Put the behaviour in the layer that owns it: pure logic under `src/klotse/k8s/`, windows under `src/klotse/ui/`, cluster writes in `KubeService` with the `@mutating` decorator, shared table behaviour in `ResourceTable`/`TableModel`. [AGENTS.md](AGENTS.md) has the file map and the rules that must not regress (read-only mode, secret masking, the change log, shutdown). Read the parts that touch your area.
4. Add or update tests next to the existing ones in `tests/test_<area>.py`. Use fake services and synthetic objects. Never mutate a real cluster from a test, and never touch the real user settings: inject a `QSettings` on `tmp_path`.
5. Keep line endings as the file already has them. The working tree uses CRLF on Windows; do not convert whole files.

## Testing

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_<area>.py   # focused
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q                        # full suite, about 10 minutes
git diff --check
```

- Run the focused tests for your area at least. CI runs the full suite on Linux, and on Windows and macOS for release branches.
- Report what you ran and what passed. If something fails for reasons unrelated to your change, say so in the PR.
- Platform matters. If you could only check Linux, say that. Do not claim Windows, macOS or live-cluster behaviour you did not see. The project keeps a list of what is verified and what is not in [LIVE_TESTING.md](LIVE_TESTING.md).

## UI changes

Every PR that changes what the app looks like **must show a before and an after screenshot in the PR body**, taken from the app. Embed them as images (`![Before](https://raw.githubusercontent.com/...)`), so they display inline. Use full `https://raw.githubusercontent.com/...` URLs pinned to the commit SHA, not relative paths and not branch names. A PR without them is not ready for review. A clearly labelled illustration is acceptable only when it says it is one.

## Pull requests

- One PR per feature or fix, squash-merged into `master`. Please do not stack PRs on each other.
- Title: a short imperative summary. Body: what changed, why, the issue (`Fixes #123`), what you tested and how, and what you did not test.
- If your change adds or changes behaviour, update [README.md](README.md) and the relevant part of [AGENTS.md](AGENTS.md) in the same PR. Keep the wording short.
- CI must be green before merge. A maintainer may ask for changes; push to the same branch.
- Closing a PR without merging: comment why it was closed and link any replacement.

## Packaging and releases

Do not change the version in `src/klotse/_version.py`, tag releases, or edit the Windows installer's `AppId`. Releases happen through the release process described in [AGENTS.md](AGENTS.md) and are done by maintainers.

## Licence

Klotse is released under the [Apache License 2.0](LICENSE). By submitting a contribution you agree that it is licensed under the same terms.

Klotse is not affiliated with or endorsed by the Linux Foundation or the Kubernetes project. Do not use the Kubernetes logo or put the Kubernetes mark in names or artwork (see the trademark note in the README).
