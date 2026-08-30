# Phase 284 — Lazy promisor size refresh

Phase284 closes a usability gap in `rev-list --filter=blob:limit=<n>[kmg]` for partial clones whose initial filtered fetch could not persist size metadata. The command now gets one metadata-only recovery path before retaining the existing strict failure.

## Scope

When an unresolved promised blob has no trusted size in `.pygit/promisor.json`:

1. inspect the configured promisor remotes recorded by the filtered-fetch state,
2. try them in deterministic remote-name order,
3. preserve each remote's configured protocol-v2 `serverOption` values,
4. issue protocol-v2 `object-info size` only for the unresolved native SHA-1 identities that still need metadata,
5. persist only non-`None` size answers for objects that are still unresolved promises,
6. retry the existing `blob:limit` membership predicate from that trusted metadata.

Already-persisted sizes never trigger another metadata query.

## Strict fallback

`object-info` is optional. A remote may speak protocol v0, omit the capability, fail transport, return malformed metadata, or report an OID as unknown. Those cases remain soft at the refresh layer but do not weaken the filter contract: if size is still unavailable, `blob:limit` raises the existing `persistent promisor size metadata is unavailable` error before user-visible traversal output.

There is deliberately no fallback to `_fetch_native_object()` or `_fetch_native_objects()` merely to learn size. Filter classification remains metadata-only.

## Git protocol compatibility

Current Git protocol-v2 documentation defines `object-info` as a metadata command intended to let clients make decisions without fully fetching objects, and currently defines `size` as the only supported information attribute. Phase284 therefore reuses only the existing `size` request/response grammar and does not invent an object-type extension.

The request stays entirely in the remote-native SHA-1 domain. Server options are forwarded exactly as configured for the named promisor remote.

## SHA-256-native identity boundary

A successful size refresh mutates only scalar size metadata in promisor state. It does not:

- create a local object,
- populate the native-to-local resolved map,
- pad or translate a 40-hex SHA-1 into 64 hex,
- synthesize a surrogate SHA-256 identity,
- change the existing omission-channel rule that only genuine local SHA-256 objects may be printed as `~<oid>`.

Materialization remains the only path that can derive the real local SHA-256 identity from object content.

## Regression coverage

`tests/test_phase284.py` covers:

- successful lazy size refresh and persistence,
- exact-threshold `blob:limit` behavior after refresh,
- configured server-option forwarding,
- avoiding a second query when trusted size already exists,
- unsupported capability preserving the strict historical error,
- unknown-object metadata preserving the strict error,
- explicit guards against single and batch content fetches,
- deterministic multi-promisor-remote fallback,
- preservation of the unresolved native SHA-1 / local SHA-256 identity boundary.
