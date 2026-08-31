# Phase 337 — bootstrap an unborn clone on its first pull

Phase331 made an explicitly empty protocol-v2 remote cloneable without inventing
an object identity. Phase335 then made the first later `fetch` Git-compatible:
the newly born upstream can be imported while the local branch intentionally
remains unborn. This phase completes the ordinary pull lifecycle.

## Behavior

`pygit pull` now detects the narrow Phase331/335 state before invoking generic
merge:

- `HEAD` is symbolic to a local branch;
- that branch has no local tip;
- `branch.<name>.remote` and `branch.<name>.merge` name the selected remote and
  the same upstream branch;
- the remote is not a persisted partial/promisor remote.

The command runs the normal porcelain fetch under Phase335's command-scoped
unborn selection. This preserves both native empty-clone fetch shapes:

- default clone: the configured wildcard refspec creates
  `refs/remotes/origin/<branch>`;
- empty `--single-branch` clone: the intentionally absent persistent fetch
  refspec remains absent, the source is imported only for FETCH_HEAD/native-map
  purposes, and no remote-tracking ref is created.

The fetched source ref's local identity is then required to be a genuine local
64-hex SHA-256 commit before any local branch publication.

## Checkout / publication boundary

Native Git fetches first and only later attempts the initial checkout. If an
untracked path would be overwritten, FETCH_HEAD and a normal remote-tracking ref
may already have advanced while the local branch remains unborn. Phase337 keeps
that boundary.

Before checkout it validates:

- the local branch is still unborn after fetch;
- the index is empty, so pygit's index-rebuilding checkout cannot discard staged
  user state;
- no target leaf already exists as an untracked path;
- no file or symlink ancestor can redirect/block a target path.

After preflight it populates the worktree/index using the established checkout
primitive and only then publishes the local branch with reflog message
`initial pull`. `RefStore.set_branch()` records both the branch and HEAD reflog
from the all-zero local sentinel to the real local SHA-256 commit while HEAD
itself remains symbolic.

The generic `Repository.merge()` contract is deliberately unchanged: direct
attempts to merge into an empty repository still fail. The special transition
belongs to pull orchestration, where successful fetch/upstream context is known.

## Partial clone boundary

Native Git automatically reapplies a persisted partial-clone filter on later
fetch/pull. pygit's established filtered fetch path still requires explicit
transport composition. Phase335 therefore refused to silently fall back to an
unfiltered first fetch, and Phase337 preserves that invariant: an unborn
persisted partial/promisor clone raises before any unfiltered network request.
A later phase can compose the persisted filter with this bootstrap without
weakening object-identity guarantees.

## Native Git compatibility

SHA-256 native probes establish:

1. default empty clone -> remote first commit -> `git pull` creates both local
   branch and `origin/<branch>`, populates the worktree, and writes `initial pull`
   to HEAD and branch reflogs;
2. empty `--single-branch` clone -> first pull creates only the local branch,
   keeps `remote.origin.fetch` absent, and uses FETCH_HEAD as the fetched source;
3. if a target path conflicts with an untracked local file, pull fails after
   fetch, remote/FETCH_HEAD state may exist, but the local branch stays unborn
   and the local file is preserved.

The CI regression repeats those observations using the runner's native Git.

## SHA-256-native invariants

- remote/native transport ids remain complete 40-hex SHA-1 values;
- imported repository identities remain complete content-derived 64-hex SHA-256;
- the bootstrap target is taken only from the already imported local object
  graph and is re-read as a `CommitObject`;
- FETCH_HEAD contains the established local 64-hex identity;
- the unborn branch is never represented by a zero object id;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived local
  identity is introduced;
- failed checkout/preflight never publishes the local branch tip.

## Coordination

- actual `main` at start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase335 / PR #311 head
  `55a13732eaabd8a0988daf05dd3935c653b7703a`;
- Phase335 authoritative Tests #2849: Python 3.9 / 3.13 both 2381 passed,
  Git 2.55.0;
- Phase333/334 and Phase336 are occupied by the independent incremental
  packfile-URI/object-map stack;
- Phase337 was collision-checked before branch creation;
- no Phase333 work from this line was committed after a same-number collision was
  detected.

## Tests

`tests/test_phase337.py` covers default and single-branch success, local SHA-256
publication, FETCH_HEAD, remote-tracking differences, HEAD/branch reflogs,
non-conflicting untracked preservation, exact-path and symlink-ancestor
conflicts, staged-state preservation/fail-closed behavior, partial-clone
no-network rejection, resolved/mismatched non-activation, non-commit targets,
checkout failure, pull CLI short-circuiting, and native Git SHA-256 lifecycle
regressions.

The execution container cannot reliably clone the GitHub repository, so the
exact-head GitHub Actions Python 3.9 / 3.13 matrix is the authoritative full-suite
gate. This phase remains open and unmerged.
