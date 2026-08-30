"""Annotated-tag-aware ``rev-list --filter=object:type=tag`` adapter.

The existing metadata-only object inventory is commit-rooted and deliberately
omits annotated tag objects.  Git still treats annotated tags named by positive
revision arguments (and by ``--all`` refs) as reachable objects.  This adapter
adds that missing object family without changing commit/tree/blob traversal.

Phase273 intentionally scopes the new tag traversal to line-oriented and count
output.  Structured ``-z`` placement and ``--in-commit-order`` tag placement are
left to focused follow-up phases instead of silently emitting the wrong order.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import rev_list_filter_cli as _filter
from .objects import TagObject
from .plumbing import list_refs
from .rev_list import _split_range
from .revision import resolve_revision


_FILTER_PROVIDED = "--filter-provided-objects"
_FILTER_PRINT_OMITTED = "--filter-print-omitted"
_IN_COMMIT_ORDER = "--in-commit-order"
_SUPPORTED_MISSING = {
    "--missing=allow-promisor",
    "--missing=print",
    "--missing=print-info",
}


def _is_tag_filter(argv: Sequence[str]) -> bool:
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        return False
    if len(filters) != 1:
        raise ValueError("rev-list accepts exactly one --filter action in this phase")
    return filters[0].split("=", 1)[1] == "object:type=tag"


def _project(argv: Sequence[str]) -> list[str]:
    projected = [
        arg
        for arg in argv
        if not arg.startswith("--filter=")
        and arg not in {_FILTER_PROVIDED, _FILTER_PRINT_OMITTED}
    ]
    missing = [arg for arg in projected if arg.startswith("--missing=")]
    if len(missing) != 1 or missing[0] not in _SUPPORTED_MISSING:
        raise ValueError(
            "--filter=object:type=tag currently requires --missing=allow-promisor, print, or print-info"
        )
    return projected


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


def _print_tag_entries(entries: Sequence[Tuple[str, str]], *, no_object_names: bool) -> None:
    for oid, name in entries:
        print(oid if no_object_names else f"{oid} {name}")


def try_run_rev_list_object_type_tag(argv: Sequence[str]) -> Optional[int]:
    """Handle line/count ``object:type=tag`` without materialization."""

    if not _is_tag_filter(argv):
        return None
    if argv.count(_FILTER_PRINT_OMITTED) > 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")
    if "-z" in argv:
        raise ValueError(
            "rev-list --filter=object:type=tag with -z is not yet supported; tag NUL placement is not modelled"
        )
    if _IN_COMMIT_ORDER in argv:
        raise ValueError(
            "rev-list --filter=object:type=tag with --in-commit-order is not yet supported; ordered tag placement is not modelled"
        )
    if any(arg == "--disk-usage" or arg.startswith("--disk-usage=") for arg in argv):
        raise ValueError(
            "rev-list --filter=object:type=tag with --disk-usage is not yet supported"
        )

    filter_provided = _FILTER_PROVIDED in argv
    projected = _project(argv)
    repo = _filter._promisor._find_repo()
    parsed, provided, edges = _filter._object_type_context(
        repo,
        projected,
        filter_provided_objects=filter_provided,
    )
    tag_entries = _annotated_tag_entries(repo, parsed)
    code, lines = _filter._run_projected(projected)
    count_mode = "--count" in projected

    if count_mode:
        if not lines:
            raise RuntimeError("rev-list object:type=tag count projection produced no output")
        try:
            int(lines[-1])
        except ValueError as exc:
            raise RuntimeError(
                "rev-list object:type=tag count projection did not end with an integer"
            ) from exc
        for line in lines[:-1]:
            if _filter._keep_object_type_line(
                repo,
                line,
                requested="tag",
                provided=provided,
                edges=edges,
            ):
                print(line)
        base_count = _filter._object_type_present_count(
            repo,
            projected,
            requested="tag",
            parsed=parsed,
            provided=provided,
            edges=edges,
        )
        print(base_count + len(tag_entries))
        return code

    for line in lines:
        if _filter._keep_object_type_line(
            repo,
            line,
            requested="tag",
            provided=provided,
            edges=edges,
        ):
            print(line)
    _print_tag_entries(tag_entries, no_object_names=parsed["no_object_names"])
    return code
