"""Repository-wide sandbox for Git-style fetch dry runs."""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .repo import Repository


@contextmanager
def dry_run_repository(repo: Repository) -> Iterator[None]:
    """Run fetch logic while restoring all repository-local metadata afterward.

    Fetch may touch objects, refs, reflogs, packed refs, config, native SHA maps,
    and FETCH_HEAD. A dry run should leave none of those mutations behind, so
    snapshot the complete ``.pygit`` directory instead of maintaining a second
    partial transaction implementation beside the real fetch path.
    """
    pygit_dir = Path(repo.pygit_dir)
    parent = pygit_dir.parent
    with tempfile.TemporaryDirectory(prefix="pygit-fetch-dry-run-", dir=parent) as tmp:
        snapshot = Path(tmp) / "snapshot"
        shutil.copytree(pygit_dir, snapshot)
        try:
            yield
        finally:
            if pygit_dir.exists():
                shutil.rmtree(pygit_dir)
            shutil.copytree(snapshot, pygit_dir)
