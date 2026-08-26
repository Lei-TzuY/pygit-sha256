# Phase 142 — reflog-aware `fsck` reachability

Phase 142 closes a recovery-safety gap in the installed `pygit fsck` command.
Before this phase, fsck reachability used HEAD, refs, the index, and shallow
boundaries only. Objects referenced solely by reflogs could therefore be
reported as dangling/unreachable even though Git normally treats reflogs as
head nodes for fsck reachability.

## CLI behavior

The installed command now enables reflog roots by default:

```bash
pygit fsck
pygit fsck --unreachable
pygit fsck --connectivity-only
```

Use Git-style `--no-reflogs` to deliberately exclude recovery history:

```bash
pygit fsck --no-reflogs --unreachable
```

Every regular reflog discovered below `.pygit/logs` participates, not only
`HEAD`. Both non-zero old and new object IDs from each record become roots, so
history retained only by a deleted branch's reflog remains reachable.

## Strict parsing and safety

Phase 142 reuses the existing Phase 72 reflog safe-path and strict record parser.
That provides one consistent policy across `reflog show`, `reflog expire`, and
reflog-aware fsck:

- symbolic-link / escaping log paths are rejected;
- malformed object IDs, timestamps, timezones, or record framing fail closed;
- zero object IDs are creation/deletion sentinels and are never roots;
- a non-zero reflog root that cannot be read is a connectivity error;
- `--no-reflogs` skips discovery and parsing entirely, so intentionally ignored
  recovery metadata cannot make that scan fail.

## Full and connectivity-only scans

Reflog roots are inserted before fsck's existing graph walk. This is important:
`--connectivity-only` does not merely hide final dangling output. It actually
walks and validates reflog-only commits, trees, tags, and blobs as part of the
reachable graph.

Full mode still inventories every loose and packed object first; reflog roots
only change reachability classification and add connectivity requirements for
objects named by recovery metadata.

## Python API compatibility

The low-level API gains an explicit switch:

```python
report = fsck(repo, include_reflogs=True)
```

`include_reflogs=False` remains the direct Python default so existing maintenance
callers that already manage reflog retention separately keep their Phase 60
contract. The installed CLI opts into reflogs by default and maps
`--no-reflogs` back to `False`.

## Regression coverage

Phase 142 locks down:

- old-side and new-side reflog roots;
- zero-OID sentinel exclusion;
- non-HEAD reflog discovery;
- full-scan unreachable/dangling reclassification;
- connectivity-only traversal of reflog-only object graphs;
- installed default-vs-`--no-reflogs` behavior;
- malformed reflog fail-closed behavior;
- missing reflog target connectivity failures;
- installed help exposure.
