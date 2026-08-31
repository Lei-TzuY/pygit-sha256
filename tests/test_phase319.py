from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pygit.protocol_v2_packfile_uri_download import (
    download_packfile_uri,
    verify_packfile_uri_payload,
)
from pygit.protocol_v2_packfile_uris import PackfileUriDescriptor
from pygit.remote import NativeObject, build_pack


def _native_blob(data: bytes = b"phase319 external blob\n") -> NativeObject:
    canonical = f"blob {len(data)}\0".encode() + data
    return NativeObject("blob", data, hashlib.sha1(canonical).hexdigest())


def _pack(data: bytes = b"phase319 external blob\n") -> bytes:
    return build_pack([_native_blob(data)])


def _descriptor(pack: bytes, uri: bytes = b"https://cdn.example.test/blob.pack") -> PackfileUriDescriptor:
    return PackfileUriDescriptor(pack[-20:].hex(), uri)


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://cdn.example.test/blob.pack",
        content_length: str | None = None,
    ):
        self.body = body
        self.offset = 0
        self.url = url
        self.headers = Message()
        if content_length is not None:
            self.headers["Content-Length"] = content_length

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


def test_verify_packfile_uri_payload_accepts_matching_pack_and_parses_objects():
    pack = _pack()
    descriptor = _descriptor(pack)

    objects = verify_packfile_uri_payload(descriptor, pack)

    assert len(objects) == 1
    assert next(iter(objects.values())).type_name == "blob"
    assert next(iter(objects.values())).data == b"phase319 external blob\n"


def test_verify_packfile_uri_payload_rejects_non_pack():
    descriptor = PackfileUriDescriptor("a" * 40, b"https://cdn.example.test/x.pack")
    with pytest.raises(ValueError, match="not a native PACK"):
        verify_packfile_uri_payload(descriptor, b"not-a-pack")


def test_verify_packfile_uri_payload_rejects_corrupt_internal_trailer():
    pack = bytearray(_pack())
    pack[12] ^= 1
    descriptor = PackfileUriDescriptor(bytes(pack[-20:]).hex(), b"https://cdn.example.test/x.pack")

    with pytest.raises(ValueError, match="invalid pack checksum"):
        verify_packfile_uri_payload(descriptor, bytes(pack))


def test_verify_packfile_uri_payload_rejects_descriptor_mismatch():
    pack = _pack()
    descriptor = PackfileUriDescriptor("0" * 40, b"https://cdn.example.test/x.pack")

    with pytest.raises(ValueError, match="does not match descriptor"):
        verify_packfile_uri_payload(descriptor, pack)


