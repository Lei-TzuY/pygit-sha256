# Transactional ref plumbing

Phase 51 adds low-level reference mutation commands that complement `commit-tree`.
They operate on pygit's native 64-hex SHA-256 object IDs and update reflogs.

## `pygit update-ref`

```text
pygit update-ref refs/heads/topic <new-oid>
pygit update-ref refs/heads/topic <new-oid> <expected-old-oid>
pygit update-ref -d refs/tags/v1 <expected-old-oid>
pygit update-ref -m "advance topic" refs/heads/topic <new> <old>
pygit update-ref --no-deref HEAD <commit-oid>
```

The optional old object ID provides compare-and-swap semantics. `000...000`
means the ref must not exist. By default a symbolic ref such as `HEAD` is
dereferenced and its target is updated; `--no-deref` updates the named ref
itself (for example, detaching `HEAD`). New object IDs must exist locally.

Like native Git, branch-like refs (`refs/heads/*`, and a directly updated
`HEAD`) must point to commit objects rather than blobs, trees, or tag objects.
Other namespaces such as `refs/tags/*` may point to any stored object.

`--stdin` validates the complete transaction before publishing any change:

```text
create refs/heads/a <oid>
update refs/heads/b <new> <old>
delete refs/tags/old <old>
verify refs/heads/main <expected>
```

Duplicate physical refs in one transaction are rejected. Replacement ref files
are prepared first and published with atomic `os.replace`. Ref and reflog files
are snapshotted before publication, so an I/O failure during a multi-ref publish
restores already-touched paths rather than leaving a half-applied transaction.
Successful branch-tip updates also append the corresponding `HEAD` reflog entry
when that branch is currently checked out.

## `pygit symbolic-ref`

```text
pygit symbolic-ref HEAD
pygit symbolic-ref HEAD refs/heads/main
pygit symbolic-ref -m "switch" HEAD refs/heads/topic
pygit symbolic-ref -d refs/aliases/current
pygit symbolic-ref -q HEAD
```

Reading prints the target only when the named ref is symbolic. Setting validates
both names and refuses updates that would create a symbolic-reference cycle.
Deleting requires the named ref to actually be symbolic; deleting `HEAD` is
explicitly refused so the repository cannot be left without a HEAD file.

## Python API

```python
from pygit.ref_transaction import RefUpdate, update_ref, update_refs

update_ref(repo, "refs/heads/main", new_oid, old_oid=old_oid)
update_refs(repo, [
    RefUpdate("verify", "refs/heads/main", None, old_oid),
    RefUpdate("create", "refs/tags/build", new_oid, "0" * 64),
])
```
