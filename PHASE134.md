# Phase 134 — `rev-list --children`

Phase 134 adds Git-style child metadata to the advanced `rev-list` traversal introduced in earlier phases.

## Behavior

- `pygit rev-list --children REV...` prints `commit child...` records.
- Child links are derived from the complete selected revision walk before `--skip`, `--max-count`, and `--reverse` presentation transforms.
- `--first-parent` restricts both traversal and child edges to first-parent links.
- Excluded/uninteresting commits do not contribute child records to the selected graph.
- Shallow commits remain synthetic roots, so their hidden stored parents do not receive child links through the shallow boundary.
- `--children` composes with symmetric-range side markers, `--objects`, `--objects-edge`, ordering, limits, and `--count`.
- `--parents` and `--children` are mutually exclusive, matching Git.

## Compatibility notes

Git documents `--children` as printing each commit followed by its children. Native behavior also establishes child metadata before output limiting: a child removed by `--skip` can still be printed beside its parent, while `--reverse` changes record order without reversing the child list. Phase 134 preserves those details instead of deriving child links only from the final displayed slice.

Pygit still does not claim Git's full path-limited history simplification/parent-rewriting engine; this phase applies child presentation to pygit's existing revision-set semantics.

## Verification

Regression coverage exercises merge graphs, excluded ranges, skip-before-presentation behavior, reverse ordering, first-parent traversal, shallow boundaries, left/right markers, object enumeration, count mode, CLI grammar, and installed help.
