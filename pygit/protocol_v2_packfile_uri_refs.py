"""Publish certified packfile-URI roots through a CAS ref transaction.

Phase323 consumes the read-only Phase322 root certificate and makes refs the
final mutable step of the external-pack fetch path. Every publication carries
an explicit expected old local SHA-256 value (or the all-zero local object id
for creation), is revalidated against the destination object store, and is
committed through pygit's existing transactional ``update-ref`` plumbing.

Canonical ``<ref>.lock`` files are held for the duration of the transaction so
native Git ref writers that follow the files backend locking convention cannot
race this publication path. The existing ref transaction still performs the
object existence/type checks, compare-and-swap verification, reflog handling,
and snapshot rollback.

Phase350 upgrades packfile-URI ref publication with a durability boundary. The
public publisher fsyncs every live ref and written reflog before releasing the
canonical target locks, then fsyncs the containing directory hierarchy after
lock removal. Successful return therefore means the final reference namespace
crossed an explicit durability fence instead of stopping at visible
``os.replace()``. The explicit ``*_durable`` spelling remains available for
callers that want to make that contract self-documenting.

Phase360 gives those canonical target-ref locks the same retained-descriptor,
inode-aware ownership discipline as the outer publication guards. Marker writes
are completed before fsync, ownership descriptors remain non-inheritable and
open through the ref/reflog durability fence, and cleanup unlinks a pathname only
when it still names the inode acquired by this publisher.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple

from .protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from .ref_query import check_ref_format
from .ref_transaction import RefUpdate, update_refs
from .refs import ZERO_SHA
from .repo import Repository

_HEX = frozenset("0123456789abcdef")
_REF_LOCK_MARKER = b"packfile-uri ref transaction\n"


@dataclass(frozen=True)
class _RefLockOwnership:
    fd: int
    device: int
    inode: int


_REF_LOCK_OWNERSHIP: dict[Path, _RefLockOwnership] = {}


@dataclass(frozen=True)
class PackfileUriRefPublication:
    """One certified native root to publish at a local reference.

    ``old_local_oid`` is mandatory. Use :data:`pygit.refs.ZERO_SHA` when the
    ref must not already exist; otherwise provide the exact current local
    SHA-256 object id. This makes every publication compare-and-swap rather
    than a blind overwrite.
    """

    native_oid: str
    old_local_oid: str


def _validate_local_oid(value: str, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a full local SHA-256 object id")
    lowered = value.lower()
    if any(ch not in _HEX for ch in lowered):
        raise ValueError(f"{field} must be hexadecimal")
    return lowered


def _validate_native_oid(value: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError("packfile-URI publication native id must be a full remote-native SHA-1")
    lowered = value.lower()
    if any(ch not in _HEX for ch in lowered):
        raise ValueError("packfile-URI publication native id must be hexadecimal")
    return lowered


def _validate_refname(refname: str) -> str:
    if not isinstance(refname, str) or not refname.startswith("refs/"):
        raise ValueError("packfile-URI publication requires a full refs/... name")
    return check_ref_format(refname)


def _lock_path(repo: Repository, refname: str) -> Path:
    target = (repo.pygit_dir / refname).resolve()
    root = repo.pygit_dir.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"invalid reference name: {refname!r}") from exc
    return target.with_name(target.name + ".lock")


def _write_ref_lock_marker(fd: int) -> None:
    """Completely write the target-ref lock marker before durability is claimed."""

    remaining = memoryview(_REF_LOCK_MARKER)
    while remaining:
        try:
            written = os.write(fd, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("packfile-URI ref lock marker write made no progress")
        remaining = remaining[written:]


def _initialize_ref_lock(lock: Path) -> _RefLockOwnership:
    """Exclusively create, initialize, fsync, and retain one target-ref lock."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    fd = os.open(lock, flags, 0o666)
    initialized = False
    try:
        os.set_inheritable(fd, False)
        _write_ref_lock_marker(fd)
        os.fsync(fd)
        stat_result = os.fstat(fd)
        ownership = _RefLockOwnership(
            fd=fd,
            device=stat_result.st_dev,
            inode=stat_result.st_ino,
        )
        initialized = True
        return ownership
    finally:
        if not initialized:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                lock.unlink()
            except FileNotFoundError:
                pass


