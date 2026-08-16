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

### Branching, Tagging, Submodules, Worktrees & History Rewriting
- `pygit branch [NAME] [-d] [-m NEW]` — List, create, delete, or rename branches
- `pygit checkout [-b | --orphan] [-p] [TARGET] [PATHS...]` — Switch branches, create & switch (`-b`), create orphan branch (`--orphan`), interactive patch restore (`-p`), or restore pathspecs
- `pygit tag [NAME] [COMMIT] [-a] [-m MSG]` — Create lightweight or annotated tags (`b"tag"` objects)
- `pygit submodule (add URL [PATH] | status)` — Manage nested submodules in `.pygitmodules`
- `pygit worktree (add PATH [BRANCH] | list | remove PATH)` — Manage multiple linked working trees sharing object storage
- `pygit sparse-checkout (set PATTERN... | list | disable)` — Manage sparse checkout patterns in `.pygit/info/sparse-checkout`
- `pygit filter-branch --path PREFIX [BRANCH]` — Rewrite branch history keeping only matching file paths

### Advanced Workflows & Patch Management
- `pygit add [-p] PATH...` — Stage files or interactively stage patch hunks (`-p`)
- `pygit mv SOURCE DESTINATION [-f]` — Move or rename tracked files / directories and update index
- `pygit checkout [-b NEW_BRANCH] [--detach] [TARGET]` — Switch branches, create new branch, or detach HEAD (`--detach`)
- `pygit reset [-p] (--soft | --mixed | --hard) [TARGET]` / `pygit reset TARGET PATH...` — Reset HEAD, pathspecs, or unstage hunks (`-p`)
- `pygit merge [--squash] TARGET [-m MSG] [--abort]` — 3-way merge with conflict markers (supports `--conflict=diff3` / `merge`)
- `pygit rebase TARGET [--autosquash] (--continue | --skip | --abort)` — Replay commits onto target branch with auto-reordering of fixup!/squash!
- `pygit cherry-pick [-n] COMMIT (--continue | --abort)` — Replay a single commit onto HEAD (or apply without committing with `-n`)
- `pygit commit [-m MSG] [-C COMMIT] [-c COMMIT] [-s] [-v] [-e] [--no-edit] [-q] [--dry-run] [--no-status] [--author="Name <email>"] [--date=DATE] [--reset-author] [-o PATH...] [-i PATH...] [--allow-empty] [--cleanup=<strip|whitespace|verbatim>] [--amend] [--fixup COMMIT] [--squash COMMIT]` — Record changes, amend (`--amend`), edit message (`-e`/`--edit`), retain message (`--no-edit`), verbose diff preview (`-v`/`--verbose`), quiet output (`-q`/`--quiet`), dry run (`--dry-run`), signoff (`-s`/`--signoff`), author override (`--author`), timestamp override (`--date`), reset author (`--reset-author`), reuse message (`-C`/`-c`), cleanup message (`--cleanup`), allow empty tree (`--allow-empty`), or submit specified paths (`-o` / `-i`)
- `pygit stash (push [-k] [-S] [-- PATH...] | save [MSG] | pop | apply | drop | clear | create | store | branch | list | show)` — Manage stashes including `clear`, `--keep-index` (`-k`), `--staged` (`-S`), pathspecs, and legacy `save` alias
- `pygit bisect (start | good | bad | reset)` — Binary search history for regressions
- `pygit rerere [status]` — Reuse recorded resolution of conflicted merges (`.pygit/rr-cache`)

