---
name: klotse-feature
description: Track features and reported problems in Klotse as GitHub issues by default; implement them only when explicitly asked to.
---

# Klotse feature tracking

Use this skill for new Klotse features and reported problems. The default is to **track**, not implement.

## Default: track, do not implement

- Every feature request or problem report gets a GitHub issue (`mbartsch/klotse`). Creating the issue is always required, even when the request reads like a question or only mentions a change.
- Do not change code, branches, or PRs unless the user explicitly says to implement, fix, or work on it (for example "implement this", "fix it", "build it now"). A request to add or track something is not an instruction to implement it.
- If a request is ambiguous about implementing, create the issue, say that no code was changed, and offer to implement.
- A request that asks for both (for example "track it and implement it") gets both: the issue, then the change.

## Creating the issue

- Reuse an existing issue when the same behavior is already tracked: search open issues first, then comment with the new details instead of duplicating.
- Give each issue a clear title, the observed behavior or the requested behavior, the source location if known (file path and function), and the platform if it is platform-specific.
- Labels: `enhancement` for features; `effort: small`, `effort: small-medium`, `effort: medium`, `effort: medium-large` or `effort: large` (see existing labels with `gh label list`). The effort label is optional; add one only when the size is clear.
- Follow the user's active parent and milestone instructions. Verify them rather than assuming: the workflow has used parent #218 and milestones 1.0.0 and 1.1.0; check `gh api repos/mbartsch/klotse/milestones` before choosing one.
- Report the issue URL in the final message.

## When implementation is explicitly requested

Only then:

- Read the repository's `AGENTS.md`, inspect the relevant source, tests, and current Git status.
- Preserve existing user changes. Do not reset, clean, overwrite, stash, or delete work you did not create.
- Branch from current `master`; never commit to `master`.
- Make the smallest change at the actual source, with focused regression tests at the layer that owns the behavior. For Qt interaction bugs, exercise the event path that really produces the behavior.
- Run focused tests and relevant checks; report failures accurately. Do not claim target-platform verification that was not performed.
- UI-change PRs must show before and after screenshots embedded in the PR body as images (`![Before](https://raw.githubusercontent.com/OWNER/REPO/<commit-sha>/...)`), not just linked. Use full HTTPS raw-content URLs pinned to the commit SHA. Never use relative or branch-name image links. A UI PR without them is not ready for review.
- Screenshots are taken on the real Windows display: the native Qt platform, never `QT_QPA_PLATFORM=offscreen`. Offscreen renders have no fonts and are not evidence.
- If the change affects contrast, icons, controls or theme styling, capture the relevant dark theme as well as the normal view and verify the changed affordance is visible there.
- Every UI change needs a before and an after of the same view: the window, dialog or popup the change touches, not just a bar. Before comes from `origin/master` (a temporary worktree is fine), after from the branch. A missing before is a gap to fix, not something to skip; if one truly cannot be taken, say why in the PR body.
- Commit the capture script beside the images (for example `docs/ux-review/<issue>/capture_*.py`), and say in the PR body which view each pair shows.
- Link the PR to its issue with `Fixes #N` and cross-reference the PR in the issue.
- Delivery: squash-merge only when every required check is green, after rechecking the PR head and checks immediately before merging. Never merge over failing or pending checks. After merge, verify the PR and issue states.

## Closing a tracked issue after implementation

- Do not close a tracked issue merely because an implementation was started or a PR was opened.
- Once the implementation is complete and its PR is merged (or the requested work is otherwise verified complete), add a comment linking the PR to the issue, then close the issue with the completed state reason.
- If the issue has a checklist, mark the delivered items complete in the same update. If no implementation PR or completion evidence exists, leave the issue open.
