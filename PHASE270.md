# Phase270: Git-compatible `rev-list -z + --count`

Phase270 reconciles pygit's historical `-z + --count` rejection with native Git behavior while preserving the SHA-256-native/promisor contracts established by the earlier object-traversal phases.

## Scope

This phase enables `-z + --count` across the already-supported structured object paths:

- ordinary `rev-list --objects`
- `--boundary`
- `--in-commit-order`
- `--filter=blob:none`
- `--filter=object:type=commit|tree|blob`
- `--filter-print-omitted` for filters that populate the omitted set
- ordered `blob:none + --filter-print-omitted`
- ordered `blob:limit=<n>[kmg] + --filter-print-omitted`

`-z + --objects-edge` remains unsupported because native Git's edge presentation is still line-oriented and Git 2.55 lists it among the `-z`-incompatible output modes.

Plain ordered `blob:limit -z` remains deferred. Phase269 already supports its omitted-object composition because that path has an explicit framing adapter; adding plain size-filtered NUL traversal is a separate concern.

## Native Git behavior

Git 2.55 `builtin/rev-list.c` sets `line_term` and `info_term` to NUL when `-z` is present, but the final count is emitted later with a normal `printf("%d\n", ...)`. The `-z` incompatibility check does not reject `revs.count`.

As a result:

```text
$ git rev-list --objects -z --count HEAD
6\n
```

There are no present-object NUL records in count mode.

Missing and omitted diagnostics retain their independent framing and ordering:

```text
~<omitted-local-sha256>\n
<missing-native-oid>\0missing=yes\0...\0
<count>\n
```

The omitted-object loop remains hard-coded to newline framing, while explicit missing records still use the structured NUL protocol. The final count is always a newline-terminated decimal integer.

A deterministic native SHA-256 fixture on Git 2.47.3 established the same count behavior before implementation. The automated Phase270 native probe runs on the GitHub Actions runner's Git 2.55.0 and checks ordinary, boundary, ordered, blob:none, object:type, blob:none omitted, and ordered blob:limit omitted count framing.

## Implementation

`rev_list_nul_cli` now treats `--count` as a rendering mode instead of an incompatible output option. It builds and filters the same structured inventory, preflights ordinary missing-object failures before emitting anything, emits explicit missing records when requested, suppresses all present-object NUL records, and prints the final present-object count with a newline.

The ordered renderer mirrors the same rule directly on the commit/snapshot-interleaved inventory.

The shared omission adapter adds `_partition_projected_nul_count()`. It isolates the final newline count by splitting after the last NUL rather than using `splitlines()`, because structured `path=` metadata may contain literal newlines. The adapter then preserves native ordering:

1. traversal records (normally empty under count),
2. newline `~<oid>` omissions,
3. NUL `missing=yes` records,
4. newline count.

Both ordered blob:none omission framing and Phase269's ordered blob-limit omission framing reuse this helper.

## SHA-256-native / promisor boundary

Count changes no object identity rules.

- present repository objects remain genuine local 64-hex SHA-256 identities;
- omitted identities remain genuine local 64-hex SHA-256 identities;
- unresolved native SHA-1 identities may appear only in explicit `missing=yes` records;
- missing promises are never counted as present objects;
- no SHA-1 padding, translation, or surrogate SHA-256 is introduced;
- count-mode classification performs no intentional single-object or batch materialization.

For `blob:limit`, unresolved promised blobs still fail before output because persistent promisor metadata does not yet contain uncompressed blob size.

## Compatibility probes

For a deterministic two-commit SHA-256 repository containing a 3-byte blob and an 8-byte blob, native Git reports:

```text
--objects -z --count                                      => 6\n
--objects -z --boundary --max-count=1 --count            => 6\n
--objects --in-commit-order -z --count                    => 6\n
--objects -z --filter=blob:none --count                   => 4\n
--objects -z --filter=object:type=tree \
  --filter-provided-objects --count                       => 2\n
--objects --in-commit-order -z --filter=blob:limit=8 \
  --filter-print-omitted --count                          => ~<8-byte-blob>\n5\n
```

`blob:none + --filter-print-omitted + --count` similarly emits its newline omission set followed by `4\n`, with no NUL present-object records.

## Deferred work

- plain ordered `blob:limit -z`
- `-z + --objects-edge`
- ordered `--disk-usage`
- `object:type=tag` / annotated-tag traversal
