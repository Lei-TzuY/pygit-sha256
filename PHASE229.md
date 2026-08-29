# Phase229 — Promisor-aware stash restore batching

Phase229 removes partial-clone one-object-at-a-time demand fetching from stash
restore operations while keeping the historical stash implementation in charge
of validation, mutation, index restoration, stash refs, and return values.

## Covered operations

- `stash pop`
- `stash apply`
- `stash apply --index` / `restore_index=True`

The wrapper first preserves the existing stash-entry and clean-worktree gates.
Only after the operation is known to be applicable does it collect unresolved
promised blobs from the snapshots the historical restore will consume.

`stash pop` prefetches the stash commit snapshot.  `stash apply --index`
prefetches one deduplicated union of the stash snapshot and the stash's index
parent snapshot when that second parent is a commit.

## Failure atomicity

Promisor materialization happens before `_tree_entries()`,
`_remove_worktree_file()`, `_restore_tree()`, index rewriting, or `refs/stash`
mutation.  A failed promisor request therefore leaves the worktree, index, HEAD,
and stash ref untouched.

Dirty-worktree and invalid-stash-index failures remain local validation errors;
Phase229 does not fetch stash content for an operation that will be rejected.
Phase228 may independently prefetch the current HEAD while `status()` evaluates
cleanliness, because that is status demand rather than stash-restore demand.

## Identity model

Phase229 does not introduce placeholder or surrogate SHA-256 object ids.
Foreign tree entries continue to hold native Git SHA-1 promises until their
content is fetched.  Materialization writes the real content-derived SHA-256
object and atomically transitions the promisor state from `promised` to
`resolved`.

The current tree representation requires a real local SHA-256 id for each
retained entry when a snapshot is flattened.  For that reason Phase229 batches
complete stash/index snapshots instead of claiming path-only materialization.

## Compatibility

Phase229 composes with the existing promisor stack:

- Phase213 single-object compatibility
- Phase214 multi-object fetch batching
- Phase221 multi-promisor fallback and shrinking wants
- Phase222 primary-promisor-last ordering
- per-remote `serverOption`
- Phase228 status prefetch for the cleanliness gate

Ordinary repositories and stash operations with no unresolved promises stay on
the original network-free path.

## Verification

Focused tests use a clean local HEAD plus a foreign filtered stash graph.  The
stash tree contains two promised blobs; its index parent contains a third unique
blob and shares one blob with the stash tree.  Tests verify:

- `stash pop` fetches only the two stash-snapshot blobs in one batch
- `stash apply --index` fetches three unique blobs in one batch
- shared blobs are deduplicated
- ordered remote server options are preserved
- promisor state transitions to resolved objects
- prefetch failure occurs before worktree/index/ref mutation
- dirty worktrees and invalid stash indices do not trigger stash prefetch
- ordinary local stashes stay outside the promisor layer

No protocol, pack, tree serialization, ref format, index format, or SHA-256
identity changes are made in this phase.
