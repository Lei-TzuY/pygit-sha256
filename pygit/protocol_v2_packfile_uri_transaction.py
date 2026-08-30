"""Repository-level orchestration for verified protocol-v2 packfile-URI fetches.

Phase324 composes the already isolated Phase320-323 boundaries into one explicit
transaction pipeline. Phase325 additionally snapshots the small mutable
publication surface before any network/repository work and verifies that no
pre-publication stage (or concurrent writer) changed it before refs are committed.
Network descriptors are fully verified first, native objects are imported through
the SHA-256 staging boundary, requested roots are certified, and compare-and-swap
ref publication remains the final mutable step.
"""

from __future__ import annotations

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
    4. Verify that the bounded mutable publication surface is byte-for-byte
       unchanged since preflight, then publish all refs through Phase323's canonical
       lock + expected-old CAS transaction.  This is intentionally the final mutable
       commit point.

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

    _assert_publication_state_unchanged(repo, publications, mutable_state)
    published_refs = publish_packfile_uri_refs(
        repo,
        certificate,
        publications,
        message=message,
    )

    return PackfileUriFetchTransactionResult(
        batch=batch,
        staged=staged,
        certificate=certificate,
        published_refs=dict(published_refs),
    )
