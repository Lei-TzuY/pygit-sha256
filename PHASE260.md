# Phase260: `rev-list --in-commit-order --boundary`

Phase260 composes Phase259's metadata-only commit/snapshot interleaving with the existing boundary planner. It changes presentation order only; revision selection, object identity, promisor state, and object storage remain unchanged.

## Native Git behavior

A deterministic native SHA-256 Git repository with three cumulative commits was exercised directly with Git 2.47.3.

For:

```text
git rev-list --objects --in-commit-order --boundary --max-count=1 HEAD
```

native Git emits the selected tip, then its first-seen snapshot objects, then the excluded boundary commit prefixed by `-`, then the first-seen objects from that boundary snapshot.

With `--reverse`, the boundary commit and its snapshot appear first, followed by the selected commit and any objects not already seen from the boundary snapshot. One global object-deduplication set therefore remains authoritative across selected and boundary snapshots.

For an explicit range such as `c1..c3`, explicit negative-revision object closure remains subtracted even when a limit-induced boundary contributes its snapshot. The boundary commit frame itself still remains visible; only snapshot objects that belong to the excluded closure disappear.

Native count fixtures also establish that boundary framing is suppressed under `--count` while all present selected/boundary frames and present snapshot objects contribute to the final integer. The three-commit `HEAD --max-count=1` fixture counts `7`; the explicit `c1..c3 --max-count=1` fixture counts `6` after exclusion subtraction.

## Implementation

Phase260 reuses:

- Phase259's commit/snapshot interleaving and global object deduplication;
- Phase236's metadata-only selected/boundary commit planner;
- Phase232's tree walking and exclusion-closure helpers;
- the existing print/print-info missing-object channels.

The ordered inventory now starts from a sequence of top-level commit frames `(oid, is_boundary)`. Each frame is appended before its snapshot is walked. Boundary OIDs are retained separately so only top-level boundary commit records receive the `-` prefix.

Explicit negative-revision closure subtraction deliberately preserves every top-level framed commit while continuing to subtract ordinary snapshot entries. This distinction matters when the boundary commit itself belongs to the excluded history: Git still advertises the boundary frame even though excluded snapshot content must not leak back into the selected object set. Path-bearing commit entries such as gitlinks remain ordinary snapshot objects and are not granted this framing exemption.

## Partial clones

No promised object is materialized merely to establish ordering.

- `--missing=allow-promisor` silently omits unresolved promised entries.
- `--missing=print` emits unresolved native transport OIDs only through the explicit `?` missing channel.
- `--missing=print-info` preserves path/type metadata at the first snapshot position where the promise is encountered.
- ordinary traversal detects unresolved promises before emitting any output and fails with the existing explicit-missing-policy diagnostic.
- `--count` still emits requested missing diagnostics first and counts only present repository objects.

Present objects and boundary commit frames always use genuine local 64-hex SHA-256 identities. No surrogate SHA-256 is synthesized from an upstream SHA-1.

## Scope

Phase260 supports `--boundary` together with Phase259's existing line-oriented `--in-commit-order` modes, including `--reverse`, `--skip`, `--max-count`, `--topo-order`, `--first-parent`, `--all`, `--count`, `--no-object-names`, and the supported missing-object policies.

The following remain deliberately deferred:

- `--objects-edge`;
- `-z`;
- object filters and `--filter-print-omitted`;
- `--disk-usage`.

Phase260 does not change object format, protocol-v2, pack format, refs/index/worktree behavior, or promisor metadata.

## Stack coordination

Phase258 / PR #235 and Phase259 / PR #236 were created as sibling branches from Phase257 because Phase258 was not exact-green when Phase259 began. Phase260 intentionally continues from the exact-green Phase259 head and does not incorporate the still-independent Phase258 blob-limit work.

Base:

```text
Phase259 / PR #236
f689c0dd946d2614d364917a0b356583ec6f3cef
```

The PR remains intentionally open and unmerged.