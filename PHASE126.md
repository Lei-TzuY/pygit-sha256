# Phase 126 — checkout-index conflict-stage extraction

Phase 126 connects the Phase 56 `checkout-index` plumbing to the persistent multi-stage index introduced in Phase 124. Conflict records can now be materialized directly from stages 1, 2, or 3 without changing `HEAD`, refs, the index, or the conflict state.

## Stage selection

`checkout-index` keeps stage 0 as its default for full backward compatibility and adds explicit stage selection:

```text
--stage=0    normal staged entry (default)
--stage=1    merge base
--stage=2    ours
--stage=3    theirs
```

For example:

```bash
pygit checkout-index --stage=1 --prefix=base/ conflict.txt
pygit checkout-index --stage=2 --prefix=ours/ conflict.txt
pygit checkout-index --stage=3 --prefix=theirs/ conflict.txt
```

The three files can then be inspected or passed to external comparison/merge tooling without first resolving the index conflict.

## Selection semantics

Pathspec matching is stage-aware. A path only matches when an entry exists at the requested stage; pygit never silently falls back to stage 0 or another conflict stage. `--all --stage=N` similarly materializes every path present at exactly stage `N`.

This matters for asymmetric conflicts such as add/delete cases where not every path has all three stages.

## Compatibility and safety

The Phase 56 default remains unchanged: omitting `--stage` reads only stage 0. Existing force, prefix, executable-bit, symlink, submodule rejection, path traversal, and `.pygit` protection behavior is reused for conflict-stage entries.

Checkout remains read-only with respect to repository metadata. Materializing a conflict side does not clear stages 1-3, create a stage-0 entry, move refs, or write new objects.

## CLI architecture

A focused `pygit.checkout_index_cli` adapter now handles the installed command through the modern `application.py` router. The older runtime handler remains available to internal callers and retains its historical stage-0 contract, while normal `python -m pygit checkout-index ...` invocations use the stage-aware adapter.

## Scope boundary

This phase selects one index stage at a time. Native Git's `checkout-index --stage=all --temp` behavior also requires temporary-file mapping output; that remains a future extension rather than inventing incompatible filename suffixes.

Phase 126 also does not yet make high-level `merge`, `cherry-pick`, or `rebase` porcelain automatically populate conflict stages. It makes the stage-aware inspection path ready for those workflows once they migrate to the Phase 124 index model.

## Regression coverage

`tests/test_phase126.py` covers base/ours/theirs extraction, stage-filtered `--all`, preservation of historical stage-0 behavior, asymmetric missing-stage errors, index immutability, the focused Python CLI adapter, installed `python -m pygit` routing, and invalid-stage rejection.
