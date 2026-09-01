# Phase414: force-reset branches from previous checkout history

Phase414 extends the previous-checkout branch porcelain added in Phase413 with Git-compatible forced resets:

```text
pygit branch -f <branch> @{-N}
pygit branch --force <branch> @{-N}
```

## Semantics

The selector is expanded from HEAD checkout reflog history before any ref mutation. If it names a previous symbolic branch, that branch tip is resolved to its genuine local commit. If it names a previous detached checkout, the genuine 64-hex local SHA-256 commit OID is preserved directly.

Without `-f`, an already-existing branch is rejected instead of being silently overwritten. With `-f`/`--force`, an existing non-current branch is reset to the selected commit without switching HEAD or touching the worktree/index. Resetting the currently checked-out branch is rejected at the focused adapter boundary.

A literal `-` remains intentionally unsupported for `git branch`: native Git does not treat it as the checkout shorthand in this command family.

## Native Git parity

Native SHA-256 Git confirms that:

- `git branch -f spare @{-1}` resets `spare` to the previous checkout tip;
- HEAD remains attached to the current branch;
- stdout is empty on success;
- the branch reflog subject is `branch: Reset to @{-1}`;
- forcing the currently checked-out branch is rejected;
- repeating ordinary `git branch spare @{-1}` without force is rejected when `spare` already exists.

Git's `git-branch` documentation defines `-f`/`--force` as resetting an existing branch to its start point while retaining worktree safety restrictions.

## SHA-256-native boundary

This phase changes only porcelain routing and local ref mutation. It does not change object serialization, hashing, packfiles, protocol behavior, FETCH_HEAD, remote/native mapping, promisor state, or storage format. Local commit identity remains genuine content-derived 64-hex SHA-256. Selector text is metadata only and is never padded, truncated, rehashed, or substituted for an object identity.

## Coordination

- exact base: Phase413 / PR #374 corrected head `2adbc835002c5f469984bf6beeaad8891ee2894b`
- Phase413 authoritative Tests #3316 / run `33539220643`: success
- Phase414 namespace was collision-checked before branch creation
- independent bundle/init/protocol work is intentionally untouched
