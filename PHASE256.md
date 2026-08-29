# Phase 256 — `rev-list --filter-print-omitted` with object edges

Phase256 composes the line-oriented omitted-object channel with Git-style
`--objects-edge` framing without adding another traversal implementation.

## Supported forms

```text
pygit rev-list --objects-edge --filter=blob:none --filter-print-omitted \
  --missing=allow-promisor <revisions>
pygit rev-list --objects-edge --count --filter=blob:none \
  --filter-print-omitted --missing=allow-promisor <revisions>
pygit rev-list --objects-edge --filter=object:type=commit|tree|blob \
  --filter-print-omitted --missing=allow-promisor <revisions>
```

The existing `print` and `print-info` missing modes remain supported through the
same metadata-only projection.

## Git compatibility

Git documents `--objects-edge` as `--objects` plus excluded commit ids prefixed
with `-`, and `--filter-print-omitted` as the post-filter omission list prefixed
with `~`. They are separate output channels. Phase256 preserves that separation:

1. normal filtered traversal and explicit `-<oid>` edge records,
2. `~<oid>` omitted objects collected by filters that populate Git's omission set,
3. `?<oid>` missing-object diagnostics,
4. the final integer when `--count` is active.

`blob:none` populates the omission set. The Git 2.55 `object:type` filter does
not, so `object:type=... --filter-print-omitted` still emits no `~` records even
when object edges are requested.

Excluded edge commits do not expand the selected object inventory. In a
`base..tip` traversal, blobs reachable only from the excluded base closure are
therefore not reported as omitted objects. This matches the role of
`--objects-edge`: advertise a thin-pack base commit, not traverse its complete
object closure as selected output.

## SHA-256-native identity boundary

Every edge record and every materialized omitted record uses a genuine local
64-hex SHA-256 identity. A foreign promised object that has not been materialized
has no derivable local SHA-256; if such an object would have to enter the
`~` channel, pygit fails explicitly rather than padding or translating its
native SHA-1. Native foreign identities remain confined to explicit missing
metadata channels.

## Implementation

The Phase246–255 filter adapter already supports `--objects-edge`; Phase253 had
kept the higher-level omitted adapter guarded while the interaction was not yet
modelled. Phase256 removes only that obsolete guard. Omitted inventory continues
to use the existing revision parser and exclusion-closure subtraction, while the
underlying edge planner remains authoritative for `-<oid>` records and count
semantics.

NUL framing (`-z`) remains deliberately deferred because it requires an explicit
metadata representation for omitted objects rather than mixing line-oriented
`~<oid>` records into the NUL protocol.
