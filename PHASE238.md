# Phase238 — Promisor-aware fsck connectivity

Phase238 makes repository integrity checking safe for partial clones whose
foreign trees retain native Git SHA-1 identities for blobs deliberately omitted
by a promisor remote.

## Problem

A native-reference `TreeEntry` has two identity domains:

- `_sha` is a real local pygit SHA-256 object id when the object has been
  materialized;
- `native_oid` is the upstream Git SHA-1 identity retained by a filtered
  foreign tree.

For an unresolved promise, reading `TreeEntry.sha` invokes the lazy promisor
resolver.  The existing fsck traversal did exactly that for every tree entry,
which meant an integrity check could unexpectedly fetch all omitted blobs.  It
also made `--connectivity-only` unsuitable as a local partial-clone check.

Git's partial-clone model treats objects intentionally omitted by a promisor as
expected missing objects rather than ordinary repository corruption.  The
important constraint for pygit is that this exception must not invent a local
SHA-256 identity for content that has not been received.

## Implementation

`pygit/promisor_fsck.py` installs a transparent wrapper around the established
fsck implementation.  Ordinary repositories delegate byte-for-byte to the
existing checker.  Promisor repositories reuse the existing roots, storage
inventory, object validators, cycle detector, and reachability accounting, but
special-case native-reference trees:

1. resolved entries are followed by their genuine local SHA-256 ids;
2. unresolved entries are inspected only through `native_oid` and persistent
   promisor metadata;
3. an unresolved native entry is accepted only when its SHA-1 is recorded as
   promised with the object kind implied by the tree mode;
4. an absent native entry that is no longer recorded as promised is reported as
   `missing-promisor-object`;
5. a promised kind inconsistent with the tree mode is reported as
   `wrong-promisor-type`.

The connectivity-only collector likewise follows only locally addressable
SHA-256 edges.  Expected unresolved promises never enter the local object queue
and therefore never invoke the demand-fetch resolver.

## SHA-256-native invariant

Phase238 deliberately keeps the two hash domains separate.  Native SHA-1 is
used only to prove promisor provenance for an absent foreign object.  It is
never inserted into fsck's local reachability sets, never rendered as a pygit
object id, and never expanded or padded into a surrogate SHA-256.  Once a
promised object is materialized, the existing native-to-local resolution maps
it to the content-derived SHA-256 and fsck follows that ordinary local edge.

## Regression coverage

`tests/test_phase238.py` covers:

- full fsck of a real `blob:none` foreign-tree fixture with every network-fetch
  seam forced to fail if called;
- `--connectivity-only` over the same fixture without materialization;
- stable promisor metadata before and after integrity checks;
- rejection when an unresolved native child loses its promisor record;
- rejection when promisor metadata claims the wrong object kind;
- unchanged ordinary-repository fsck behavior.

## Native Git alignment

Git's `fsck` verifies object connectivity and validity, while partial-clone
promisor objects are a defined category of objects that may be intentionally
absent until demanded.  Phase238 follows that model locally: promised absence is
not corruption, but the promise itself must remain explicit and type-consistent.
No integrity operation is allowed to silently turn into a bulk content fetch.

## Next step

A useful follow-up is Phase239: expose expected promisor omissions in a
machine-readable fsck diagnostic/reporting mode without changing the success
status, while preserving the explicit `sha1:` native-identity namespace already
established by Phase237.  That would improve observability without weakening
SHA-256-native repository identity.
