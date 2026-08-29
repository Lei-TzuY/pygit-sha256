"""Machine-readable NUL framing for promisor-aware ``rev-list``.

Current Git defines ``-z`` as an object-record protocol rather than a simple
newline replacement. Each object id starts a record and optional metadata is
emitted as additional NUL-terminated ``token=value`` fields. In particular,
boundary and missing state move out of the textual ``-``/``?`` prefixes and
into ``boundary=yes`` / ``missing=yes`` metadata.

This adapter deliberately owns only the inventory-backed ``--missing`` path.
That keeps partial-clone traversal metadata-only, preserves pygit's local
SHA-256 object identity, and confines unresolved foreign SHA-1 identities to
records explicitly marked ``missing=yes``.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from . import rev_list_promisor_cli as _promisor
from .promisor_object_inventory import PromisorObjectInventoryEntry, promisor_object_inventory


def _missing_mode(argv: Sequence[str]) -> Optional[str]:
    missing = [arg for arg in argv if arg.startswith("--missing=")]
    if not missing:
        return None
    if len(missing) != 1:
        raise ValueError("rev-list accepts exactly one --missing action")
    mode = missing[0].split("=", 1)[1]
    if mode not in {"allow-promisor", "print", "print-info"}:
        return None
    return mode


def _parse(argv: Sequence[str]):
    if "-z" not in argv:
        return None

    mode = _missing_mode(argv)
    if mode is None:
        # Phase244 intentionally limits -z to the already metadata-only missing
        # traversal. Ordinary rev-list -z can be added independently without
        # weakening missing-object integrity semantics.
        return None

    if "--objects-edge" in argv:
        raise ValueError("rev-list -z is only compatible with --objects, --boundary, and --missing")
    if "--count" in argv:
        raise ValueError("rev-list -z is not compatible with --count")

    projected: list[str] = []
    for arg in argv:
        if arg == "-z":
            continue
        if arg == "--missing=print":
            projected.append("--missing=print-info")
        else:
            projected.append(arg)

    parsed = _promisor._parse_allow_promisor(projected)
    if parsed is None:
        raise RuntimeError("promisor rev-list parser declined -z projection")
    parsed = dict(parsed)
    parsed["nul_missing_mode"] = mode
    return parsed


def _emit_fields(*fields: str) -> None:
    """Emit one object's NUL-delimited record fields without path quoting."""

    sys.stdout.write("\0".join(fields) + "\0")


def _emit_present(
    entry: PromisorObjectInventoryEntry,
    *,
    no_object_names: bool,
) -> None:
    if entry.oid is None:
        raise RuntimeError("present inventory entry has no local SHA-256 identity")
    fields = [entry.oid.lower()]
    if not no_object_names and entry.path is not None:
        fields.append(f"path={entry.path}")
    _emit_fields(*fields)


def _emit_missing(entry: PromisorObjectInventoryEntry, *, mode: str) -> None:
    if mode == "allow-promisor":
        return
    if entry.native_oid is None:
        raise RuntimeError("missing inventory entry has no native object identity")

    fields = [entry.native_oid.lower(), "missing=yes"]
    if mode == "print-info":
        if entry.path is not None:
            fields.append(f"path={entry.path}")
        fields.append(f"type={entry.type_name}")
    _emit_fields(*fields)


def _emit_entries(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    mode: str,
    no_object_names: bool,
    skip_top_level_commits: bool,
) -> None:
    for entry in entries:
        if skip_top_level_commits and entry.type_name == "commit" and entry.path is None:
            continue
        if entry.missing:
            _emit_missing(entry, mode=mode)
        else:
            _emit_present(entry, no_object_names=no_object_names)


def try_run_rev_list_nul(argv: Sequence[str]) -> Optional[int]:
    """Handle ``rev-list -z --objects --missing=...`` without materialization.

    Present object records always start with a genuine local 64-hex SHA-256.
    An unresolved promise may start a record with its known native SHA-1 only
    when the same record contains ``missing=yes``. Paths are emitted verbatim as
    ``path=`` metadata, so newlines are preserved instead of quoted/truncated.
    """

    parsed = _parse(argv)
    if parsed is None:
        return None

    repo = _promisor._find_repo()
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

    entries = promisor_object_inventory(
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

    mode = parsed["nul_missing_mode"]
    if parsed["boundary"]:
        for oid, is_boundary in boundary_commits:
            if is_boundary:
                _emit_fields(oid.lower(), "boundary=yes")
            else:
                _emit_fields(oid.lower())
        _emit_entries(
            entries,
            mode=mode,
            no_object_names=parsed["no_object_names"],
            skip_top_level_commits=True,
        )
        return 0

    _emit_entries(
        entries,
        mode=mode,
        no_object_names=parsed["no_object_names"],
        skip_top_level_commits=False,
    )
    return 0
