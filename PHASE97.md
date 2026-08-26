# Phase 97 — strict `count-objects` diagnostics

Phase 97 replaces the legacy path-counting implementation behind `pygit count-objects` with storage-aware diagnostics that distinguish valid objects from garbage and expose the verbose fields used by modern Git.

## What is counted

Loose objects are accepted only from the canonical SHA-256 layout:

```text
.pygit/objects/HH/<62 hexadecimal characters>
```

A candidate must also pass zlib decompression, SHA-256 identity verification, object-envelope validation, and typed payload parsing. A malformed or hash-mismatched file is reported as garbage instead of inflating the loose-object count.

Pack statistics count only validated `.idx` / `.pack` pairs. The existing strict `PackReader` validation is reused so malformed or orphaned pack artifacts cannot contribute to `in-pack`, `packs`, or `size-pack`.

## Verbose output

`pygit count-objects -v` now reports:

```text
count: <loose objects>
size: <loose KiB>
in-pack: <objects in valid packs>
packs: <valid pack pairs>
size-pack: <pack + index KiB>
prune-packable: <loose objects duplicated in packs>
garbage: <invalid/orphan storage files>
size-garbage: <garbage KiB>
alternate: <absolute object database path>   # when configured
```

`prune-packable` is the intersection of validated loose OIDs and validated packed OIDs. Standard `.keep`, `.bitmap`, and `.rev` sidecars attached to a valid pack are metadata rather than garbage.

The `objects/info/alternates` file is read without traversing alternate databases; relative entries are normalized to absolute paths for reporting.

## Human-readable sizes

`-H` / `--human-readable` changes only presentation. The scanner retains exact byte counts internally and the CLI formats them in binary units (`bytes`, `KiB`, `MiB`, ...). The existing non-human-readable output remains KiB-based.

## Compatibility and safety

The operation is read-only. It never deletes garbage or pruneable loose objects. Existing `Repository.count_objects()` keys (`count`, `size_kb`, `in_pack`, `packs`, `size_pack_kb`) remain available; Phase 97 adds exact byte counters plus `prune_packable`, `garbage`, `size_garbage_*`, and `alternates`.

Regression coverage includes valid loose objects, corrupt/hash-mismatched candidates, validated packs, loose/packed duplicates, malformed and orphan pack files, recognized pack sidecars, alternates, verbose output, and human-readable formatting.
