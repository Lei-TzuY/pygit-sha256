# Phase402: `check-ref-format` CLI parity

Phase402 completes the remaining small command-line compatibility gaps around the reference-name validator without changing pygit's SHA-256-native object model.

## Behavior added

- `--no-allow-onelevel` is accepted alongside `--allow-onelevel`; as in native Git, the last occurrence wins.
- deprecated `--print` is accepted as an alias for `--normalize` and emits the normalized refname.
- `--print` composes with `--refspec-pattern` exactly like `--normalize`.
- `--branch` now prints the checked branch name on success, matching native Git's plumbing output.
- `--branch` remains its own synopsis and rejects `--allow-onelevel`, `--no-allow-onelevel`, `--normalize`, `--print`, and `--refspec-pattern` combinations.

## Native Git differential

The regression suite probes the runner's native `git check-ref-format` for option precedence, `--print` output, refspec-pattern normalization, branch output, and branch-mode incompatibilities, then compares pygit's result.

The behavior follows the Git manual: the default is `--no-allow-onelevel`, `--[no-]allow-onelevel` controls one-level names, and `--print` is the deprecated spelling of `--normalize`.

## SHA-256-native invariants

This phase only changes command-line parsing and textual refname validation/output. It creates or rewrites no object IDs, refs, reflogs, mappings, `FETCH_HEAD` records, or packfiles. Local identity remains genuine content-derived 64-hex SHA-256; interoperability identities remain genuine complete 40-hex SHA-1 where native Git compatibility requires them. No padding, truncation, textual-ID rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

Phase402 was collision-checked before branch creation and is based on the exact Phase401 head after Phase401's full GitHub Actions test run completed successfully. It intentionally remains a separate, unmerged pull request.
