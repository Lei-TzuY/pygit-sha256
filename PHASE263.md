# Phase263: `rev-list --in-commit-order -z`

Phase263 composes the metadata-only `rev-list --objects --in-commit-order`
traversal with Git's modern structured `-z` object metadata protocol.

## What changed

`pygit rev-list` now accepts combinations such as:

```text
pygit rev-list --objects --in-commit-order -z HEAD
pygit rev-list --objects --in-commit-order --reverse -z HEAD
pygit rev-list --objects --in-commit-order --boundary --max-count=1 -z HEAD
pygit rev-list --objects --in-commit-order -z --missing=print-info HEAD
```

The ordered inventory from Phases259-262 remains authoritative. Phase263 changes
only presentation: each ordered inventory entry is emitted as a structured NUL
record instead of line-oriented `OID [path]` text.

## Git compatibility

Current Git 2.55 documentation defines `-z` output as:

```text
<OID> NUL [<token>=<value> NUL]...
```

with examples including `path=<path>`, `boundary=yes`, and `missing=yes`. The
same documentation says that `-z` is compatible only with the `--objects`,
`--boundary`, and `--missing` output options. `--in-commit-order` is an object
traversal ordering mode rather than an additional output channel, so Phase263
composes it with `--objects -z` while retaining the existing `--objects-edge`
and `--count` rejection under `-z`.

The available local native Git is 2.47.3. It confirms the commit/snapshot
ordering used by `--in-commit-order`, including reverse and boundary behavior,
but predates the modern documented NUL metadata protocol and therefore still
prints legacy line framing even when passed `-z`. For Phase263 the latest Git
2.55 documentation and pygit's already-established Phase244 structured NUL
contract are authoritative for framing; the native 2.47.3 probe remains useful
for ordering only.

## Ordering contract

For ordinary traversal, every selected commit is emitted immediately before the
first tree/blob records reached from its snapshot. One global deduplication set
still controls first-seen object placement.

With `--reverse`, reversing the commit stream changes which snapshot first sees
shared trees/blobs exactly as in the Phase259 line-oriented traversal.

With `--boundary`, the boundary commit remains in that same ordered stream and
is encoded as:

```text
<local-sha256> NUL boundary=yes NUL
```

rather than the line-oriented `-<oid>` prefix. Boundary snapshot objects follow
that boundary frame at their existing first-seen positions.

## Missing/promisor records

The Phase244 NUL renderer is reused rather than reimplemented.

- `--missing=allow-promisor` omits unresolved promises.
- `--missing=print` emits `<native-oid> NUL missing=yes NUL`.
- `--missing=print-info` additionally emits stable metadata such as
  `path=<path>` and `type=blob`.
- ordinary traversal detects unresolved promises before emitting any bytes and
  fails with the existing explicit missing-policy diagnostic.

No single-object or batch materialization is performed merely to establish
ordered NUL output.

## SHA-256-native boundary

Present commits, trees, blobs, and boundary frames always use genuine local
64-hex SHA-256 object identities. An unresolved foreign promise may expose its
40-hex upstream/native SHA-1 only inside a record explicitly marked
`missing=yes`.

Phase263 does not pad, translate, or synthesize SHA-256 values from foreign
SHA-1 identities and does not mutate promisor state.

## Deliberately still deferred

Phase263 keeps these existing compatibility boundaries:

- `-z + --objects-edge` remains rejected because current Git documents `-z` as
  compatible only with the `--objects`, `--boundary`, and `--missing` output
  options.
- `-z + --count` remains rejected.
- `--in-commit-order + --filter` / omitted-object framing remains a separate
  composition problem.
- `--in-commit-order + --disk-usage` remains deferred.

## Verification

Focused Phase263 regressions cover:

- ordinary commit/snapshot NUL interleaving with `path=` metadata;
- reverse first-seen object placement;
- inline `boundary=yes` framing in normal and reverse order;
- metadata-only `missing=yes` / `path=` / `type=` records;
- strict local SHA-256 versus native SHA-1 identity separation;
- zero-fetch `allow-promisor` traversal and unchanged promisor state;
- ordinary partial-clone failure before output; and
- preservation of `-z + --objects-edge` and `-z + --count` guards.
