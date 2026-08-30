"""Annotated-tag-aware ``rev-list object:type`` composition.

The existing metadata-only object inventory is commit-rooted and deliberately
omits annotated tag objects. Git nevertheless walks annotated tags named by
positive revision arguments (and by ``--all`` refs), and those tag objects are
also "provided objects" that bypass object filters unless
``--filter-provided-objects`` is requested.

Phase274 adds that missing object family for line-oriented and count output.
Phase277 extends the same composition to Git 2.55's structured ``-z`` protocol:
provided tag names use the normal ``path=<name>`` metadata token, present local
identities remain SHA-256, and the mature NUL renderer still owns missing,
boundary, and count framing.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from typing import Optional, Sequence, Tuple

from . import rev_list_filter_cli as _filter
from . import rev_list_nul_cli as _nul
from .objects import TagObject
from .plumbing import list_refs
from .rev_list import _split_range
from .revision import resolve_revision


_FILTER_PROVIDED = "--filter-provided-objects"
_FILTER_PRINT_OMITTED = "--filter-print-omitted"
_IN_COMMIT_ORDER = "--in-commit-order"
_SUPPORTED_OBJECT_TYPES = {"commit", "tree", "blob", "tag"}
_SUPPORTED_MISSING = {
    "--missing=allow-promisor",
    "--missing=print",
    "--missing=print-info",
}


def _requested_object_type(argv: Sequence[str]) -> Optional[str]:
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        return None
    if len(filters) != 1:
        raise ValueError("rev-list accepts exactly one --filter action in this phase")
    spec = filters[0].split("=", 1)[1]
    if not spec.startswith("object:type="):
        return None
    requested = spec.split("=", 1)[1]
    return requested if requested in _SUPPORTED_OBJECT_TYPES else None


def _project_line(argv: Sequence[str], *, requested: str) -> list[str]:
    projected = [
        arg
        for arg in argv
        if not arg.startswith("--filter=")
        and arg not in {_FILTER_PROVIDED, _FILTER_PRINT_OMITTED}
    ]
    missing = [arg for arg in projected if arg.startswith("--missing=")]
    if len(missing) != 1 or missing[0] not in _SUPPORTED_MISSING:
        raise ValueError(
            f"--filter=object:type={requested} currently requires "
            "--missing=allow-promisor, print, or print-info"
        )
    return projected


def _project_nul(argv: Sequence[str]) -> list[str]:
    """Project annotated-tag filtering onto the mature structured renderer."""

    projected = [
        arg
        for arg in argv
        if not arg.startswith("--filter=")
        and arg not in {_FILTER_PROVIDED, _FILTER_PRINT_OMITTED}
    ]
    missing = [arg for arg in projected if arg.startswith("--missing=")]
    if len(missing) > 1:
        raise ValueError("rev-list accepts exactly one --missing action")
    if missing and missing[0] not in _SUPPORTED_MISSING:
        raise ValueError(
            "--filter=object:type with -z supports --missing=allow-promisor, print, or print-info"
        )
    return projected


def _parse_projection(argv: Sequence[str]):
    """Parse selection independent of framing so tag roots can be discovered."""

    projected = [
        arg
        for arg in argv
        if not arg.startswith("--filter=")
        and arg not in {_FILTER_PROVIDED, _FILTER_PRINT_OMITTED, "-z", _IN_COMMIT_ORDER}
        and not (arg == "--disk-usage" or arg.startswith("--disk-usage="))
    ]
    if not any(arg.startswith("--missing=") for arg in projected):
        projected.append("--missing=allow-promisor")
    return _filter._parse_inventory_request(projected)


def _positive_object_expressions(repo, parsed) -> Tuple[str, ...]:
    """Return positive object-ish expressions whose tag chains are reachable."""

    revisions = tuple(parsed["revisions"])
    result: list[str] = []
    symmetric = [token for token in revisions if "..." in token]
    if symmetric:
        if len(revisions) != 1:
            raise ValueError("a symmetric A...B range cannot be mixed with other revisions")
        left, right = _split_range(symmetric[0], "...")
        result.extend((left, right))
    else:
        for token in revisions:
            if token.startswith("^"):
                continue
            if ".." in token:
                _left, right = _split_range(token, "..")
                result.append(right)
            else:
                result.append(token)

    if parsed["all_refs"]:
        result.extend(refname for _oid, refname in list_refs(repo))
    if not revisions and not parsed["all_refs"]:
        result.append("HEAD")
    return tuple(result)


def _annotated_tag_entries(repo, parsed) -> Tuple[Tuple[str, str], ...]:
    """Collect positive annotated-tag chains in Git-style root order."""

    output: list[tuple[str, str]] = []
    emitted: set[str] = set()
    for expression in _positive_object_expressions(repo, parsed):
        oid = resolve_revision(repo, expression).lower()
        chain_seen: set[str] = set()
        while True:
            if oid in chain_seen:
                raise RuntimeError(f"Tag cycle while traversing {expression!r}")
            chain_seen.add(oid)
            obj = repo.store.read(oid)
            if not isinstance(obj, TagObject):
                break
            if oid not in emitted:
                emitted.add(oid)
                output.append((oid, obj.tag_name))
            oid = obj.target_sha.lower()
    return tuple(output)


def _tag_lines(entries: Sequence[Tuple[str, str]], *, no_object_names: bool) -> list[str]:
    return [oid if no_object_names else f"{oid} {name}" for oid, name in entries]


def _is_edge_or_provided_commit(repo, line: str, *, provided, edges) -> bool:
    prefix, oid = _filter._line_oid(line)
    if prefix == "-" and oid in edges:
        return True
    if prefix != "" or oid not in provided:
        return False
    return _filter._local_type(repo, oid) == "commit"


def _compose_lines(
    repo,
    lines: Sequence[str],
    *,
    requested: str,
    provided,
    edges,
    tag_lines: Sequence[str],
) -> list[str]:
    kept = [
        line
        for line in lines
        if _filter._keep_object_type_line(
            repo,
            line,
            requested=requested,
            provided=provided,
            edges=edges,
        )
    ]
    if not tag_lines:
        return kept
    if requested == "commit":
        return [*kept, *tag_lines]

    insert_at = 0
    while insert_at < len(kept) and _is_edge_or_provided_commit(
        repo,
        kept[insert_at],
        provided=provided,
        edges=edges,
    ):
        insert_at += 1
    return [*kept[:insert_at], *tag_lines, *kept[insert_at:]]


def _parse_nul_records(raw: str) -> list[tuple[str, ...]]:
    """Parse a present/missing NUL stream into opaque structured records."""

    if not raw:
        return []
    if not raw.endswith("\0"):
        raise RuntimeError("rev-list NUL projection did not end at a record boundary")

    records: list[tuple[str, ...]] = []
    current: list[str] = []
    for field in raw[:-1].split("\0"):
        # OIDs never contain '='; every metadata field does. This is the same
        # record-boundary rule documented by Git for rev-list -z.
        if "=" not in field:
            if current:
                records.append(tuple(current))
            current = [field]
            continue
        if not current:
            raise RuntimeError("rev-list NUL projection started with metadata")
        current.append(field)
    if current:
        records.append(tuple(current))
    return records


def _tag_nul_records(
    entries: Sequence[Tuple[str, str]],
    *,
    no_object_names: bool,
) -> list[tuple[str, ...]]:
    records: list[tuple[str, ...]] = []
    for oid, name in entries:
        if no_object_names:
            records.append((oid.lower(),))
        else:
            records.append((oid.lower(), f"path={name}"))
    return records


def _record_is_provided_commit(record: Sequence[str], *, provided) -> bool:
    return bool(record) and record[0].lower() in provided and "boundary=yes" not in record[1:]


def _compose_nul_records(
    records: Sequence[tuple[str, ...]],
    *,
    requested: str,
    provided,
    tag_records: Sequence[tuple[str, ...]],
) -> list[tuple[str, ...]]:
    """Insert tag records at the same object phase as line-oriented Git output."""

    kept = list(records)
    if not tag_records:
        return kept
    if requested == "commit":
        return [*kept, *tag_records]

    insert_at = 0
    while insert_at < len(kept) and _record_is_provided_commit(
        kept[insert_at], provided=provided
    ):
        insert_at += 1
    return [*kept[:insert_at], *tag_records, *kept[insert_at:]]


def _write_nul_records(records: Sequence[Sequence[str]]) -> None:
    for record in records:
        sys.stdout.write("\0".join(record) + "\0")


def _split_nul_count(raw: str) -> tuple[str, int]:
    """Return preserved NUL diagnostics plus Git's final newline count."""

    if "\0" in raw:
        prefix, count_line = raw.rsplit("\0", 1)
        prefix += "\0"
    else:
        prefix, count_line = "", raw
    if not count_line.endswith("\n"):
        raise RuntimeError("rev-list NUL count projection did not end with a newline integer")
    try:
        value = int(count_line[:-1])
    except ValueError as exc:
        raise RuntimeError("rev-list NUL count projection did not end with an integer") from exc
    return prefix, value


