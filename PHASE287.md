# Phase287: cache protocol-v2 object-info capability discovery

Phase286 bounds large promisor size refreshes by splitting unresolved native OIDs into multiple `object-info size` requests. The same `SmartHttpV2ObjectInfoClient` instance is reused for those chunks, but before Phase287 every `query_sizes()` call repeated smart-HTTP capability discovery through `/info/refs?service=git-upload-pack`.

Phase287 caches that advertisement for the lifetime of one object-info client.

## Behavior

- the first `query_sizes()` call performs the normal Git protocol-v2 capability discovery;
- later queries on the same client reuse the discovered `ProtocolV2Capabilities` value;
- protocol-v0 fallback (`None`) is cached as well, avoiding repeated discovery requests to a server that did not negotiate v2;
- discovery exceptions are deliberately not cached, so an explicit later retry can recover from a transient network failure;
- each bounded OID chunk still receives its own normal `object-info size` POST;
- separate client instances keep independent capability caches, so different promisor remotes cannot leak negotiation state into one another.

## Git compatibility

This phase does not change the protocol-v2 request grammar, capability interpretation, server-option forwarding, response parser, or size semantics. Git smart-HTTP capability discovery is per remote endpoint; reusing one successful advertisement during one short-lived client operation only removes redundant metadata GETs.

No capability is synthesized. A v2 server that does not advertise `object-info` still raises the existing unsupported-capability error when the command request is built, and a protocol-v0 response still yields the existing `None` fallback.

## SHA-256-native boundary

The cache contains only protocol capability metadata. Object-info requests and responses continue to use genuine remote-native 40-hex SHA-1 OIDs. Returned scalar sizes may be persisted for unresolved promises, but no SHA-1 value is padded, translated, or promoted into a repository-visible 64-hex SHA-256 identity. No local object is materialized by this optimization.

## Verification

`tests/test_phase287.py` covers:

- one capability discovery across multiple object-info batches;
- protocol-v0 fallback caching with no object-info POST;
- retry after a discovery exception;
- independent caches for independent clients/remotes.

The full Python 3.9/3.13 regression suite remains the authoritative CI gate.