def _release_locks(locks: Sequence[Path]) -> None:
    """Release only target-ref lock pathnames that still name owned inodes."""

    for lock in reversed(tuple(locks)):
        ownership = _REF_LOCK_OWNERSHIP.pop(lock, None)
        if ownership is None:
            continue
        try:
            try:
                stat_result = os.stat(lock, follow_symlinks=False)
            except FileNotFoundError:
                continue
            current = (stat_result.st_dev, stat_result.st_ino)
            expected = (ownership.device, ownership.inode)
            if current != expected:
                continue
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
        finally:
            try:
                os.close(ownership.fd)
            except OSError:
                pass


def _acquire_locks(repo: Repository, refnames: list[str]) -> list[Path]:
    acquired: list[Path] = []
    try:
        for refname in sorted(refnames):
            lock = _lock_path(repo, refname)
            lock.parent.mkdir(parents=True, exist_ok=True)
            if lock in _REF_LOCK_OWNERSHIP:
                raise RuntimeError(f"cannot lock ref {refname!r}: lock file already exists")
            try:
                ownership = _initialize_ref_lock(lock)
            except FileExistsError as exc:
                raise RuntimeError(f"cannot lock ref {refname!r}: lock file already exists") from exc
            _REF_LOCK_OWNERSHIP[lock] = ownership
            acquired.append(lock)
    except Exception:
        _release_locks(acquired)
        raise
    return acquired


def _prepare_publications(
    repo: Repository,
    certificate: PackfileUriRootCertificate,
    publications: Mapping[str, PackfileUriRefPublication],
) -> Tuple[list[RefUpdate], Dict[str, str]]:
    """Validate one publication set and return its concrete local ref updates."""

    if not isinstance(repo, Repository):
        raise TypeError("packfile-URI ref publication requires a Repository")
    if not isinstance(certificate, PackfileUriRootCertificate):
        raise TypeError("packfile-URI ref publication requires a Phase322 certificate")
    if not isinstance(publications, Mapping):
        raise TypeError("packfile-URI ref publications must be a mapping")
    if not publications:
        raise ValueError("packfile-URI ref publication requires at least one ref")

    updates: list[RefUpdate] = []
    result: Dict[str, str] = {}

    for refname, publication in publications.items():
        normalized_ref = _validate_refname(refname)
        if normalized_ref in result:
            raise ValueError(f"duplicate packfile-URI publication ref: {normalized_ref}")
        if not isinstance(publication, PackfileUriRefPublication):
            raise TypeError("packfile-URI publication values must be PackfileUriRefPublication")

        native_oid = _validate_native_oid(publication.native_oid)
        old_local = _validate_local_oid(publication.old_local_oid, field="expected old local id")

        local_oid = certificate.native_to_local.get(native_oid)
        expected_type = certificate.expected_types.get(native_oid)
        if local_oid is None or expected_type is None:
            raise ValueError("packfile-URI publication root is not present in the certificate")
        local_oid = _validate_local_oid(local_oid, field="certified local id")
        if not isinstance(expected_type, bytes):
            raise ValueError("packfile-URI certificate expected type must be bytes")
        if normalized_ref.startswith("refs/heads/") and expected_type != b"commit":
            raise ValueError("packfile-URI branch publication requires a certified commit root")

        obj = repo.store.read(local_oid)
        if obj.hash() != local_oid:
            raise RuntimeError("packfile-URI certified root changed local SHA-256 identity")
        if obj.type_name != expected_type:
            raise ValueError("packfile-URI certified root changed Git object type")

        updates.append(RefUpdate("update", normalized_ref, local_oid, old_local))
        result[normalized_ref] = local_oid

    return updates, result


def _live_ref_path(repo: Repository, refname: str) -> Path:
    path = (repo.pygit_dir / refname).resolve()
    root = repo.pygit_dir.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"invalid reference name: {refname!r}") from exc
    return path


