"""
pygit/repo.py
=============
High-level Repository API.

This is the "glue" layer that connects the object store, index, and ref
store into the familiar Git workflow:

    init → add → commit → log / status / diff / checkout / branch / tag

Design decisions
----------------
* ``Repository.init()`` is a class-method factory; ``Repository(path)``
  opens an existing repo (raises if ``.pygit`` is absent).
* All paths accepted by public methods are resolved against CWD, exactly
  as real Git does when called from a sub-directory.
* Trees are built recursively from the flat index by grouping entries on
  the ``/``-separated components of their path strings.
* ``checkout`` cleans up files that were tracked but absent in the target
  tree, then restores the full target tree and rebuilds the index.
"""

from __future__ import annotations

import json
import stat as stat_mod
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .diff import unified_diff
from .ignore import IgnoreMatcher
from .index import Index, IndexEntry, _mode_for
from .objects import BlobObject, CommitObject, GitObject, TreeObject
from .objects.commit import Identity
from .refs import ReflogEntry, RefStore
from .store import ObjectStore


class Repository:
    """
    A pygit repository rooted at *path*.

    Attributes
    ----------
    worktree  : absolute Path to the working directory
    pygit_dir : absolute Path to the ``.pygit`` directory
    store     : :class:`~pygit.store.ObjectStore`
    index     : :class:`~pygit.index.Index`
    refs      : :class:`~pygit.refs.RefStore`
    """

    def __init__(self, path: str = ".") -> None:
        self.worktree = Path(path).resolve()
        self.pygit_dir = self.worktree / ".pygit"
        if not self.pygit_dir.is_dir():
            raise RuntimeError(
                f"Not a pygit repository: {self.worktree}\n"
                "Run 'pygit init' first."
            )
        self.store = ObjectStore(self.pygit_dir / "objects")
        self.index = Index(self.pygit_dir / "index")
        self.refs  = RefStore(self.pygit_dir)

    # ------------------------------------------------------------------
    # init
    # ------------------------------------------------------------------

    @classmethod
    def init(cls, path: str = ".") -> "Repository":
        """Create a new repository (or reinitialise an existing one)."""
        worktree  = Path(path).resolve()
        pygit_dir = worktree / ".pygit"

        worktree.mkdir(parents=True, exist_ok=True)
        reinit = pygit_dir.exists()

        (pygit_dir / "objects").mkdir(parents=True, exist_ok=True)
        (pygit_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (pygit_dir / "refs" / "tags").mkdir(parents=True, exist_ok=True)
        (pygit_dir / "refs" / "remotes").mkdir(parents=True, exist_ok=True)
        head = pygit_dir / "HEAD"
        if not head.exists():
            head.write_text("ref: refs/heads/main", encoding="utf-8")

        verb = "Reinitialized existing" if reinit else "Initialized empty"
        print(f"{verb} pygit repository in {pygit_dir}")

        repo = cls.__new__(cls)
        repo.worktree = worktree
        repo.pygit_dir = pygit_dir
        repo.store = ObjectStore(pygit_dir / "objects")
        repo.index = Index(pygit_dir / "index")
        repo.refs  = RefStore(pygit_dir)
        return repo

    # ------------------------------------------------------------------
    # remote / clone / fetch / pull
    # ------------------------------------------------------------------

    @classmethod
    def clone(cls, url: str, path: Optional[str] = None) -> "Repository":
        """Clone a real Git smart HTTP repository into a new pygit worktree."""
        if path is None:
            name = url.rstrip("/").rsplit("/", 1)[-1]
            path = name[:-4] if name.endswith(".git") else name
        destination = Path(path).resolve()
        if destination.exists():
            if not destination.is_dir() or any(destination.iterdir()):
                raise RuntimeError(f"Destination path is not empty: {destination}")

        repo = cls.init(str(destination))
        repo.add_remote("origin", url)
        result = repo.fetch("origin")
        branch = str(result["default_branch"] or "main")
        sha = repo.refs.get_remote("origin", branch)
        if not sha:
            raise RuntimeError("Remote did not provide a default branch.")
        repo.refs.set_branch(branch, sha, message=f"clone: from {url}")
        repo.refs.set_head_symbolic(branch, message=f"clone: from {url}")
        repo._replace_worktree_from_commit(sha)
        return repo

    def add_remote(self, name: str, url: str) -> None:
        """Add or update a named smart HTTP remote."""
        config = self._read_config()
        remotes = config.setdefault("remotes", {})
        remotes[name] = {"url": url}
        self._write_config(config)

    def remove_remote(self, name: str) -> None:
        """Remove a remote, its tracking refs, and its native SHA map."""
        config = self._read_config()
        remotes = config.setdefault("remotes", {})
        if name not in remotes:
            raise KeyError(f"Unknown remote: '{name}'")
        del remotes[name]
        self._write_config(config)
        self.refs.delete_remote(name)
        self._delete_native_map(name)

    def rename_remote(self, old: str, new: str) -> None:
        """Rename a remote and its tracking/native-map namespaces."""
        config = self._read_config()
        remotes = config.setdefault("remotes", {})
        if old not in remotes:
            raise KeyError(f"Unknown remote: '{old}'")
        if new in remotes:
            raise RuntimeError(f"Remote already exists: '{new}'")
        remotes[new] = remotes.pop(old)
        self._write_config(config)
        self.refs.rename_remote(old, new)
        self._rename_native_map(old, new)

    def prune_remote(self, remote: str = "origin") -> Dict[str, object]:
        """Delete remote-tracking branches no longer advertised by *remote*."""
        from .remote import SmartHttpClient

        config = self._read_config()
        settings = config.get("remotes", {}).get(remote)
        if not settings:
            raise KeyError(f"Unknown remote: '{remote}'")
        advertisement = SmartHttpClient(str(settings["url"])).discover()
        advertised = {
            name[len("refs/heads/"):]
            for name in advertisement.refs
            if name.startswith("refs/heads/")
        }
        stale = [
            branch
            for branch in self.refs.list_remotes(remote)
            if branch not in advertised
        ]
        for branch in stale:
            self.refs.delete_remote(remote, branch)
        return {"remote": remote, "pruned": stale}

    def list_remotes(self) -> Dict[str, str]:
        """Return ``remote -> URL`` mappings."""
        config = self._read_config()
        return {
            name: str(settings["url"])
            for name, settings in config.get("remotes", {}).items()
        }

    def fetch(self, remote: str = "origin") -> Dict[str, object]:
        """Fetch branches and tags from a smart HTTP remote."""
        from .remote import NativeImporter, SmartHttpClient

        config = self._read_config()
        settings = config.get("remotes", {}).get(remote)
        if not settings:
            raise KeyError(f"Unknown remote: '{remote}'")

        client = SmartHttpClient(str(settings["url"]))
        advertisement = client.discover()
        native_map = self._read_native_map(remote)
        known_by_native = {native: sha for sha, native in native_map.items()}
        native_refs = self._advertised_import_refs(advertisement.refs)

        if native_refs and all(native_oid in known_by_native for native_oid in native_refs.values()):
            imported = {
                ref_name: known_by_native[native_oid]
                for ref_name, native_oid in native_refs.items()
            }
            object_count = 0
        else:
            result = client.fetch(
                haves=native_map.values(),
                advertisement=advertisement,
            )
            importer = NativeImporter(self.store, result.objects, known=known_by_native)
            imported = {
                ref_name: importer.import_oid(native_oid)
                for ref_name, native_oid in native_refs.items()
            }
            native_map.update(
                {
                    pygit_sha: native_oid
                    for native_oid, pygit_sha in importer.converted.items()
                }
            )
            self._write_native_map(native_map, remote)
            object_count = len(result.objects)

        for ref_name, sha in imported.items():
            if ref_name.startswith("refs/heads/"):
                self.refs.set_remote(remote, ref_name[len("refs/heads/"):], sha)
            elif ref_name.startswith("refs/tags/"):
                self.refs.set_tag(ref_name[len("refs/tags/"):], sha)

        default_ref = advertisement.symrefs.get("HEAD")
        default_branch = (
            default_ref[len("refs/heads/"):]
            if default_ref and default_ref.startswith("refs/heads/")
            else self._infer_default_branch(advertisement.refs)
        )
        settings["default_branch"] = default_branch
        self._write_config(config)
        return {
            "remote": remote,
            "default_branch": default_branch,
            "refs": imported,
            "objects": object_count,
        }

    def pull(self, remote: str = "origin") -> Dict[str, object]:
        """Fetch and merge the current branch's remote-tracking ref."""
        branch = self.refs.current_branch()
        if not branch:
            raise RuntimeError("Cannot pull with a detached HEAD.")
        self.fetch(remote)
        sha = self.refs.get_remote(remote, branch)
        if not sha:
            raise KeyError(f"Remote branch not found: '{remote}/{branch}'")
        return self.merge(sha, message=f"Merge '{remote}/{branch}'")

    def push(self, remote: str = "origin", force: bool = False) -> Dict[str, object]:
        """Push the current branch to a smart HTTP Git receive-pack endpoint."""
        from .remote import NativeExporter, SmartHttpPushClient

        branch = self.refs.current_branch()
        head_sha = self.refs.resolve_head()
        if not branch:
            raise RuntimeError("Cannot push with a detached HEAD.")
        if not head_sha:
            raise RuntimeError("Cannot push an empty repository.")
        config = self._read_config()
        settings = config.get("remotes", {}).get(remote)
        if not settings:
            raise KeyError(f"Unknown remote: '{remote}'")

        client = SmartHttpPushClient(str(settings["url"]))
        advertisement = client.discover()
        ref_name = f"refs/heads/{branch}"
        old_native = advertisement.refs.get(ref_name, "0" * 40)
        native_map = self._read_native_map(remote)
        old_internal: Optional[str] = None
        if old_native != "0" * 40 and not force:
            old_internal = next(
                (
                    internal
                    for internal, native in native_map.items()
                    if native == old_native
                ),
                None,
            )
            if (
                not old_internal
                or old_internal not in self._ancestor_distances(head_sha)
            ):
                raise RuntimeError(
                    "Push rejected: remote tip is not an ancestor of HEAD; "
                    "fetch first or use --force."
                )

        if old_native != "0" * 40 and old_internal is None:
            old_internal = next(
                (
                    internal
                    for internal, native in native_map.items()
                    if native == old_native
                ),
                None,
            )
        have_shas = (
            set(self._ancestor_distances(old_internal))
            if old_internal
            else set()
        )
        exporter = NativeExporter(self.store, native_map, have_shas=have_shas)
        new_native = exporter.export_oid(head_sha)
        if old_native == new_native:
            return {
                "status": "up-to-date",
                "remote": remote,
                "branch": branch,
                "sha": head_sha,
                "objects": 0,
            }

        result = client.push(
            ref_name,
            new_native,
            exporter.objects,
            advertisement=advertisement,
        )
        native_map.update(exporter.converted)
        self._write_native_map(native_map, remote)
        self.refs.set_remote(remote, branch, head_sha)
        return {
            "status": "pushed",
            "remote": remote,
            "branch": branch,
            "sha": head_sha,
            "old_oid": result.old_oid,
            "new_oid": result.new_oid,
            "objects": result.objects_sent,
        }

    # ------------------------------------------------------------------
    # reset
    # ------------------------------------------------------------------

    def reset(self, target: str = "HEAD", mode: str = "mixed") -> Dict[str, object]:
        """
        Move HEAD to *target*.

        ``soft``  moves only the current ref.
        ``mixed`` moves the ref and resets the index.
        ``hard``  moves the ref, resets the index, and restores tracked files.
        """
        if mode not in {"soft", "mixed", "hard"}:
            raise ValueError("reset mode must be one of: soft, mixed, hard")

        operation = self._operation_name()
        if operation and not (mode == "hard" and operation in {"merge", "cherry-pick", "rebase"}):
            raise RuntimeError(f"Cannot reset during a {operation} operation.")

        sha = self._resolve_revision(target)
        old_sha = self.refs.resolve_head()
        branch = self.refs.current_branch()
        message = f"reset: moving to {target}"
        if branch:
            self.refs.set_branch(branch, sha, message=message)
        else:
            self.refs.set_head_detached(sha, message=message)

        old_index_paths = set(self.index.paths())
        if mode == "mixed":
            self._reset_index_to_commit(sha)
        if mode == "hard":
            self._replace_worktree_from_commit(sha, remove_paths=old_index_paths)
            self._clear_merge_state()
            self._clear_cherry_pick_state()
            self._clear_rebase_state()
            self._clear_conflicts()

        return {
            "status": "reset",
            "mode": mode,
            "old": old_sha,
            "sha": sha,
        }

    def reset_paths(
        self,
        paths: List[str],
        target: str = "HEAD",
    ) -> Dict[str, object]:
        """Reset selected index entries to *target* without moving HEAD."""
        operation = self._operation_name()
        if operation:
            raise RuntimeError(f"Cannot reset paths during a {operation} operation.")
        if not paths:
            raise RuntimeError("reset paths requires at least one pathspec.")
        sha = self._resolve_revision(target)
        target_tree = self._commit_tree_entries(sha)
        changed: List[str] = []

        for pathspec in paths:
            rel = self._normalize_pathspec(pathspec)
            candidates = sorted(
                path
                for path in set(target_tree) | set(self.index.paths())
                if path == rel or path.startswith(f"{rel}/")
            )
            if not candidates:
                raise KeyError(f"pathspec '{pathspec}' did not match any files")
            for path in candidates:
                target_entry = target_tree.get(path)
                if target_entry is None:
                    if path in self.index:
                        self.index.entries.pop(path, None)
                        changed.append(path)
                    continue
                blob_sha, mode = target_entry
                self.index.entries[path] = self._index_entry_for_blob(path, blob_sha, mode)
                changed.append(path)

        self.index.save()
        return {"status": "reset", "sha": sha, "paths": sorted(set(changed))}

    # ------------------------------------------------------------------
    # hash-object / cat-file
    # ------------------------------------------------------------------

    def hash_object(self, data: bytes, write: bool = False) -> str:
        """Return the SHA-256 of *data* as a blob; store it if *write* is True."""
        blob = BlobObject(data)
        return self.store.write(blob) if write else blob.hash()

    def cat_file(self, sha: str) -> GitObject:
        """Read the object identified by *sha* from the store."""
        return self.store.read(sha)

    # ------------------------------------------------------------------
    # add
    # ------------------------------------------------------------------

    def add(self, paths: List[str]) -> None:
        """
        Stage files (equivalent to ``git add``).

        Each element of *paths* is resolved from the worktree root.
        Directories are added recursively; ``.pygit`` is always skipped.
        """
        ignore = IgnoreMatcher(self.worktree)
        for path_str in paths:
            p = self._resolve_worktree_path(path_str)
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and self._should_track(f, ignore):
                        self._stage_file(f)
            elif p.is_file():
                if self._should_track(p, ignore):
                    self._stage_file(p)
            else:
                raise FileNotFoundError(
                    f"pathspec '{path_str}' did not match any files"
                )

    def _stage_file(self, abs_path: Path) -> None:
        rel = abs_path.relative_to(self.worktree).as_posix()
        sha = self.store.write(BlobObject(abs_path.read_bytes()))
        self.index.add(rel, sha, abs_path)
        self._clear_conflict(rel)

    # ------------------------------------------------------------------
    # rm
    # ------------------------------------------------------------------

    def rm(self, path: str, cached: bool = False) -> None:
        """
        Remove *path* from the index (and optionally from disk).

        Parameters
        ----------
        cached : if True, only remove from the index, leave the file on disk.
        """
        if path not in self.index and path not in self._read_conflicts():
            raise KeyError(f"pathspec '{path}' did not match any files")
        self.index.remove(path)
        self._clear_conflict(path)
        if not cached:
            abs_path = self.worktree / path
            if abs_path.exists():
                abs_path.unlink()

    # ------------------------------------------------------------------
    # commit
    # ------------------------------------------------------------------

    def commit(
        self,
        message: str,
        author_name:  str = "Unknown",
        author_email: str = "unknown@example.com",
        parents: Optional[List[str]] = None,
        committer_name: Optional[str] = None,
        committer_email: Optional[str] = None,
        allow_rebase: bool = False,
    ) -> str:
        """
        Create a commit from the current index.

        Returns the new commit's SHA-256 hex string.
        """
        if self._read_rebase_state() and not allow_rebase:
            raise RuntimeError("A rebase is in progress; use 'pygit rebase --continue'.")

        conflicts = self._read_conflicts()
        if conflicts:
            raise RuntimeError(
                "Cannot commit with unresolved conflicts: " + ", ".join(conflicts)
            )

        tree_sha = self._build_tree()
        head_sha = self.refs.resolve_head()
        merge_head = self._read_merge_head()
        if parents is None:
            parents = [head_sha] if head_sha else []
            if merge_head and merge_head not in parents:
                parents.append(merge_head)

        if not head_sha and not self.index.entries:
            raise RuntimeError("Nothing to commit (index is empty).")
        if head_sha and not merge_head:
            head_obj = self.store.read(head_sha)
            if isinstance(head_obj, CommitObject) and head_obj.tree == tree_sha:
                raise RuntimeError("Nothing to commit (working tree clean).")

        author = Identity(author_name, author_email)
        committer = Identity(
            committer_name or author_name,
            committer_email or author_email,
        )
        commit_obj = CommitObject(
            tree=tree_sha,
            parents=parents,
            author=author,
            committer=committer,
            message=message,
        )
        sha = self.store.write(commit_obj)

        branch = self.refs.current_branch()
        if branch:
            self.refs.set_branch(branch, sha, message=f"commit: {message.splitlines()[0]}")
        else:
            self.refs.set_head_detached(sha, message=f"commit: {message.splitlines()[0]}")

        self._clear_merge_state()
        self._clear_cherry_pick_state()
        return sha

    def _build_tree(self) -> str:
        """Recursively build tree objects from the current index; return root SHA."""
        return self._build_tree_from_entries(self.index.all_entries())

    def _build_tree_from_entries(self, entries: List[IndexEntry]) -> str:
        """Recursively build tree objects from flat index-like entries."""
        # Group index entries by their parent directory path (POSIX, no leading slash).
        # e.g.  "src/util/helper.py"  → group "src/util", filename "helper.py"
        dir_entries: Dict[str, List[Tuple[str, IndexEntry]]] = defaultdict(list)
        directories = {""}
        for entry in entries:
            parts    = entry.path.split("/")
            dirname  = "/".join(parts[:-1])   # "" for root-level files
            filename = parts[-1]
            dir_entries[dirname].append((filename, entry))
            for i in range(1, len(parts)):
                directories.add("/".join(parts[:i]))

        def build(prefix: str) -> str:
            tree = TreeObject()

            # Files directly inside this directory
            for filename, entry in dir_entries.get(prefix, []):
                tree.add_entry(entry.mode, filename, entry.sha)

            # Immediate sub-directories
            for dir_path in sorted(directories):
                if dir_path == prefix:
                    continue
                parent = "/".join(dir_path.split("/")[:-1]) if "/" in dir_path else ""
                if parent == prefix:
                    subdir_name = dir_path.split("/")[-1]
                    tree.add_entry("040000", subdir_name, build(dir_path))

            return self.store.write(tree)

        return build("")

    # ------------------------------------------------------------------
    # log
    # ------------------------------------------------------------------

    def log(
        self,
        start: Optional[str] = None,
        max_count: int = 0,
    ) -> List[Tuple[str, CommitObject]]:
        """
        Walk commit history from *start* (defaults to HEAD) via BFS.

        Returns a list of ``(sha, CommitObject)`` pairs, newest first.
        """
        sha = start or self.refs.resolve_head()
        if not sha:
            return []

        result: List[Tuple[str, CommitObject]] = []
        seen: set = set()
        queue = [sha]

        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)

            obj = self.store.read(current)
            if not isinstance(obj, CommitObject):
                continue

            result.append((current, obj))
            if max_count and len(result) >= max_count:
                break
            queue.extend(obj.parents)

        return result

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self) -> Dict:
        """
        Return a dict describing the working-tree state::

            {
              "branch"   : str | None,      # current branch or None (detached)
              "staged"   : [(kind, path)],  # index vs HEAD
              "unstaged" : [(kind, path)],  # working tree vs index
              "untracked": [path],          # files not in the index
              "conflicts": [path],          # unresolved merge conflicts
            }

        *kind* is one of ``"new file"``, ``"modified"``, ``"deleted"``.
        """
        head_tree = self._head_tree_flat()

        # --- staged (HEAD → index) ---
        staged: List[Tuple[str, str]] = []
        index_paths = set(self.index.paths())
        head_paths  = set(head_tree)
        for p in sorted(index_paths - head_paths):
            staged.append(("new file", p))
        for p in sorted(head_paths - index_paths):
            staged.append(("deleted", p))
        for p in sorted(index_paths & head_paths):
            if self.index.get(p).sha != head_tree[p]:  # type: ignore[union-attr]
                staged.append(("modified", p))

        # --- unstaged (index → working tree) ---
        unstaged: List[Tuple[str, str]] = []
        for entry in self.index.all_entries():
            abs_path = self.worktree / entry.path
            if not abs_path.exists():
                unstaged.append(("deleted", entry.path))
            elif BlobObject(abs_path.read_bytes()).hash() != entry.sha:
                unstaged.append(("modified", entry.path))

        # --- untracked ---
        untracked: List[str] = []
        ignore = IgnoreMatcher(self.worktree)
        for f in sorted(self.worktree.rglob("*")):
            if not f.is_file():
                continue
            if ".pygit" in f.parts:
                continue
            rel = f.relative_to(self.worktree).as_posix()
            if rel not in self.index and not ignore.is_ignored(rel):
                untracked.append(rel)

        return {
            "branch":    self.refs.current_branch(),
            "staged":    staged,
            "unstaged":  unstaged,
            "untracked": untracked,
            "conflicts": self._read_conflicts(),
        }

    # ------------------------------------------------------------------
    # diff
    # ------------------------------------------------------------------

    def diff(self, cached: bool = False) -> str:
        """
        Produce a unified-diff string.

        cached=False (default) : working tree vs index
        cached=True            : index vs HEAD  (``git diff --cached``)
        """
        parts: List[str] = []

        if cached:
            head_tree = self._head_tree_flat()
            for path in sorted(set(head_tree) | set(self.index.paths())):
                old_bytes = self._blob_bytes(head_tree.get(path))
                entry = self.index.get(path)
                new_bytes = self._blob_bytes(entry.sha if entry else None)
                if old_bytes != new_bytes:
                    parts.append(
                        f"diff --pygit a/{path} b/{path}\n"
                        + unified_diff(old_bytes, new_bytes, f"a/{path}", f"b/{path}")
                    )
        else:
            for entry in self.index.all_entries():
                old_bytes = self._blob_bytes(entry.sha)
                abs_path  = self.worktree / entry.path
                new_bytes = abs_path.read_bytes() if abs_path.exists() else b""
                if old_bytes != new_bytes:
                    parts.append(
                        f"diff --pygit a/{entry.path} b/{entry.path}\n"
                        + unified_diff(
                            old_bytes, new_bytes,
                            f"a/{entry.path}", f"b/{entry.path}",
                        )
                    )

        return "".join(parts)

    # ------------------------------------------------------------------
    # checkout
    # ------------------------------------------------------------------

    def checkout(self, target: str) -> None:
        """
        Restore the working tree and index to *target* (branch, tag, or SHA).

        Files tracked in the current index but absent in *target* are deleted.
        Untracked files are left untouched.
        """
        sha = self.refs.resolve(target)
        if not sha:
            raise KeyError(f"Unknown revision: '{target}'")

        obj = self.store.read(sha)
        if not isinstance(obj, CommitObject):
            raise ValueError(f"'{target}' does not point to a commit")

        new_tree: Dict[str, str] = {}
        self._flatten_tree(obj.tree, "", new_tree)

        # Remove tracked files that disappear in the new tree
        for path in set(self.index.paths()) - set(new_tree):
            abs_path = self.worktree / path
            if abs_path.exists():
                abs_path.unlink()

        # Write the new tree to disk
        self._restore_tree(obj.tree, self.worktree)

        # Rebuild index from the new tree
        self.index.entries.clear()
        for path, blob_sha in new_tree.items():
            abs_path = self.worktree / path
            mode  = _mode_for(abs_path) if abs_path.exists() else "100644"
            st    = abs_path.stat()      if abs_path.exists() else None
            self.index.entries[path] = IndexEntry(
                path  = path,
                sha   = blob_sha,
                mode  = mode,
                size  = st.st_size  if st else 0,
                mtime = st.st_mtime if st else 0.0,
            )
        self.index.save()

        # Update HEAD
        if self.refs.get_branch(target):
            self.refs.set_head_symbolic(target, message=f"checkout: moving to {target}")
        else:
            self.refs.set_head_detached(sha, message=f"checkout: moving to {target}")

    def _restore_tree(self, tree_sha: str, base_dir: Path) -> None:
        """Recursively write all blobs in *tree_sha* under *base_dir*."""
        tree = self.store.read(tree_sha)
        if not isinstance(tree, TreeObject):
            return
        for entry in tree.entries:
            target = base_dir / entry.name
            if entry.is_dir:
                target.mkdir(exist_ok=True)
                self._restore_tree(entry.sha, target)
            else:
                blob = self.store.read(entry.sha)
                if isinstance(blob, BlobObject):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blob.data)
                    if entry.is_executable:
                        target.chmod(
                            target.stat().st_mode
                            | stat_mod.S_IXUSR
                            | stat_mod.S_IXGRP
                            | stat_mod.S_IXOTH
                        )

    # ------------------------------------------------------------------
    # merge
    # ------------------------------------------------------------------

    def merge(
        self,
        target: str,
        message: Optional[str] = None,
        author_name: str = "Unknown",
        author_email: str = "unknown@example.com",
    ) -> Dict[str, object]:
        """Merge *target* into HEAD using a three-way file merge."""
        self._ensure_no_operation("merge")

        self._ensure_clean_worktree("merge")

        ours = self.refs.resolve_head()
        theirs = self.refs.resolve(target)
        if not ours:
            raise RuntimeError("Cannot merge into an empty repository.")
        if not theirs:
            raise KeyError(f"Unknown revision: '{target}'")
        self._require_commit(ours)
        self._require_commit(theirs)

        if ours == theirs:
            return {"status": "up-to-date", "sha": ours, "conflicts": []}

        base = self._find_merge_base(ours, theirs)
        if base == theirs:
            return {"status": "up-to-date", "sha": ours, "conflicts": []}
        if base == ours:
            branch = self.refs.current_branch()
            self._replace_worktree_from_commit(theirs)
            if branch:
                self.refs.set_branch(branch, theirs, message=f"merge {target}: Fast-forward")
                self.refs.set_head_symbolic(branch, message=f"merge {target}: Fast-forward")
            else:
                self.refs.set_head_detached(theirs, message=f"merge {target}: Fast-forward")
            return {"status": "fast-forward", "sha": theirs, "conflicts": []}

        base_tree = self._commit_tree_entries(base) if base else {}
        our_tree = self._commit_tree_entries(ours)
        their_tree = self._commit_tree_entries(theirs)
        conflicts = self._apply_three_way(base_tree, our_tree, their_tree, target)

        if conflicts:
            self._write_merge_state(theirs, conflicts, ours)
            return {"status": "conflicts", "sha": None, "conflicts": conflicts}

        sha = self.commit(
            message or f"Merge '{target}'",
            author_name=author_name,
            author_email=author_email,
            parents=[ours, theirs],
        )
        return {"status": "merged", "sha": sha, "conflicts": []}

    def merge_abort(self) -> Dict[str, object]:
        """Abort an in-progress conflicted merge and restore pre-merge HEAD."""
        merge_head = self._read_merge_head()
        if not merge_head:
            raise RuntimeError("No merge is in progress.")
        original = self._read_merge_original_head() or self.refs.resolve_head()
        if not original:
            raise RuntimeError("Cannot abort merge without an original HEAD.")
        self._replace_worktree_from_commit(original)
        self._clear_merge_state()
        return {"status": "aborted", "sha": original, "conflicts": []}

    def _apply_three_way(
        self,
        base_tree: Dict[str, Tuple[str, str]],
        our_tree: Dict[str, Tuple[str, str]],
        their_tree: Dict[str, Tuple[str, str]],
        target: str,
    ) -> List[str]:
        """Apply a three-way tree merge to the worktree and index."""
        merged_index = dict(self.index.entries)
        conflicts: List[str] = []

        for path in sorted(set(base_tree) | set(our_tree) | set(their_tree)):
            base_entry = base_tree.get(path)
            our_entry = our_tree.get(path)
            their_entry = their_tree.get(path)

            if our_entry == their_entry:
                chosen = our_entry
            elif our_entry == base_entry:
                chosen = their_entry
            elif their_entry == base_entry:
                chosen = our_entry
            else:
                conflicts.append(path)
                conflict_data = self._conflict_bytes(
                    self._entry_bytes(our_entry),
                    self._entry_bytes(their_entry),
                    target,
                )
                abs_path = self.worktree / path
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(conflict_data)
                continue

            if chosen is None:
                self._remove_worktree_file(path)
                merged_index.pop(path, None)
            else:
                sha, mode = chosen
                self._write_worktree_blob(path, sha, mode)
                merged_index[path] = self._index_entry(path, sha, mode)

        self.index.entries = merged_index
        self.index.save()
        return conflicts

    def _find_merge_base(self, ours: str, theirs: str) -> Optional[str]:
        our_ancestors = self._ancestor_distances(ours)
        their_ancestors = self._ancestor_distances(theirs)
        common = set(our_ancestors) & set(their_ancestors)
        if not common:
            return None
        return min(
            common,
            key=lambda sha: (
                our_ancestors[sha] + their_ancestors[sha],
                max(our_ancestors[sha], their_ancestors[sha]),
            ),
        )

    def _ancestor_distances(self, start: str) -> Dict[str, int]:
        distances: Dict[str, int] = {}
        queue = [(start, 0)]
        while queue:
            sha, distance = queue.pop(0)
            if sha in distances and distances[sha] <= distance:
                continue
            distances[sha] = distance
            obj = self.store.read(sha)
            if isinstance(obj, CommitObject):
                queue.extend((parent, distance + 1) for parent in obj.parents)
        return distances

    # ------------------------------------------------------------------
    # cherry-pick / rebase
    # ------------------------------------------------------------------

    def cherry_pick(
        self,
        target: str,
        committer_name: str = "Unknown",
        committer_email: str = "unknown@example.com",
    ) -> Dict[str, object]:
        """Replay one non-merge commit on top of HEAD."""
        self._ensure_no_operation("cherry-pick")
        self._ensure_clean_worktree("cherry-pick")
        source_sha = self.refs.resolve(target)
        head_sha = self.refs.resolve_head()
        if not source_sha:
            raise KeyError(f"Unknown revision: '{target}'")
        if not head_sha:
            raise RuntimeError("Cannot cherry-pick into an empty repository.")

        conflicts = self._apply_cherry_pick(source_sha, target)
        if conflicts:
            self._write_cherry_pick_state(source_sha, head_sha, target)
            return {"status": "conflicts", "sha": None, "conflicts": conflicts}
        if self._index_matches_head():
            return {"status": "empty", "sha": head_sha, "conflicts": []}

        sha = self._commit_replayed(source_sha, committer_name, committer_email)
        return {"status": "picked", "sha": sha, "conflicts": []}

    def cherry_pick_continue(
        self,
        committer_name: str = "Unknown",
        committer_email: str = "unknown@example.com",
    ) -> Dict[str, object]:
        """Commit a resolved cherry-pick conflict."""
        state = self._read_cherry_pick_state()
        if not state:
            raise RuntimeError("No cherry-pick is in progress.")
        conflicts = self._read_conflicts()
        if conflicts:
            raise RuntimeError(
                "Cannot continue with unresolved conflicts: " + ", ".join(conflicts)
            )

        head_sha = self.refs.resolve_head()
        if not head_sha or self._index_matches_head():
            self._clear_cherry_pick_state()
            return {"status": "empty", "sha": head_sha, "conflicts": []}
        sha = self._commit_replayed(
            str(state["source"]),
            committer_name,
            committer_email,
        )
        return {"status": "picked", "sha": sha, "conflicts": []}

    def cherry_pick_abort(self) -> Dict[str, object]:
        """Discard an in-progress cherry-pick and restore its original HEAD."""
        state = self._read_cherry_pick_state()
        if not state:
            raise RuntimeError("No cherry-pick is in progress.")
        sha = str(state["head_before"])
        self._replace_worktree_from_commit(sha)
        self._clear_cherry_pick_state()
        self._clear_conflicts()
        return {"status": "aborted", "sha": sha, "conflicts": []}

    def rebase(
        self,
        target: str,
        committer_name: str = "Unknown",
        committer_email: str = "unknown@example.com",
    ) -> Dict[str, object]:
        """Replay the current branch's first-parent commits onto *target*."""
        self._ensure_no_operation("rebase")
        self._ensure_clean_worktree("rebase")
        branch = self.refs.current_branch()
        if not branch:
            raise RuntimeError("Cannot rebase with a detached HEAD.")
        head_sha = self.refs.resolve_head()
        onto = self.refs.resolve(target)
        if not head_sha:
            raise RuntimeError("Cannot rebase an empty repository.")
        if not onto:
            raise KeyError(f"Unknown revision: '{target}'")
        self._require_commit(onto)

        base = self._find_merge_base(head_sha, onto)
        if head_sha == onto or base == onto:
            return {"status": "up-to-date", "sha": head_sha, "conflicts": []}
        if base == head_sha:
            self._replace_worktree_from_commit(onto)
            self.refs.set_branch(branch, onto, message=f"rebase: fast-forward to {target}")
            self.refs.set_head_symbolic(branch, message=f"rebase: fast-forward to {target}")
            return {"status": "fast-forward", "sha": onto, "conflicts": []}

        pending = list(reversed(self._first_parent_commits_until(head_sha, base)))
        state = {
            "branch": branch,
            "original_head": head_sha,
            "onto": onto,
            "pending": pending,
            "current": None,
        }
        self._write_rebase_state(state)
        self._replace_worktree_from_commit(onto)
        self.refs.set_branch(branch, onto, message=f"rebase: checkout {target}")
        self.refs.set_head_symbolic(branch, message=f"rebase: checkout {target}")
        return self._continue_rebase(committer_name, committer_email)

    def rebase_continue(
        self,
        committer_name: str = "Unknown",
        committer_email: str = "unknown@example.com",
    ) -> Dict[str, object]:
        """Continue replaying commits after resolving a rebase conflict."""
        if not self._read_rebase_state():
            raise RuntimeError("No rebase is in progress.")
        return self._continue_rebase(committer_name, committer_email)

    def rebase_abort(self) -> Dict[str, object]:
        """Restore the branch tip and worktree from before the rebase."""
        state = self._read_rebase_state()
        if not state:
            raise RuntimeError("No rebase is in progress.")
        branch = str(state["branch"])
        sha = str(state["original_head"])
        self._replace_worktree_from_commit(sha)
        self.refs.set_branch(branch, sha, message="rebase: abort")
        self.refs.set_head_symbolic(branch, message="rebase: abort")
        self._clear_rebase_state()
        self._clear_conflicts()
        return {"status": "aborted", "sha": sha, "conflicts": []}

    def rebase_skip(
        self,
        committer_name: str = "Unknown",
        committer_email: str = "unknown@example.com",
    ) -> Dict[str, object]:
        """Skip the currently stopped rebase commit and continue replaying."""
        state = self._read_rebase_state()
        if not state:
            raise RuntimeError("No rebase is in progress.")
        if not state.get("current"):
            raise RuntimeError("No stopped rebase commit to skip.")
        head_sha = self.refs.resolve_head()
        if not head_sha:
            raise RuntimeError("Cannot skip rebase commit without HEAD.")
        self._replace_worktree_from_commit(head_sha)
        self._clear_conflicts()
        state["current"] = None
        self._write_rebase_state(state)
        return self._continue_rebase(committer_name, committer_email)

    def _continue_rebase(
        self,
        committer_name: str,
        committer_email: str,
    ) -> Dict[str, object]:
        state = self._read_rebase_state()
        if not state:
            raise RuntimeError("No rebase is in progress.")

        current = state.get("current")
        if current:
            conflicts = self._read_conflicts()
            if conflicts:
                raise RuntimeError(
                    "Cannot continue with unresolved conflicts: " + ", ".join(conflicts)
                )
            if not self._index_matches_head():
                self._commit_replayed(
                    str(current),
                    committer_name,
                    committer_email,
                    allow_rebase=True,
                )
            state["current"] = None
            self._write_rebase_state(state)

        pending = list(state["pending"])
        while pending:
            source_sha = str(pending.pop(0))
            state["pending"] = pending
            state["current"] = source_sha
            self._write_rebase_state(state)
            conflicts = self._apply_cherry_pick(source_sha, source_sha[:12])
            if conflicts:
                return {"status": "conflicts", "sha": None, "conflicts": conflicts}
            if not self._index_matches_head():
                self._commit_replayed(
                    source_sha,
                    committer_name,
                    committer_email,
                    allow_rebase=True,
                )
            state["current"] = None
            self._write_rebase_state(state)

        sha = self.refs.resolve_head()
        self._clear_rebase_state()
        return {"status": "rebased", "sha": sha, "conflicts": []}

    def _apply_cherry_pick(self, source_sha: str, target: str) -> List[str]:
        source = self._require_commit(source_sha)
        if len(source.parents) > 1:
            raise RuntimeError("Cannot cherry-pick a merge commit without a mainline.")
        head_sha = self.refs.resolve_head()
        if not head_sha:
            raise RuntimeError("Cannot cherry-pick into an empty repository.")
        base_tree = self._commit_tree_entries(source.parents[0]) if source.parents else {}
        our_tree = self._commit_tree_entries(head_sha)
        their_tree = self._tree_entries(source.tree)
        conflicts = self._apply_three_way(base_tree, our_tree, their_tree, target)
        if conflicts:
            self._write_conflicts(conflicts)
        return conflicts

    def _commit_replayed(
        self,
        source_sha: str,
        committer_name: str,
        committer_email: str,
        allow_rebase: bool = False,
    ) -> str:
        source = self._require_commit(source_sha)
        return self.commit(
            source.message,
            author_name=source.author.name,
            author_email=source.author.email,
            committer_name=committer_name,
            committer_email=committer_email,
            allow_rebase=allow_rebase,
        )

    def _first_parent_commits_until(
        self,
        start: str,
        stop: Optional[str],
    ) -> List[str]:
        commits: List[str] = []
        current: Optional[str] = start
        while current and current != stop:
            commit = self._require_commit(current)
            if len(commit.parents) > 1:
                raise RuntimeError("Cannot rebase a history containing merge commits.")
            commits.append(current)
            current = commit.parents[0] if commit.parents else None
        if current != stop:
            raise RuntimeError("Rebase base is not on the first-parent history.")
        return commits

    def _index_matches_head(self) -> bool:
        head_sha = self.refs.resolve_head()
        if not head_sha:
            return not self.index.entries
        return self._require_commit(head_sha).tree == self._build_tree()

    # ------------------------------------------------------------------
    # bisect
    # ------------------------------------------------------------------

    def bisect_start(
        self,
        bad: Optional[str] = None,
        good: Optional[str] = None,
    ) -> Dict[str, object]:
        """Start a first-parent binary search for the first bad commit."""
        self._ensure_no_operation("bisect")
        self._ensure_clean_worktree("start bisect")
        head_sha = self.refs.resolve_head()
        if not head_sha:
            raise RuntimeError("Cannot bisect an empty repository.")
        state: Dict[str, object] = {
            "original_head": self.refs.get_head(),
            "original_sha": head_sha,
            "bad": self._resolve_revision(bad) if bad else None,
            "good": self._resolve_revision(good) if good else None,
        }
        result = self._bisect_next(state)
        self._write_bisect_state(state)
        return result

    def bisect_good(self, target: Optional[str] = None) -> Dict[str, object]:
        """Mark a revision as good and select the next midpoint."""
        return self._bisect_mark("good", target)

    def bisect_bad(self, target: Optional[str] = None) -> Dict[str, object]:
        """Mark a revision as bad and select the next midpoint."""
        return self._bisect_mark("bad", target)

    def bisect_reset(self) -> Dict[str, object]:
        """Restore the HEAD that was active before bisect started."""
        state = self._read_bisect_state()
        if not state:
            raise RuntimeError("No bisect is in progress.")
        self._ensure_clean_worktree("reset bisect")
        sha = str(state["original_sha"])
        self._replace_worktree_from_commit(sha)
        original_head = str(state["original_head"])
        if original_head.startswith("ref: refs/heads/"):
            self.refs.set_head_symbolic(
                original_head[len("ref: refs/heads/"):],
                message="bisect: reset",
            )
        else:
            self.refs.set_head_detached(sha, message="bisect: reset")
        self._clear_bisect_state()
        return {"status": "reset", "sha": sha}

    def _bisect_mark(self, kind: str, target: Optional[str]) -> Dict[str, object]:
        state = self._read_bisect_state()
        if not state:
            raise RuntimeError("No bisect is in progress.")
        self._ensure_clean_worktree(f"mark bisect {kind}")
        state[kind] = self._resolve_revision(target or "HEAD")
        result = self._bisect_next(state)
        self._write_bisect_state(state)
        return result

    def _bisect_next(self, state: Dict[str, object]) -> Dict[str, object]:
        bad = state.get("bad")
        good = state.get("good")
        if not bad or not good:
            return {"status": "awaiting", "bad": bad, "good": good}
        path = self._first_parent_path(str(bad), str(good))
        if len(path) == 1:
            self._checkout_bisect_commit(path[0])
            return {"status": "found", "sha": path[0]}
        candidate = path[len(path) // 2]
        self._checkout_bisect_commit(candidate)
        return {"status": "testing", "sha": candidate, "remaining": len(path)}

    def _first_parent_path(self, bad: str, good: str) -> List[str]:
        if bad == good:
            raise RuntimeError("A revision cannot be both good and bad.")
        path: List[str] = []
        current: Optional[str] = bad
        while current and current != good:
            path.append(current)
            commit = self._require_commit(current)
            current = commit.parents[0] if commit.parents else None
        if current != good:
            raise RuntimeError("The good revision is not a first-parent ancestor of bad.")
        return path

    def _checkout_bisect_commit(self, sha: str) -> None:
        self._replace_worktree_from_commit(sha)
        self.refs.set_head_detached(sha, message=f"bisect: checkout {sha[:12]}")

    def _resolve_revision(self, target: str) -> str:
        sha = self.refs.resolve(target)
        if not sha:
            raise KeyError(f"Unknown revision: '{target}'")
        self._require_commit(sha)
        return sha

    @staticmethod
    def _conflict_bytes(ours: bytes, theirs: bytes, target: str) -> bytes:
        def with_newline(data: bytes) -> bytes:
            return data if not data or data.endswith(b"\n") else data + b"\n"

        return (
            b"<<<<<<< HEAD\n"
            + with_newline(ours)
            + b"=======\n"
            + with_newline(theirs)
            + f">>>>>>> {target}\n".encode()
        )

    # ------------------------------------------------------------------
    # stash
    # ------------------------------------------------------------------

    def stash_push(
        self,
        message: str = "WIP on current branch",
        author_name: str = "Unknown",
        author_email: str = "unknown@example.com",
    ) -> str:
        """Store dirty worktree content under ``refs/stash`` and restore HEAD."""
        state = self.status()
        if state["conflicts"]:
            raise RuntimeError("Cannot stash with unresolved conflicts.")
        if not any(state[key] for key in ("staged", "unstaged", "untracked")):
            raise RuntimeError("No local changes to save.")

        snapshot_entries = self._snapshot_worktree_entries()
        tree_sha = self._build_tree_from_entries(snapshot_entries)
        head_sha = self.refs.resolve_head()
        previous_stash = self.refs.get_stash()
        parents = [sha for sha in (head_sha, previous_stash) if sha]
        identity = Identity(author_name, author_email)
        stash_obj = CommitObject(
            tree=tree_sha,
            parents=parents,
            author=identity,
            committer=identity,
            message=message,
        )
        stash_sha = self.store.write(stash_obj)
        self.refs.set_stash(stash_sha, message=f"stash: {message}")

        remove_paths = set(self.index.paths()) | {entry.path for entry in snapshot_entries}
        if head_sha:
            self._replace_worktree_from_commit(head_sha, remove_paths=remove_paths)
        else:
            for path in remove_paths:
                self._remove_worktree_file(path)
            self.index.entries.clear()
            self.index.save()
        return stash_sha

    def stash_pop(self) -> str:
        """Restore the latest stash to the working tree and drop its ref."""
        stash_sha = self.refs.get_stash()
        if not stash_sha:
            raise RuntimeError("No stash entries found.")

        state = self.status()
        if any(state[key] for key in ("staged", "unstaged", "untracked", "conflicts")):
            raise RuntimeError("Cannot pop stash with local changes in the working tree.")

        stash_obj = self._require_commit(stash_sha)
        stash_tree = self._tree_entries(stash_obj.tree)
        for path in set(self.index.paths()) - set(stash_tree):
            self._remove_worktree_file(path)
        self._restore_tree(stash_obj.tree, self.worktree)

        previous_stash = stash_obj.parents[1] if len(stash_obj.parents) > 1 else None
        if previous_stash:
            self.refs.set_stash(previous_stash, message="stash pop")
        else:
            self.refs.delete_stash()
        return stash_sha

    def stash_list(self) -> List[Tuple[str, CommitObject]]:
        """Return newest-first stash entries."""
        result: List[Tuple[str, CommitObject]] = []
        sha = self.refs.get_stash()
        while sha:
            obj = self._require_commit(sha)
            result.append((sha, obj))
            sha = obj.parents[1] if len(obj.parents) > 1 else None
        return result

    # ------------------------------------------------------------------
    # reflog
    # ------------------------------------------------------------------

    def reflog(self, ref: str = "HEAD") -> List[ReflogEntry]:
        """Return recorded movements for HEAD or another ref."""
        return self.refs.read_reflog(ref)

    # ------------------------------------------------------------------
    # branch
    # ------------------------------------------------------------------

    def branch(
        self,
        name:   Optional[str] = None,
        delete: bool = False,
    ) -> Optional[List[str]]:
        """
        Manage branches.

        branch()                  → return sorted list of branch names
        branch("feat")            → create branch at HEAD
        branch("feat", delete=True) → delete branch
        """
        if name is None:
            return self.refs.list_branches()

        if delete:
            current = self.refs.current_branch()
            if name == current:
                sha = self.refs.get_branch(name)
                replacement = next(
                    (
                        branch
                        for branch in self.refs.list_branches()
                        if branch != name and self.refs.get_branch(branch) == sha
                    ),
                    None,
                )
                if replacement:
                    self.refs.set_head_symbolic(replacement, message=f"branch: delete {name}")
                elif sha:
                    self.refs.set_head_detached(sha, message=f"branch: delete {name}")
            self.refs.delete_branch(name)
            return None

        head_sha = self.refs.resolve_head()
        if not head_sha:
            raise RuntimeError("Cannot create a branch on an empty repository.")
        self.refs.set_branch(name, head_sha, message=f"branch: created {name}")
        self.refs.set_head_symbolic(name, message=f"checkout: moving to {name}")
        return None

    # ------------------------------------------------------------------
    # tag
    # ------------------------------------------------------------------

    def tag(
        self,
        name:   Optional[str] = None,
        target: Optional[str] = None,
    ) -> Optional[List[str]]:
        """
        Manage lightweight tags.

        tag()               → return sorted list of tag names
        tag("v1.0")         → create tag at HEAD
        tag("v1.0", "sha")  → create tag at given commit
        """
        if name is None:
            return self.refs.list_tags()

        sha = self.refs.resolve(target or "HEAD")
        if not sha:
            raise KeyError(f"Unknown revision: '{target or 'HEAD'}'")
        self.refs.set_tag(name, sha)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_worktree_path(self, path: str) -> Path:
        """Resolve a user path against the repository root and reject escapes."""
        raw = Path(path)
        resolved = raw.resolve() if raw.is_absolute() else (self.worktree / raw).resolve()
        try:
            resolved.relative_to(self.worktree)
        except ValueError as exc:
            raise ValueError(f"Path is outside the repository: {path!r}") from exc
        return resolved

    def _normalize_pathspec(self, path: str) -> str:
        resolved = self._resolve_worktree_path(path)
        return resolved.relative_to(self.worktree).as_posix()

    def _should_track(self, path: Path, ignore: IgnoreMatcher) -> bool:
        rel = path.relative_to(self.worktree).as_posix()
        return (
            ".pygit" not in path.parts
            and (rel in self.index or not ignore.is_ignored(rel))
        )

    def _read_config(self) -> Dict:
        path = self.pygit_dir / "config.json"
        if not path.exists():
            return {"remotes": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_config(self, config: Dict) -> None:
        (self.pygit_dir / "config.json").write_text(
            json.dumps(config, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _read_native_map(self, remote: str = "origin") -> Dict[str, str]:
        path = self.pygit_dir / "native-map.json"
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw and all(isinstance(value, str) for value in raw.values()):
            return raw if remote == "origin" else {}
        return raw.get(remote, {})

    def _write_native_map(self, mapping: Dict[str, str], remote: str = "origin") -> None:
        path = self.pygit_dir / "native-map.json"
        raw: Dict[str, object] = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded and all(isinstance(value, str) for value in loaded.values()):
                raw["origin"] = loaded
            else:
                raw = loaded
        raw[remote] = mapping
        (self.pygit_dir / "native-map.json").write_text(
            json.dumps(raw, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _delete_native_map(self, remote: str) -> None:
        path = self.pygit_dir / "native-map.json"
        if not path.exists():
            return
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if loaded and all(isinstance(value, str) for value in loaded.values()):
            if remote == "origin":
                path.unlink()
            return
        loaded.pop(remote, None)
        if loaded:
            path.write_text(json.dumps(loaded, indent=2, sort_keys=True), encoding="utf-8")
        else:
            path.unlink()

    def _rename_native_map(self, old: str, new: str) -> None:
        path = self.pygit_dir / "native-map.json"
        if not path.exists():
            return
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if loaded and all(isinstance(value, str) for value in loaded.values()):
            if old == "origin":
                loaded = {new: loaded}
            else:
                return
        elif old in loaded:
            loaded[new] = loaded.pop(old)
        path.write_text(json.dumps(loaded, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _infer_default_branch(refs: Dict[str, str]) -> Optional[str]:
        head = refs.get("HEAD")
        for candidate in ("main", "master"):
            if refs.get(f"refs/heads/{candidate}") == head:
                return candidate
        for name, sha in sorted(refs.items()):
            if name.startswith("refs/heads/") and sha == head:
                return name[len("refs/heads/"):]
        return None

    @staticmethod
    def _advertised_import_refs(refs: Dict[str, str]) -> Dict[str, str]:
        return {
            ref_name: native_oid
            for ref_name, native_oid in refs.items()
            if (
                ref_name == "HEAD"
                or ref_name.startswith("refs/heads/")
                or (
                    ref_name.startswith("refs/tags/")
                    and not ref_name.endswith("^{}")
                )
            )
        }

    def _require_commit(self, sha: str) -> CommitObject:
        obj = self.store.read(sha)
        if not isinstance(obj, CommitObject):
            raise ValueError(f"'{sha}' does not point to a commit")
        return obj

    def _commit_tree_entries(self, sha: str) -> Dict[str, Tuple[str, str]]:
        return self._tree_entries(self._require_commit(sha).tree)

    def _tree_entries(self, tree_sha: str) -> Dict[str, Tuple[str, str]]:
        result: Dict[str, Tuple[str, str]] = {}
        self._flatten_tree_entries(tree_sha, "", result)
        return result

    def _flatten_tree_entries(
        self,
        tree_sha: str,
        prefix: str,
        out: Dict[str, Tuple[str, str]],
    ) -> None:
        tree = self.store.read(tree_sha)
        if not isinstance(tree, TreeObject):
            return
        for entry in tree.entries:
            path = entry.name if not prefix else f"{prefix}/{entry.name}"
            if entry.is_dir:
                self._flatten_tree_entries(entry.sha, path, out)
            else:
                out[path] = (entry.sha, entry.mode)

    def _entry_bytes(self, entry: Optional[Tuple[str, str]]) -> bytes:
        return self._blob_bytes(entry[0]) if entry else b""

    def _write_worktree_blob(self, path: str, sha: str, mode: str) -> None:
        blob = self.store.read(sha)
        if not isinstance(blob, BlobObject):
            raise ValueError(f"Object {sha} is not a blob")
        target = self.worktree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.data)
        if mode == "100755":
            target.chmod(
                target.stat().st_mode
                | stat_mod.S_IXUSR
                | stat_mod.S_IXGRP
                | stat_mod.S_IXOTH
            )

    def _remove_worktree_file(self, path: str) -> None:
        target = self.worktree / path
        if target.exists() and target.is_file():
            target.unlink()

    def _index_entry(self, path: str, sha: str, mode: str) -> IndexEntry:
        abs_path = self.worktree / path
        stat = abs_path.stat()
        return IndexEntry(
            path=path,
            sha=sha,
            mode=mode,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )

    def _replace_worktree_from_commit(
        self,
        sha: str,
        remove_paths: Optional[set] = None,
    ) -> None:
        commit = self._require_commit(sha)
        target_tree = self._tree_entries(commit.tree)
        paths_to_check = set(self.index.paths()) if remove_paths is None else remove_paths
        for path in paths_to_check - set(target_tree):
            self._remove_worktree_file(path)
        self._restore_tree(commit.tree, self.worktree)
        self.index.entries = {
            path: self._index_entry(path, blob_sha, mode)
            for path, (blob_sha, mode) in target_tree.items()
        }
        self.index.save()

    def _reset_index_to_commit(self, sha: str) -> None:
        commit = self._require_commit(sha)
        tree_entries = self._tree_entries(commit.tree)
        self.index.entries = {
            path: self._index_entry_for_blob(path, blob_sha, mode)
            for path, (blob_sha, mode) in tree_entries.items()
        }
        self.index.save()

    def _index_entry_for_blob(self, path: str, blob_sha: str, mode: str) -> IndexEntry:
        abs_path = self.worktree / path
        if abs_path.exists():
            stat = abs_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        else:
            blob = self.store.read(blob_sha)
            size = len(blob.serialize()) if isinstance(blob, BlobObject) else 0
            mtime = 0.0
        return IndexEntry(
            path=path,
            sha=blob_sha,
            mode=mode,
            size=size,
            mtime=mtime,
        )

    def _snapshot_worktree_entries(self) -> List[IndexEntry]:
        ignore = IgnoreMatcher(self.worktree)
        entries: List[IndexEntry] = []
        for file_path in sorted(self.worktree.rglob("*")):
            if not file_path.is_file() or ".pygit" in file_path.parts:
                continue
            rel = file_path.relative_to(self.worktree).as_posix()
            if rel not in self.index and ignore.is_ignored(rel):
                continue
            sha = self.store.write(BlobObject(file_path.read_bytes()))
            entries.append(self._index_entry(rel, sha, _mode_for(file_path)))
        return entries

    def _read_merge_head(self) -> Optional[str]:
        path = self.pygit_dir / "MERGE_HEAD"
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def _ensure_no_operation(self, requested: str) -> None:
        operation = self._operation_name()
        if operation:
            raise RuntimeError(
                f"Cannot start {requested}: a {operation} operation is already in progress."
            )

    def _ensure_clean_worktree(self, operation: str) -> None:
        state = self.status()
        if any(state[key] for key in ("staged", "unstaged", "untracked", "conflicts")):
            raise RuntimeError(
                f"Cannot {operation} with local changes in the working tree."
            )

    def _operation_name(self) -> Optional[str]:
        if self._read_merge_head():
            return "merge"
        if self._read_cherry_pick_state():
            return "cherry-pick"
        if self._read_rebase_state():
            return "rebase"
        if self._read_bisect_state():
            return "bisect"
        return None

    def _read_conflicts(self) -> List[str]:
        path = self.pygit_dir / "MERGE_CONFLICTS"
        if not path.exists():
            return []
        return sorted(json.loads(path.read_text(encoding="utf-8")))

    def _write_merge_state(
        self,
        merge_head: str,
        conflicts: List[str],
        original_head: Optional[str] = None,
    ) -> None:
        (self.pygit_dir / "MERGE_HEAD").write_text(merge_head, encoding="utf-8")
        if original_head:
            (self.pygit_dir / "MERGE_ORIG_HEAD").write_text(
                original_head,
                encoding="utf-8",
            )
        self._write_conflicts(conflicts)

    def _write_conflicts(self, conflicts: List[str]) -> None:
        (self.pygit_dir / "MERGE_CONFLICTS").write_text(
            json.dumps(sorted(conflicts), indent=2),
            encoding="utf-8",
        )

    def _clear_conflict(self, path: str) -> None:
        conflicts = self._read_conflicts()
        if path not in conflicts:
            return
        conflicts.remove(path)
        conflict_path = self.pygit_dir / "MERGE_CONFLICTS"
        if conflicts:
            conflict_path.write_text(json.dumps(conflicts, indent=2), encoding="utf-8")
        elif conflict_path.exists():
            conflict_path.unlink()

    def _clear_merge_state(self) -> None:
        for filename in ("MERGE_HEAD", "MERGE_ORIG_HEAD", "MERGE_CONFLICTS"):
            path = self.pygit_dir / filename
            if path.exists():
                path.unlink()

    def _read_merge_original_head(self) -> Optional[str]:
        path = self.pygit_dir / "MERGE_ORIG_HEAD"
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def _read_cherry_pick_state(self) -> Optional[Dict]:
        path = self.pygit_dir / "CHERRY_PICK_STATE"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_cherry_pick_state(
        self,
        source: str,
        head_before: str,
        target: str,
    ) -> None:
        (self.pygit_dir / "CHERRY_PICK_STATE").write_text(
            json.dumps(
                {
                    "source": source,
                    "head_before": head_before,
                    "target": target,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _read_rebase_state(self) -> Optional[Dict]:
        path = self.pygit_dir / "REBASE_STATE"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_rebase_state(self, state: Dict) -> None:
        (self.pygit_dir / "REBASE_STATE").write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _clear_rebase_state(self) -> None:
        path = self.pygit_dir / "REBASE_STATE"
        if path.exists():
            path.unlink()

    def _read_bisect_state(self) -> Optional[Dict]:
        path = self.pygit_dir / "BISECT_STATE"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_bisect_state(self, state: Dict) -> None:
        (self.pygit_dir / "BISECT_STATE").write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _clear_bisect_state(self) -> None:
        path = self.pygit_dir / "BISECT_STATE"
        if path.exists():
            path.unlink()

    def _clear_cherry_pick_state(self) -> None:
        path = self.pygit_dir / "CHERRY_PICK_STATE"
        if path.exists():
            path.unlink()

    def _clear_conflicts(self) -> None:
        path = self.pygit_dir / "MERGE_CONFLICTS"
        if path.exists():
            path.unlink()

    def _head_tree_flat(self) -> Dict[str, str]:
        """Return a path→blob_sha dict for the current HEAD tree (empty if no commits)."""
        result: Dict[str, str] = {}
        head_sha = self.refs.resolve_head()
        if head_sha:
            obj = self.store.read(head_sha)
            if isinstance(obj, CommitObject):
                self._flatten_tree(obj.tree, "", result)
        return result

    def _flatten_tree(
        self, tree_sha: str, prefix: str, out: Dict[str, str]
    ) -> None:
        """Recursively map every blob in *tree_sha* to its POSIX path."""
        tree = self.store.read(tree_sha)
        if not isinstance(tree, TreeObject):
            return
        for entry in tree.entries:
            path = entry.name if not prefix else f"{prefix}/{entry.name}"
            if entry.is_dir:
                self._flatten_tree(entry.sha, path, out)
            else:
                out[path] = entry.sha

    def _blob_bytes(self, sha: Optional[str]) -> bytes:
        """Return the raw content of a blob SHA, or b"" if sha is None."""
        if not sha:
            return b""
        obj = self.store.read(sha)
        return obj.serialize() if isinstance(obj, BlobObject) else b""
