# Phase 239 — `rev-list --boundary --missing=print-info`

Phase 239 composes the metadata-only promisor inventory from Phase232, the
boundary snapshot-root planner from Phases235–236, and the dual-hash
`--missing=print-info` presentation introduced in Phase237.

The supported form is now:

```text
pygit rev-list --objects --boundary --missing=print-info <revisions>
```

including `--skip`, `--max-count`, `--reverse`, `--topo-order`,
`--first-parent`, `--all`, and `--no-object-names`.

## Output domains

Boundary and selected commit records remain repository-visible local SHA-256:

```text
<64-hex selected commit>
-<64-hex boundary commit>
```

Present tree/blob records also remain local SHA-256. An unresolved expected
promisor object has no correct local SHA-256 until its content is materialized,
so it appears only in Git's missing-object channel:

```text
?<40-hex native SHA-1> path=<encoded-path> type=<kind>
```

The `?` prefix is therefore an explicit native transport-identity channel, not
a repository object-id namespace. Phase239 never invents a surrogate SHA-256.

## Boundary snapshot closure

`rev-list --objects --boundary` is not just commit presentation. A boundary
commit can contribute its own tree/blob snapshot to the object stream. Phase236
already models that rule, including boundaries induced by `--max-count`.
Phase239 reuses the same final selected/boundary commit stream as the ordered
snapshot-root plan for `print-info`.

For example, with a linear history:

```text
c1 <- c2 <- c3 (HEAD)
```

`--max-count=1 --boundary` emits the selected `c3`, boundary `-c2`, then the
snapshot objects first reached from `c3` followed by those first reached from
`c2`. If either snapshot contains an unresolved expected promise, its inventory
record is rendered as `?native_oid path=... type=...` without fetching it.

`--reverse` reverses the final boundary/commit presentation and the snapshot
root order together, preserving Phase236's native-Git-compatible ordering.

## Explicit exclusions

Revision exclusions remain authoritative. For a range such as `c2..c3`, `c2`
can still be displayed as the boundary commit while the complete object closure
explicitly excluded by `c2` is subtracted from the inventory. Phase239 therefore
does not reintroduce `c2`'s tree or missing-promisor records merely because the
boundary marker is displayed.

## Top-level commit filtering

The promisor inventory contains selected commit records plus snapshot objects.
When boundary presentation owns commit framing, Phase239 suppresses only
`type=commit` entries whose `path` is `None`. Path-bearing commit objects (for
example gitlinks/submodule entries) remain part of snapshot traversal and are
not accidentally discarded.

## No network activity

This remains a metadata-only operation:

- no Phase213 single-object demand fetch;
- no Phase214 batch demand fetch;
- no promisor-state mutation;
- no worktree/index/ref mutation.

Unexpected repository corruption is still an error; only objects explicitly
represented as expected promisor omissions receive missing-object framing.

## Deliberate limits

Phase239 still rejects:

- `--objects-edge --missing=print-info`;
- `--count --missing=print-info`;
- plain `--missing=print`.

`--count` remains deferred because its treatment of emitted missing records
must be verified separately against native Git rather than inferred from the
line-oriented documentation.

## Verification

`tests/test_phase239.py` uses a real foreign `blob:none` three-commit history
with three distinct snapshots. It covers:

- limit-induced boundary snapshot missing records;
- reverse boundary/snapshot ordering;
- `--skip + --max-count` boundary selection;
- explicit exclusion closure subtraction;
- `--no-object-names` with intact `path=` / `type=` missing metadata;
- unchanged promisor state;
- zero single/batch network fetching.
