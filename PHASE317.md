# Phase 317: Initialize pristine repositories from unborn remote HEAD

Phase317 turns Phase315's explicit protocol-v2 `unborn HEAD` metadata into a safe local reference-state initialization primitive. It intentionally stops at the reference layer: no fetch, importer, checkout, object materialization, or promisor mutation is involved.

## Native Git baseline

Local Git 2.47.3 was probed with a SHA-256 bare repository whose initial branch was `topic/empty`, followed by a normal clone. Native Git produced:

- `.git/HEAD` -> `ref: refs/heads/topic/empty`;
- `git symbolic-ref HEAD` -> `refs/heads/topic/empty`;
- empty `git show-ref` output;
- no `.git/refs/heads/topic/empty` file;
- no `.git/logs/HEAD` reflog;
- zero object files and `git count-objects -v` count/in-pack values of zero.

An explicit `git clone --branch main` against an empty remote fails because no concrete remote branch exists, while the default clone and `--single-branch` preserve the remote unborn HEAD target. Phase317 therefore models metadata-only default initialization, not an invented branch object.

## Implementation

New module: `pygit/unborn_init.py`.

`initialize_empty_remote_head(repo, result)` accepts the `ProtocolV2LsRefsResult` introduced in Phase315 and validates the complete trust boundary before changing local state:

1. exactly `HEAD` is explicitly unborn;
2. remote HEAD is not simultaneously concrete;
3. there are no concrete remote refs;
4. HEAD has exactly one symbolic target under `refs/heads/`;
5. dangerous/invalid branch-ref forms are rejected;
6. local HEAD is unresolved and symbolic;
7. the destination has no branch/tag/remote-tracking refs;
8. the local object database contains no files;
9. `.pygit/promisor.json` is absent.

Only after all checks pass is `.pygit/HEAD` changed. Publication uses `HEAD.lock`, `fsync`, and `os.replace`, and deliberately creates no reflog. Reapplying the same target is idempotent.

The historical `Repository.clone()` protocol-v0 path is intentionally unchanged. Phase317 is an additive bridge from validated v2 metadata to local repository state; a later orchestration phase can compose it into full clone selection without forcing unrelated v0 clones through a new network probe.

## SHA-256-native invariants

Unborn remains reference-state metadata, not an object identity:

- no 64-hex local OID is fabricated;
- no zero OID is written as a branch tip or HEAD surrogate;
- no native 40-hex SHA-1 is invented, padded, truncated, or translated;
- no local object is written;
- no native object is fetched or materialized;
- no promisor metadata is created or modified;
- the target branch ref remains absent until a real commit eventually exists.

## Regression coverage

`tests/test_phase317.py` covers nested unborn branches, direct composition with Phase315 parsing, idempotence without a reflog, remote metadata conflicts, invalid targets, absent explicit-unborn metadata, resolved local HEAD, local object state, promisor preservation/rejection, lock contention, and a native Git SHA-256 empty-clone regression.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase315 / PR #291 green head `b1b0f7f2d9daff601cb6aa1ad89ab438f4d2f32c`;
- Phase315 Tests #2728: Python 3.9 / 3.13 both 2334 passed;
- Phase316 / PR #292 is an independent line and is untouched;
- Phase318 / PR #293 was already occupied by another worker and is untouched;
- `phase317-empty-remote-unborn-initialization` was rechecked before work and contained only the Phase315 exact base.

Full GitHub Actions Python 3.9 / 3.13 matrix must be green on the final exact head before Phase317 is considered complete.
