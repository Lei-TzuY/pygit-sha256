# Git Architecture & Internals: Content-Addressed Database

> **"Git is fundamentally a content-addressed key-value store with a VCS interface built on top of it."** — Linus Torvalds

This document details the exact object data model, storage formats, and graph mechanics powering `pygit`.

---

## 1. Core Architecture Overview

At its lowest layer, Git is not a version control system—it is an **immutable, append-only, content-addressed key-value database**:

- **Key**: Cryptographic hash (SHA-1 in standard Git, SHA-256 in `pygit`) calculated from the object header + payload bytes.
- **Value**: Compressed raw binary data envelope stored on disk under `.pygit/objects/xx/yyyy...`.

```
                    +-------------------+
                    |   Commit Object   |  (Metadata + Author + Tree SHA)
                    +---------+---------+
                              |
                              v
                    +-------------------+
                    |    Tree Object    |  (Directory listing: mode, name, SHA)
                    +----+---------+----+
                         |         |
            +------------+         +------------+
            |                                   |
            v                                   v
  +-------------------+               +-------------------+
  |    Blob Object    |               |    Tree Object    |  (Sub-directory)
  +-------------------+               +-------------------+
  (Raw File Bytes)
```

Because every key is determined strictly by its payload content:
1. **Deduplication**: Two identical files anywhere in the project share the exact same Blob SHA. Renaming a file doesn't duplicate its content.
2. **Immutability & Integrity**: Tampering with a single byte in any file or commit message changes its SHA hash, breaking parent links and immediately detecting corruption.
3. **DAG Lineage**: History is represented as a Directed Acyclic Graph (DAG) of immutable Commit objects.

---

## 2. On-Disk Object Envelope & Storage Format

Every Git object stored on disk (whether `blob`, `tree`, or `commit`) follows the exact same binary header envelope format prior to compression:

```
<type-name> SP <payload-size-bytes> NUL <payload-bytes>
```

### Storage Location
Objects are compressed using standard `zlib` deflate and stored under `.pygit/objects/`:
- **Directory**: `.pygit/objects/<SHA[:2]>/`
- **Filename**: `<SHA[2:]>`

