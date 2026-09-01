# Phase 400 — `clone --branch <tag>` with detached SHA-256 HEAD

Phase400 closes a long-standing clone compatibility gap: native Git accepts
`git clone -b/--branch <name>` when `<name>` names a tag, while pygit's existing
clone paths treated the option only as `refs/heads/<name>`.

## Scope

This phase adds the ordinary full-clone tag case on top of the exact-green
Phase331 clone orchestration.

- branches keep precedence when a branch and tag share the same short name;
- a matching lightweight tag clones successfully and detaches HEAD at its
  commit;
- a matching annotated tag is imported as a real local `TagObject`, while HEAD
  detaches at the fully peeled commit;
- ordinary non-single tag clones retain the normal wildcard branch fetch refspec
  and remote-tracking branches;
- `--single-branch -b <tag>` uses the native Git-style tag refspec
  `+refs/tags/<tag>:refs/tags/<tag>` and does not create `origin/HEAD`;
- detached tag clones do not create `branch.<tag>.remote` / `.merge` upstream
  metadata;
- `--no-checkout` still creates the detached reference/object state but leaves
  the worktree unpopulated;
- a tag that does not peel to a commit fails and rolls back the newly created
  destination.

`--depth` and `--filter` tag clones are deliberately deferred.  They cross the
stable shallow/promisor boundaries and need a dedicated composition rather than
silently reusing the ordinary full-clone importer.

## Native Git reference

Local SHA-256 Git 2.47.3 probes established the observable behavior for both
lightweight and annotated tags.  For an annotated `release` tag:

- `git clone -b release` succeeds;
- `HEAD` is detached;
- `HEAD == refs/tags/release^{}`;
- `refs/tags/release` remains the annotated tag object and differs from HEAD;
- default/full clone keeps
  `+refs/heads/*:refs/remotes/origin/*`;
- no `branch.release.*` config exists;
- `--single-branch -b release` instead persists
  `+refs/tags/release:refs/tags/release` and omits `origin/HEAD`;
- the detached HEAD reflog records the zero-to-peeled-commit clone transition.

`tests/test_phase400.py` repeats the annotated-tag detached-HEAD/config contract
against the CI runner Git 2.55.0.

## Implementation

`pygit/clone_tag.py` is an additive protocol-v2 orchestration layer.

1. perform v2 ref discovery;
2. return `None` for v0, unknown names, or a same-named branch so existing clone
   paths retain ownership;
3. fetch the selected tag graph (or the ordinary full advertisement);
4. import content through the existing `TagPreservingNativeImporter`;
5. publish local tags and, for ordinary full clone, remote-tracking branches;
6. resolve the advertised peeled tag identity to the imported local object and
   require that object to be a commit;
7. detach HEAD with the existing `RefStore.set_head_detached()` API;
8. populate the worktree unless `--no-checkout` was requested.

The CLI only invokes this path for an explicit `-b/--branch` when neither
`--depth` nor `--filter` is active.  Phase331's override-seam policy is retained:
if callers replace the selected clone implementation, hidden protocol-v2
preflights remain disabled.

## SHA-256-native invariants

Remote advertisement/fetch identities remain genuine full 40-hex SHA-1 OIDs.
The new tag path never pads, truncates, or transforms one into a local identity.

- commits, trees, blobs, and annotated tags cross the existing content importer;
- local object IDs are therefore full content-derived 64-hex SHA-256 values;
- detached HEAD is written only with the imported peeled local commit SHA-256;
- the local annotated tag ref names the imported local `TagObject` SHA-256;
- the native/local map contains only identities actually produced by content
  conversion;
- no metadata-derived surrogate SHA-256 is introduced;
- no promisor state is created by this ordinary full clone path.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase331 / PR #308 head
  `40dacfe1dd2f05d6fb67864d291523f3add21036`;
- Phase331 authoritative Tests #2826: Python 3.9 / 3.13 both 2374 passed,
  Git 2.55.0;
- Phase332 through the high 300s were already heavily occupied by independent
  object-map, packfile-URI, unborn-bootstrap, FETCH_HEAD durability, and clone
  option work;
- Phase400 was collision-checked immediately before branch creation;
- this phase does not merge or modify those independent stacks.

## Tests

Focused regressions cover:

- lightweight tag detached HEAD;
- annotated tag preservation plus peeled commit HEAD;
- branch-over-tag precedence;
- full-clone remote branches and default remote HEAD;
- single-tag refspec and no remote HEAD;
- `--no-checkout`;
- non-commit tag rollback;
- protocol-v0 fallback without local mutation;
- public CLI routing/short-circuit behavior;
- explicit deferral of `--depth` and `--filter` tag composition;
- native Git SHA-256 annotated-tag clone behavior.

GitHub Actions Python 3.9 / 3.13 on the exact PR head is the authoritative full
suite gate because the execution container cannot reliably clone the repository
from github.com.
