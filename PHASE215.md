# Phase 215 — Clone no-checkout

Phase215 adds Git-style `pygit clone -n` / `pygit clone --no-checkout` across ordinary, shallow, and partial clone paths.

## Behavior

Git documents `-n` / `--no-checkout` as completing the clone without checking out `HEAD`. pygit now preserves the cloned branch/ref/tracking state while leaving the worktree unpopulated.

Examples:

```bash
pygit clone -n https://example.test/repo.git
pygit clone --no-checkout --depth=1 https://example.test/repo.git
pygit clone --no-checkout --filter=blob:none https://example.test/repo.git
```

For ordinary clones, Phase215 preserves the historical `Repository.clone` API/call shape and command-scopes suppression of only the final worktree replacement step. The original method is restored in `finally`.

For protocol-v2 shallow clones, the depth-limited fetch, shallow boundary, refs, native map, tags, and configuration are still created; only worktree replacement is skipped.

For partial clones, no-checkout has an additional bandwidth/storage benefit: current-HEAD promised blobs are not materialized just to populate the initial worktree. They remain in `.pygit/promisor.json` until a later operation actually needs them. Repository-visible object/ref identities remain SHA-256; native SHA-1 stays confined to the Git interoperability/promisor boundary.

## Compatibility

The default clone paths deliberately keep their existing call shapes when `--no-checkout` is absent, avoiding regressions in established monkeypatch/caller seams.

## Verification targets

- CLI forwards `checkout=False` only when requested for shallow/partial transports.
- ordinary clone suppression restores `Repository._replace_worktree_from_commit` after the command scope.
- no-checkout partial clone leaves the target branch/HEAD configured while the promised HEAD blob remains unresolved and absent from the worktree.
- existing Phase214 partial-clone checkout behavior remains unchanged by default.
