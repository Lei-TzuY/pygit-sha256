# Phase 63: ls-remote plumbing

Phase 63 adds read-only Smart HTTP remote-reference inspection without fetching a pack or mutating local repository state.

## CLI

```bash
pygit ls-remote https://example.com/owner/repo.git
pygit ls-remote origin
pygit ls-remote --heads origin
pygit ls-remote --tags --refs origin
pygit ls-remote --symref origin
pygit ls-remote --exit-code origin 'release/*'
pygit ls-remote --get-url origin
```

`REPOSITORY` may be a direct HTTP(S) URL or a configured remote name when run inside a pygit worktree. `--get-url` resolves the source locally and does not contact the remote.

`--heads` and `--tags` restrict the advertised namespace and may be combined. `--refs` removes pseudorefs such as `HEAD` and peeled tag helper refs ending in `^{}`. Patterns are matched against the full ref name and slash-delimited ref tails, so `main` can match `refs/heads/main` and `release/*` can match a matching branch or tag tail. `--exit-code` returns status 2 when no advertised ref matches.

`--symref` emits symbolic targets reported by the advertisement, such as `HEAD -> refs/heads/main`, before the corresponding object-id records.

## Python API

```python
from pygit import ls_remote

result = ls_remote("origin", repo=repo, heads=True)
for ref in result.refs:
    print(ref.oid, ref.name)
```

The exported API includes `RemoteRef`, `LsRemoteResult`, `resolve_remote_url()`, and `ls_remote()`.

## Object-format boundary

Remote Smart HTTP interoperability currently targets native Git SHA-1 advertisements. Therefore `ls-remote` intentionally returns the remote's 40-hex SHA-1 object IDs. It does not translate those IDs into pygit's internal 64-hex SHA-256 namespace because no objects are fetched or imported.

This command performs advertisement discovery only. It does not download pack data, write objects, update remote-tracking refs, modify `.pygit/config`, or alter HEAD/index/worktree state.
