"""Stage verified inline/external native objects before publishing local SHA-256 objects.

Phase321 crosses the content-import boundary, but deliberately stops before refs or
promisor metadata.  All remote-native objects are first converted in an isolated
temporary SHA-256 object store.  Only after the complete graph imports successfully
are the resulting immutable local objects copied into the destination store.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping

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


def stage_packfile_uri_import(
    store: ObjectStore,
    inline_objects: Mapping[str, NativeObject],
    external_batch: DownloadedPackfileUriBatch,
) -> StagedPackfileUriImport:
    """Import one inline + external pack object set through an isolated staging store.

    The function validates and merges the complete remote-native object set first.
    Conflicting duplicate native OIDs fail before any destination-store write.  A
    temporary ``ObjectStore`` then exercises the ordinary ``NativeImporter`` across
    every object, which catches missing graph dependencies and conversion failures.

    Destination publication begins only after that full staging import succeeds.
    Publication consists solely of immutable content-addressed SHA-256 object writes;
    refs, HEAD, reflogs, and promisor metadata are intentionally outside this phase.
    Therefore a process failure during publication can at worst leave unreachable
    valid loose objects, never a ref that points at an incomplete graph.
    """

    if not isinstance(store, ObjectStore):
        raise TypeError("protocol-v2 staged import requires an ObjectStore")
    if not isinstance(external_batch, DownloadedPackfileUriBatch):
        raise TypeError("protocol-v2 staged import requires a verified external batch")
    if not isinstance(inline_objects, Mapping):
        raise TypeError("protocol-v2 inline objects must be a mapping")

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

    with tempfile.TemporaryDirectory(prefix="pygit-packfile-uri-stage-") as temp_dir:
        staging_store = ObjectStore(Path(temp_dir) / "objects")
        importer = NativeImporter(staging_store, merged)
        for oid in sorted(merged):
            importer.import_oid(oid)

        native_to_local = {oid: importer.converted[oid] for oid in sorted(merged)}
        local_oids = tuple(sorted(set(native_to_local.values())))

        # Verify every staged SHA-256 object can be decoded before touching the
        # destination store.  This also gives publication a stable object list.
        staged_objects = [(oid, staging_store.read(oid)) for oid in local_oids]

        for expected_oid, obj in staged_objects:
            actual_oid = store.write(obj)
            if actual_oid != expected_oid:
                raise RuntimeError(
                    "protocol-v2 staged SHA-256 object changed identity during publication"
                )

    return StagedPackfileUriImport(native_to_local, local_oids)
