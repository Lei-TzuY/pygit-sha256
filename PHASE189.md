# Phase 189 — Atomic fetch ref updates

Phase 189 adds Git-style `fetch --atomic` local-reference transactions on top of
Phase 188 multi-remote fetch orchestration.

## User-facing behavior

`pygit fetch --atomic <remote>` and atomic direct-URL fetches now guarantee that
all local reference mutations from that single fetch source either remain or
are rolled back together when an error occurs.

The transaction includes:

- loose refs under `.pygit/refs`
- packed refs in `.pygit/packed-refs`
- reflogs under `.pygit/logs`
- pruning performed earlier in the same fetch operation
- automatically followed tag refs
- explicit branch/tag/remote-tracking destinations

Downloaded objects and the per-remote native SHA map are intentionally not
rolled back. Git documents `--atomic` as an atomic transaction for *local refs*;
object transfer may already have completed before a local ref update is rejected.
`FETCH_HEAD` is written only after successful porcelain fetch completion, so a
ref-update exception naturally leaves it untouched.

## Single-remote boundary

Git rejects `--atomic` when fetching more than one remote. Phase 189 therefore
rejects the option with:

- `fetch --multiple`
- `fetch --all`
- argument-less fetch when `fetch.all=true`
- a `remotes.<group>` source

A single configured remote and a single direct HTTP(S) URL remain supported.

## Git compatibility checks

Current upstream `git-fetch` documentation defines `--atomic` as using an
atomic transaction to update local refs: either every ref update succeeds, or
on error none are updated.

Native Git 2.47.3 local probes additionally confirmed that
`git fetch --atomic --multiple ...` is rejected with the single-remote
restriction rather than attempting a cross-remote transaction.

## Architecture

`pygit.fetch_atomic.atomic_ref_updates()` snapshots the repository ref namespace,
packed refs, and reflogs before one fetch source runs. If any exception escapes
the fetch path, those files are restored before the exception is re-raised.
Successful scopes discard the snapshot.

This deliberately avoids widening `RefStore`, `SmartHttpClient`, or importer
APIs in this phase. The transaction wraps the established Phase 183–188 fetch
paths, so pruning, explicit refspecs, `--refmap`, direct URL fetches, tag policy,
and FETCH_HEAD behavior all share one rollback boundary.

## SHA-256-native design

Atomicity changes persistence orchestration only. All local refs continue to
contain pygit's 64-hex SHA-256 object IDs. Object serialization, the index,
pack conversion, native SHA maps, and the SHA-256-native to native SHA-1
smart-HTTP boundary are unchanged.

## Regression coverage

`tests/test_phase189.py` covers:

- rollback of loose branch/remote/tag refs
- rollback of branch reflogs
- rollback of `packed-refs`
- successful transaction persistence
- named-remote fetch rollback after a simulated partial update
- the contrasting non-atomic partial-update behavior
- direct-URL fetch rollback
- rejection with `--multiple`, `--all`, and remote groups
