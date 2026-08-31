# Phase 333 — Plan verified incremental packfile-URI haves

Phase332 can read Git-compatible loose-object LMAP files and recover explicit
SHA-256 ↔ SHA-1 compatibility identities. Phase333 turns that capability into a
read-only incremental-fetch planning boundary.

## Scope

`pygit.protocol_v2_packfile_uri_incremental` adds:

- `PackfileUriIncrementalState`;
- `plan_packfile_uri_incremental_state()`.

For each existing Phase328 remote-tracking publication, the planner considers its
current local SHA-256 tip. It emits a native SHA-1 `have` only when all of the
following are true:

1. the tracking tip is an existing local commit;
2. the tip has an explicit validated LMAP compatibility mapping;
3. every local object reachable through the commit/tree/blob closure is readable;
4. every object in that closure also has an explicit validated LMAP mapping;
5. the tip is not an incomplete foreign shallow commit requiring separate shallow
   negotiation semantics.

The result also carries the complete native-SHA-1 → local-SHA-256 mapping for the
safe closure. A later importer integration can pass that mapping to
`NativeImporter(..., known=...)`, allowing objects omitted because they are
reachable from an advertised `have` to resolve to already-present local objects.

## Fallback behavior

Missing compatibility metadata is not corruption and never causes an identity to
be guessed. New tracking refs, unmapped tips, partially mapped closures, and
foreign shallow boundaries are returned in `fallback_refs` and contribute no
`have`; the caller can perform the established full fetch instead.

By contrast, a validated LMAP entry pointing at a missing/corrupt local object, or
an existing tracking tip resolving to a non-commit object, fails closed. Claiming
such state as a `have` could cause the server to omit objects that pygit cannot
actually supply locally.

## Git compatibility

Phase333 introduces no new wire grammar. Protocol-v2 `have` already carries a
remote-native object id, and the existing fetch transport validates those values
as full 40-hex SHA-1 identities. This phase only determines when pygit has enough
validated local compatibility state to safely reuse that existing mechanism.

## SHA-256-native invariant

The planner never converts a local SHA-256 id into SHA-1 by padding, truncating,
rehashing the id, or inventing a surrogate. A native identity is usable only when
Phase332's validated Git object-map explicitly contains the full pair.

Likewise, the returned `known_native_to_local` values are complete full 40-hex
SHA-1 → full 64-hex SHA-256 pairs. They are lookup evidence for future import
resolution, not newly derived repository object identities.

## Coordination

- actual `main` remains independently tracked;
- exact base: Phase332 / PR #307 head
  `d7d5b1317e6aaf4cd1cd558ffca1fa1e0da98d55`;
- Phase332 GitHub Actions Tests #2820: success;
- Phase331 is an independent unborn-clone line;
- Phase333 was collision-checked immediately before branch creation;
- Phase333 changes are additive: one production module, focused tests, and this
  document; no existing fetch, importer, transaction, ref, or object-map file is
  modified.

## Tests

`tests/test_phase333.py` covers:

- a complete LMAP-backed commit/tree/blob closure producing one native `have` and
  the complete known-object map;
- new-ref full-fetch fallback;
- partial-map fallback without synthetic identities;
- mapped-but-missing local object failure;
- non-commit tracking-tip rejection;
- shallow foreign commit fallback;
- deduplication when multiple tracking refs share the same existing commit.

Full GitHub Actions Python 3.9 / 3.13 remains the authoritative suite gate.

This phase intentionally stops before wiring the planned state into the network
and staging transaction. That integration should pass both `haves` and
`known_native_to_local` together so negotiation cannot get ahead of importer
knowledge.
