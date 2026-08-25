# Phase 73: safe repack maintenance

Phase 73 adds `pygit repack` to turn the existing pack producer, strict pack/index validators, and pruning primitives into a conservative storage-maintenance workflow.

## CLI

```bash
pygit repack
pygit repack --verbose
pygit repack -d
pygit repack -a -d
pygit repack -a -d --dry-run --verbose
```

`repack` without `-a` selects reachable objects that do not already have a copy in a strictly validated pack/index pair. It creates a new pack but does **not** delete loose objects or existing packs.

`-a` / `--all` selects the complete currently reachable object closure, including objects that are already packed. This is the mode used to consolidate multiple packs.

`-d` / `--delete-redundant` enables destructive cleanup after the new pack has been installed and revalidated. An old pack/index pair is removed only if every object it contains is present in the newly installed pack. Verified loose duplicates are then removed through the existing `prune-packed` logic.

`--dry-run` performs integrity checks and computes the selection/cleanup plan without creating or deleting repository storage.

## Safety model

Repacking fails closed. A full `fsck` is completed before the first write, so corrupt loose objects, malformed pack/index pairs, broken connectivity, or orphan pack metadata abort the operation.

Every old pack/index pair is then parsed again with the strict Phase 64/69 validators. Pack and index OID/offset/CRC metadata must agree exactly.

A new pack is generated inside a temporary directory under `.pygit/objects/pack`, validated there, and only then installed. The installed pair is validated again before any old storage can be deleted.

For `-d`, each old pair is revalidated immediately before deletion. The `.idx` file is removed before its `.pack` file; if deletion is interrupted, the safe failure mode is an ignored orphan pack rather than an index that points at a missing pack.

An old pack containing even one object outside the new pack is retained. In particular, unreachable or recovery-only packed objects are never silently discarded just because they are absent from the current reachable closure. This also preserves the safety boundary introduced by Phase 72 reflog expiry: reflog-only history is not silently destroyed by pack consolidation.

## Incremental versus full compaction

A normal `repack -d` is incremental. Suppose an old pack already contains history A and new loose history B is added. The command creates a pack for B, keeps the old A pack, and removes verified loose duplicates.

`repack -a -d` instead writes the full reachable A+B closure into one new pack. Only after that pair is verified can the fully subsumed old A/B packs be removed.

## Python API

```python
from pygit import repack, RepackResult

result = repack(repo, all_objects=True, delete_redundant=True)
print(result.pack_path)
print(result.removed_packs)
print(result.pruned_loose)
```

`RepackResult` also exposes the reachable count, already-packed count, selected OIDs, new pack hash/path, dry-run cleanup candidates, and actual cleanup totals.

## Compatibility boundary

This remains pygit's educational SHA-256 pack format: non-delta objects, 64-character ASCII SHA-256 IDs in `.idx`, and 32-bit offsets. Phase 73 does not claim native Git pack/index compatibility, delta compression, bitmaps, multi-pack-index support, or background `gc` scheduling.
