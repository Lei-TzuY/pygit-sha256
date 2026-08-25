# Phase 74 — Safe `gc` orchestration

Phase 74 replaces the installed legacy `pygit gc` path with a coordinated maintenance pipeline built from the hardened primitives added in Phases 71–73.

## Why this phase exists

The older `Repository.gc()` implementation treated every object reported as dangling by the legacy fsck path as immediately disposable. It did not honor reflog recovery history, object-age grace periods, verified pack redundancy, or the newer storage-integrity checks.

The installed command now uses `pygit.gc.garbage_collect()` instead. The legacy method remains untouched for source compatibility, but `python -m pygit gc` and the installed `pygit gc` entrypoint no longer dispatch through it.

## Pipeline

A normal pass freezes one policy timestamp and performs:

1. full `fsck` health check;
2. dry-run/preflight of the requested repack, reflog-expiry, and prune phases;
3. verified full reachable-object `repack -a -d`;
4. atomic `reflog expire --all` using the configured recovery windows;
5. grace-aware loose unreachable-object `prune`;
6. final full `fsck` validation.

All requested phases are planned before the first real mutation. A malformed reflog, corrupt pack/index pair, unhealthy current graph, or missing historical root needed by this pass is therefore rejected before repack changes storage.

Repack runs before reflog expiry, so a pack-write failure cannot shorten recovery metadata.

## Same-cycle recovery retention

A reflog record selected for expiry may be the last name for an old commit graph. Removing that record and pruning its old objects in the same command would collapse the recovery window abruptly.

Phase 74 therefore collects every non-zero old/new OID from reflog records expired in this invocation and passes those OIDs to `prune` as extra retention roots. The reflog record may disappear now, but its historical commit/tree/blob closure survives the current gc pass. A later gc may reclaim it once no recovery root remains and the normal object-age policy permits deletion.

This rule applies even when explicit `--prune=now` and immediate reflog cutoffs are requested.

## Default retention policy

- reflog general expiry: 90 days;
- unreachable reflog expiry: 30 days;
- loose unreachable-object prune grace: 2 weeks.

The three cutoffs are independent. Expiring metadata never makes its historical object graph disposable during the same gc invocation.

## CLI

```text
pygit gc
pygit gc --dry-run --verbose
pygit gc --prune=now
pygit gc --no-prune
pygit gc --no-reflog-expire
pygit gc --reflog-expire=120.days.ago
pygit gc --reflog-expire-unreachable=45.days.ago
```

`WHEN` accepts the same deterministic expiry subset as `prune`: `now`, `never`, `default`, an epoch timestamp, or `N.minutes.ago`, `N.hours.ago`, `N.days.ago`, and `N.weeks.ago`.

`--no-prune` preserves the historical project CLI contract as a **no-write compatibility mode**. It validates and reports the complete maintenance plan but does not install packs, rewrite reflogs, or remove objects. It is effectively a legacy spelling of `--dry-run`.

For programmatic stage-level control, `garbage_collect(..., prune_objects=False)` can still run verified repack and reflog expiry while skipping unreachable loose-object pruning.

`--no-reflog-expire` preserves every existing recovery record; the prune phase continues to treat all reflog old/new OIDs as retention roots.

## Dry-run semantics

`--dry-run` executes the same health, pack, reflog parsing, and loose-object validation paths without writing repository state. No pack is installed, no reflog is rewritten, and no loose object is removed.

Dry-run also computes the OIDs whose reflog records *would* expire and supplies them as explicit prune roots. This intentionally matches the real pass's same-cycle recovery boundary rather than showing objects that the real invocation would refuse to delete.

The installed command always emits a compact summary; `--verbose` adds per-stage OIDs, pack removals, reflog records, and the freshly expired roots preserved for this pass.

## Python API

```python
from pygit import garbage_collect

result = garbage_collect(repo)
print(result.repack.object_count)
print(result.reflog.expired if result.reflog else 0)
print(result.prune.pruned if result.prune else 0)
print(result.preserved_expired_roots)
```

`GarbageCollectResult` exposes the repack, reflog-expiry, and prune sub-results, preflight/final reachable counts, same-cycle preserved recovery roots, and dry-run state.

## Safety properties

- full repository integrity is required before maintenance;
- every requested destructive phase is dry-planned before the first mutation;
- repack uses strict pack/index validation and verified redundant-copy cleanup;
- reflogs remain recovery roots until policy expires them;
- freshly expired reflog roots survive the current gc invocation;
- prune keeps its two-week default object-age grace period;
- malformed reflogs and missing historical roots fail before pack creation;
- corrupt current connectivity fails before any maintenance;
- a final full fsck verifies the resulting object database;
- `gc --dry-run` and legacy `gc --no-prune` never write repository state.

## Compatibility boundary

This remains pygit's educational SHA-256 object and pack format. Phase 74 does not claim native Git pack compatibility, delta compression, bitmap/MIDX maintenance, background scheduling, auto-gc heuristics, or native Git's complete configuration surface.

`repack -a -d` remains deliberately conservative: an existing pack that contains an object outside the newly generated reachable pack is retained. `gc` therefore does not force-delete unreachable objects that exist only inside such a pack.

## Regression coverage

Phase 74 tests cover:

- verified repack → reflog-expire → prune orchestration;
- one-gc-cycle retention for freshly expired historical commit/tree/blob closure;
- reclamation on a later pass once that recovery root is gone;
- recent reflog and `--no-reflog-expire` retention;
- API-level prune skipping;
- full-pipeline dry-run immutability;
- legacy `gc --no-prune` no-write compatibility;
- malformed-reflog and missing-historical-root preflight failure before pack creation;
- unhealthy current-connectivity failure before any maintenance;
- explicit immediate cutoff behavior;
- installed `pygit gc` routing through the safe application front door.
