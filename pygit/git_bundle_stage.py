"""Stage one verified self-contained Git bundle into the SHA-256 object store.

Phase387 establishes the remote file-format trust boundary.  This module crosses
only the content-import boundary: the complete native SHA-1 object graph is
converted in an isolated temporary SHA-256 ObjectStore first, verified there,
and only then published as immutable local objects.  Reference publication is a
separate later transaction.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .git_bundle import GitBundlePayload
from .remote import NativeImporter, NativeObject
from .store import ObjectStore


@dataclass(frozen=True)
class StagedGitBundleImport:
    """One completely converted bundle graph published without ref mutation."""

    native_to_local: Dict[str, str]
    ref_targets: Dict[str, str]
    local_oids: tuple[str, ...]


def _validate_native_object(oid: str, obj: NativeObject) -> None:
    if not isinstance(oid, str) or len(oid) != 40:
        raise ValueError("Git bundle native object id must be a full SHA-1")
    try:
        bytes.fromhex(oid)
    except ValueError as exc:
        raise ValueError("Git bundle native object id must be hexadecimal") from exc
    if not isinstance(obj, NativeObject):
        raise TypeError("Git bundle object map contains a non-native object")
    if obj.oid.lower() != oid.lower():
        raise ValueError("Git bundle object-map key does not match native object id")
    canonical = f"{obj.type_name} {len(obj.data)}\0".encode() + obj.data
    if hashlib.sha1(canonical).hexdigest() != oid.lower():
        raise ValueError("Git bundle native object content does not match its SHA-1")


def stage_git_bundle_import(
    store: ObjectStore,
    bundle: GitBundlePayload,
) -> StagedGitBundleImport:
    """Convert and publish one Phase387-verified self-contained bundle graph.

    The bundle must be complete without prerequisite objects and without a
    partial-clone filter.  Every native object is revalidated at this boundary,
    then imported into an isolated temporary ObjectStore.  Missing graph
    dependencies or conversion errors therefore occur before any destination
    object is written.

    Only after the entire staged graph can be decoded do we copy its immutable,
    content-addressed SHA-256 objects into *store*.  A process/storage failure
    during this final copy can at worst leave valid unreachable objects; this
    function never mutates refs, HEAD, reflogs, shallow state, or promisor data.
    """

    if not isinstance(store, ObjectStore):
        raise TypeError("Git bundle staged import requires an ObjectStore")
    if not isinstance(bundle, GitBundlePayload):
        raise TypeError("Git bundle staged import requires a verified GitBundlePayload")
    if bundle.object_format != "sha1":
        raise RuntimeError("Git bundle staged import requires remote-native SHA-1")
    if bundle.requires_prerequisites:
        raise RuntimeError(
            "Git bundle prerequisites must be satisfied before staged import"
        )
    if bundle.is_filtered:
        raise RuntimeError(
            "Filtered Git bundles require a promisor-aware import boundary"
        )
    if not bundle.is_self_contained or bundle.objects is None:
        raise RuntimeError("Git bundle is not self-contained for staged import")
    if not bundle.objects:
        raise ValueError("Git bundle staged import requires at least one native object")

    objects: Dict[str, NativeObject] = {}
    for oid, obj in bundle.objects.items():
        normalized = oid.lower()
        _validate_native_object(normalized, obj)
        previous = objects.get(normalized)
        if previous is not None and previous != obj:
            raise ValueError("Git bundle contains conflicting objects for one native OID")
        objects[normalized] = obj

    refs: Dict[str, str] = {}
    for refname, native_oid in bundle.refs.items():
        if not isinstance(refname, str) or not refname:
            raise ValueError("Git bundle staged import contains an invalid reference name")
        if not isinstance(native_oid, str) or len(native_oid) != 40:
            raise ValueError("Git bundle reference target must be a full SHA-1")
        try:
            bytes.fromhex(native_oid)
        except ValueError as exc:
            raise ValueError("Git bundle reference target must be hexadecimal") from exc
        normalized = native_oid.lower()
        if normalized not in objects:
            raise ValueError(
                f"Git bundle reference {refname!r} targets an object absent from the verified graph"
            )
        refs[refname] = normalized

    if not refs:
        raise ValueError("Git bundle staged import requires at least one advertised ref")

    with tempfile.TemporaryDirectory(prefix="pygit-bundle-stage-") as temp_dir:
        staging_store = ObjectStore(Path(temp_dir) / "objects")
        importer = NativeImporter(staging_store, objects)

        # Import every native object, not only advertised roots.  This verifies
        # that the entire verified pack graph can cross the SHA-1 -> SHA-256
        # content conversion boundary before destination publication starts.
        for oid in sorted(objects):
            importer.import_oid(oid)

        native_to_local = {
            oid: importer.converted[oid]
            for oid in sorted(objects)
        }
        ref_targets = {
            refname: native_to_local[native_oid]
            for refname, native_oid in sorted(refs.items())
        }
        local_oids = tuple(sorted(set(native_to_local.values())))

        staged_objects = []
        for local_oid in local_oids:
            if len(local_oid) != 64:
                raise RuntimeError("Git bundle importer produced a non-SHA-256 local object id")
            try:
                bytes.fromhex(local_oid)
            except ValueError as exc:
                raise RuntimeError(
                    "Git bundle importer produced a non-hexadecimal local object id"
                ) from exc
            obj = staging_store.read(local_oid)
            staged_objects.append((local_oid, obj))

        for expected_oid, obj in staged_objects:
            actual_oid = store.write(obj)
            if actual_oid != expected_oid:
                raise RuntimeError(
                    "Git bundle staged SHA-256 object changed identity during publication"
                )

    return StagedGitBundleImport(
        native_to_local=native_to_local,
        ref_targets=ref_targets,
        local_oids=local_oids,
    )
