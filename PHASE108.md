# Phase 108 — multi-pack-index preferred-pack selection

Phase 108 aligns duplicate-object selection in pygit's SHA-256 multi-pack-index writer with current Git behavior.

## Command

```bash
pygit multi-pack-index write
pygit multi-pack-index write --preferred-pack=<pack>
```

`--preferred-pack` accepts a pack basename with `.pack`, `.idx`, or no suffix. The named pack must exist in the current pack directory and contain at least one object.

## Duplicate-object selection

A MIDX stores only one location for each object ID even when that object appears in multiple packs.

- If an explicit preferred pack contains the object, that copy wins.
- Without an explicit preferred pack, the oldest pack by packfile mtime is treated as the default preferred pack.
- For duplicate objects not present in the preferred pack, the newest packfile mtime wins.
- Equal mtimes use the `.idx` basename as a deterministic final tie-break.

This replaces the earlier Phase 104 implementation that always selected the lexicographically first pack, which was deterministic but not Git-compatible.

## Safety and compatibility

The writer still validates every source `.idx`, requires every sibling `.pack`, keeps pack names sorted in the MIDX image, and atomically replaces `multi-pack-index` only after selection succeeds. A missing or empty explicit preferred pack fails before replacing an existing MIDX.

The on-disk MIDX format is unchanged. ObjectStore lookup, Phase 107 full verification, Phase 105 expire, and Phase 106 repack continue to consume the same structure; only duplicate-copy ownership chosen at write time changes. Phase 111 reuses these exact selection rules when `write --stdin-packs` narrows the pack universe.

Pygit still does not implement MIDX bitmaps, incremental MIDX chains, `--refs-snapshot`, or alternate-object-directory routing.

## Regression coverage

`tests/test_phase108.py` covers default oldest-pack preference, explicit preferred-pack override, newest-mtime selection among non-preferred duplicates, missing/empty preferred-pack rejection without MIDX replacement, and installed CLI/help behavior. Phase 104's deterministic duplicate regression now explicitly pins equal mtimes so it tests the final basename tie-break rather than the superseded lexicographic-first policy.
