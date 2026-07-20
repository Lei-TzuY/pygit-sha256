# pygit

`pygit` is a small Git implementation written in Python. It stores loose
objects with zlib compression and SHA-256 identifiers under `.pygit/objects`.
Its JSON index is intentionally readable so the staging area is easy to
inspect.

## Supported workflow

```powershell
python -m pygit init demo
cd demo
"hello" | Set-Content hello.txt
python -m pygit add .
python -m pygit commit -m "initial"
python -m pygit status
python -m pygit log --oneline
```

Local commands include:

- `init`, `hash-object`, `cat-file`
- `add`, `rm`, `status`, `diff`
- `commit`, `log`, `branch`, `checkout`, `tag`, `reset`
- `merge`, `cherry-pick`, `rebase`
- `stash push`, `stash pop`, `stash list`
- `reflog`
- `bisect start`, `bisect good`, `bisect bad`, `bisect reset`

`.pygitignore` supports comments, negation with `!`, directory patterns, rooted
patterns, and shell-style wildcards. `merge` performs a three-way merge and
writes `<<<<<<<`, `=======`, and `>>>>>>>` markers for content conflicts.

`merge` supports `--abort` after conflicts. `rebase` replays first-parent
histories and supports `--continue`, `--skip`, and `--abort`.
`cherry-pick` also supports conflict resolution with `--continue` and `--abort`.
`reset` supports `--soft`, default `--mixed`, and `--hard` modes.
Path reset is also available, for example `python -m pygit reset HEAD -- file.py`.
`reflog` records HEAD, branch, and stash ref movements under `.pygit/logs/`.

## Smart HTTP remotes

`clone`, `fetch`, and `pull` can read smart HTTP Git repositories:

```powershell
python -m pygit clone https://github.com/octocat/Hello-World.git
cd Hello-World
python -m pygit remote -v
python -m pygit remote rename origin upstream
python -m pygit remote prune upstream
python -m pygit fetch
python -m pygit pull
```

Remote Git servers normally send SHA-1 packfiles. `pygit` parses pkt-lines,
expands regular, `OFS_DELTA`, and `REF_DELTA` pack entries, then converts the
fetched object graph into its internal SHA-256 format.
Subsequent fetches reuse `.pygit/native-map.json`: known advertised refs are
updated without downloading another pack, and unknown refs are requested with
`have` lines so the server can avoid resending objects already present locally.

`push` performs the reverse conversion and sends a regular pack through the
smart HTTP `receive-pack` protocol:

```powershell
python -m pygit push
python -m pygit push --force  # allow a non-fast-forward update
```

Fetched and successfully pushed objects retain per-remote native SHA-1 mappings
in `.pygit/native-map.json`. New commits can therefore reuse the real remote
history instead of rewriting imported parents. Push updates the current local
branch's matching remote branch. Authentication is delegated to the HTTP URL
handling provided by Python.

## Tests

```powershell
python -m pytest -q
```
