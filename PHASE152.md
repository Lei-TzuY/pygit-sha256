# Phase 152 — status stash-count reporting

Phase 152 completes another optional Git status protocol surface on top of Phase 151's porcelain-v2 renderer: `pygit status --show-stash` now reports the number of live stash reflog entries without changing ordinary status classification.

## Commands

```bash
pygit status --show-stash
pygit status --porcelain=v2 --show-stash
pygit status --porcelain=v2 --branch --show-stash
pygit status --porcelain=v2 --show-stash -z
pygit status --show-stash --no-show-stash
```

## Porcelain v2

Git porcelain v2 defines stash information as an optional header independent of `--branch`. When `--show-stash` is active and `refs/stash` has entries, pygit emits:

```text
# stash <N>
```

The header follows any requested branch headers and precedes changed-entry records. A zero count emits no stash header. With `-z`, the header participates in the same NUL-framed stream as the remaining porcelain-v2 records.

## Long status

The human-readable renderer follows Git's singular/plural wording:

```text
Your stash currently has 1 entry
Your stash currently has 2 entries
```

No extra line is emitted when the stash is empty. Short format and porcelain v1 deliberately remain unchanged because they do not define a stash metadata record.

## Reflog source of truth

Counting reuses Phase 72/128's strict `refs/stash` reflog parser rather than inferring a count from the current stash ref. This matters because older stash entries live only in the reflog. A missing reflog means zero entries; malformed or unsafe existing reflog metadata remains an error rather than producing a misleading machine-readable count.

`--show-stash` and `--no-show-stash` are ordinary opposing boolean controls. If both are supplied, the last option wins, matching Git's command-line override style.

## Safety

Status remains read-only. Stash refs, reflogs, index entries, worktree files, objects and branch metadata are never modified while counting or rendering stash information.
