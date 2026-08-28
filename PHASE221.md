# Phase221 — Multi-promisor fallback materialization

Phase221 removes pygit's previous single-promisor restriction for lazy partial-clone object materialization.

## Motivation

Git supports more than one promisor remote. Missing objects are attempted against promisor remotes in order until all requested objects have been obtained. This is useful when a nearby cache can satisfy most objects while the canonical remote remains the fallback.

Before this phase, `.pygit/promisor.json` could record multiple promisor remotes, but `materialize_promised_objects()` rejected that state with `repository does not identify exactly one promisor remote`.

## Behavior

`materialize_promised_objects()` now:

1. validates every requested native SHA-1 against the global promised-object set;
2. orders recorded promisor remotes by the repository's configured remote order;
3. asks the first configured promisor for the complete unresolved batch;
4. imports every requested object that remote actually supplied;
5. atomically records those native-SHA1 -> local-SHA256 resolutions;
6. retries only the still-missing set against the next promisor;
7. raises `PromisorMissingError` only after the configured fallback set is exhausted.

A transport/protocol failure from an earlier cache-like promisor is not authoritative; pygit continues to the next promisor. Missing remotes left behind in promisor metadata are skipped, preserving the existing intentional-missing error contract if no configured fallback can provide the object.

## Batching

The fallback loop keeps Phase214's bulk-prefetch behavior. If several objects remain unresolved, they are requested together from each remote. As objects are resolved, later remotes receive only the reduced missing set.

For one remaining object, the Phase213 `_fetch_native_object` compatibility seam remains in use.

## Server options

Each fallback attempt obtains `remote.<name>.serverOption` for the remote currently being contacted. Options from one promisor are never reused for another.

## SHA-256-native identity

The fallback policy changes only where missing native Git objects are retrieved from. Requested wants remain native SHA-1 at the protocol boundary; fetched objects are imported through the established native importer and stored under their real content-derived SHA-256 identities. No surrogate SHA-256 ids are introduced.

## Compatibility boundary

pygit does not yet model Git's special `extensions.partialClone` primary-remote marker, whose remote Git deliberately tries last. Phase221 therefore uses configured remote order for all recorded promisor remotes. Adding the primary-marker ordering rule can be done independently once pygit's config model exposes that distinction.

## Verification

Focused regression coverage exercises:

- a two-remote batch where the first promisor resolves only one object and the second receives only the remaining want;
- per-remote server-option isolation;
- fallback after an earlier transport/protocol failure;
- exhaustion preserving `PromisorMissingError` and unresolved metadata;
- compatibility of the legacy single-owner validation helper;
- skipping a recorded promisor whose remote configuration has been removed.
