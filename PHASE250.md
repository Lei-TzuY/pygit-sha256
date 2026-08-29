# Phase250: provided-root semantics and `object:type` counts

Phase250 extends the Phase249 metadata-only `rev-list --filter=object:type=...`
adapter with Git-style `--count` framing and corrects the provided-object
exemption used by the line-oriented filter.

## Problem

Phase249 correctly separated selected commits, explicit object edges, boundary
commits, snapshot objects, and promised objects, but it exempted every commit in
the selected rev-list set from `object:type` filtering. Native Git's
provided-object rule is narrower: the filter exemption applies to traversal
roots supplied by the caller (or ref tips supplied by `--all`), not every commit
reached from those roots.

That difference is visible on an ordinary three-commit history. With `HEAD` as
the positive root:

- `object:type=commit` lists all three commits;
- `object:type=tree` lists the provided HEAD commit plus the three trees;
- `object:type=blob` lists the provided HEAD commit plus the three blobs.

The older two commits are traversed commits, not provided roots, and must not
survive tree/blob filters merely because rev-list selected them.

## Native SHA-256 Git baseline

A native SHA-256 repository with deterministic `c1 <- c2 <- c3` timestamps and
three cumulative files was exercised directly.

For `HEAD`:

| filter | count |
| --- | ---: |
| `object:type=commit` | 3 |
| `object:type=tree` | 4 |
| `object:type=blob` | 4 |

The tree/blob counts are one provided tip commit plus three requested-type
objects.

For `--boundary --max-count=1 HEAD`:

| filter | count |
| --- | ---: |
| `object:type=commit` | 2 |
| `object:type=tree` | 3 |
| `object:type=blob` | 4 |

The boundary commit itself survives only the commit filter, but its snapshot
still contributes requested tree/blob objects.

For `c1..c3 --objects-edge --boundary --max-count=1`:

| filter | output/count |
| --- | --- |
| `object:type=commit` | `-c1`, then `2` |
| `object:type=tree` | `-c1`, then `3` |
| `object:type=blob` | `-c1`, then `3` |

The explicit object edge remains advertised but never contributes to the
numeric count. The explicit negative revision closure still subtracts objects
already reachable through `c1`.

A second native check with `--all` confirmed that each ref tip is a provided
root. A common ancestor that is merely reached from those tips is not exempt.

## Implementation

### Provided commit roots

Phase250 derives the provided commit roots from the same revision grammar used
by rev-list:

- positive ordinary revision tips;
- both endpoints of one symmetric `A...B` expression;
- commit tips discovered by `--all`;
- implicit `HEAD` when no explicit revision or `--all` root is present.

Output limiting still controls whether a root is actually visible. A provided
root skipped by `--skip` or excluded from the selected set does not reappear.

### Line-oriented presentation

The Phase249 presentation filter now preserves only:

- an unprefixed commit whose OID is one of the provided roots;
- an explicit `--objects-edge` record;
- any ordinary present/missing record whose known type matches the requested
  `commit`, `tree`, or `blob` type.

Therefore older traversed commits are filtered under tree/blob requests, while
provided positive roots and explicit edges retain native Git's exemptions.

### Structured count

Count mode does not count filtered text lines. It reuses the Phase232 inventory,
Phase236 boundary snapshot roots, and Phase243 edge/boundary overlap planner.

Without `--boundary`:

- present snapshot objects count only when their type matches the request;
- a top-level selected commit counts for `object:type=commit`;
- a top-level provided root also counts for tree/blob filters;
- missing promised objects never contribute to the integer.

With `--boundary`:

- selected commits count when they are requested commits or provided roots;
- boundary commits count only for `object:type=commit`;
- a boundary commit overlapping an explicit object edge is not counted;
- boundary snapshot objects count when their present type matches the request;
- path-bearing commit objects such as gitlinks remain ordinary snapshot objects.

Existing missing-object framing is preserved under `--count`: matching
`?missing` records remain visible for `print`/`print-info`, but they do not
contribute to the integer.

## Partial-clone behavior

Promisor type metadata is sufficient to classify unresolved promised objects.
Phase250 therefore performs no materialization:

- zero single-object promisor fetches;
- zero batch promisor fetches;
- no promisor-state mutation;
- no worktree/index/ref mutation.

A matching promised blob may still be emitted through the explicit missing
channel, but it is never counted as a present object.

## Identity boundary

Phase250 preserves the existing dual-domain rule:

- present objects, provided roots, edges, and boundaries use genuine local
  64-hex SHA-256 identities;
- unresolved foreign identities may appear only through the explicit missing
  channel;
- no native SHA-1 is treated as a repository-visible object id;
- no surrogate SHA-256 is invented.

## Deferred work

Phase250 deliberately leaves two Phase249 deferrals unchanged:

- `--filter=object:type=tag`, because annotated-tag traversal is not yet modeled
  by the current commit-rooted inventory;
- `-z + object:type`, because NUL structured type filtering deserves its own
  explicit presentation phase.

## Regression coverage

Focused tests cover:

- multi-commit HEAD traversal proving only the provided tip bypasses tree
  filtering;
- `--all` with two ref tips proving both roots bypass the filter while their
  common ancestor does not;
- native-compatible commit/tree/blob counts for ordinary HEAD traversal;
- boundary counts under `--max-count=1`;
- explicit object-edge plus boundary/range counts and exclusion closure;
- matching promised-blob missing records remaining visible but not counted;
- nonmatching promised blobs being filtered with zero network access;
- continued deferral of tag and NUL object:type forms.

Phase250 changes no object format, tree serialization, pack format, wire
protocol, ref/index/worktree format, or promisor identity representation.
