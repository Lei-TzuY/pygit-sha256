# Phase 168 — Atomic multi-ref push

Phase 168 adds opt-in Git-compatible atomic receive-pack transactions on top of
Phase 167's expanded branch/tag/delete refspec support.

## Goal

A normal Phase 167 multi-ref push deliberately sends one receive-pack request per
selected ref. That preserves simple failure isolation, but it cannot provide
Git's `--atomic` guarantee: if the first ref succeeds and a later ref fails, the
remote may be left partially updated.

Phase 168 adds:

```text
pygit push --atomic origin main topic
pygit push --atomic --all origin
pygit push --atomic --tags origin
pygit push --atomic --delete origin old-a old-b
pygit push --no-atomic ...
```

When `--atomic` is requested, all selected refs are sent in one receive-pack
command list followed by one packfile. The client requests the server's `atomic`
capability, so either every command is accepted or none of them is applied.

## Protocol behavior

`pygit.push_atomic.AtomicSmartHttpPushClient` extends the existing smart-HTTP
push client without changing `SmartHttpPushClient.push()`.

For one transaction it:

1. discovers the receive-pack advertisement;
2. requires the advertised `atomic` capability;
3. emits all `<old> <new> <ref>` commands before the flush packet;
4. puts `report-status atomic` on the first command capability list;
5. emits only one merged packfile for all object-producing updates;
6. parses all report-status lines and fails on any `ng <ref> ...` result.

The client never asks for `atomic` when the server did not advertise it. A remote
without atomic support therefore fails explicitly instead of silently degrading
to a partial multi-ref push.

## All-or-nothing local state

`push_atomic_specs()` performs every local preflight before calling receive-pack:

- source refs must exist;
- branch updates must be fast-forwards unless forced;
- existing tags cannot be replaced unless forced;
- mixed branch/tag/deletion plans are validated as one batch.

SHA-1 native conversion still happens only at the transport boundary. The
exporter builds one union object graph and one pack for the transaction while
local objects and refs remain SHA-256-native.

Native-map and remote-tracking updates are intentionally delayed until the
atomic request succeeds. If receive-pack returns an unpack error or rejects any
ref, no local remote-tracking ref is advanced or deleted and the native map is
not committed.

## Compatibility

The historical APIs remain intact:

```python
Repository.push(remote="origin", force=False)
SmartHttpPushClient.push(ref_name, new_oid, objects, ...)
push_branch(...)
push_ref(...)
delete_remote_ref(...)
```

Without `--atomic`, Phase 167's sequential behavior is unchanged. Even a
single-ref `--atomic` invocation uses the atomic protocol path so the explicit
user request is never silently ignored.

## Git compatibility notes

Git's receive-pack protocol advertises `atomic` as a capability. A client may
request it on the first update command, after which the remote treats the full
command list as one transaction. Git documents that `--atomic` must fail when
the server does not support atomic pushes.

Phase 168 implements that capability and transaction boundary for the existing
smart-HTTP v0/v1 transport. It does not yet add remote groups, push-options,
signed pushes, force-with-lease, or protocol-v2 send-pack behavior.

## Tests

`tests/test_phase168.py` covers:

- multiple update commands in one HTTP POST;
- exactly one atomic capability list and one packfile;
- failure before POST when the server lacks `atomic`;
- propagation of any per-ref `ng` status;
- one batch call for `--all`;
- no native-map/tracking mutation after server rejection;
- complete preflight before receive-pack is called;
- mixed delete/create transactions;
- CLI routing for `--atomic` and `--no-atomic`;
- explicit atomic handling for a single ref.
