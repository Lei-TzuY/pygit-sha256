# Phase271: ordered blob-limit NUL traversal

Phase271 removes the last plain-output framing gap in the current ordered
`rev-list` filter stack: `--filter=blob:limit=<n>[kmg]` can now be combined with
`--in-commit-order -z` without requiring `--filter-print-omitted`.

## Scope

Supported combinations now include:

```text
rev-list --objects --in-commit-order -z --filter=blob:limit=<n>[kmg] ...
rev-list --objects --in-commit-order --reverse -z --filter=blob:limit=<n>[kmg] ...
rev-list --objects --in-commit-order --boundary -z --filter=blob:limit=<n>[kmg] ...
rev-list --objects --in-commit-order -z --filter=blob:limit=<n>[kmg] --count ...
rev-list --objects --in-commit-order -z --filter=blob:limit=<n>[kmg] --filter-provided-objects ...
```

Phase269 already owns the separate
`blob:limit + --filter-print-omitted + -z` presentation path. Phase271 does not
merge or duplicate that omission channel; it only removes the plain-path NUL
deferral.

`-z + --objects-edge` remains rejected by the shared structured-output parser.
Ordered `--disk-usage` also remains a separate future composition.

## Implementation

Phase267 already builds a structured commit/snapshot-interleaved inventory and
applies `blob:limit` membership before rendering. Its projection deliberately
removes only the filter arguments, so `-z`, `--boundary`, `--count`, revision
limits, ordering flags, missing policies, and `--no-object-names` naturally flow
into the shared ordered parser.

Phase271 therefore needs no second walker and no new wire protocol. It removes
the obsolete explicit `-z` rejection from
`rev_list_in_commit_order_blob_limit_cli.py` and delegates the surviving
inventory to the Phase263/270 ordered renderer.

The result preserves:

- first-seen commit/snapshot ordering;
- `--reverse` first-seen positions;
- structured `path=` fields;
- structured `boundary=yes` metadata;
- Phase270 `-z + --count` behavior, where normal present records are suppressed
  and the final count is a newline-terminated decimal integer;
- the existing `--filter-provided-objects` behavior (no visible effect for this
  blob-only filter on commit roots).

## Native Git compatibility

The size rule remains the Phase267/native Git rule: a `blob:limit=N` filter
keeps blobs whose uncompressed size is strictly less than `N`; blobs at or above
the threshold are filtered. `k`, `m`, and `g` remain binary units.

Git 2.55's structured `-z` protocol emits object IDs and optional metadata as
NUL-terminated fields. A dedicated SHA-256 native-Git regression verifies the
plain ordered blob-limit composition byte-for-byte for:

- normal traversal;
- reverse traversal;
- boundary traversal with `boundary=yes`;
- `-z + --count`, which remains a plain newline integer.

The deterministic fixture contains a 3-byte blob and an 8-byte blob with
`blob:limit=8`. Native Git emits the 3-byte blob and filters the exact-threshold
8-byte blob. The expected present-object count is `5`.

## SHA-256 / promisor boundary

Present repository-visible object identities remain genuine local 64-hex
SHA-256 values.

`blob:limit` requires the uncompressed blob size. Pygit's persistent promisor
metadata records unresolved native identity and object kind, but not that size.
An unresolved promised blob therefore remains unclassifiable without content.
Phase271 preserves the existing strict rule:

- fail before any NUL/count/missing output;
- do not single-fetch or batch-fetch merely to classify the filter;
- do not guess a size;
- do not pad or translate the foreign/native SHA-1 into a local SHA-256 slot;
- do not mutate promisor state.

## Regression coverage

Phase271 tests cover:

- normal NUL commit/snapshot first-seen order;
- `path=` metadata for surviving blobs;
- reverse traversal;
- structured boundaries;
- native-compatible NUL count framing;
- `--filter-provided-objects` composition;
- unresolved promised-blob fail-before-output with both fetch paths forbidden;
- unchanged promisor state;
- retained `-z + --objects-edge` incompatibility;
- native Git 2.55 SHA-256 normal/reverse/boundary/count byte-level behavior.
