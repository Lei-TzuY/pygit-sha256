# Phase 203 — protocol-v2 fetch server options

Phase 203 adds Git-style `fetch -o/--server-option` on top of the Phase201 protocol-v2 fetch integration. A parallel Phase202 branch already exists for shallow-fetch work, so this phase deliberately uses a separate number and a non-overlapping feature area.

## User-facing behavior

Examples:

```bash
pygit fetch --server-option=trace=1 origin
pygit fetch -o one -o two origin
pygit fetch --server-option=trace=1 --negotiate-only --negotiation-tip=main origin
```

Server options are protocol-v2-only. Supplying any server option makes pygit attempt the v2 transport even if `protocol.version` is not explicitly configured. If the remote falls back to protocol v0, pygit fails instead of silently dropping the requested options.

Repeated options are kept in command-line order. `--` remains the option terminator, so a token such as `--server-option=literal` after `--` is treated as a refspec token rather than a transport option.

## Wire format

Git protocol v2 defines `server-option` as a request capability. When the server advertises the `server-option` capability, each value is emitted as:

```text
server-option=<value>
```

in the capability-list section before the delimiter packet. Phase203 applies the same ordered option list to both `ls-refs` and `fetch` requests, including the ACK-only fetch request used by `--negotiate-only`.

Options containing NUL or LF are rejected. If a v2 server does not advertise `server-option`, pygit rejects the request rather than sending an unsupported capability.

## Compatibility boundary

Current Git documentation also supports `remote.<name>.serverOption` as a configuration fallback when no CLI server option is supplied. Phase203 intentionally limits this first transport phase to explicit `-o/--server-option` values; per-remote configuration fallback belongs in a follow-up because multi-remote orchestration must preserve named-remote identity even when remotes share URLs.

## SHA-256-native design

This feature changes only protocol-v2 request metadata. Native Git SHA-1 object IDs remain confined to smart-HTTP `ls-refs`, `want`, `have`, `ACK`, and pack parsing. Repository objects, refs, FETCH_HEAD, reflogs, negotiation output, and native maps retain pygit's SHA-256-native identity model.

## Tests

`tests/test_phase203.py` covers:

- ordered server-option framing for `ls-refs` and `fetch`
- capability gating
- NUL/LF rejection
- short and long CLI forms
- option-terminator behavior
- forcing the v2 scope when options are supplied
- rejecting legacy-protocol fallback instead of dropping options
- passing options into the v2 query client
- preserving the established no-argument protocol-v2 wrapper seam when no server options are present
