# Phase 311 — Protocol-v2 ref-in-want direct named-ref fetch

Phase311 adds an isolated, request-aware implementation of Git protocol-v2 `ref-in-want` on top of the exact-green Phase309 transport line.

The feature is intentionally kept in `pygit/protocol_v2_ref_in_want.py` rather than reopening the already-validated Phase309 fetch parser. It reuses the strict capability discovery, Smart HTTP envelope validation, sectioned fetch parser, pack parser, native SHA-1 transport OID validation, and repository-visible SHA-256 identity boundary already established by Phase309.

## Why ref-in-want

Protocol-v2 normally lets a client run `ls-refs`, resolve remote refs to OIDs, and then send `want <oid>`. When a server advertises the fetch feature `ref-in-want`, a client may instead send `want-ref <ref>` directly.

This is useful for servers whose ref view can change between replicas or between reference discovery and fetch. The server resolves the named ref in the fetch request and reports that resolution in the `wanted-refs` response section.

Phase311 therefore exposes a dedicated `SmartHttpV2RefInWantClient.fetch_refs()` path which performs capability discovery and then goes directly to the fetch command. It does **not** issue `ls-refs` first.

## Request grammar

`build_ref_in_want_request()`:

- requires the server to advertise both the `fetch` command and its `ref-in-want` feature;
- requires at least one requested ref;
- rejects duplicate `want-ref` names instead of silently deduplicating them;
- preserves caller ref order on the wire;
- reuses `check_ref_format(..., allow_onelevel=True)` as a structural/injection safety boundary;
- deliberately permits safe one-level names because native Git accepts pseudo-refs such as `HEAD`;
- leaves existence/remote resolution to the server;
- validates all supplied `have` values as genuine full 40-hex remote-native SHA-1 OIDs;
- always sends `done`, making this phase a one-shot named-ref fetch rather than a second negotiation engine.

## Response contract

Phase309's shared fetch parser remains the authority for:

- section ordering and delimiter placement;
- final `flush-pkt` framing;
- textual record decoding;
- ACK/NAK/ready state;
- wanted-ref OID syntax;
- sideband pack transport and pack signature validation.

Phase311 then adds the request-aware checks that a generic parser cannot perform:

- a successful terminating request must contain a packfile;
- every returned `wanted-refs` name must have been requested;
- every requested ref must appear in the successful response;
- the exact requested/returned ref-name sets must therefore match.

That follows Git's protocol-v2 contract that `wanted-refs` is included for `want-ref` requests when a packfile is included, that the server sends a listing for each requested ref, and that it MUST NOT send unrequested refs.

## Native Git compatibility

A local Git 2.47.3 probe with `uploadpack.allowRefInWant=true` established the native behavior used to design this phase:

- the v2 capability advertisement includes `fetch=... ref-in-want`;
- `want-ref refs/heads/main` returns `wanted-refs` followed by a packfile;
- `want-ref HEAD` is accepted;
- a duplicate `want-ref` is rejected by native Git as a protocol error;
- a safe but nonexistent name remains the server's resolution error rather than a client-side refname grammar error.

The Phase311 regression suite repeats a real stateless-rpc ref-in-want round trip using the Git version installed on GitHub Actions, so current native Git remains the compatibility authority.

## SHA-256-native invariants

No repository identity translation is introduced.

- `want-ref` names are transport metadata, not local object identities;
- returned `wanted-refs` OIDs remain genuine remote-native full 40-hex SHA-1 values;
- pack contents continue through the existing importer/pack boundary;
- repository-visible objects remain content-derived local SHA-256;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced;
- no metadata-only native-to-local identity mapping is added.

## Coordination

- actual `main` at branch creation: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase309 / PR #285 head `34ce0a59431a5743d1bd9d725d51eb617c867789`;
- Phase309 Tests #2687: Python 3.9 / 3.13 both 2303 passed, Git 2.55.0;
- Phase310 is independently occupied by `phase310-validate-promisor-identity-state` and is intentionally untouched;
- Phase311 was rechecked as free immediately before branch creation.

Phase311 is complete only after its own exact-head Python 3.9 / 3.13 full matrix and native Git ref-in-want probe pass.
