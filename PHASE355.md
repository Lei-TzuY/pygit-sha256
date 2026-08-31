# Phase 355 — Respect fetch refspecs when publishing push tracking state

Phase355 fixes the local remote-tracking side effect after a successful push.
The remote receive-pack transaction and native SHA-1 export were already able to
create a previously-unborn remote branch.  The remaining incompatibility was
local bookkeeping: pygit always wrote `refs/remotes/<remote>/<branch>` after a
successful branch push, even when Git's configured fetch mapping did not map
that remote ref into a local tracking ref.

## Native Git compatibility

A native Git 2.47.3 differential probe starts from an empty bare repository whose
HEAD targets `refs/heads/topic/empty`, clones it, creates the first local commit,
and pushes it.

Default clone:

- `remote.origin.fetch=+refs/heads/*:refs/remotes/origin/*`
- the first push creates `refs/heads/topic/empty` remotely
- Git also creates/updates local `refs/remotes/origin/topic/empty`

Empty `--single-branch` clone:

- native Git intentionally has no `remote.origin.fetch`
- the first push still creates `refs/heads/topic/empty` remotely
- Git does **not** invent local `refs/remotes/origin/topic/empty`
- branch upstream configuration may exist, but `@{u}` remains unresolved until a
  later fetch creates a tracking ref

Phase331 already models that empty-single-branch clone configuration.  Phase355
makes the push publication step respect it.

## Implementation

`pygit.push_tracking` is the shared policy boundary.

- `_match_refspec()` handles the exact and one-wildcard positive fetch refspec
  forms already emitted by pygit's clone/remote configuration.
- `tracking_branch_for_push()` maps a pushed `refs/heads/...` destination through
  `remote.<name>.fetch` and only accepts destinations under
  `refs/remotes/<name>/...`.
- `update_tracking_after_push()` applies successful create/update/delete results
  only to the mapped local tracking ref.
- `install_repository_push_tracking_support()` wraps the historical
  `Repository.push()` method without changing its receive-pack/export code.  It
  restores the pre-push same-name tracking ref when the legacy implementation
  wrote a ref that the modern fetch mapping does not select, then updates the
  actual mapped destination when necessary.

`push_ref()`, `delete_remote_ref()`, and `push_atomic_specs()` all use the same
helper, so single-ref, deletion, and atomic publication cannot diverge.

### Legacy compatibility

Very old pygit repositories created only through `Repository.add_remote()` do
not have Git-style `remote.<name>.url` / `.fetch` configuration.  Existing public
API tests expect their historical same-name tracking update.  Phase355 preserves
that behavior until such a repository has a Git-style remote URL entry.

Once a Git-style remote exists, an absent fetch refspec is intentional and
fails closed to **no local tracking mutation**, matching native Git's empty
single-branch clone.

## SHA-256/native-SHA1 boundary

This phase changes no transport identity or object conversion behavior.

- receive-pack old/new object IDs remain genuine full 40-hex native SHA-1 values
- local branch and remote-tracking refs remain genuine content-derived 64-hex
  SHA-256 values
- native-map persistence after push is unchanged
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced
- no fetch, object materialization, promisor mutation, or object-store write is
  added beyond the already-existing push exporter path

Only the **local ref selected for post-push bookkeeping** changes.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase352 / PR #329 head
  `1680f8e0e55ccd57f2567db8e0f4737123f49431`
- Phase352 authoritative Tests #2821: success
- Phase354 was already occupied by another worker
- Phase355 was collision-checked and free immediately before branch creation
- the independent packfile-URI/object-map stack through Phase353 is untouched

## Tests

`tests/test_phase355.py` covers:

- exact/wildcard refspec mapping and malformed/negative fail-closed behavior
- legacy add-remote compatibility
- modern no-fetch-refspec suppression
- custom mapped remote-tracking aliases
- `Repository.push()` restoration of stale unmapped tracking state
- `push_ref()` mapping behavior
- atomic multi-branch selective tracking publication
- atomic deletion preserving an unmapped local tracking ref
- native Git empty default-vs-single-branch first-push behavior

The execution container cannot reliably clone GitHub, so GitHub Actions Python
3.9 / 3.13 on the exact PR head is the authoritative full-suite gate.

This phase intentionally does not merge any pull request.
