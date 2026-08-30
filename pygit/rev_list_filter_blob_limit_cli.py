"""Metadata-only ``rev-list --filter=blob:limit=<n>[kmg]`` adapter.

This is the clean current-stack port of the earlier Phase258 sibling. Local
blobs are classified from already-materialized payloads. Unresolved promised
blobs do not currently carry persistent size metadata in pygit, so classification
fails explicitly instead of materializing content merely to apply the filter.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from . import rev_list_filter_cli as _filter
from . import rev_list_missing_print_cli as _missing_print
from . import rev_list_promisor_cli as _promisor
from .objects import BlobObject


_BLOB_LIMIT_RE = re.compile(r"^blob:limit=([0-9]+)([kKmMgG]?)$")
_UNIT_MULTIPLIER = {
    "": 1,
    "k": 1024,
    "m": 1024 * 1024,
    "g": 1024 * 1024 * 1024,
}


def _blob_limit(argv: Sequence[str]) -> Optional[int]:
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    matching = [arg for arg in filters if arg.startswith("--filter=blob:limit=")]
    if not matching:
        return None
    if len(filters) != 1 or len(matching) != 1:
        raise ValueError("rev-list accepts exactly one --filter action in this phase")

    spec = matching[0].split("=", 1)[1]
    match = _BLOB_LIMIT_RE.fullmatch(spec)
    if match is None:
        raise ValueError("--filter=blob:limit requires <n>[kmg] with a non-negative integer")
    number = int(match.group(1))
    suffix = match.group(2).lower()
    return number * _UNIT_MULTIPLIER[suffix]


def _project(argv: Sequence[str]) -> list[str]:
    if "-z" in argv:
        raise ValueError("--filter=blob:limit with -z is not yet supported")
    if "--filter-print-omitted" in argv:
        raise ValueError(
            "--filter=blob:limit with --filter-print-omitted is not yet supported"
        )

    projected = [
        arg
        for arg in argv
        if not arg.startswith("--filter=") and arg != "--filter-provided-objects"
    ]
    missing = [arg for arg in projected if arg.startswith("--missing=")]
    if len(missing) != 1 or missing[0] not in {
        "--missing=allow-promisor",
        "--missing=print",
        "--missing=print-info",
    }:
        raise ValueError(
            "--filter=blob:limit currently requires --missing=allow-promisor, print, or print-info"
        )
    return projected


def _inventory_context(repo, argv: Sequence[str]):
    parsed = _filter._parse_inventory_request(argv)
    boundary_commits = ()
    snapshot_commits = None
    if parsed["boundary"]:
        boundary_commits = _promisor._promisor_boundary_commits(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
            topo_order=parsed["topo_order"],
            reverse=parsed["reverse"],
            skip=parsed["skip"],
            max_count=parsed["max_count"],
        )
        snapshot_commits = tuple(oid for oid, _is_boundary in boundary_commits)

    entries = _promisor.promisor_object_inventory(
        repo,
        parsed["revisions"],
        all_refs=parsed["all_refs"],
        first_parent=parsed["first_parent"],
        topo_order=parsed["topo_order"],
        reverse=parsed["reverse"],
        skip=parsed["skip"],
        max_count=parsed["max_count"],
        snapshot_commits=snapshot_commits,
    )
    return parsed, boundary_commits, entries


def _ensure_missing_blobs_are_classifiable(entries) -> None:
    for entry in entries:
        if entry.missing and entry.type_name == "blob":
            native = entry.native_oid or "<unknown>"
            raise RuntimeError(
                "--filter=blob:limit cannot classify unresolved promised blob "
                f"{native}: persistent promisor size metadata is unavailable"
            )


def _local_blob_size(repo, oid: str) -> Optional[int]:
    lowered = oid.lower()
    if len(lowered) != 64 or any(ch not in "0123456789abcdef" for ch in lowered):
        return None
    try:
        obj = repo.store.read(lowered)
    except (FileNotFoundError, KeyError):
        return None
    if not isinstance(obj, BlobObject):
        return None
    return len(obj)


def _keep_line(repo, line: str, *, limit: int) -> bool:
    if not line:
        return False
    token = line.split(None, 1)[0]
    if token.startswith("?"):
        # Missing blobs are rejected by the inventory preflight. Any remaining
        # missing record is a non-blob and therefore unaffected by blob:limit.
        return True
    if token.startswith("-"):
        token = token[1:]
    size = _local_blob_size(repo, token)
    return size is None or size < limit


def _entry_is_kept(repo, entry, *, limit: int) -> bool:
    if entry.missing:
        return False
    if entry.type_name != "blob":
        return True
    if entry.oid is None:
        raise RuntimeError("present blob inventory entry has no local SHA-256 identity")
    size = _local_blob_size(repo, entry.oid)
    if size is None:
        raise RuntimeError(f"present blob {entry.oid} cannot be read for size filtering")
    return size < limit


def _filtered_present_count(repo, argv: Sequence[str], *, limit: int) -> int:
    parsed, boundary_commits, entries = _inventory_context(repo, argv)
    _ensure_missing_blobs_are_classifiable(entries)

    if not parsed["boundary"]:
        return sum(1 for entry in entries if _entry_is_kept(repo, entry, limit=limit))

    snapshot_present = sum(
        1
        for entry in entries
        if not (entry.type_name == "commit" and entry.path is None)
        and _entry_is_kept(repo, entry, limit=limit)
    )

    overlap = frozenset()
    if "--objects-edge" in argv:
        edge_values = _promisor._promisor_object_edges(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
        )
        overlap = _missing_print._edge_boundary_overlap(repo, parsed, edge_values)

    return len(boundary_commits) - len(overlap) + snapshot_present


def try_run_rev_list_blob_limit(argv: Sequence[str]) -> Optional[int]:
    """Handle line/count ``blob:limit`` filtering without promisor fetches."""

    limit = _blob_limit(argv)
    if limit is None:
        return None

    projected = _project(argv)
    repo = _promisor._find_repo()
    _parsed, _boundary_commits, entries = _inventory_context(repo, projected)
    _ensure_missing_blobs_are_classifiable(entries)

    code, lines = _filter._run_projected(projected)
    if code:
        for line in lines:
            if _keep_line(repo, line, limit=limit):
                print(line)
        return code

    count_mode = "--count" in projected
    if count_mode:
        if not lines:
            raise RuntimeError("rev-list blob:limit count projection produced no output")
        try:
            int(lines[-1])
        except ValueError as exc:
            raise RuntimeError(
                "rev-list blob:limit count projection did not end with an integer"
            ) from exc
        for line in lines[:-1]:
            if _keep_line(repo, line, limit=limit):
                print(line)
        print(_filtered_present_count(repo, projected, limit=limit))
        return code

    for line in lines:
        if _keep_line(repo, line, limit=limit):
            print(line)
    return code
