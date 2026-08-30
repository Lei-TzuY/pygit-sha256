# Phase 323 — Certified packfile-URI ref publication

Phase323 makes reference publication the final mutable step of the verified
external-pack pipeline introduced in Phases 318–322.

## Scope

`pygit.protocol_v2_packfile_uri_refs.publish_packfile_uri_refs()` consumes a
Phase322 `PackfileUriRootCertificate` and an explicit set of local ref updates.
Each update carries both:

- the genuine 40-hex remote-native SHA-1 root recorded in the certificate; and
- the exact expected old local 64-hex SHA-256 ref value, or pygit's all-zero
  64-hex value when the ref must not exist.

The function re-reads each certified local object immediately before ref
publication, confirms its content-derived SHA-256 identity and certified Git
object type, and requires branch refs to target certified commit objects.

## CAS and lock boundary

Every publication is an explicit compare-and-swap operation. Blind ref
overwrite is intentionally not exposed by this API.

Before calling the existing transactional `update_refs()` backend, Phase323
acquires canonical `<ref>.lock` files in lexical refname order. This matches the
files-backend lock namespace used by native Git and prevents a cooperating
native Git ref writer from publishing the same loose ref concurrently. An
already-existing lock aborts before any ref is updated. Locks are removed on
success or failure.

The established `update_refs()` implementation remains responsible for:

- rechecking every expected old local SHA-256 value;
- verifying target object existence and branch target type;
- publishing all requested refs as one pygit transaction;
- reflog updates; and
- snapshot rollback on publication I/O failure.

If the transaction loses a CAS race or cannot acquire a lock, refs remain
unpublished. Valid immutable SHA-256 objects imported by Phase321 may remain
unreachable, which is the intentional Git-style failure mode.

## Native Git compatibility

Git's `update-ref <ref> <new-oid> <old-oid>` contract updates a ref only when its
current value matches the supplied old object id; an all-zero old value means
that the ref must not exist. Git's reference transaction lifecycle also has a
prepared state in which references have been locked on disk before commit.
Phase323 mirrors those two observable safety properties at the external-pack
publication boundary while retaining pygit's SHA-256-native local object ids.

## SHA-256-native invariants

Phase323 does not translate object identities.

- certificate keys remain genuine full remote-native SHA-1 object ids;
- ref values are always full local SHA-256 object ids;
- the new ref target is taken only from a Phase322 certificate and revalidated
  against actual object content;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived local
  object identity is introduced;
- refs are the final mutable step after external pack verification, complete
  content import, and root certification.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase322 / PR #298 head
  `709723e33dfe97a2edf885c9906437e3d6af7e1a`
- Phase322 Tests #2771: success
- Phase323 branch name was collision-checked before creation
- Phase317 remains an independent unborn-ref initialization line

## Tests

`tests/test_phase323.py` covers:

- successful certified commit publication;
- explicit zero-old creation CAS;
- stale expected-old failure with no partial multi-ref publication;
- canonical lock contention;
- branch type safety;
- certificate object-type revalidation before locking;
- multi-ref publication and lock cleanup;
- strict 40-hex native SHA-1 validation;
- strict 64-hex local expected-old validation; and
- rejection of pseudo-ref/empty publication requests.

The full inherited test suite remains the authority for the underlying ref
transaction, reflog, object-store, packfile-URI transport/download/import, and
SHA-256 compatibility behavior.
