# Phase262: `rev-list --in-commit-order --objects-edge --boundary`

Phase262 composes the metadata-only `rev-list --in-commit-order` traversal with both object-edge and boundary framing.

## Native Git behavior

A deterministic SHA-256 repository with three cumulative commits was exercised directly with native Git 2.47.3 before implementation.

For an explicit range such as `c1..c3`, `--objects-edge --boundary` exposes two potentially overlapping presentation channels:

- `--objects-edge` advertises excluded commits as leading `-<oid>` records;
- `--boundary` marks excluded boundary commits in the commit traversal.

When the same explicit exclusion is both an object edge and a boundary commit, native Git prints it only once: the leading object-edge record owns presentation and the later boundary frame disappears. Its excluded snapshot remains excluded as usual.

For:

```text
git rev-list --objects-edge --in-commit-order --boundary c1..c3
```

native Git therefore emits the explicit `-c1` edge first, then the selected commit/snapshot stream, with no second `-c1` boundary record.

A limit-induced boundary remains distinct. For:

```text
git rev-list --objects-edge --in-commit-order --boundary --max-count=1 c1..c3
```

Git emits `-c1` first, then the selected `c3` snapshot, then `-c2` and the first-seen objects from the `c2` boundary snapshot.

With `--reverse`, explicit object edges remain at the front while the selected/boundary commit-snapshot stream is reversed. With `--count`, explicit edge records remain visible but are not counted; an overlapping boundary frame suppressed by the edge is also absent from the count.

## Implementation

Phase262 removes the Phase261 parser guard for the triple composition and adds an explicit edge/boundary overlap normalization step.

The existing planners remain authoritative:

- Phase261 computes explicit object edges;
- Phase260 computes selected/boundary commit frames and commit/snapshot interleaving;
- Phase232 object-closure subtraction removes objects reachable only from explicit negative revisions.

After both plans are built, Phase262 intersects the explicit edge set with the set of top-level boundary commit OIDs. Only overlapping top-level boundary commit inventory entries are removed. Path-bearing commit objects such as gitlinks are not affected, and non-overlapping limit-induced boundary frames and snapshots remain untouched.

This keeps presentation logic separate from reachability and object identity.

## Partial clones

The triple composition remains metadata-only.

- `--missing=allow-promisor` silently skips unresolved promised objects.
- `--missing=print` and `--missing=print-info` expose unresolved native identities only through the explicit `?` missing channel.
- ordinary traversal detects an unresolved promise before printing any edge, boundary, or object record.
- neither single-object nor batch promisor materialization is required merely to establish edge/boundary ordering.

Promisor state is not mutated.

## SHA-256-native boundary

All present commits, trees, blobs, object edges, and boundary frames use genuine local 64-hex SHA-256 identities.

An unresolved foreign object may still have only its upstream/native SHA-1. Such an identity can appear only through an explicit missing-object channel. Phase262 never pads, translates, or synthesizes a repository-visible SHA-256 from that SHA-1.

## Scope

Phase262 supports the triple composition with the existing line-oriented controls, including:

- `--reverse`;
- `--skip` / `--max-count`;
- `--topo-order`;
- `--first-parent`;
- `--all`;
- `--count`;
- `--no-object-names`;
- `--missing=allow-promisor|print|print-info`.

The following remain deliberately deferred to later phases:

- `-z` with `--in-commit-order`;
- object filters and `--filter-print-omitted` with `--in-commit-order`;
- `--disk-usage` with `--in-commit-order`.

Phase262 makes no object-format, protocol-v2, pack, refs/index/worktree, or promisor-state format changes.

## Stack coordination

Base:

```text
Phase261 / PR #238
b8835d08b0eb548fb862432e89f2f755bc7761f7
```

Phase258 remains an independent sibling from Phase257 and is intentionally not incorporated into this in-commit-order stack.

The PR remains intentionally open and unmerged.
