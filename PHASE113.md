# Phase 113 — explicit commit-graph reachability coverage verification

Phase 113 adds an opt-in coverage check on top of the strict structural/object verification introduced in Phase 103 and the repository-wide root selection added in Phase 109.

## Why coverage is opt-in

A commit-graph is an acceleration artifact, not the source of truth for repository reachability. A graph may legitimately become stale after a new commit or ref update while all entries already stored in the graph remain structurally correct. The historical command therefore keeps its existing meaning:

```bash
pygit commit-graph verify
```

This validates the graph image and every indexed commit/tree relationship, but does not require the graph to contain every commit currently reachable from refs.

Phase 113 adds an explicit stronger mode for callers that need to prove a freshly generated graph is complete for the current repository state:

```bash
pygit commit-graph verify --reachable
```

## Coverage semantics

`--reachable` first performs the normal strict Phase 103 verification. It then reuses the exact Phase 109 reachability collector used by `commit-graph write` and requires every selected reachable commit to appear in the installed graph.

Repository-wide mode covers all commit-ish refs plus HEAD, including packed refs, remote-tracking refs, annotated tags, detached HEAD, and shallow boundaries through the shared `rev-list` traversal.

Extra graph entries are allowed. A commit that was reachable when the graph was written may later become unreachable after a ref is deleted; retaining that valid entry does not make the acceleration file corrupt. Coverage therefore checks `reachable ⊆ indexed`, not exact set equality.

## Explicit roots

Coverage can be restricted to deliberate roots using stdin:

```bash
printf '%s\n' main topic | pygit commit-graph verify --reachable --stdin-commits
```

This uses the same explicit-root and shallow-boundary semantics as `commit-graph write --stdin-commits`. Blank-only stdin is rejected, and `--stdin-commits` without `--reachable` is a command-line error rather than a silent no-op.

## API

`verify_commit_graph_coverage(repo, revisions=None)` returns `CommitGraphCoverage(expected_count, indexed_count, extra_count)` after successful integrity and coverage verification. Missing reachable commits raise `CommitGraphError` with the missing count and first missing object ID.

## Compatibility boundary

The ordinary `commit-graph verify` contract remains unchanged. Phase 113 does not make the commit-graph authoritative for normal revision traversal, does not require exact equality with current reachability, and does not add Git's native split commit-graph format. It provides a deliberate stronger verification mode for maintenance and CI workflows.

## Regression coverage

`tests/test_phase113.py` covers stale-but-valid graphs remaining acceptable to ordinary verify, missing reachable commits failing only the explicit coverage mode, extra unreachable entries, stdin subset verification, shallow boundaries, invalid stdin combinations, and help output.
