"""Modern status rendering with Git-style porcelain v1/v2 output.

Phase 150 made the persistent multi-stage index authoritative for conflict
classification. Phase 151 builds on that model with porcelain-v2 metadata and
NUL framing for script-facing status consumers while keeping the historical
``Repository.status()`` dictionary untouched.
"""

from __future__ import annotations

import argparse
import stat as stat_mod
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .repo import Repository


_CONFLICT_CODES: Dict[Tuple[bool, bool, bool], str] = {
    (True, False, False): "DD",   # both deleted
    (False, True, False): "AU",   # added by us
    (True, True, False): "UD",    # deleted by them
    (False, False, True): "UA",   # added by them
    (True, False, True): "DU",    # deleted by us
    (False, True, True): "AA",    # both added
    (True, True, True): "UU",     # both modified
}

_CONFLICT_LABELS = {
    "DD": "both deleted",
    "AU": "added by us",
    "UD": "deleted by them",
    "UA": "added by them",
    "DU": "deleted by us",
    "AA": "both added",
    "UU": "both modified",
}

_ZERO_MODE = "000000"
_ZERO_OID = "0" * 64


@dataclass(frozen=True)
class UnmergedStatus:
    """One unmerged path classified from index-stage presence."""

    path: str
    code: str
    stages: Tuple[int, ...]


@dataclass(frozen=True)
class StatusRecord:
    """One porcelain-v1/short status record."""

    path: str
    code: str


def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def unmerged_status(repo: Repository) -> List[UnmergedStatus]:
    """Return Git-style unmerged classifications derived from stages 1/2/3."""
    paths = sorted({entry.path for entry in repo.index.stage_entries()})
    records: List[UnmergedStatus] = []
    for path in paths:
        present = tuple(repo.index.get(path, stage) is not None for stage in (1, 2, 3))
        code = _CONFLICT_CODES.get(present)
        if code is None:
            raise RuntimeError(f"invalid unmerged index stage combination for {path!r}: {present}")
        stages = tuple(stage for stage, exists in zip((1, 2, 3), present) if exists)
        records.append(UnmergedStatus(path=path, code=code, stages=stages))
    return records


def _kind_code(kind: str) -> str:
    if kind == "new file":
        return "A"
    if kind == "deleted":
        return "D"
    if kind == "modified":
        return "M"
    raise RuntimeError(f"unsupported status kind: {kind!r}")


def _normalized_status(repo: Repository, *, ignored: bool) -> Tuple[dict, List[UnmergedStatus]]:
    result = repo.status(ignored=ignored)
    unmerged = unmerged_status(repo)
    conflict_paths = {record.path for record in unmerged}

    result = dict(result)
    result["staged"] = [item for item in result["staged"] if item[1] not in conflict_paths]
    result["unstaged"] = [item for item in result["unstaged"] if item[1] not in conflict_paths]
    result["untracked"] = [path for path in result["untracked"] if path not in conflict_paths]
    if "ignored" in result:
        result["ignored"] = [path for path in result["ignored"] if path not in conflict_paths]
    result["conflicts"] = sorted(conflict_paths)
    result["unmerged"] = unmerged
    return result, unmerged


def _tracked_codes(result: dict, unmerged: Sequence[UnmergedStatus]) -> Dict[str, str]:
    codes: Dict[str, List[str]] = {}
    for kind, path in result["staged"]:
        codes.setdefault(path, [" ", " "])[0] = _kind_code(kind)
    for kind, path in result["unstaged"]:
        codes.setdefault(path, [" ", " "])[1] = _kind_code(kind)
    for record in unmerged:
        codes[record.path] = [record.code[0], record.code[1]]
    return {path: "".join(value) for path, value in codes.items()}


def status_records(repo: Repository, *, ignored: bool = False) -> List[StatusRecord]:
    """Return porcelain-v1 records in Git's tracked/untracked/ignored groups."""
    result, unmerged = _normalized_status(repo, ignored=ignored)
    tracked = _tracked_codes(result, unmerged)
    records = [
        StatusRecord(path=path, code=tracked[path])
        for path in sorted(tracked)
    ]
    records.extend(StatusRecord(path=path, code="??") for path in sorted(result["untracked"]))
    if ignored:
        records.extend(
            StatusRecord(path=path, code="!!")
            for path in sorted(result.get("ignored", []))
        )
    return records


