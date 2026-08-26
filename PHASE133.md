# Phase 133 — `rev-list --parents`

Phase 133 adds Git-style parent metadata to the advanced `rev-list` plumbing without changing commit selection.

## Behavior

- `pygit rev-list --parents REV...` prints `commit parent...` records.
- Parent display composes with ordinary positive/negative revisions, `A..B`, one `A...B` symmetric range, `--all`, `--topo-order`, `--reverse`, `--skip`, `--max-count`, `--first-parent`, and Phase 131 side filters/markers.
- `--first-parent` restricts traversal only; an emitted merge still reports all parents stored in the commit, matching Git.
- Parents outside an excluded range remain visible on an emitted commit. Selection and topology metadata are separate concerns.
- Commits listed in `.pygit/shallow` are presented as synthetic roots and therefore report no parents.
- `--objects --parents` formats selected commit records with parents while tree/blob object records retain their existing pathname annotations. `--count` continues to count selected records rather than parent tokens.

## Implementation

`pygit.rev_list_parents` is a small reusable presentation layer over the Phase 121/131 revision-set engine. It does not introduce a second graph walk or mutate repository state. `parent_oids()` centralizes shallow-boundary-aware parent lookup so normal commit output and object-mode output share the same semantics.

## Compatibility boundary

Pygit still does not implement path-limited history simplification, so Git's more advanced `--parents` parent-rewriting rules for simplified path histories are outside this phase. For the revision-set traversal pygit supports, raw stored parents are emitted except at explicit shallow boundaries.

## Regression coverage

Phase 133 tests cover excluded range boundaries, merges under `--first-parent`, shallow roots, `--left-right`, `--objects`, `--count`, and installed CLI help.
