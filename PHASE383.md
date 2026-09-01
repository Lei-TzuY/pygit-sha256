# Phase383 — Git-compatible `init -b/--initial-branch`

Phase383 adds Git-compatible initial-branch selection to `pygit init` without touching the active bundle-URI or loose-object durability work.

## Behavior

`pygit init -b <name> [DIR]` and `pygit init --initial-branch=<name> [DIR]` now select the symbolic unborn `HEAD` of a newly-created repository.

The implementation validates the actual stored reference, `refs/heads/<name>`, before creating the destination. This matters because native `git init -b` accepts some names such as `-topic` that `git check-ref-format --branch` intentionally rejects for command-line safety even though `refs/heads/-topic` is a valid ref.

Invalid names fail before filesystem mutation. No branch file, zero OID, object, or reflog record is synthesized for the unborn branch.

Reinitializing an existing repository preserves its current `HEAD`. If `-b/--initial-branch` is supplied during reinit, pygit emits a Git-style warning and ignores the requested name instead of retargeting the repository.

The legacy no-option behavior remains unchanged: a new pygit repository defaults to unborn `main`.

## Native Git differential

The Phase383 regression suite invokes the runner's native Git and verifies:

- `git init -b feature/api` and pygit create the same symbolic `HEAD` target;
- reinitialization with a different `-b` leaves the original `HEAD` unchanged and reports that the option was ignored;
- both implementations reject the `bad..name` ref shape.

The public Git documentation defines `-b <branch-name>` / `--initial-branch=<branch-name>` for choosing the initial branch of a newly-created repository and states that re-running `git init` is safe and does not overwrite existing repository state.

## SHA-256-native invariants

This phase changes only unborn symbolic-HEAD selection. It creates no object IDs and does not alter object serialization, refs containing object IDs, native SHA-1 compatibility mappings, FETCH_HEAD, packfiles, or object storage.

Local object identity therefore remains genuine content-derived 64-hex SHA-256. Remote/native identities remain genuine complete 40-hex SHA-1 wherever the interoperability layer requires them. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or zero-OID unborn branch is introduced.