def _branch_header(result: dict) -> str:
    branch = result.get("branch") or "HEAD"
    upstream = result.get("upstream")
    if not isinstance(upstream, dict):
        return f"## {branch}"

    upstream_name = upstream.get("upstream")
    if not upstream_name:
        return f"## {branch}"

    details: List[str] = []
    ahead = int(upstream.get("ahead") or 0)
    behind = int(upstream.get("behind") or 0)
    if ahead:
        details.append(f"ahead {ahead}")
    if behind:
        details.append(f"behind {behind}")
    suffix = f" [{', '.join(details)}]" if details else ""
    return f"## {branch}...{upstream_name}{suffix}"


def _quote_path(path: str) -> str:
    """Quote a pathname using Git's C-style byte escaping when needed."""
    raw = path.encode("utf-8")
    needs_quote = any(byte < 0x20 or byte >= 0x7F or byte in {0x22, 0x5C} for byte in raw)
    if not needs_quote:
        return path

    named = {
        0x07: "\\a",
        0x08: "\\b",
        0x09: "\\t",
        0x0A: "\\n",
        0x0B: "\\v",
        0x0C: "\\f",
        0x0D: "\\r",
        0x22: '\\"',
        0x5C: "\\\\",
    }
    parts: List[str] = ['"']
    for byte in raw:
        if byte in named:
            parts.append(named[byte])
        elif 0x20 <= byte < 0x7F:
            parts.append(chr(byte))
        else:
            parts.append(f"\\{byte:03o}")
    parts.append('"')
    return "".join(parts)


def _emit(lines: Sequence[str], *, zero: bool) -> None:
    if not lines:
        return
    separator = "\x00" if zero else "\n"
    sys.stdout.write(separator.join(lines) + separator)


def _print_short(repo: Repository, *, branch: bool, ignored: bool, zero: bool) -> None:
    result, _unmerged = _normalized_status(repo, ignored=ignored)
    lines: List[str] = []
    if branch:
        lines.append(_branch_header(result))
    for record in status_records(repo, ignored=ignored):
        path = record.path if zero else _quote_path(record.path)
        lines.append(f"{record.code} {path}")
    _emit(lines, zero=zero)


def _worktree_mode(repo: Repository, path: str, *, indexed: bool = True) -> str:
    if not indexed:
        return _ZERO_MODE
    target = repo.worktree / path
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        return _ZERO_MODE
    if stat_mod.S_ISLNK(mode):
        return "120000"
    if stat_mod.S_ISREG(mode):
        return "100755" if mode & stat_mod.S_IXUSR else "100644"
    if stat_mod.S_ISDIR(mode):
        entry = repo.index.get(path, 0)
        if entry is not None and entry.mode == "160000":
            return "160000"
    return _ZERO_MODE


def _submodule_field(*modes: str) -> str:
    return "S..." if "160000" in modes else "N..."


def _head_entries(repo: Repository) -> Dict[str, Tuple[str, str]]:
    head = repo.refs.resolve_head()
    return repo._commit_tree_entries(head) if head else {}


def _porcelain_v2_headers(repo: Repository, result: dict) -> List[str]:
    head_oid = repo.refs.resolve_head()
    branch = repo.refs.current_branch()
    lines = [
        f"# branch.oid {head_oid or '(initial)'}",
        f"# branch.head {branch if branch is not None else '(detached)'}",
    ]
    upstream = result.get("upstream")
    if isinstance(upstream, dict) and upstream.get("upstream"):
        lines.append(f"# branch.upstream {upstream['upstream']}")
        if head_oid:
            lines.append(
                f"# branch.ab +{int(upstream.get('ahead') or 0)} "
                f"-{int(upstream.get('behind') or 0)}"
            )
    return lines


def _porcelain_v2_ordinary(
    repo: Repository,
    path: str,
    code: str,
    head_entries: Dict[str, Tuple[str, str]],
    *,
    zero: bool,
) -> str:
    head_entry = head_entries.get(path)
    index_entry = repo.index.get(path, 0)

    head_oid = head_entry[0] if head_entry else _ZERO_OID
    head_mode = head_entry[1] if head_entry else _ZERO_MODE
    index_oid = index_entry.sha if index_entry else _ZERO_OID
    index_mode = index_entry.mode if index_entry else _ZERO_MODE
    worktree_mode = _worktree_mode(repo, path, indexed=index_entry is not None)
    xy = code.replace(" ", ".")
    sub = _submodule_field(head_mode, index_mode, worktree_mode)
    rendered_path = path if zero else _quote_path(path)
    return (
        f"1 {xy} {sub} {head_mode} {index_mode} {worktree_mode} "
        f"{head_oid} {index_oid} {rendered_path}"
    )


