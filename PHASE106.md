# Phase 106 — multi-pack-index repack

Phase 106 adds verified pack consolidation for pygit's SHA-256 multi-pack-index.

## Command

```bash
pygit multi-pack-index repack
pygit multi-pack-index repack --batch-size=64m
```

`--batch-size=0` (the default) selects every non-kept pack that currently owns at least one object entry in the verified MIDX. A positive batch size follows Git's expected-size heuristic: packs are considered oldest-to-newest, each pack's expected contribution is `MIDX-referenced objects / total pack objects * pack bytes`, individually oversized packs are skipped, and eligible packs accumulate until the target is reached or all packs have been considered. Selecting fewer than two packs is a no-op.

The CLI accepts byte counts plus `k`, `m`, and `g` binary suffixes.

## Lifecycle

The operation first verifies the existing MIDX. Selected objects are then read from the exact MIDX-selected source pack through the hardened `PackReader`, written to one deterministic pack/index pair, and fully revalidated.

The rewritten MIDX deliberately prefers the newly created pack for every repacked object. Source packs are **not** deleted by `repack`; they become unreferenced MIDX packs and can be removed by the existing Phase 105 lifecycle:

```bash
pygit multi-pack-index repack --batch-size=64m
pygit multi-pack-index verify
pygit multi-pack-index expire
```

This separation keeps creation and deletion independently verifiable.

## Safety properties

- Corrupt MIDX data fails before any new pack is created.
- Selected source objects are read from their exact MIDX-selected packs, so loose-object state cannot redirect verification.
- Generated pack/index metadata, CRCs, offsets, object IDs, and pack checksums are validated before installation.
- If MIDX rewrite or post-write verification fails, the previous MIDX is restored and a newly generated pack pair is removed.
- A sibling `.keep` file excludes a pack from the repack batch.
- Existing source packs remain untouched until a later explicit `multi-pack-index expire`.

## Compatibility boundary

This implements the core non-incremental MIDX repack lifecycle and Git's batch-selection model. Pygit still has no incremental MIDX chain, MIDX bitmap, cruft-pack format, or `repack.packKeptObjects` configuration knob. Kept packs are therefore conservatively excluded from repack selection.

## Regression coverage

`tests/test_phase106.py` covers zero-size full batching, positive expected-size selection, oldest-to-newest ordering, single-pack no-op behavior, `.keep` protection, source/MIDX corruption fail-closed behavior, rewrite rollback, CLI size suffixes, installed help, and the complete `repack → verify → expire` object-readability lifecycle.
