# Session hand-off (updated 2026-11-12)

- Released: `v0.1.0`, `v0.2.0`. Everything merged since is unreleased; release only on the owner's explicit go-ahead. The file map above covers the merged features.
- Open issues (each needs `enhancement` and an `effort:` label): #61 (done, owner to close), #63, #64, #99, #115, #131. Skip #99, #63, #115. `ROADMAP.md` lists everything.
- Asked for, not done: an external URL in the usage chart's **Source…** and a "show all" view.
- Untracked `windows.zip` and the owner's `timing*.log` files are not Claude's: never stage them; add files by name.
- CodeQL flags `startswith`/prefix tests on strings as URL sanitisation: compare exactly instead.
- A background permission classifier may refuse actions without explanation: do not work around it; finish what you can and report what was blocked.
- Real-API checks use the owner's `docker-desktop` cluster through scripts in the session scratchpad; clean up what you create. Installs of metrics-server, Gateway API CRDs and Traefik there were allowed.
