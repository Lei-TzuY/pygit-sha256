# Phase 64: pack import plumbing

Phase 64 adds low-level import operations for pygit's native SHA-256 pack format. It complements the existing `repack`, `verify-pack`, `count-objects`, and `fsck` commands with the inverse operations needed to reconstruct an index or materialize packed objects as loose storage.

## Commands

```bash
pygit index-pack archive.pack
pygit index-pack --force archive.pack
pygit index-pack --verbose archive.pack

pygit unpack-objects archive.pack
pygit unpack-objects --dry-run archive.pack
pygit unpack-objects --strict archive.pack
```

`index-pack` validates the entire pack and creates a sibling `.idx` using the project's existing version-2 fan-out format. It refuses to overwrite an existing index unless `--force` is explicit.

`unpack-objects` validates the complete pack before writing anything, then materializes the exact packed object envelopes as zlib-compressed loose objects under `.pygit/objects/`. Existing loose objects are verified and preserved. `--dry-run` performs validation only; `--strict` additionally runs repository `fsck` after a real unpack.

## Validation model

The standalone parser does not trust or require an existing `.idx`. It walks the pack stream sequentially and checks the `PACK` signature/version/count, bounded size varints, supported commit/tree/blob/tag type IDs, complete zlib streams, pack/envelope sizes, concrete object deserialization, SHA-256 object identity, duplicate IDs, CRC-32 values, exact stream termination, and the final SHA-256 pack trailer.

A damaged or truncated pack therefore fails before `unpack-objects` starts creating loose objects.

## Python API

```python
from pathlib import Path
from pygit import index_pack, parse_pack, unpack_objects

parsed = parse_pack(Path("archive.pack"))
for entry in parsed.entries:
    print(entry.oid, entry.type_name, entry.offset)

index_result = index_pack(Path("archive.pack"))
unpack_result = unpack_objects(repo, Path("archive.pack"))
```

Structured APIs include `PackEntry`, `ParsedPack`, `IndexPackResult`, `UnpackResult`, `parse_pack_bytes()`, `parse_pack()`, `build_index_bytes()`, `index_pack()`, and `unpack_objects()`.

## Storage safety and scope

`index-pack` writes through a temporary file followed by `os.replace()`. `unpack-objects` validates the complete input and all pre-existing loose objects before the first new object is written; new loose objects use temporary files and atomic replacement.

The implementation deliberately targets pygit's own educational SHA-256 pack schema. It does not claim compatibility with native Git delta-compressed packfiles or Git's SHA-1 pack-index layout.
