# Phase 131 — rev-list symmetric side filters and counts

Phase 131 tightens pygit's existing symmetric-range `rev-list` support around the left/right selection surface introduced in Phase 68.

## Side filters

For one `A...B` symmetric range, pygit now accepts:

```bash
pygit rev-list --left-only A...B
pygit rev-list --right-only A...B
pygit rev-list --left-right --left-only A...B
pygit rev-list --left-right --right-only A...B
```

`--left-only` keeps commits reachable only from the left tip; `--right-only` keeps commits reachable only from the right tip. Without `--left-right`, output remains plain object IDs. With `--left-right`, the surviving commits retain `<` or `>` markers.

`--left-only` and `--right-only` are mutually exclusive. As with Phase 68's `--left-right`, Phase 131 deliberately scopes side-aware selection to one explicit `A...B` range instead of claiming the full native Git revision-option surface.

## Ordering and limits

Git applies side filtering before output limiting. Phase 131 preserves that order explicitly:

1. compute the complete symmetric difference;
2. apply `--left-only` / `--right-only`;
3. apply `--skip`;
4. apply `--max-count` / `-n`;
5. apply `--reverse`.

This matters when the left and right histories are interleaved in date or topological order: skipping one left-side commit must not accidentally consume a right-side commit first.

## Side-aware `--count`

Native Git treats `--left-right --count` as a two-column protocol. Pygit now matches that focused behavior:

```text
<left-count>\t<right-count>
```

Examples:

```bash
pygit rev-list --left-right --count A...B
pygit rev-list --left-right --left-only --count A...B
pygit rev-list --left-right --right-only --count A...B
```

After filtering and limits, the two columns report the remaining left and right commits respectively. Without `--left-right`, `--count` keeps the historical one-number output, including with `--left-only` or `--right-only`.

## Architecture

Phase 131 leaves the Phase 68 `rev_list()` graph engine unchanged. `pygit.rev_list_sides` layers side filtering and side counting over the complete marked symmetric selection. This isolates compatibility behavior from Phase 75/121 object enumeration and keeps `--objects` / `--objects-edge` side-mode combinations rejected rather than inventing a mixed protocol.

## Regression coverage

`tests/test_phase131.py` covers:

- left-only and right-only selection;
- per-side ordering under topological traversal;
- filter-before-skip/max-count behavior;
- reverse after limiting;
- side-count helper semantics;
- two-column `--left-right --count` output;
- filtered two-column counts such as `2\t0` and `0\t2`;
- plain single-number counts without `--left-right`;
- marker preservation when side filters and `--left-right` are combined;
- mutual exclusion and object-mode validation;
- installed CLI help exposure.
