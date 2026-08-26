# Phase 146 — fsck root and annotated-tag diagnostics

Phase 146 adds the remaining graph-summary diagnostics `fsck --root` and `fsck --tags` on top of the validated Phase 60/142-145 object database and reachability machinery.

## Commands

```bash
pygit fsck --root
pygit fsck --tags
pygit fsck --root --tags --no-dangling
pygit fsck --root HEAD
```

## Root reporting

`--root` prints every validated commit with no traversal-visible parent as:

```text
root <oid>
```

The scan is intentionally database-wide. Root commits are reported whether they are reachable or unreachable, and explicit positional fsck heads do not narrow the diagnostic inventory. `--no-dangling` suppresses only dangling output and does not suppress roots.

A commit named by `.pygit/shallow` is treated as a synthetic root because fsck intentionally removes parent edges beyond a shallow boundary.

## Annotated-tag reporting

`--tags` reports every validated annotated tag object using Git-compatible relationship output:

```text
tagged commit <target-oid> (v1.0) in <tag-oid>
```

The declared target type, target object ID, embedded tag name, and tag object's own ID are retained. Lightweight tags are refs rather than tag objects and therefore do not produce `--tags` records.

Like `--root`, tag diagnostics inspect the complete validated object inventory and are independent of reachability roots or dangling suppression.

## Connectivity-only behavior

Native Git suppresses `--root` and `--tags` diagnostics under `--connectivity-only`, because that mode deliberately does not inventory the complete object database. pygit follows the same rule rather than emitting a misleading partial report.

## Python API

```python
from pygit.fsck_diagnostics import annotated_tags, root_commits

report = fsck(repo)
roots = root_commits(repo, report)
tags = annotated_tags(repo, report)
```

`FsckTagDiagnostic` exposes `tag_oid`, `target_oid`, `target_type`, and `tag_name`; `format_tag_diagnostic()` renders the installed CLI form.

## Safety

The feature is read-only. It does not alter refs, reflogs, index state, object storage, packs, shallow metadata, or lost-found files. Diagnostics reuse the objects already accepted into `FsckReport.checked_objects`; unreadable/corrupt objects remain ordinary fsck errors rather than being trusted for summary output.
