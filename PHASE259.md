# Phase259: `rev-list --in-commit-order`

Phase259 adds Git-style commit-ordered object presentation without changing revision selection, object identity, or partial-clone materialization semantics.

## Native Git behavior

Current Git documents `--in-commit-order` as an object-traversal ordering mode: tree and blob ids are printed in commit order, after they are first referenced by a commit.

A deterministic native SHA-256 Git fixture with cumulative commits `c1 <- c2 <- c3` confirms the important detail: each selected commit is followed by the complete snapshot reachable from that commit, but object ids are globally deduplicated. With normal reverse-chronological commit selection, the first visible snapshot can therefore introduce blobs that also exist in older commits; those blobs are not repeated later. With `--reverse`, the oldest commit's snapshot is visited first, so later commits introduce only objects not seen in earlier snapshots.

Phase259 mirrors that behavior directly on the metadata-only inventory substrate.

## Implementation

The existing Phase232 inventory intentionally emits all selected commits before walking snapshot objects. Phase259 leaves that default behavior untouched and introduces a focused adapter which:

1. reuses the existing revision parser after projecting away `--in-commit-order`;
2. obtains selected commits from the existing `rev_list()` selector;
3. appends each selected commit record;
4. immediately walks that commit's full root tree with the existing Phase232 `_walk_tree()` helper;
5. reuses one global object `seen` set across every commit snapshot;
6. applies the existing explicit-negative-revision closure subtraction after the ordered walk.

No second tree representation or object-id translation layer is introduced.

## Supported modes

Phase259 supports line-oriented:

- ordinary `--objects --in-commit-order`;
- `--missing=allow-promisor`;
- `--missing=print`;
- `--missing=print-info`;
- `--reverse`;
- `--skip`;
- `--max-count` / `-n`;
- `--topo-order`;
- `--first-parent`;
- `--all`;
- `--count`;
- `--no-object-names`.

`--count` changes only presentation: present records are counted, unresolved promises are not, and print/print-info missing records remain visible before the final integer just as in the existing missing-object count path.

## Partial-clone safety

Foreign promised tree entries are never materialized merely to determine order. The existing tree walker reports unresolved promises through their persistent native identity and kind metadata.

- ordinary mode fails before producing any output if an unresolved promise is encountered;
- `allow-promisor` omits unresolved promises;
- `print` emits `?<native-oid>`;
- `print-info` emits the existing `?<native-oid> path=... type=...` metadata channel.

Present objects remain genuine repository-visible 64-hex SHA-256 ids. Foreign native SHA-1 identities remain confined to explicit missing-object channels. No surrogate SHA-256 is invented.

## Deliberately deferred combinations

Phase259 rejects rather than guesses semantics for:

- `--boundary`;
- `--objects-edge`;
- `-z`;
- `--filter=...` / `--filter-print-omitted` / `--filter-provided-objects`;
- `--disk-usage`.

Boundary traversal has a distinct selected/boundary commit presentation stream and therefore needs an explicit snapshot-interleaving model. NUL and filter modes have their own structured framing and remain separate phases. This also keeps Phase259 orthogonal to the parallel Phase258 `blob:limit` work.

## Coordination

Phase259 was created while Phase258 existed as an active, not-yet-green parallel branch. To preserve the stack invariant, Phase259 is based on the latest exact green predecessor available at creation time:

- base: Phase257 / PR #234 exact green head `da7b48922b3ff67af6e18e005ea21387a3e1d1bd`;
- Phase257 Tests #2271: Python 3.9 / 3.13 success;
- Phase258 / PR #235 is intentionally not used as a base until its exact head is green.

## Verification

Focused regression coverage includes:

- exact normal commit/snapshot interleaving;
- reverse traversal and changed first-seen object positions;
- max-count behavior with full visible snapshots;
- foreign `blob:none` promises appearing at their first snapshot position;
- plain print and print-info missing framing;
- count semantics;
- `allow-promisor` zero-fetch omission;
- ordinary partial-clone refusal before any output;
- unchanged promisor state;
- explicit rejection of deferred presentation combinations.

Full GitHub Actions on Python 3.9 and 3.13 is the authoritative gate.
