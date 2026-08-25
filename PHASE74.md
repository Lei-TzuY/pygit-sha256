# Phase 74 — Safe `gc` orchestration

Phase 74 replaces the installed legacy `pygit gc` path with a coordinated maintenance pipeline built from the hardened primitives added in Phases 71–73.

## Why this phase exists

The older `Repository.gc()` implementation treated every object reported as dangling by the legacy fsck path as immediately disposable. It did not honor reflog recovery history, object-age grace periods, verified pack redundancy, or the newer storage-integrity checks.

The installed command now uses `pygit.gc.garbage_collect()` instead. The legacy method remains untouched for source compatibility, but `python -m pygit gc` and the installed `pygit gc` entrypoint no longer dispatch through it.

## Pipeline

A normal pass uses one frozen policy timestamp and performs:

1. full `fsck` health check;
2. dry-run/preflight of every requested maintenance phase;
3. full reachable-object `repack -a -d`;
4. `reflog expire --all` using the configured recovery windows;
5. conservative loose unreachable-object `prune`;
6. final full `fsck` validation.

All requested phases are planned before the first real mutation. This means a malformed reflog, corrupt pack/index pair, or unhealthy reachable graph is rejected before repack changes storage.

## Default retention policy

- reflog general expiry: 90 days;
- unreachable reflog expiry: 30 days;
- loose unreachable-object prune grace: 2 weeks.

The three cutoffs are intentionally independent. Expiring a reflog record does not itself delete an object, and an object still younger than the prune cutoff remains available after its reflog recovery record expires.

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

`--no-prune` skips deletion of unreachable loose objects. Repack may still remove verified duplicate loose copies of objects that have a trusted packed copy; that operation is redundancy cleanup, not unreachable-object pruning.

`--no-reflog-expire` preserves every existing recovery record, so the subsequent prune phase continues to treat all reflog old/new OIDs as retention roots.

## Dry-run semantics

`--dry-run` executes the same health, pack, reflog parsing, and loose-object validation paths without writing repository state. No pack is installed, no reflog is rewritten, and no loose object is removed.

The prune portion of dry-run is deliberately conservative: because the reflogs are not actually rewritten, objects protected only by reflog records that *would* expire may not yet appear in the dry-run prune candidate list. The verbose output labels this explicitly.

## Python API

```python
from pygit import garbage_collect

result = garbage_collect(repo)
print(result.repack.object_count)
print(result.reflog.expired if result.reflog else 0)
print(result.prune.pruned if result.prune else 0)
```

`GarbageCollectResult` exposes the repack, reflog-expiry, and prune sub-results along with preflight/final reachable counts.

## Safety properties

- full current repository integrity is required before maintenance;
- all requested phases are dry-planned before the first mutation;
- repack uses strict pack/index validation and verified redundant-copy cleanup;
- reflogs remain recovery roots until explicitly expired by policy;
- prune keeps its two-week default object-age grace period;
- malformed reflogs prevent the entire pass from starting;
- corrupt current connectivity prevents the entire pass from starting;
- a final full fsck verifies the resulting object database;
- `gc --dry-run` never writes repository state.

## Regression coverage

Phase 74 tests cover:

- complete repack → reflog-expire → prune lifecycle;
- current objects remaining readable after loose duplicates are removed;
- recent reflogs preserving recovery-only commit/tree/blob closure;
- `--no-reflog-expire` retention;
- `--no-prune` behavior;
- full-pipeline dry-run immutability;
- malformed-reflog preflight failure before pack creation;
- unhealthy current-connectivity failure before any maintenance;
- explicit immediate cutoff overrides;
- installed `pygit gc` routing without the legacy argparse path.
