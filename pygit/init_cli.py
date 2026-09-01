"""Git-compatible ``pygit init`` argument handling.

Phase383 keeps the mature repository initializer intact while adding Git's
``-b/--initial-branch`` porcelain behavior at the application-routing layer.
The requested branch is validated before any filesystem mutation. Reinitializing
an existing repository deliberately leaves HEAD unchanged and emits the same
style of warning as native Git.

Phase384 extends that porcelain boundary with Git-compatible quiet operation and
explicit storage-format selection for the formats pygit actually implements.
Pygit is SHA-256-native with the files ref backend, so ``--object-format=sha256``
and ``--ref-format=files`` are accepted while unsupported alternatives fail
before repository creation instead of pretending to select an unusable backend.
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional, Sequence

from .ref_query import check_ref_format
from .repo import Repository


_OBJECT_FORMAT = "sha256"
_REF_FORMAT = "files"


def _validate_initial_branch(name: str) -> str:
    """Validate an init branch as the tail of ``refs/heads/<name>``.

    Native ``git init -b`` accepts names such as ``-topic`` that
    ``git check-ref-format --branch`` rejects because the latter protects
    command-line branch arguments. The actual stored reference is what matters
    here, so validate the fully-qualified ref instead.
    """

    check_ref_format(f"refs/heads/{name}")
    return name


def _validate_storage_formats(
    object_format: Optional[str],
    ref_format: Optional[str],
) -> None:
    """Reject format selections that pygit's storage implementation cannot honor.

    Native Git can create both SHA-1 and SHA-256 repositories and can select
    multiple ref backends. Pygit intentionally has a narrower invariant: local
    object identity is always content-derived SHA-256 and refs use the files
    backend. Accepting another value here would make the CLI claim a storage
    format that the repository implementation does not actually provide.
    """

    if object_format is not None and object_format != _OBJECT_FORMAT:
        raise ValueError(
            f"unsupported object format {object_format!r}: pygit requires {_OBJECT_FORMAT}"
        )
    if ref_format is not None and ref_format != _REF_FORMAT:
        raise ValueError(
            f"unsupported ref format {ref_format!r}: pygit requires {_REF_FORMAT}"
        )


def run_init(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit init",
        description="Create an empty pygit repository or reinitialize an existing one.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress ordinary initialization output",
    )
    parser.add_argument(
        "--object-format",
        dest="object_format",
        metavar="FORMAT",
        help="select the object hash format (pygit supports sha256)",
    )
    parser.add_argument(
        "--ref-format",
        dest="ref_format",
        metavar="FORMAT",
        help="select the ref storage format (pygit supports files)",
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

    _validate_storage_formats(args.object_format, args.ref_format)

    initial_branch = None
    if args.initial_branch is not None:
        try:
            initial_branch = _validate_initial_branch(args.initial_branch)
        except ValueError as exc:
            raise ValueError(f"invalid initial branch name: {args.initial_branch!r}: {exc}") from exc

    worktree = Path(args.directory).resolve()
    pygit_dir = worktree / ".pygit"
    reinit = pygit_dir.is_dir()

    if args.quiet:
        # ``Repository.init`` is also a public library API whose historical
        # behavior includes its informational stdout line. Keep that API stable
        # and make quietness a porcelain concern instead of mutating every caller.
        with redirect_stdout(io.StringIO()):
            repo = Repository.init(str(worktree))
    else:
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
