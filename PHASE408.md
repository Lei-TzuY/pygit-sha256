# Phase408 — user-facing previous-checkout CLI

Phase408 wires the exact-green Phase407 previous-checkout operation into the installed `pygit checkout` command for the focused Git syntax `checkout @{-N}`.

## Behavior

The top-level application router recognizes only a checkout invocation containing exactly one previous-checkout selector. It expands that selector through Phase405/407 HEAD reflog history before invoking `Repository.checkout()`.

Examples:

- `pygit checkout '@{-1}'`
- `pygit checkout '@{-2}'`

Successful symbolic destinations print the normal branch-oriented checkout message. Detached destinations remain the genuine full local SHA-256 commit ID internally and print the normal abbreviated detached-HEAD message.

`@{-0}` and unavailable history fail closed before HEAD mutation.

## Compatibility boundary

All other checkout syntax is intentionally left on the mature legacy checkout parser. In particular, Phase408 does not intercept branch creation, `--detach`, `--orphan`, patch/path checkout, or other option combinations. This keeps the compatibility change narrow while making the ordinary `git checkout @{-1}` workflow directly available.

The selector is expanded before checkout, so the resulting reflog stores the concrete destination (`checkout: moving from X to Y`) rather than the literal `@{-N}` token, matching native Git.

## Native Git differential

The focused regression creates a native SHA-256 Git repository, performs `main -> topic -> main -> checkout @{-1}`, and verifies both the resulting symbolic branch and `HEAD` reflog subject against pygit.

## SHA-256-native invariant

This phase does not alter object serialization, hashing, packfiles, FETCH_HEAD, object mappings, protocol identities, or the files-ref format. Local object and detached-HEAD identities remain genuine content-derived 64-hex SHA-256 values. Remote/native SHA-1 interoperability elsewhere remains genuine complete 40-hex SHA-1 where required. No padding, truncation, textual identifier rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- exact base: Phase407 / PR #367 head `7d7bd000ee92b9c1c43d91015838e6fbbc005508`
- Phase407 GitHub Actions Tests #3251 / run `33493572785`: success
- `phase408` namespace was checked before branch creation and was free
- active clone/init/protocol-v2/loose-object stacks remain untouched
