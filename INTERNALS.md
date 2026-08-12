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

