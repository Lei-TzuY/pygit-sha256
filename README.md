# pygit

`pygit` is a feature-rich Git version control system implemented in Python. It stores loose objects compressed with `zlib` and addressed by SHA-256 hashes under `.pygit/objects/`. Its staging area (`.pygit/index`) is formatted as readable JSON for educational transparency.

---

## Supported Commands & Features

### Core Operations & Database Maintenance
- `pygit init [DIR]` — Initialise a new repository
- `pygit hash-object [-w] FILE` — Compute SHA-256 hash (and write to store)
- `pygit cat-file (-t | -s | -p) SHA` — Inspect object type, size, or content
- `pygit ls-files [--stage]` — List tracked files and index metadata
- `pygit rev-parse REV` — Resolve ref, tag, HEAD, `~N`/`^N` ancestors, or short SHA to full 64-char SHA-256
- `pygit fsck` — Check object database integrity and report dangling objects
- `pygit gc [--no-prune]` — Garbage-collect unreferenced dangling objects
- `pygit repack [-k]` — Consolidate loose objects into a paired `.pack` and Fan-out Index `.idx` file
- `pygit verify-pack [-v] FILE` — Validate CRC-32 checksums, offsets, and objects in `.pack`/`.idx` pairs
- `pygit count-objects [-v]` — Count unpacked/packed objects and report disk space consumption in KB
- `pygit bundle (create FILE [REF] | verify FILE)` — Create and verify portable binary `.bundle` files
- `pygit archive -o output.zip [TARGET]` — Export repository snapshot to a `.zip` archive

### Working Tree, Staging & LFS
- `pygit add PATH...` — Stage files or directories recursively (honours `.pygitignore`, EOL normalizers, and Git LFS)
- `pygit rm [--cached] PATH...` — Unstage or remove files from worktree
- `pygit clean -f [-d]` — Remove untracked files and directories
- `pygit status` — View working tree status (staged, unstaged, untracked, conflicts, active operation)
- `pygit diff [--cached] [--stat] [FROM_REF] [TO_REF]` — Unified diffs and diffstat summaries
- `pygit difftool [FROM_REF] [TO_REF]` — Format changes using external/custom diff tool output
- `pygit mergetool` — Inspect and resolve unmerged 3-way conflict stages
- `pygit lfs (track PATTERN | ls-files)` — Git Large File Storage pointer filter and local payload management

### Commits & History Navigation
- `pygit commit -m MSG [-t TEMPLATE] [--amend] [--author "Name <email>"]` — Create or amend a commit snapshot (supports `.pygitmessage` templates)
- `pygit log [--oneline] [--all] [--graph] [--author PATTERN] [--grep PATTERN] [-n N]` — Commit history with filters
- `pygit graph [-n N]` — Render ASCII DAG commit history graph
- `pygit shortlog [-s] [COMMIT]` — Summarize commit history grouped by author
- `pygit describe [COMMIT]` — Human-readable commit description relative to nearest tag
- `pygit show [COMMIT] [--stat]` — Show commit details and patch (supports `HEAD~1`, `main^2`)
- `pygit verify-commit [COMMIT]` — Inspect OpenPGP `gpgsig` signature headers in commit payloads
- `pygit revert COMMIT` — Create a new commit undoing changes from a target commit
- `pygit blame FILE` — Per-line author attribution
- `pygit reflog [REF]` — View HEAD and ref history movements

### Branching, Tagging, Submodules & Worktrees
- `pygit branch [NAME] [-d] [-m NEW]` — List, create, delete, or rename branches
- `pygit checkout [-b] [TARGET] [PATHS...]` — Switch branches, create & switch (`-b`), or restore pathspecs
- `pygit tag [NAME] [COMMIT] [-a] [-m MSG]` — Create lightweight or annotated tags (`b"tag"` objects)
- `pygit submodule (add URL [PATH] | status)` — Manage nested submodules in `.pygitmodules`
- `pygit worktree (add PATH [BRANCH] | list | remove PATH)` — Manage multiple linked working trees sharing object storage

### Advanced Workflows
- `pygit reset (--soft | --mixed | --hard) [TARGET]` / `pygit reset TARGET PATH...` — Reset HEAD or pathspecs
- `pygit merge TARGET [-m MSG] [--abort]` — 3-way merge with `<<<<<<<`, `=======`, `>>>>>>>` markers
- `pygit rebase TARGET (--continue | --skip | --abort)` — Replay commits onto target branch
- `pygit cherry-pick COMMIT (--continue | --abort)` — Replay a single commit onto HEAD
- `pygit stash (push | pop | list | show)` — Save, restore, or view dirty working-tree state
- `pygit bisect (start | good | bad | reset)` — Binary search history for regressions

### Configuration, Search & Notes
- `pygit config [--list] [--unset] SECTION.KEY [VALUE]` — Get, set, unset, or list repository configuration
- `pygit grep [-i] [-n] [-c] PATTERN [COMMIT]` — Search tracked files or commit tree for content matching a pattern
- `pygit notes (add -m MSG | show | list | remove) [COMMIT]` — Attach, view, list, or remove notes on commits without changing their SHA

### Smart HTTP Remote Sync (SHA-1 ↔ SHA-256 Interop)
- `pygit clone URL [DIR]` — Clone from GitHub or any Smart HTTP Git remote
- `pygit fetch [REMOTE]` / `pygit pull [REMOTE]` / `pygit push [-f] [REMOTE]` — Sync with remotes
- `pygit remote (add | remove | rename | prune)` — Remote configuration management

---

## Quick Example

```powershell
python -m pygit init myrepo
cd myrepo
"print('hello')" | Set-Content app.py
python -m pygit add app.py
python -m pygit commit -m "initial commit"
python -m pygit checkout -b feature
"print('hello world')" | Set-Content app.py
python -m pygit commit -m "update app"
python -m pygit log --graph --oneline
```

---

## Test Suite

Run the 158 unit and integration tests:

```powershell
python -m pytest -v
```

See [INTERNALS.md](INTERNALS.md) for a deep dive into the content-addressed object database architecture.
