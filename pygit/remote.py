"""
Smart HTTP fetch support for real Git repositories.

Remote servers expose SHA-1 Git objects.  ``pygit`` uses a SHA-256 object
store, so fetched objects are unpacked and converted before they are written
locally.  The transport implements the protocol-v0 upload-pack flow used by
Git smart HTTP endpoints.
"""

from __future__ import annotations

import hashlib
import struct
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .objects import BlobObject, CommitObject, Identity, TagObject, TreeObject
from .store import ObjectStore


def pkt_line(payload: bytes) -> bytes:
    """Encode one pkt-line payload."""
    return f"{len(payload) + 4:04x}".encode() + payload


def _read_pkt_line(data: bytes, offset: int) -> Tuple[Optional[bytes], int]:
    if offset + 4 > len(data):
        raise ValueError("Truncated pkt-line length")
    raw_length = data[offset:offset + 4]
    try:
        length = int(raw_length, 16)
    except ValueError as exc:
        raise ValueError(f"Invalid pkt-line length: {raw_length!r}") from exc
    offset += 4
    if length in (0, 1, 2):
        return None, offset
    if length < 4 or offset + length - 4 > len(data):
        raise ValueError("Truncated pkt-line payload")
    return data[offset:offset + length - 4], offset + length - 4


@dataclass
class Advertisement:
    refs: Dict[str, str]
    capabilities: Set[str]
    symrefs: Dict[str, str]


@dataclass
class FetchResult:
    advertisement: Advertisement
    objects: Dict[str, "NativeObject"]


@dataclass
class PushResult:
    advertisement: Advertisement
    ref_name: str
    old_oid: str
    new_oid: str
    objects_sent: int


