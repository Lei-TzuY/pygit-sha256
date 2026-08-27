# Phase 161 — status rename/copy exhaustive limits

Phase 161 adds Git-style `status.renameLimit` behavior to pygit's staged
rename/copy presentation layer.

## Why this phase exists

Rename and copy detection has two qualitatively different costs:

1. **cheap exact-object matching** — a deleted/source blob and added target have
   the same object id;
2. **exhaustive similarity matching** — remaining source/target pairs need blob
   comparison and similarity scoring.

The second step can become O(N²). Git therefore exposes a rename-limit config so
status can avoid that expensive fallback on large candidate sets.

## Configuration

Phase 161 resolves the limit in the same order documented by Git:

1. `status.renameLimit`
2. `diff.renameLimit`
3. current default: `1000`

Examples:

```bash
pygit config status.renameLimit 200
pygit config diff.renameLimit 500
```

`status.renameLimit` wins when both are set.

Git-style integer suffixes are accepted by the parser used by the status
similarity engine:

- `2k` = 2048
- `1m` = 1,048,576
- `1g` = 1,073,741,824

Zero and negative values are treated as unlimited for the exhaustive fallback,
matching native Git's effective behavior.

## Exact pass vs exhaustive pass

The limit **does not disable exact matches**.

For staged rename detection pygit now performs:

1. find exact HEAD-source/index-target object-id matches;
2. greedily pair those exact candidates as `R100`;
3. remove their sources and targets;
4. inspect the remaining source/target counts;
5. run the byte-similarity fallback only when both counts fit the configured
   positive limit, or when the limit is non-positive/unlimited.

This matches native behavior observed with multiple staged renames: exact
renames can still be reported when the configured limit is lower than the
candidate population, while non-identical similarity renames remain ordinary
`D` + `A` records until the limit is high enough.

## Copy detection

Phase 160's copy policy uses the same limit.

Normal status copy candidates remain restricted to sources changed in the same
HEAD-to-index changeset. Phase 161 then:

1. resolves exact preimage-object copies as `C100` first;
2. leaves those exact targets classified even if the candidate population is
   over the limit;
3. gates only the remaining similarity fallback with `renameLimit`.

A copy source can still feed multiple destinations.

## `--find-renames` interaction

`git status --find-renames[=<n>]` changes whether rename detection is enabled
and/or its similarity threshold. It does **not** bypass `status.renameLimit`.
Pygit preserves that behavior: a low configured limit can still suppress the
exhaustive similarity fallback after `--find-renames=50%`.

## Deliberate non-feature: `--find-copies-harder`

Native `git status` does not accept `-C`, `--find-copies`, or
`--find-copies-harder`. Those are diff-family options. Phase 161 therefore does
not invent a status flag that Git itself rejects.

`status.renames=copies` remains the supported status-level mechanism for copy
classification.

## Compatibility boundary

Pygit's non-identical similarity score still uses deterministic
`difflib.SequenceMatcher` byte-sequence scoring rather than Git's exact
`diffcore-rename` implementation. Phase 161 aligns the important public
semantics — config precedence, exact-match fast path, and exhaustive fallback
limit — without claiming algorithmic identity.

## Regression coverage

`tests/test_phase161.py` covers:

- integer/suffix rename-limit parsing;
- `status.renameLimit` > `diff.renameLimit` > default precedence;
- exact renames surviving a low limit;
- approximate renames being suppressed below the candidate count;
- zero/negative unlimited behavior;
- CLI `status` and porcelain-v1 limit behavior;
- porcelain-v2 behavior through `diff.renameLimit` fallback;
- copy similarity gating;
- exact `C100` preimage copies surviving the low-limit gate;
- `--find-renames` not bypassing the configured limit;
- rejection of the diff-only `--find-copies-harder` option by `status`.