def _run_nul(
    repo,
    argv: Sequence[str],
    *,
    requested: str,
    discovery,
    emitted_tags: Sequence[Tuple[str, str]],
    filter_provided: bool,
) -> int:
    """Render annotated-tag-aware object:type output through Phase270 framing."""

    projected = _project_nul(argv)
    provided = (
        frozenset()
        if filter_provided
        else _filter._provided_commit_roots(repo, discovery)
    )

    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _nul.try_run_rev_list_nul(
            projected,
            object_type=requested,
            provided_oids=provided,
        )
    if code is None:
        raise RuntimeError("NUL rev-list adapter declined annotated-tag projection")
    raw = capture.getvalue()

    if "--count" in projected:
        prefix, base_count = _split_nul_count(raw)
        sys.stdout.write(prefix)
        print(base_count + len(emitted_tags))
        return code

    records = _parse_nul_records(raw)
    tag_records = _tag_nul_records(
        emitted_tags,
        no_object_names=discovery["no_object_names"],
    )
    _write_nul_records(
        _compose_nul_records(
            records,
            requested=requested,
            provided=provided,
            tag_records=tag_records,
        )
    )
    return code


def try_run_rev_list_object_type_tag(argv: Sequence[str]) -> Optional[int]:
    """Compose annotated-tag roots with supported line/count/NUL object filters."""

    requested = _requested_object_type(argv)
    if requested is None:
        return None
    if argv.count(_FILTER_PRINT_OMITTED) > 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")

    repo = _filter._promisor._find_repo()
    discovery = _parse_projection(argv)
    discovered_tags = _annotated_tag_entries(repo, discovery)
    filter_provided = _FILTER_PROVIDED in argv
    emitted_tags = discovered_tags if requested == "tag" or not filter_provided else ()

    # Existing commit/tree/blob paths remain authoritative when there are no
    # annotated-tag objects to add. This keeps Phase277 narrowly compositional.
    if requested != "tag" and not emitted_tags:
        return None

    if _IN_COMMIT_ORDER in argv:
        raise ValueError(
            f"rev-list object:type={requested} with annotated tags and --in-commit-order is not yet supported; "
            "ordered tag placement is modelled on the independent Phase273 line"
        )
    if any(arg == "--disk-usage" or arg.startswith("--disk-usage=") for arg in argv):
        raise ValueError(
            f"rev-list object:type={requested} with --disk-usage is not yet supported; "
            "annotated-tag disk accounting is not modelled"
        )
    if "-z" in argv:
        return _run_nul(
            repo,
            argv,
            requested=requested,
            discovery=discovery,
            emitted_tags=emitted_tags,
            filter_provided=filter_provided,
        )

    projected = _project_line(argv, requested=requested)
    parsed, provided, edges = _filter._object_type_context(
        repo,
        projected,
        filter_provided_objects=filter_provided,
    )
    code, lines = _filter._run_projected(projected)
    count_mode = "--count" in projected

    if count_mode:
        if not lines:
            raise RuntimeError("rev-list object:type count projection produced no output")
        try:
            int(lines[-1])
        except ValueError as exc:
            raise RuntimeError(
                "rev-list object:type count projection did not end with an integer"
            ) from exc
        for line in _compose_lines(
            repo,
            lines[:-1],
            requested=requested,
            provided=provided,
            edges=edges,
            tag_lines=(),
        ):
            print(line)
        base_count = _filter._object_type_present_count(
            repo,
            projected,
            requested=requested,
            parsed=parsed,
            provided=provided,
            edges=edges,
        )
        print(base_count + len(emitted_tags))
        return code

    composed = _compose_lines(
        repo,
        lines,
        requested=requested,
        provided=provided,
        edges=edges,
        tag_lines=_tag_lines(emitted_tags, no_object_names=parsed["no_object_names"]),
    )
    for line in composed:
        print(line)
    return code
