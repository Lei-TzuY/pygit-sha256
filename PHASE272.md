# Phase272: ordered `rev-list --disk-usage`

Phase272 composes Git-compatible on-disk object accounting with the current SHA-256-native `rev-list --in-commit-order` stack. It deliberately reuses the existing ordered/filter traversal adapters and the existing loose/pack `object_disk_size()` implementation rather than adding another walker or pack parser.

## Scope

This phase enables `--disk-usage[=human]` when the selection is produced by the current ordered stack, including composition with:

- commit-only `--in-commit-order`
- `--objects`
- `--boundary`
- `--objects-edge`
- `--reverse`
- `--count`
- existing object filters, including ordered `blob:limit=<n>[kmg]`
- `--filter-print-omitted`

Git 2.55 explicitly rejects `-z + --disk-usage`; Phase272 follows that current compatibility boundary instead of preserving the permissive behavior observed in older Git releases.

Normal traversal/object records remain suppressed by disk accounting. Independent edge/omitted/missing side channels remain visible before the final count/aggregate lines.

## Native Git behavior

The official `git-rev-list` documentation defines `--disk-usage` as suppressing normal output and reporting the sum of on-disk storage used by the selected commits or, with `--objects`, by the selected objects. `--disk-usage=human` formats that aggregate for humans.

Native SHA-256 probes establish the observable details that matter to the adapter:

- `--in-commit-order --disk-usage HEAD` emits one newline-terminated integer and no traversal records.
- `--objects --in-commit-order --disk-usage HEAD` accounts the ordered object selection but still suppresses traversal records.
- `--reverse` does not change the aggregate because it changes order, not membership.
- `--count` emits `0\n` before the disk-usage aggregate.
- `--objects-edge` keeps its leading `-<oid>` edge records, but excluded edges do not contribute to the aggregate.
- `--boundary` objects are part of the selected inventory and therefore do contribute when requested.
- `--filter-print-omitted` keeps newline `~<oid>` omission records before the aggregate; omitted objects are not sized.
- Git 2.55 rejects `-z` with `fatal: -z option used with unsupported option`.

An older local Git 2.47.3 probe accepted `-z + --disk-usage` and still produced a newline aggregate. The authoritative Phase272 target is the CI runner's Git 2.55 behavior, so the automated SHA-256 probe locks the newer rejection rather than carrying the older permissive behavior forward.

## Implementation

`rev_list_disk_usage_cli` detects `--disk-usage` before any ordered/filter presentation adapter can claim the invocation. It then removes only the disk-accounting and presentation-only tokens and routes the remaining selection arguments through the same current-stack dispatcher used by normal `rev-list`.

That routing is factored into `_run_routed()`. Disk accounting captures its line-oriented selection and projects it into two channels:

1. genuine local 64-hex SHA-256 object IDs that are passed to `object_disk_size()` exactly once per selected object;
2. independent side-channel records such as leading `--objects-edge` records and `~`/`?` diagnostics, which are preserved but never sized.

The current ordered object adapter intentionally requires `--objects` or `--objects-edge`. For commit-only `--in-commit-order --disk-usage`, Phase272 removes only the ordering hint before delegating to the mature commit walker. This is safe because disk usage depends on the selected commit set, not its output order.

`-z` is validated before traversal and rejected for disk accounting, matching Git 2.55.

## SHA-256-native boundary

Phase272 does not synthesize or translate object identities.

- Every object passed to local disk accounting is a genuine local 64-hex SHA-256.
- Leading edge records remain genuine local SHA-256 but are excluded from the byte sum, matching native Git.
- Filtered omission records retain genuine local SHA-256 identities and are not sized.
- Missing/promisor identities stay in their existing diagnostic channel and are never promoted into a local SHA-256 slot.
- No SHA-1 padding, surrogate object ID, size guessing, or extra traversal implementation is introduced.

On-disk size accounting continues to use the existing validated loose/pack/alternate-aware implementation.

## Compatibility matrix

For deterministic local repositories, Phase272 regression coverage verifies:

```text
--in-commit-order --disk-usage HEAD
--objects --in-commit-order --disk-usage HEAD
--objects --in-commit-order --disk-usage --reverse HEAD
--in-commit-order --disk-usage --count HEAD
--objects-edge --in-commit-order --disk-usage <old>..<new>
--objects --in-commit-order --disk-usage \
  --filter=blob:limit=8 --filter-print-omitted HEAD
--objects --in-commit-order --disk-usage -z HEAD  => rejected on Git 2.55
```

The tests use unit disk sizes for deterministic membership assertions and a separate native SHA-256 Git probe for external protocol compatibility. The full repository test suite remains the final regression gate.

## Deferred work

- `object:type=tag` / annotated-tag traversal
- richer persistent promisor size metadata for filters that require unavailable object sizes
- any future disk-accounting support for genuinely unavailable promised content (only if a trustworthy persistent size source exists)
