# Phase 318 — Protocol-v2 packfile URI transport

Phase318 adds the protocol-v2 `packfile-uris` transport primitive on top of the
exact-green Phase316 line.  It negotiates URI offload, parses Git's native
`sideband-all` response form, and exposes strict external-pack descriptors to
callers.  It deliberately does **not** download arbitrary external URIs yet.

## Why this phase exists

Before Phase318 the shared fetch parser already knew the `packfile-uris` section
name for ordering purposes, but intentionally raised `RuntimeError` whenever a
server actually returned that section.  That kept older transports safe, but it
left a documented protocol-v2 fetch branch unimplemented.

Git's packfile URI design allows a server to move part of a fetch response to a
CDN or other HTTP(S) endpoint.  A client advertises the URI protocols it is
willing to use with exactly one:

`packfile-uris <comma-separated-protocols>`

The server may then send a `packfile-uris` section directly before the inline
`packfile` section.  Each record carries the current 40-hex pack checksum, a
space, and the external URI.

## API

`pygit.protocol_v2_packfile_uris` adds:

- `PackfileUriDescriptor(pack_hash, uri)`;
- `ProtocolV2PackfileUriResponse`;
- `V2PackfileUriFetchResult`;
- `normalize_packfile_uri_protocols()`;
- `build_packfile_uri_fetch_request()`;
- `parse_fetch_response_with_packfile_uris()`;
- `validate_packfile_uri_response()`;
- `SmartHttpV2PackfileUriClient.fetch_with_packfile_uris()`.

The ordinary exact-green `parse_fetch_response()` remains unchanged.  Phase318
uses an additive adapter: it normalizes `sideband-all`, extracts and validates
the URI section, removes only that section from the wire image, and then feeds
the remaining response through the established strict fetch parser.

## `sideband-all` compatibility

A positive native Git packfile-URI response has an additional compatibility
constraint.  Current Git's server emits actual URI descriptors when
`sideband-all` is negotiated; section headers, URI records, and the packfile
header are all carried on sideband channel 1.

Phase318 therefore requests `sideband-all` opportunistically whenever the
server advertises it.  The response adapter:

- strips the global channel-1 envelope from section headers and textual records;
- discards channel-2 progress;
- preserves channel-3 as a fatal server error;
- restores channel 1 around packfile payload chunks so the established fetch
  parser sees its historical input shape;
- still accepts a spec-shaped unbanded `packfile-uris` response from another
  protocol-v2 server.

A server that advertises `packfile-uris` without `sideband-all` is still usable:
the request is sent without inventing an unsupported `sideband-all` argument,
and the server may simply return its normal inline pack.

## Descriptor trust boundary

The current protocol grammar defines each descriptor as a 40-hex pack checksum
plus URI.  Phase318 validates that exact remote transport form and does not
reinterpret it as a pygit object ID.

- pack checksum must be exactly 40 hexadecimal characters;
- duplicate pack checksums are rejected;
- URI must be non-empty and contain no bytes below `0x20`;
- URI bytes are preserved rather than forced through UTF-8, matching the
  protocol's byte-oriented URI field;
- the response URI scheme must be one of the protocols the caller requested;
- Phase318 currently permits only the protocol-defined HTTP/HTTPS client set;
- a non-empty `packfile-uris` section must directly precede `packfile`;
- the terminating inline pack remains mandatory and is parsed by the existing
  `PackParser`.

The protocol permits the server to return no URI section even after the client
requests it.  That remains a valid response.

## No automatic external download yet

Git's full client design says all URI packs should be downloaded/indexed before
connectivity checking and retained with keep files until refs are updated.
Those operations introduce a new network trust boundary, hash verification,
partial-failure cleanup, keep-file lifecycle, and repository transaction
semantics.

Phase318 intentionally stops before that boundary.  It returns verified
transport descriptors to the caller and performs no HTTP request to a URI from
the server.  A later phase can implement download + checksum verification +
transactional import explicitly.

## Native Git compatibility

Git's own implementation advertises `packfile-uris` when
`uploadpack.blobPackfileUri` has a value.  `pack-objects --uri-protocol=<scheme>`
then excludes a configured blob and emits `<pack-hash> <uri>` before the inline
PACK.

Local Git 2.47.3 probes additionally established that a positive URI response is
emitted when `uploadpack.allowSidebandAll=true` and the client sends
`sideband-all`.  The Phase318 CI regression repeats this against the runner's
Git:

1. create a real repository containing one blob;
2. create a separate pack containing that blob and derive its actual trailing
   SHA-1 pack checksum;
3. configure `uploadpack.blobPackfileUri` with the blob OID, real pack checksum,
   and HTTPS URI;
4. request `packfile-uris https` plus native `sideband-all`;
5. parse the returned descriptor;
6. prove the inline pack contains commit + tree and no blob.

This verifies observable offload semantics, not only packet spelling.

## SHA-256-native invariants

Packfile URI metadata does not create repository-visible identities.

- request wants/haves remain genuine remote-native full 40-hex SHA-1 OIDs;
- the descriptor's 40-hex value is a remote pack checksum, not a padded or
  translated local SHA-256 object ID;
- URI bytes are transport metadata only;
- inline pack contents continue through the existing content-derived importer
  boundary;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-only local object
  mapping is introduced;
- no promisor metadata is written;
- no external content is materialized in this phase.

## Coordination

- exact base: Phase316 / PR #292 exact-green head
  `ca31bdb36f9e542935f949162c3a40d3f28eacdf`;
- Phase316 Tests #2739: Python 3.9 / 3.13 both 2363 passed, Git 2.55.0;
- Phase317 is independently occupied by
  `phase317-empty-remote-unborn-initialization` and is untouched;
- Phase318 was rechecked as free immediately before branch creation.

The Phase318 PR remains open and unmerged.