For example, an object with SHA `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`:
```
.pygit/objects/e3/b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

---

## 3. Detailed Object Model & Schemas

### 3.1. Blob (`b"blob"`)
Stores raw file content. Blobs store **zero filename or file permission metadata**.

- **Envelope Header**: `blob <size>\x00`
- **Payload**: Raw binary bytes of the file.

```
+-------------------------------------------------------+
| blob 12\x00Hello, world!\n                            |
+-------------------------------------------------------+
```

---

### 3.2. Tree (`b"tree"`)
Represents directory snapshots. Each entry maps a Unix mode + filename to a child SHA-256 object (Blob for files, Tree for subdirectories).

- **Envelope Header**: `tree <size>\x00`
- **Payload Format**: Concatenated binary entries sorted lexicographically by name:
  ```
  <mode> SP <entry-name> NUL <32-byte-raw-binary-hash>
  ```
  - Mode examples: `"100644"` (regular file), `"100755"` (executable file), `"040000"` (directory).

---

### 3.3. Commit (`b"commit"`)
Represents an immutable project state snapshot linked to its history.

- **Envelope Header**: `commit <size>\x00`
- **Payload Format**:
  ```
  tree <tree-sha-256-hex>
  parent <parent-commit-sha-256-hex>
  author Alice <alice@example.com> 1770000000 +0000
  committer Alice <alice@example.com> 1770000000 +0000

  Commit message title

  Optional multi-line commit body text.
  ```

---

### 3.4. Tag (`b"tag"`)
Represents an explicit annotated tag object pointing to a target object (usually a commit).

- **Envelope Header**: `tag <size>\x00`
- **Payload Format**:
  ```
  object <target-sha-256-hex>
  type commit
  tag v1.0
  tagger Alice <alice@example.com> 1770000000 +0000

  Release 1.0 notes and tag annotation message.
  ```

---

## 4. The Staging Area (Index)

The **Index** (or Staging Area) acts as the cache layer between the working directory and object storage. It represents the proposed next commit.

In `pygit`, the index is stored as `.pygit/index` (formatted as structured JSON for inspection and debugging):

```json
[
  {
    "path": "src/main.py",
    "sha": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "mode": "100644",
    "size": 1234,
    "mtime": 1770000000.0
  }
]
```

When you run `pygit add <path>`:
1. `pygit` reads the target file, creates a `BlobObject`, and writes it to `.pygit/objects/`.
2. `pygit` records/updates the entry `(path, blob_sha, mode, size, mtime)` in `.pygit/index`.

When you run `pygit commit`:
1. `pygit` reads `.pygit/index` and constructs a tree hierarchy of `TreeObject`s recursively.
2. Writes all `TreeObject`s to `.pygit/objects/`.
3. Creates a `CommitObject` pointing to the root `TreeObject` SHA and the current `HEAD` SHA as its parent.
4. Updates `HEAD` ref to point to the new commit SHA.

---

## 5. References (`refs`) & HEAD

References are human-readable pointers to commit SHAs.

- **Branch**: `.pygit/refs/heads/<branch_name>` containing a single line with a 64-character SHA-256 hex string.
- **Tag**: `.pygit/refs/tags/<tag_name>` containing a 64-character SHA-256 hex string.
- **HEAD**: `.pygit/HEAD` containing either:
  - Symbolic ref: `ref: refs/heads/main`
  - Detached HEAD: `<64-hex-commit-sha>`

---

## 6. Smart HTTP Interoperability (SHA-1 ↔ SHA-256 Translation)

Real Git remotes (e.g. GitHub, GitLab) use native SHA-1 packfiles over HTTP (`git-upload-pack` and `git-receive-pack`).

`pygit` seamlessly interfaces with remote standard Git servers via `.pygit/native-map.json`:
- **Fetch**: Parses remote pkt-lines and delta packfiles (`OFS_DELTA`, `REF_DELTA`), computes native SHA-1 IDs, stores objects in local SHA-256 storage, and records `pygit_sha256 -> native_sha1` in `native-map.json`.
- **Push**: Translates local SHA-256 objects back into native SHA-1 pack format and streams them using the Smart HTTP `receive-pack` protocol.

---

## 7. Configuration System

`pygit` stores repository configuration in `.pygit/config` using the standard INI-style format:

```ini
[user]
    name = Alice
    email = alice@example.com
[core]
    eol = lf
