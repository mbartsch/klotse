## What changed

<!-- A few sentences: what the change does and why. -->

## Issue

<!-- Fixes #123, or "Relates to #123". Every non-trivial PR should link an issue. -->

## How it was tested

<!-- What you ran and what you saw. Name the platform (Windows, macOS, Linux) and whether you ran against a real cluster. -->

- [ ] Focused tests pass: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_<area>.py`
- [ ] I ran the app and checked the change myself (AI-written code is fine when a human has done this)
- [ ] `git diff --check` is clean

**Not tested:** <!-- Platforms, clusters or cases you did not check. Write "none" only if that is true. -->

## Screenshots (UI changes only)

<!-- Required for any change to what the app looks like. Embed before and after as images, pinned to the commit SHA:
![Before](https://raw.githubusercontent.com/mbartsch/klotse/<commit-sha>/path/to/before.png)
![After](https://raw.githubusercontent.com/mbartsch/klotse/<commit-sha>/path/to/after.png)
Delete this section if the change has no UI effect. -->

## Checklist

- [ ] Work is on a branch from current `master`, one feature or fix per PR
- [ ] Behaviour changes are reflected in `README.md` and the relevant part of `AGENTS.md`
- [ ] Cluster writes use `@mutating`, and new UI entry points call `_writable` (if relevant)
- [ ] No secrets, kubeconfig data, real Secret values or identifying cluster output in the code, tests, screenshots or this description
- [ ] If an AI tool wrote most of this change, I say so here: <!-- yes / no -->
