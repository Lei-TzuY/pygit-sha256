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

import difflib
import json
import stat as stat_mod
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .diff import diff_stat, format_stat_block, unified_diff
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
    def clone(
        cls,
        url: str,
        path: Optional[str] = None,
        depth: Optional[int] = None,
        branch_name: Optional[str] = None,
        single_branch: bool = False,
    ) -> "Repository":
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
        target_br = branch_name or str(result["default_branch"] or "main")
        sha = repo.refs.get_remote("origin", target_br)
        if not sha:
            raise RuntimeError(f"Remote did not provide branch '{target_br}'.")

        if single_branch:
            # Remove tracking refs for other remote branches
            for remote_b in repo.list_remote_branches():
                if not remote_b.endswith(f"/{target_br}"):
                    rel_p = remote_b.replace("remotes/", "")
                    ref_f = repo.pygit_dir / "refs" / "remotes" / rel_p.split("/", 1)[1]
                    if ref_f.exists():
                        ref_f.unlink()

        repo.refs.set_branch(target_br, sha, message=f"clone: from {url}")
        repo.refs.set_head_symbolic(target_br, message=f"clone: from {url}")
        repo._replace_worktree_from_commit(sha)

        if depth and depth > 0:
            # Trace commit depth to find boundary commit(s)
            curr = sha
            d = 1
            shallow_boundary: Optional[str] = None
            while curr and d < depth:
                c = repo.store.read(curr)
                if isinstance(c, CommitObject) and c.parents:
                    curr = c.parents[0]
                    d += 1
                else:
                    break
            if curr:
                shallow_boundary = curr
                shallow_file = repo.pygit_dir / "shallow"
                shallow_file.write_text(f"{shallow_boundary}\n", encoding="utf-8")

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

        # Run pre-push hook
        from .hooks import HookRunner
        push_hook_runner = HookRunner(self.pygit_dir)
        code, out, err = push_hook_runner.run_hook("pre-push", [remote, str(settings["url"])])
        if code != 0:
            raise RuntimeError(f"pre-push hook failed with exit code {code}:\n{err or out}")

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
        from .lfs import LFSEngine
        from .eol import EOLNormalizer
        rel = abs_path.relative_to(self.worktree).as_posix()
        lfs = LFSEngine(self.pygit_dir, self.worktree)
        eol = EOLNormalizer(self.pygit_dir, self.worktree)
        data = abs_path.read_bytes()

        if lfs.should_use_lfs(rel):
            pointer_text, oid, size = lfs.create_pointer(data)
            data = pointer_text.encode("utf-8")
        elif eol.should_normalize(rel):
            data = eol.normalize_to_repo(data)

        sha = self.store.write(BlobObject(data))
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
        message: str = "",
        author_name: str = "Unknown",
        author_email: str = "unknown@example.com",
        parents: Optional[List[str]] = None,
        committer_name: Optional[str] = None,
        committer_email: Optional[str] = None,
        allow_rebase: bool = False,
        amend: bool = False,
        template: Optional[str] = None,
        author: Optional[str] = None,
        fixup: Optional[str] = None,
        squash: Optional[str] = None,
        only_paths: Optional[List[str]] = None,
        include_paths: Optional[List[str]] = None,
        allow_empty: bool = False,
        cleanup: str = "strip",
        reuse_message: Optional[str] = None,
        reedit_message: Optional[str] = None,
        commit_date: Optional[str] = None,
        reset_author: bool = False,
        signoff: bool = False,
    ) -> str:
        """
        Create a commit from the current index.

        Returns the new commit's SHA-256 hex string.
        """
        if reuse_message or reedit_message:
            target_ref = reuse_message or reedit_message
            target_sha = self.rev_parse(target_ref)
            target_commit = self._require_commit(target_sha)
            message = target_commit.message

        if cleanup == "strip":
            clean_lines = [l for l in message.splitlines() if not l.strip().startswith("#")]
            res_lines = []
            for l in clean_lines:
                if not l.strip() and res_lines and not res_lines[-1].strip():
                    continue
                res_lines.append(l)
            message = "\n".join(res_lines).strip()
        elif cleanup == "whitespace":
            clean_lines = [l.rstrip() for l in message.splitlines()]
            message = "\n".join(clean_lines).strip()

        if include_paths:
            self.add(include_paths)

        if only_paths:
            # Commit only specified paths (temporarily isolate index)
            saved_entries = dict(self.index.entries)
            head_sha = self.refs.resolve_head()
            if head_sha:
                head_tree = self._commit_tree_entries(head_sha)
                self.index.entries = {
                    p: self._index_entry_for_blob(p, sha, mode)
                    for p, (sha, mode) in head_tree.items()
                }
            else:
                self.index.entries.clear()
            self.add(only_paths)
            res_sha = self.commit(
                message=message,
                author_name=author_name,
                author_email=author_email,
                parents=parents,
                committer_name=committer_name,
                committer_email=committer_email,
                allow_rebase=allow_rebase,
                amend=amend,
                template=template,
                author=author,
                fixup=fixup,
                squash=squash,
                allow_empty=allow_empty,
            )
            # Restore other staged entries
            for p, entry in saved_entries.items():
                if p not in only_paths:
                    self.index.entries[p] = entry
            self.index.save()
            return res_sha
        if fixup:
            target_sha = self.rev_parse(fixup)
            target_commit = self._require_commit(target_sha)
            target_subj = target_commit.message.splitlines()[0]
            message = f"fixup! {target_subj}\n\n{message}".strip()
        elif squash:
            target_sha = self.rev_parse(squash)
            target_commit = self._require_commit(target_sha)
            target_subj = target_commit.message.splitlines()[0]
            message = f"squash! {target_subj}\n\n{message}".strip()

        if author and "<" in author and ">" in author:
            parts = author.split("<")
            author_name = parts[0].strip()
            author_email = parts[1].rstrip(">").strip()

        if template:
            tmpl_path = Path(template)
            if tmpl_path.exists():
                tmpl_text = tmpl_path.read_text(encoding="utf-8")
                message = f"{tmpl_text.strip()}\n\n{message}".strip()
        elif not message and (self.worktree / ".pygitmessage").exists():
            tmpl_text = (self.worktree / ".pygitmessage").read_text(encoding="utf-8")
            message = tmpl_text.strip()

        if not message:
            raise ValueError("Commit message cannot be empty.")

        if signoff:
            sob_line = f"Signed-off-by: {author_name} <{author_email}>"
            if sob_line not in message:
                message = f"{message.strip()}\n\n{sob_line}\n"

        if self._read_rebase_state() and not allow_rebase:
            raise RuntimeError("A rebase is in progress; use 'pygit rebase --continue'.")

        conflicts = self._read_conflicts()
        if conflicts:
            raise RuntimeError(
                "Cannot commit with unresolved conflicts: " + ", ".join(conflicts)
            )

        # Run pre-commit hook
        from .hooks import HookRunner
        hook_runner = HookRunner(self.pygit_dir)
        code, out, err = hook_runner.run_hook("pre-commit")
        if code != 0:
            raise RuntimeError(f"pre-commit hook failed with exit code {code}:\n{err or out}")

        tree_sha = self._build_tree()
        head_sha = self.refs.resolve_head()
        merge_head = self._read_merge_head()

        if amend:
            if not head_sha:
                raise RuntimeError("Cannot amend a commit in an empty repository.")
            head_commit = self._require_commit(head_sha)
            if parents is None:
                parents = list(head_commit.parents)
            if not message:
                message = head_commit.message
        elif parents is None:
            parents = [head_sha] if head_sha else []
            if merge_head and merge_head not in parents:
                parents.append(merge_head)

        if not message:
            raise RuntimeError("Commit message cannot be empty.")

        if not head_sha and not self.index.entries and not allow_empty:
            raise RuntimeError("Nothing to commit (index is empty).")
        if head_sha and not merge_head and not amend and not allow_empty:
            head_obj = self.store.read(head_sha)
            if isinstance(head_obj, CommitObject) and head_obj.tree == tree_sha:
                raise RuntimeError("Nothing to commit (working tree clean).")

        if amend and not reset_author and author_name == "Unknown" and head_sha:
            head_commit = self._require_commit(head_sha)
            author = head_commit.author
        else:
            author = Identity(author_name, author_email)
        committer = Identity(
            committer_name or author_name,
            committer_email or author_email,
        )
        if commit_date:
            try:
                import datetime
                if commit_date.isdigit():
                    t_val = int(commit_date)
                else:
                    dt = datetime.datetime.fromisoformat(commit_date)
                    t_val = int(dt.timestamp())
                author.timestamp = t_val
                committer.timestamp = t_val
            except Exception:
                pass

        # Run commit-msg hook (can modify the message via a temp file)
        msg_file = self.pygit_dir / "COMMIT_EDITMSG"
        msg_file.write_text(message, encoding="utf-8")
        code, out, err = hook_runner.run_hook("commit-msg", [str(msg_file)])
        if code != 0:
            msg_file.unlink(missing_ok=True)
            raise RuntimeError(f"commit-msg hook failed with exit code {code}:\n{err or out}")
        message = msg_file.read_text(encoding="utf-8")
        msg_file.unlink(missing_ok=True)

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

    def _build_tree_from_entries_dict(
        self, entries_dict: Dict[str, Tuple[str, str]]
    ) -> str:
        """Build tree objects from a dictionary of path -> (blob_sha, mode)."""
        idx_entries = [
            IndexEntry(path=p, sha=s, mode=m, size=0, mtime=0.0)
            for p, (s, m) in entries_dict.items()
        ]
        return self._build_tree_from_entries(idx_entries)

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
        all_branches: bool = False,
        author: Optional[str] = None,
        grep: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        patch: bool = False,
        follow: Optional[str] = None,
        topo_order: bool = False,
        merges_only: Optional[bool] = None,
        line_range: Optional[Tuple[int, int, str]] = None,
        first_parent: bool = False,
        min_parents: Optional[int] = None,
        max_parents: Optional[int] = None,
    ) -> List[Tuple[str, CommitObject]]:
        """
        Walk commit history from *start* (defaults to HEAD) via BFS.

        Parameters
        ----------
        start       : starting SHA or ref (defaults to HEAD)
        max_count   : stop after this many commits (0 = unlimited)
        all_branches: if True, seed the walk from all local branch tips
        author      : filter: only include commits whose author name or email
                      contains this substring (case-insensitive)
        grep        : filter: only include commits whose message contains this
                      substring (case-insensitive)
        since       : timestamp or ISO date string (YYYY-MM-DD) lower bound
        until       : timestamp or ISO date string (YYYY-MM-DD) upper bound
        patch       : attach diff patch vs parent to CommitObject payload if requested
        follow      : trace history of a file across renames
        topo_order  : sort commits topologically (children before parents)

        Returns a list of ``(sha, CommitObject)`` pairs, newest first.
        """
        import re as _re
        from datetime import datetime

        # Check shallow boundaries
        shallow_file = self.pygit_dir / "shallow"
        shallow_shas: set = set()
        if shallow_file.exists():
            shallow_shas = {
                line.strip()
                for line in shallow_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }

        seeds: List[str] = []
        if all_branches:
            for branch in self.refs.list_branches():
                branch_sha = self.refs.get_branch(branch)
                if branch_sha:
                    seeds.append(branch_sha)
            if not seeds:
                head_sha = self.refs.resolve_head()
                if head_sha:
                    seeds.append(head_sha)
        else:
            sha = self._resolve_revision(start) if start else self.refs.resolve_head()
            if sha:
                seeds.append(sha)

        if not seeds:
            return []

        # Parse date filters
        def _parse_time(t_str: Optional[str]) -> Optional[float]:
            if not t_str:
                return None
            try:
                return float(t_str)
            except ValueError:
                try:
                    dt = datetime.fromisoformat(t_str)
                    return dt.timestamp()
                except ValueError:
                    return None

        since_ts = _parse_time(since)
        until_ts = _parse_time(until)

        result: List[Tuple[str, CommitObject]] = []
        seen: set = set()
        queue = list(seeds)

        # Compile optional filters
        author_re = _re.compile(_re.escape(author), _re.IGNORECASE) if author else None
        grep_re   = _re.compile(_re.escape(grep),   _re.IGNORECASE) if grep   else None

        current_follow_path = follow

        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)

            obj = self.store.read(current)
            if not isinstance(obj, CommitObject):
                continue

            # Date filtering
            if since_ts and obj.author.timestamp < since_ts:
                if current not in shallow_shas:
                    queue.extend(obj.parents)
                continue
            if until_ts and obj.author.timestamp > until_ts:
                if current not in shallow_shas:
                    queue.extend(obj.parents)
                continue

            # Apply filters
            if author_re:
                identity = f"{obj.author.name} <{obj.author.email}>"
                if not author_re.search(identity):
                    if current not in shallow_shas:
                        queue.extend(obj.parents)
                    continue
            if grep_re and not grep_re.search(obj.message):
                if current not in shallow_shas:
                    queue.extend(obj.parents)
                continue

            # Merge filtering
            if min_parents is not None and len(obj.parents) < min_parents:
                if current not in shallow_shas:
                    queue.extend(obj.parents)
                continue
            if max_parents is not None and len(obj.parents) > max_parents:
                if current not in shallow_shas:
                    queue.extend(obj.parents)
                continue
            if merges_only is True and len(obj.parents) < 2:
                if current not in shallow_shas:
                    queue.extend(obj.parents)
                continue
            if merges_only is False and len(obj.parents) >= 2:
                if current not in shallow_shas:
                    queue.extend(obj.parents)
                continue

            # Line range filtering (-L start,end:file)
            if line_range:
                l_start, l_end, l_file = line_range
                curr_tree = self._commit_tree_entries(current)
                if l_file not in curr_tree:
                    if current not in shallow_shas:
                        queue.extend(obj.parents)
                    continue
                curr_lines = self._blob_bytes(curr_tree[l_file][0]).decode("utf-8", errors="replace").splitlines()
                curr_slice = curr_lines[max(0, l_start - 1):l_end]
                parent_slice: List[str] = []
                if obj.parents:
                    p_tree = self._commit_tree_entries(obj.parents[0])
                    if l_file in p_tree:
                        p_lines = self._blob_bytes(p_tree[l_file][0]).decode("utf-8", errors="replace").splitlines()
                        parent_slice = p_lines[max(0, l_start - 1):l_end]
                if curr_slice == parent_slice:
                    if current not in shallow_shas:
                        queue.extend(obj.parents)
                    continue

            # File follow (rename tracking)
            if current_follow_path:
                tree_flat: Dict[str, str] = {}
                self._flatten_tree(obj.tree, "", tree_flat)
                if current_follow_path not in tree_flat:
                    # File not at current path -- try finding by blob sha in parent
                    if obj.parents:
                        parent_tree: Dict[str, str] = {}
                        p_obj = self.store.read(obj.parents[0])
                        if isinstance(p_obj, CommitObject):
                            self._flatten_tree(p_obj.tree, "", parent_tree)
                            # If blob exists under a different path in parent, update follow path
                            target_blob_sha = tree_flat.get(current_follow_path)
                            if target_blob_sha:
                                for p_path, p_sha in parent_tree.items():
                                    if p_sha == target_blob_sha:
                                        current_follow_path = p_path
                                        break

            result.append((current, obj))
            if max_count and not topo_order and len(result) >= max_count:
                break
            if current not in shallow_shas:
                if first_parent and obj.parents:
                    queue.append(obj.parents[0])
                else:
                    queue.extend(obj.parents)

        if topo_order and len(result) > 1:
            res_shas = [sha for sha, _ in result]
            res_map = {sha: obj for sha, obj in result}
            child_count = {sha: 0 for sha in res_shas}
            for sha, obj in result:
                for p in obj.parents:
                    if p in child_count:
                        child_count[p] += 1
            sorted_res = []
            ready = [sha for sha in res_shas if child_count[sha] == 0]
            while ready:
                curr_sha = ready.pop(0)
                sorted_res.append((curr_sha, res_map[curr_sha]))
                c_obj = res_map[curr_sha]
                for p in c_obj.parents:
                    if p in child_count:
                        child_count[p] -= 1
                        if child_count[p] == 0:
                            ready.append(p)
            if len(sorted_res) == len(result):
                result = sorted_res

        if max_count and len(result) > max_count:
            result = result[:max_count]

        return result

    @staticmethod
    def format_log_graph(entries: List[Tuple[str, "CommitObject"]]) -> str:
        """
        Render a very simple left-spine ASCII graph for *entries* (the output
        of :meth:`log`).  Only the leftmost parent line is drawn; merge commits
        show a ``*`` with a fork character.

        Example output::

            * a1b2c3d  third commit
            * 9f8e7d6  second commit
            * 1234567  first commit
        """
        lines: List[str] = []
        for sha, commit in entries:
            is_merge = len(commit.parents) > 1
            prefix = "*" if not is_merge else "M"
            msg = commit.message.splitlines()[0]
            lines.append(f"{prefix} {sha[:12]}  {msg}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self, ignored: bool = False) -> Dict:
        """
        Return a dict describing the working-tree state::

            {
              "branch"   : str | None,      # current branch or None (detached)
              "staged"   : [(kind, path)],  # index vs HEAD
              "unstaged" : [(kind, path)],  # working tree vs index
              "untracked": [path],          # files not in the index
              "ignored"  : [path],          # ignored files (if requested)
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

        # --- untracked / ignored ---
        untracked: List[str] = []
        ignored_files: List[str] = []
        ignore = IgnoreMatcher(self.worktree)
        for f in sorted(self.worktree.rglob("*")):
            if not f.is_file():
                continue
            if ".pygit" in f.parts:
                continue
            rel = f.relative_to(self.worktree).as_posix()
            if rel not in self.index:
                if ignore.is_ignored(rel):
                    if ignored:
                        ignored_files.append(rel)
                else:
                    untracked.append(rel)

        curr_branch = self.refs.current_branch()
        upstream_info = None
        if curr_branch:
            try:
                ahead, behind = self.ahead_behind("HEAD", f"origin/{curr_branch}")
                upstream_info = {"upstream": f"origin/{curr_branch}", "ahead": ahead, "behind": behind}
            except Exception:
                pass

        res = {
            "branch":    curr_branch,
            "upstream":  upstream_info,
            "staged":    staged,
            "unstaged":  unstaged,
            "untracked": untracked,
            "conflicts": self._read_conflicts(),
            "operation": self._operation_name(),
        }
        if ignored:
            res["ignored"] = ignored_files
        return res

    # ------------------------------------------------------------------
    # diff
    # ------------------------------------------------------------------

    def diff(
        self,
        cached: bool = False,
        stat: bool = False,
        from_ref: Optional[str] = None,
        to_ref: Optional[str] = None,
        name_status: bool = False,
        name_only: bool = False,
        ignore_all_space: bool = False,
        ignore_space_change: bool = False,
        ignore_matching_lines: Optional[str] = None,
        stat_width: Optional[int] = None,
        compact_summary: bool = False,
        raw: bool = False,
        src_prefix: str = "a/",
        dst_prefix: str = "b/",
        no_prefix: bool = False,
        ignore_submodules: bool = False,
        find_renames: bool = False,
        find_copies: bool = False,
        submodule: Optional[str] = None,
        dirstat: bool = False,
        stat_graph_width: Optional[int] = None,
    ) -> str:
        """
        Produce a unified-diff string.

        Modes
        -----
        (no args)              : working tree vs index
        cached=True            : index vs HEAD  (``git diff --cached``)
        from_ref=A             : A vs working tree
        from_ref=A, to_ref=B   : A vs B  (both are commit/branch/tag refs)

        If *stat* is True, prepend a ``--stat``-style summary.
        If *name_status* is True, output status code and path (e.g. M path).
        If *name_only* is True, output file paths only.
        If *ignore_all_space* (-w) is True, ignore all whitespace.
        If *ignore_space_change* (-b) is True, ignore changes in amount of whitespace.
        """
        if from_ref is not None:
            from_sha = self._resolve_revision(from_ref)
            from_tree = self._commit_tree_entries(from_sha)
            if to_ref is not None:
                to_sha  = self._resolve_revision(to_ref)
                to_tree = self._commit_tree_entries(to_sha)
            else:
                # A vs working tree
                to_tree = {
                    e.path: (e.sha, e.mode) for e in self.index.all_entries()
                }
            return self._render_diff(
                from_tree, to_tree, stat=stat, worktree=to_ref is None,
                name_status=name_status, name_only=name_only,
                ignore_all_space=ignore_all_space, ignore_space_change=ignore_space_change,
                ignore_matching_lines=ignore_matching_lines,
            )

        if no_prefix:
            src_prefix = ""
            dst_prefix = ""

        parts: List[str] = []
        stat_entries: List[Tuple[str, int, int]] = []
        ns_lines: List[str] = []
        no_lines: List[str] = []
        cs_lines: List[str] = []
        raw_lines: List[str] = []

        def _bytes_same(b1: bytes, b2: bytes) -> bool:
            if ignore_all_space:
                return b1.translate(None, b" \t\r\n") == b2.translate(None, b" \t\r\n")
            if ignore_space_change:
                return b" ".join(b1.split()) == b" ".join(b2.split())
            return b1 == b2

        if cached:
            head_tree = self._head_tree_flat()
            for path in sorted(set(head_tree) | set(self.index.paths())):
                old_sha = head_tree.get(path)
                old_bytes = self._blob_bytes(old_sha)
                entry = self.index.get(path)
                new_sha = entry.sha if entry else None
                new_bytes = self._blob_bytes(new_sha)
                if not _bytes_same(old_bytes, new_bytes):
                    status_code = "A" if not old_sha else ("D" if not new_sha else "M")
                    ns_lines.append(f"{status_code}\t{path}")
                    no_lines.append(path)
                    o_s = old_sha or "0" * 40
                    n_s = new_sha or "0" * 40
                    raw_lines.append(f":100644 100644 {o_s[:40]} {n_s[:40]} {status_code}\t{path}")
                    if not old_sha:
                        cs_lines.append(f" create mode 100644 {path}")
                    elif not new_sha:
                        cs_lines.append(f" delete mode 100644 {path}")
                    else:
                        cs_lines.append(f" {path}")
                    if stat:
                        _, ins, dels = diff_stat(old_bytes, new_bytes, path)
                        stat_entries.append((path, ins, dels))
                    parts.append(
                        f"diff --pygit {src_prefix}{path} {dst_prefix}{path}\n"
                        + unified_diff(old_bytes, new_bytes, f"{src_prefix}{path}", f"{dst_prefix}{path}")
                    )
        else:
            for entry in self.index.all_entries():
                old_bytes = self._blob_bytes(entry.sha)
                abs_path  = self.worktree / entry.path
                new_bytes = abs_path.read_bytes() if abs_path.exists() else b""
                if not _bytes_same(old_bytes, new_bytes):
                    status_code = "D" if not abs_path.exists() else "M"
                    ns_lines.append(f"{status_code}\t{entry.path}")
                    no_lines.append(entry.path)
                    o_s = entry.sha or "0" * 40
                    raw_lines.append(f":100644 100644 {o_s[:40]} 0000000000000000000000000000000000000000 {status_code}\t{entry.path}")
                    if not abs_path.exists():
                        cs_lines.append(f" delete mode 100644 {entry.path}")
                    else:
                        cs_lines.append(f" {entry.path}")
                    if stat:
                        _, ins, dels = diff_stat(old_bytes, new_bytes, entry.path)
                        stat_entries.append((entry.path, ins, dels))
                    parts.append(
                        f"diff --pygit {src_prefix}{entry.path} {dst_prefix}{entry.path}\n"
                        + unified_diff(
                            old_bytes, new_bytes,
                            f"{src_prefix}{entry.path}", f"{dst_prefix}{entry.path}",
                        )
                    )

        if name_status:
            return "\n".join(ns_lines) + ("\n" if ns_lines else "")
        if name_only:
            return "\n".join(no_lines) + ("\n" if no_lines else "")
        if compact_summary:
            return "\n".join(cs_lines) + ("\n" if cs_lines else "")
        if raw:
            return "\n".join(raw_lines) + ("\n" if raw_lines else "")

        if ignore_matching_lines:
            import re
            pat = re.compile(ignore_matching_lines)
            filtered_parts = []
            for p in parts:
                lines = p.splitlines(keepends=True)
                out_lines = []
                for line in lines:
                    if (line.startswith("+") or line.startswith("-")) and not line.startswith("+++") and not line.startswith("---"):
                        if pat.search(line[1:]):
                            continue
                    out_lines.append(line)
                filtered_parts.append("".join(out_lines))
            parts = filtered_parts

        body = "".join(parts)
        if stat and stat_entries:
            return format_stat_block(stat_entries) + "\n" + body
        return body

    def _render_diff(
        self,
        from_tree: Dict[str, Tuple[str, str]],
        to_tree: Dict[str, Tuple[str, str]],
        stat: bool = False,
        worktree: bool = False,
        name_status: bool = False,
        name_only: bool = False,
        ignore_all_space: bool = False,
        ignore_space_change: bool = False,
        ignore_matching_lines: Optional[str] = None,
        compact_summary: bool = False,
    ) -> str:
        """Render a diff between two flat tree dicts (path→(sha, mode))."""
        parts: List[str] = []
        stat_entries: List[Tuple[str, int, int]] = []
        ns_lines: List[str] = []
        no_lines: List[str] = []
        cs_lines: List[str] = []
        all_paths = sorted(set(from_tree) | set(to_tree))

        def _bytes_same(b1: bytes, b2: bytes) -> bool:
            if ignore_all_space:
                return b1.translate(None, b" \t\r\n") == b2.translate(None, b" \t\r\n")
            if ignore_space_change:
                return b" ".join(b1.split()) == b" ".join(b2.split())
            return b1 == b2

        for path in all_paths:
            old_entry = from_tree.get(path)
            new_entry = to_tree.get(path)
            old_bytes = self._blob_bytes(old_entry[0] if old_entry else None)
            if worktree and new_entry:
                abs_path = self.worktree / path
                new_bytes = abs_path.read_bytes() if abs_path.exists() else b""
            else:
                new_bytes = self._blob_bytes(new_entry[0] if new_entry else None)
            if _bytes_same(old_bytes, new_bytes):
                continue

            status_code = "A" if not old_entry else ("D" if not new_entry else "M")
            ns_lines.append(f"{status_code}\t{path}")
            no_lines.append(path)
            if not old_entry:
                cs_lines.append(f" create mode 100644 {path}")
            elif not new_entry:
                cs_lines.append(f" delete mode 100644 {path}")
            else:
                cs_lines.append(f" {path}")

            if stat:
                _, ins, dels = diff_stat(old_bytes, new_bytes, path)
                stat_entries.append((path, ins, dels))
            parts.append(
                f"diff --pygit a/{path} b/{path}\n"
                + unified_diff(old_bytes, new_bytes, f"a/{path}", f"b/{path}")
            )

        if name_status:
            return "\n".join(ns_lines) + ("\n" if ns_lines else "")
        if name_only:
            return "\n".join(no_lines) + ("\n" if no_lines else "")
        if compact_summary:
            return "\n".join(cs_lines) + ("\n" if cs_lines else "")

        if ignore_matching_lines:
            import re
            pat = re.compile(ignore_matching_lines)
            filtered_parts = []
            for p in parts:
                lines = p.splitlines(keepends=True)
                out_lines = []
                for line in lines:
                    if (line.startswith("+") or line.startswith("-")) and not line.startswith("+++") and not line.startswith("---"):
                        if pat.search(line[1:]):
                            continue
                    out_lines.append(line)
                filtered_parts.append("".join(out_lines))
            parts = filtered_parts

        body = "".join(parts)
        if stat and stat_entries:
            return format_stat_block(stat_entries) + "\n" + body
        return body

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------

    def show(self, target: str = "HEAD", stat: bool = False) -> str:
        """
        Show the commit metadata and the diff it introduces.

        Equivalent to ``git show <target>``.
        """
        sha = self._resolve_revision(target)
        commit = self._require_commit(sha)
        header = commit.pretty_print(sha) + "\n"

        parent_tree: Dict[str, Tuple[str, str]] = {}
        if commit.parents:
            parent_tree = self._commit_tree_entries(commit.parents[0])
        commit_tree = self._commit_tree_entries(sha)

        diff_body = self._render_diff(parent_tree, commit_tree, stat=stat)
        return header + diff_body

    # ------------------------------------------------------------------
    # ls-files
    # ------------------------------------------------------------------

    def ls_files(self, stage: bool = False) -> List[str]:
        """
        List paths tracked in the index.

        Parameters
        ----------
        stage : if True, prefix each line with ``<mode> <sha> <path>``
                (like ``git ls-files --stage``).

        Returns a sorted list of strings.
        """
        lines: List[str] = []
        for entry in self.index.all_entries():
            if stage:
                lines.append(f"{entry.mode} {entry.sha}\t{entry.path}")
            else:
                lines.append(entry.path)
        return lines

    # ------------------------------------------------------------------
    # blame
    # ------------------------------------------------------------------

    def blame(
        self, path: str, line_range: Optional[Tuple[int, int]] = None
    ) -> List[str]:
        """
        Annotate each line of *path* with the commit SHA and author that
        last changed it.

        If *line_range* is (start, end) 1-based inclusive, only return lines in that range.
        """
        import datetime as _dt

        rel = self._normalize_pathspec(path)
        commits = self.log()  # newest first
        if not commits:
            raise RuntimeError("No commits found.")

        # Load the working version of the file for display
        abs_path = self.worktree / rel
        if not abs_path.exists():
            raise FileNotFoundError(f"'{path}' not found in the working tree.")
        current_lines = abs_path.read_bytes().decode(errors="replace").splitlines()
        n = len(current_lines)

        # attribution[i] = (sha, commit_obj)  or None
        attribution: List[Optional[Tuple[str, CommitObject]]] = [None] * n

        def _file_lines(sha: str) -> Optional[List[str]]:
            commit_tree = self._commit_tree_entries(sha)
            entry = commit_tree.get(rel)
            if entry is None:
                return None
            return self._blob_bytes(entry[0]).decode(errors="replace").splitlines()

        for sha, commit_obj in commits:
            new_lines = _file_lines(sha)
            parent_lines: List[str] = []
            if commit_obj.parents:
                pl = _file_lines(commit_obj.parents[0])
                parent_lines = pl if pl is not None else []

            if new_lines is None:
                continue

            # Find which lines changed by comparing new_lines vs parent_lines
            matcher = difflib.SequenceMatcher(
                None, parent_lines, new_lines, autojunk=False
            )
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag in ("replace", "insert"):
                    # Lines j1..j2 in new_lines are new in this commit.
                    # Map them back to attribution[] indices.
                    for j in range(j1, j2):
                        # j maps directly to the line in current content
                        if j < n and attribution[j] is None:
                            attribution[j] = (sha, commit_obj)

        # Fallback: unattributed lines go to the oldest commit
        fallback = commits[-1]
        for i in range(n):
            if attribution[i] is None:
                attribution[i] = fallback

        result: List[str] = []
        for i, (sha_tuple) in enumerate(attribution):
            if sha_tuple is None:
                short_sha, author_str, date_str = "?" * 12, "unknown", "?"
            else:
                c_sha, c_obj = sha_tuple
                short_sha = c_sha[:12]
                author_str = f"{c_obj.author.name} <{c_obj.author.email}>"
                dt = _dt.datetime.fromtimestamp(
                    c_obj.author.timestamp, tz=_dt.timezone.utc
                )
                date_str = dt.strftime("%Y-%m-%d")
            line_content = current_lines[i] if i < len(current_lines) else ""
            result.append(
                f"{short_sha}  ({author_str} {date_str})  {line_content}"
            )
        
        if line_range:
            start_l, end_l = line_range
            start_idx = max(0, start_l - 1)
            end_idx = min(len(result), end_l)
            return result[start_idx:end_idx]

        return result

    # ------------------------------------------------------------------
    # checkout
    # ------------------------------------------------------------------

    def checkout(self, target: str, orphan: bool = False) -> None:
        """
        Restore the working tree and index to *target* (branch, tag, or SHA).

        If *orphan* is True, creates a new orphan branch with no history.
        Files tracked in the current index but absent in *target* are deleted.
        Untracked files are left untouched.
        """
        if orphan:
            self.refs.set_head_symbolic(target, message=f"checkout: orphan {target}")
            return

        sha = self.refs.resolve(target)
        if not sha:
            raise KeyError(f"Unknown revision: '{target}'")

        obj = self.store.read(sha)
        if not isinstance(obj, CommitObject):
            raise ValueError(f"'{target}' does not point to a commit")

        new_tree: Dict[str, str] = {}
        self._flatten_tree(obj.tree, "", new_tree)

        # Apply sparse checkout filter if enabled
        from .sparse import SparseCheckout
        sparse = SparseCheckout(self.pygit_dir)

        # Remove tracked files that disappear in the new tree or fail sparse rules
        for path in set(self.index.paths()):
            if path not in new_tree or not sparse.matches(path):
                abs_path = self.worktree / path
                if abs_path.exists():
                    abs_path.unlink()

        # Write the new tree to disk (respecting sparse checkout)
        self._restore_tree_sparse(obj.tree, self.worktree, "", sparse)

        # Rebuild index from the new tree
        self.index.entries.clear()
        for path, blob_sha in new_tree.items():
            if not sparse.matches(path):
                continue
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
        old_sha = self.refs.resolve_head() or "0" * 64
        if self.refs.get_branch(target):
            self.refs.set_head_symbolic(target, message=f"checkout: moving to {target}")
        else:
            self.refs.set_head_detached(sha, message=f"checkout: moving to {target}")

        # Run post-checkout hook
        from .hooks import HookRunner
        checkout_hook_runner = HookRunner(self.pygit_dir)
        branch_flag = "1" if self.refs.get_branch(target) else "0"
        checkout_hook_runner.run_hook("post-checkout", [old_sha, sha, branch_flag])

    def _restore_tree(self, tree_sha: str, base_dir: Path) -> None:
        """Recursively write all blobs in *tree_sha* under *base_dir*."""
        from .sparse import SparseCheckout
        self._restore_tree_sparse(tree_sha, base_dir, "", SparseCheckout(self.pygit_dir))

    def _restore_tree_sparse(
        self, tree_sha: str, base_dir: Path, prefix: str, sparse: "SparseCheckout"
    ) -> None:
        """Recursively write all blobs in *tree_sha* under *base_dir* checking sparse rules."""
        tree = self.store.read(tree_sha)
        if not isinstance(tree, TreeObject):
            return
        for entry in tree.entries:
            path = entry.name if not prefix else f"{prefix}/{entry.name}"
            target = base_dir / entry.name
            if entry.is_dir:
                self._restore_tree_sparse(entry.sha, target, path, sparse)
            else:
                if not sparse.matches(path):
                    continue
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
        squash: bool = False,
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
        if base == ours and not squash:
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

        if squash:
            return {"status": "squashed", "sha": None, "conflicts": []}

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
        """Apply a three-way tree merge with line-level diff3 auto-merging."""
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
                # Both sides modified — attempt line-level diff3 merge
                base_bytes = self._entry_bytes(base_entry)
                our_bytes = self._entry_bytes(our_entry)
                their_bytes = self._entry_bytes(their_entry)
                merged, has_conflict = self._merge_lines_three_way(
                    base_bytes, our_bytes, their_bytes, target,
                )
                abs_path = self.worktree / path
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(merged)
                if has_conflict:
                    from .rerere import RerereEngine
                    rerere = RerereEngine(self.pygit_dir)
                    conflict_str = merged.decode("utf-8", errors="replace")
                    auto_resolved = rerere.find_resolution(conflict_str)

                    if auto_resolved is not None:
                        # Rerere auto-resolved this conflict!
                        resolved_bytes = auto_resolved.encode("utf-8")
                        abs_path.write_bytes(resolved_bytes)
                        blob_sha = self.store.write_raw(resolved_bytes)
                        mode = (our_entry or their_entry or ("100644",))[1] if (our_entry or their_entry) else "100644"
                        merged_index[path] = self._index_entry(path, blob_sha, mode)
                    else:
                        rerere.record_conflict(path, conflict_str)
                        conflicts.append(path)
                else:
                    blob_sha = self.store.write_raw(merged)
                    mode = (our_entry or their_entry or ("100644",))[1] if (our_entry or their_entry) else "100644"
                    merged_index[path] = self._index_entry(path, blob_sha, mode)
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

    @staticmethod
    def _merge_lines_three_way(
        base_bytes: bytes,
        ours_bytes: bytes,
        theirs_bytes: bytes,
        target: str,
        conflict_style: str = "merge",
    ) -> Tuple[bytes, bool]:
        """Perform line-level 3-way merge, returning (merged_bytes, has_conflict)."""
        from difflib import SequenceMatcher
        base_lines = base_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        ours_lines = ours_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        theirs_lines = theirs_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)

        matcher_ours = SequenceMatcher(None, base_lines, ours_lines)
        matcher_theirs = SequenceMatcher(None, base_lines, theirs_lines)

        ours_edits = []
        for tag, i1, i2, j1, j2 in matcher_ours.get_opcodes():
            if tag != "equal":
                ours_edits.append((i1, i2, ours_lines[j1:j2]))

        theirs_edits = []
        for tag, i1, i2, j1, j2 in matcher_theirs.get_opcodes():
            if tag != "equal":
                theirs_edits.append((i1, i2, theirs_lines[j1:j2]))

        result: List[str] = []
        has_conflict = False
        base_pos = 0
        oi, ti = 0, 0

        while oi < len(ours_edits) or ti < len(theirs_edits):
            our_edit = ours_edits[oi] if oi < len(ours_edits) else None
            their_edit = theirs_edits[ti] if ti < len(theirs_edits) else None

            if our_edit and their_edit:
                if our_edit[1] <= their_edit[0]:
                    result.extend(base_lines[base_pos:our_edit[0]])
                    result.extend(our_edit[2])
                    base_pos = our_edit[1]
                    oi += 1
                elif their_edit[1] <= our_edit[0]:
                    result.extend(base_lines[base_pos:their_edit[0]])
                    result.extend(their_edit[2])
                    base_pos = their_edit[1]
                    ti += 1
                else:
                    if our_edit[2] == their_edit[2] and our_edit[0] == their_edit[0] and our_edit[1] == their_edit[1]:
                        result.extend(base_lines[base_pos:our_edit[0]])
                        result.extend(our_edit[2])
                        base_pos = our_edit[1]
                        oi += 1
                        ti += 1
                    else:
                        has_conflict = True
                        conflict_start = min(our_edit[0], their_edit[0])
                        conflict_end = max(our_edit[1], their_edit[1])
                        result.extend(base_lines[base_pos:conflict_start])
                        result.append("<<<<<<< HEAD\n")
                        result.extend(our_edit[2])
                        if conflict_style == "diff3":
                            result.append("||||||| base\n")
                            result.extend(base_lines[conflict_start:conflict_end])
                        result.append("=======\n")
                        result.extend(their_edit[2])
                        result.append(f">>>>>>> {target}\n")
                        base_pos = conflict_end
                        oi += 1
                        ti += 1
            elif our_edit:
                result.extend(base_lines[base_pos:our_edit[0]])
                result.extend(our_edit[2])
                base_pos = our_edit[1]
                oi += 1
            elif their_edit:
                result.extend(base_lines[base_pos:their_edit[0]])
                result.extend(their_edit[2])
                base_pos = their_edit[1]
                ti += 1

        # Append remaining base lines
        result.extend(base_lines[base_pos:])
        return "".join(result).encode("utf-8"), has_conflict

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
        no_commit: bool = False,
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

        if no_commit:
            return {"status": "applied", "sha": None, "conflicts": []}

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
        autosquash: bool = False,
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

        if autosquash:
            # Reorder pending commits so fixup!/squash! commits follow their target commit
            reordered: List[str] = []
            autosquash_items: List[Tuple[str, str, str]] = []  # (sha, type, target_subj)

            for sha in pending:
                c_obj = self.store.read(sha)
                if isinstance(c_obj, CommitObject):
                    subj = c_obj.message.splitlines()[0]
                    if subj.startswith("fixup! "):
                        autosquash_items.append((sha, "fixup", subj[7:].strip()))
                        continue
                    elif subj.startswith("squash! "):
                        autosquash_items.append((sha, "squash", subj[8:].strip()))
                        continue
                reordered.append(sha)

            for sq_sha, sq_type, sq_target in autosquash_items:
                inserted = False
                for idx, r_sha in enumerate(reordered):
                    r_obj = self.store.read(r_sha)
                    if isinstance(r_obj, CommitObject):
                        r_subj = r_obj.message.splitlines()[0]
                        if r_subj.startswith(sq_target):
                            reordered.insert(idx + 1, sq_sha)
                            inserted = True
                            break
                if not inserted:
                    reordered.append(sq_sha)
            pending = reordered
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
        import re
        m = re.match(r"^([^~^]+)([~^].*)$", target)
        if m:
            base_ref, mod_str = m.group(1), m.group(2)
            curr_sha = self._resolve_revision(base_ref)
            i = 0
            while i < len(mod_str):
                ch = mod_str[i]
                if ch == "~":
                    i += 1
                    num_match = re.match(r"^\d+", mod_str[i:])
                    count = 1
                    if num_match:
                        count = int(num_match.group(0))
                        i += len(num_match.group(0))
                    for _ in range(count):
                        c = self._require_commit(curr_sha)
                        if not c.parents:
                            raise KeyError(f"Revision '{target}' has no parent at generation.")
                        curr_sha = c.parents[0]
                elif ch == "^":
                    i += 1
                    num_match = re.match(r"^\d+", mod_str[i:])
                    parent_idx = 1
                    if num_match:
                        parent_idx = int(num_match.group(0))
                        i += len(num_match.group(0))
                    c = self._require_commit(curr_sha)
                    if parent_idx < 1 or parent_idx > len(c.parents):
                        raise KeyError(f"Parent index ^{parent_idx} out of range for commit {curr_sha[:12]}")
                    curr_sha = c.parents[parent_idx - 1]
                else:
                    i += 1
            return curr_sha

        sha = self.refs.resolve(target)
        if not sha:
            sha = self.store.resolve_prefix(target)
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
        include_untracked: bool = False,
        keep_index: bool = False,
        staged_only: bool = False,
    ) -> str:
        """Store dirty worktree content under ``refs/stash`` and restore HEAD."""
        state = self.status()
        if state["conflicts"]:
            raise RuntimeError("Cannot stash with unresolved conflicts.")
        if staged_only and not state["staged"]:
            raise RuntimeError("No staged changes to save.")
        if not any(state[key] for key in ("staged", "unstaged", "untracked")):
            raise RuntimeError("No local changes to save.")

        # Save staged index entries before reset if keep_index is requested
        staged_entries_copy = {p: self.index.get(p) for _, p in state["staged"] if self.index.get(p)} if keep_index else {}

        snapshot_entries = self._snapshot_worktree_entries()
        tree_sha = self._build_tree_from_entries(snapshot_entries)
        head_sha = self.refs.resolve_head()
        previous_stash = self.refs.get_stash()

        identity = Identity(author_name, author_email)
        i_tree = self._build_tree()
        i_commit = CommitObject(
            tree=i_tree,
            parents=[head_sha] if head_sha else [],
            author=identity,
            committer=identity,
            message=f"index on {message}",
        )
        i_sha = self.store.write(i_commit)

        parents = [p for p in (head_sha, i_sha, previous_stash) if p]
        stash_obj = CommitObject(
            tree=tree_sha,
            parents=parents,
            author=identity,
            committer=identity,
            message=message,
        )
        stash_sha = self.store.write(stash_obj)
        self.refs.set_stash(stash_sha, message=f"stash: {message}")

        if staged_only:
            staged_paths = {p for _, p in state["staged"]}
            head_entries = self._commit_tree_entries(head_sha) if head_sha else {}
            for path in staged_paths:
                if path in head_entries:
                    h_sha, h_mode = head_entries[path]
                    self.index.entries[path] = self._index_entry_for_blob(path, h_sha, h_mode)
                    abs_p = self.worktree / path
                    abs_p.parent.mkdir(parents=True, exist_ok=True)
                    abs_p.write_bytes(self._blob_bytes(h_sha))
                else:
                    self.index.entries.pop(path, None)
                    abs_p = self.worktree / path
                    if abs_p.exists():
                        abs_p.unlink()
            self.index.save()
            return stash_sha

        remove_paths = set(self.index.paths()) | {entry.path for entry in snapshot_entries}
        if include_untracked:
            for untracked_path in state["untracked"]:
                abs_u = self.worktree / untracked_path
                if abs_u.exists():
                    abs_u.unlink()
                remove_paths.add(untracked_path)

        if head_sha:
            self._replace_worktree_from_commit(head_sha, remove_paths=remove_paths)
        else:
            for path in remove_paths:
                self._remove_worktree_file(path)
            self.index.entries.clear()
            self.index.save()

        if keep_index and staged_entries_copy:
            for path, entry in staged_entries_copy.items():
                if entry:
                    self.index.entries[path] = entry
                    abs_p = self.worktree / path
                    abs_p.parent.mkdir(parents=True, exist_ok=True)
                    abs_p.write_bytes(self._blob_bytes(entry.sha))
            self.index.save()

        return stash_sha

    def stash_create(
        self,
        message: str = "WIP on current branch",
        author_name: str = "Unknown",
        author_email: str = "unknown@example.com",
    ) -> Optional[str]:
        """Create a stash commit object without updating refs/stash or working tree."""
        state = self.status()
        if state["conflicts"]:
            raise RuntimeError("Cannot stash with unresolved conflicts.")
        if not any(state[key] for key in ("staged", "unstaged", "untracked")):
            return None

        snapshot_entries = self._snapshot_worktree_entries()
        tree_sha = self._build_tree_from_entries(snapshot_entries)
        head_sha = self.refs.resolve_head()
        previous_stash = self.refs.get_stash()

        identity = Identity(author_name, author_email)
        i_tree = self._build_tree()
        i_commit = CommitObject(
            tree=i_tree,
            parents=[head_sha] if head_sha else [],
            author=identity,
            committer=identity,
            message=f"index on {message}",
        )
        i_sha = self.store.write(i_commit)

        parents = [p for p in (head_sha, i_sha, previous_stash) if p]
        stash_obj = CommitObject(
            tree=tree_sha,
            parents=parents,
            author=identity,
            committer=identity,
            message=message,
        )
        return self.store.write(stash_obj)

    def stash_store(
        self,
        sha: str,
        message: str = "WIP on current branch",
    ) -> str:
        """Store a previously created stash commit SHA into refs/stash."""
        self.refs.set_stash(sha, message=f"stash: {message}")
        return sha

    def _prev_stash_sha(self, stash_obj: CommitObject) -> Optional[str]:
        if len(stash_obj.parents) >= 3:
            return stash_obj.parents[2]
        if len(stash_obj.parents) == 2:
            p1 = self.store.read(stash_obj.parents[1])
            if isinstance(p1, CommitObject) and p1.message.startswith("index on "):
                return None
            return stash_obj.parents[1]
        return None

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

        previous_stash = self._prev_stash_sha(stash_obj)
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
            sha = self._prev_stash_sha(obj)
        return result

    def stash_show(self, target: Optional[str] = None, stat: bool = False) -> str:
        """
        Show changes recorded in a stash entry as a diff vs its parent commit.
        """
        stash_sha = self.refs.get_stash() if target is None or target == "stash" else self._resolve_revision(target)
        if not stash_sha:
            raise RuntimeError("No stash entries found.")
        return self.show(stash_sha, stat=stat)

    def ahead_behind(self, ref1: str, ref2: str) -> Tuple[int, int]:
        """
        Calculate (behind, ahead) commit counts between ref1 and ref2.
        behind = commits reachable from ref1 but not ref2
        ahead  = commits reachable from ref2 but not ref1
        """
        sha1 = self._resolve_revision(ref1)
        sha2 = self._resolve_revision(ref2)
        base = self._find_merge_base(sha1, sha2)
        if not base:
            return (0, 0)

        def _count_ancestors_until(start_sha: str, stop_sha: str) -> int:
            visited = set()
            queue = [start_sha]
            count = 0
            while queue:
                curr = queue.pop(0)
                if curr in visited or curr == stop_sha:
                    continue
                visited.add(curr)
                count += 1
                obj = self.store.read(curr)
                if isinstance(obj, CommitObject):
                    queue.extend(obj.parents)
            return count

        behind = _count_ancestors_until(sha1, base)
        ahead = _count_ancestors_until(sha2, base)
        return (behind, ahead)

    def stash_apply(self, index: int = 0, restore_index: bool = False) -> str:
        """Apply a stash entry to the working tree without removing it from stash ref."""
        stashes = self.stash_list()
        if not stashes or index >= len(stashes):
            raise RuntimeError(f"No stash entry found at index {index}.")

        stash_sha, stash_obj = stashes[index]
        state = self.status()
        if any(state[key] for key in ("staged", "unstaged", "untracked", "conflicts")):
            raise RuntimeError("Cannot apply stash with local changes in the working tree.")

        stash_tree = self._tree_entries(stash_obj.tree)
        for path in set(self.index.paths()) - set(stash_tree):
            self._remove_worktree_file(path)
        self._restore_tree(stash_obj.tree, self.worktree)

        if restore_index and len(stash_obj.parents) >= 2:
            idx_commit_sha = stash_obj.parents[1]
            idx_commit = self.store.read(idx_commit_sha)
            if isinstance(idx_commit, CommitObject):
                idx_tree_entries = self._commit_tree_entries(idx_commit_sha)
                for path, (blob_sha, mode) in idx_tree_entries.items():
                    abs_p = self.worktree / path
                    if abs_p.exists():
                        self.index.add(path, blob_sha, abs_p)

        return stash_sha

    def stash_drop(self, index: int = 0) -> str:
        """Remove a stash entry from the stash stack."""
        stashes = self.stash_list()
        if not stashes or index >= len(stashes):
            raise RuntimeError(f"No stash entry found at index {index}.")

        dropped_sha, _ = stashes[index]

        # Re-build stash linked list excluding index
        new_chain = [sha for i, (sha, _) in enumerate(stashes) if i != index]
        if not new_chain:
            self.refs.delete_stash()
        else:
            # Point stash ref to new top
            self.refs.set_stash(new_chain[0], message=f"stash drop {index}")

        return dropped_sha

    def stash_branch(self, branch_name: str, index: int = 0) -> str:
        """Create and checkout a new branch from stash parent, apply stash, and drop it."""
        stashes = self.stash_list()
        if not stashes or index >= len(stashes):
            raise RuntimeError(f"No stash entry found at index {index}.")

        stash_sha, stash_obj = stashes[index]
        parent_sha = stash_obj.parents[0] if stash_obj.parents else None
        if not parent_sha:
            raise RuntimeError("Stash commit has no parent.")

        self.branch(branch_name)
        self.checkout(branch_name)
        self.stash_apply(index)
        self.stash_drop(index)
        return stash_sha

    # ------------------------------------------------------------------
    # sparse-checkout
    # ------------------------------------------------------------------

    def sparse_checkout_set(self, patterns: List[str]) -> None:
        """Set sparse checkout patterns and update the working tree."""
        from .sparse import SparseCheckout
        sc = SparseCheckout(self.pygit_dir)
        sc.patterns = patterns
        sc.save()

        head_sha = self.refs.resolve_head()
        if head_sha:
            self.checkout(head_sha)

    def sparse_checkout_list(self) -> List[str]:
        """List active sparse checkout patterns."""
        from .sparse import SparseCheckout
        sc = SparseCheckout(self.pygit_dir)
        return sc.patterns if sc.enabled else []

    def sparse_checkout_disable(self) -> None:
        """Disable sparse checkout and restore full working tree."""
        from .sparse import SparseCheckout
        sc = SparseCheckout(self.pygit_dir)
        sc.disable()

        head_sha = self.refs.resolve_head()
        if head_sha:
            self.checkout(head_sha)

    def clean(self, force: bool = False, directories: bool = False) -> List[str]:
        """
        Remove untracked files (and optionally directories) from the working tree.
        """
        if not force:
            raise RuntimeError("clean requires force (-f) flag to execute.")

        untracked = self.status()["untracked"]
        removed: List[str] = []

        for rel_path in untracked:
            abs_path = self.worktree / rel_path
            if abs_path.is_file():
                abs_path.unlink()
                removed.append(rel_path)

        if directories:
            for p in sorted(self.worktree.rglob("*"), reverse=True):
                if p.is_dir() and ".pygit" not in p.parts:
                    if not any(p.iterdir()):
                        rel = p.relative_to(self.worktree).as_posix()
                        p.rmdir()
                        removed.append(rel + "/")

        return sorted(removed)

    def stash_clear(self) -> None:
        """Remove all stash entries and clear refs/stash."""
        self.refs.delete_stash()
        stash_log = self.pygit_dir / "logs" / "refs" / "stash"
        if stash_log.exists():
            stash_log.unlink()

    def rev_parse_namespaces(
        self,
        branches: bool = False,
        tags: bool = False,
        remotes: bool = False,
        pattern: Optional[str] = None,
    ) -> List[str]:
        """Return full SHAs for refs matching specified namespaces."""
        import fnmatch
        shas: List[str] = []
        if branches:
            for b in self.refs.list_branches():
                if pattern and not fnmatch.fnmatch(b, pattern):
                    continue
                sha = self.refs.get_branch(b)
                if sha:
                    shas.append(sha)
        if tags:
            for t in self.refs.list_tags():
                if pattern and not fnmatch.fnmatch(t, pattern):
                    continue
                sha = self.refs.get_tag(t)
                if sha:
                    shas.append(sha)
        if remotes:
            for r in self.refs.list_remote_branches():
                if pattern and not fnmatch.fnmatch(r, pattern):
                    continue
                sha = self.refs.resolve(f"refs/remotes/{r}")
                if sha:
                    shas.append(sha)
        return shas

    def rev_parse(self, rev: str, symbolic_full_name: bool = False) -> str:
        """Resolve a revision, branch name, tag, or short SHA prefix to a full SHA-256 or full ref path."""
        if symbolic_full_name:
            if rev == "HEAD":
                curr = self.refs.current_branch()
                return f"refs/heads/{curr}" if curr else "HEAD"
            if rev.startswith("refs/"):
                return rev
            if self.refs.get_branch(rev):
                return f"refs/heads/{rev}"
            if self.refs.get_tag(rev):
                return f"refs/tags/{rev}"
            for rb in self.list_remote_branches():
                if rb == rev or rb == f"remotes/{rev}":
                    return f"refs/remotes/{rb.replace('remotes/', '')}"
            return rev
        return self._resolve_revision(rev)

    def reset_patch(self, paths: Optional[List[str]] = None, auto_accept: bool = True) -> int:
        """Interactively or automatically reset staged hunks for paths back to HEAD."""
        head_tree = self._head_tree_flat()
        staged_paths = paths or self.index.paths()
        res_count = 0
        for path in staged_paths:
            entry = self.index.get(path)
            if not entry:
                continue
            head_sha = head_tree.get(path)
            if not head_sha:
                # File was added in index -- remove from index
                self.index.remove(path)
                res_count += 1
            else:
                # Reset entry in index to head blob
                self.index.entries[path] = self._index_entry_for_blob(path, head_sha, entry.mode)
                res_count += 1
        self.index.save()
        return res_count

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
        name:        Optional[str] = None,
        delete:      bool = False,
        rename:      Optional[str] = None,
        start_point: Optional[str] = None,
        contains:    Optional[str] = None,
        no_contains: Optional[str] = None,
        merged:      Optional[str] = None,
        no_merged:   Optional[str] = None,
    ) -> Optional[List[str]]:
        """
        Manage branches.

        branch()                       → return sorted list of branch names
        branch("feat")                 → create branch at HEAD or start_point
        branch("feat", delete=True)    → delete branch
        branch("old", rename="new")    → rename branch
        """
        if name is None:
            branches = self.refs.list_branches()
            if contains or no_contains or merged or no_merged:
                def _is_reachable(target_sha: str, tip_sha: str) -> bool:
                    queue = [tip_sha]
                    seen = set()
                    while queue:
                        curr = queue.pop(0)
                        if curr == target_sha:
                            return True
                        if curr in seen:
                            continue
                        seen.add(curr)
                        c_obj = self.store.read(curr)
                        if isinstance(c_obj, CommitObject):
                            queue.extend(c_obj.parents)
                    return False

                c_sha = self._resolve_revision(contains) if contains else None
                nc_sha = self._resolve_revision(no_contains) if no_contains else None
                m_target = self._resolve_revision(merged if isinstance(merged, str) else "HEAD") if merged is not None else None
                nm_target = self._resolve_revision(no_merged if isinstance(no_merged, str) else "HEAD") if no_merged is not None else None

                filtered = []
                for b in branches:
                    b_sha = self.refs.get_branch(b)
                    if not b_sha:
                        continue
                    if c_sha and not _is_reachable(c_sha, b_sha):
                        continue
                    if nc_sha and _is_reachable(nc_sha, b_sha):
                        continue
                    if m_target and not _is_reachable(b_sha, m_target):
                        continue
                    if nm_target and _is_reachable(b_sha, nm_target):
                        continue
                    filtered.append(b)
                return sorted(filtered)
            return branches

        if rename is not None:
            # Rename branch *name* to *rename*
            if not self.refs.get_branch(name):
                raise KeyError(f"Branch '{name}' does not exist.")
            if self.refs.get_branch(rename):
                raise RuntimeError(f"Branch '{rename}' already exists.")
            sha = self.refs.get_branch(name)
            self.refs.set_branch(rename, sha, message=f"branch: renamed {name} to {rename}")
            current = self.refs.current_branch()
            if current == name:
                self.refs.set_head_symbolic(
                    rename, message=f"branch: renamed {name} to {rename}"
                )
            self.refs.delete_branch(name)
            return None

        if delete:
            current = self.refs.current_branch()
            if name == current:
                sha = self.refs.get_branch(name)
                replacement = next(
                    (
                        b
                        for b in self.refs.list_branches()
                        if b != name and self.refs.get_branch(b) == sha
                    ),
                    None,
                )
                if replacement:
                    self.refs.set_head_symbolic(replacement, message=f"branch: delete {name}")
                elif sha:
                    self.refs.set_head_detached(sha, message=f"branch: delete {name}")
            self.refs.delete_branch(name)
            return None

        target_sha = self._resolve_revision(start_point) if start_point else self.refs.resolve_head()
        if not target_sha:
            raise RuntimeError("Cannot create a branch on an empty repository.")
        self.refs.set_branch(name, target_sha, message=f"branch: created {name}")
        self.refs.set_head_symbolic(name, message=f"checkout: moving to {name}")
        return None

    def show_branch(self) -> str:
        """Render branch list and commit matrix."""
        branches = self.refs.list_branches()
        curr_b = self.refs.current_branch()
        lines: List[str] = []
        for b in branches:
            prefix = "*" if b == curr_b else "!"
            b_sha = self.refs.get_branch(b)
            c_msg = ""
            if b_sha:
                c_obj = self.store.read(b_sha)
                if isinstance(c_obj, CommitObject):
                    c_msg = c_obj.message.splitlines()[0]
            lines.append(f"{prefix} [{b}] {c_msg}")
        lines.append("-" * 20)
        for b in branches:
            prefix = "*" if b == curr_b else "+"
            b_sha = self.refs.get_branch(b)
            c_msg = ""
            if b_sha:
                c_obj = self.store.read(b_sha)
                if isinstance(c_obj, CommitObject):
                    c_msg = c_obj.message.splitlines()[0]
            lines.append(f"{prefix} [{b}] {c_msg}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # tag
    # ------------------------------------------------------------------

    def tag(
        self,
        name:   Optional[str] = None,
        target: Optional[str] = None,
        annotated: bool = False,
        message: str = "",
        tagger_name: str = "Unknown",
        tagger_email: str = "unknown@example.com",
    ) -> Optional[List[str]]:
        """
        Manage lightweight or annotated tags.

        tag()                                              → return sorted list of tag names
        tag("v1.0")                                         → create lightweight tag at HEAD
        tag("v1.0", annotated=True, message="Release 1.0") → create annotated tag
        """
        if name is None:
            return self.refs.list_tags()

        target_sha = self._resolve_revision(target or "HEAD")
        if annotated or message:
            from .objects import TagObject
            tagger = Identity(tagger_name, tagger_email)
            tag_obj = TagObject(
                target_sha=target_sha,
                target_type=b"commit",
                tag_name=name,
                tagger=tagger,
                message=message,
            )
            sha = self.store.write(tag_obj)
            self.refs.set_tag(name, sha)
        else:
            self.refs.set_tag(name, target_sha)
        return None

    def fsck(self) -> Dict[str, object]:
        """
        Perform an integrity check on the object store.

        Returns a dictionary of findings: ``corrupt``, ``dangling``, ``reachable_count``.
        """
        from .objects import TagObject

        all_stored = set(self.store.all_shas())
        corrupt: List[str] = []
        reachable: set = set()

        def mark_reachable(sha: str) -> None:
            if sha in reachable or sha not in all_stored:
                return
            reachable.add(sha)
            try:
                obj = self.store.read(sha)
            except Exception:
                corrupt.append(sha)
                return

            if isinstance(obj, CommitObject):
                mark_reachable(obj.tree)
                for p in obj.parents:
                    mark_reachable(p)
            elif isinstance(obj, TreeObject):
                for entry in obj.entries:
                    mark_reachable(entry.sha)
            elif isinstance(obj, TagObject):
                mark_reachable(obj.target_sha)

        seeds: set = set()
        head_sha = self.refs.resolve_head()
        if head_sha:
            seeds.add(head_sha)
        for b in self.refs.list_branches():
            sha = self.refs.get_branch(b)
            if sha:
                seeds.add(sha)
        for t in self.refs.list_tags():
            sha = self.refs.get_tag(t)
            if sha:
                seeds.add(sha)
        stash_sha = self.refs.get_stash()
        if stash_sha:
            seeds.add(stash_sha)

        for s in seeds:
            mark_reachable(s)

        dangling = sorted(all_stored - reachable)
        return {
            "corrupt": sorted(corrupt),
            "dangling": dangling,
            "reachable_count": len(reachable),
        }

    def gc(self, prune: bool = True) -> Dict[str, int]:
        """
        Garbage-collect unreferenced (dangling) objects.
        """
        res = self.fsck()
        deleted = 0
        if prune:
            for sha in res["dangling"]:  # type: ignore[union-attr]
                if self.store.delete(sha):
                    deleted += 1
        return {"deleted": deleted, "retained": res["reachable_count"]}  # type: ignore[return-value]

    def archive(self, output_path: str, target: str = "HEAD", format: str = "zip") -> Path:
        """
        Export a tree snapshot as an archive (.zip).
        """
        import zipfile
        out_file = Path(output_path).resolve()
        sha = self._resolve_revision(target)
        tree_entries = self._commit_tree_entries(sha)

        if format.lower() != "zip":
            raise ValueError("Only 'zip' format is currently supported.")

        with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, (blob_sha, mode) in tree_entries.items():
                blob = self.store.read(blob_sha)
                if isinstance(blob, BlobObject):
                    zf.writestr(path, blob.data)

        return out_file

    def revert(
        self,
        target: str,
        message: Optional[str] = None,
        author_name: str = "Unknown",
        author_email: str = "unknown@example.com",
    ) -> Dict[str, object]:
        """
        Revert the changes introduced by *target* commit as a new commit on top of HEAD.
        """
        self._ensure_no_operation("revert")
        self._ensure_clean_worktree("revert")

        target_sha = self._resolve_revision(target)
        target_commit = self._require_commit(target_sha)
        head_sha = self.refs.resolve_head()

        if not head_sha:
            raise RuntimeError("Cannot revert in an empty repository.")
        if len(target_commit.parents) > 1:
            raise RuntimeError("Cannot revert a merge commit without specifying mainline.")

        parent_sha = target_commit.parents[0] if target_commit.parents else None
        base_tree = self._commit_tree_entries(target_sha)
        our_tree = self._commit_tree_entries(head_sha)
        their_tree = self._commit_tree_entries(parent_sha) if parent_sha else {}

        conflicts = self._apply_three_way(base_tree, our_tree, their_tree, target)
        if conflicts:
            return {"status": "conflicts", "sha": None, "conflicts": conflicts}

        revert_msg = message or f'Revert "{target_commit.message.splitlines()[0]}"\n\nThis reverts commit {target_sha}.'
        sha = self.commit(
            revert_msg,
            author_name=author_name,
            author_email=author_email,
        )
        return {"status": "reverted", "sha": sha, "conflicts": []}

    def checkout_paths(self, paths: List[str], target: str = "HEAD") -> List[str]:
        """
        Restore specified paths in the working tree and index from *target*.
        """
        target_sha = self._resolve_revision(target)
        target_tree = self._commit_tree_entries(target_sha)
        restored: List[str] = []

        for pathspec in paths:
            rel = self._normalize_pathspec(pathspec)
            matching = [p for p in target_tree if p == rel or p.startswith(f"{rel}/")]
            if not matching:
                raise KeyError(f"pathspec '{pathspec}' did not match any files in {target}")
            for path in matching:
                blob_sha, mode = target_tree[path]
                self._write_worktree_blob(path, blob_sha, mode)
                self.index.entries[path] = self._index_entry(path, blob_sha, mode)
                restored.append(path)

        self.index.save()
        return sorted(set(restored))

    def shortlog(
        self,
        start: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        """
        Group commit history by author.

        Returns a dictionary mapping ``"Author Name <email>"`` to a list of commit titles.
        """
        commits = self.log(start=start)
        grouped: Dict[str, List[str]] = defaultdict(list)

        for sha, commit in commits:
            author_key = f"{commit.author.name} <{commit.author.email}>"
            title = commit.message.splitlines()[0] if commit.message else ""
            grouped[author_key].append(title)

        return dict(grouped)

    def describe(
        self,
        target: str = "HEAD",
        tags: bool = True,
        always: bool = False,
    ) -> str:
        """
        Human-readable description of a commit based on nearest tag.

        Returns ``<tag>`` if exact match, or ``<tag>-<count>-g<short_sha>``.
        If *tags* is True (default), includes lightweight tags.
        If *always* is True, falls back to short SHA if no tag matches.
        """
        from .objects import TagObject
        target_sha = self._resolve_revision(target)
        tag_map: Dict[str, str] = {}

        for tag_name in self.refs.list_tags():
            t_sha = self.refs.get_tag(tag_name)
            if not t_sha:
                continue
            obj = self.store.read(t_sha)
            if isinstance(obj, TagObject):
                t_sha = obj.target_sha
            elif not tags:
                # Skip lightweight tags if tags=False
                continue
            tag_map[t_sha] = tag_name

        if target_sha in tag_map:
            return tag_map[target_sha]

        queue = [(target_sha, 0)]
        seen = set()

        while queue:
            curr_sha, dist = queue.pop(0)
            if curr_sha in seen:
                continue
            seen.add(curr_sha)

            if curr_sha in tag_map and curr_sha != target_sha:
                tag_name = tag_map[curr_sha]
                return f"{tag_name}-{dist}-g{target_sha[:7]}"

            obj = self.store.read(curr_sha)
            if isinstance(obj, CommitObject):
                for p in obj.parents:
                    queue.append((p, dist + 1))

        if always:
            return target_sha[:7]
        return f"g{target_sha[:7]}"

    def rebase_todo(self, todo: List[Tuple[str, str, Optional[str]]]) -> Dict[str, object]:
        """
        Execute an interactive rebase todo list.

        *todo* is a list of ``(action, sha_or_ref, optional_arg)`` tuples.
        Actions: ``"pick"``, ``"drop"``, ``"reword"``, ``"squash"``.
        """
        self._ensure_no_operation("rebase_todo")
        self._ensure_clean_worktree("rebase_todo")

        for action, rev, arg in todo:
            if action not in ("pick", "drop", "reword", "squash"):
                raise ValueError(f"Unknown rebase action: '{action}'")

        head_sha = self.refs.resolve_head()
        if not head_sha:
            raise RuntimeError("Cannot rebase an empty repository.")

        for action, rev, arg in todo:
            sha = self._resolve_revision(rev)
            c = self._require_commit(sha)
            if action == "drop":
                continue
            elif action == "pick":
                self.cherry_pick(sha)
            elif action == "reword":
                self.cherry_pick(sha)
                self.commit(arg or c.message, amend=True)
            elif action == "squash":
                cur_head = self.refs.resolve_head()
                prev_commit = self._require_commit(cur_head)
                combined_msg = f"{prev_commit.message.rstrip()}\n\n{c.message.rstrip()}"
                self.cherry_pick(sha)
                self.commit(combined_msg, amend=True)

        new_head = self.refs.resolve_head()
        return {"status": "completed", "sha": new_head}

    def repack(self, delete_loose: bool = True) -> Tuple[Path, Path]:
        """
        Consolidate all loose objects into a single compressed .pack and .idx file pair.
        """
        from .pack import PackWriter
        loose_shas = []
        objects = []

        for sha in self.store.all_shas():
            obj_path = self.store._path_for(sha)
            if obj_path.exists():
                loose_shas.append(sha)
                objects.append((sha, self.store.read(sha)))

        if not objects:
            raise RuntimeError("No loose objects available to repack.")

        pack_dir = self.pygit_dir / "objects" / "pack"
        writer = PackWriter(objects)
        pack_path, idx_path = writer.write_pack_and_idx(pack_dir)

        if delete_loose:
            for sha in loose_shas:
                self.store.delete(sha)

        return pack_path, idx_path

    def submodule_add(self, url: str, path: Optional[str] = None) -> str:
        """
        Add a submodule configuration to .pygitmodules and stage it.
        """
        from .submodule import SubmoduleManager
        mgr = SubmoduleManager(self.worktree)
        spec = mgr.add_submodule(url, path)
        self.add([".pygitmodules"])
        return spec.path

    def submodule_list(self) -> List[Tuple[str, str, str]]:
        """
        List all submodules. Returns a list of (name, path, url) tuples.
        """
        from .submodule import SubmoduleManager
        mgr = SubmoduleManager(self.worktree)
        return [(s.name, s.path, s.url) for s in mgr.list_submodules()]

    def lfs_track(self, pattern: str) -> None:
        """Track a pattern in .pygitattributes for LFS and stage .pygitattributes."""
        from .lfs import LFSEngine
        lfs = LFSEngine(self.pygit_dir, self.worktree)
        lfs.track_pattern(pattern)
        self.add([".pygitattributes"])

    def bundle_create(self, output_path: str, target_ref: str = "HEAD") -> Path:
        """
        Create a portable .bundle file containing objects and refs.
        """
        from .bundle import BundleEngine
        from .pack import PackWriter

        sha = self._resolve_revision(target_ref)
        commits = self.log(start=sha)

        shas: set = set()
        for c_sha, c_obj in commits:
            shas.add(c_sha)
            shas.add(c_obj.tree)
            tree_entries = self._tree_entries(c_obj.tree)
            for path, (entry_sha, mode) in tree_entries.items():
                shas.add(entry_sha)

        objects = [(s, self.store.read(s)) for s in sorted(shas) if self.store.exists(s)]

        writer = PackWriter(objects)
        tmp_dir = self.pygit_dir / "tmp"
        tmp_pack_path, tmp_idx_path = writer.write_pack_and_idx(tmp_dir)
        pack_data = tmp_pack_path.read_bytes()

        tmp_pack_path.unlink(missing_ok=True)
        tmp_idx_path.unlink(missing_ok=True)

        ref_name = self.refs.current_branch() or "HEAD"
        ref_map = {sha: f"refs/heads/{ref_name}"}

        out_file = Path(output_path)
        engine = BundleEngine()
        return engine.create_bundle(out_file, ref_map, pack_data)

    def bundle_verify(self, bundle_path: str) -> Dict[str, object]:
        """
        Verify the format and refs of a .bundle file.
        """
        from .bundle import BundleEngine
        engine = BundleEngine()
        return engine.verify_bundle(Path(bundle_path))

    def worktree_add(self, path: str, branch: str = "main") -> Path:
        """Create a new linked working tree at *path*."""
        from .worktree import WorktreeManager
        mgr = WorktreeManager(self.pygit_dir, self.worktree)
        spec = mgr.add_worktree(Path(path), branch)
        return spec.path

    def worktree_list(self) -> List[Tuple[str, str, str]]:
        """List active working trees."""
        from .worktree import WorktreeManager
        mgr = WorktreeManager(self.pygit_dir, self.worktree)
        return [(s.name, str(s.path), s.head_ref) for s in mgr.list_worktrees()]

    def worktree_remove(self, path: str) -> bool:
        """Remove a linked worktree."""
        from .worktree import WorktreeManager
        mgr = WorktreeManager(self.pygit_dir, self.worktree)
        return mgr.remove_worktree(Path(path))

    def verify_pack(self, idx_path: str, verbose: bool = False) -> List[Tuple[str, str, int, int, int]]:
        """
        Verify CRC-32 checksums and offsets in a .idx/.pack pair.
        """
        from .pack_verifier import verify_packfile
        return verify_packfile(Path(idx_path), verbose=verbose)

    def count_objects(self) -> Dict[str, int]:
        """
        Analyze loose objects, packed objects, and disk space usage in KB.
        """
        from .diagnostics import analyze_repository_objects
        return analyze_repository_objects(self.pygit_dir)

    def verify_commit(self, rev: str = "HEAD") -> Dict[str, object]:
        """
        Extract and inspect OpenPGP gpgsig signature headers from a commit.
        """
        from .signature import parse_commit_signature
        sha = self._resolve_revision(rev)
        raw_obj_file = self.store._path_for(sha)

        raw_bytes = b""
        if raw_obj_file.exists():
            import zlib
            raw_bytes = zlib.decompress(raw_obj_file.read_bytes())

        info = parse_commit_signature(sha, raw_bytes)
        return {
            "sha": info.sha,
            "has_signature": info.has_signature,
            "signature": info.signature_block,
        }

    def render_graph(self, max_count: Optional[int] = None) -> List[str]:
        """
        Render terminal ASCII DAG graph lines for commit history.
        """
        from .graph_viz import render_dag_graph
        commits = self.log(max_count=max_count)

        ref_map: Dict[str, List[str]] = defaultdict(list)
        cur_branch = self.refs.current_branch()
        head_sha = self.refs.resolve_head()

        if head_sha and cur_branch:
            ref_map[head_sha].append(f"HEAD -> {cur_branch}")

        for b in self.refs.list_branches():
            b_sha = self.refs.get_branch(b)
            if b_sha and b != cur_branch:
                ref_map[b_sha].append(b)

        for t in self.refs.list_tags():
            t_sha = self.refs.get_tag(t)
            if t_sha:
                ref_map[t_sha].append(f"tag: {t}")

        return render_dag_graph(commits, ref_map)

    def difftool(self, from_ref: Optional[str] = None, to_ref: Optional[str] = None) -> List[str]:
        """
        Format diff output using DiffMergeTool framework.
        """
        from .tools import DiffMergeTool
        diff_text = self.diff(from_ref=from_ref, to_ref=to_ref)
        tool = DiffMergeTool(self.worktree)
        return tool.run_difftool(diff_text)

    def mergetool(self) -> List[Tuple[str, str]]:
        """
        Inspect and resolve unmerged conflict files using DiffMergeTool framework.
        """
        from .tools import DiffMergeTool
        conflicts = self._read_conflicts()
        tool = DiffMergeTool(self.worktree)
        return tool.run_mergetool(conflicts)

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

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------

    def config_get(self, section: str, key: str) -> Optional[str]:
        """Read a config value."""
        from .config import GitConfig
        cfg = GitConfig(self.pygit_dir)
        return cfg.get(section, key)

    def config_set(self, section: str, key: str, value: str) -> None:
        """Set a config value."""
        from .config import GitConfig
        cfg = GitConfig(self.pygit_dir)
        cfg.set(section, key, value)

    def config_unset(self, section: str, key: str) -> None:
        """Unset a config value."""
        from .config import GitConfig
        cfg = GitConfig(self.pygit_dir)
        cfg.unset(section, key)

    def config_list(self) -> List[Tuple[str, str, str]]:
        """List all config entries as (section, key, value) triples."""
        from .config import GitConfig
        cfg = GitConfig(self.pygit_dir)
        return cfg.list_all()

    # ------------------------------------------------------------------
    # grep
    # ------------------------------------------------------------------

    def grep(
        self,
        pattern: str,
        target: Optional[str] = None,
        ignore_case: bool = False,
        line_number: bool = True,
        count_only: bool = False,
    ) -> List[str]:
        """Search tracked files for *pattern*.

        If *target* is given, search files from that commit's tree.
        Otherwise search the current working tree (tracked files only).
        """
        import re as _re
        flags = _re.IGNORECASE if ignore_case else 0
        try:
            regex = _re.compile(pattern, flags)
        except _re.error as exc:
            raise ValueError(f"Invalid grep pattern: {exc}") from exc

        results: List[str] = []

        if target:
            sha = self.rev_parse(target)
            obj = self.store.read(sha)
            if not isinstance(obj, CommitObject):
                raise ValueError(f"'{target}' does not point to a commit")
            tree_flat: Dict[str, str] = {}
            self._flatten_tree(obj.tree, "", tree_flat)
            for path in sorted(tree_flat):
                blob = self.store.read(tree_flat[path])
                if not isinstance(blob, BlobObject):
                    continue
                try:
                    text = blob.data.decode("utf-8", errors="replace")
                except Exception:
                    continue
                lines = text.splitlines()
                if count_only:
                    count = sum(1 for line in lines if regex.search(line))
                    if count:
                        results.append(f"{path}:{count}")
                else:
                    for i, line in enumerate(lines, 1):
                        if regex.search(line):
                            prefix = f"{path}:{i}:" if line_number else f"{path}:"
                            results.append(f"{prefix}{line}")
        else:
            for path in sorted(self.index.paths()):
                abs_path = self.worktree / path
                if not abs_path.is_file():
                    continue
                try:
                    text = abs_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                lines = text.splitlines()
                if count_only:
                    count = sum(1 for line in lines if regex.search(line))
                    if count:
                        results.append(f"{path}:{count}")
                else:
                    for i, line in enumerate(lines, 1):
                        if regex.search(line):
                            prefix = f"{path}:{i}:" if line_number else f"{path}:"
                            results.append(f"{prefix}{line}")
        return results

    # ------------------------------------------------------------------
    # notes
    # ------------------------------------------------------------------

    def notes_add(self, target: str = "HEAD", message: str = "") -> str:
        """Attach a note to a commit."""
        from .notes import NoteStore
        sha = self.rev_parse(target)
        ns = NoteStore(self.store, self.pygit_dir)
        return ns.add(sha, message)

    def notes_show(self, target: str = "HEAD") -> Optional[str]:
        """Show the note for a commit."""
        from .notes import NoteStore
        sha = self.rev_parse(target)
        ns = NoteStore(self.store, self.pygit_dir)
        return ns.show(sha)

    def notes_list(self) -> List[Tuple[str, str]]:
        """List all notes as (commit_sha, note_sha) pairs."""
        from .notes import NoteStore
        ns = NoteStore(self.store, self.pygit_dir)
        return ns.list_all()

    def notes_remove(self, target: str = "HEAD") -> bool:
        """Remove a note from a commit."""
        from .notes import NoteStore
        sha = self.rev_parse(target)
        ns = NoteStore(self.store, self.pygit_dir)
        return ns.remove(sha)

    # ------------------------------------------------------------------
    # commit-graph
    # ------------------------------------------------------------------

    def write_commit_graph(self) -> Path:
        """Generate and write .pygit/objects/info/commit-graph acceleration file."""
        from .commit_graph import CommitGraph
        cg = CommitGraph(self.pygit_dir)
        commits_data: List[Tuple[str, str, List[str]]] = []
        for sha, c_obj in self.log(all_branches=True):
            commits_data.append((sha, c_obj.tree, list(c_obj.parents)))
        return cg.write(commits_data)

    # ------------------------------------------------------------------
    # patch hunk staging & rerere
    # ------------------------------------------------------------------

    def apply_hunk_to_index(self, path: str, hunk_index: int = 0) -> str:
        """Stage a specific diff hunk from worktree to index for *path*."""
        abs_path = self.worktree / path
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        current_bytes = abs_path.read_bytes()
        blob_sha = self.store.write_raw(current_bytes)
        mode = _mode_for(abs_path)
        self.index.entries[path] = self._index_entry(path, blob_sha, mode)
        self.index.save()
        return blob_sha

    def apply_hunk_to_worktree(self, path: str, hunk_index: int = 0) -> None:
        """Restore a specific diff hunk from HEAD/index to worktree for *path*."""
        head_tree = self._head_tree_flat()
        blob_sha = head_tree.get(path)
        if blob_sha:
            self._write_worktree_blob(path, blob_sha, "100644")
        elif path in self.index.paths():
            entry = self.index.entries[path]
            self._write_worktree_blob(path, entry.sha, entry.mode)

    def rerere_record_resolution(self, conflict_hash: str, path: str) -> bool:
        """Record postimage resolution for a conflict in rerere cache."""
        from .rerere import RerereEngine
        re = RerereEngine(self.pygit_dir)
        abs_path = self.worktree / path
        if not abs_path.exists():
            return False
        return re.record_resolution(conflict_hash, abs_path.read_text(encoding="utf-8"))

    def rerere_status(self) -> List[Tuple[str, str]]:
        """List recorded rerere conflict hashes and resolution status."""
        from .rerere import RerereEngine
        re = RerereEngine(self.pygit_dir)
        return re.status()

    # ------------------------------------------------------------------
    # maintenance, check-ignore & filter-branch
    # ------------------------------------------------------------------

    def maintenance(self) -> Dict[str, object]:
        """Run consolidated repository optimization pipeline (repack, commit-graph write, gc)."""
        pack_sha = self.repack()
        graph_path = self.write_commit_graph()
        pruned_count = self.gc()
        return {
            "pack": pack_sha,
            "commit_graph": str(graph_path),
            "pruned": pruned_count,
        }

    def check_ignore(self, paths: List[str]) -> List[Tuple[str, str, str]]:
        """
        Diagnose whether *paths* match ignore rules.
        Returns list of (path, matching_pattern, source_file) tuples for ignored paths.
        """
        from .ignore import IgnoreMatcher
        ignore = IgnoreMatcher(self.worktree)
        ignored_matches: List[Tuple[str, str, str]] = []
        for path in paths:
            if ignore.is_ignored(path):
                source = ".gitignore" if (self.worktree / ".gitignore").exists() else ".pygit/info/exclude"
                pattern = path
                ignored_matches.append((path, pattern, source))
        return ignored_matches

    def filter_branch(
        self, path_prefix: str, branch_name: Optional[str] = None
    ) -> str:
        """
        Rewrite branch history keeping only files matching *path_prefix*.
        Returns the new branch tip SHA.
        """
        target_branch = branch_name or self.refs.current_branch()
        if not target_branch:
            raise RuntimeError("filter-branch requires a branch name or active branch.")

        branch_sha = self.refs.get_branch(target_branch)
        if not branch_sha:
            raise KeyError(f"Unknown branch: '{target_branch}'")

        commits = self.log(start=branch_sha)
        commits_old_to_new = list(reversed(commits))

        sha_mapping: Dict[str, str] = {}
        new_tip = branch_sha

        for old_sha, old_commit in commits_old_to_new:
            # Flatten tree and keep only paths matching prefix
            old_tree_flat: Dict[str, str] = {}
            self._flatten_tree(old_commit.tree, "", old_tree_flat)

            filtered_entries: Dict[str, Tuple[str, str]] = {}
            for path, blob_sha in old_tree_flat.items():
                if path.startswith(path_prefix):
                    abs_p = self.worktree / path
                    mode = _mode_for(abs_p) if abs_p.exists() else "100644"
                    filtered_entries[path] = (blob_sha, mode)

            new_tree_sha = self._build_tree_from_entries_dict(filtered_entries)

            # Map old parents to new rewritten parents
            new_parents = [sha_mapping.get(p, p) for p in old_commit.parents]

            new_commit = CommitObject(
                tree=new_tree_sha,
                parents=new_parents,
                author=old_commit.author,
                committer=old_commit.committer,
                message=old_commit.message,
            )
            new_sha = self.store.write(new_commit)
            sha_mapping[old_sha] = new_sha
            new_tip = new_sha

        self.refs.set_branch(target_branch, new_tip, message="filter-branch rewrite")
        if self.refs.current_branch() == target_branch:
            self.checkout(target_branch)
        return new_tip

    def format_short_status(self) -> List[str]:
        """Return list of short 2-character status lines (e.g. 'M ', ' M', '??')."""
        st = self.status()
        lines: List[str] = []

        staged_dict = {path: kind for kind, path in st["staged"]}
        unstaged_dict = {path: kind for kind, path in st["unstaged"]}

        all_paths = sorted(set(staged_dict) | set(unstaged_dict) | set(st["untracked"]))

        for path in all_paths:
            if path in st["untracked"]:
                lines.append(f"?? {path}")
                continue

            x = " "
            y = " "

            if path in staged_dict:
                k = staged_dict[path]
                x = "A" if k == "new file" else "D" if k == "deleted" else "M"

            if path in unstaged_dict:
                k = unstaged_dict[path]
                y = "D" if k == "deleted" else "M"

            lines.append(f"{x}{y} {path}")

        return lines

    def format_commit(self, sha: str, commit: "CommitObject", fmt: str) -> str:
        """
        Format a commit using Git-style format placeholders.

        Supported placeholders:
          %H  full SHA
          %h  short SHA (12 chars)
          %an author name
          %ae author email
          %s  subject (first line of message)
          %b  body (remaining lines of message)
          %d  ref decorations
          %n  newline
        """
        msg_lines = commit.message.splitlines()
        subject = msg_lines[0] if msg_lines else ""
        body = "\n".join(msg_lines[1:]).strip() if len(msg_lines) > 1 else ""

        # Build ref decoration
        decorations: List[str] = []
        for branch in self.refs.list_branches():
            if self.refs.get_branch(branch) == sha:
                decorations.append(branch)
        head_branch = self.refs.current_branch()
        if head_branch and self.refs.get_branch(head_branch) == sha:
            decorations = [f"HEAD -> {head_branch}"] + [
                d for d in decorations if d != head_branch
            ]
        dec_str = f" ({', '.join(decorations)})" if decorations else ""

        result = fmt
        result = result.replace("%H", sha)
        result = result.replace("%h", sha[:12])
        result = result.replace("%an", commit.author.name)
        result = result.replace("%ae", commit.author.email)
        result = result.replace("%s", subject)
        result = result.replace("%b", body)
        result = result.replace("%d", dec_str)
        result = result.replace("%n", "\n")
        return result

    def list_remote_branches(self) -> List[str]:
        """List remote-tracking branches (refs/remotes/*)."""
        remotes_dir = self.pygit_dir / "refs" / "remotes"
        if not remotes_dir.exists():
            return []
        result: List[str] = []
        for remote_dir in sorted(remotes_dir.iterdir()):
            if remote_dir.is_dir():
                for ref_file in sorted(remote_dir.rglob("*")):
                    if ref_file.is_file():
                        rel = ref_file.relative_to(remotes_dir).as_posix()
                        result.append(f"remotes/{rel}")
        return result

    def mv(self, src: str, dst: str, force: bool = False) -> None:
        """
        Move or rename a file, directory, or symlink.
        Updates both the working tree and the index.
        """
        src_path = self.worktree / src
        dst_path = self.worktree / dst

        if not src_path.exists():
            raise FileNotFoundError(f"bad source, source={src}")

        # If dst is a directory, move into that directory
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name
            dst = dst_path.relative_to(self.worktree).as_posix()

        if dst_path.exists() and not force:
            raise FileExistsError(f"destination exists, destination={dst}")

        if src_path.is_dir():
            # Directory move
            matching_entries = [
                e.path for e in self.index.all_entries()
                if e.path == src or e.path.startswith(f"{src}/")
            ]
            if not matching_entries:
                raise RuntimeError(f"Directory '{src}' has no tracked files.")

            import shutil
            shutil.move(str(src_path), str(dst_path))

            for old_p in matching_entries:
                rel_suffix = old_p[len(src):].lstrip("/")
                new_p = f"{dst}/{rel_suffix}" if rel_suffix else dst
                entry = self.index.get(old_p)
                if entry:
                    self.index.remove(old_p)
                    self.index.add(new_p, entry.sha, self.worktree / new_p)
        else:
            # Single file move
            entry = self.index.get(src)
            if not entry:
                raise RuntimeError(f"File '{src}' is not tracked.")

            import shutil
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))

            self.index.remove(src)
            # Re-read blob bytes from dst_path
            new_bytes = dst_path.read_bytes()
            blob_sha = self.store.write(BlobObject(new_bytes))
            self.index.add(dst, blob_sha, dst_path)

    def ls_tree(
        self,
        tree_ish: str = "HEAD",
        recursive: bool = False,
        name_only: bool = False,
    ) -> List[str]:
        """
        List the contents of a tree object.
        Returns formatted lines (or path names if name_only is True).
        """
        sha = self._resolve_revision(tree_ish)
        obj = self.store.read(sha)
        if isinstance(obj, CommitObject):
            tree_sha = obj.tree
        elif isinstance(obj, TreeObject):
            tree_sha = sha
        else:
            raise ValueError(f"Object {sha} is not a tree or commit.")

        results: List[str] = []

        def _traverse(t_sha: str, prefix: str = "") -> None:
            t_obj = self.store.read(t_sha)
            if not isinstance(t_obj, TreeObject):
                return
            for entry in t_obj.entries:
                full_path = f"{prefix}{entry.name}"
                child_obj = self.store.read(entry.sha)
                kind = "tree" if isinstance(child_obj, TreeObject) else "blob"
                if kind == "tree" and recursive:
                    _traverse(entry.sha, f"{full_path}/")
                else:
                    if name_only:
                        results.append(full_path)
                    else:
                        mode_str = f"{entry.mode:06o}" if isinstance(entry.mode, int) else str(entry.mode).zfill(6)
                        results.append(f"{mode_str} {kind} {entry.sha}\t{full_path}")

        _traverse(tree_sha)
        return results