```

Configuration is managed via the `pygit config` command:
- **Read**: `pygit config user.name` → prints the value
- **Write**: `pygit config user.name "Alice"` → sets the value
- **List**: `pygit config --list` → prints all entries as `section.key=value`
- **Unset**: `pygit config --unset user.name` → removes the key

The `commit` command auto-reads `user.name` and `user.email` from config as default author identity.

---

## 8. Notes Storage

Git Notes allow attaching metadata to commits without changing their SHA. `pygit` stores notes as:

1. **Note Blobs**: The note text is stored as a regular `BlobObject` in the object store.
2. **Mapping File**: `.pygit/notes.json` maintains a JSON mapping of `commit_sha → note_blob_sha`.

```json
{
    "a1b2c3d4...": "e5f6g7h8..."
}
```

This design keeps notes completely separate from the commit DAG, so adding or removing a note never changes any commit hash.

---

## 9. Line-Level diff3 Merge Algorithm

When two branches modify the same file, `pygit` uses a line-level three-way merge algorithm (diff3):

1. **Compute Edits**: For both "ours" and "theirs", compute edit ranges against the common base using `difflib.SequenceMatcher`.
2. **Walk Edits**: Process edit ranges in order of their position in the base file:
   - **Non-overlapping edits**: Applied automatically without conflict.
   - **Identical overlapping edits**: Both sides made the same change — applied once.
   - **Conflicting overlapping edits**: Wrapped in standard conflict markers:
     ```
     <<<<<<< HEAD
     our changes
     =======
     their changes
     >>>>>>> target
     ```
3. **Copy Base Lines**: Unmodified regions of the base are copied through verbatim.

This is a significant improvement over the previous approach, which marked the **entire file** as conflicted whenever both sides modified it. Now, non-overlapping changes (e.g., one side edits line 1, the other edits line 50) merge cleanly and automatically.

---

## 10. Sparse-Checkout Mechanics

Sparse checkout restricts which repository files are written to the physical working tree:

1. **Rule Persistence**: Patterns are saved to `.pygit/info/sparse-checkout` (fnmatch / directory pattern format).
2. **Checkout Filtering**: During `pygit checkout`, `SparseCheckout.matches(path)` tests each file path. Matching files are created on disk and added to `.pygit/index`; non-matching files are omitted from the working tree and index.
3. **Disabling**: Running `pygit sparse-checkout disable` deletes `.pygit/info/sparse-checkout` and triggers a full checkout to restore all repository files.

---

## 11. SSH Subprocess Transport (`remote_ssh.py`)

`pygit` handles SSH remote endpoints (`git@host:repo.git` and `ssh://user@host/repo.git`) by launching a managed subprocess:

1. **SSH Command Construction**: Parses SSH targets into `user`, `host`, `port`, and `path`, constructing:
   `ssh [-p PORT] user@host "git-upload-pack 'path'"` (or `git-receive-pack`).
2. **Stream Interactivity**: `SSHRemoteClient.open_process()` returns a `subprocess.Popen` object with pipe-wrapped stdin/stdout.
3. **Pkt-Line Integration**: Remote discovery and pack data are read and written using standard `pkt-line` framing directly over the SSH process standard streams.

---

## 12. Fixup / Squash & Autosquash Mechanics

1. **Commit Creation**: `pygit commit --fixup <COMMIT>` (or `--squash`) resolves the target commit and formats the new commit message as `fixup! <target_subject>` or `squash! <target_subject>`.
2. **Autosquash Reordering**: During `pygit rebase <TARGET> --autosquash`, `pygit` inspects the pending commit list, extracts `fixup!`/`squash!` commits, and automatically reorders them directly behind their target commit in the rebase queue.

---

## 13. Rename Tracking (`log --follow`)

When `log(follow="path")` is invoked:
1. `pygit` walks commit history tracking the target path.
2. If a commit deletes or moves the path, `pygit` compares blob SHA hashes between parent and child trees to detect renames (`old_path -> new_path`).
3. The traversal seamlessly switches to follow `old_path` in earlier history commits.

---

## 14. Binary Commit-Graph Acceleration (`.pygit/objects/info/commit-graph`)

1. **Format**: Starts with magic header `CGPH`, version `1`, commit count, followed by 70-byte records:
   - `32 bytes`: Commit SHA-256
   - `32 bytes`: Tree SHA-256
   - `4 bytes`: Generation number (topological level)
   - `2 bytes`: Parent count
   - `32*N bytes`: Parent SHA-256 list
2. **Acceleration**: Pre-computed generation numbers and parent lists allow fast BFS/DFS traversals without decompressing loose/pack commit objects.

---

## 15. Reuse Recorded Resolution (`rerere`)

1. **Preimage Recording**: When a merge produces conflict markers, `RerereEngine.record_conflict()` hashes the conflict block content and creates a folder in `.pygit/rr-cache/<hash>/preimage`.
2. **Postimage Recording**: When the user resolves the conflict and stages/commits it, `record_resolution()` saves the resolved block to `.pygit/rr-cache/<hash>/postimage`.
3. **Auto-Reuse**: When an identical conflict occurs in a future merge, `_apply_three_way()` detects matching preimage in `rr-cache`, retrieves `postimage`, and automatically applies the resolution without flagging a conflict.