def test_download_packfile_uri_uses_get_and_explicit_accept_header():
    pack = _pack()
    descriptor = _descriptor(pack)
    seen = {}

    def opener(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(pack, content_length=str(len(pack)))

    result = download_packfile_uri(descriptor, timeout=7, opener=opener)

    assert result.pack == pack
    assert len(result.objects) == 1
    assert seen["timeout"] == 7
    assert seen["request"].method == "GET"
    assert seen["request"].get_header("Accept") == "application/x-git-packed-objects"


@pytest.mark.parametrize(
    "uri",
    [
        b"file:///tmp/p.pack",
        b"ssh://git.example.test/p.pack",
        b"https:///missing-host.pack",
        b"https://user:secret@example.test/p.pack",
        b"https://example.test/non-ascii-\xff.pack",
    ],
)
def test_download_packfile_uri_rejects_unsafe_or_unsupported_urls(uri):
    pack = _pack()
    descriptor = _descriptor(pack, uri)

    with pytest.raises(ValueError):
        download_packfile_uri(
            descriptor,
            opener=lambda request, timeout: (_ for _ in ()).throw(
                AssertionError("invalid URI must fail before network access")
            ),
        )


def test_download_packfile_uri_rejects_https_to_http_redirect():
    pack = _pack()
    descriptor = _descriptor(pack)

    def opener(request, timeout):
        return _Response(pack, url="http://cdn.example.test/blob.pack")

    with pytest.raises(ValueError, match="downgraded HTTPS to HTTP"):
        download_packfile_uri(descriptor, opener=opener)


def test_download_packfile_uri_allows_http_to_https_redirect():
    pack = _pack()
    descriptor = _descriptor(pack, b"http://cdn.example.test/blob.pack")

    def opener(request, timeout):
        return _Response(pack, url="https://cdn.example.test/blob.pack")

    result = download_packfile_uri(descriptor, opener=opener)
    assert result.final_url == "https://cdn.example.test/blob.pack"


def test_download_packfile_uri_rejects_redirect_outside_http_schemes():
    pack = _pack()
    descriptor = _descriptor(pack)

    def opener(request, timeout):
        return _Response(pack, url="file:///tmp/blob.pack")

    with pytest.raises(ValueError, match=r"left the allowed HTTP\(S\) schemes"):
        download_packfile_uri(descriptor, opener=opener)


def test_download_packfile_uri_rejects_content_length_above_limit_before_reading():
    pack = _pack()
    descriptor = _descriptor(pack)
    response = _Response(pack, content_length=str(len(pack) + 10))

    with pytest.raises(ValueError, match="exceeds configured size limit"):
        download_packfile_uri(
            descriptor,
            max_bytes=len(pack),
            opener=lambda request, timeout: response,
        )

    assert response.offset == 0


def test_download_packfile_uri_rejects_invalid_content_length():
    pack = _pack()
    descriptor = _descriptor(pack)

    with pytest.raises(ValueError, match="invalid Content-Length"):
        download_packfile_uri(
            descriptor,
            opener=lambda request, timeout: _Response(pack, content_length="NaN"),
        )


def test_download_packfile_uri_stream_limit_works_without_content_length():
    pack = _pack()
    descriptor = _descriptor(pack)

    with pytest.raises(ValueError, match="exceeds configured size limit"):
        download_packfile_uri(
            descriptor,
            max_bytes=len(pack) - 1,
            opener=lambda request, timeout: _Response(pack),
        )


@pytest.mark.parametrize("timeout", [0, -1, 1.5, True])
def test_download_packfile_uri_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout must be a positive integer"):
        download_packfile_uri(
            PackfileUriDescriptor("a" * 40, b"https://example.test/x.pack"),
            timeout=timeout,
        )


@pytest.mark.parametrize("max_bytes", [0, -1, 1.5, True])
def test_download_packfile_uri_rejects_invalid_size_limit(max_bytes):
    with pytest.raises(ValueError, match="max_bytes must be a positive integer"):
        download_packfile_uri(
            PackfileUriDescriptor("a" * 40, b"https://example.test/x.pack"),
            max_bytes=max_bytes,
        )


def test_native_git_pack_downloaded_over_real_local_http_is_verified_and_parsed(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run([git, "init", str(repo)], check=True, stdout=subprocess.PIPE)
    payload = repo / "payload.bin"
    payload.write_bytes(b"phase319 native HTTP pack download\n")
    blob = subprocess.check_output(
        [git, "-C", str(repo), "hash-object", "-w", "payload.bin"],
        text=True,
    ).strip()
    pack = subprocess.run(
        [git, "-C", str(repo), "pack-objects", "--stdout"],
        input=(blob + "\n").encode(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert pack.startswith(b"PACK")
    pack_hash = pack[-20:].hex()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/external.pack":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-git-packed-objects")
            self.send_header("Content-Length", str(len(pack)))
            self.end_headers()
            self.wfile.write(pack)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        descriptor = PackfileUriDescriptor(
            pack_hash,
            f"http://{host}:{port}/external.pack".encode("ascii"),
        )
        downloaded = download_packfile_uri(
            descriptor,
            max_bytes=len(pack) + 1024,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert downloaded.pack == pack
    assert downloaded.descriptor == descriptor
    assert set(downloaded.objects) == {blob}
    assert downloaded.objects[blob].type_name == "blob"
    assert downloaded.objects[blob].data == b"phase319 native HTTP pack download\n"
