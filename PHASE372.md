# Phase 372 — Clean rebuild of Git-compatible custom clone origin names

Phase372 is a clean rebuild of the useful Phase371 `pygit clone -o/--origin <name>` work from the last exact-green clone base, Phase331.

## Why rebuild

Phase371 / PR #348 was based correctly on Phase331 and its production implementation was not implicated by the full-suite failure. The authoritative GitHub Actions Tests #3124 failed four focused Phase371 assertions because those tests treated `Repository.list_remotes()` as returning a list of names. The established API actually returns a mapping of remote names to URLs.

The failing assertions were therefore test-contract errors, not evidence that custom-origin production behavior was wrong. Rather than modifying the red Phase371 branch in place or stacking further work on a red head, Phase372 starts exactly from the Phase331 exact-green head and reapplies the production changes with corrected regression expectations.

## Behavior

`pygit clone -o upstream URL DIR` now finalizes clone state under `upstream` across ordinary, shallow, partial and protocol-v2 unborn-empty clone paths.

For completed non-empty clones, the mature transport/import paths may still use their stable temporary `origin` namespace. `retarget_completed_clone_remote()` then moves the finished local state to the selected clone remote:

- legacy remote configuration;
- Git-style `remote.<name>.*` configuration;
- remote-tracking refs;
- branch upstream configuration;
- native-object maps;
- partial-clone promisor remote metadata;
- `extensions.partialClone` when applicable.

For explicit unborn-empty clones no fetch/import happens, so the selected remote name is used from initialization onward and no intermediate `origin` namespace is created.

## Remote-name validation

Clone remote names are validated before network or filesystem mutation. The name must be non-empty, contain no whitespace or NUL, and form a safe namespace under `refs/remotes/<name>/...`. Nested names remain supported when the repository refname validator accepts them.

## Native Git compatibility

Native Git empty SHA-256 clone probes establish that `git clone -o upstream`:

- writes `remote.upstream.url`;
- uses `+refs/heads/*:refs/remotes/upstream/*` for an ordinary empty clone;
- sets `branch.<unborn>.remote=upstream` and the corresponding merge ref;
- leaves no `remote.origin.*` configuration;
- omits the fetch refspec for `--single-branch` while retaining upstream branch configuration;
- still creates no concrete branch ref or object for an unborn remote.

The Phase372 regression repeats this behavior on the CI runner Git.

## SHA-256-native / promisor invariants

This phase changes names, not object identity.

- remote-native transport identities remain genuine complete 40-hex SHA-1 values;
- local objects and refs remain genuine content-derived 64-hex SHA-256 values;
- native maps are moved as metadata, not regenerated from textual ids;
- no SHA-1 padding, truncation, translation or surrogate SHA-256 is introduced;
- no zero-id branch tip is fabricated for unborn clones;
- unborn clones remain metadata-only and perform no object fetch/import;
- partial promisor state preserves promised/resolved native identities while only the configured remote key changes.

## Coordination

- actual `main` at rebuild start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact clean base: Phase331 / PR #308 head `40dacfe1dd2f05d6fb67864d291523f3add21036`;
- Phase331 authoritative Tests #2826: Python 3.9 / 3.13 both 2374 passed, Git 2.55.0;
- Phase371 / PR #348 exact head `c570a055bd830773f43147e3c80cec242b4d2b44` had Tests #3124 failure with 2382 passed / 4 failed on Python 3.9;
- all four failures were incorrect `list_remotes()` list expectations; the established API returns `{remote: url}`;
- Phase372 was collision-checked and free before branch creation;
- independent Phase321+ packfile-URI/durability branches are untouched.

## Tests

`tests/test_phase372.py` preserves the full Phase371 custom-origin coverage while correcting the four remote-list expectations to the actual mapping contract. It covers ref-safe names, unborn default/single/partial initialization, explicit-branch error cleanup, ordinary native-map/tracking retargeting, promisor metadata retargeting, ordinary/shallow/partial CLI override seams, invalid-name preflight and native Git custom-origin empty clones.

This PR is intended to remain open and unmerged until explicitly requested.
