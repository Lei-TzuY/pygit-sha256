# Phase 72: reflog expire plumbing

Phase 72 adds an explicit, conservative way to retire reflog recovery roots before unreachable loose-object pruning.

## CLI

```text
pygit reflog expire [--all] [--expire=WHEN] [--expire-unreachable=WHEN]
                    [-n|--dry-run] [-v|--verbose] [REF...]
```

Without `--all` or an explicit ref, `HEAD` is selected. Explicit names must be `HEAD` or fully-qualified `refs/...` names.

Expiry expressions reuse the deterministic Phase 71 parser: `now`, `never`, an epoch timestamp, or forms such as `30.days.ago` and `12.hours.ago`.

Defaults intentionally mirror the common Git maintenance shape:

- `--expire=90.days.ago`: remove any record older than 90 days.
- `--expire-unreachable=30.days.ago`: remove records older than 30 days only when every non-zero old/new OID is outside the current refs/index/shallow reachability closure.

`--dry-run` performs the same validation and planning without rewriting files. `--verbose` prints each selected record and its expiry reason.

Existing `pygit reflog [REF]` display behavior is unchanged. A thin `pygit.application` front door recognizes only the nested `reflog expire` grammar and delegates every other command to the existing launcher stack.

## Safety model

Before considering any record for expiry, pygit runs connectivity-only `fsck`. Broken current refs, index roots, shallow boundaries, or reachable object links abort the entire operation.

Every selected reflog is parsed strictly before the first write:

- old/new IDs must be canonical 64-hex SHA-256 values;
- timestamps must be non-negative integers;
- timezones must be `+HHMM`/`-HHMM` shaped;
- every record must contain the expected tab-delimited message field;
- symbolic-link entries below `.pygit/logs` are rejected.

For multi-log operations all rewritten contents are prepared and fsynced before the first `os.replace`. If a later replacement fails, already replaced logs are restored from snapshots with atomic replacements.

The command never mutates refs, the index, objects, packed storage, or the worktree.

## Relationship with prune

Phase 71 `prune` treats every reflog old/new OID as a retention root. Therefore the intended recovery lifecycle is:

```text
ref movement -> reflog recovery window -> reflog expire -> prune
```

Objects do not become prune-eligible merely because a branch moved. They remain protected until the relevant reflog records expire (or another retention root disappears), after which the ordinary prune grace and validation rules still apply.

## Python API

```python
from pygit import (
    ReflogExpireEntry,
    ReflogExpireResult,
    default_reflog_expire_before,
    default_reflog_unreachable_before,
    expire_reflogs,
)

result = expire_reflogs(
    repo,
    ["HEAD", "refs/heads/main"],
    expire_before=...,
    expire_unreachable_before=...,
    dry_run=True,
)
```

Regression coverage includes general expiry, reachability-aware expiry, all-ref selection, malformed-log fail-closed behavior, unhealthy connectivity, path validation, multi-log rollback, dry-run, legacy reflog display compatibility, and the end-to-end `reflog expire -> prune` recovery transition.
