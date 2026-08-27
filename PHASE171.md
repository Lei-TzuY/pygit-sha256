# Phase 171 — push options

Phase 171 adds Git-compatible receive-pack push options on top of the Phase 170
push safety stack.

## User-facing options

`pygit push` now accepts repeatable push options:

```bash
pygit push -o ci.skip origin main
pygit push --push-option=deploy=staging origin main
pygit push -o one -o two origin main
```

The strings are transmitted to a receive-pack server that advertises the
`push-options` capability.  Git passes these strings to pre-receive and
post-receive hooks.

Push-option values preserve command-line order.  An explicitly empty option is
valid, while NUL and LF characters are rejected before transport.

## Configuration

When no command-line `-o` / `--push-option` is supplied, Phase 171 reads the
multi-valued `push.pushOption` variable:

```ini
[push]
    pushOption = ci.skip
    pushOption = deploy=staging
```

Any command-line push-option occurrence replaces the configured list rather
than appending to it.

`GitConfig` now tolerates duplicate keys and exposes `get_all()` for
multi-valued entries.  An empty value clears values seen earlier, matching the
Git reset convention used by `push.pushOption`.

The repository currently has a local configuration scope only, so Phase 171
applies this reset behavior within `.pygit/config`; system/global/local scope
merging remains outside the current configuration model.

## Receive-pack framing

For protocol v0/v1 the remote advertises `push-options`.  When at least one
option is active, pygit requests that capability on the first update command.
The request layout is:

```text
<update command + capabilities>
<additional update commands, if any>
0000
<option 1 pkt-line>
<option 2 pkt-line>
...
0000
<packfile, if needed>
```

Push-option payloads are sent as raw pkt-line strings without an added newline.
An empty option therefore becomes a valid zero-byte pkt-line (`0004`).

If any option is requested and the remote does not advertise `push-options`,
the push fails instead of silently discarding metadata.  This capability check
also occurs when the selected ref is already up to date, matching native Git.
When the server supports push options but the ref is already up to date, no
receive-pack POST is sent and receive hooks are not invoked.

## Compatibility-preserving client design

The historical methods remain unchanged:

- `SmartHttpPushClient.push(...)`
- `AtomicSmartHttpPushClient.push_many(...)`
- `Repository.push(...)`

Phase 171 adds opt-in clients in `pygit.push_options`:

- `PushOptionSmartHttpPushClient.push_with_options(...)`
- `PushOptionAtomicSmartHttpPushClient.push_many_with_options(...)`

The modern transport selects those clients only when an option is active.
No-option callers continue through the exact Phase 167–170 client path.

Likewise, `push_ref`, `push_branch`, `delete_remote_ref`, and
`push_atomic_specs` gain only an optional keyword with an empty default.  The
CLI does not pass that keyword when no option is active, preserving historical
monkeypatch and caller behavior.

## Atomic composition

`--atomic` and push options compose in one receive-pack transaction:

```bash
pygit push --atomic -o deploy=prod --all origin
```

The first command requests both `atomic` and `push-options`; all ref commands
are flushed, all option pkt-lines are flushed, and then one union packfile is
sent.  Missing support for either requested capability causes the operation to
fail before the update transaction is sent.

## Native Git compatibility checked

Phase 171 was implemented against current Git documentation and native Git
2.47.3 probes.  The probes confirmed:

- multiple `-o` values are transmitted in command-line order;
- `--push-option value` and `--push-option=value` are accepted;
- an empty option is accepted;
- an LF-containing option is rejected;
- a remote without `push-options` rejects an option-bearing push;
- that rejection still occurs when the ref is already up to date;
- a supported up-to-date push does not invoke receive hooks;
- repeated `push.pushOption` config values are used only when the command line
  supplies no push option.

## SHA-256-native boundary

Push options carry only hook metadata.  They do not affect local SHA-256 object
identity, native SHA-1 conversion, lease expectations, reflog checks, pack
construction, or ref storage.

## Scope boundaries

Later push phases can still add:

- signed push certificates;
- `--follow-tags` / `push.followTags`;
- `--prune` / mirror semantics;
- negative push refspecs;
- remote groups;
- protocol-v2 / newer send-pack negotiation.

## Regression coverage

`tests/test_phase171.py` covers:

- option validation and ordering;
- explicit empty values;
- duplicate/multi-valued config and empty reset;
- CLI-over-config precedence;
- single-ref wire framing;
- unsupported capability rejection before POST;
- unsupported capability rejection for an up-to-date ref;
- supported up-to-date behavior without a POST;
- atomic multi-ref framing;
- CLI routing away from the legacy `Repository.push()` shortcut;
- config fallback in the full CLI path;
- atomic CLI batch propagation.
