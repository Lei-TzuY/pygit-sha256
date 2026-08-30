"""Repository-level orchestration for verified protocol-v2 packfile-URI fetches.

Phase324 composes the already isolated Phase320-323 boundaries into one explicit
transaction pipeline. Network descriptors are fully verified first, native
objects are imported through the SHA-256 staging boundary, requested roots are
certified, and compare-and-swap ref publication remains the final mutable step.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    4. Publish all refs through Phase323's canonical lock + expected-old CAS
       transaction.  This is intentionally the final mutable commit point.

    A failure before step 4 publishes no refs.  A failure in step 4 may leave valid
    unreachable immutable objects from staging, but Phase323's ref transaction
    guarantees that no successful partial ref result is exposed.
    """

    if not isinstance(repo, Repository):
        raise TypeError("packfile-URI fetch transaction requires a Repository")
    if not isinstance(inline_objects, Mapping):
        raise TypeError("packfile-URI inline objects must be a mapping")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("packfile-URI fetch transaction message must be non-empty")

    _preflight_publication_plan(expected_roots, publications)

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
