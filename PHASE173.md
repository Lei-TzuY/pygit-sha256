# Phase 173 — push `--follow-tags`

Phase 173 extends the modern push-selection stack with Git-style automatic
following of reachable annotated tags.

## User-facing options

```bash
pygit push --follow-tags origin main
pygit push --no-follow-tags origin main
```

The local configuration variable is also honored:

```bash
pygit config push.followTags true
```

`push.followTags` defaults to false.  Explicit `--follow-tags` and
`--no-follow-tags` override the configured value.

## Selection rule

`--follow-tags` keeps every ref that the push already selected and then adds
local annotated tag refs that satisfy all of these conditions:

1. the tag is under `refs/tags/`;
2. the tag ref points to an annotated `TagObject`, not directly to another
   object as a lightweight tag does;
3. recursively peeling the annotated-tag chain ends at a commit;
4. that commit is reachable from at least one non-deletion source ref already
   selected by the push plan;
5. the remote receive-pack advertisement does not already contain the same tag
   ref name;
6. the tag was not already selected explicitly; and
7. a negative tag refspec does not exclude it.

The resulting tag additions are deterministic and sorted by local tag name.

This matches current Git's documented rule: `--follow-tags` adds annotated tags
missing from the remote whose commit-ish targets are reachable from refs being
pushed.

## Annotated versus lightweight tags

A lightweight tag is only a ref pointing directly at a commit/object.  It is
not followed automatically.

For example, if `main` reaches commit `A` and both of these exist locally:

```text
refs/tags/light-A -> A
refs/tags/release -> annotated-tag-object -> A
```

then:

```bash
pygit push --follow-tags origin main
```

adds `release` but not `light-A`.

`--tags` remains different: it selects every local tag, annotated or
lightweight.

## Reachability

Reachability is calculated from the *source refs* being pushed, not their remote
destination names.  Branch sources naturally contribute their commit tips.
Explicit lightweight or annotated tag sources can also contribute a commit-ish
root after peeling.

Annotated tags pointing at ancestors of a pushed branch tip are therefore
followed.  Tags pointing at commits on unrelated history are not.

The graph walk uses the shared shallow-aware commit plumbing.  A `.pygit/shallow`
boundary stops parent traversal, so the selector does not claim reachability
through history that the local repository intentionally treats as unavailable.

## Nested annotated tags

Nested annotated tags are recursively peeled.  If both `inner` and `outer`
ultimately peel to a commit reachable from the pushed refs, both tag refs may be
followed when missing remotely.

Tag cycles fail closed rather than looping.

## Existing remote tags

Follow-tags is additive only.  If receive-pack already advertises
`refs/tags/<name>`, that name is skipped even when its remote object differs from
the local tag object.

This is deliberately different from an explicit forced tag refspec.  Automatic
following never turns into an implicit tag replacement.

## Negative refspecs

Phase 172 negative tag refspecs also constrain automatically followed tags.
For example:

```bash
pygit push --follow-tags origin main '^refs/tags/private-*'
```

can follow other reachable annotated tags while excluding matching private tag
names.

The same rule applies when negative patterns come from `remote.<name>.push`.

## Composition

Followed tags are appended to the existing `PushPlan` before transport.  They
therefore compose with earlier push phases without special transport forks:

- Phase 168 `--atomic` puts branch updates and followed tags in one transaction;
- Phase 169 force-with-lease remains scoped by the resulting ref selection;
- Phase 170 force-if-includes remains attached to applicable implicit leases;
- Phase 171 push options are transmitted with pushes containing followed tags;
- Phase 172 prune deletions may coexist in the same plan.

Deletion specs do not contribute reachability roots.

## Compatibility boundary

Phase 173 does not add `--mirror`, signed pushes, arbitrary new ref namespaces,
or submodule push recursion.  It also does not force replacement of an existing
remote tag.

## SHA-256-native design

All tag inspection, recursive peeling, and commit reachability operate on local
SHA-256 object IDs.  The phase changes only ref selection.  Existing smart-HTTP
transport code remains responsible for the native SHA-1 boundary when the
selected tag refs are actually sent.

## Regression coverage

`tests/test_phase173.py` covers:

- boolean config parsing and CLI precedence;
- reachable ancestor/tip annotated tags;
- lightweight-tag exclusion;
- unrelated-history exclusion;
- existing remote tag suppression;
- exact/wildcard negative tag refspec filtering;
- configured `remote.<name>.push` negatives;
- nested annotated tags;
- lightweight tag sources contributing a reachable commit root;
- deletion-only plans;
- shallow boundaries;
- duplicate avoidance with `--tags`-style plans;
- configured follow-tags in sequential CLI transport;
- `--no-follow-tags` preserving the legacy single-branch path; and
- atomic batch composition.
