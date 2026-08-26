# Phase 105 — multi-pack-index expire

Phase 105 adds lifecycle cleanup for redundant packfiles tracked by pygit's SHA-256 multi-pack-index.

## Command

```bash
pygit multi-pack-index expire
```

The command first strictly verifies the current `.pygit/objects/pack/multi-pack-index` and all tracked `.idx` / `.pack` pairs. It then identifies packs that are listed by the MIDX but have **no object entry referencing them**. Those packs are fully redundant because every object they contain has a selected copy in another tracked pack.

Eligible redundant pack families remove the `.pack`, `.idx`, `.rev`, and `.bitmap` files, after which the MIDX is rewritten from the remaining pack indexes and verified again.

## Safety boundaries

- A sibling `.keep` file protects a redundant pack from expiration.
- Corrupt MIDX data or a missing/corrupt tracked pack aborts before deletion because verification runs first.
- Pygit has no cruft-pack format, so Git's cruft-pack exception has no pygit analogue yet.
- This phase does not implement `multi-pack-index repack`, incremental MIDX chains, bitmaps, or preferred-pack selection.
- The operation only removes packs that are already redundant according to a verified MIDX; it does not rewrite object contents.

Git's native `multi-pack-index expire` uses the same core rule: delete MIDX-tracked packfiles that have no objects referenced by the MIDX, except protected packs, then rewrite the MIDX.

## Regression coverage

`tests/test_phase105.py` covers redundant-pack deletion, MIDX rewrite and verification, generated sidecar cleanup, `.keep` protection, corruption fail-before-delete behavior, no-op expiration, post-expire object readability, and installed CLI/help behavior.
