# Phase 102 — `verify-pack` integrity diagnostics

Phase 102 adds a dedicated read-only `verify-pack` plumbing command for validating pygit's SHA-256 pack/index pairs end to end.

## CLI

```bash
pygit verify-pack .pygit/objects/pack/pack-<id>.idx
pygit verify-pack -v .pygit/objects/pack/pack-<id>.idx
pygit verify-pack one.idx two.idx
```

The command follows Git's `verify-pack` shape: callers provide one or more `.idx` files and the corresponding `.pack` file is resolved by replacing the suffix. A successful default verification prints `<idx>: ok`. Invalid pairs print `<idx>: bad` plus the concrete validation error and return a non-zero status. Multiple inputs are processed independently so one bad archive does not hide diagnostics for later inputs.

`-v` / `--verbose` prints one line per verified non-delta object:

```text
<object-name> <type> <size> <size-in-packfile> <offset-in-packfile>
```

followed by `non delta: N objects` and the normal `ok` line. Pygit's educational pack schema intentionally has no delta objects, so no delta depth/base columns or chain histogram buckets are synthesized.

## Validation boundary

`verify_pack()` deliberately reuses the existing strict pack/index readers instead of maintaining a second parser.

The `.idx` path validates:

- signature and version;
- monotonic fan-out table and exact fan-out/object-ID agreement;
- canonical strictly sorted 64-hex SHA-256 object IDs;
- unique valid offsets;
- exact file size and SHA-256 index checksum.

The paired `.pack` path then validates:

- pack signature, version, and index/object-count agreement;
- pack SHA-256 trailer;
- every indexed offset and entry boundary;
- bounded zlib decompression and declared size;
- exact entry CRC-32;
- canonical typed object envelope;
- recomputed SHA-256 object identity.

That makes this command useful for diagnosing corruption that `count-objects` or index-only inspection cannot fully prove away.

## Compatibility scope

Current Git documents `git verify-pack [-v|--verbose] [-s|--stat-only] <pack>.idx...`. Phase 102 implements the full-verification default and verbose object listing. `--stat-only` remains separate work because pygit's non-delta educational pack format has no meaningful delta-chain statistics to inspect without verification; this phase does not add a misleading no-op flag.

## Regression coverage

`tests/test_phase102.py` covers successful API/CLI verification, native-shaped verbose output, index checksum corruption, pack checksum corruption, CRC corruption under a recomputed index checksum, pack/index count mismatch under a recomputed pack checksum, multiple-input continuation, missing pack pairs, extension validation, and help output.
