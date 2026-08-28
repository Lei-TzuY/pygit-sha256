# Phase192 — fetch dry-run sandbox

Phase192 adds Git-style `fetch --dry-run` on top of the repaired Phase191 base.

## Behavior

- `pygit fetch --dry-run ...` executes the established fetch selection and validation path so refspec, pruning, tag, force, atomic, direct-URL, and multi-remote behavior stays centralized.
- The complete `.pygit` directory is snapshotted before the dry run and restored afterward, including on exceptions.
- Objects, refs, reflogs, packed refs, config, remote native-SHA maps, and other repository-local metadata therefore finish unchanged.
- `FETCH_HEAD` writing is explicitly disabled while dry-run is active, even if `--write-fetch-head` was supplied.
- `--dry-run` is recognized as an option only before the standard `--` option terminator.
- The worktree is not snapshotted because fetch does not update it.

## Git compatibility

Current upstream `git-fetch` documentation defines `--dry-run` as showing what would be done without making changes. It also explicitly states that `FETCH_HEAD` is never written under `--dry-run`.

The implementation deliberately reuses the mature real fetch path rather than creating a second planner whose behavior could drift from configured refspecs, pruning, tag following, force checks, direct URL fetches, or multi-remote orchestration.

## SHA-256-native design

Dry-run changes persistence orchestration only. Repository-local objects and refs remain SHA-256-native, and the native SHA-1 smart-HTTP compatibility boundary is unchanged. Any transient objects/native-map changes produced while validating the operation are removed when the `.pygit` snapshot is restored.

## Tests

`tests/test_phase192.py` covers whole-repository restoration after success and failure, FETCH_HEAD suppression, transparent non-dry-run forwarding, composition with atomic fetch, and `--` option-terminator handling.
