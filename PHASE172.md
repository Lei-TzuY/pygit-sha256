# Phase 172 — negative push refspecs and prune

Phase 172 extends the Phase 167–171 push stack with Git-style negative refspec filtering and refspec-aware remote pruning.

## User-visible behavior

### Negative refspecs

A push refspec beginning with `^` excludes matching source refs from the union of positive refspecs:

```text
pygit push origin 'refs/heads/*:refs/heads/*' '^refs/heads/dev-*'
```

The positive pattern selects local branches. The negative pattern removes matching sources before transport planning.

Implemented compatibility rules:

- at least one positive refspec is required when negatives are present
- negative refspecs contain only a source; a destination is rejected
- exact and one-star source patterns are supported
- raw 40- or 64-hex object IDs are rejected as negative refspecs
- filtering applies after all positive refspec expansion, matching Git's union-minus-negative model
- branch and tag namespaces remain the supported Phase 167 ref namespaces

### `--prune`

`pygit push --prune` removes remote refs that are covered by a selected pattern mapping but no longer have the corresponding local source ref.

Examples:

```text
pygit push --prune origin 'refs/heads/*:refs/heads/*'
pygit push --prune origin 'refs/heads/feature/*:refs/heads/archive/*'
pygit push --prune --tags origin
pygit push --all --prune origin
```

Prune planning reads the current receive-pack advertisement rather than relying only on cached remote-tracking refs. For custom mappings, Phase 172 reverses the destination wildcard capture to determine the expected local source. Exact/default refspecs do not implicitly widen the prune domain.

Negative refspecs protect excluded refs from prune as well as from updates. For example:

```text
pygit push --prune origin \
  'refs/heads/*:refs/heads/*' \
  '^refs/heads/private-*'
```

will neither update nor delete refs corresponding to `private-*` sources.

`--tags --prune` treats `refs/tags/*:refs/tags/*` as the prune domain, matching native Git behavior observed with a local bare receive repository.

## Git compatibility references

Current `git push` documentation specifies that:

- a negative refspec starts with `^`, contains only `<src>`, may be a pattern, and excludes refs otherwise matched by positive refspecs
- negative refspecs do not accept fully spelled object IDs
- `--prune` removes remote refs without local counterparts and respects refspec mappings
- `refs/heads/*:refs/tmp/*`-style mappings prune the mapped destination namespace according to the corresponding local source

Phase 172 implements those rules inside the namespaces already supported by pygit's push stack (`refs/heads/*` and `refs/tags/*`). Arbitrary `refs/*` destination namespaces remain outside the current transport scope.

## SHA-256-native design

Negative refspecs and prune are ref-selection operations. They do not change object identity, object serialization, the index, or local ref storage. Existing object-producing updates continue through the SHA-256-to-native-SHA-1 exporter boundary. Prune deletions use the existing receive-pack zero native OID path.

## Composition

Prune-generated deletions are ordinary `PushSpec(delete=True)` entries, so they automatically compose with:

- Phase 168 atomic transactions
- Phase 169 force-with-lease checks
- Phase 170 force-if-includes checks where an implicit lease applies
- Phase 171 push options

The existing transport method signatures are unchanged.

## Scope boundaries

Still deferred:

- `--mirror` / `remote.<name>.mirror`
- `--follow-tags` / `push.followTags`
- annotated-tag SHA-256 -> SHA-1 export completion required for full follow-tags support
- arbitrary revision-expression push sources
- signed pushes
- protocol-v2 send-pack negotiation

## Regression coverage

`tests/test_phase172.py` covers:

- exact and wildcard negative filtering
- negative-only rejection
- destination-bearing negative rejection
- raw object-ID negative rejection
- same-name branch pruning
- custom wildcard destination pruning
- negative patterns protecting refs from prune
- tag pruning
- exact refspecs not widening prune scope
- CLI routing of generated deletion specs
- invalid `--delete --prune` combination
