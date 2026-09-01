"""Git-compatible ``pygit init`` argument handling.

Phase383 keeps the mature repository initializer intact while adding Git's
``-b/--initial-branch`` porcelain behavior at the application-routing layer.
The requested branch is validated before any filesystem mutation. Reinitializing
an existing repository deliberately leaves HEAD unchanged and emits the same
style of warning as native Git.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .ref_query import check_ref_format
from .repo import Repository


def _validate_initial_branch(name: str) -> str:
    """Validate an init branch as the tail of ``refs/heads/<name>``.

    Native ``git init -b`` accepts names such as ``-topic`` that
    ``git check-ref-format --branch`` rejects because the latter protects
    command-line branch arguments. The actual stored reference is what matters
    here, so validate the fully-qualified ref instead.
    """

    check_ref_format(f"refs/heads/{name}")
    return name


def run_init(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit init",
        description="Create an empty pygit repository or reinitialize an existing one.",
    )
    parser.add_argument(
        "-b",
        "--initial-branch",
        dest="initial_branch",
        metavar="BRANCH",
        help="use BRANCH as the initial branch in a newly created repository",
    )
    parser.add_argument("directory", nargs="?", default=".", metavar="DIR")
    args = parser.parse_args(list(argv))

    initial_branch = None
    if args.initial_branch is not None:
        try:
            initial_branch = _validate_initial_branch(args.initial_branch)
        except ValueError as exc:
            raise ValueError(f"invalid initial branch name: {args.initial_branch!r}: {exc}") from exc

    worktree = Path(args.directory).resolve()
    pygit_dir = worktree / ".pygit"
    reinit = pygit_dir.is_dir()

    repo = Repository.init(str(worktree))

    if initial_branch is not None:
        if reinit:
            print(
                f"warning: re-init: ignored --initial-branch={initial_branch}",
                file=sys.stderr,
            )
        else:
            # Repository.init creates the default unborn HEAD. Replace only that
            # freshly-created symbolic ref; no branch file or reflog is created.
            (repo.pygit_dir / "HEAD").write_text(
                f"ref: refs/heads/{initial_branch}",
                encoding="utf-8",
            )

    return 0