---

## 16. Low-Level Plumbing Architecture

- `pygit write-tree`: Directly inspects the staged index entries, builds parent/child `TreeObject`s recursively, writes them to loose object storage, and prints the root Tree SHA.
- `pygit commit-tree`: Creates a raw `CommitObject` linking a specific Tree SHA and parent SHA(s) with identity metadata, writing it directly to disk without requiring clean working tree checks or ref mutations.

---

## 17. Stash Untracked & Short Status Mechanics

1. **Untracked Stashing**: `stash_push(include_untracked=True)` scans `status()["untracked"]`, stores them into the snapshot tree, and unlinks them from the working tree.
2. **Short Status Output**: `format_short_status()` calculates a 2-character matrix mapping staged (`X`) and unstaged (`Y`) changes per file (e.g., `M `, ` M`, `MM`, `A `, `??`).

---

## 18. Format Strings, Squash Merge & Enhanced Listings

### 18.1 `log --format`

`format_commit(sha, commit, fmt)` performs placeholder substitution on the format string:

| Placeholder | Meaning                   |
|-------------|---------------------------|
| `%H`        | Full SHA-256 hash         |
| `%h`        | Short SHA (12 chars)      |
| `%an`       | Author name               |
| `%ae`       | Author email              |
| `%s`        | Subject (first msg line)  |
| `%b`        | Body (remaining msg)      |
| `%d`        | Ref decorations           |
| `%n`        | Newline                   |

### 18.2 `merge --squash`

With `squash=True`, `merge()` performs the full three-way merge (applying changes to worktree and staging them in the index) but **skips creating a merge commit**. The user retains full control to `pygit commit` manually, producing a single "squashed" commit with a clean linear history instead of a merge commit with two parents.

### 18.3 `tag -l PATTERN`

Uses Python's `fnmatch.fnmatch()` to filter tag names against a glob pattern (e.g., `v1.*` matches `v1.0`, `v1.1` but not `v2.0`).

### 18.4 `branch -a`

`list_remote_branches()` walks `.pygit/refs/remotes/` recursively, returning ref paths like `remotes/origin/main`. Combined with local branch listing, `branch -a` shows the complete picture.

---

## 19. File Move, Tree Inspection & Environment Diagnostics

### 19.1 `pygit mv`

`mv(src, dst)` supports moving both single files and entire directory structures:
1. Moves the physical path on disk using `shutil.move()`.
2. Updates index entries for all affected paths: removes `src` entries and re-adds them under `dst` with updated file metadata and SHA.

### 19.2 `pygit ls-tree`

`ls_tree(tree_ish, recursive, name_only)` parses tree objects directly:
- Accepts any commit or tree SHA / ref (`HEAD`, tag, branch).
- Traverses child trees recursively when `recursive=True`.
- Displays Git mode, object type (`tree` or `blob`), SHA, and path (or filename only with `name_only=True`).

### 19.3 `pygit rev-parse` Diagnostic Flags

- `--is-inside-work-tree`: Prints `true`.
- `--git-dir`: Prints absolute path to `.pygit`.
- `--show-toplevel`: Prints root directory path of the working tree.
- `--short`: Formats commit revision SHA to 12 hex characters.

### 19.4 `pygit status --ignored`

When `ignored=True`, `status()` collects files matched by `IgnoreMatcher` that are not staged in the index, outputting them under the `"ignored"` key.

---

## 20. Commit Amending, Ahead/Behind Analysis & Stash Index Restoration

### 20.1 `pygit commit --amend`

When `amend=True`, `commit()` modifies the current tip commit:
- Inherits parent(s) of the current HEAD commit instead of creating a link to current HEAD.
- Preserves or replaces commit message and author metadata while updating the root tree hash.

### 20.2 `pygit ahead_behind`

