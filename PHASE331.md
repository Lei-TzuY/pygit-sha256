# Phase 331: Orchestrate protocol-v2 unborn empty clones

Phase331 connects the exact-green Phase315/317 unborn-reference work to the
public `pygit clone` command. A protocol-v2 server that explicitly reports

`unborn HEAD symref-target:refs/heads/<branch>`

can now produce a successful metadata-only empty clone without fabricating an
object id or entering the legacy fetch/import path.

## Why this is a separate phase

The historical `Repository.clone()` transport is protocol-v0 shaped and its
fetch path requires at least one concrete advertised object id. Phase315 added a
strict protocol-v2 result channel that preserves explicit unborn metadata, and
Phase317 added the local symbolic-HEAD initialization primitive. Phase331 is the
orchestration layer between those boundaries and the mature clone CLI.

Non-empty and protocol-v0 repositories deliberately remain on the established
clone, shallow-clone, and partial-clone implementations.

## Implementation

New module: `pygit/clone_unborn.py`

- resolve and validate the clone destination before network access;
- query with `SmartHttpV2UnbornQueryClient`, forwarding ordered server options;
- return `None` for protocol-v0 fallback and ordinary non-empty v2 results;
- accept only an explicit standalone unborn `HEAD` symref result;
- reject `--branch` even when it spells the unborn target, matching native Git's
  requirement that `--branch` select a concrete remote ref;
- initialize the repository only after the explicit unborn shape is validated;
- reuse Phase317 `initialize_empty_remote_head()` for the actual HEAD update;
- persist the server default branch in the historical remote config used by
  later pygit fetches;
- persist Git-visible remote URL and branch upstream configuration;
- omit `remote.origin.fetch` for `--single-branch` empty clones because no
  concrete selected ref exists;
- retain the wildcard fetch refspec for ordinary / explicit
  `--no-single-branch` empty clones;
- preserve protocol-v2 preference for shallow/partial clone modes without
  creating a shallow boundary or promisor object state;
- persist partial-clone remote/filter config when `--filter` was requested;
- roll back all locally created clone metadata if a post-init step fails.

`pygit/clone_cli.py` performs the unborn preflight only when the production clone
function for the selected mode is still installed. Existing tests/callers that
replace `Repository.clone`, `clone_partial_repository`, or
`clone_shallow_repository` therefore keep their historical call shape and do
not receive a hidden network preflight.

## Native Git compatibility

Local Git 2.47.3 SHA-256 probes against an empty bare repository whose initial
branch is `topic/empty` established:

- default clone succeeds with `HEAD -> refs/heads/topic/empty`;
- `remote.origin.fetch` is the normal wildcard refspec;
- `branch.topic/empty.remote=origin` and
  `branch.topic/empty.merge=refs/heads/topic/empty` are present even though the
  remote-tracking ref does not exist;
- `--single-branch` succeeds but omits `remote.origin.fetch` entirely;
- `--depth 1` behaves like single-branch and creates no `shallow` boundary;
- `--depth 1 --no-single-branch` restores the wildcard fetch refspec;
- `--no-checkout` still produces the same unborn reference/config state;
- `--filter=blob:none` preserves the partial-clone remote/filter configuration
  without any object to materialize;
- explicit `--branch topic/empty` fails with `Remote branch ... not found` and a
  newly-created destination is removed.

The Phase331 regression suite repeats the native default/single/depth metadata
matrix and explicit-branch failure on the CI runner's Git.

## SHA-256-native / no-fetch invariants

An unborn ref is reference metadata, not an object identity.

- no 64-hex local object id is invented;
- no zero object id is written as a branch tip;
- no 40-hex SHA-1 is padded, truncated, translated, or used as a surrogate
  SHA-256;
- the explicit empty path performs no fetch request after `ls-refs` discovery;
- no native object importer runs;
- no object is written to `.pygit/objects`;
- no `.pygit/shallow` boundary is created;
- no `.pygit/promisor.json` object promise is created or modified;
- partial-clone configuration may be recorded, but there is no promised object
  until a future concrete fetch actually omits content.

## Coordination

- actual `main` at phase start:
  `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact stacked base: Phase317 / PR #295 head
  `6124d0fb7d8ceb17e5a9ad1ecd5607b3e2cdeb93`
- Phase317 authoritative Tests #2757: Python 3.9 / 3.13 both 2356 passed
- Phase321 through Phase330 were already occupied by an independent packfile-URI
  stack when this phase started and were intentionally untouched
- newest independent PR at coordination time: Phase330 / PR #306
- Phase331 was rechecked as free immediately before branch creation

## Regression coverage

`tests/test_phase331.py` covers:

- default empty clone metadata and no-object state;
- single-branch omission of the fetch refspec;
- shallow empty clone with no shallow boundary;
- explicit `--no-single-branch` shallow metadata;
- partial-clone config with no promisor objects;
- explicit-branch failure before destination creation;
- protocol-v0 and ordinary non-empty v2 fallback without mutation;
- conflicting explicit-unborn results;
- rollback for newly-created and pre-existing empty destinations;
- CLI short-circuit before the legacy transport;
- Repository/partial/shallow override-seam preservation;
- native Git empty-clone config parity;
- native explicit-branch failure and destination cleanup.

The execution container still cannot reliably clone the GitHub repository, so
the exact-head GitHub Actions Python 3.9 / 3.13 matrix is the authoritative
full-suite gate for this phase.
