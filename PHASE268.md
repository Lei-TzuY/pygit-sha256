# Phase 268 — Ordered blob-limit omission framing

Phase268 composes the current SHA-256-native metadata-only `rev-list --in-commit-order` stack with Git's `--filter-print-omitted` channel for `blob:limit=<n>[kmg]`.

## Scope

Supported combinations now include:

```text
pygit rev-list --objects --in-commit-order \
  --filter=blob:limit=8 --filter-print-omitted HEAD

pygit rev-list --objects --in-commit-order --reverse \
  --filter=blob:limit=8 --filter-print-omitted HEAD

pygit rev-list --objects --in-commit-order --boundary --max-count=1 \
  --filter=blob:limit=8 --filter-print-omitted HEAD

pygit rev-list --objects-edge --in-commit-order \
  --filter=blob:limit=8 --filter-print-omitted A..B

pygit rev-list --objects --in-commit-order --count \
  --filter=blob:limit=8 --filter-print-omitted HEAD
```

Structured `-z` composition remains deliberately deferred.

## Git compatibility

Git's blob-limit rule keeps blobs smaller than the requested threshold and omits blobs at or above it. Suffixes `k`, `m`, and `g` use binary KiB/MiB/GiB units. The filter participates in Git's omitted-object collection, unlike `object:type`, whose Git 2.55 implementation leaves the omitted set empty.

Native SHA-256 Git was probed before implementation with a deterministic two-commit repository containing a 3-byte blob and an 8-byte blob. For `blob:limit=8`, native Git produces:

```text
ordered traversal
~<8-byte-blob-oid>
```

The same probe confirms:

- normal/reverse ordered traversal completes before the omission list;
- `--boundary` records and their snapshots precede omissions;
- `--objects-edge` keeps the leading `-<edge>` record before selected traversal and omissions;
- `--count` emits the omission list before the final filtered present-object count;
- the exact-threshold 8-byte blob is omitted, while the 3-byte blob remains present.

Phase268 includes this native SHA-256 probe in the automated suite so GitHub Actions validates the observable contract on the runner's Git version.

## Implementation

Phase268 does not add a second object walker. It reuses Phase267's ordered inventory and size helpers:

1. build the commit/snapshot-interleaved inventory;
2. compute object edges and deduplicate edge/boundary overlap;
3. preflight unresolved blobs without materialization;
4. partition local blobs into surviving entries and omitted local SHA-256 IDs using the same `< limit` rule;
5. render the surviving ordered stream through the shared Phase259–267 renderer;
6. partition captured output into traversal, missing diagnostics, and the optional final count;
7. emit `traversal -> ~omitted -> missing -> count`.

This keeps `--reverse`, boundary, object-edge, exclusion-closure, missing, and count behavior on the already-tested shared paths.

## SHA-256-native / promisor boundary

The `~` channel is a repository-visible object-id channel. Every omission emitted by Phase268 is therefore a genuine local 64-hex SHA-256.

Pygit's current persistent promisor metadata records an unresolved object's native identity and kind, but not its uncompressed blob size. A promised blob therefore cannot be safely classified for `blob:limit` without content. Phase268 keeps Phase267's metadata-only policy:

- do not single-fetch;
- do not batch-fetch;
- do not guess the blob size;
- do not pad or translate its foreign SHA-1 into a repository SHA-256;
- fail before emitting traversal, edge, boundary, omission, missing, or count output;
- leave `.pygit/promisor.json` unchanged.

A future persistent promisor-size metadata phase could relax this refusal without weakening the hash-domain boundary.

## Deferred work

- ordered `blob:limit + --filter-print-omitted + -z` mixed NUL/newline framing;
- ordered `blob:limit + --disk-usage`;
- persistent promised-blob size metadata;
- additional Git filter families such as `tree:<depth>`.
