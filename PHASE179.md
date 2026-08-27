# Phase 179: Remote lifecycle configuration synchronization

Phase 178 added Git-style multi-valued remote URL porcelain, but pygit still had
a split lifecycle: legacy `Repository.add_remote/remove_remote/rename_remote`
mutated `.pygit/config.json`, while newer URL, mirror, tracking, push-default,
and group settings live in `.pygit/config`.

Phase 179 makes the user-facing `pygit remote add/remove/rm/rename` commands keep
those two representations coherent.

## Remote add

`pygit remote add <name> <url>` now:

- rejects an existing remote instead of silently replacing it;
- treats a stale `remote.<name>.*` INI entry as an existing configured remote;
- preserves the historical config.json endpoint used by old transport APIs;
- materializes `remote.<name>.url` in `.pygit/config`;
- creates Git's default fetch mapping:

  `+refs/heads/*:refs/remotes/<name>/*`

This immediately makes the new remote visible to Phase178 URL porcelain and to
future fetch-refspec work while preserving compatibility with existing
Repository fetch/push code.

## Remote rename

`pygit remote rename <old> <new>` continues to use the mature Repository API to
move config.json state, remote-tracking refs, and the per-remote native SHA map.
It additionally rewrites Git-style INI state:

- every `remote.<old>.*` key becomes `remote.<new>.*`;
- duplicate URL and pushurl entries retain their source order;
- fetch refspec destinations change from `refs/remotes/<old>/...` to
  `refs/remotes/<new>/...`;
- `branch.*.remote=<old>` becomes `<new>`;
- `branch.*.pushRemote=<old>` becomes `<new>`;
- `remote.pushDefault=<old>` becomes `<new>`.

Like native Git, textual `remotes.<group>` membership is deliberately not
rewritten by remote rename.

## Remote remove

`pygit remote remove <name>` / `remote rm <name>` keeps the existing cleanup of
remote-tracking refs and native SHA maps and now also removes:

- all `remote.<name>.*` INI configuration;
- `remote.pushDefault` when it names the removed remote;
- `branch.*.pushRemote` entries naming that remote;
- a branch's `remote` and `merge` pair when that branch tracked the removed
  remote.

Unrelated branch settings such as rebase/description are preserved. If the
branch tracks another remote and only its pushRemote points at the removed
remote, its upstream remote+merge pair remains intact. Remote-group text is
again left unchanged, matching Git.

## Git compatibility probes

Native Git 2.47.3 was used to confirm:

- remote rename moves URL/pushurl/mirror/fetch configuration and tracking refs;
- rename rewrites the fetch destination namespace;
- rename updates branch remote, branch pushRemote, and remote.pushDefault;
- remove deletes the remote's configuration and tracking refs;
- removing the branch's upstream remote removes its remote+merge pair while
  retaining unrelated branch configuration;
- removing only a pushRemote leaves a different configured upstream intact;
- remote-group membership strings are not rewritten on rename/remove.

Current upstream `git-remote` documentation also specifies that rename updates
all remote-tracking branches and configuration settings, while remove deletes
all remote-tracking branches and configuration for the named remote.

## Architecture boundary

Phase 179 deliberately does not change the historical Repository method
signatures. The modern remote CLI composes those proven lifecycle operations
with a focused INI synchronization layer in `pygit.remote_lifecycle`.

Bare `pygit remote`, `remote prune`, and other not-yet-modernized remote
subcommands still use the legacy launcher. `add`, `remove`, `rm`, `rename`,
`get-url`, and `set-url` are intercepted by the modern nested parser.

## SHA-256-native design

This phase only changes remote metadata/configuration lifecycle. Local object
IDs, refs, object serialization, index data, pack conversion, and the
SHA-256-native to native SHA-1 smart-HTTP boundary are unchanged.