class SmartHttpClient:
    """Fetch a pack from a Git smart HTTP ``upload-pack`` endpoint."""

    def __init__(self, url: str, timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def discover(self) -> Advertisement:
        query = urllib.parse.urlencode({"service": "git-upload-pack"})
        request = urllib.request.Request(
            f"{self.url}/info/refs?{query}",
            headers={"Accept": "application/x-git-upload-pack-advertisement"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read()
        return self._parse_advertisement(body)

    def fetch(
        self,
        haves: Optional[Iterable[str]] = None,
        advertisement: Optional[Advertisement] = None,
    ) -> FetchResult:
        advertisement = advertisement or self.discover()
        wants = sorted(
            {
                sha
                for name, sha in advertisement.refs.items()
                if name == "HEAD"
                or name.startswith("refs/heads/")
                or (name.startswith("refs/tags/") and not name.endswith("^{}"))
            }
        )
        if not wants:
            raise RuntimeError("Remote repository does not advertise any refs.")

        capabilities = []
        for capability in ("multi_ack_detailed", "side-band-64k", "ofs-delta"):
            if capability in advertisement.capabilities:
                capabilities.append(capability)
        if any(cap.startswith("agent=") for cap in advertisement.capabilities):
            capabilities.append("agent=pygit/0.1")

        suffix = f" {' '.join(capabilities)}" if capabilities else ""
        body = pkt_line(f"want {wants[0]}{suffix}\n".encode())
        for sha in wants[1:]:
            body += pkt_line(f"want {sha}\n".encode())
        body += b"0000"
        for sha in sorted(set(haves or [])):
            body += pkt_line(f"have {sha}\n".encode())
        body += pkt_line(b"done\n")

        request = urllib.request.Request(
            f"{self.url}/git-upload-pack",
            data=body,
            method="POST",
            headers={
                "Accept": "application/x-git-upload-pack-result",
                "Content-Type": "application/x-git-upload-pack-request",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            pack_response = response.read()

        pack = self._extract_pack(pack_response)
        return FetchResult(advertisement, PackParser(pack).parse())

    @staticmethod
    def _parse_advertisement(data: bytes) -> Advertisement:
        return _parse_service_advertisement(data, "git-upload-pack")

    @staticmethod
    def _extract_pack(data: bytes) -> bytes:
        return _extract_pack(data)


class SmartHttpPushClient:
    """Push a regular pack to a Git smart HTTP ``receive-pack`` endpoint."""

    def __init__(self, url: str, timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def discover(self) -> Advertisement:
        query = urllib.parse.urlencode({"service": "git-receive-pack"})
        request = urllib.request.Request(
            f"{self.url}/info/refs?{query}",
            headers={"Accept": "application/x-git-receive-pack-advertisement"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read()
        return _parse_service_advertisement(body, "git-receive-pack")

    def push(
        self,
        ref_name: str,
        new_oid: str,
        objects: Dict[str, "NativeObject"],
        advertisement: Optional[Advertisement] = None,
    ) -> PushResult:
        advertisement = advertisement or self.discover()
        old_oid = advertisement.refs.get(ref_name, "0" * 40)
        capabilities = []
        if "report-status" in advertisement.capabilities:
            capabilities.append("report-status")
        if any(cap.startswith("agent=") for cap in advertisement.capabilities):
            capabilities.append("agent=pygit/0.1")
        suffix = f"\0{' '.join(capabilities)}" if capabilities else ""
        body = pkt_line(f"{old_oid} {new_oid} {ref_name}{suffix}\n".encode())
        body += b"0000"
        if objects:
            body += build_pack(objects.values())

        request = urllib.request.Request(
            f"{self.url}/git-receive-pack",
            data=body,
            method="POST",
            headers={
                "Accept": "application/x-git-receive-pack-result",
                "Content-Type": "application/x-git-receive-pack-request",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = response.read()
        self._check_report_status(result, ref_name)
        return PushResult(advertisement, ref_name, old_oid, new_oid, len(objects))

    @classmethod
    def _check_report_status(cls, data: bytes, ref_name: str) -> None:
        if not data:
            return
        lines = cls._status_lines(data)
        for line in lines:
            text = line.decode("utf-8", errors="replace").strip()
            if text.startswith("unpack ") and text != "unpack ok":
                raise RuntimeError(text)
            if text.startswith(f"ng {ref_name} "):
                raise RuntimeError(text)

    @classmethod
    def _status_lines(cls, data: bytes) -> List[bytes]:
        offset = 0
        lines: List[bytes] = []
        try:
            while offset < len(data):
                line, offset = _read_pkt_line(data, offset)
                if line is not None:
                    lines.append(line)
        except ValueError:
            return data.splitlines()
        if lines and all(line and line[0] in (1, 2, 3) for line in lines):
            errors = [line[1:] for line in lines if line[0] == 3]
            if errors:
                raise RuntimeError(b"".join(errors).decode(errors="replace").strip())
            return cls._status_lines(b"".join(line[1:] for line in lines if line[0] == 1))
        return lines


def _parse_service_advertisement(data: bytes, service_name: str) -> Advertisement:
    offset = 0
    service, offset = _read_pkt_line(data, offset)
    expected = f"# service={service_name}\n".encode()
    if service != expected:
        raise ValueError(f"Remote did not return a {service_name} advertisement")
    flush, offset = _read_pkt_line(data, offset)
    if flush is not None:
        raise ValueError(f"Malformed {service_name} advertisement")

    refs: Dict[str, str] = {}
    capabilities: Set[str] = set()
    first = True
    while offset < len(data):
        line, offset = _read_pkt_line(data, offset)
        if line is None:
            break
        line = line.rstrip(b"\n")
        if first and b"\x00" in line:
            line, raw_capabilities = line.split(b"\x00", 1)
            capabilities.update(raw_capabilities.decode().split())
        first = False
        sha, name = line.decode().split(" ", 1)
        if name != "capabilities^{}":
            refs[name] = sha

    object_format = next(
        (
            cap.split("=", 1)[1]
            for cap in capabilities
            if cap.startswith("object-format=")
        ),
        "sha1",
    )
    if object_format != "sha1":
        raise RuntimeError(
            f"Unsupported remote object format: {object_format}; expected sha1"
        )

    symrefs = {
        name: target
        for capability in capabilities
        if capability.startswith("symref=")
        for name, target in [capability[7:].split(":", 1)]
    }
    return Advertisement(refs, capabilities, symrefs)


def _extract_pack(data: bytes) -> bytes:
    offset = 0
    chunks: List[bytes] = []
    while offset < len(data):
        if data[offset:offset + 4] == b"PACK":
            chunks.append(data[offset:])
            break
        line, offset = _read_pkt_line(data, offset)
        if line is None or line.startswith((b"NAK", b"ACK")):
            continue
        channel = line[0]
        if channel == 1:
            chunks.append(line[1:])
        elif channel == 2:
            continue
        elif channel == 3:
            raise RuntimeError(line[1:].decode(errors="replace").strip())
        elif line.startswith(b"PACK"):
            chunks.append(line)
        else:
            raise ValueError(f"Unexpected upload-pack response: {line[:80]!r}")
    pack = b"".join(chunks)
    if not pack.startswith(b"PACK"):
        raise ValueError("upload-pack response did not contain a packfile")
    return pack


@dataclass
class NativeObject:
    type_name: str
    data: bytes
    oid: str


class NativeExporter:
    """Convert pygit's SHA-256 object graph into native SHA-1 Git objects."""

    def __init__(
        self,
        store: ObjectStore,
        known_oids: Optional[Dict[str, str]] = None,
        have_shas: Optional[Set[str]] = None,
    ) -> None:
        self.store = store
        self.known_oids = known_oids or {}
        self.have_shas = have_shas or set()
        self.converted: Dict[str, str] = {}
        self.objects: Dict[str, NativeObject] = {}

    def export_oid(self, sha: str) -> str:
        if sha in self.converted:
            return self.converted[sha]
        known = self.known_oids.get(sha)
        if known and sha in self.have_shas:
            self.converted[sha] = known
            return known

        obj = self.store.read(sha)
        if isinstance(obj, BlobObject):
            type_name = "blob"
            data = obj.data
        elif isinstance(obj, TreeObject):
            type_name = "tree"
            chunks = []
            for entry in sorted(
                obj.entries,
                key=lambda item: item.name + ("/" if item.is_dir else ""),
            ):
                mode = "40000" if entry.mode == "040000" else entry.mode
                oid = self.export_oid(entry.sha)
                chunks.append(
                    mode.encode()
                    + b" "
                    + entry.name.encode()
                    + b"\x00"
                    + bytes.fromhex(oid)
                )
            data = b"".join(chunks)
        elif isinstance(obj, CommitObject):
            type_name = "commit"
            lines = [f"tree {self.export_oid(obj.tree)}"]
            lines.extend(f"parent {self.export_oid(parent)}" for parent in obj.parents)
            lines.append(f"author {obj.author.encode()}")
            lines.append(f"committer {obj.committer.encode()}")
            lines.extend(("", obj.message))
            data = "\n".join(lines).encode()
        elif isinstance(obj, TagObject):
            type_name = "tag"
            target_type = (
                obj.target_type.decode()
                if isinstance(obj.target_type, bytes)
                else str(obj.target_type)
            )
            lines = [
                f"object {self.export_oid(obj.target_sha)}",
                f"type {target_type}",
                f"tag {obj.tag_name}",
                f"tagger {obj.tagger.encode()}",
                "",
                obj.message,
            ]
            data = "\n".join(lines).encode()
        else:
            raise ValueError(f"Unsupported pygit object type: {type(obj).__name__}")

        canonical = f"{type_name} {len(data)}\0".encode() + data
        oid = hashlib.sha1(canonical).hexdigest()
        self.converted[sha] = oid
        self.objects[oid] = NativeObject(type_name, data, oid)
        return oid


def build_pack(objects: Iterable[NativeObject]) -> bytes:
    """Build a native Git pack containing regular, non-delta objects."""
    entries = sorted(objects, key=lambda obj: obj.oid)
    body = b"PACK" + struct.pack(">II", 2, len(entries))
    type_numbers = {"commit": 1, "tree": 2, "blob": 3, "tag": 4}
    for obj in entries:
        try:
            type_number = type_numbers[obj.type_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported native object type: {obj.type_name}") from exc
        body += _encode_object_header(type_number, len(obj.data))
        body += zlib.compress(obj.data)
    return body + hashlib.sha1(body).digest()


def _encode_object_header(type_number: int, size: int) -> bytes:
    byte = (type_number << 4) | (size & 0x0F)
    size >>= 4
    result = bytearray()
    while size:
        result.append(byte | 0x80)
        byte = size & 0x7F
        size >>= 7
    result.append(byte)
    return bytes(result)


@dataclass
class _PackEntry:
    offset: int
    packed_type: int
    data: bytes
    base_offset: Optional[int] = None
    base_oid: Optional[str] = None


class PackParser:
    """Expand a SHA-1 Git pack, including OFS_DELTA and REF_DELTA entries."""

    _TYPE_NAMES = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}

    def __init__(self, pack: bytes) -> None:
        self.pack = pack

    def parse(self) -> Dict[str, NativeObject]:
        if len(self.pack) < 32 or self.pack[:4] != b"PACK":
            raise ValueError("Invalid packfile header")
        version, count = struct.unpack(">II", self.pack[4:12])
        if version not in (2, 3):
            raise ValueError(f"Unsupported packfile version: {version}")
        expected_checksum = self.pack[-20:]
        if hashlib.sha1(self.pack[:-20]).digest() != expected_checksum:
            raise ValueError("Packfile checksum mismatch")

        entries: List[_PackEntry] = []
        offset = 12
        for _ in range(count):
            entry_offset = offset
            packed_type, size, offset = self._read_object_header(offset)
            base_offset: Optional[int] = None
            base_oid: Optional[str] = None
            if packed_type == 6:
                distance, offset = self._read_offset_distance(offset)
                base_offset = entry_offset - distance
            elif packed_type == 7:
                base_oid = self.pack[offset:offset + 20].hex()
                offset += 20

            payload, consumed = self._inflate(offset)
            offset += consumed
            if len(payload) != size:
                raise ValueError(
                    f"Packed object at {entry_offset} declared {size} bytes, "
                    f"decoded {len(payload)}"
                )
            entries.append(
                _PackEntry(
                    offset=entry_offset,
                    packed_type=packed_type,
                    data=payload,
                    base_offset=base_offset,
                    base_oid=base_oid,
                )
            )
        if offset != len(self.pack) - 20:
            raise ValueError("Unexpected bytes between pack entries and checksum")

        resolved_by_offset: Dict[int, NativeObject] = {}
        resolved_by_oid: Dict[str, NativeObject] = {}
        unresolved = list(entries)
        while unresolved:
            remaining: List[_PackEntry] = []
            progress = False
            for entry in unresolved:
                obj = self._resolve_entry(entry, resolved_by_offset, resolved_by_oid)
                if obj is None:
                    remaining.append(entry)
                    continue
                resolved_by_offset[entry.offset] = obj
                resolved_by_oid[obj.oid] = obj
                progress = True
            if not progress:
                raise ValueError("Pack contains unresolved delta bases (thin packs unsupported)")
            unresolved = remaining
        return resolved_by_oid

    def _read_object_header(self, offset: int) -> Tuple[int, int, int]:
        byte = self.pack[offset]
        offset += 1
        packed_type = (byte >> 4) & 0x07
        size = byte & 0x0F
        shift = 4
        while byte & 0x80:
            byte = self.pack[offset]
            offset += 1
            size |= (byte & 0x7F) << shift
            shift += 7
        return packed_type, size, offset

    def _read_offset_distance(self, offset: int) -> Tuple[int, int]:
        byte = self.pack[offset]
        offset += 1
        distance = byte & 0x7F
        while byte & 0x80:
            byte = self.pack[offset]
            offset += 1
            distance = ((distance + 1) << 7) | (byte & 0x7F)
        return distance, offset

    def _inflate(self, offset: int) -> Tuple[bytes, int]:
        compressed = self.pack[offset:-20]
        inflater = zlib.decompressobj()
        payload = inflater.decompress(compressed)
        payload += inflater.flush()
        if not inflater.eof:
            raise ValueError("Truncated compressed object in pack")
        return payload, len(compressed) - len(inflater.unused_data)

    def _resolve_entry(
        self,
        entry: _PackEntry,
        by_offset: Dict[int, NativeObject],
        by_oid: Dict[str, NativeObject],
    ) -> Optional[NativeObject]:
        if entry.packed_type in self._TYPE_NAMES:
            type_name = self._TYPE_NAMES[entry.packed_type]
            data = entry.data
        elif entry.packed_type == 6:
            base = by_offset.get(entry.base_offset or -1)
            if base is None:
                return None
            type_name = base.type_name
            data = self._apply_delta(base.data, entry.data)
        elif entry.packed_type == 7:
            base = by_oid.get(entry.base_oid or "")
            if base is None:
                return None
            type_name = base.type_name
            data = self._apply_delta(base.data, entry.data)
        else:
            raise ValueError(f"Unsupported packed object type: {entry.packed_type}")

        canonical = f"{type_name} {len(data)}\0".encode() + data
        return NativeObject(type_name, data, hashlib.sha1(canonical).hexdigest())

    @classmethod
    def _apply_delta(cls, base: bytes, delta: bytes) -> bytes:
        source_size, offset = cls._read_varint(delta, 0)
        result_size, offset = cls._read_varint(delta, offset)
        if source_size != len(base):
            raise ValueError("Delta base size mismatch")

        result = bytearray()
        while offset < len(delta):
            opcode = delta[offset]
            offset += 1
            if opcode & 0x80:
                copy_offset = 0
                copy_size = 0
                for bit, shift in ((0x01, 0), (0x02, 8), (0x04, 16), (0x08, 24)):
                    if opcode & bit:
                        copy_offset |= delta[offset] << shift
                        offset += 1
                for bit, shift in ((0x10, 0), (0x20, 8), (0x40, 16)):
                    if opcode & bit:
                        copy_size |= delta[offset] << shift
                        offset += 1
                if copy_size == 0:
                    copy_size = 0x10000
                result.extend(base[copy_offset:copy_offset + copy_size])
            elif opcode:
                result.extend(delta[offset:offset + opcode])
                offset += opcode
            else:
                raise ValueError("Invalid zero opcode in delta")

        if len(result) != result_size:
            raise ValueError("Delta result size mismatch")
        return bytes(result)

    @staticmethod
    def _read_varint(data: bytes, offset: int) -> Tuple[int, int]:
        result = 0
        shift = 0
        while True:
            byte = data[offset]
            offset += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result, offset
            shift += 7


class NativeImporter:
    """Convert unpacked SHA-1 Git objects into pygit's SHA-256 object model."""

    def __init__(
        self,
        store: ObjectStore,
        objects: Dict[str, NativeObject],
        known: Optional[Dict[str, str]] = None,
    ) -> None:
        self.store = store
        self.objects = objects
        self.converted: Dict[str, str] = dict(known or {})

    def import_oid(self, oid: str) -> str:
        stack: List[Tuple[str, bool]] = [(oid, False)]
        visiting: Set[str] = set()
        while stack:
            current, ready = stack.pop()
            if current in self.converted:
                continue
            native = self.objects.get(current)
            if native is None:
                raise KeyError(f"Fetched pack is missing object {current}")
            if ready:
                self.converted[current] = self._convert_one(native)
                visiting.discard(current)
                continue
            if current in visiting:
                raise ValueError(f"Cycle detected while importing object {current}")
            visiting.add(current)
            stack.append((current, True))
            for dependency in reversed(self._dependencies(native)):
                if dependency not in self.converted:
                    stack.append((dependency, False))
        return self.converted[oid]

    def _dependencies(self, obj: NativeObject) -> List[str]:
        if obj.type_name == "tree":
            return [oid for _, _, oid in self._parse_tree(obj.data)]
        if obj.type_name == "commit":
            tree, parents, _, _, _ = self._parse_commit(obj.data)
            return [tree, *parents]
        if obj.type_name == "tag":
            return [self._tag_target(obj.data)]
        return []

    def _convert_one(self, obj: NativeObject) -> str:
        if obj.type_name == "blob":
            return self.store.write(BlobObject(obj.data))
        if obj.type_name == "tree":
            tree = TreeObject()
            for mode, name, oid in self._parse_tree(obj.data):
                tree.add_entry(mode, name, self.converted[oid])
            return self.store.write(tree)
        if obj.type_name == "commit":
            tree, parents, author, committer, message = self._parse_commit(obj.data)
            return self.store.write(
                CommitObject(
                    tree=self.converted[tree],
                    parents=[self.converted[parent] for parent in parents],
                    author=author,
                    committer=committer,
                    message=message,
                )
            )
        if obj.type_name == "tag":
            return self.converted[self._tag_target(obj.data)]
        raise ValueError(f"Unsupported native object type: {obj.type_name}")

    @staticmethod
    def _parse_tree(data: bytes) -> List[Tuple[str, str, str]]:
        entries: List[Tuple[str, str, str]] = []
        offset = 0
        while offset < len(data):
            space = data.index(b" ", offset)
            null = data.index(b"\x00", space)
            mode = data[offset:space].decode()
            if mode == "40000":
                mode = "040000"
            name = data[space + 1:null].decode("utf-8", errors="replace")
            oid = data[null + 1:null + 21].hex()
            entries.append((mode, name, oid))
            offset = null + 21
        return entries

    @classmethod
    def _parse_commit(
        cls,
        data: bytes,
    ) -> Tuple[str, List[str], Identity, Identity, str]:
        header, _, message = data.decode("utf-8", errors="replace").partition("\n\n")
        tree = ""
        parents: List[str] = []
        author = Identity("Unknown", "unknown@example.com")
        committer = author
        for line in header.splitlines():
            if line.startswith("tree "):
                tree = line[5:]
            elif line.startswith("parent "):
                parents.append(line[7:])
            elif line.startswith("author "):
                author = cls._parse_identity(line[7:])
            elif line.startswith("committer "):
                committer = cls._parse_identity(line[10:])
        if not tree:
            raise ValueError("Remote commit has no tree")
        return tree, parents, author, committer, message

    @staticmethod
    def _parse_identity(value: str) -> Identity:
        try:
            return Identity.decode(value)
        except (ValueError, IndexError):
            return Identity("Unknown", "unknown@example.com")

    @staticmethod
    def _tag_target(data: bytes) -> str:
        for line in data.decode("utf-8", errors="replace").splitlines():
            if line.startswith("object "):
                return line[7:]
        raise ValueError("Remote tag object has no target")
