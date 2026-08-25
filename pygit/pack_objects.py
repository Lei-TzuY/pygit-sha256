"""Object selection and pack production for ``pygit pack-objects``.

The command deliberately targets pygit's educational SHA-256, non-delta pack
format.  Selection is separate from storage so callers can inspect the exact
object set before any output is produced.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Set, Tuple

from .objects import CommitObject, GitObject, TagObject, TreeObject
from .pack import PackWriter
from .plumbing import list_refs
from .repo import Repository
from .revision import resolve_revision


@dataclass(frozen=True)
class PackObjectsResult:
    """Result of one pack-objects operation."""

    object_count: int
    oids: Tuple[str, ...]
    pack_hash: str
    pack_data: Optional[bytes] = None
    pack_path: Optional[Path] = None
    idx_path: Optional[Path] = None


def _shallow_boundaries(repo: Repository) -> Set[str]:
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    result: Set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        oid = raw.strip().lower()
        if len(oid) == 64 and all(char in "0123456789abcdef" for char in oid):
            result.add(oid)
    return result


def reachable_objects(repo: Repository, roots: Iterable[str]) -> Set[str]:
    """Return every object reachable from *roots* using pygit's object graph.

    Commit parents are not traversed beyond entries listed in ``.pygit/shallow``.
    Missing referenced objects are reported as errors rather than silently
    producing an incomplete pack.
    """

    shallow = _shallow_boundaries(repo)
    seen: Set[str] = set()
    pending = [oid.lower() for oid in roots]

    while pending:
        oid = pending.pop()
        if oid in seen:
            continue
        obj = repo.store.read(oid)
        seen.add(oid)

        if isinstance(obj, CommitObject):
            pending.append(obj.tree.lower())
            if oid not in shallow:
                pending.extend(parent.lower() for parent in obj.parents)
        elif isinstance(obj, TreeObject):
            pending.extend(entry.sha.lower() for entry in obj.entries)
        elif isinstance(obj, TagObject):
            pending.append(obj.target_sha.lower())

    return seen


def select_pack_objects(
    repo: Repository,
    expressions: Sequence[str] = (),
    *,
    revs: bool = False,
    all_refs: bool = False,
) -> Tuple[str, ...]:
    """Resolve stdin-style expressions into a deterministic object set.

    Without ``revs`` each expression names exactly one object.  With ``revs``
    positive expressions are recursively expanded and ``^REV`` expressions
    subtract their complete reachable closure.  ``all_refs`` adds every local
    ref plus HEAD as positive roots and implies recursive traversal.
    """

    positives = []
    negatives = []
    for raw in expressions:
        expression = raw.strip()
        if not expression:
            continue
        if expression.startswith("^"):
            if not revs and not all_refs:
                raise ValueError("negative revisions require --revs or --all")
            if expression == "^":
                raise ValueError("empty negative revision")
            negatives.append(resolve_revision(repo, expression[1:]))
        else:
            positives.append(resolve_revision(repo, expression))

    if all_refs:
        positives.extend(oid for oid, _ in list_refs(repo, include_head=True))
        revs = True

    if not positives:
        raise ValueError("pack-objects requires at least one positive object or revision")

    if revs:
        selected = reachable_objects(repo, positives)
        if negatives:
            selected.difference_update(reachable_objects(repo, negatives))
    else:
        selected = set(positives)
        for oid in selected:
            repo.store.read(oid)

    return tuple(sorted(selected))


def _objects_for_pack(repo: Repository, oids: Sequence[str]) -> list[tuple[str, GitObject]]:
    objects = []
    for oid in oids:
        obj = repo.store.read(oid)
        actual = obj.hash()
        if actual != oid:
            raise ValueError(f"object {oid} re-serializes as {actual}")
        objects.append((oid, obj))
    return objects


def _pack_hash(pack_path: Path) -> str:
    data = pack_path.read_bytes()
    if len(data) < 32:
        raise ValueError("generated pack is truncated")
    return hashlib.sha256(data[-32:]).hexdigest()[:40]


def pack_objects(
    repo: Repository,
    expressions: Sequence[str] = (),
    *,
    revs: bool = False,
    all_refs: bool = False,
    output_prefix: Optional[Path] = None,
    stdout: bool = False,
) -> PackObjectsResult:
    """Select objects and create a pygit pack.

    ``stdout=True`` returns the binary pack in ``pack_data`` and creates no
    persistent files.  File mode requires ``output_prefix`` and writes the
    paired ``<prefix>-<hash>.pack`` / ``.idx`` files through :class:`PackWriter`.
    """

    if stdout and output_prefix is not None:
        raise ValueError("--stdout cannot be combined with an output prefix")
    if not stdout and output_prefix is None:
        raise ValueError("file output requires an output prefix")

    oids = select_pack_objects(repo, expressions, revs=revs, all_refs=all_refs)
    objects = _objects_for_pack(repo, oids)

    if stdout:
        with tempfile.TemporaryDirectory(prefix="pygit-pack-objects-") as temp:
            pack_path, _ = PackWriter(objects).write_pack_and_idx(Path(temp), "pack")
            data = pack_path.read_bytes()
            return PackObjectsResult(
                object_count=len(oids),
                oids=oids,
                pack_hash=_pack_hash(pack_path),
                pack_data=data,
            )

    assert output_prefix is not None
    prefix = Path(output_prefix)
    output_dir = prefix.parent if str(prefix.parent) else Path(".")
    name_prefix = prefix.name
    if not name_prefix:
        raise ValueError("output prefix must include a basename")
    pack_path, idx_path = PackWriter(objects).write_pack_and_idx(output_dir, name_prefix)
    return PackObjectsResult(
        object_count=len(oids),
        oids=oids,
        pack_hash=_pack_hash(pack_path),
        pack_path=pack_path,
        idx_path=idx_path,
    )
