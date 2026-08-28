"""Atomic local-ref transactions for fetch porcelain.

Git's ``fetch --atomic`` promises that local reference updates either all
succeed or none are left behind.  Object transfer is intentionally outside
that guarantee: downloaded objects may remain in the object database even when
reference validation later rejects the fetch.

pygit's RefStore currently exposes individual loose/packed-ref operations rather
than a batch transaction API.  This module provides a repository-level
transaction boundary by snapshotting the complete local ref namespace,
``packed-refs``, and reflogs before a single-remote fetch.  Any exception restores
those files exactly; successful scopes keep the mutations.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .repo import Repository


def _copy_optional_tree(source: Path, destination: Path) -> None:
    if source.exists():
        shutil.copytree(source, destination)


def _restore_tree(snapshot: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    if snapshot.exists():
        shutil.copytree(snapshot, target)
    else:
        target.mkdir(parents=True, exist_ok=True)


def _copy_optional_file(source: Path, destination: Path) -> None:
    if source.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _restore_file(snapshot: Path, target: Path) -> None:
    if snapshot.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot, target)
    elif target.exists():
        target.unlink()


@contextmanager
def atomic_ref_updates(repo: Repository) -> Iterator[None]:
    """Rollback every local ref/reflog mutation if the fetch scope raises.

    The object store, per-remote native SHA map, FETCH_HEAD, and remote metadata
    are deliberately not rolled back because Git's ``--atomic`` contract is
    specifically about local ref updates.  FETCH_HEAD is written only after a
    successful fetch in the current porcelain paths, so a ref-update exception
    naturally leaves it untouched.
    """

    pygit_dir = repo.pygit_dir
    refs = pygit_dir / "refs"
    logs = pygit_dir / "logs"
    packed_refs = pygit_dir / "packed-refs"

    with tempfile.TemporaryDirectory(prefix="pygit-fetch-atomic-") as temp:
        snapshot = Path(temp)
        _copy_optional_tree(refs, snapshot / "refs")
        _copy_optional_tree(logs, snapshot / "logs")
        _copy_optional_file(packed_refs, snapshot / "packed-refs")
        try:
            yield
        except BaseException:
            _restore_tree(snapshot / "refs", refs)
            _restore_tree(snapshot / "logs", logs)
            _restore_file(snapshot / "packed-refs", packed_refs)
            raise
