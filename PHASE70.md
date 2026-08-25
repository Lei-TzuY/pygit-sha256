# Phase 70: prune-packed plumbing

Phase 70 adds a conservative maintenance command for removing loose objects that are already stored in a fully verified pygit SHA-256 pack.

## CLI

```bash
pygit prune-packed
pygit prune-packed --dry-run
pygit prune-packed --verbose
pygit prune-packed -n -v
```

The normal command is quiet on success. `--verbose` prints every object actually removed. `--dry-run` performs the same discovery and validation but leaves object storage unchanged and prints the objects that would be removed.

## Trust boundary

A loose object is eligible for pruning only when at least one sibling `.pack` / `.idx` pair passes both strict parsers and the two files agree exactly on every indexed object's:

- full 64-hex SHA-256 object ID;
- physical pack offset;
- CRC32 of the packed entry.

Orphan `.pack` / `.idx` files, corrupt checksums, malformed entries, and mismatched pack/index pairs do not establish trust and are ignored with a warning.

Before deleting an eligible loose path, `prune-packed` also verifies the loose zlib stream, rejects trailing or incomplete compressed data, checks that the decompressed object hashes to the path's SHA-256 ID, and parses the object envelope. A malformed loose copy is reported and retained rather than silently removed.

All candidates are validated before the first unlink. Empty two-hex loose-object directories are removed only after their objects have been pruned.

## Python API

```python
from pygit import prune_packed

result = prune_packed(repo, dry_run=True)
print(result.oids)
print(result.ignored_packs)
print(result.skipped_loose)
```

`PrunePackedResult` reports the number of loose objects scanned, trusted packed object IDs, candidates, objects actually pruned, ignored pack/index files, and malformed loose objects that were deliberately kept.

## Scope

This command removes only redundant loose copies. It does not delete unreachable objects, rewrite packs, expire reflogs, or replace `fsck` / `gc`. Its job is deliberately narrower: after packing, reclaim loose-object duplicates without trusting incomplete or inconsistent storage metadata.
