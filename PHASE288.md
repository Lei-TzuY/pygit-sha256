# Phase288: repository-scoped promisor object-info client reuse

Phase288 extends Phase287's protocol-v2 capability cache across repeated promisor size refreshes that use the same in-process `Repository` instance.

Phase287 cached `/info/refs?service=git-upload-pack` capability discovery inside one `SmartHttpV2ObjectInfoClient`, which removed repeated discovery across Phase286's bounded chunks. A later `refresh_promisor_sizes()` call still constructed a new client, however, so the same remote could be renegotiated again during one long-lived process.

Phase288 keeps one object-info client per repository and effective remote configuration `(remote name, URL, server options)`. The cache uses weak repository keys, so it is process-local session state and cannot keep a repository alive. Changing the remote URL or effective server options naturally creates a different cache entry.

## Compatibility and failure semantics

No Git wire format changes are introduced. Each size query remains an ordinary protocol-v2 `object-info` command using the existing `size` attribute. Phase287's rules remain authoritative: successful protocol-v2 or protocol-v0 discovery is cached inside the reused client, while discovery exceptions are not cached and can be retried later.

Transport/query failures remain soft at the refresh layer and do not trigger content materialization. Reusing a client does not persist capabilities to disk and does not introduce cross-process assumptions about a server.

## SHA-256-native boundary

The cache stores transport client objects only. Requests still contain genuine remote-native 40-hex SHA-1 OIDs and successful responses persist scalar size metadata only. No SHA-1 padding, translation, surrogate SHA-256, local object creation, or native-to-local identity mapping is introduced.

## Tests

`tests/test_phase288.py` covers:

- one client construction across multiple refresh calls for the same repository and remote configuration;
- preservation and normalization of native 40-hex SHA-1 transport identities;
- no client/cache creation when no metadata query is needed; and
- cache invalidation by effective server-option changes.
