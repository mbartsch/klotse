# Optional All Clusters overview

Design draft for [#531](https://github.com/mbartsch/klotse/issues/531).

This is a design-only proposal. It does not change the existing per-cluster workspaces or add a new data-loading path yet.

## Shared direction

The page is an optional **All clusters** home view for users who keep several contexts open. It reuses Klotse's existing theme tokens, status wording, Overview data, loading states and navigator. Every cluster loads independently, so an unreachable context cannot block the rest of the page.

Common header:

```text
ALL CLUSTERS                                      [Search clusters…] [Refresh]
14 connected · 11 healthy · 2 warnings · 1 offline       Updated 12s ago
```

Common states:

- Healthy, warning, offline, loading, stale and permission-limited are explicit labels, not colour alone.
- Metrics are optional; unavailable values show `—` with an explanation.
- Clicking a cluster opens its existing workspace.
- Refreshing a row refreshes only that cluster.
- The page remains useful with zero, one or many connected clusters.

## Option A — Operations table (recommended)

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ ALL CLUSTERS                                      [Search…] [Refresh]         │
│ 14 connected · 11 healthy · 2 warnings · 1 offline                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ HEALTH       NODES              PODS                 LAST UPDATE              │
│ 11 healthy   1,284 ready        8,420 running        12s                      │
│  2 warning       8 not ready       37 pending                                  │
│  1 offline       4 failed          12 failed                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ Cluster              State       Nodes        Pods       Version     Updated  │
│ prod-eu              ● Healthy   151/151      3,815      v1.31.4      8s     │
│ prod-us              ● Warning   145/148      3,204      v1.30.8     12s     │
│ staging              ● Healthy    24/24         612      v1.31.4     18s     │
│ trial                ○ Offline     —            —          —           2m     │
└─────────────────────────────────────────────────────────────────────────────┘
```

Best for 5–50 clusters. It gives the highest information density, works naturally with keyboard navigation and matches Klotse's existing resource tables.

Trade-off: less visual emphasis per cluster. A selected row can expand inline to show failed nodes, pending pods, resource usage and actions.

## Option B — Cluster cards

```text
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ ● prod-eu   Healthy  │  │ ● prod-us   Warning  │  │ ○ trial     Offline  │
│ v1.31.4              │  │ v1.30.8              │  │ last seen 2m ago     │
│ Nodes 151/151        │  │ Nodes 145/148        │  │ Nodes —              │
│ Pods  3,815          │  │ Pods  3,204          │  │ Pods  —              │
│ Updated 8s ago       │  │ 3 unavailable nodes  │  │ [Reconnect]          │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘
```

Best for 1–12 clusters and quick visual status recognition.

Trade-off: cards become noisy and uneven when there are many clusters or when details vary by provider. Sorting and comparison are weaker than in a table.

## Option C — Split list and detail cockpit

```text
┌────────────────────────────┬───────────────────────────────────────────────┐
│ ALL CLUSTERS               │ prod-us                         ● Warning      │
│ [Search…]                  │                                               │
│                            │ Nodes     145 ready / 148 total               │
│ ● prod-eu     Healthy      │ Pods    3,204 running · 8 pending · 3 failed   │
│ ● prod-us     Warning      │ Version                         v1.30.8       │
│ ● staging     Healthy      │ CPU 68% · Memory 74% · Updated 12s ago         │
│ ○ trial       Offline      │                                               │
│                            │ Issues                                        │
│                            │ 3 unavailable nodes                           │
│                            │ 8 pending pods                                │
│                            │                                               │
│                            │ [Open cluster] [Refresh] [View events]         │
└────────────────────────────┴───────────────────────────────────────────────┘
```

Best for operators who investigate one cluster at a time while keeping the fleet visible.

Trade-off: it consumes more horizontal space and makes the selected cluster feel like a second workspace inside the main window.

## Recommendation

Choose **Option A** for the first implementation, with Option C's expandable detail treatment added only for the selected row. This preserves Klotse's table-first language, scales to the owner's larger context lists and avoids building a second full cluster workspace.

Proposed entry points:

- Navigator: **All clusters** above the connected-cluster section.
- Command palette: **All clusters**.
- Optional Preferences setting: **Open All clusters on startup**.
- Existing cluster tabs and per-cluster Overview remain unchanged.

## Data and loading notes

- Start with the existing overview summary: node counts, pod counts, warning/failure counts, namespace count and optional metrics.
- Load each context independently through its existing `KubeService`; no shared client or cross-cluster API call.
- Render partial results as they arrive and retain visible values during refresh.
- Keep detailed metrics and issue lists lazy until a row is expanded.
- Preserve the existing auto-refresh and manual-refresh policies per workspace.

## Review decision

The implementation PR should begin only after choosing A, B or C. The recommendation is **A + expandable detail**.
