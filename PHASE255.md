# Phase255 — `rev-list --filter-print-omitted --boundary`

Phase255 composes the line-oriented omitted-object channel with boundary traversal and corrects the omitted-set semantics for `object:type` filters to match Git 2.55.

## Behavior

Supported examples now include:

```bash
pygit rev-list --objects --boundary --filter=blob:none --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --boundary --filter=object:type=tree --filter-print-omitted --missing=print-info HEAD
```

Normal traversal and boundary records are emitted first. Objects actually collected by the active Git-compatible omission filter are emitted afterward as `~<oid>`, followed by any explicit missing-object diagnostics. When `--count` is also present, the final integer remains last, preserving the Phase254 ordering contract.

For `blob:none`, boundary traversal contributes snapshot roots to omission discovery. This matters when a boundary commit references an older blob that is not present in the selected tip snapshot: that local blob must still enter the omitted set instead of disappearing merely because it is reachable only through the boundary snapshot.

For `object:type=...`, filtered objects are suppressed from traversal but do **not** enter Git's omitted-object set. Thus a boundary commit filtered out by `object:type=tree` is absent from the `-<oid>` boundary stream and is not rewritten as `~<oid>`; `--filter-print-omitted` produces no synthetic omitted records for `object:type`. The existing positive-root exemption remains in force unless `--filter-provided-objects` is supplied.

## Native Git compatibility

Git 2.55.0 `builtin/rev-list.c` performs `traverse_commit_list_filtered(...)` first and only afterward iterates the collected `omitted_objects` set and prints `~<oid>` records. Missing-object diagnostics and the final count are printed later. Phase255 preserves this ordering, including boundary records as traversal output.

The crucial filter-specific detail comes from Git 2.55.0 `list-objects-filter.c`: `filter_blobs_none()` inserts filtered blobs into `omits`, while `filter_object_type()` declares its `omits` argument unused and never records nonmatching objects. An initial Phase255 CI run exposed that the earlier Phase253/254 interpretation had treated every `object:type` rejection as omitted. Phase255 corrects that compatibility drift and updates the regressions accordingly.

## SHA-256-native boundary

Every `~` record remains a genuine local 64-hex SHA-256 object id. A matching boundary record likewise uses the real local SHA-256 with a leading `-`. If a filtered unresolved foreign promise would need to be reported as omitted by a filter that actually collects omissions (currently `blob:none`), pygit still fails explicitly because its local SHA-256 cannot be derived without materializing content. Native SHA-1 is never padded, translated, or substituted into the repository-visible omitted-object channel.

## Scope deliberately left for later phases

`-z` and `--objects-edge` remain explicitly deferred with `--filter-print-omitted`. Their framing and overlap rules should be implemented directly rather than inferred from the line-oriented boundary path.
