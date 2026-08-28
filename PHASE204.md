# Phase 204 — true protocol-v2 shallow clone

Phase202 / PR #180 made subsequent `fetch --depth`, `--deepen`, and
`--unshallow` exchanges protocol-correct, but initial `clone --depth` still
performed a full historical download and only marked a logical shallow boundary
after conversion.

Phase204 removes that bandwidth limitation from the CLI shallow-clone path.

## Stable foreign commit identity

A genuinely truncated Git pack can contain a native commit whose parent object
is not present. pygit's ordinary importer historically translated every native
parent SHA-1 to a local SHA-256 id before it could write the child commit, which
made such a pack impossible to import.

Phase204 does **not** invent a fake local id and does **not** rewrite commits
when history is deepened. Instead, shallow-imported commits use an extended
local commit payload:

```text
tree <local-sha256>
parent-sha1 <native-sha1>
author ...
committer ...

message
```

The complete payload is still hashed normally by the SHA-256 object store. The
native parent header is therefore stable content, not side metadata that changes
the object id later.

A repository-side `.pygit/foreign-commits.json` index records native commit
SHA-1 -> local commit SHA-256 mappings for commit objects that have actually
arrived. `ObjectStore.read()` resolves all native parents lazily when every
direct parent is present. Until then the commit remains root-like, matching
shallow-boundary semantics. When a deepen operation imports the missing parent,
the same child SHA-256 immediately exposes the restored parent edge.

For merge commits, Phase204 exposes either the complete direct parent list or no
parents. It never turns a resolved second parent into a synthetic first parent.

## True `clone --depth`

The CLI now routes a depth clone through `clone_shallow_repository()` instead of
calling the historical full `Repository.clone(..., depth=...)` path.

The flow is:

1. initialize the repository and configure `origin`;
2. require smart-HTTP protocol v2;
3. discover refs with `ls-refs`;
4. select the requested/default branch (or all branch tips under
   `--no-single-branch`);
5. issue protocol-v2 `fetch` with `deepen <depth>` and no local haves;
6. parse the genuinely truncated pack;
7. import commits through `StableShallowNativeImporter` even when boundary
   parents are absent;
8. translate returned `shallow-info` to local SHA-256 `.pygit/shallow` entries;
9. write remote tracking refs, the local checked-out branch, and worktree;
10. persist `protocol.version=2` so Phase202 deepen/unshallow commands continue
    on the same transport model.

The initial transfer no longer needs the omitted parent object graph merely to
compute local child commit identities.

## Deepen continuity

Phase202's shallow fetch importer now also uses `StableShallowNativeImporter`.
That means repositories created by Phase204 can be deepened incrementally:
newly arrived parent commits extend `.pygit/foreign-commits.json`, and ordinary
repository object reads reconnect the graph without changing pre-existing child
SHA-256 ids.

Existing older logical-shallow repositories remain compatible. Their already
converted ordinary commits continue to use local SHA-256 parent headers; newly
received shallow commits may use the stable native-parent representation.

## Scope and compatibility

- Non-depth clone behavior is unchanged.
- `clone --depth` still implies `--single-branch` unless the caller explicitly
  supplies `--no-single-branch`, matching the existing CLI policy.
- `--branch` is validated against advertised branch refs before transfer.
- This phase selects branch tips for the true shallow transfer. Broader Git tag
  auto-follow parity during initial shallow clone remains a follow-up rather
  than forcing unrelated tag wants into a depth-limited pack.
- The historical `Repository.clone(depth=...)` method is retained for API
  compatibility in this phase; the user-facing `pygit clone --depth` command is
  the new bandwidth-saving path.
- Native SHA-1 ids remain an interoperability detail. Repository refs, object
  ids, shallow boundaries, local commits, and user-visible revision ids remain
  SHA-256.

## Regression coverage

`tests/test_phase204.py` covers:

- importing a commit whose native parent is completely absent from the pack;
- stable child SHA-256 identity before and after that parent arrives;
- persistent foreign commit mapping across repository reopen;
- a depth-1 clone fixture whose returned object set deliberately omits the
  boundary parent;
- local `.pygit/shallow` translation and protocol-v2 persistence;
- `--no-single-branch` branch-tip selection.