`ahead_behind(ref1, ref2)` computes the symmetric divergence between two revisions:
1. Identifies the lowest common ancestor `base = _find_merge_base(ref1, ref2)`.
2. Counts commits in `ref1` lineage reachable back to `base` (`behind`).
3. Counts commits in `ref2` lineage reachable back to `base` (`ahead`).

### 20.3 `pygit checkout -b <name> [start_point]`

Allows seeding new branch creation directly from a specific starting commit, tag, or branch revision (e.g. `pygit checkout -b feature v1.0`).

### 20.4 `pygit stash apply --index`

Git stashes store two (or three) commit objects: `w` (worktree snapshot) and `i` (index snapshot). When `restore_index=True`, `stash_apply()` reads `i` commit's tree entries (`parents[1]`) and reinstates them directly into the staging index.

---

## 21. Object Statistics, Detached Checkout, Topological Sorting & Porcelain Status

### 21.1 `pygit count-objects -v`

`analyze_repository_objects()` computes complete repository disk footprint:
- Loose objects: count and total bytes.
- Packfiles: count of `.idx` / `.pack` pairs and total packed objects.
- `count-objects -v` formats output into standard Git key-value stats (`count`, `size`, `in-pack`, `packs`, `size-pack`).

### 21.2 `pygit checkout --detach`

When `--detach` is specified, `checkout()` sets HEAD as a detached SHA (`.pygit/HEAD` contains the raw 64-hex SHA rather than `ref: refs/heads/...`), allowing temporary inspection without creating or advancing a branch.

### 21.3 `pygit rev-list --topo-order`

`log(topo_order=True)` sorts the commit list so that no parent commit is emitted until all of its child commits have been emitted. It uses an in-degree dependency graph to ensure strict topological ordering across parallel branch histories.

### 21.4 `pygit status --porcelain`

`status --porcelain` outputs Porcelain V1 2-character status codes (`XY PATH`) designed for machine parsing, without terminal styling or decorative headers.

---

## 22. Single-Branch Clone, Merge Commit Filtering, Diff Name/Status & Describe Options

### 22.1 `pygit clone --single-branch`

When `--single-branch` is passed, `clone()` fetches and sets up remote tracking refs only for the targeted branch (default or `-b BRANCH`), pruning tracking ref entries for all other remote branches.

### 22.2 `pygit log --merges` / `--no-merges`

`log(merges_only=True)` filters the history walk to yield only commits with two or more parents. `log(merges_only=False)` filters out all merge commits.

### 22.3 `pygit diff --name-status` & `--name-only`

- `--name-status` formats output as `<status>\t<path>` lines (`A`, `M`, `D`).
- `--name-only` outputs only the changed file paths.

### 22.4 `pygit describe --tags` & `--always`

- `--tags`: includes lightweight tags in the commit search graph.
- `--always`: falls back to returning the short 7-hex commit SHA when no tag is reachable.

---

## 23. Stash Plumbing, Path-Filtered Commits, Line History Trace & Branch Contains

### 23.1 `pygit stash create` & `stash store`

- `stash_create()` constructs the commit DAG representation (`i_commit` and `w_commit`) and saves them to the object store without updating `.pygit/refs/stash` or resetting the working tree.
- `stash_store()` places the specified commit SHA onto `.pygit/refs/stash`.

### 23.2 `pygit commit --only` & `--include`

- `--only PATH...`: temporarily resets index to HEAD, stages specified paths, creates the commit, and restores remaining staged entries.
- `--include PATH...`: stages specified paths into index prior to creating the commit.

### 23.3 `pygit log -L <start>,<end>:<file>`

`log(line_range=(start, end, file))` evaluates line slices `file[start-1:end]` across parent-child commit boundaries, filtering out commits that left the specified line range untouched.

### 23.4 `pygit branch --contains` & `--no-contains`

`branch(contains=commit)` / `branch(no_contains=commit)` performs BFS ancestry reachability checks from each branch tip to determine if `commit` is in the branch's commit history.

