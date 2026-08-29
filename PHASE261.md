# Phase261: `rev-list --in-commit-order --objects-edge`

Phase261 composes Phase259/260's metadata-only commit/snapshot interleaving with Git-style object-edge framing.

## Native Git compatibility

A deterministic SHA-256 repository was exercised with native Git 2.47.3 before implementation.

For an explicit range such as:

```text
git rev-list --objects-edge --in-commit-order c1..c3
```

Git emits the excluded edge commit first as `-<oid>`, then emits selected commits with each tree/blob object immediately after the first selected commit that references it.

Important observed behavior:

- object-edge records are emitted before the ordered object stream;
- `--reverse` reverses the selected commit/snapshot stream but leaves object-edge records first;
- edge records do not participate in object deduplication for selected snapshots;
- object closure reachable only from explicit exclusions remains subtracted from the selected object stream;
- `--count` still emits edge records, then emits the final count of present selected objects only; edge records are not counted.

The current Git documentation defines `--in-commit-order` as printing trees/blobs after the first commit that references them, and `--objects-edge` as `--objects` plus excluded commits prefixed by `-`. Phase261 preserves both contracts together.

## Implementation

The in-commit-order adapter now accepts exactly one of `--objects` or `--objects-edge`.

For `--objects-edge`, parsing is projected internally onto the mature `--objects` promisor parser so revision selection, skip/max-count, first-parent, topo-order, reverse, object naming, count, and missing-object policy remain shared. Edge presentation is then added locally through the existing metadata-only `_promisor_object_edges()` planner.

This projection also allows Phase261 to support `--missing=print` and `--missing=print-info` without inheriting older generic parser guards that are unrelated to the ordered adapter.

Rendering deliberately validates ordinary partial-clone missing objects before printing edge records. Therefore an ordinary partial clone still fails before producing partial output unless the caller explicitly chooses a supported `--missing` policy.

## Partial clones and SHA-256-native identity

No promised object is materialized merely to establish object-edge or in-commit-order presentation.

- present commit/tree/blob records always use genuine local 64-hex SHA-256 identities;
- edge commits are local commit objects and therefore also use genuine SHA-256 identities;
- unresolved promised objects can expose upstream SHA-1 only through the explicit `?` missing channel under `--missing=print` or `print-info`;
- explicit exclusion closure can remove a promised object before presentation without fetching it;
- promisor state remains unchanged.

No surrogate or padded SHA-256 identity is synthesized from a foreign SHA-1.

## Scope

Phase261 supports `--objects-edge --in-commit-order` together with the already supported line-oriented controls, including:

- `--reverse`;
- `--skip` / `--max-count`;
- `--topo-order`;
- `--first-parent`;
- `--all`;
- `--count`;
- `--no-object-names`;
- `--missing=allow-promisor|print|print-info`.

The following remain deliberately deferred:

- combining `--objects-edge` and `--boundary` under `--in-commit-order`;
- `-z`;
- object filters and `--filter-print-omitted`;
- `--disk-usage`.

Phase261 changes presentation/planning only. It does not change object format, protocol-v2, pack format, refs, index/worktree behavior, or promisor metadata.

## Stack coordination

Base:

```text
Phase260 / PR #237
1c29722f7414cd5261f28a300dfeacb5844c0266
```

Phase258 remains an independent sibling and is intentionally not incorporated into this stack.

This PR remains intentionally open and unmerged.
