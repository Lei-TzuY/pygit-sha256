from __future__ import annotations

import hashlib
from email.message import Message

import pytest

import pygit.protocol_v2_packfile_uri_batch as batch_module
from pygit.protocol_v2_packfile_uri_batch import download_packfile_uris
from pygit.protocol_v2_packfile_uri_download import DownloadedPackfileUri
from pygit.protocol_v2_packfile_uris import PackfileUriDescriptor
from pygit.remote import NativeObject, build_pack


def _native_blob(data: bytes) -> NativeObject:
    canonical = f"blob {len(data)}\0".encode() + data
    return NativeObject("blob", data, hashlib.sha1(canonical).hexdigest())


def _pack(data: bytes) -> bytes:
    return build_pack([_native_blob(data)])


def _descriptor(pack: bytes, name: str) -> PackfileUriDescriptor:
    return PackfileUriDescriptor(
        pack[-20:].hex(),
        f"https://cdn.example.test/{name}.pack".encode("ascii"),
    )


class _Response:
    def __init__(self, body: bytes, url: str):
        self.body = body
        self.url = url
        self.offset = 0
        self.headers = Message()
        self.headers["Content-Length"] = str(len(body))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_batch_downloads_all_descriptors_and_merges_native_objects():
    pack_a = _pack(b"phase320-a\n")
    pack_b = _pack(b"phase320-b\n")
    descriptor_a = _descriptor(pack_a, "a")
    descriptor_b = _descriptor(pack_b, "b")
    bodies = {
        descriptor_a.uri.decode(): pack_a,
        descriptor_b.uri.decode(): pack_b,
    }
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, timeout))
        return _Response(bodies[request.full_url], request.full_url)

    result = download_packfile_uris(
        [descriptor_a, descriptor_b],
        timeout=9,
        opener=opener,
    )

    assert [item.descriptor for item in result.downloads] == [descriptor_a, descriptor_b]
    assert result.total_bytes == len(pack_a) + len(pack_b)
    assert set(result.objects) == {_native_blob(b"phase320-a\n").oid, _native_blob(b"phase320-b\n").oid}
    assert seen == [(descriptor_a.uri.decode(), 9), (descriptor_b.uri.decode(), 9)]


def test_batch_rejects_duplicate_pack_checksums_before_network_access():
    pack = _pack(b"duplicate\n")
    descriptor = _descriptor(pack, "one")
    duplicate = PackfileUriDescriptor(descriptor.pack_hash, b"https://cdn.example.test/two.pack")

    with pytest.raises(ValueError, match="duplicate pack checksums"):
        download_packfile_uris(
            [descriptor, duplicate],
            opener=lambda request, timeout: (_ for _ in ()).throw(
                AssertionError("duplicate descriptors must fail before network access")
            ),
        )


def test_batch_rejects_pack_count_before_network_access():
    pack_a = _pack(b"a\n")
    pack_b = _pack(b"b\n")

    with pytest.raises(ValueError, match="pack-count limit"):
        download_packfile_uris(
            [_descriptor(pack_a, "a"), _descriptor(pack_b, "b")],
            max_packs=1,
            opener=lambda request, timeout: (_ for _ in ()).throw(
                AssertionError("pack-count validation must precede network access")
            ),
        )


def test_batch_enforces_cumulative_budget_on_later_pack():
    pack_a = _pack(b"first pack\n")
    pack_b = _pack(b"second pack\n")
    descriptor_a = _descriptor(pack_a, "a")
    descriptor_b = _descriptor(pack_b, "b")
    bodies = {
        descriptor_a.uri.decode(): pack_a,
        descriptor_b.uri.decode(): pack_b,
    }
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        return _Response(bodies[request.full_url], request.full_url)

    with pytest.raises(ValueError, match="exceeds configured size limit"):
        download_packfile_uris(
            [descriptor_a, descriptor_b],
            max_pack_bytes=max(len(pack_a), len(pack_b)) + 10,
            max_total_bytes=len(pack_a) + len(pack_b) - 1,
            opener=opener,
        )

    assert calls == [descriptor_a.uri.decode(), descriptor_b.uri.decode()]


def test_batch_deduplicates_identical_native_object_across_verified_packs():
    pack_a = _pack(b"same object\n")
    pack_b = _pack(b"same object\n")
    descriptor_a = _descriptor(pack_a, "a")
    # A real identical single-object pack has the same checksum, which Phase320
    # correctly rejects as a duplicate descriptor.  Stub Phase319 results here
    # to exercise only the cross-pack native-object merge rule.
    descriptor_b = PackfileUriDescriptor("b" * 40, b"https://cdn.example.test/b.pack")
    obj = _native_blob(b"same object\n")

    def fake_download(descriptor, **kwargs):
        return DownloadedPackfileUri(descriptor, descriptor.uri.decode(), pack_a, {obj.oid: obj})

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(batch_module, "download_packfile_uri", fake_download)
    try:
        result = download_packfile_uris([descriptor_a, descriptor_b])
    finally:
        monkeypatch.undo()

    assert result.objects == {obj.oid: obj}


def test_batch_rejects_conflicting_native_object_identity():
    descriptor_a = PackfileUriDescriptor("a" * 40, b"https://cdn.example.test/a.pack")
    descriptor_b = PackfileUriDescriptor("b" * 40, b"https://cdn.example.test/b.pack")
    oid = "1" * 40
    first = NativeObject("blob", b"first", oid)
    second = NativeObject("blob", b"second", oid)
    results = {
        descriptor_a.pack_hash: DownloadedPackfileUri(descriptor_a, descriptor_a.uri.decode(), b"x", {oid: first}),
        descriptor_b.pack_hash: DownloadedPackfileUri(descriptor_b, descriptor_b.uri.decode(), b"y", {oid: second}),
    }

    def fake_download(descriptor, **kwargs):
        return results[descriptor.pack_hash]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(batch_module, "download_packfile_uri", fake_download)
    try:
        with pytest.raises(ValueError, match="conflicting objects"):
            download_packfile_uris([descriptor_a, descriptor_b])
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0}, "timeout"),
        ({"max_pack_bytes": 0}, "max_pack_bytes"),
        ({"max_total_bytes": 0}, "max_total_bytes"),
        ({"max_packs": 0}, "max_packs"),
    ],
)
def test_batch_rejects_non_positive_limits(kwargs, message):
    pack = _pack(b"limits\n")
    with pytest.raises(ValueError, match=message):
        download_packfile_uris([_descriptor(pack, "x")], **kwargs)


def test_batch_requires_at_least_one_descriptor():
    with pytest.raises(ValueError, match="at least one descriptor"):
        download_packfile_uris([])


def test_batch_rejects_non_descriptor_values_before_network_access():
    with pytest.raises(TypeError, match="non-descriptor"):
        download_packfile_uris([object()])
