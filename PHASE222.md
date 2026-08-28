# Phase222 — Primary promisor ordering

Phase222 completes pygit's multi-promisor missing-object policy by adding Git's `extensions.partialClone` primary-remote marker and the corresponding fallback ordering rule.

## Motivation

Phase221 allows more than one promisor remote and tries configured fallbacks in repository remote order. Git has one additional rule: the remote named by `extensions.partialClone` is the primary partial-clone source and is deliberately tried **last** for demand fetches. Cache or mirror promisors therefore get the first chance to satisfy missing objects while the canonical source remains the final fallback.

## Partial clone configuration

A protocol-v2 filtered clone now records all three relevant signals:

```text
extensions.partialClone = origin
remote.origin.promisor = true
remote.origin.partialCloneFilter = <filter-spec>
```

`protocol.version=2` remains unchanged.

The new marker is policy metadata only. Repository-visible objects remain SHA-256-native and native Git SHA-1 remains confined to protocol/promisor lookup state.

## Promisor discovery

Missing-object materialization now builds candidates from:

1. promisor remotes already recorded in `.pygit/promisor.json`;
2. configured remotes whose `remote.<name>.promisor` is true;
3. configured remotes with `remote.<name>.partialCloneFilter`;
4. the remote named by `extensions.partialClone`.

This matters for cache-like remotes added after the original partial clone: they can participate in lazy materialization through normal promisor config without rewriting the promise sidecar's remote list.

The public multi-promisor materializer no longer requires the sidecar to name a remote at all. If `.pygit/promisor.json` records the promised native object IDs while Git config supplies `extensions.partialClone` or other promisor candidates, config alone is sufficient to select the demand-fetch source. The historical sidecar-owner requirement remains only on the legacy single-owner compatibility helpers.

## Ordering

Candidate order is:

1. non-primary configured promisor remotes in repository remote configuration order;
2. metadata-only recorded promisor names that are not primary;
3. `extensions.partialClone` primary remote last.

Names whose remote URL was removed are skipped when materialization begins. A stale primary marker therefore does not prevent a working cache promisor from satisfying an object.

If no configured candidate can provide the object, the existing `PromisorMissingError` contract remains authoritative.

## Batching and server options

Phase221's batch shrinking behavior is preserved. Every remote receives the complete still-missing set at the time it is attempted, and later fallbacks receive only unresolved wants.

The Phase213 single-object fetch seam also remains intact. `remote.<name>.serverOption` values continue to be loaded independently for the remote currently being contacted; primary ordering does not leak options between promisors.

## Compatibility

- no `extensions.partialClone` marker: Phase221 configuration order is unchanged;
- ordinary repositories remain network-free;
- legacy single-owner validation helpers are unchanged;
- stale or removed primary remotes are skipped rather than made authoritative;
- config-only promisor discovery is allowed for the public multi-promisor path;
- no protocol request or object format changes are introduced.

## Verification targets

Focused regressions cover:

- an `origin` configured first but marked primary being attempted after a config-only cache promisor;
- config-only primary materialization when the sidecar records promised OIDs but no remote names;
- `remote.<name>.partialCloneFilter` alone marking a promisor candidate;
- the primary marker contributing a candidate even when the sidecar did not record that remote;
- a removed/stale primary remote not blocking a working cache;
- Phase221 ordering remaining unchanged when no primary marker exists;
- filtered clone persisting `extensions.partialClone=origin` alongside the existing promisor/filter settings.
