"""Stage verified inline/external native objects before publishing local SHA-256 objects.

Phase321 crosses the content-import boundary, but deliberately stops before refs or
promisor metadata.  All remote-native objects are first converted in an isolated
temporary SHA-256 object store.  Only after the complete graph imports successfully
are the resulting immutable local objects copied into the destination store.

Phase334 additionally permits an explicit native-SHA-1 -> local-SHA-256 ``known``
map for objects a server legitimately omitted because they were reachable from an
advertised ``have``.  Known objects are validated in the destination store before
they may satisfy dependencies in the staging importer.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from .protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from .remote import NativeImporter, NativeObject
from .store import ObjectStore


@dataclass(frozen=True)
class StagedPackfileUriImport:
    """A fully validated native-to-local import published without ref mutation."""

    native_to_local: Dict[str, str]
    local_oids: tuple[str, ...]


def _validate_native_object(oid: str, obj: NativeObject) -> None:
    if not isinstance(oid, str) or len(oid) != 40:
        raise ValueError("protocol-v2 staged native object id must be a full SHA-1")
    try:
        bytes.fromhex(oid)
    except ValueError as exc:
        raise ValueError("protocol-v2 staged native object id must be hexadecimal") from exc
    if not isinstance(obj, NativeObject):
        raise TypeError("protocol-v2 staged object map contains a non-native object")
    if obj.oid != oid:
        raise ValueError("protocol-v2 staged object map key does not match native object id")
    canonical = f"{obj.type_name} {len(obj.data)}\0".encode() + obj.data
    if hashlib.sha1(canonical).hexdigest() != oid:
        raise ValueError("protocol-v2 staged native object content does not match its SHA-1")


def _validate_known_native_to_local(
    store: ObjectStore,
    known_native_to_local: Optional[Mapping[str, str]],
) -> Dict[str, str]:
    """Validate importer-known identities against readable destination objects."""

    if known_native_to_local is None:
        return {}
    if not isinstance(known_native_to_local, Mapping):
        raise TypeError("protocol-v2 known-object identities must be a mapping")

    known: Dict[str, str] = {}
    local_seen: Dict[str, str] = {}
    for native, local in known_native_to_local.items():
        if (
            not isinstance(native, str)
            or len(native) != 40
            or native != native.lower()
        ):
            raise ValueError(
                "protocol-v2 known native object id must be a full lowercase SHA-1"
            )
        try:
            bytes.fromhex(native)
        except ValueError as exc:
            raise ValueError(
                "protocol-v2 known native object id must be hexadecimal"
            ) from exc

        if (
            not isinstance(local, str)
            or len(local) != 64
            or local != local.lower()
        ):
            raise ValueError(
                "protocol-v2 known local object id must be a full lowercase SHA-256"
            )
        try:
            bytes.fromhex(local)
        except ValueError as exc:
            raise ValueError(
                "protocol-v2 known local object id must be hexadecimal"
            ) from exc

        previous = local_seen.get(local)
        if previous is not None and previous != native:
            raise ValueError(
                "protocol-v2 known identities map multiple native objects to one local object"
            )

        try:
            obj = store.read(local)
        except KeyError as exc:
            raise RuntimeError(
                f"protocol-v2 known local object is missing: {local}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"protocol-v2 known local object is unreadable: {local}"
            ) from exc
        if obj.hash() != local:
            raise RuntimeError(
                "protocol-v2 known local object changed SHA-256 identity while validating"
            )

        known[native] = local
        local_seen[local] = native
    return known


def stage_packfile_uri_import(
    store: ObjectStore,
    inline_objects: Mapping[str, NativeObject],
    external_batch: DownloadedPackfileUriBatch,
    *,
    known_native_to_local: Optional[Mapping[str, str]] = None,
) -> StagedPackfileUriImport:
    """Import inline + external objects with optional verified existing dependencies.

    The function validates and merges the fetched remote-native object set first.
    Conflicting duplicate native OIDs fail before any destination-store write.
    ``known_native_to_local`` may name already-present destination objects omitted by
    an incremental server response.  Every such local object is re-read and
    content-address verified before the mapping is supplied to ``NativeImporter``.

    If a native OID is both fetched and present in ``known_native_to_local``, the
    fetched bytes are imported rather than skipped; the newly derived local SHA-256
    must exactly equal the known mapping.  This prevents stale compatibility
    metadata from masking contradictory fetched content.

    A temporary ``ObjectStore`` exercises the ordinary ``NativeImporter`` across
    every fetched object. Destination publication begins only after that staging
    import succeeds and consists solely of immutable content-addressed SHA-256
    object writes. Refs, HEAD, reflogs, and promisor metadata remain outside this
    phase.
    """

    if not isinstance(store, ObjectStore):
        raise TypeError("protocol-v2 staged import requires an ObjectStore")
    if not isinstance(external_batch, DownloadedPackfileUriBatch):
        raise TypeError("protocol-v2 staged import requires a verified external batch")
    if not isinstance(inline_objects, Mapping):
        raise TypeError("protocol-v2 inline objects must be a mapping")

    known = _validate_known_native_to_local(store, known_native_to_local)

    merged: Dict[str, NativeObject] = {}
    for source in (inline_objects, external_batch.objects):
        for oid, obj in source.items():
            _validate_native_object(oid, obj)
            previous = merged.get(oid)
            if previous is not None and previous != obj:
                raise ValueError(
                    "protocol-v2 inline/external packs contain conflicting objects for one native OID"
                )
            merged[oid] = obj

    if not merged:
        raise ValueError("protocol-v2 staged import requires at least one native object")

    # Fetched bytes are authoritative for any overlap. Known-only identities can
    # satisfy dependencies that the server legitimately omitted after a `have`.
    importer_known = {
        native: local for native, local in known.items() if native not in merged
    }

    with tempfile.TemporaryDirectory(prefix="pygit-packfile-uri-stage-") as temp_dir:
        staging_store = ObjectStore(Path(temp_dir) / "objects")
        importer = NativeImporter(staging_store, merged, known=importer_known)
        for oid in sorted(merged):
            importer.import_oid(oid)

        native_to_local = {oid: importer.converted[oid] for oid in sorted(merged)}
        for native, local in native_to_local.items():
            expected = known.get(native)
            if expected is not None and expected != local:
                raise ValueError(
                    "protocol-v2 fetched object contradicts its known native-to-local mapping"
                )

        local_oids = tuple(sorted(set(native_to_local.values())))

        # Verify every newly staged SHA-256 object can be decoded before touching
        # the destination store. Known-only local objects already passed the
        # destination-store validation above and are not republished here.
        staged_objects = [(oid, staging_store.read(oid)) for oid in local_oids]

        for expected_oid, obj in staged_objects:
            actual_oid = store.write(obj)
            if actual_oid != expected_oid:
                raise RuntimeError(
                    "protocol-v2 staged SHA-256 object changed identity during publication"
                )

    return StagedPackfileUriImport(native_to_local, local_oids)
