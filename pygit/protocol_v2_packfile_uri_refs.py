"""Publish certified packfile-URI roots through a CAS ref transaction.

Phase323 consumes the read-only Phase322 root certificate and makes refs the
final mutable step of the external-pack fetch path.  Every publication carries
an explicit expected old local SHA-256 value (or the all-zero local object id
for creation), is revalidated against the destination object store, and is
committed through pygit's existing transactional ``update-ref`` plumbing.

Canonical ``<ref>.lock`` files are held for the duration of the transaction so
native Git ref writers that follow the files backend locking convention cannot
race this publication path.  The existing ref transaction still performs the
object existence/type checks, compare-and-swap verification, reflog handling,
and snapshot rollback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping

from .protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from .ref_query import check_ref_format
from .ref_transaction import RefUpdate, update_refs
from .refs import ZERO_SHA
from .repo import Repository

_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class PackfileUriRefPublication:
    """One certified native root to publish at a local reference.

    ``old_local_oid`` is mandatory.  Use :data:`pygit.refs.ZERO_SHA` when the
    ref must not already exist; otherwise provide the exact current local
    SHA-256 object id.  This makes every publication compare-and-swap rather
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


def _acquire_locks(repo: Repository, refnames: list[str]) -> list[Path]:
    acquired: list[Path] = []
    try:
        for refname in sorted(refnames):
            lock = _lock_path(repo, refname)
            lock.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            except FileExistsError as exc:
                raise RuntimeError(f"cannot lock ref {refname!r}: lock file already exists") from exc
            try:
                os.write(fd, b"packfile-uri ref transaction\n")
                os.fsync(fd)
            finally:
                os.close(fd)
            acquired.append(lock)
    except Exception:
        for lock in reversed(acquired):
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
        raise
    return acquired


def publish_packfile_uri_refs(
    repo: Repository,
    certificate: PackfileUriRootCertificate,
    publications: Mapping[str, PackfileUriRefPublication],
    *,
    message: str = "fetch: publish certified packfile-uri refs",
) -> Dict[str, str]:
    """Publish certified roots as one compare-and-swap ref transaction.

    The function deliberately accepts only full local ref names and requires an
    explicit old value for every update.  It re-reads every certified local
    object immediately before locking/publishing, proving that the certificate
    still names the same content-derived SHA-256 object and expected Git object
    type.  Branch refs additionally require a certified commit root.

    All canonical ref lock files are acquired in lexical order before the
    existing :func:`pygit.ref_transaction.update_refs` transaction runs.  A CAS
    mismatch, lock conflict, type failure, or I/O failure publishes no successful
    result.  Immutable objects imported by Phase321 may remain unreachable,
    which is the intended failure mode for a fetch transaction.
    """

    if not isinstance(repo, Repository):
        raise TypeError("packfile-URI ref publication requires a Repository")
    if not isinstance(certificate, PackfileUriRootCertificate):
        raise TypeError("packfile-URI ref publication requires a Phase322 certificate")
    if not isinstance(publications, Mapping):
        raise TypeError("packfile-URI ref publications must be a mapping")
    if not publications:
        raise ValueError("packfile-URI ref publication requires at least one ref")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("packfile-URI ref publication message must be non-empty")

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

    locks = _acquire_locks(repo, list(result))
    try:
        update_refs(repo, updates, message=message, deref=False)
    finally:
        for lock in reversed(locks):
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

    return result