### Configuration, Search, Inspection & Maintenance Pipeline
- `pygit config [--list] [--unset] SECTION.KEY [VALUE]` — Get, set, unset, or list repository configuration
- `pygit status [-s] [-b] [--ahead-behind|--no-ahead-behind] [--display-comment-prefix] [--porcelain] [--ignored]` — Working tree status with ahead/behind control (`--ahead-behind`), comment prefix toggle (`--display-comment-prefix`), and branch tracking info in short mode (`-s -b`)
- `pygit diff [-w] [-b] [-I REGEX] [-M] [-C] [--dirstat] [--stat-graph-width=WIDTH] [--submodule[=KIND]] [--raw] [--no-prefix] [--ignore-submodules] [--ws-error-highlight=KIND] [--compact-summary] [--src-prefix=PREFIX] [--dst-prefix=PREFIX] [--stat-width=WIDTH] [--cached] [--stat] [--name-status] [--name-only] [REV [REV]]` — Unified diff with stat graph width limit (`--stat-graph-width`), directory stats (`--dirstat`), submodule formatting (`--submodule`), copy detection (`-C`/`--find-copies`), rename detection (`-M`/`--find-renames`), whitespace error options (`--ws-error-highlight`), ignore submodules (`--ignore-submodules`), no-prefix mode (`--no-prefix`), custom path prefixes (`--src-prefix`/`--dst-prefix`), raw mode (`--raw`), compact summary (`--compact-summary`), stat width limit (`--stat-width`), regex line filter (`-I`), whitespace options (`-w`/`-b`), stat, name-status, or name-only
- `pygit grep [-i] [-n] [-c] PATTERN [COMMIT]` — Search tracked files or commit tree for content matching a pattern
- `pygit notes (add -m MSG | show | list | remove) [COMMIT]` — Attach, view, list, or remove notes on commits without changing their SHA
- `pygit blame [-L START,END] FILE` — Show per-line authorship with optional line range filtering (`-L`)
- `pygit log [--oneline] [--all] [--graph] [--topo-order] [--first-parent] [--min-parents=N] [--max-parents=N] [--date=<relative|short|iso>] [--merges | --no-merges] [-L START,END:FILE] [--format FORMAT] [--since DATE] [--until DATE] [-p] [--follow FILE]` — Commit history with custom date formatting (`--date`), parent filtering (`--min-parents`/`--max-parents`), `--first-parent`, and line trace (`-L`)
- `pygit count-objects [-v]` — Count loose objects, packfiles, and disk usage in KB (verbose Git-style `-v`)
- `pygit branch [-a] [--contains COMMIT] [--no-contains COMMIT] [--merged [COMMIT]] [--no-merged [COMMIT]] [NAME] [-d] [-m NEW]` — List branches (filter with `--merged`/`--no-merged`), create, delete, or rename
- `pygit show-branch` — Display branch commit matrix and reachability status
- `pygit tag [-l PATTERN] [NAME] [COMMIT] [-a] [-m MSG]` — List tags (filter with `-l`), create lightweight or annotated tags
- `pygit describe [--tags] [--always] [COMMIT]` — Nearest tag description with lightweight tag support (`--tags`) and SHA fallback (`--always`)
- `pygit maintenance run` — Consolidated repository optimization pipeline (runs repack, commit-graph write, and gc)
- `pygit check-ignore PATH...` — Inspect and debug `.gitignore` / `.pygit/info/exclude` ignore rules

### Low-Level Plumbing Commands
- `pygit ls-tree [-r] [--name-only] [TREE-ISH]` — List contents of a tree object
- `pygit rev-parse [--branches] [--tags] [--remotes] [--verify] [--default=ARG] [--path-format=<absolute|relative>] [--resolve-git-dir PATH] [--is-shallow-repository] [--is-bare-repository] [--is-inside-git-dir] [--abbrev-ref] [--sq] [--not] [--revs-only] [--no-revs] [--symbolic-full-name] [--is-inside-work-tree] [--prefix] [--git-dir] [--show-toplevel] [--short[=N]] [REV]` — Resolve ref, path format control (`--path-format`), git dir resolution (`--resolve-git-dir`), shallow clone check (`--is-shallow-repository`), bare repo check (`--is-bare-repository`), git dir location (`--is-inside-git-dir`), abbreviated ref name (`--abbrev-ref`), fallback default (`--default`), full ref path (`--symbolic-full-name`), custom short length (`--short=N`), revision verification (`--verify`), wildcard pattern filtering (`--branches/tags/remotes=<pattern>`), negation prefix (`--not`), shell single-quote formatting (`--sq`), revision filtering (`--revs-only`/`--no-revs`), relative path prefix (`--prefix`), repository status flags, namespace refs, SHA, or environment flags
- `pygit write-tree` / `pygit commit-tree TREE -p PARENT -m MSG` — Build raw tree and commit objects
- `pygit update-ref REF NEW_SHA [OLD_SHA]` / `pygit symbolic-ref [NAME [TARGET]]` — Manipulate raw reference targets
- `pygit rev-list [REVISION] [--count] [--left-right] [--topo-order] [-n N]` — List or count commit SHAs with symmetric difference side markers (`--left-right`)

### Smart HTTP Remote Sync & SSH Transport
- `pygit clone URL [DIR] [-b BRANCH] [--single-branch] [--depth N]` — Clone from GitHub or Smart HTTP remotes with single-branch (`--single-branch`) and shallow depth support
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

Run the 253 unit and integration tests:

```powershell
python -m pytest -v
```

See [INTERNALS.md](INTERNALS.md) for a deep dive into the content-addressed object database architecture.