---

## 24. Symbolic Ref Resolution, First-Parent Walk, Diff Whitespace & Reset Patch

### 24.1 `pygit rev-parse --symbolic-full-name`

`rev_parse(symbolic_full_name=True)` maps short reference identifiers to canonical full paths:
- `"main"` → `"refs/heads/main"`
- `"v1.0"` → `"refs/tags/v1.0"`
- `"origin/main"` → `"refs/remotes/origin/main"`

### 24.2 `pygit log --first-parent`

`log(first_parent=True)` restricts commit queue expansion at merge nodes exclusively to `parents[0]`, bypassing side topic branch histories.

### 24.3 `pygit diff -w` & `diff -b`

- `-w` / `--ignore-all-space`: strips all whitespace characters before evaluating line differences.
- `-b` / `--ignore-space-change`: collapses consecutive whitespace into single space characters prior to comparison.

### 24.4 `pygit reset -p`

`reset_patch()` unstages staged index entries for specified paths back to their HEAD state, acting as an automated/interactive patch hunk unstage tool.

---

## 25. Stash Keep-Index, Diff3 Conflict Style & Symmetric Difference Left-Right

### 25.1 `pygit stash push --keep-index` (`-k`)

`stash_push(keep_index=True)` saves index and working tree changes to `refs/stash`, resets working directory to HEAD, and then restores all staged index entries so they remain in both working tree and index.

### 25.2 `pygit merge --conflict=diff3`

`_merge_lines_three_way(conflict_style="diff3")` renders an additional `||||||| base` block containing common ancestor lines inside conflict markers.

### 25.3 `pygit rev-list --left-right`

`rev_list(commit="A...B", left_right=True)` outputs `<SHA` for commits reachable from `A` but not `B`, and `>SHA` for commits reachable from `B` but not `A`.

---

## 26. Stash Clear, Status Upstream Info & Namespace Rev-Parse

### 26.1 `pygit stash clear`

`stash_clear()` deletes `.pygit/refs/stash` and unlinks `.pygit/logs/refs/stash`, purging all stash entries from the repository stack.

### 26.2 `pygit rev-parse --branches` / `--tags` / `--remotes`

`rev_parse_namespaces(branches=True, tags=True, remotes=True)` iterates over all references matching the target namespaces and outputs their full SHAs.

### 26.3 `pygit status` Upstream Tracking Info

`status()` checks `ahead_behind("HEAD", f"origin/{branch}")` to attach upstream branch name, ahead count, and behind count metadata to the status response payload.

---

## 27. Branch Merged Filtering, Allow Empty Commit, Parent Count Filtering & Show-Branch

### 27.1 `pygit branch --merged` & `--no-merged`

`branch(merged=target)` / `branch(no_merged=target)` performs reachability checks between branch tips and the target commit (defaults to HEAD).

### 27.2 `pygit commit --allow-empty`

`commit(allow_empty=True)` bypasses the working-tree clean check, writing a new commit object whose tree SHA equals the parent commit's tree SHA.

### 27.3 `pygit log --min-parents` & `--max-parents`

`log(min_parents=N, max_parents=M)` evaluates `len(commit.parents)` to filter commit traversal outputs.

### 27.4 `pygit show-branch`

`show_branch()` renders a compact overview matrix displaying local branches, current HEAD pointers, and top commit titles.

---

## 28. Commit Cleanup Modes, Date Formatting, Diff Regex Line Filtering & Rev-Parse Repository Flags

### 28.1 `pygit commit --cleanup=<mode>`

`commit(cleanup="strip"|"whitespace"|"verbatim")` sanitizes or preserves the commit message body based on requested cleanup rules.

### 28.2 `pygit log --date=<format>`

`pretty_print(date_format="short"|"iso"|"relative")` formats the author date into ISO 8601 strings, short YYYY-MM-DD dates, or human-readable relative time deltas.

