# Phase 352: Persisted partial filters across unborn first fetch and pull

Phase352 closes the explicit gap left by Phase335 and Phase337 for repositories
created as empty partial clones.  Native Git remembers a partial clone's
`remote.<name>.partialCloneFilter`, automatically reuses it on later ordinary
fetches, and materializes only checkout-required missing blobs when the first
pull turns an unborn branch into a real branch.

## Why this phase exists

Phase331 can clone an explicitly empty remote with `--filter=blob:none` and
persist the partial-clone configuration without inventing any promised object.
Phase335 later learned how an ordinary empty clone fetches the remote's first
commit while keeping the local branch unborn, but deliberately excluded
persistent promisor remotes: taking that source-only fallback under an
unfiltered transport would silently defeat partial-clone semantics.

Phase337 similarly bootstraps an ordinary unborn branch on first pull, but
rejects an empty partial clone before network access until a filter-aware fetch
and checkout-materialization path exists.

Phase352 provides those missing composition boundaries.

## Persisted-filter fetch routing

New module `pygit/fetch_persisted_partial.py` wraps the established fetch
frontend.

- explicit `--filter` remains authoritative;
- otherwise, a selected named remote with a validated
  `remote.<name>.partialCloneFilter` automatically re-enters the exact existing
  filtered-fetch transport;
- `remote.<name>.promisor=true` alone never invents a filter;
- invalid persisted filters fail before an ordinary unfiltered fallback;
- unsupported/complex filtered fetch modes continue to fail through the
  existing Phase212 compatibility guards instead of silently over-fetching;
- Phase335's source-only unborn selector may include a persistent partial remote
  only while a real filtered transport is already active.

`pygit/application.py` now routes `fetch` through this persisted-filter wrapper.
Non-partial fetches still delegate to the Phase335 frontend unchanged.

## Filtered initial pull

New module `pygit/pull_unborn_partial_transition.py` extends the exact-green
Phase337 transition.

For an unborn branch whose configured upstream is a persisted partial-clone
remote:

1. validate the recorded filter instead of inferring one from the promisor bit;
2. run `fetch_porcelain()` inside both the Phase335 unborn selection scope and
   the established `partial_filter_transport()`;
3. keep the local branch unborn while fetch updates FETCH_HEAD / optional
   remote-tracking / promisor state;
4. run Phase337's staged/untracked/path-prefix/symlink checkout preflight;
5. use Phase214's `_collect_checkout_promises()` to identify only unresolved
   blobs needed by the first commit's worktree;
6. batch those exact native OIDs through `materialize_promised_objects()`;
7. populate the worktree and only then publish the local branch with the
   established `initial pull` reflog message.

A conflict or materialization failure therefore keeps the local branch unborn.
Already-completed fetch/promisor effects remain, matching the same fetch-before-
checkout transaction boundary established in Phase337.

## Native Git compatibility

Local Git 2.47.3 SHA-256 differential probes use an initially empty bare remote
with `uploadpack.allowFilter=true` and a client cloned with
`--filter=blob:none`.

After the remote publishes its first commit:

- plain `git fetch origin` automatically sends `filter blob:none` again;
- the default clone creates `origin/<branch>` and FETCH_HEAD but leaves the
  local branch unborn;
- an empty `--single-branch` partial clone still has no persistent
  `remote.origin.fetch` and creates no `origin/<branch>` tracking ref;
- plain `git pull` first obtains the commit/tree through the filtered fetch,
  then performs an explicit missing-object fetch for checkout content, and only
  afterwards resolves the local branch;
- the checked-out file contains the real blob content.

The Phase352 CI regressions repeat the native filtered-fetch and single-branch
first-pull lifecycle on the runner's Git.

## SHA-256-native / promisor invariants

- remote transport wants/promises remain genuine full 40-hex native SHA-1 ids;
- fetched and materialized local objects receive only content-derived full
  64-hex SHA-256 identities through the established importers;
- no SHA-1 padding, truncation, textual re-hashing, or surrogate SHA-256;
- persisted filter metadata controls transport omission but never manufactures a
  local identity;
- first-pull checkout materializes only promises reachable from the selected
  worktree, not every historical promised blob;
- a promisor remote without an explicit persisted filter fails closed rather
  than guessing how to fetch it.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase337 / PR #314 head
  `b2639d05a1dca7c3b444029262770bdb4a4b6114`
- Phase337 authoritative Tests #2896: success
- Phase331 / PR #308 exact-green: Tests #2826, Python 3.9 / 3.13 both
  2374 passed, Git 2.55.0
- the independent packfile-URI/object-map line occupies Phase318-351 and is not
  modified by this phase
- branch and PR searches found no existing unborn-partial first-fetch/pull work
- Phase352 was collision-checked immediately before branch creation

## Regression coverage

`tests/test_phase352.py` covers persisted-filter validation, promisor-only
non-inference, automatic filter injection, explicit-filter precedence,
non-partial delegation, filter-aware source-only selection, filtered partial
first-pull orchestration, batched checkout materialization, conflict-before-
materialization behavior, materialization failure, missing-filter fail-closed
behavior, Phase337 delegation, and native Git filtered empty-clone fetch/pull
lifecycles.

The local execution container cannot reliably clone `github.com`; GitHub Actions
Python 3.9 / 3.13 on the exact Phase352 head is the authoritative full-suite
gate.
