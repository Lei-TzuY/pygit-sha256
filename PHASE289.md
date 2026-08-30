# Phase289: evict failed promisor object-info clients

Phase288 reuses one protocol-v2 `object-info` client per repository and effective
promisor remote configuration. That removes repeated capability discovery across
independent metadata refreshes, but a reused client must not remain cached forever
after its transport or protocol session fails.

Phase289 adds a narrow failure-lifetime rule:

- keep successful clients cached exactly as Phase288 does;
- if `query_sizes()` raises `OSError`, `RuntimeError`, or `ValueError`, evict only
  that exact cached client before falling back to the next promisor remote;
- do not immediately create a replacement for the same remote inside the failing
  refresh pass, so one request cannot loop or double-query a broken endpoint;
- allow a later independent refresh to construct a fresh client and renegotiate
  capabilities;
- identity-guard eviction so an older failing reference cannot remove a newer
  replacement stored under the same `(remote, URL, server options)` key;
- leave sibling remotes and sibling effective configurations untouched.

This remains metadata-only. Failure recovery never falls back to fetching object
content merely to learn a blob size.

## Git and SHA-256-native compatibility

No Git protocol grammar changes are introduced. Each retry remains a normal
protocol-v2 `object-info size` request, and request identities remain genuine
remote-native 40-hex SHA-1 object IDs. Successful responses persist scalar sizes
only. Phase289 does not pad or translate SHA-1, synthesize repository-visible
SHA-256 IDs, materialize local objects, or create native-to-local identity maps.

## Tests

`tests/test_phase289.py` covers:

- replacing a failed reused client on a later refresh;
- avoiding same-remote retry loops inside the failing refresh call;
- identity-guarded eviction when a newer client has already replaced an older
  reference.

The existing Phase284-Phase288 regression suite continues to cover bounded
batching, multi-promisor fallback, capability caching, effective configuration
keys, and native SHA-1/local SHA-256 identity separation.
