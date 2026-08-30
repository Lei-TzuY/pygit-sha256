# Phase275: protocol-v2 `object-info` size transport

Phase275 adds a metadata-only Git protocol-v2 `object-info` primitive. The goal is deliberately narrower than changing partial-clone semantics: first establish a validated way to ask a supporting remote for native object sizes without downloading object contents.

## Motivation

The current `blob:limit` rev-list adapters refuse to classify unresolved promised blobs because pygit's persistent promisor metadata records native identity and object type, but not trustworthy uncompressed size. Guessing from filter thresholds or unit-test fixture payloads would make the repository state lie about remote objects.

Git protocol v2 provides a purpose-built `object-info` command for this problem. A server may advertise the capability and answer `size` queries for full native object IDs without sending a packfile or the object payload itself.

This phase builds that transport foundation only. Persisting returned sizes into promisor state and consuming them from `blob:limit` are intentionally separate follow-up changes, so unsupported servers retain the existing strict behavior.

## Wire protocol

`pygit.protocol_v2_object_info` adds:

- capability-gated `object-info` request construction;
- deterministic de-duplication and validation of full 40-hex native SHA-1 IDs;
- the `size` request attribute;
- ordered protocol-v2 `server-option` forwarding through the existing command-prefix layer;
- parsing of the echoed `size` attribute and one result per requested OID;
- explicit representation of unknown OIDs as `size=None` rather than a guessed zero;
- duplicate, malformed-size, invalid-OID, missing-header, and response-set validation;
- `SmartHttpV2ObjectInfoClient`, reusing the existing protocol-v2 capability discovery and smart-HTTP upload-pack endpoint;
- `None` as the established clean fallback signal when a server ignores protocol v2 entirely;
- an explicit error when a protocol-v2 server does not advertise `object-info`.

No `fetch` command is sent by this client. The second HTTP request is `command=object-info`, so successful size lookup does not download a packfile.

## Compatibility boundary

Git servers do not necessarily advertise `object-info`. The server-side `transfer.advertiseObjectInfo` setting controls advertisement and defaults to false in current Git. Phase275 therefore treats remote size metadata as optional capability-derived information, never as something every partial clone can assume exists.

The phase deliberately implements only the mature `size` attribute. Current Git documentation also describes newer object-type reporting, but size is the metadata required by the existing `blob:limit` gap and is supported by older native Git releases used during compatibility probing.

## SHA-256-native boundary

All `object-info` requests and responses remain at the remote-native Git boundary:

- requested identities are full native 40-hex SHA-1 OIDs;
- returned identities remain native SHA-1 and are not exposed as local repository object IDs;
- no SHA-1 value is padded, translated, or substituted into a 64-hex local SHA-256 slot;
- no object payload is materialized merely to discover size;
- no `.pygit` object, ref, index, or promisor state is mutated by this transport primitive.

A later phase may persist a size only when it came from this capability-gated remote response (or another equally trustworthy source).

## Native Git probe

The regression suite creates a native repository and starts `git upload-pack --stateless-rpc` with protocol v2 and `transfer.advertiseObjectInfo=true`. It then:

1. parses the real capability advertisement;
2. verifies `object-info` is advertised;
3. builds a pygit `size` request for the native commit OID;
4. sends that request to native upload-pack;
5. parses the response with the pygit parser;
6. compares the returned size with native `git cat-file -s`.

This locks both request and response framing against a real Git implementation instead of relying only on synthetic pkt-lines.

## Tests

`tests/test_phase275.py` covers:

- deterministic command/attribute/OID framing;
- server-option composition;
- capability and OID validation;
- known and unknown object responses;
- malformed/duplicate response rejection;
- complete smart-HTTP capability → object-info exchange;
- proof that the exchange does not issue `command=fetch`;
- protocol-v0 fallback versus protocol-v2-without-capability distinction;
- requested/returned OID set validation;
- native Git stateless-rpc size round trip.

## Deferred integration

- persist trustworthy remote size in promisor metadata with backward-compatible state migration;
- teach general and ordered `blob:limit` filters to consume that metadata without fetching object contents;
- retain fail-before-output behavior whenever size metadata is absent;
- evaluate newer `object-info type` support independently rather than assuming it on older servers.
