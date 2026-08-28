# Phase 199 — Smart HTTP protocol v2 `ls-refs` foundation

Phase 199 adds the first protocol-v2 transport layer without pretending that
protocol-v2 pack transfer is already complete.

## Scope

`pygit.protocol_v2` now implements:

- packet-line parsing for v2 flush, delimiter, and response-end packets;
- HTTP capability discovery using `Git-Protocol: version=2`;
- protocol-v2 capability parsing, including `object-format` validation;
- `ls-refs` command construction with `symrefs`, `peel`, optional `unborn`, and
  `ref-prefix` requests;
- parsing of normal refs, symbolic refs, unborn `HEAD`, and peeled annotated
  tags;
- clean detection of a server that ignored the v2 request and answered using
  protocol v0.

`pygit ls-remote` honors repository `protocol.version=2`. When v2 is requested,
it first attempts the v2 capability/`ls-refs` exchange. If the server ignores
the request and returns a v0 advertisement, pygit falls back to the established
v0 `SmartHttpClient`, matching Git's opportunistic protocol negotiation model.

Example:

```bash
pygit config protocol.version 2
pygit ls-remote --symref origin
```

The command remains read-only: it does not import packs, update refs, or mutate
the repository.

## SHA-256-native boundary

Git protocol v2 does not change pygit's repository object format. This phase
accepts the native Git SHA-1 object IDs exchanged at the smart-HTTP boundary and
keeps them confined to remote advertisement data. It explicitly rejects a v2
server advertising another `object-format` instead of silently treating a
64-hex remote ID as a pygit SHA-256 object.

Repository objects and refs remain pygit SHA-256-native.

## Deliberate limitation

Phase 199 does **not** route normal `fetch` pack transfer through protocol v2.
The v2 `fetch` command has sectioned responses (`acknowledgments`,
`shallow-info`, `wanted-refs`, `packfile`, and optional extensions) and needs a
separate implementation before it can safely replace the mature v0 path.

This boundary is important because Git's `fetch --negotiate-only` requires
protocol v2. The next transport phase can build that behavior on this capability
and `ls-refs` foundation rather than emulating it over protocol v0.

## Compatibility notes

The implementation follows Git's protocol-v2 specification:

1. HTTP clients request v2 through `Git-Protocol: version=2` on the smart
   `info/refs` request.
2. A v2 server answers with `version 2` plus a capability advertisement and does
   not advertise refs until `ls-refs` is requested.
3. `ls-refs` supports `symrefs`, `peel`, `ref-prefix`, and the optional `unborn`
   feature.
4. Unknown capabilities are retained but not interpreted.
5. The client only sends its `agent` string if the server advertised `agent`.

## Tests

`tests/test_phase199.py` covers capability parsing, v0 fallback recognition,
object-format safety, command framing, symrefs, peeled tags, unborn HEAD,
HTTP headers/request bodies, repository `protocol.version=2` integration, and
fallback to the existing v0 query path.
