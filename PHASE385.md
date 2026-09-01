# Phase385 — Git init storage-format environment defaults

Phase385 extends Phase384's explicit `pygit init` storage-format contract with Git's initialization environment defaults while preserving pygit's deliberately narrower SHA-256-native storage model.

## Supported environment variables

`pygit init` now reads:

- `GIT_DEFAULT_HASH`
- `GIT_DEFAULT_REF_FORMAT`

when the corresponding command-line option is absent.

The precedence is intentionally the same shape as native Git:

1. explicit `--object-format` / `--ref-format` command-line option;
2. corresponding Git environment variable;
3. pygit's built-in storage invariant (`sha256` / `files`).

An explicitly supplied CLI value overrides even an incompatible environment value. For example, `GIT_DEFAULT_HASH=sha1 pygit init --object-format=sha256` selects the explicit SHA-256 contract, and `GIT_DEFAULT_REF_FORMAT=reftable pygit init --ref-format=files` selects files refs.

## Fail-before-mutation boundary

Pygit still does not implement a local SHA-1 object store or reftable refs. Therefore an environment default requesting an unsupported mode fails before `Repository.init()` runs and before the destination directory is created.

This includes empty environment values. Native Git treats an explicitly present empty `GIT_DEFAULT_HASH` as an invalid hash algorithm rather than as an absent variable, so pygit likewise does not silently fall back to SHA-256 when the environment variable is present but empty.

Examples that fail before mutation unless overridden by the corresponding CLI option:

- `GIT_DEFAULT_HASH=sha1`
- `GIT_DEFAULT_HASH=SHA256`
- `GIT_DEFAULT_HASH=`
- `GIT_DEFAULT_REF_FORMAT=reftable`
- `GIT_DEFAULT_REF_FORMAT=`

Each option overrides only its own environment source. An explicit object format does not suppress an incompatible ref-format environment value, and vice versa.

## Native Git differential

The Phase385 regression suite asks the CI runner's Git to verify both precedence paths:

- `GIT_DEFAULT_HASH=sha256 GIT_DEFAULT_REF_FORMAT=files git init -q ...` produces a SHA-256/files repository;
- `GIT_DEFAULT_HASH=sha1 GIT_DEFAULT_REF_FORMAT=reftable git init -q --object-format=sha256 --ref-format=files ...` still produces SHA-256/files because the command line wins.

Local pygit tests exercise the same precedence shape and verify that resulting object IDs remain genuine 64-hex content-derived SHA-256.

## SHA-256-native invariants

These environment variables are input selection signals only. Phase385 does not introduce selectable local SHA-1 storage or reftable support. Remote/native compatibility identities remain genuine complete 40-hex SHA-1 where interoperability requires them; local objects remain genuine content-derived complete 64-hex SHA-256.

No padding, truncation, identifier-text rehashing, surrogate SHA-256, zero OID, or fake backend metadata is introduced.

## Coordination

- exact base: Phase384 / PR #358 head `77b54a9abf6126a0c254c511767f8a72d0c26f19`;
- Phase384 GitHub Actions Tests #3195 / run `33464180125`: success; 2416 passed on Python 3.9 and Python 3.13; CI Git 2.55.0;
- Phase385 was collision-checked immediately before branch creation and was free;
- `--separate-git-dir` remains deliberately deferred because pygit currently has many direct `.pygit` directory assumptions and a CLI-only pointer implementation would be incomplete.

This phase is intended to remain an open, unmerged stacked pull request.
