"""Repository-level orchestration for verified protocol-v2 packfile-URI fetches.

Phase324 composes the already isolated Phase320-323 boundaries into one explicit
transaction pipeline. Phase325 snapshots the small mutable publication surface
before any network/repository work and verifies that no pre-publication stage (or
concurrent writer) changed it before refs are committed. Phase326 closes the
remaining check-to-publication window for Git-managed mutable metadata by holding
canonical lockfiles across the final state comparison and ref transaction.
Network descriptors are fully verified first, native objects are imported through
the SHA-256 staging boundary, requested roots are certified, and compare-and-swap
ref publication remains the final mutable step.

Phase351 hardens publication-guard initialization against short or interrupted
low-level writes. A guard is considered initialized only after its complete
marker has been written and fsynced; zero-progress writes fail closed and reuse
Phase349's transaction-owned lock cleanup.

Phase353 retains a non-inheritable descriptor for every acquired publication
guard and records its filesystem identity. Release checks that the pathname still
names that exact inode before unlinking it. If an external actor removes and
recreates a guard pathname while this transaction is active, cleanup closes the
old descriptor but leaves the replacement lock in place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .protocol_v2_packfile_uri_batch import (
    DownloadedPackfileUriBatch,
    download_packfile_uris,
)
from .protocol_v2_packfile_uri_connectivity import (
    PackfileUriRootCertificate,
    certify_packfile_uri_roots,
)
from .protocol_v2_packfile_uri_refs import (
    PackfileUriRefPublication,
    publish_packfile_uri_refs,
)
from .protocol_v2_packfile_uri_stage import (
    StagedPackfileUriImport,
    stage_packfile_uri_import,
)
from .protocol_v2_packfile_uris import PackfileUriDescriptor
from .remote import NativeObject
from .repo import Repository


_PUBLICATION_GUARD_MARKER = b"packfile-uri publication guard\n"


@dataclass(frozen=True)
class _PublicationGuardOwnership:
    fd: int
    device: int
    inode: int


_PUBLICATION_GUARD_OWNERSHIP: dict[Path, _PublicationGuardOwnership] = {}


@dataclass(frozen=True)
class PackfileUriFetchTransactionResult:
    """Successful result from the complete external-pack fetch pipeline."""

    batch: DownloadedPackfileUriBatch
    staged: StagedPackfileUriImport
    certificate: PackfileUriRootCertificate
    published_refs: dict[str, str]


def _preflight_publication_plan(
    expected_roots: Mapping[str, bytes | str],
    publications: Mapping[str, PackfileUriRefPublication],
) -> None:
    """Reject obviously inconsistent plans before any network or repository I/O."""

    if not isinstance(expected_roots, Mapping):
        raise TypeError("packfile-URI expected roots must be a mapping")
    if not expected_roots:
        raise ValueError("packfile-URI fetch transaction requires at least one expected root")
    if not isinstance(publications, Mapping):
        raise TypeError("packfile-URI ref publications must be a mapping")
    if not publications:
        raise ValueError("packfile-URI fetch transaction requires at least one ref publication")

    for refname, publication in publications.items():
        if not isinstance(refname, str) or not refname.startswith("refs/"):
            raise ValueError("packfile-URI publication requires a full refs/... name")
        if not isinstance(publication, PackfileUriRefPublication):
            raise TypeError("packfile-URI publication values must be PackfileUriRefPublication")
        if publication.native_oid not in expected_roots:
            raise ValueError(
                "packfile-URI publication native root must be declared in expected_roots"
            )


def _publication_state_paths(
    repo: Repository,
    publications: Mapping[str, PackfileUriRefPublication],
) -> tuple[Path, ...]:
    """Return the bounded mutable state that must stay stable until ref commit.

    Immutable objects are deliberately excluded: Phase321 is allowed to publish
    verified content-addressed SHA-256 objects before the ref transaction.  The
    paths below are the mutable reference/promisor/shallow surfaces which this
    packfile-URI pipeline must not touch before the final Phase323 commit.
    """

    base = repo.pygit_dir
    paths = [
        base / "HEAD",
        base / "logs" / "HEAD",
        base / "packed-refs",
        base / "promisor.json",
        base / "shallow",
    ]
    for refname in sorted(publications):
        paths.append(base / refname)
        paths.append(base / "logs" / refname)

    # Preserve deterministic ordering while avoiding duplicate paths.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return tuple(unique)


def _snapshot_publication_state(
    repo: Repository,
    publications: Mapping[str, PackfileUriRefPublication],
) -> dict[str, bytes | None]:
    """Capture exact bytes/existence for the bounded mutable publication surface."""

    snapshot: dict[str, bytes | None] = {}
    for path in _publication_state_paths(repo, publications):
        key = path.relative_to(repo.pygit_dir).as_posix()
        try:
            snapshot[key] = path.read_bytes()
        except FileNotFoundError:
            snapshot[key] = None
    return snapshot


def _assert_publication_state_unchanged(
    repo: Repository,
    publications: Mapping[str, PackfileUriRefPublication],
    before: Mapping[str, bytes | None],
) -> None:
    """Fail closed if pre-publication work changed mutable repository state."""

    after = _snapshot_publication_state(repo, publications)
    changed = sorted(
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    )
    if changed:
        raise RuntimeError(
            "packfile-URI mutable repository state changed before ref publication: "
            + ", ".join(changed)
        )


def _publication_guard_lock_paths(repo: Repository) -> tuple[Path, ...]:
    """Return non-ref lockfiles held across final validation and publication.

    Target refs are intentionally absent here: Phase323 acquires their canonical
    ``<ref>.lock`` files and performs expected-old CAS while publishing.  These
    additional locks protect mutable repository-wide metadata that Phase325
    snapshots but that a target-ref CAS alone cannot serialize.
    """

    base = repo.pygit_dir
    return (
        base / "HEAD.lock",
        base / "packed-refs.lock",
        base / "promisor.json.lock",
        base / "shallow.lock",
    )


def _write_publication_guard_marker(fd: int) -> None:
    """Write the complete guard marker, retrying interruption and short writes.

    ``os.write`` is allowed to consume fewer bytes than requested. Guard
    initialization must therefore not treat one successful call as proof that
    the marker is complete. An interrupted call is retried; a zero-progress call
    is treated as an I/O failure so the transaction-owned lock is cleaned up by
    the caller rather than being accepted as initialized.
    """

    remaining = memoryview(_PUBLICATION_GUARD_MARKER)
    while remaining:
        try:
            written = os.write(fd, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("publication guard marker write made no progress")
        remaining = remaining[written:]


def _open_publication_guard_lock(lock: Path) -> int:
    """Exclusively create one guard lock with a non-inheritable descriptor."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    fd = os.open(lock, flags, 0o666)
    try:
        os.set_inheritable(fd, False)
    except BaseException:
        try:
            os.close(fd)
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
        raise
    return fd


