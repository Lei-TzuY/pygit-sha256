# Phase 113 — commit-graph reachability coverage verification

Phase 113 adds an optional coverage layer to the strict Phase 103 commit-graph verifier. Structural verification proves that the graph is internally valid and agrees with the objects it contains; coverage verification additionally proves that the graph has not fallen behind the commit roots a caller cares about.

## Commands

Repository-wide coverage uses the same all-refs-plus-`HEAD` root selection introduced by Phase 109:

```bash
pygit commit-graph verify --reachable
```

Scripts can verify only a deliberate root subset through stdin:

```bash
printf '%s\n' main topic | pygit commit-graph verify --stdin-commits
```

Plain `pygit commit-graph verify` keeps its Phase 103 semantics and does **not** require repository-wide coverage. This is important because `commit-graph write --stdin-commits` intentionally creates valid partial graphs.

## Shared traversal semantics

Coverage does not implement a second reachability algorithm. `verify_commit_graph_coverage()` reuses `collect_commit_graph_commits()`, so writes and coverage checks share:

- commit-ish resolution and annotated-tag peeling;
- packed, loose, remote-tracking, and other refs;
- detached `HEAD` handling;
- deterministic `rev-list` traversal;
- `.pygit/shallow` boundaries;
- explicit-root semantics for stdin callers.

The graph is first passed through the ordinary strict repository-aware verifier. Coverage is checked only after signature/version/generation/object/tree/parent validation succeeds.

## Missing versus extra entries

Coverage is intentionally one-way: every requested reachable commit must be indexed, but extra graph entries are allowed.

That policy distinguishes dangerous incompleteness from benign staleness. Deleting a ref can leave an otherwise valid acceleration file with commits that are no longer reachable; those entries do not make current reachable-history acceleration incomplete. Requiring exact set equality would turn harmless ref deletion into a verification failure.

When coverage is incomplete, verification fails closed with the number of missing commits and the first missing SHA-256 object ID. Verification is read-only and never rewrites or removes the installed graph.

## Compatibility boundary

The on-disk graph remains pygit's educational SHA-256 `CGPH` format. Phase 113 adds no new file fields and does not alter generation-number semantics, writer ordering, or the Phase 103 parser.

`--reachable` and verify-side `--stdin-commits` are pygit maintenance extensions; they are not claims of native Git command-line compatibility.

## Regression coverage

`tests/test_phase113.py` covers structural-only verification of an intentional subset, repository-wide missing-root detection, successful full coverage, explicit stdin coverage, extra-entry tolerance, blank stdin rejection, shallow-boundary behavior, empty repositories, read-only failure behavior, and help output.
