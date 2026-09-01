# Phase409 — `checkout -` previous-checkout shorthand

Phase409 extends the exact-green Phase408 previous-checkout CLI path with Git's one-character shorthand:

```text
pygit checkout -
```

This is treated exactly as:

```text
pygit checkout @{-1}
```

## Behavior

- only the exact two-token command `checkout -` is intercepted by the stable application router;
- all other checkout grammar remains on the mature legacy parser;
- the shorthand is translated to `@{-1}` before the existing reflog-backed previous-checkout resolver runs;
- branch destinations remain symbolic branches;
- detached destinations remain their genuine full local SHA-256 commit IDs;
- the shorthand fails before mutating HEAD when no previous checkout exists;
- reflog messages record the resolved destination (`checkout: moving from X to Y`), never the literal `-` token.

## Native Git differential

The focused regression creates SHA-256 repositories in native Git and pygit, performs:

```text
main -> topic -> main -> checkout -
```

and compares both the resulting symbolic branch and the latest HEAD reflog subject. Native Git treats `checkout -` as the previous branch/commit shorthand; Phase409 matches that behavior.

## SHA-256-native boundary

This phase changes only CLI routing and selector translation. It does not change object serialization, hashing, packfiles, FETCH_HEAD, native maps, protocol identity, or ref storage. Local detached identities remain genuine content-derived 64-hex SHA-256 OIDs; native/remote SHA-1 identities elsewhere remain genuine complete 40-hex values where interoperability requires them.

## Coordination

- exact base: Phase408 / PR #368 head `baae7f12022f958b2acedf1eacfbf5f8c66b0d8d`;
- Phase408 GitHub Actions Tests #3257 / run `33498700327`: success;
- `phase409` namespace was collision-checked and free before creation;
- clone/init/protocol-v2/loose-object durability stacks were left untouched.
