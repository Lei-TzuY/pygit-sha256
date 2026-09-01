# Phase403 — preserve refspec normalization slash boundaries

Phase403 tightens the Phase401/402 `check-ref-format --refspec-pattern` path so `--normalize` matches native Git at the trailing-slash boundary.

## Problem

The shared `normalize_refname()` helper removes all empty slash-separated components. That is convenient for internal normalization, but Git's `check-ref-format --normalize` contract is narrower: it removes leading slashes and collapses adjacent slashes *between* refname components. It does not turn an otherwise-invalid trailing slash into a valid refname.

Before this phase, for example, pygit could normalize `refs/heads/*/` to `refs/heads/*` and accept it in refspec-pattern mode, while native Git rejects the original trailing-slash form.

## Implementation

`check_refspec_pattern()` now rejects a trailing slash before invoking the shared normalizer. This keeps the fix local to the Phase401 refspec CLI surface and avoids silently changing the semantics of other internal callers of `normalize_refname()`.

The valid normalization behavior remains unchanged:

- leading slash runs are removed;
- repeated internal slash runs are collapsed;
- zero or one `*` remains allowed only in explicit `--refspec-pattern` mode;
- all ordinary refname safety checks continue to be delegated to `check_ref_format()`.

The deprecated `--print` spelling inherits the same boundary because Phase402 aliases it to `--normalize` before dispatch.

## Native Git differential

The regression suite compares pygit with the runner's native Git for valid and invalid normalized refspec patterns. In particular:

- `//refs//heads//*` normalizes successfully to `refs/heads/*`;
- `refs/heads/*/` is rejected;
- repeated internal slashes followed by a trailing slash are still rejected;
- slash-only inputs remain invalid.

Git's `git-check-ref-format` documentation describes `--normalize` as removing leading slashes and collapsing adjacent slashes between name components; successful output is still conditioned on the resulting refname being valid.

## SHA-256-native invariant

This phase only changes textual refspec validation. It creates or modifies no objects, refs, reflogs, native maps, `FETCH_HEAD` records, packfiles, or promisor state. Local object identity remains genuine content-derived 64-hex SHA-256. Remote/native interoperability identities remain genuine complete 40-hex SHA-1 where Git compatibility requires them. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- exact base: Phase402 head `d796b4237c424bcf15a8e950fadc52150e0a4041`
- Phase402 GitHub Actions Tests #3222 completed successfully before Phase403 started
- Phase403 namespace was collision-checked immediately before branch creation
- active clone/init/loose-object stacks were left untouched
