# Phase279 — annotated-tag object filtering with `rev-list --disk-usage`

Phase279 composes Phase274/277 annotated-tag-aware `object:type` selection with the existing local object disk-size accounting primitive.

## Scope

Supported non-ordered combinations now include:

```text
rev-list --objects --filter=object:type=tag --disk-usage <tag>
rev-list --objects --filter=object:type=tag --filter-provided-objects --disk-usage <tag>
rev-list --objects --filter=object:type=commit|tree|blob --disk-usage <annotated-tag>
rev-list --objects --filter=object:type=tag --count --disk-usage <tag>
rev-list --objects --filter=object:type=tag --disk-usage=human <tag>
rev-list --objects --boundary ... --filter=object:type=... --disk-usage <tag>
rev-list --objects-edge --filter=object:type=tag --disk-usage A..<tag>
```

The adapter does not add another object walker. It captures the already-validated Phase274 annotated-tag-aware line selection and sums only genuine local SHA-256 identities with `cat_file.object_disk_size()`.

## Native Git semantics

A deterministic SHA-256 probe establishes the key provided-object behavior:

- `object:type=tag v1` sizes the explicitly provided peeled commit plus the annotated tag object.
- `--filter-provided-objects` removes the peeled-commit exemption, so only the matching tag is sized.
- `object:type=commit v1` sizes the matching commit ancestry plus the provided annotated tag.
- `object:type=tree|blob v1` keeps the provided commit and tag in addition to matching snapshot objects.
- `--boundary --max-count=1` sizes selected boundary objects that survive the filter.
- `--objects-edge` keeps the leading `-<oid>` presentation record but does not size the excluded edge.
- `--count` emits `0` before the disk-usage value.
- `--disk-usage=human` preserves Git's human-readable byte formatting.
- Git 2.55 rejects `-z + --disk-usage`; Phase279 retains that rejection.

CI includes a Git 2.55+ native SHA-256 regression for these contracts.

## Missing/promisor behavior

Disk accounting never assigns a byte size to a missing native identity.

For ordinary missing policy, Phase279 performs an internal metadata-only `print-info` selection solely to detect a surviving missing object before any user-visible output, then raises the usual missing-object error.

With explicit `--missing=allow-promisor|print|print-info`, the existing missing policy remains authoritative. Missing diagnostic records may be preserved, but only present local SHA-256 OIDs are passed to `object_disk_size()`.

A blob-less partial-clone regression verifies that `object:type=tag` filters the promised blob without either single-object or batch materialization and leaves promisor state unchanged.

## SHA-256-native boundary

Every identity sent to local disk accounting is a real 64-hex SHA-256 object already present in the local repository. Annotated tags are ordinary local `TagObject` instances.

Remote-native 40-hex SHA-1 may appear only in explicit missing diagnostics. It is never padded, translated, synthesized into a local object ID, or passed to disk accounting.

## Coordination

- Base: Phase277 / PR #255 exact-green head `c3b7a89735d6fe6fc3ba6c0c4485a0bf9ff17fc3`.
- Phase278 / PR #256 is the independent promisor-size/blob-limit line and is not modified.
- Phase273 / PR #251 remains the independent ordered annotated-tag line.
- Phase279 was rechecked free immediately before branch creation.
- No PR is merged by this phase.
