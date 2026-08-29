"""Metadata-only ``rev-list --filter=blob:none`` adapter.

The promisor-aware rev-list paths already know how to traverse partial-clone
metadata without materializing promised blobs.  This adapter composes Git's
``blob:none`` object filter with those paths instead of falling back to the
historical object walker, where touching a foreign tree entry could trigger a
lazy fetch.

Phase246 intentionally starts with the filter form whose semantics are both
useful to partial clones and hash-domain neutral: blobs are omitted from the
reported object set, while commit/tree identities stay genuine local SHA-256.
Unresolved native SHA-1 promises are never rendered because they are blobs and
are filtered before presentation.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_missing_print_cli as _missing_print
from . import rev_list_promisor_cli as _promisor
from .objects import BlobObject
from .promisor import promised_kind


def _filter_spec(argv: Sequence[str]) -> Optional[str]:
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        return None
    if len(filters) != 1:
        raise ValueError("rev-list accepts exactly one --filter action in this phase")
    spec = filters[0].split("=", 1)[1]
    if spec != "blob:none":
        raise ValueError("pygit currently supports --filter=blob:none with --missing")
    return spec


def _project(argv: Sequence[str]) -> list[str]:
    projected = [arg for arg in argv if not arg.startswith("--filter=")]
    if "--count" in projected:
        raise ValueError("--filter=blob:none with --count is not yet supported")
    if "-z" in projected:
        raise ValueError("--filter=blob:none with -z is not yet supported")
    missing = [arg for arg in projected if arg.startswith("--missing=")]
    if len(missing) != 1 or missing[0] not in {
        "--missing=allow-promisor",
        "--missing=print",
        "--missing=print-info",
    }:
        raise ValueError(
            "--filter=blob:none currently requires --missing=allow-promisor, print, or print-info"
        )
    return projected


def _run_projected(argv: Sequence[str]) -> tuple[int, tuple[str, ...]]:
    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _missing_print.try_run_rev_list_missing_print(argv)
        if code is None:
            code = _promisor.try_run_rev_list_allow_promisor(argv)
    if code is None:
        raise RuntimeError("promisor rev-list adapter declined blob:none projection")
    return code, tuple(capture.getvalue().splitlines())


def _is_blob_line(repo, line: str) -> bool:
    if not line:
        return False

    token = line.split(None, 1)[0]
    if token.startswith("?"):
        native_oid = token[1:].lower()
        return promised_kind(repo.pygit_dir, native_oid) == "blob"

    if token.startswith("-"):
        token = token[1:]
    oid = token.lower()
    if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
        return False
    try:
        return isinstance(repo.store.read(oid), BlobObject)
    except (FileNotFoundError, KeyError):
        # The metadata-only --missing paths should never expose a missing local
        # SHA-256 as a present record.  Keep the line so the downstream
        # integrity semantics remain visible instead of silently hiding damage.
        return False


def try_run_rev_list_filter(argv: Sequence[str]) -> Optional[int]:
    """Handle ``--filter=blob:none`` for metadata-only missing-object traversal."""

    if _filter_spec(argv) is None:
        return None

    projected = _project(argv)
    code, lines = _run_projected(projected)
    repo = _promisor._find_repo()
    for line in lines:
        if not _is_blob_line(repo, line):
            print(line)
    return code
