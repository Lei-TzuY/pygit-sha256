# Phase 273 — annotated-tag-aware `rev-list object:type` filtering

Phase273 closes the long-standing annotated-tag object traversal gap in the
line-oriented/count `rev-list --filter=object:type=...` stack.

## Scope

The existing promisor inventory is intentionally commit-rooted. Before this
phase, annotated tag objects named by positive revisions or `--all` refs were
not present in that inventory, so `object:type=tag` was rejected and existing
`commit|tree|blob` filters also missed Git's provided-tag exemption.

Phase273 adds a small composition adapter that:

- supports `--filter=object:type=tag` in line and count modes;
- walks nested annotated-tag chains from positive revision roots;
- walks annotated tag refs under `--all` in deterministic ref order;
- preserves the peeled positive commit as a provided object unless
  `--filter-provided-objects` is used;
- treats annotated tag objects themselves as provided objects for
  `commit|tree|blob` filters unless `--filter-provided-objects` is used;
- preserves tag objects for `object:type=tag` even when provided objects are
  filtered, because they match the requested type;
- prints the tag object's embedded tag name when object names are enabled;
- keeps `--filter-print-omitted` empty for `object:type`, matching Git;
- includes tag objects in filtered `--count` results;
- keeps object-edge records as an independent presentation channel.

## Native Git compatibility

Deterministic native SHA-256 probes establish these observable contracts.
For an annotated `v1` pointing at a commit:

- `object:type=tag v1` => provided commit, then tag object;
- adding `--filter-provided-objects` => tag object only;
- `object:type=commit v1` => matching commits, then the provided tag object;
- `object:type=tree|blob v1` => provided commit, provided tag, then matching
  snapshot objects;
- adding `--filter-provided-objects` to those existing filters removes the tag
  exemption;
- a nested `v2 -> v1 -> commit` explicit revision prints outer tag then inner
  tag;
- `--all` deduplicates nested tag chains according to sorted ref traversal;
- `v1^{}` is already peeled and does not reintroduce the tag object;
- `object:type` does not populate the omitted-object set.

GitHub Actions runs dedicated Git 2.55.0 SHA-256 probes for both the new tag
filter and the provided-tag behavior of `commit|tree|blob`.

## SHA-256-native boundary

Annotated tag objects are already ordinary local Git objects in pygit's object
store. Every emitted commit/tag/tree/blob identity is therefore a genuine
64-hex local SHA-256 object ID. No foreign transport SHA-1 is padded,
translated, or promoted into repository-visible output.

The adapter reuses the existing metadata-only missing-object traversal and does
not add any materialization path.

## Explicit follow-ups

Tag placement is deliberately still rejected for:

- structured `-z` output;
- `--in-commit-order` output;
- `--disk-usage`.

Those modes have distinct placement/accounting contracts and should be enabled
only with focused native probes rather than by silently appending tag records.

## Coordination

- actual `main` remained at `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- base is Phase271 / PR #249 exact-green head
  `e1460991c01d51fde2f2ccc656214195ce6ff2d4`;
- Phase272 was already occupied by an unfinished sibling branch based on
  Phase270, with one production-only disk-usage commit and no completed PR/CI;
- Phase272 was not modified or used as a base;
- Phase273 was free when this work began.

## Verification

Focused tests cover:

- explicit annotated tag roots;
- nested tag chains;
- internal tag-name presentation and `--no-object-names`;
- `--filter-provided-objects` semantics;
- counts;
- `--all` deduplication;
- peeled `^{}` expressions;
- empty object:type omission sets;
- provided-tag behavior for existing commit/tree/blob filters;
- explicit NUL / ordered / disk-usage follow-up guards;
- native Git 2.55 SHA-256 behavior.