def _initialize_publication_guard_lock(lock: Path) -> None:
    """Create, initialize, and fsync one transaction-owned guard lock.

    This compatibility helper preserves Phase349/351's standalone boundary: the
    descriptor is closed on success and the caller owns the initialized pathname.
    The transaction acquisition path below uses the retaining variant so inode
    ownership remains pinned for the complete critical section.
    """

    fd = _open_publication_guard_lock(lock)
    initialized = False
    try:
        _write_publication_guard_marker(fd)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        initialized = True
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if not initialized:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass


def _initialize_owned_publication_guard_lock(lock: Path) -> _PublicationGuardOwnership:
    """Initialize one guard and retain its descriptor until transaction release."""

    fd = _open_publication_guard_lock(lock)
    initialized = False
    try:
        _write_publication_guard_marker(fd)
        os.fsync(fd)
        stat_result = os.fstat(fd)
        ownership = _PublicationGuardOwnership(
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


def _acquire_publication_guard_locks(repo: Repository) -> list[Path]:
    """Acquire canonical metadata locks without stealing an existing writer's lock.

    A path is appended to ``acquired`` only after descriptor hardening, complete
    marker write, fsync, and descriptor-derived identity capture have succeeded.
    The retained non-inheritable descriptor pins the owned inode until release so
    unlink/recreate cannot recycle that identity underneath this transaction.
    """

    acquired: list[Path] = []
    try:
        for lock in sorted(_publication_guard_lock_paths(repo)):
            lock.parent.mkdir(parents=True, exist_ok=True)
            try:
                ownership = _initialize_owned_publication_guard_lock(lock)
            except FileExistsError as exc:
                relative = lock.relative_to(repo.pygit_dir).as_posix()
                raise RuntimeError(
                    f"cannot lock packfile-URI publication state {relative!r}: "
                    "lock file already exists"
                ) from exc
            _PUBLICATION_GUARD_OWNERSHIP[lock] = ownership
            acquired.append(lock)
    except Exception:
        _release_publication_guard_locks(acquired)
        raise
    return acquired


def _release_publication_guard_locks(locks: Iterable[Path]) -> None:
    """Release only guard pathnames that still name this transaction's inode.

    Git-style lockfiles rely on cooperative ownership. An external actor may
    nevertheless remove a lock pathname and create a new lock before the original
    owner reaches cleanup. A path-only ``unlink`` would then delete the new lock.
    Phase353 compares the current non-following path identity with the still-open
    transaction-owned descriptor. A missing or replaced path is left alone, and
    the retained descriptor is always closed exactly once.
    """

    for lock in reversed(tuple(locks)):
        ownership = _PUBLICATION_GUARD_OWNERSHIP.pop(lock, None)
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


def execute_packfile_uri_fetch_transaction(
    repo: Repository,
    descriptors: Iterable[PackfileUriDescriptor],
    inline_objects: Mapping[str, NativeObject],
    expected_roots: Mapping[str, bytes | str],
    publications: Mapping[str, PackfileUriRefPublication],
    *,
    message: str = "fetch: publish verified packfile-uri transaction",
    timeout: int = 30,
    max_pack_bytes: int = 256 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
    max_packs: int = 64,
    opener=None,
) -> PackfileUriFetchTransactionResult:
    """Run the complete verified external-pack pipeline with refs committed last.

    The operation has four ordered boundaries:

    1. Download every external descriptor through Phase320's bounded checksum and
       PACK verification.  This stage has no repository side effects.
    2. Merge inline/external native objects and import the complete graph through
       Phase321's isolated SHA-256 staging store.  Only immutable content-addressed
       objects may be published to the destination store.
    3. Re-read and certify every requested native root through Phase322, proving it
       maps to a published content-derived SHA-256 object of the required Git type.
    4. Acquire repository-wide metadata locks, verify that the bounded mutable
       publication surface is byte-for-byte unchanged since preflight, then publish
       all target refs through Phase323's canonical per-ref lock + expected-old CAS
       transaction.  This is intentionally the final mutable commit point.

    The Phase326 locks are acquired only after the expensive network/import work,
    minimizing contention.  Any writer that changed metadata before the locks were
    acquired is caught by the Phase325 byte snapshot; compliant Git-style writers
    are then excluded until the target-ref transaction completes.

    A failure before step 4 publishes no refs.  Valid immutable objects staged in
    step 2 may remain unreachable.  Any concurrent or accidental mutation of HEAD,
    target refs/reflogs, packed-refs, shallow state, or promisor state aborts before
    publication.  A failure in Phase323 may likewise leave valid unreachable
    immutable objects, but its ref transaction guarantees that no successful
    partial ref result is exposed.
    """

    if not isinstance(repo, Repository):
        raise TypeError("packfile-URI fetch transaction requires a Repository")
    if not isinstance(inline_objects, Mapping):
        raise TypeError("packfile-URI inline objects must be a mapping")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("packfile-URI fetch transaction message must be non-empty")

    _preflight_publication_plan(expected_roots, publications)
    mutable_state = _snapshot_publication_state(repo, publications)

    batch = download_packfile_uris(
        descriptors,
        timeout=timeout,
        max_pack_bytes=max_pack_bytes,
        max_total_bytes=max_total_bytes,
        max_packs=max_packs,
        opener=opener,
    )
    staged = stage_packfile_uri_import(repo.store, inline_objects, batch)
    certificate = certify_packfile_uri_roots(repo.store, staged, expected_roots)

    guard_locks = _acquire_publication_guard_locks(repo)
    try:
        _assert_publication_state_unchanged(repo, publications, mutable_state)
        published_refs = publish_packfile_uri_refs(
            repo,
            certificate,
            publications,
            message=message,
        )
    finally:
        _release_publication_guard_locks(guard_locks)

    return PackfileUriFetchTransactionResult(
        batch=batch,
        staged=staged,
        certificate=certificate,
        published_refs=dict(published_refs),
    )
