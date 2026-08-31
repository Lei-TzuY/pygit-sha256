# Phase350: Durable packfile-URI tracking-ref publication

Phase347 correlates durable populated `FETCH_HEAD` publication with the final
tracking-ref CAS critical section. Phase350 closes the remaining persistence
gap at the ref commit point itself.

This work was originally prepared under Phase348, but that namespace was claimed
by an independent concurrent worker before PR creation. Phase349 was also
occupied. The implementation was therefore rebuilt cleanly as Phase350 directly
from the exact-green Phase347 head; no Phase348/349 commits are part of this
stack.

## Problem

The generic `ref_transaction.update_refs()` path fsyncs each temporary ref file
before `os.replace()`, but packfile-URI ref publication previously returned
success without an explicit durability fence for:

- the live ref files after rename;
- reflog append writes;
- parent-directory entries containing the renamed refs/reflogs;
- removal of the canonical `<ref>.lock` files.

That left a weaker success contract than the existing durable LMAP and
`FETCH_HEAD` stages in the same verified-fetch transaction.

## Final ordering

The latest incremental transaction now has the intended success boundary:

`download -> SHA-256 stage -> [durable immutable LMAP] -> certify -> publication guards -> state revalidation -> durable FETCH_HEAD -> ref CAS -> ref/reflog file fsync -> target-lock removal -> directory durability fences`

The generic ref transaction remains unchanged. Phase350 strengthens the
packfile-URI publisher specifically.

## Publication contract

`publish_packfile_uri_refs()` now uses the durable path by default. The explicit
`publish_packfile_uri_refs_durable()` spelling exposes the same contract.

After certificate/type/CAS validation and `update_refs()`:

1. every live published ref is fsynced while its canonical target lock is held;
2. every reflog actually written by the transaction is fsynced while those locks
   are still held;
3. canonical target locks are removed;
4. every containing directory from leaf ref/reflog directories through `.pygit`
   is fsynced once, leaf-first.

Doing the directory fence after lock removal persists both the new namespace and
the absence of canonical lockfiles. A later writer that acquires the lock after
removal is a later transaction, not a race inside this one.

On Windows, where Python exposes no portable directory-fsync primitive, the
directory fence is an explicit no-op, matching the portability boundary already
used by the project's durable LMAP/FETCH_HEAD helpers.

Git documents `reference` as a repository component that can be hardened by
`core.fsync`. Phase350 does not implement the global Git config policy; it makes
this verified-fetch publication boundary explicitly durable regardless of the
generic ref settings.

## Failure model

A file-fsync or directory-fsync error propagates. A complete ref may already be
visible if the durability error occurs after the generic ref transaction, but
the caller must not report durable success.

- file-fsync failure releases target locks and skips directory fences;
- directory-fsync failure occurs after target-lock removal and propagates;
- old==new CAS still fsyncs the live ref but does not fabricate a reflog entry.

Phase347's outer repository publication guards remain held around the complete
public ref-publisher call, so durable FETCH_HEAD and tracking refs stay
correlated at the higher transaction boundary.

## SHA-256-native invariants

Nothing changes identity domains:

- remote negotiation/certificate identities are genuine full 40-hex SHA-1;
- local objects, refs and reflogs carry genuine content-derived 64-hex SHA-256;
- LMAP remains validated SHA-1 <-> SHA-256 compatibility metadata;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or
  metadata-derived local identity is introduced.

## Regression coverage

`tests/test_phase350.py` covers ref/reflog fsync under target locks, directory
fences after unlock, file/directory failure propagation, old==new publication,
multi-ref nested directory deduplication and explicit hash-domain assertions.

`tests/test_phase350_integration.py` verifies that Phase347's established
`publish_packfile_uri_refs` monkeypatch seam still resolves to the now-durable
public publisher, so no large incremental-fetch rewrite is needed.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase347 / PR #324 head
  `71622528232af9499e53a5d7b55ccfbda7893863`;
- Phase347 authoritative Tests #2944: completed / success;
- Phase348 and Phase349 were occupied by independent workers before this clean
  rebuild;
- Phase350 was rechecked free immediately before branch creation;
- the old local Phase348 durability branch has no PR and will be reset back to
  the Phase347 base after this clean Phase350 commit is safely published;
- this phase intentionally remains stacked, open and unmerged.
