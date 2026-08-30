# Phase 322 — certify packfile-URI roots before ref publication

Phase321 proved that inline and verified external pack objects can be imported through an isolated content-derived SHA-256 staging store and only then published as immutable local objects. Phase322 adds the next narrow trust boundary: prove that the exact remote-native tips a caller intends to publish as refs are present, locally readable, and of the expected Git object type.

## New API

`certify_packfile_uri_roots(store, staged, expected_roots)` accepts a Phase321 `StagedPackfileUriImport` plus a mapping from full remote-native 40-hex SHA-1 object IDs to the caller-required Git object type (`blob`, `tree`, `commit`, or `tag`). It returns a `PackfileUriRootCertificate` only after every requested root satisfies all of the following:

1. the remote id is a genuine full 40-hex SHA-1;
2. the id exists in the exact staged native-to-local mapping;
3. the mapped local id is a genuine full 64-hex SHA-256;
4. that local id appears in Phase321's published object set;
5. the destination `ObjectStore` can read and validate the object;
6. the object's content-derived SHA-256 identity still equals the mapped local id;
7. the decoded object type matches the caller's expected ref semantics.

This catches missing requested tips and type-confusion errors before any ref transaction begins. A branch publisher can require `commit`; an annotated-tag publisher can require `tag`.

## Transaction boundary

Phase322 is deliberately read-only. It does not update refs, HEAD, reflogs, packed-refs, or promisor metadata. Failure therefore cannot expose a partially published ref namespace. Phase321 may already have left valid unreachable immutable objects, which is acceptable and recoverable in the same way as interrupted Git object transfer/import.

A later phase can consume the certificate as the precondition for lockfile/CAS ref publication, with refs remaining the final mutable step.

## SHA-256-native invariants

Remote transport identity and local repository identity remain distinct domains:

- requested roots are genuine remote-native full 40-hex SHA-1 ids;
- local roots are genuine content-derived full 64-hex SHA-256 ids;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced;
- no metadata-derived local object identity is created;
- no object content is synthesized during certification.

## Tests

`tests/test_phase322.py` covers successful certification, explicit type normalization, missing staged roots, unpublished mapped roots, local object absence, type confusion, malformed native ids, malformed expected types, empty root sets, and an explicit assertion that local SHA-256 identity is not a padded remote SHA-1.

The complete inherited suite remains authoritative for Phase318–321 native Git packfile-URI transport, checksum verification, bounded batch handling, importer dependency validation, and SHA-256 staging behavior.