def _reflog_path(repo: Repository, refname: str) -> Path:
    path = (repo.pygit_dir / "logs" / refname).resolve()
    root = repo.pygit_dir.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"invalid reference name: {refname!r}") from exc
    return path


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    """Durably fence one directory where the platform exposes POSIX semantics."""

    if os.name == "nt":
        # Python does not expose a portable directory-fsync primitive on Windows.
        # Match the explicit portability boundary used by the existing durable
        # LMAP/FETCH_HEAD helpers instead of claiming a guarantee we cannot make.
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publication_file_paths(repo: Repository, refnames: Sequence[str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for refname in sorted(refnames):
        ref_path = _live_ref_path(repo, refname)
        if not ref_path.is_file():
            raise RuntimeError(f"published ref disappeared before durability fence: {refname}")
        paths.append(ref_path)
        log_path = _reflog_path(repo, refname)
        if log_path.is_file():
            paths.append(log_path)
    return tuple(paths)


def _publication_directories(repo: Repository, paths: Sequence[Path]) -> tuple[Path, ...]:
    """Return containing directories leaf-first through ``.pygit`` exactly once."""

    root = repo.pygit_dir.resolve()
    directories: set[Path] = set()
    for path in paths:
        current = path.parent.resolve()
        while True:
            try:
                current.relative_to(root)
            except ValueError as exc:
                raise RuntimeError("publication durability path escaped repository metadata") from exc
            directories.add(current)
            if current == root:
                break
            current = current.parent
    return tuple(sorted(directories, key=lambda item: (-len(item.parts), str(item))))


def _fsync_publication_files(repo: Repository, refnames: Sequence[str]) -> tuple[Path, ...]:
    paths = _publication_file_paths(repo, refnames)
    for path in paths:
        _fsync_file(path)
    return paths


def _publish_packfile_uri_refs(
    repo: Repository,
    certificate: PackfileUriRootCertificate,
    publications: Mapping[str, PackfileUriRefPublication],
    *,
    message: str,
    durable: bool,
) -> Dict[str, str]:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("packfile-URI ref publication message must be non-empty")

    updates, result = _prepare_publications(repo, certificate, publications)
    locks = _acquire_locks(repo, list(result))
    publication_paths: tuple[Path, ...] = ()
    try:
        update_refs(repo, updates, message=message, deref=False)
        if durable:
            # Ref files are written through already-fsynced temporary files by the
            # generic transaction, but reflogs are append writes. Fsync all live
            # publication files while the canonical target locks and their retained
            # ownership descriptors are still held.
            publication_paths = _fsync_publication_files(repo, tuple(result))
    finally:
        _release_locks(locks)

    if durable:
        # Fsync directories only after owner-aware lock removal. This single
        # leaf-to-root fence persists both the ref/reflog directory entries and
        # the absence of transaction-owned canonical lock pathnames. A pathname
        # replaced by another actor is deliberately preserved and is therefore
        # not represented as this transaction's removed lock.
        for directory in _publication_directories(repo, publication_paths):
            _fsync_directory(directory)

    return result


def publish_packfile_uri_refs(
    repo: Repository,
    certificate: PackfileUriRootCertificate,
    publications: Mapping[str, PackfileUriRefPublication],
    *,
    message: str = "fetch: publish certified packfile-uri refs",
) -> Dict[str, str]:
    """Durably publish certified roots as one compare-and-swap ref transaction.

    Phase350 strengthens the historical Phase323 API in place so every existing
    packfile-URI caller, including the Phase359 incremental transaction and its
    established monkeypatch seam, receives the same success-after-durability
    guarantee without a parallel publication path. Phase360 additionally pins
    canonical target-lock ownership through that durability boundary.
    """

    return _publish_packfile_uri_refs(
        repo,
        certificate,
        publications,
        message=message,
        durable=True,
    )


def publish_packfile_uri_refs_durable(
    repo: Repository,
    certificate: PackfileUriRootCertificate,
    publications: Mapping[str, PackfileUriRefPublication],
    *,
    message: str = "fetch: durably publish certified packfile-uri refs",
) -> Dict[str, str]:
    """Explicit spelling for the durable publication contract.

    The target ref lockfiles and retained ownership descriptors remain held
    through ref/reflog file fsync. Owned pathnames are then removed before the
    directory hierarchy is fsynced, so successful return persists both the new
    ref namespace and transaction-owned lock cleanup. If a file or directory
    fsync fails after the generic ref transaction became visible, the exception
    is propagated. Complete new refs/reflogs may remain visible; callers must not
    represent that operation as durably successful or perform later mutable steps.
    """

    return _publish_packfile_uri_refs(
        repo,
        certificate,
        publications,
        message=message,
        durable=True,
    )
