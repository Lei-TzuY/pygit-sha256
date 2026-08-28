from __future__ import annotations

from pygit import Repository
from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    SmartHttpV2QueryClient,
    build_ls_refs_request,
    parse_capability_advertisement,
    parse_ls_refs_response,
)
from pygit.remote import Advertisement, pkt_line
from pygit.remote_query import ls_remote


def _caps() -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "agent": "git/2.47.3",
            "ls-refs": "unborn",
            "fetch": "shallow wait-for-done",
            "object-format": "sha1",
        }
    )


def test_parse_protocol_v2_capabilities():
    body = (
        pkt_line(b"version 2\n")
        + pkt_line(b"agent=git/2.47.3\n")
        + pkt_line(b"ls-refs=unborn\n")
        + pkt_line(b"fetch=shallow wait-for-done\n")
        + pkt_line(b"object-format=sha1\n")
        + b"0000"
    )
    caps = parse_capability_advertisement(body)
    assert caps is not None
    assert caps.supports("ls-refs")
    assert caps.feature("ls-refs", "unborn")
    assert caps.value("object-format") == "sha1"


def test_protocol_v0_advertisement_is_a_clean_v2_fallback():
    sha = "a" * 40
    body = (
        pkt_line(b"# service=git-upload-pack\n")
        + b"0000"
        + pkt_line(f"{sha} HEAD\\0multi_ack\n".encode())
        + b"0000"
    )
    assert parse_capability_advertisement(body) is None


def test_v2_capabilities_reject_non_sha1_remote_format():
    body = (
        pkt_line(b"version 2\n")
        + pkt_line(b"ls-refs\n")
        + pkt_line(b"object-format=sha256\n")
        + b"0000"
    )
    try:
        parse_capability_advertisement(body)
    except RuntimeError as exc:
        assert "expected sha1" in str(exc)
    else:
        raise AssertionError("protocol-v2 SHA-256 remote should be rejected")


def test_ls_refs_request_asks_for_symrefs_peeling_unborn_and_prefixes():
    body = build_ls_refs_request(_caps(), prefixes=["refs/heads/", "refs/tags/v"])
    assert body.startswith(pkt_line(b"command=ls-refs\n"))
    assert pkt_line(b"agent=pygit/0.1\n") in body
    assert b"0001" in body
    assert pkt_line(b"symrefs\n") in body
    assert pkt_line(b"peel\n") in body
    assert pkt_line(b"unborn\n") in body
    assert pkt_line(b"ref-prefix refs/heads/\n") in body
    assert pkt_line(b"ref-prefix refs/tags/v\n") in body
    assert body.endswith(b"0000")


def test_parse_ls_refs_preserves_symrefs_and_peeled_tag_helpers():
    head = "a" * 40
    tag = "b" * 40
    peeled = "c" * 40
    body = (
        pkt_line(f"{head} HEAD symref-target:refs/heads/main\n".encode())
        + pkt_line(f"{head} refs/heads/main\n".encode())
        + pkt_line(f"{tag} refs/tags/v1 peeled:{peeled}\n".encode())
        + b"0000"
    )
    adv = parse_ls_refs_response(body, _caps())
    assert adv.refs["HEAD"] == head
    assert adv.refs["refs/heads/main"] == head
    assert adv.refs["refs/tags/v1"] == tag
    assert adv.refs["refs/tags/v1^{}"] == peeled
    assert adv.symrefs["HEAD"] == "refs/heads/main"


def test_parse_ls_refs_keeps_unborn_head_symref_without_fake_oid():
    body = pkt_line(b"unborn HEAD symref-target:refs/heads/main\n") + b"0000"
    adv = parse_ls_refs_response(body, _caps())
    assert "HEAD" not in adv.refs
    assert adv.symrefs == {"HEAD": "refs/heads/main"}


def test_v2_http_discovery_sends_header_and_ls_refs_command(monkeypatch):
    sha = "a" * 40
    capabilities = (
        pkt_line(b"version 2\n")
        + pkt_line(b"agent=git/2.47.3\n")
        + pkt_line(b"ls-refs=unborn\n")
        + pkt_line(b"object-format=sha1\n")
        + b"0000"
    )
    refs = (
        pkt_line(f"{sha} HEAD symref-target:refs/heads/main\n".encode())
        + pkt_line(f"{sha} refs/heads/main\n".encode())
        + b"0000"
    )
    requests = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.body

    responses = iter([Response(capabilities), Response(refs)])

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adv = SmartHttpV2QueryClient("https://example.test/repo.git").discover_refs()
    assert adv is not None
    assert adv.refs["refs/heads/main"] == sha
    assert requests[0].headers["Git-protocol"] == "version=2"
    assert requests[0].full_url.endswith("/info/refs?service=git-upload-pack")
    assert requests[1].full_url.endswith("/git-upload-pack")
    assert pkt_line(b"command=ls-refs\n") in requests[1].data


def test_ls_remote_uses_protocol_v2_when_config_requests_it(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    sha = "a" * 40
    calls = []

    class V2:
        def __init__(self, url):
            calls.append(("v2", url))

        def discover_refs(self):
            return Advertisement(
                {"HEAD": sha, "refs/heads/main": sha},
                {"object-format=sha1", "ls-refs"},
                {"HEAD": "refs/heads/main"},
            )

    class V0:
        def __init__(self, url):
            raise AssertionError("v0 fallback should not run when v2 succeeds")

    monkeypatch.setattr("pygit.remote_query.SmartHttpV2QueryClient", V2)
    monkeypatch.setattr("pygit.remote_query.SmartHttpClient", V0)
    result = ls_remote("origin", repo=repo)
    assert calls == [("v2", "https://example.test/repo.git")]
    assert [ref.name for ref in result.refs] == ["HEAD", "refs/heads/main"]


def test_ls_remote_protocol_v2_cleanly_falls_back_to_v0(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    sha = "d" * 40
    calls = []

    class V2:
        def __init__(self, url):
            calls.append("v2")

        def discover_refs(self):
            return None

    class V0:
        def __init__(self, url):
            calls.append("v0")

        def discover(self):
            return Advertisement({"refs/heads/main": sha}, set(), {})

    monkeypatch.setattr("pygit.remote_query.SmartHttpV2QueryClient", V2)
    monkeypatch.setattr("pygit.remote_query.SmartHttpClient", V0)
    result = ls_remote("origin", repo=repo)
    assert calls == ["v2", "v0"]
    assert result.refs[0].oid == sha
