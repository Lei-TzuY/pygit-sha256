# Phase255 — `rev-list --filter-print-omitted --boundary`

Phase255 composes the line-oriented omitted-object channel with boundary traversal.

## Behavior

Supported examples now include:

```bash
pygit rev-list --objects --boundary --filter=blob:none --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --boundary --filter=object:type=tree --filter-print-omitted --missing=print-info HEAD
```

Normal traversal and boundary records are emitted first. Objects removed by the active object filter are emitted afterward as `~<oid>`, followed by any explicit missing-object diagnostics. When `--count` is also present, the final integer remains last, preserving the Phase254 ordering contract.

Boundary traversal also contributes snapshot roots to omission discovery. This matters when a boundary commit references an older blob that is not present in the selected tip snapshot: `blob:none` must report that local object as omitted rather than silently losing it from the omission set.

For `object:type=...`, a boundary commit that does not match the requested type is no longer shown as a `-<oid>` boundary record; the same local object is instead eligible for the `~<oid>` omitted channel. Matching boundary commits remain ordinary boundary output and are not duplicated as omitted records. The existing positive-root exemption remains in force unless `--filter-provided-objects` is supplied.

## Native Git compatibility

Git 2.55.0 `builtin/rev-list.c` performs `traverse_commit_list_filtered(...)` first and only after traversal iterates the collected `omitted_objects` set and prints `~<oid>` records. Missing-object diagnostics and the final count are printed later. Phase255 preserves this ordering, including boundary records as traversal output.

The current Git documentation defines `--boundary` as emitting excluded boundary commits and `--filter-print-omitted` as printing objects omitted by the object filter with a leading `~`. Phase255 models the interaction structurally instead of concatenating two independently rendered text streams.

## SHA-256-native boundary

Every `~` record remains a genuine local 64-hex SHA-256 object id. A matching boundary record likewise uses the real local SHA-256 with a leading `-`. If a filtered unresolved foreign promise would need to be reported as omitted, pygit still fails explicitly because its local SHA-256 cannot be derived without materializing content. Native SHA-1 is never padded, translated, or substituted into the repository-visible omitted-object channel.

## Scope deliberately left for later phases

`-z` and `--objects-edge` remain explicitly deferred with `--filter-print-omitted`. Their framing and overlap rules should be implemented directly rather than inferred from the line-oriented boundary path.
