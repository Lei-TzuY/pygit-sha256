# Phase249: metadata-only `rev-list --filter=object:type=...`

Phase249 adds the first type-selective object filter to the promisor-aware,
line-oriented `rev-list --objects* --missing=...` traversal.

## Supported filters

This phase supports:

- `--filter=object:type=commit`
- `--filter=object:type=tree`
- `--filter=object:type=blob`

with the existing metadata-only missing modes:

- `--missing=allow-promisor`
- `--missing=print`
- `--missing=print-info`

and the already-modelled revision/presentation options such as `--objects`,
`--objects-edge`, `--boundary`, `--skip`, `--max-count`, `--reverse`,
`--topo-order`, and `--first-parent` where the underlying missing adapter
supports them.

`object:type=tag` is deliberately deferred because the current inventory starts
from commit selection and does not yet model annotated-tag objects supplied as
revision tips. Claiming tag filtering without that traversal would be
incomplete.

`--count` and `-z` are also deferred for `object:type`; both have structured
presentation rules that should be added directly rather than inferred from the
line-oriented projection.

## Native Git semantics

Current Git documents `object:type=(tag|commit|tree|blob)` as omitting objects
which are not of the requested type. It also notes that explicitly provided
objects bypass filters unless `--filter-provided-objects` is requested.

Native SHA-256 Git was exercised directly to pin down how that rule interacts
with rev-list presentation:

- `--filter=object:type=commit HEAD` prints selected commits and no tree/blob
  snapshot objects.
- `--filter=object:type=tree HEAD` still prints selected commits, then only tree
  snapshot objects.
- `--filter=object:type=blob HEAD` still prints selected commits, then only blob
  snapshot objects.
- an explicit `--objects-edge` commit remains advertised even when the requested
  type is tree or blob.
- a normal `--boundary` commit is not an explicit provided object and is
  therefore removed by tree/blob filters; it remains visible for the commit
  filter.
- even when the boundary commit record itself is filtered, its snapshot still
  participates in object traversal, so requested tree/blob objects from that
  boundary snapshot remain eligible.

Phase249 models those distinctions explicitly rather than treating every commit
line the same way.

## Implementation

The existing Phase246-248 `blob:none` code path remains unchanged. A new
line-oriented `object:type` projection:

1. strips only the filter argument and delegates revision/object traversal to the
   already-tested promisor missing adapters;
2. obtains the selected commit set from the existing rev-list selector;
3. obtains explicit object edges from the existing Phase234 edge planner;
4. preserves unprefixed selected commit records regardless of the requested
   object type;
5. preserves explicit `-edge` records regardless of the requested object type;
6. classifies all other present local records from their stored object type;
7. classifies `?missing` records from persistent promisor kind metadata without
   fetching their content;
8. emits only records accepted by the requested type.

Unknown or malformed local records are not silently hidden by the filter; if the
adapter cannot classify a present record, it leaves that integrity signal
visible. A missing record without promisor type metadata is an error because the
filter cannot safely decide whether the missing object belongs in the result.

## SHA identity boundary

Phase249 preserves the existing two-domain invariant:

- selected commits, explicit edges, and other present objects use real local
  64-hex SHA-256 identities;
- unresolved foreign promises may use native SHA-1 only on the explicit
  `?missing` channel;
- type filtering never creates a surrogate SHA-256;
- deciding whether a promised object matches `object:type` uses metadata only
  and does not materialize it.

## Network and mutation guarantees

The new filter remains read-only and metadata-only:

- zero single-object promisor fetches;
- zero batch promisor fetches;
- no worktree/index/ref mutation;
- no promisor-state mutation.

## Regression coverage

Focused tests cover:

- commit/tree/blob filters over an ordinary SHA-256 repository;
- selected-commit exemption for every supported requested type;
- tree filtering of a normal boundary commit while retaining selected and
  boundary snapshot trees;
- commit filtering which keeps the boundary commit and removes snapshot
  tree/blob objects;
- explicit object-edge preservation under a tree filter;
- a real foreign `blob:none` promise filtered by promisor kind under commit,
  tree, and blob requests with zero fetches and unchanged promisor state;
- plain `--missing=print` filtering where no `type=` token is available in the
  rendered line;
- explicit deferral of `--count`, `-z`, and `object:type=tag`.

Phase249 changes no object format, pack format, wire protocol, tree
serialization, refs, index, worktree format, or promisor identity representation.
