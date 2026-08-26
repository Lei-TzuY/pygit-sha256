# Phase 111 — multi-pack-index `write --stdin-packs`

Phase 111 adds selective multi-pack-index writes that index only pack-index basenames supplied on standard input.

## Command

```bash
printf 'pack-a.idx\npack-c.idx\n' | pygit multi-pack-index write --stdin-packs
printf 'pack-a.idx\npack-c.idx\n' | \
  pygit multi-pack-index write --stdin-packs --preferred-pack=pack-c.pack
```

Each input record is interpreted as an exact `.idx` basename in `.pygit/objects/pack`. Surrounding whitespace is not trimmed. Blank records, malformed basenames, missing indexes, and duplicate records do not add a pack to the selected set. The command fails when no valid pack remains.

Only selected packs are recorded in the resulting MIDX. Other pack/index pairs remain untouched and continue to participate in ObjectStore lookup through the existing uncovered/stale-MIDX fallback path.

## Selection semantics

Phase 111 deliberately reuses the Phase 108 writer rather than maintaining a second duplicate-object algorithm. A temporary staging directory contains only the selected `.idx` files plus lightweight pack placeholders carrying the real pack mtimes. The ordinary writer therefore applies the same rules to the reduced pack universe:

- an included explicit `--preferred-pack` wins duplicate ownership;
- otherwise the oldest selected pack is the default preferred pack;
- duplicates absent from the preferred pack use the newest selected copy;
- equal mtimes use the `.idx` basename as the deterministic tie-break.

Matching native Git's `--stdin-packs` behavior, an explicit preferred pack outside the selected set does not fail the write: the CLI emits `warning: unknown preferred pack` and falls back to the selected-set default.

## Safety

The destination MIDX is not replaced until selection, source-pair checks, staging, ordinary MIDX generation, and structural re-parse all succeed. A selected `.idx` whose sibling `.pack` is missing remains a fatal error. Empty or entirely invalid stdin leaves an existing MIDX unchanged.

The staging area copies only selected index files; pack contents are not duplicated. Pack placeholders exist solely so the shared Phase 108 writer can reuse real pack mtimes while computing duplicate ownership.

## Compatibility boundary

This phase implements the non-incremental `--stdin-packs` write path. Phase 112 adds alternate object-directory routing and composes it with this command. MIDX bitmaps, incremental MIDX chains, and `--refs-snapshot` remain separate work.

## Regression coverage

`tests/test_phase111.py` covers selective pack membership, ObjectStore fallback for excluded packs, blank/missing/malformed/duplicate stdin records, fail-without-replace behavior, missing sibling packs, Phase 108 mtime/preferred-pack composition, native-style unknown preferred-pack warnings, installed CLI behavior, verification, and help output.
