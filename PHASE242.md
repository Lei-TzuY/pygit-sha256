# Phase242 — promisor missing output with `--objects-edge`

Phase242 composes the existing missing-object presentation stack with Git's
`rev-list --objects-edge` framing without adding another object traversal.

## Scope

The supported forms are now:

```text
pygit rev-list --objects-edge --missing=print <revisions>
pygit rev-list --objects-edge --missing=print-info <revisions>
```

They inherit the already-tested selection and presentation behavior for
`--all`, `--first-parent`, `--topo-order`, `--reverse`, `--skip`,
`--max-count`, `--no-object-names`, and `--count`.

`--boundary + --objects-edge` remains deliberately unsupported. That is a
separate boundary-framing problem already rejected by the older
`allow-promisor` path and is not silently broadened here.

## Composition instead of duplicate traversal

Phase234 already owns metadata-only discovery of excluded edge commits through
`_promisor_object_edges()`. Phase237–240 own metadata-only `print-info`
selection, missing records, SHA-domain separation, and counting. Phase241
projects plain `print` from `print-info`.

Phase242 joins those layers in the Phase241 adapter:

1. detect `--objects-edge + --missing=print/print-info`;
2. reject the still-unmodelled `--boundary + --objects-edge` combination;
3. project `--objects-edge` to `--objects` for the existing inventory-backed
   traversal;
4. compute excluded edge commits with the Phase234 helper;
5. print each edge first as `-<local-sha256>`;
6. replay the existing missing-object output unchanged (`print-info`) or with
   only `path=`/`type=` stripped (`print`).

No new reachability walker or tree/blob inventory is introduced.

## Hash-domain rule

Edge and present records are repository-visible SHA-256 identities:

```text
-<64-hex local SHA-256 edge commit>
<64-hex local SHA-256 selected object>
```

An unresolved foreign promise still has no derivable local SHA-256 until its
content is materialized, so it appears only on the explicit missing channel:

```text
?<40-hex native SHA-1>
?<40-hex native SHA-1> path=f.txt type=blob
```

No surrogate SHA-256 is invented.

## Count framing

The Phase234 edge contract and Phase240 missing count contract compose directly:

- edge lines remain visible before the count stream;
- missing `?` records remain visible;
- the final integer counts present selected objects only;
- excluded edge commits are not part of that integer;
- unresolved promises are not part of that integer.

For a range whose selected side contains one commit and one present tree while
its blob is promised, the shape is:

```text
-<edge>
?<missing>
2
```

## Exclusion closure

The projected traversal keeps the original revision range. Explicit negative
revision/common-ancestry subtraction therefore remains authoritative: an edge
commit may be advertised as `-<oid>`, but its tree/blob closure is not silently
reintroduced into the selected object stream.

## Network behavior

The operation remains metadata-only:

- no single-object promisor fetch;
- no batch promisor fetch;
- no promisor-state mutation.

## Non-goals

Phase242 does not change:

- object serialization;
- pack format;
- smart HTTP or protocol v2;
- refs or index format;
- worktree behavior;
- fsck behavior;
- the core promisor object inventory;
- the existing boundary planner.

## Regression coverage

`tests/test_phase242.py` uses a real foreign `blob:none` range with distinct
base/tip snapshots and verifies:

- `print-info` edge framing plus missing metadata;
- plain `print` projection;
- explicit exclusion of the base tree/blob closure;
- count framing for both missing modes;
- zero single/batch fetching and unchanged promisor state;
- continued rejection of `--boundary + --objects-edge`.
