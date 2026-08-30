# Phase291: cache explicit object-info capability absence

Phase287 caches protocol-v2 capability discovery inside one object-info client,
Phase288 reuses that client across refresh calls, and Phase289 evicts a reused
client after transport/protocol/query failures so a later refresh can construct a
fresh session.

One failure class is different from a broken session: a server may successfully
negotiate protocol v2 yet explicitly omit the `object-info` capability. That is a
stable capability-negative result for the lifetime of the cached advertisement,
not evidence that the smart-HTTP session is corrupt.

Phase291 makes that distinction explicit:

- add `ObjectInfoUnsupportedError`, a `RuntimeError` subclass used only when a
  protocol-v2 advertisement lacks `object-info`;
- retain the existing cached client when that exception is raised;
- continue immediately to the next configured promisor remote in the current
  refresh pass;
- on a later independent refresh, reuse the same client and its already-cached
  negative capability advertisement instead of repeating discovery;
- continue evicting clients for ordinary `OSError`, `RuntimeError`, and
  `ValueError` failures exactly as Phase289 requires;
- preserve per-repository and effective-remote-configuration cache isolation.

This change does not persist negative capability state to disk. Changing the
remote URL or configured server options still produces a different cache key,
and a new `Repository` object receives a fresh process-local cache.

## Git protocol and SHA-256-native boundary

No Git wire grammar changes are introduced. Capability absence is learned from a
normal protocol-v2 advertisement. No object-info POST is generated when the
capability is absent.

All metadata queries continue to use genuine remote-native 40-hex SHA-1 OIDs.
Only scalar sizes may be persisted. Phase291 does not fetch object contents,
create local objects, create native-to-local mappings, pad/translate SHA-1, or
synthesize repository-visible SHA-256 identities.

## Tests

`tests/test_phase291.py` covers:

- the dedicated unsupported-capability exception and RuntimeError compatibility;
- one capability discovery across repeated `query_sizes()` calls when the same
  client has a v2 advertisement without `object-info`;
- retaining the unsupported cached client across independent refresh calls;
- fallback to a second promisor remote while retaining/reusing both clients;
- continued eviction/replacement for generic RuntimeError session failures.

The full Phase284-Phase289 regression suite remains authoritative for bounded
batching, multi-promisor fallback, capability caching, client reuse, failure
eviction, server-option cache keys, and SHA-1/SHA-256 identity separation.
