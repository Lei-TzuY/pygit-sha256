# Phase253 — rev-list filter omitted-object reporting

Phase253 added the first line-oriented `rev-list --filter-print-omitted` path on top of the metadata-only object-filter stack. Phase255 later corrected one important Git-compatibility detail documented below.

## Supported surface

```text
pygit rev-list --objects --filter=blob:none --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --filter=object:type=commit|tree|blob --filter-print-omitted --missing=allow-promisor HEAD
```

The same line-oriented filter path may continue to use the existing `--missing=print` or `--missing=print-info` policies when every object that must actually be reported by the filter has a genuine local SHA-256 identity.

Git documents `--filter-print-omitted` as useful only with `--filter=` and specifies that objects present in a filter's omitted-object set are printed with a leading `~` character.

## Phase255 compatibility correction

Git 2.55.0's filters do not all populate the omitted-object set in the same way. In `list-objects-filter.c`, `filter_blobs_none()` inserts filtered blobs into `omits`, but `filter_object_type()` leaves its `omits` argument unused. Therefore:

- `blob:none --filter-print-omitted` emits `~<oid>` records for omitted local blobs;
- `object:type=... --filter-print-omitted` still filters traversal output, but it emits no `~` records merely for nonmatching object types;
- `--filter-provided-objects` changes which provided roots survive `object:type`, but does not cause those filtered roots to enter the omitted set.

The original Phase253 implementation treated every `object:type` rejection as an omitted object. Phase255 retired that behavior and updated the regression suite to match native Git 2.55 semantics.

## SHA-256-native identity boundary

A local object actually collected by an omission-reporting filter is emitted as:

```text
~<64-hex-local-sha256>
```

An unresolved foreign promise is different: until materialization pygit knows only its upstream/native transport SHA-1 and cannot derive the repository-visible SHA-256. pygit therefore refuses a `--filter-print-omitted` request if such an unresolved promise itself would need to appear on the `~` channel. It never pads, translates, or substitutes the native SHA-1 as a fake SHA-256.

Existing explicit `?` / `missing=yes` channels remain the only places where unresolved native identities may be exposed.

## Traversal design

The adapter reuses the Phase246–252 metadata-only filter and inventory machinery:

- filtered normal output is produced by the existing filter adapter;
- omission discovery reuses the selected inventory rather than fetching objects;
- `blob:none` reports local blobs that native Git's filter would place in its omitted set;
- `object:type=...` applies filtering but intentionally contributes no omitted records, matching Git 2.55;
- the normal provided-object exemption remains respected by traversal;
- `--filter-provided-objects` removes that traversal exemption without changing object:type omission-set behavior.

No object is intentionally fetched or materialized in order to decide whether it is omitted.

## Verification

Focused regression coverage checks `blob:none` SHA-256 omitted records, native-empty omitted sets for `object:type`, required-filter validation, and explicit deferral of still-unmodelled framing combinations. Later phases compose count and boundary behavior while retaining this corrected filter-specific omitted-set contract.
