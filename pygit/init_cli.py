"""Git-compatible ``pygit init`` argument handling.

This convergence replay keeps the mature repository initializer intact while
providing Git-style initial-branch, quiet, storage-format, and environment-default
porcelain on top of current ``main``.

Pygit is SHA-256-native with the files ref backend. Explicit or environment
requests for unsupported storage modes therefore fail before repository creation
instead of claiming a mode the storage implementation cannot honor.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional, Sequence

from .ref_query import check_ref_format
from .repo import Repository


_OBJECT_FORMAT = "sha256"
_REF_FORMAT = "files"
_OBJECT_FORMAT_ENV = "GIT_DEFAULT_HASH"
_REF_FORMAT_ENV = "GIT_DEFAULT_REF_FORMAT"


def _validate_initial_branch(name: str) -> str:
    """Validate the fully-qualified branch ref used by ``git init -b``."""

    check_ref_format(f"refs/heads/{name}")
    return name


def _effective_storage_format(
    cli_value: Optional[str],
    env_name: str,
    default: str,
) -> str:
    """Resolve one init storage format with CLI > environment > default precedence."""

    if cli_value is not None:
        return cli_value
    env_value = os.environ.get(env_name)
    return default if env_value is None else env_value


def _validate_storage_formats(object_format: str, ref_format: str) -> None:
    """Reject format selections that pygit's storage implementation cannot honor."""

    if object_format != _OBJECT_FORMAT:
        raise ValueError(
            f"unsupported object format {object_format!r}: pygit requires {_OBJECT_FORMAT}"
        )
    if ref_format != _REF_FORMAT:
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

    object_format = _effective_storage_format(
        args.object_format,
        _OBJECT_FORMAT_ENV,
        _OBJECT_FORMAT,
    )
    ref_format = _effective_storage_format(
        args.ref_format,
        _REF_FORMAT_ENV,
        _REF_FORMAT,
    )
    _validate_storage_formats(object_format, ref_format)

    initial_branch = None
    if args.initial_branch is not None:
        try:
            initial_branch = _validate_initial_branch(args.initial_branch)
        except ValueError as exc:
            raise ValueError(
                f"invalid initial branch name: {args.initial_branch!r}: {exc}"
            ) from exc

    worktree = Path(args.directory).resolve()
    pygit_dir = worktree / ".pygit"
    reinit = pygit_dir.is_dir()

    if args.quiet:
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
            (repo.pygit_dir / "HEAD").write_text(
                f"ref: refs/heads/{initial_branch}",
                encoding="utf-8",
            )

    return 0
