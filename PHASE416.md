# Phase416 — `rev-parse @{-N}` previous-checkout revisions

Phase416 extends the exact-green Phase414 checkout-history stack into revision plumbing without touching the occupied Phase415 branch-move work.

## Supported forms

- `pygit rev-parse @{-N}`
- `pygit rev-parse --verify @{-N}`
- `pygit rev-parse --verify --quiet @{-N}` (and `-q`)
- `pygit rev-parse --abbrev-ref @{-N}`
- `pygit rev-parse --symbolic-full-name @{-N}`

Only these focused shapes are intercepted. Ordinary `rev-parse` expressions and unsupported option combinations remain owned by the existing implementation.

## Git compatibility

Git defines `@{-N}` as the N-th branch/commit checked out before the current checkout. Phase416 first expands that selector from HEAD reflog history, then resolves the selected local revision.

For a symbolic previous checkout, `--abbrev-ref` prints the branch name and `--symbolic-full-name` prints its full `refs/heads/...` name. For a previous detached checkout those symbolic modes emit no line, matching native Git. The ordinary form prints the selected commit object ID.

A native SHA-256 Git differential verifies `main -> topic -> main` and compares the ref-aware output modes. Local object IDs are independently content-derived, so tests compare semantics rather than assuming separately-created repositories have identical commit hashes.

## SHA-256-native invariant

This phase reads reflog/ref/object state only. It writes no objects, refs, HEAD, reflogs, index, worktree, packfiles, mappings, FETCH_HEAD, promisor state, or protocol state. Raw revision output is the genuine local 64-hex SHA-256 commit ID; selector text is metadata and is never padded, truncated, rehashed, or converted into a surrogate identity.

## Coordination

- exact base: Phase414 / PR #376 head `1a43f5383577cdc64ba4d4dac0aeb0febac9e72f`
- Phase414 Tests #3323 / run `33544930739`: success
- `phase415-branch-move-previous-selector` already existed when this phase started and was deliberately left untouched
- Phase416 namespace was collision-checked before branch creation
- no merge is performed by this phase
