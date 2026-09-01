# Phase 407 — previous-checkout operation helper

Phase407 extends the Phase405/406 previous-checkout and reflog work from validation-only expansion into an operation-level checkout helper.

## Behavior

`pygit.branch_checkout.checkout_previous(repo, "@{-N}")` now:

1. resolves the selector through the existing HEAD checkout-history parser;
2. rejects non-selector input and unavailable history before changing HEAD;
3. passes the concrete branch name or detached local object ID to `Repository.checkout()`;
4. returns that concrete expansion to the caller.

Expanding before checkout is deliberate. Native Git does not persist the literal `@{-1}` token in the resulting checkout reflog record: after `main -> topic -> main`, `git checkout @{-1}` leaves HEAD on `topic` and records `checkout: moving from main to topic`.

Detached previous checkouts are preserved as their genuine full local 64-hex SHA-256 commit identity. No synthetic branch is created.

## Compatibility

The helper reuses `expand_previous_checkout()`, so both modern/native `checkout: moving from X to Y` records and historical pygit `checkout: moving to Y` records keep the Phase405 compatibility behavior.

Ordinary revision checkout remains owned by `Repository.checkout()`. The helper intentionally accepts only Git previous-checkout syntax and fails closed for ordinary revision strings.

## SHA-256-native invariant

This phase does not alter object serialization, hashing, native maps, packfiles, `FETCH_HEAD`, or remote protocol identity. Local object/ref identities remain genuine content-derived 64-hex SHA-256. A detached previous-checkout destination is the real local commit OID; there is no padding, truncation, textual OID rehashing, surrogate SHA-256, or metadata-derived identity.

## Regression coverage

`tests/test_phase407.py` covers symbolic previous checkout, older `@{-N}` selection, detached SHA-256 round trips, fail-before-mutation invalid/unavailable selectors, and a native Git SHA-256 differential proving that the resulting branch and reflog destination are the expanded value rather than the shorthand token.
