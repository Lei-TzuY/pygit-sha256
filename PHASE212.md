# Phase 212 — protocol-v2 partial fetch / promisor foundation

Phase212 starts partial-clone support at the fetch/storage boundary without pretending that filtered clone is ready before on-demand object materialization exists.

## User-facing fetch filter

`pygit fetch` now accepts one protocol-v2 filter:

- `--filter=blob:none`
- `--filter=blob:limit=<positive-bytes>`

The split `--filter VALUE` form is also accepted. The standard `--` option terminator is preserved.

This first phase intentionally supports one named remote and rejects combinations whose ownership is already complex (`--all`, `--multiple`, prefetch/refetch/negotiate-only, update-shallow, and explicit shallow-history controls). Negotiation controls, dry-run, ref mapping and set-upstream continue through the established inner fetch stack.

## Protocol-v2 transport

Filtered fetch is protocol-v2-only. The server must advertise the `filter` feature on its `fetch` capability. The command-scoped transport emits:

`filter <filter-spec>`

in the fetch argument section after the v2 command capability delimiter and before `want` lines.

Explicit fetch `-o/--server-option` values retain CLI precedence over `remote.<name>.serverOption`; the effective ordered values are attached to the same v2 client and therefore remain in the command capability-list.

Protocol-v0 fallback is forbidden rather than silently dropping the requested filter.

## Why filtered clone is not exposed yet

A `blob:none` clone normally omits the files required to populate the initial worktree. Until pygit can lazily request promised blobs, exposing `clone --filter` would create a repository that cannot complete checkout. Phase212 therefore implements filtered fetch and the persistent object model first. A later phase can add on-demand materialization and then enable filtered clone safely.

## Stable foreign-tree identity

Ordinary pygit trees are unchanged: they still store 32-byte local SHA-256 child object ids.

A filtered foreign tree uses a separate canonical payload beginning with `pygit-native-tree-v1\\0`. Every tree entry stores its original 20-byte native Git SHA-1 object id. This solves the core identity problem for omitted blobs:

- the local tree SHA-256 can be computed even when a blob body is absent;
- no surrogate or fake SHA-256 blob id is invented;
- when a blob is later materialized, the tree payload and tree SHA-256 do not change;
- runtime resolution maps native entry ids to real local SHA-256 ids through promisor metadata.

`TreeEntry.sha` raises `PromisorMissingError` for an unresolved promised object. Operations that genuinely need file data therefore report an intentional promisor miss instead of generic object corruption.

## Persistent promisor state

`.pygit/promisor.json` records:

- promisor remote and effective filter;
- promised native SHA-1 objects and their kinds;
- native SHA-1 -> local SHA-256 resolutions for materialized tree children.

ObjectStore receives a small package-installed extension which resolves foreign-tree entries on read without rewriting the content-addressed tree object.

## Filtered importer

`PromisorFilteredNativeImporter` extends the modern tag-preserving importer.

For native trees:

- available blob/tree dependencies are converted normally;
- an omitted non-directory entry is recorded as a promised blob;
- an omitted subtree is rejected as a malformed filtered pack because the supported blob filters must not omit the required tree graph;
- every foreign tree is stored in the stable native-entry representation.

Commits and tags continue using the established SHA-256-native models.

## Compatibility boundary

Normal fetches and clones do not use the promisor importer and preserve their historical object identities and call shapes. Phase204/206 shallow foreign-commit semantics are also left untouched.

Phase212 does not yet perform lazy network materialization of a promised blob. That is the next architectural step before `clone --filter` can be enabled.

## Verification target

- base: Phase211 / PR #188 exact head `09e95ed35bbedbd55c905b90d0c878a843b2f4c3`
- Phase211 GitHub Actions Tests #1892: success on Python 3.9 / 3.13 before Phase212
- focused coverage: ordinary-tree encoding compatibility, stable native-tree identity, late resolution, missing-blob promise recording, missing-subtree rejection, filter capability/order, CLI parsing, server-option ownership and no-filter transparency
- full GitHub Actions Python 3.9 / 3.13 matrix remains the final gate