### 28.3 `pygit diff -I <regex>` / `--ignore-matching-lines=<regex>`

`diff(ignore_matching_lines=pattern)` filters diff hunks by dropping added/deleted lines matching the compiled regular expression.

### 28.4 `pygit rev-parse --is-shallow-repository` / `--is-bare-repository`

Resolves repository type and shallow clone metadata (`.pygit/shallow`).

---

## 29. Stash Staged-Only Mode & Rev-Parse Prefix

### 29.1 `pygit stash push --staged` (`-S`)

`stash_push(staged_only=True)` saves only the staged index entries into a stash commit, restoring staged paths in the working tree and index to HEAD state while preserving unstaged modifications.

### 29.2 `pygit rev-parse --prefix`

`rev_parse(prefix=True)` returns the relative path of the current working directory relative to the repository worktree root.

---

## 30. Reuse Commit Message, Rev-Parse Revisions Filtering & Diffstat Width Limit

### 30.1 `pygit commit -C <commit>` / `-c <commit>`

`commit(reuse_message=commit)` extracts the message from the target commit object to build a new commit.

### 30.2 `pygit rev-parse --revs-only` & `--no-revs`

Filters command arguments, outputting only items that resolve to valid revisions (`--revs-only`) or non-revision flags (`--no-revs`).

### 30.3 `pygit diff --stat-width=<width>`

`diff(stat_width=width)` limits the maximum character width of the generated diffstat block.

---

## 31. Commit Author Override, Rev-Parse Shell Quoting & Compact Diff Summary

### 31.1 `pygit commit --author="Name <email>"`

`commit(author="Name <email>")` parses name and email components to override authorship metadata on new commits.

### 31.2 `pygit rev-parse --sq`

`rev_parse(sq=True)` single-quotes the output string and escapes inner single quotes for safe shell `eval`.

### 31.3 `pygit diff --compact-summary`

`diff(compact_summary=True)` prints concise mode status lines (e.g. `create mode 100644 path`, `delete mode 100644 path`).

---

## 32. Commit Timestamp Override, Rev-Parse Negation & Raw Diff Output

### 32.1 `pygit commit --date=<date>`

`commit(commit_date=date)` overrides author and committer timestamp with explicit Unix epoch or ISO 8601 string values.

### 32.2 `pygit rev-parse --not`

`rev_parse(not_flag=True)` prefixes the resolved commit SHA with `^` for negation in revision selection.

### 32.3 `pygit diff --raw`

`diff(raw=True)` outputs raw mode/SHA diff lines (e.g. `:100644 100644 <old_sha> <new_sha> M\tfile`).

---

## 33. Reset Author, Custom Diff Prefixes & Rev-Parse Namespace Patterns

### 33.1 `pygit commit --reset-author`

`commit(amend=True, reset_author=True)` discards the author metadata of the commit being amended and applies current system/configured identity.

### 33.2 `pygit diff --src-prefix=<prefix>` & `--dst-prefix=<prefix>`

`diff(src_prefix=prefix, dst_prefix=prefix)` replaces default `a/` and `b/` prefixes in patch headers.

### 33.3 `pygit rev-parse --branches=<pattern>`

`rev_parse_namespaces(pattern=pattern)` filters reference lists using wildcard glob matching.

---

## 34. Commit Signoff, Rev-Parse Verify & No-Prefix Diff Header

### 34.1 `pygit commit --signoff` / `-s`

`commit(signoff=True)` appends a `Signed-off-by: Name <email>` trailer line to the end of the commit message body.

### 34.2 `pygit rev-parse --verify`

`rev_parse(verify=True)` ensures the specified revision argument exists and resolves to a valid object, exiting with code 1 if invalid.

### 34.3 `pygit diff --no-prefix`

`diff(no_prefix=True)` clears source and destination path prefixes (`src_prefix=""`, `dst_prefix=""`) in patch headers.

---

## 35. Short Status Branch Output, Commit Dry-Run & Custom Short Rev-Parse Length

