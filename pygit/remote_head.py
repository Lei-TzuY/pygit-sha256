"""Git-style remote default-branch symbolic-reference helpers."""

from __future__ import annotations

from typing import Optional

from .remote import SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


def _head_path(repo: Repository, remote: str):
    return repo.refs._path_under(repo.pygit_dir / "refs" / "remotes", f"{remote}/HEAD")


def remote_head_target(repo: Repository, remote: str) -> Optional[str]:
    """Return the fully-qualified target of ``refs/remotes/<remote>/HEAD``."""
    fetch_url(repo, remote)  # validate that this is a configured remote
    path = _head_path(repo, remote)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.startswith("ref: "):
        raise RuntimeError(f"Malformed remote HEAD for '{remote}': {raw!r}")
    target = raw[5:].strip()
    prefix = f"refs/remotes/{remote}/"
    if not target.startswith(prefix) or target == f"{prefix}HEAD":
        raise RuntimeError(f"Malformed remote HEAD for '{remote}': {raw!r}")
    return target


def _write_remote_head(repo: Repository, remote: str, branch: str) -> None:
    if not branch or branch == "HEAD" or branch.startswith(("/", "\\")):
        raise ValueError(f"invalid remote HEAD branch: {branch!r}")
    if repo.refs.get_remote(remote, branch) is None:
        raise RuntimeError(f"Not a valid ref: refs/remotes/{remote}/{branch}")
    path = _head_path(repo, remote)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"ref: refs/remotes/{remote}/{branch}\n", encoding="utf-8")


def _discover_default_branch(repo: Repository, remote: str) -> str:
    advertisement = SmartHttpClient(fetch_url(repo, remote)).discover()
    target = advertisement.symrefs.get("HEAD")
    if target and target.startswith("refs/heads/"):
        branch = target[len("refs/heads/") :]
    else:
        branch = repo._infer_default_branch(advertisement.refs)
    if not branch:
        raise RuntimeError(f"Cannot determine remote HEAD for '{remote}'")
    return branch


def set_remote_head(
    repo: Repository,
    remote: str,
    branch: Optional[str] = None,
    *,
    auto: bool = False,
    delete: bool = False,
) -> Optional[str]:
    """Set, auto-detect, or delete a named remote's tracking ``HEAD`` symref.

    Returns the selected branch for set/auto operations and ``None`` for delete.
    The destination tracking branch must already exist, matching native Git.
    """
    fetch_url(repo, remote)  # validate before any mutation
    if auto and delete:
        raise ValueError("--auto and --delete are mutually exclusive")
    if branch is not None and (auto or delete):
        raise ValueError("a branch cannot be combined with --auto or --delete")

    if delete:
        path = _head_path(repo, remote)
        if path.exists():
            path.unlink()
        return None

    selected = _discover_default_branch(repo, remote) if auto else branch
    if selected is None:
        raise ValueError("remote set-head requires --auto, --delete, or a branch")
    _write_remote_head(repo, remote, selected)
    return selected
