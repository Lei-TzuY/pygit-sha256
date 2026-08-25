"""Unified revision/object-ish parsing for low-level plumbing commands.

The resolver is intentionally read-only. It understands pygit's SHA-256
object IDs, loose/packed refs, abbreviated IDs, numeric reflog selectors,
commit ancestry expressions, ``REV:path`` tree walks, and Git-style
``^{type}`` peeling without touching the index or worktree.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

from .objects import BlobObject, CommitObject, GitObject, TagObject, TreeObject
from .plumbing import list_refs
from .repo import Repository


_HEX = frozenset("0123456789abcdef")
_ZERO_OID = "0" * 64
_PEEL_RE = re.compile(r"^(.*)\^\{([^{}]*)\}$", re.DOTALL)
_REFLOG_SELECTOR_RE = re.compile(r"^(.*)@\{([^{}]*)\}$", re.DOTALL)


@dataclass(frozen=True)
class RevisionResult:
    expression: str
    oid: str
    symbolic_name: Optional[str] = None


def _is_hex(value: str) -> bool:
    return bool(value) and all(char in _HEX for char in value.lower())


def _all_prefix_matches(repo: Repository, prefix: str) -> List[str]:
    if len(prefix) < 4 or len(prefix) > 64 or not _is_hex(prefix):
        return []
    lowered = prefix.lower()
    return [oid for oid in repo.store.all_shas() if oid.startswith(lowered)]


def resolve_abbreviation(repo: Repository, prefix: str) -> Optional[str]:
    """Resolve a unique 4+ SHA-256 prefix across loose and packed objects."""
    if len(prefix) == 64 and _is_hex(prefix):
        lowered = prefix.lower()
        return lowered if repo.store.exists(lowered) else None
    matches = _all_prefix_matches(repo, prefix)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous short SHA prefix {prefix!r}: matches {len(matches)} objects"
        )
    return None


def _resolve_reflog_selector(repo: Repository, expression: str) -> Optional[str]:
    """Resolve ``REF@{N}`` using the strict Phase 77 reflog reader."""
    match = _REFLOG_SELECTOR_RE.fullmatch(expression)
    if match is None:
        return None

    ref, raw_index = match.groups()
    if not ref:
        raise ValueError(f"Invalid reflog selector: {expression!r}")
    if not raw_index or not raw_index.isdigit():
        raise ValueError(
            f"Only non-negative numeric reflog selectors are supported: {expression!r}"
        )

    index = int(raw_index)
    # Local import keeps revision parsing independent from the application
    # routing layer and avoids introducing a module-import cycle through repo.py.
    from .reflog_show import show_reflog

    entries = show_reflog(repo, ref, max_count=index + 1)
    if len(entries) <= index:
        raise KeyError(f"Reflog selector is out of range: {expression!r}")

    oid = entries[index].new_oid.lower()
    if oid == _ZERO_OID:
        raise KeyError(f"Reflog selector names the zero object: {expression!r}")
    if not repo.store.exists(oid):
        raise KeyError(
            f"Reflog selector {expression!r} names missing object {oid}"
        )
    return oid


def _resolve_direct(repo: Repository, expression: str) -> str:
    reflog_oid = _resolve_reflog_selector(repo, expression)
    if reflog_oid is not None:
        return reflog_oid

    oid = repo.refs.resolve(expression)
    if oid and repo.store.exists(oid):
        return oid.lower()
    oid = resolve_abbreviation(repo, expression)
    if oid:
        return oid
    raise KeyError(f"Unknown object: {expression!r}")


def _peel_tags(repo: Repository, oid: str, display: str) -> str:
    current = oid
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle while resolving {display!r}")
        seen.add(current)
        obj = repo.store.read(current)
        if not isinstance(obj, TagObject):
            return current
        current = obj.target_sha.lower()


def _as_commit(repo: Repository, oid: str, display: str) -> str:
    peeled = _peel_tags(repo, oid, display)
    obj = repo.store.read(peeled)
    if not isinstance(obj, CommitObject):
        raise RuntimeError(f"Revision {display!r} does not name a commit")
    return peeled


def _shallow_boundaries(repo: Repository) -> Set[str]:
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    result: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        oid = line.strip().lower()
        if len(oid) == 64 and _is_hex(oid):
            result.add(oid)
    return result


def _resolve_commit_expression(repo: Repository, expression: str) -> str:
    split_at = len(expression)
    for marker in ("~", "^"):
        pos = expression.find(marker)
        if pos >= 0:
            split_at = min(split_at, pos)

    if split_at == len(expression):
        return _resolve_direct(repo, expression)

    base = expression[:split_at]
    suffix = expression[split_at:]
    if not base:
        raise ValueError(f"Invalid revision: {expression!r}")

    oid = _as_commit(repo, _resolve_direct(repo, base), expression)
    shallow = _shallow_boundaries(repo)

    while suffix:
        operator = suffix[0]
        if operator not in {"~", "^"}:
            raise ValueError(f"Invalid revision suffix in {expression!r}")
        suffix = suffix[1:]

        digits = []
        while suffix and suffix[0].isdigit():
            digits.append(suffix[0])
            suffix = suffix[1:]
        number = int("".join(digits)) if digits else 1

        if operator == "~":
            for _ in range(number):
                if oid in shallow:
                    raise ValueError(
                        f"Revision {expression!r} walks beyond a shallow boundary"
                    )
                commit = repo.store.read(oid)
                if not isinstance(commit, CommitObject) or not commit.parents:
                    raise ValueError(
                        f"Revision {expression!r} walks past a root commit"
                    )
                oid = commit.parents[0].lower()
            continue

        if number == 0:
            continue
        if oid in shallow:
            raise ValueError(
                f"Revision {expression!r} walks beyond a shallow boundary"
            )
        commit = repo.store.read(oid)
        if not isinstance(commit, CommitObject):
            raise RuntimeError(f"Revision {expression!r} is not a commit")
        if number > len(commit.parents):
            raise ValueError(
                f"Revision {expression!r} requests parent {number}, "
                f"but commit has {len(commit.parents)} parent(s)"
            )
        oid = commit.parents[number - 1].lower()

    return oid


def _treeish_oid(repo: Repository, oid: str, display: str) -> str:
    current = oid
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle while resolving {display!r}")
        seen.add(current)
        obj = repo.store.read(current)
        if isinstance(obj, TreeObject):
            return current
        if isinstance(obj, CommitObject):
            tree = repo.store.read(obj.tree)
            if not isinstance(tree, TreeObject):
                raise RuntimeError(f"Commit {current} references a non-tree root")
            return obj.tree.lower()
        if isinstance(obj, TagObject):
            current = obj.target_sha.lower()
            continue
        raise RuntimeError(f"Object {display!r} is not a tree-ish")


def _resolve_tree_path(repo: Repository, expression: str) -> str:
    base, path = expression.split(":", 1)
    if not base:
        raise ValueError("index-style :path expressions are not supported")

    base_oid = _resolve_commit_expression(repo, base)
    tree_oid = _treeish_oid(repo, base_oid, expression)
    if path == "":
        return tree_oid
    if path.startswith("/") or "\x00" in path:
        raise ValueError(f"Invalid object path: {expression!r}")

    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Invalid object path: {expression!r}")

    current_oid = tree_oid
    for index, part in enumerate(parts):
        tree = repo.store.read(current_oid)
        if not isinstance(tree, TreeObject):
            raise KeyError(f"Path component before {part!r} is not a directory")
        entry = next((item for item in tree.entries if item.name == part), None)
        if entry is None:
            raise KeyError(f"Path {path!r} does not exist in {base!r}")
        if index == len(parts) - 1:
            return entry.sha.lower()
        child = repo.store.read(entry.sha)
        if not isinstance(child, TreeObject):
            raise KeyError(f"Path component {part!r} is not a directory")
        current_oid = entry.sha.lower()

    raise AssertionError("unreachable")


def _apply_peel_selector(repo: Repository, oid: str, selector: str, display: str) -> str:
    if selector == "object":
        repo.store.read(oid)
        return oid

    if selector == "tag":
        obj = repo.store.read(oid)
        if not isinstance(obj, TagObject):
            raise RuntimeError(f"Revision {display!r} is not a tag object")
        return oid

    peeled = _peel_tags(repo, oid, display)
    obj = repo.store.read(peeled)

    if selector == "":
        return peeled
    if selector == "commit":
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Revision {display!r} cannot be peeled to commit")
        return peeled
    if selector == "tree":
        if isinstance(obj, TreeObject):
            return peeled
        if isinstance(obj, CommitObject):
            tree = repo.store.read(obj.tree)
            if not isinstance(tree, TreeObject):
                raise RuntimeError(f"Commit {peeled} references a non-tree root")
            return obj.tree.lower()
        raise RuntimeError(f"Revision {display!r} cannot be peeled to tree")
    if selector == "blob":
        if not isinstance(obj, BlobObject):
            raise RuntimeError(f"Revision {display!r} cannot be peeled to blob")
        return peeled

    raise ValueError(f"Unsupported peel type in {display!r}: {selector!r}")


def resolve_revision(repo: Repository, expression: str) -> str:
    """Resolve a Git-style object-ish expression to one full SHA-256 object ID."""
    if not expression:
        raise ValueError("empty revision expression")

    peel = _PEEL_RE.match(expression)
    if peel:
        base, selector = peel.groups()
        if not base:
            raise ValueError(f"Invalid peel expression: {expression!r}")
        oid = resolve_revision(repo, base)
        return _apply_peel_selector(repo, oid, selector, expression)

    if ":" in expression:
        return _resolve_tree_path(repo, expression)
    return _resolve_commit_expression(repo, expression)


def symbolic_refname(repo: Repository, expression: str) -> Optional[str]:
    if not expression or any(marker in expression for marker in ("~", "^", ":", "@{")):
        return None

    if expression == "HEAD":
        raw = repo.refs.get_head()
        if raw.startswith("ref: "):
            target = raw[5:].strip()
            return target if repo.refs.resolve(target) is not None else None
        return "HEAD"

    if expression.startswith("refs/"):
        return expression if repo.refs.resolve(expression) is not None else None
    if repo.refs.get_branch(expression) is not None:
        return f"refs/heads/{expression}"
    if repo.refs.get_tag(expression) is not None:
        return f"refs/tags/{expression}"
    if "/" in expression:
        remote, branch = expression.split("/", 1)
        if repo.refs.get_remote(remote, branch) is not None:
            return f"refs/remotes/{expression}"
    return None


def short_refname(refname: str) -> str:
    for prefix in ("refs/heads/", "refs/tags/", "refs/remotes/"):
        if refname.startswith(prefix):
            return refname[len(prefix) :]
    return refname


def abbreviate_oid(repo: Repository, oid: str, minimum: int = 12) -> str:
    if minimum < 4 or minimum > 64:
        raise ValueError("abbreviation length must be between 4 and 64")
    lowered = oid.lower()
    if not repo.store.exists(lowered):
        raise KeyError(f"Object not found: {oid}")

    shas = repo.store.all_shas()
    for width in range(minimum, 65):
        prefix = lowered[:width]
        if sum(candidate.startswith(prefix) for candidate in shas) == 1:
            return prefix
    return lowered


def namespace_refs(
    repo: Repository,
    namespace: str,
    pattern: Optional[str] = None,
) -> List[Tuple[str, str]]:
    prefixes = {
        "branches": "refs/heads/",
        "tags": "refs/tags/",
        "remotes": "refs/remotes/",
    }
    if namespace not in prefixes:
        raise ValueError(f"Unknown ref namespace: {namespace!r}")
    prefix = prefixes[namespace]
    match = pattern or "*"

    records = []
    for oid, refname in list_refs(repo):
        if not refname.startswith(prefix):
            continue
        short = refname[len(prefix) :]
        if fnmatch.fnmatchcase(short, match) or fnmatch.fnmatchcase(refname, match):
            records.append((oid, refname))
    return sorted(records, key=lambda item: item[1])


def glob_refs(repo: Repository, pattern: str) -> List[Tuple[str, str]]:
    candidate = pattern if pattern.startswith("refs/") else "refs/" + pattern
    if not any(char in candidate for char in "*?["):
        candidate = candidate.rstrip("/") + "/*"
    return sorted(
        [
            (oid, refname)
            for oid, refname in list_refs(repo)
            if fnmatch.fnmatchcase(refname, candidate)
        ],
        key=lambda item: item[1],
    )


def resolve_many(repo: Repository, expressions: Sequence[str]) -> List[RevisionResult]:
    results: List[RevisionResult] = []
    for expression in expressions:
        results.append(
            RevisionResult(
                expression=expression,
                oid=resolve_revision(repo, expression),
                symbolic_name=symbolic_refname(repo, expression),
            )
        )
    return results
