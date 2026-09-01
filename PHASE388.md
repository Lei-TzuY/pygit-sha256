# Phase 388 — stage verified Git bundle imports

Phase387 verifies Git bundle v2/v3 headers and the embedded pack without touching
repository state.  Phase388 crosses the next trust boundary for **self-contained,
unfiltered SHA-1 bundles only**: convert the complete verified native object graph
to pygit's SHA-256 object model and publish only immutable local objects.

Reference publication remains deliberately outside this phase.

## API

`pygit.git_bundle_stage` adds:

- `StagedGitBundleImport`
- `stage_git_bundle_import(store, bundle)`

The result contains:

- `native_to_local`: every genuine remote-native 40-hex SHA-1 object id mapped to
  the content-derived local 64-hex SHA-256 identity produced by `NativeImporter`;
- `ref_targets`: bundle ref names projected to those local identities for a later
  ref-publication transaction;
- `local_oids`: the unique immutable SHA-256 objects published into the
  destination store.

No ref is written by this API.

## Transaction boundary

The function does not import directly into the destination.

1. Revalidate every native object key/type/content SHA-1.
2. Revalidate every advertised ref name and 40-hex target.
3. Require every ref target to exist in the verified native graph.
4. Create an isolated temporary SHA-256 `ObjectStore`.
5. Run the ordinary `NativeImporter` over **every native object**, not merely the
   advertised roots.  Missing dependencies in an otherwise-unreferenced object
   therefore fail here too.
6. Read every resulting local SHA-256 object back from the staging store.
7. Only after the complete graph succeeds, copy those immutable objects into the
   destination and require their identities to remain byte-for-byte stable.

Validation/conversion failures therefore occur before any destination object
write.  A process/storage failure during the final publication loop may leave
valid unreachable content-addressed objects, but no mutable reference can point
at an incomplete graph because refs are not part of this phase.

## Deliberate exclusions

Phase388 rejects:

- bundles with prerequisites;
- v3 filtered bundles;
- non-SHA-1 remote object formats;
- missing/forged native objects;
- malformed ref metadata.

Prerequisite bundles may contain thin REF_DELTA packs and need a separate
prerequisite-aware importer.  Filtered bundles need a promisor-aware transaction
that can represent intentionally omitted objects without fabricating local
identity.  Neither is silently treated as complete here.

## Native compatibility

The focused native regression creates a real SHA-1 Git repository with commit,
tree, and blob objects, writes a full bundle with native `git bundle create`,
parses it through Phase387, and stages it through Phase388.

The regression proves:

- the Phase387 native object set matches native `git rev-list --objects --all`;
- every native id remains exactly 40 hex;
- every converted local id is exactly 64 hex;
- the advertised `refs/heads/main` target maps to the converted native HEAD;
- the local commit is readable from the SHA-256 ObjectStore with the original
  commit message.

## SHA-256-native invariants

The two identity domains remain explicit.

- bundle/header/pack identities stay genuine full 40-hex SHA-1;
- local identities are produced only from actual converted object content and
  remain full 64-hex SHA-256;
- no SHA-1 padding, truncation, textual re-hashing, surrogate SHA-256, or
  metadata-derived local identity is introduced;
- refs, HEAD, reflogs, shallow state, and promisor metadata are untouched;
- no network fetch is performed by staging.

## Coordination

- actual `main` at Phase388 start: `fb45a0249306ce163dc64b32c508d01bca58e592`
  (Phase420 / PR #382 had been merged by another workstream);
- exact functional base: Phase387 / PR #375 head
  `cf325a16326c4d0ae0abb87abddc6a4ab43958a2`;
- Phase387 authoritative Tests #3311 / run `33533895855`: success;
- Phase388 was collision-checked immediately before branch creation;
- Phase358 already contains a more complete programmatic-unborn-clone API than
  an un-PR'd experimental Phase332 branch, so that duplicate work was not opened
  as a competing PR.

## Tests

`tests/test_phase388.py` covers:

- simple SHA-1 blob -> SHA-256 object conversion;
- full-graph validation beyond advertised roots;
- forged native content;
- prerequisite/filtered/non-SHA-1 rejection;
- missing ref targets and invalid ref names;
- idempotent publication over an existing identical local object;
- explicit non-mutation of repository HEAD/refs/config/shallow/promisor state;
- native Git full-bundle SHA-1 -> pygit SHA-256 differential behavior.

The execution container still cannot reliably clone `github.com`, so GitHub
Actions Python 3.9 / 3.13 on the exact PR head is the authoritative full-suite
gate.
