# Phase 144 — fsck explicit reachability heads

Phase 144 adds Git-style positional object heads to `pygit fsck` and makes the reachability model controllable for recovery and isolated integrity analysis.

## Commands

```bash
pygit fsck HEAD~2
pygit fsck rescue-tag other-commit
pygit fsck --connectivity-only <object>
pygit fsck --cache <object>
pygit fsck --lost-found <object>
```

When no positional objects are supplied, the existing default root policy is unchanged: refs, index entries, shallow declarations, and (for the installed CLI) reflog old/new OIDs participate in reachability.

When one or more positional objects are supplied, they become the complete reachability head set. Ref and reflog tips no longer make unrelated objects reachable, and index entries are excluded unless `--cache` is present. This follows native `git fsck [<object>...]` semantics, where explicit objects are heads for the unreachability trace and the default ref/index/reflog heads are used only when no objects are given.

## Resolution and diagnostics

Each supplied object is resolved through pygit's existing revision resolver, so full object IDs, unique abbreviations, refs, tags, and supported revision expressions use the same lookup rules as other plumbing commands. A value that cannot be resolved produces an error-level `bad-root` diagnostic and a failing exit status instead of silently falling back to repository refs.

Multiple explicit heads are supported and their reachable closures are unioned.

## `--cache`

`--cache` opts current index entries back into the explicit head set. Without positional objects the index was already part of the historical default root policy, so `--cache` is intentionally redundant in that mode.

This is useful when checking a recovery commit while preserving staged-but-uncommitted objects from dangling classification.

## Shallow repositories

A shallow declaration remains a traversal boundary even in explicit-head mode, so a supplied shallow commit does not require its intentionally absent parent. Unlike default mode, shallow declarations are not independently promoted to reachability heads when explicit objects are present.

## Connectivity-only and recovery

`--connectivity-only <object>` visits only the closure reachable from the supplied heads (plus index entries when `--cache` is used). Unrelated published or dangling storage is not inventoried.

Full scans still validate all primary loose/packed storage, but reachable/unreachable and dangling classification is computed from the explicit head set. Therefore `--lost-found <object>` can deliberately materialize repository objects that are outside a chosen recovery root, while the supplied object itself remains protected.

## Python API

```python
report = fsck(repo, heads=["rescue-tag"])
report = fsck(repo, heads=[oid], include_index=True)
```

The extended signature is:

```python
fsck(
    repo,
    *,
    connectivity_only=False,
    include_reflogs=False,
    heads=(),
    include_index=None,
)
```

`include_index=None` preserves the native default: index entries are roots when no explicit heads are supplied, and are excluded when explicit heads are present. `include_index=True` corresponds to CLI `--cache` for explicit-head mode.

All operations remain read-only except the already-explicit `--lost-found` recovery writer.