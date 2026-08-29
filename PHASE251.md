# Phase251: `object:type` filtering for NUL-framed `rev-list`

Phase251 composes the Phase249/250 metadata-only
`--filter=object:type=(commit|tree|blob)` traversal with the Phase244/245 `-z`
object-record protocol.

## Goal

Git's current `rev-list -z` format is not a newline substitution. Each object
starts with its object id followed by NUL-delimited metadata tokens such as
`path=...`, `boundary=yes`, and `missing=yes`. Type filtering therefore needs to
happen against structured inventory entries before records are rendered; parsing
or rewriting already-rendered NUL output would lose record boundaries and would
make promised-object classification fragile.

## Semantics

Phase251 supports:

```text
pygit rev-list --objects -z --filter=object:type=commit HEAD
pygit rev-list --objects -z --filter=object:type=tree HEAD
pygit rev-list --objects -z --filter=object:type=blob HEAD
```

The same traversal can be combined with `--boundary`, selection limits, and the
existing metadata-only `--missing=allow-promisor|print|print-info` modes.

Git's provided-object exemption is preserved: explicitly provided positive
commit roots (including ref tips selected by `--all`) remain visible even when
the requested type is tree or blob. Older commits merely reached from those
roots are filtered normally.

Boundary commits are ordinary filtered commit objects. Consequently a boundary
record survives `object:type=commit` and retains `boundary=yes`; tree/blob
filters omit the boundary commit itself while still traversing and emitting
matching objects from its snapshot.

`-z + --objects-edge` and `-z + --count` remain rejected by the NUL adapter,
matching the protocol combinations already enforced before this phase.

## Partial-clone behavior

Filtering is applied to `PromisorObjectInventoryEntry` values before NUL
rendering. Promised objects therefore use persistent type metadata for the
filter decision and do not need to be materialized.

For ordinary `-z` traversal without an explicit `--missing` policy, a promised
object that does not match the requested type is filtered before the ordinary
missing-object error path. A matching promised object still raises unless the
caller opts into an explicit missing policy.

With `--missing=print-info`, a matching unresolved promise is represented by its
native identity plus explicit metadata, for example:

```text
<native-sha1> NUL missing=yes NUL path=f.txt NUL type=blob NUL
```

## SHA-256 identity boundary

Present commits, trees, and blobs continue to use genuine local 64-hex SHA-256
object ids. Foreign native SHA-1 identities are never padded, translated, or
presented as repository-visible SHA-256 ids; they may appear only for unresolved
promises in the explicit `missing=yes` record channel.

## Regression coverage

Focused tests verify:

- tree filtering preserves only the provided HEAD commit while filtering older
  reachable commits;
- NUL tree records retain matching snapshot trees;
- tree filtering removes boundary commit records while preserving boundary
  snapshot trees;
- commit filtering retains `boundary=yes` framing;
- promised blobs can be filtered out with zero single/batch fetches and no
  promisor-state mutation;
- blob `print-info` records keep the 40-hex native identity separated from the
  64-hex local SHA-256 provided root.

`object:type=tag` remains intentionally deferred until annotated-tag roots and
traversal are represented by the inventory planner.
