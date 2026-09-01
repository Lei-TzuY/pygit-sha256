# Phase 371 — Git-compatible custom clone origin names

Phase371 adds `pygit clone -o/--origin <name>` across the established ordinary,
shallow, partial, and protocol-v2 unborn-empty clone paths.

## Why this phase

The clone stack historically used `origin` as an implementation constant.  That
was observable after cloning even though native Git lets callers select the
upstream remote namespace with `-o/--origin`.  Phase331 made empty protocol-v2
unborn clones Git-compatible, but it also inherited that hard-coded name.

Phase371 keeps the mature transport/import paths unchanged and changes only the
local clone-finalization namespace.

## Behavior

`pygit clone -o upstream URL DIR` now finishes with:

- legacy remote configuration under `upstream`;
- Git-style `remote.upstream.*` configuration;
- remote-tracking refs under `refs/remotes/upstream/*`;
- branch upstream metadata naming `upstream`;
- native-object map metadata moved to the new remote namespace;
- partial-clone `promisor.json` remote metadata moved from `origin` to
  `upstream`;
- `extensions.partialClone` rewritten to `upstream` for partial clones.

The underlying non-empty transports may still use their historically stable
`origin` namespace while importing.  After the completed local clone is safe,
`retarget_completed_clone_remote()` reuses the existing Git-style remote rename
machinery to move every local namespace before final clone metadata is emitted.
This preserves existing transport/test seams and avoids duplicating the mature
clone implementations.

Explicit unborn-empty clones are different: no fetch/import occurs, so
`try_clone_explicit_unborn_remote()` receives the selected remote name and uses
it from repository initialization onward.  It never creates an intermediate
`origin` namespace.

## Native empty-clone compatibility

Native Git 2.47.3 probes with an empty SHA-256 bare repository whose unborn HEAD
is `refs/heads/topic/empty` establish:

- `git clone -o upstream ...` writes `remote.upstream.url`;
- default clone writes
  `+refs/heads/*:refs/remotes/upstream/*`;
- `branch.topic/empty.remote=upstream` and
  `branch.topic/empty.merge=refs/heads/topic/empty` are present;
- no `remote.origin.*` entry exists;
- `--single-branch` keeps branch upstream metadata but omits the fetch refspec;
- there are still no concrete refs or objects.

The same native probe is part of `tests/test_phase371.py` and runs on the CI
runner Git.

## Remote-name safety

Clone remote names are validated before network or filesystem mutation.  The
name must be non-empty, contain no whitespace/NUL, and form a safe namespace
inside `refs/remotes/<name>/...`.  Nested names such as `team/upstream` remain
valid when the repository refname validator accepts them.

## SHA-256-native / no-identity-shortcut invariants

This phase changes names, never object identity:

- remote transport identities remain genuine full native 40-hex SHA-1 values;
- local objects/refs remain genuine content-derived 64-hex SHA-256 values;
- native maps are moved as metadata, not regenerated from textual ids;
- no SHA-1 padding, truncation, translation, surrogate SHA-256, or zero-id
  branch tip is introduced;
- unborn clones remain metadata-only and perform no object fetch/import;
- partial promisor metadata keeps its existing native identities unchanged while
  only the configured remote key is renamed.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase331 / PR #308 head
  `40dacfe1dd2f05d6fb67864d291523f3add21036`;
- Phase331 authoritative Tests #2826: Python 3.9 / 3.13 both 2374 passed,
  Git 2.55.0;
- Phase370 was taken by the independent durable-object-publication stack during
  coordination, so this work moved to the first free namespace, Phase371;
- the independent packfile-URI/durability line and prior programmatic-unborn
  branches are not modified.

## Tests

`tests/test_phase371.py` covers ref-safe name validation, custom unborn default
and single-branch initialization, custom partial-empty config, explicit-branch
failure wording/cleanup, completed ordinary tracking/native-map retargeting,
partial promisor retargeting, ordinary/shallow/partial CLI paths, override-seam
preservation, invalid-name preflight, and native Git custom-origin empty clones.

This PR is intended to remain open and unmerged until explicitly requested.