def _porcelain_v2_unmerged(repo: Repository, record: UnmergedStatus, *, zero: bool) -> str:
    entries = [repo.index.get(record.path, stage) for stage in (1, 2, 3)]
    modes = [entry.mode if entry is not None else _ZERO_MODE for entry in entries]
    oids = [entry.sha if entry is not None else _ZERO_OID for entry in entries]
    worktree_mode = _worktree_mode(repo, record.path)
    sub = _submodule_field(*modes, worktree_mode)
    rendered_path = record.path if zero else _quote_path(record.path)
    return (
        f"u {record.code} {sub} {modes[0]} {modes[1]} {modes[2]} "
        f"{worktree_mode} {oids[0]} {oids[1]} {oids[2]} {rendered_path}"
    )


def porcelain_v2_records(
    repo: Repository,
    *,
    branch: bool = False,
    ignored: bool = False,
    zero: bool = False,
) -> List[str]:
    """Build porcelain-v2 records, including optional branch headers."""
    result, unmerged = _normalized_status(repo, ignored=ignored)
    tracked_codes = _tracked_codes(result, unmerged)
    unmerged_by_path = {record.path: record for record in unmerged}
    head_entries = _head_entries(repo)

    lines: List[str] = []
    if branch:
        lines.extend(_porcelain_v2_headers(repo, result))

    for path in sorted(tracked_codes):
        conflict = unmerged_by_path.get(path)
        if conflict is not None:
            lines.append(_porcelain_v2_unmerged(repo, conflict, zero=zero))
        else:
            lines.append(
                _porcelain_v2_ordinary(
                    repo,
                    path,
                    tracked_codes[path],
                    head_entries,
                    zero=zero,
                )
            )

    for path in sorted(result["untracked"]):
        lines.append(f"? {path if zero else _quote_path(path)}")
    if ignored:
        for path in sorted(result.get("ignored", [])):
            lines.append(f"! {path if zero else _quote_path(path)}")
    return lines


def _print_porcelain_v2(repo: Repository, *, branch: bool, ignored: bool, zero: bool) -> None:
    _emit(
        porcelain_v2_records(repo, branch=branch, ignored=ignored, zero=zero),
        zero=zero,
    )


def _print_full(repo: Repository, *, ignored: bool) -> None:
    result, unmerged = _normalized_status(repo, ignored=ignored)

    branch = result["branch"] or "HEAD (detached)"
    print(f"On branch {branch}")
    if result.get("operation"):
        print(f"You are currently in a {result['operation']} operation.")
    print()

    keys = ("staged", "unstaged", "untracked", "ignored") if ignored else (
        "staged",
        "unstaged",
        "untracked",
    )
    if not unmerged and not any(result.get(key) for key in keys):
        print("nothing to commit, working tree clean")
        return

    if unmerged:
        print("Unmerged paths:")
        for record in unmerged:
            print(f"\t{_CONFLICT_LABELS[record.code]}:\t{record.path}")
        print()

    if result["staged"]:
        print("Changes to be committed:")
        for kind, path in result["staged"]:
            print(f"\t{kind}:\t{path}")
        print()

    if result["unstaged"]:
        print("Changes not staged for commit:")
        for kind, path in result["unstaged"]:
            print(f"\t{kind}:\t{path}")
        print()

    if result["untracked"]:
        print("Untracked files:")
        for path in result["untracked"]:
            print(f"\t{path}")
        print()

    if ignored and result.get("ignored"):
        print("Ignored files:")
        for path in result["ignored"]:
            print(f"\t{path}")
        print()


def run_status(argv: Sequence[str]) -> int:
    """Run modern status output while preserving the legacy visible surface."""
    parser = argparse.ArgumentParser(
        prog="pygit status",
        description="Show working-tree status with Git-compatible porcelain output.",
    )
    parser.add_argument("-s", "--short", action="store_true", help="show short-format status")
    parser.add_argument(
        "--porcelain",
        nargs="?",
        const="v1",
        choices=("1", "v1", "2", "v2"),
        metavar="VERSION",
        help="machine-readable porcelain v1 or v2 output",
    )
    parser.add_argument("-b", "--branch", action="store_true", help="show branch/upstream information")
    parser.add_argument("--ignored", action="store_true", help="show ignored files")
    parser.add_argument(
        "-z",
        dest="null",
        action="store_true",
        help="terminate machine-readable records with NUL; implies porcelain v1 when unspecified",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    porcelain: Optional[str] = args.porcelain
    if args.null and porcelain is None and not args.short:
        porcelain = "v1"

    if porcelain in {"2", "v2"}:
        _print_porcelain_v2(repo, branch=args.branch, ignored=args.ignored, zero=args.null)
    elif args.short or porcelain in {"1", "v1"}:
        _print_short(repo, branch=args.branch, ignored=args.ignored, zero=args.null)
    else:
        _print_full(repo, ignored=args.ignored)
    return 0
