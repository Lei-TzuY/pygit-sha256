# Phase273: ordered `object:type=tag` for annotated tag roots

Phase273 closes the first annotated-tag gap in the SHA-256-native ordered `rev-list` stack without changing the existing commit/tree/blob traversal.

## Scope

This phase supports:

- `rev-list --objects --in-commit-order --filter=object:type=tag <annotated-tag>`
- Git's default provided-object exemption, so the peeled positive commit remains visible before the tag object
- `--filter-provided-objects`, which filters that peeled commit and leaves only the matching tag object
- `--count`
- structured `-z` output
- `--filter-print-omitted` with the native empty omission set for `object:type`
- positive annotated-tag roots recovered from ordinary revisions, ranges, symmetric ranges, and `--all` ref enumeration
- local annotated-tag chains that ultimately peel to commits

The deliberately staged boundary is that `object:type=tag` still requires at least one positive annotated-tag root. A plain commit-only root such as `HEAD` continues to reject the tag filter in this phase rather than pretending that full tag reachability semantics have been modelled. Annotated tags that peel to trees/blobs are also deferred because the ordered walker is currently commit-rooted.

## Native Git behavior

A native SHA-256 Git probe establishes the central observable contract. For an annotated tag `v1` that points at a commit:

```text
$ git rev-list --objects --in-commit-order \
    --filter=object:type=tag --no-object-names v1
<peeled-commit-sha256>
<tag-object-sha256>

$ git rev-list --objects --in-commit-order \
    --filter=object:type=tag --filter-provided-objects \
    --no-object-names v1
<tag-object-sha256>
```

The tag object is presented immediately after its peeled commit and before the commit's tree/blob snapshot. `object:type` does not populate the omitted-object set, so `--filter-print-omitted` adds no `~<oid>` records.

## Implementation

The mature ordered inventory intentionally starts from peeled commitish roots, so annotated tag objects are no longer visible by the time commit traversal begins. Phase273 restores only the positive provided tag roots:

1. recover positive revision expressions before peeling;
2. resolve each expression in the repository-visible object domain;
3. follow local `TagObject` chains with cycle detection;
4. require the final target to be a commit for this phase;
5. insert the tag object(s) immediately after the matching commit frame;
6. run the existing `object:type` membership filter and ordered renderer.

No second commit/tree walker is introduced. Boundary, missing-object, count, NUL, and object-edge behavior stay owned by the existing ordered stack.

## SHA-256-native boundary

Every repository-visible tag identity emitted by Phase273 is a genuine local 64-hex SHA-256 returned by the local object store. Tag traversal never pads or translates a foreign SHA-1 and never synthesizes a surrogate SHA-256.

This phase does not add a promisor tag-materialization path. It only follows local annotated tag objects that already exist in the repository-visible SHA-256 domain.

## Verification

Focused regressions cover:

- default provided commit + tag ordering;
- `--filter-provided-objects` leaving only the tag;
- count values `2` vs `1`;
- structured NUL framing with local SHA-256 identities;
- the empty `--filter-print-omitted` channel;
- the staged plain-commit-root rejection;
- a native SHA-256 Git compatibility probe for ordered tag filtering.

The authoritative full-suite gate is the repository's GitHub Actions Python 3.9/3.13 matrix on the exact PR head.

## Deferred work

- `object:type=tag` for commit-only roots with no positive annotated-tag object;
- annotated tags that peel to tree/blob objects;
- unresolved/promised annotated-tag metadata;
- non-ordered `object:type=tag` parity;
- broader tag reachability semantics beyond provided positive tag roots.