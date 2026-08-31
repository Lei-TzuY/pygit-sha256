# Phase359: Integrate durable ref publication with fork-safe ownership

Phase359 brings the independently exact-green Phase350 tracking-ref durability boundary into the Phase358 fork-safe publication-ownership stack.

## Composition

The Phase358 base already contains:

- inode-aware repository publication guard ownership;
- inode-aware `FETCH_HEAD.state.lock` ownership;
- child-side fork cleanup that closes inherited ownership descriptors and clears copied registries without unlinking parent lock pathnames.

Phase350 independently strengthened `publish_packfile_uri_refs()` so successful return requires:

1. the compare-and-swap ref transaction to complete;
2. every live published ref and written reflog to be fsynced while canonical target locks are still held;
3. target lock pathnames to be removed;
4. every containing metadata directory, leaf-first through `.pygit`, to cross a directory durability fence.

Phase359 reuses Phase350's production/tests/docs byte-for-byte and verifies those durability operations execute inside the newer Phase358 outer ownership critical section.

## Exact Phase350 reuse

The following exact-green blobs are reused without rewriting:

- `pygit/protocol_v2_packfile_uri_refs.py`: `6a04cb59b9b9779a791b59ccc82fc5bbb48b8882`
- `tests/test_phase350.py`: `bd1bb1ea0bf26904749e1769abd03740e810f68b`
- `tests/test_phase350_integration.py`: `072b515033700f29e42a1cf87f36e0cbafa69e98`
- `PHASE350.md`: `6620b403e09590ddf28416f1f0d478f73cb08078`

No Phase358 fork/ownership state machine is modified by this integration.

## Cross-layer ordering

For the incremental named-fetch transaction, the mutable tail is now:

`FETCH_HEAD state guard -> repository publication guards -> state revalidation -> [durable FETCH_HEAD] -> ref CAS -> ref/reflog file fsync -> target-ref lock removal -> ref/reflog directory fences -> repository-guard release -> FETCH_HEAD-state-guard release`

The Phase350 ref durability work therefore happens while the Phase358 outer guard ownership is still live. A second cooperating fetch cannot enter the final publication window merely because the target ref transaction has reached its own durability fence.

## Failure model

Phase350 deliberately provides a **success-after-durability** contract, not rollback after visibility. A ref file may already be visible if a subsequent ref/reflog/directory fsync fails. The exception propagates, and Phase359 verifies that the outer publication ownership layers still unwind correctly:

- retained ownership descriptors are closed;
- ownership registries are cleared;
- transaction-owned state/repository guard pathnames are released;
- no later mutable step is reported as successful by the failing call.

## Regression coverage

`tests/test_phase359.py` adds two cross-layer tests:

- ref/reflog file fsync and directory fsync callbacks assert both the `FETCH_HEAD.state.lock` registry and all repository publication guard ownership records are live during the durability boundary;
- an injected ref-file fsync failure verifies the visible-ref failure semantics from Phase350 while also proving both outer ownership layers unwind without residual registry entries or guard pathnames.

The inherited Phase350 suite continues to test file/directory durability ordering and failures. Phase358 continues to exercise real fork-child ownership cleanup.

## SHA-256-native invariants

This integration changes durability and process ownership only:

- remote/native compatibility identities remain genuine full 40-hex SHA-1 values;
- local objects, refs, reflogs, `FETCH_HEAD`, and object-map values remain genuine content-derived full 64-hex SHA-256 values;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.

## Coordination

- actual `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase358 / PR #335 head `cd7e33b7f7ddd6294870e0262f0347e73d93a076`;
- Phase358 Tests #3030: Python 3.9 / 3.13 both **2635 passed**, Git 2.55.0;
- Phase350 / PR #327 remains the exact-green sibling source for durable ref publication;
- Phase359 was collision-checked immediately before branch creation and was free.

This phase remains stacked, open, and unmerged until its own exact-head CI is green.
