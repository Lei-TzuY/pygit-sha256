# Phase 138 — rev-list timestamp age filters

Phase 138 adds native-style `rev-list --max-age=<timestamp>` and `--min-age=<timestamp>` commit limiting. These low-level options use Unix timestamps directly and are useful for scripts that already operate on epoch values.

## Commands

```bash
pygit rev-list --max-age=1780000000 HEAD
pygit rev-list --min-age=1780003600 HEAD
pygit rev-list --max-age=1780000000 --min-age=1780003600 HEAD
pygit rev-list --no-merges --max-age=1780000000 HEAD
pygit rev-list --max-age=1780000000 --max-count-oldest=3 HEAD
pygit rev-list --boundary --max-age=1780000000 HEAD
pygit rev-list --objects --min-age=1780003600 HEAD
```

## Native semantics

The option names are historical and can look backwards at first glance:

- `--max-age=<timestamp>` keeps commits whose committer timestamp is **strictly greater** than the supplied timestamp. It is the raw timestamp counterpart of the newer-than / `--since` family.
- `--min-age=<timestamp>` keeps commits whose committer timestamp is **strictly less** than the supplied timestamp. It is the raw timestamp counterpart of the older-than / `--until` family.

A commit exactly on either boundary is excluded. When both options are present, both predicates must hold. Negative timestamps are rejected by pygit's CLI instead of being treated as an internal sentinel.

## Selection order

pygit first computes the ordinary revision/range ancestry set, applies side and parent-count filters, then applies the timestamp predicate. Ordinary `--skip` / `--max-count` or Phase 137 `--max-count-oldest` limiting happens after age filtering. `--reverse` remains the final presentation transform.

The timestamp predicate matches Git's raw age-limit comparison, while pygit deliberately performs an exhaustive ancestry walk before filtering instead of depending on Git's history-walk pruning optimizations. This keeps results deterministic even for deliberately non-monotonic commit timestamps; performance-level traversal pruning remains outside this phase's scope.

## Metadata, boundaries, and objects

`--children` retains the pre-limit child metadata established in Phase 134. Therefore an emitted older commit can still name a child that the age predicate filtered from output.

`--boundary` computes direct parents outside the final visible age-filtered commit set. A parent excluded only by the timestamp predicate is therefore eligible for the usual `-OID` boundary record.

`--objects` expands only the tree/blob closure of commits that survive age filtering and later output limits. Filtered commits are not reintroduced through parent traversal. `--objects-edge` keeps its independent pre-limit revision-edge semantics from Phase 121.

## Python API

```python
from pygit.rev_list_age_filter import (
    rev_list_age_filter,
    rev_list_age_filter_boundary,
    rev_list_age_filter_children,
    rev_list_age_filter_named_objects,
)

entries = rev_list_age_filter(
    repo,
    ["HEAD"],
    max_age=1780000000,
    min_age=1780003600,
)
```

The helpers are read-only and compose with ranges, `--all`, side selection, parent-count filters, first-parent traversal, topo ordering, count limits, oldest-N limiting, children, boundaries, and object enumeration.

## Compatibility boundary

Human-readable date parsing (`--since`, `--after`, `--until`, `--before`, `--since-as-filter`), message/identity grep filters, path-limited history, reflog walks, pretty formatting, and native performance-level age pruning remain separate work.
