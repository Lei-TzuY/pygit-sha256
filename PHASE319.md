# Phase 319 — Explicit verified packfile URI downloads

Phase319 adds the next safe layer above Phase318's protocol-v2 `packfile-uris`
descriptor transport.  A caller can explicitly download one previously parsed
external pack, but the operation remains bounded and side-effect free with
respect to repository state.

## Scope

`pygit.protocol_v2_packfile_uri_download` adds:

- `DownloadedPackfileUri`;
- `verify_packfile_uri_payload()`;
- `download_packfile_uri()`.

This phase does **not** automatically follow descriptors returned by a fetch.
Phase318's Smart HTTP client still stops after returning the descriptors.  The
new downloader runs only when a caller explicitly hands it one descriptor.

## Verification chain

Git SHA-1 packfiles carry a 20-byte SHA-1 trailer over every preceding pack
byte.  The current protocol-v2 packfile URI descriptor separately advertises
the same pack checksum as 40 hexadecimal characters.

Phase319 requires all of the following before returning parsed objects:

1. the payload begins with `PACK` and is large enough to contain the pack header
   and SHA-1 trailer;
2. `sha1(pack_without_trailer)` equals the pack trailer;
3. the trailer equals the descriptor's advertised pack checksum;
4. the complete verified bytes parse through the existing `PackParser`.

The descriptor hash remains remote pack metadata.  It is not a Git object OID
and is never converted into a local SHA-256 object identity.

## Download boundary

The explicit downloader adds conservative network constraints:

- only HTTP and HTTPS descriptor schemes are accepted;
- URI bytes must be ASCII/percent-encoded at the point where they are handed to
  `urllib`; Phase318 still preserves arbitrary descriptor bytes while parsing;
- embedded URI credentials are rejected;
- redirect targets must remain HTTP(S);
- HTTPS-to-HTTP redirect downgrades are rejected;
- redirect targets with embedded credentials are rejected;
- timeout must be a positive integer;
- download size is bounded (256 MiB by default);
- `Content-Length`, when present, is checked before reading;
- an independent streaming byte counter enforces the same bound when length is
  absent or incorrect.

The response media type is not required because Git's packfile URI design does
not define a mandatory HTTP Content-Type for CDN responses.  Integrity is
instead established cryptographically by the native pack checksum chain.

## No repository mutation

`download_packfile_uri()` returns:

- the original descriptor;
- the final validated HTTP(S) URL;
- the verified native pack bytes;
- the `PackParser` result keyed by genuine remote-native SHA-1 object IDs.

It does not:

- update refs;
- write objects into the pygit SHA-256 store;
- create or remove Git keep files;
- write promisor metadata;
- mark a fetch complete;
- run connectivity checks.

Those actions need a repository transaction that can coordinate the inline pack
and all external packs atomically.  They remain deferred.

## Native Git regression

The Phase319 native test creates a real loose blob with Git, runs
`git pack-objects --stdout`, and derives the actual pack checksum from the
native pack trailer.  A local `ThreadingHTTPServer` then serves those exact pack
bytes over HTTP.

The test calls the public explicit downloader, verifies the checksum chain, and
asserts that `PackParser` returns exactly the native blob OID and content.  This
covers real HTTP I/O plus a Git-generated pack without depending on the public
Internet.

Additional tests cover:

- internal pack corruption;
- descriptor checksum mismatch;
- unsupported or malformed URLs;
- embedded credentials;
- HTTPS downgrade redirects;
- HTTP-to-HTTPS redirect acceptance;
- redirect to non-HTTP schemes;
- invalid and oversized Content-Length;
- streaming size overflow without Content-Length;
- invalid timeout and size-limit values;
- explicit GET / Accept request construction.

## SHA-256-native invariants

Phase319 maintains the repository's identity boundary:

- external pack objects remain genuine remote-native SHA-1 identities while in
  transport form;
- the advertised 40-hex pack checksum remains a pack checksum, not a local OID;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived object is
  created;
- local SHA-256 identity remains content-derived at the existing importer/store
  boundary, which this phase intentionally does not cross.

## Coordination

- exact base: Phase318 / PR #293 exact-green head
  `36bea82990166621a408cf3eeda531cdd09e0533`;
- Phase318 Tests #2745: Python 3.9 / 3.13 both 2385 passed, Git 2.55.0;
- Phase317 remains an independent unborn-initialization line;
- Phase319 and Phase320 were checked free before Phase319 creation.

The Phase319 PR remains open and unmerged.