### 35.1 `pygit status -s -b` / `--branch`

Prints `## branch...upstream [ahead A, behind B]` header line above short status entries.

### 35.2 `pygit commit --dry-run`

Previews working tree and index status changes without creating objects or modifying refs.

### 35.3 `pygit rev-parse --short[=N]`

Truncates resolved SHA-256 string to specified character length `N` (default 7).

---

## 36. Revision Default Fallback, Submodule Ignore & Commit No-Status

### 36.1 `pygit rev-parse --default=<arg>`

Provides a fallback reference `ARG` when no target argument is explicitly supplied to `rev-parse`.

### 36.2 `pygit diff --ignore-submodules`

`diff(ignore_submodules=True)` filters out tracked submodule directory paths from diff calculation.

### 36.3 `pygit commit --no-status`

Suppresses status summary lines when generating initial commit message templates.

---

## 37. Abbreviated Reference Name Parsing & Quiet Commit Creation

### 37.1 `pygit rev-parse --abbrev-ref`

Converts full reference strings (`refs/heads/main`, `refs/remotes/origin/main`) to abbreviated short names (`main`, `origin/main`).

### 37.2 `pygit commit --quiet` / `-q`

`commit(quiet=True)` suppresses printing the commit summary line to standard output upon completion.

---

## 38. Control Directory Detection & Verbose Commit Diff Preview

### 38.1 `pygit rev-parse --is-inside-git-dir`

Checks if current working directory is inside the `.pygit` internal control directory structure.

### 38.2 `pygit commit --verbose` / `-v`

`commit(verbose=True)` appends unified diff of changes to be committed below commit creation summary.

---

## 39. Rename Detection in Diff & Force Commit Message Editing

### 39.1 `pygit diff --find-renames` / `-M`

`diff(find_renames=True)` enables file similarity matching to detect renamed files across trees.

### 39.2 `pygit commit --edit` / `-e`

Forces entry into commit message editor even when a message string is provided via CLI flags.

---

## 40. Bare Repository Query, No-Edit Commit Amend & Copy Detection

### 40.1 `pygit rev-parse --is-bare-repository`

Queries repository bare configuration mode (returns `false` for standard working tree repositories).

### 40.2 `pygit commit --no-edit`

Suppresses editor prompt when amending or reusing commit messages.

### 40.3 `pygit diff --find-copies` / `-C`

`diff(find_copies=True)` enables file copy similarity detection across trees.

---

## 41. Shallow Clone Query, Seamless Amend Commit & Submodule Diff Format

### 41.1 `pygit rev-parse --is-shallow-repository`

Queries whether the repository is a shallow clone (checks `.pygit/shallow` marker file).

### 41.2 `pygit commit --amend --no-edit`

Reuses previous commit message when amending without opening interactive text editor.

### 41.3 `pygit diff --submodule`

Controls display format of submodule diffs (`short`, `log`).

---

## 42. Git Directory Resolution, Directory Statistics & Ahead/Behind Status Control

### 42.1 `pygit rev-parse --resolve-git-dir <path>`

Resolves the absolute path to the `.pygit` control directory for a given repository or gitfile path.

### 42.2 `pygit diff --dirstat`

`diff(dirstat=True)` calculates percentage distribution of line changes across directories.

### 42.3 `pygit status --ahead-behind` / `--no-ahead-behind`

Toggles calculation of upstream branch `[ahead A, behind B]` counts in status output.

---

## 43. Diff Graph Width Limit, Path Formatting & Comment Prefix Support

### 43.1 `pygit diff --stat-graph-width=<width>`

Limits maximum character width for the `+`/`-` histogram graph in `--stat` summary output.

### 43.2 `pygit rev-parse --path-format=<absolute|relative>`

Formats repository control path output as absolute or relative path string.

### 43.3 `pygit status --display-comment-prefix`

Toggles inclusion of comment prefix `#` characters in status template preview.


























