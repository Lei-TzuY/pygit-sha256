# Phase 107 — full multi-pack-index pack verification

Phase 107 closes a data-integrity gap between the strict multi-pack-index structure checks added in Phase 104 and the destructive `expire` lifecycle added in Phase 105.

## Problem

Before Phase 107, `verify_multi_pack_index()` validated the MIDX image, every named `.idx` file, the union of indexed object IDs, and each selected object-to-pack offset. It required the sibling `.pack` file to exist, but did not read and validate the pack payload itself.

That distinction matters for destructive maintenance. Consider two packs containing the same object: the MIDX selects pack A and pack B is therefore redundant. If pack A's payload or trailer is corrupt while pack B remains healthy, a shallow MIDX verification can still pass and `multi-pack-index expire` can delete pack B. The repository is then left with only the damaged selected copy.

## New verification contract

`verify_multi_pack_index()` now fully verifies every MIDX-tracked pack/index pair through the Phase 102 `verify-pack` machinery before accepting its source mapping. This validates:

- strict pack-index structure and checksum;
- pack signature, version, object count, and SHA-256 trailer;
- indexed offset boundaries;
- bounded object decompression;
- per-entry CRC-32;
- canonical object envelopes and object types;
- recomputed SHA-256 object identity.

Only after those checks pass does MIDX verification compare the union of source objects and selected offsets against the MIDX image.

## Lifecycle impact

The stronger contract automatically hardens existing callers without introducing a parallel verification mode:

```bash
pygit multi-pack-index verify
pygit multi-pack-index repack
pygit multi-pack-index expire
```

In particular, `expire` now fails before deleting any redundant pack if either the selected copy or the redundant copy is corrupt. Phase 106 `repack` also inherits a full verified-MIDX preflight and post-rewrite verification.

The object-store read path is unchanged: MIDX remains an accelerator, and a normal object read can still fall back from a corrupt selected pack to a healthy redundant copy. Full validation is intentionally paid by explicit verification and maintenance operations, not every lookup.

## Regression coverage

`tests/test_phase107.py` covers:

- a corrupt MIDX-selected pack with a healthy redundant fallback;
- CRC tampering in an otherwise checksum-valid `.idx` image, proving verification reaches the pack entry rather than stopping at index structure;
- `expire` fail-before-delete behavior for a corrupt selected pack;
- `expire` refusal to mutate when the redundant pack itself is corrupt;
- installed `multi-pack-index verify` surfacing pack checksum corruption.
