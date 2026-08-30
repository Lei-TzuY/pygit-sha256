from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    _validate_fetch_response_for_request,
    parse_fetch_response,
)
from pygit.protocol_v2_shallow_cutoff import (
    SmartHttpV2ShallowCutoffClient,
    build_shallow_cutoff_fetch_request,
    validate_shallow_response_for_request,
)
from pygit.remote import Advertisement, build_pack


def _caps(*, shallow: bool = True) -> ProtocolV2Capabilities:
    fetch = "shallow wait-for-done" if shallow else "wait-for-done"
    return ProtocolV2Capabilities({"fetch": fetch, "object-format": "sha1"})


def _response(*, shallow=(), unshallow=(), pack=None) -> ProtocolV2FetchResponse:
    return ProtocolV2FetchResponse(
        acknowledgments=(),
        ready=False,
        nak=False,
        shallow=tuple(shallow),
        unshallow=tuple(unshallow),
        wanted_refs={},
        pack=pack,
    )


def test_deepen_since_and_repeated_deepen_not_are_emitted_before_wants():
    want = "a" * 40
    existing_shallow = "b" * 40

    body = build_shallow_cutoff_fetch_request(
        _caps(),
        [want],
        shallow=[existing_shallow],
        deepen_since=1704067200,
        deepen_not=["old", "old", "refs/heads/base"],
    )

    shallow_record = f"shallow {existing_shallow}\n".encode()
    since_record = b"deepen-since 1704067200\n"
    first_not = b"deepen-not old\n"
    second_not = b"deepen-not old\n"
    full_not = b"deepen-not refs/heads/base\n"
    want_record = f"want {want}\n".encode()

    shallow_at = body.index(shallow_record)
    since_at = body.index(since_record)
    first_not_at = body.index(first_not, since_at)
    second_not_at = body.index(second_not, first_not_at + 1)
    full_not_at = body.index(full_not)
    want_at = body.index(want_record)

    assert shallow_at < since_at < first_not_at < second_not_at < full_not_at < want_at
    assert body.endswith(b"0000")


@pytest.mark.parametrize("value", [-1, True, "1704067200"])
def test_deepen_since_rejects_invalid_timestamp(value):
    with pytest.raises(ValueError, match="non-negative integer timestamp"):
        build_shallow_cutoff_fetch_request(
            _caps(),
            ["a" * 40],
            deepen_since=value,
        )


def test_deepen_since_zero_matches_native_git_domain():
    body = build_shallow_cutoff_fetch_request(
        _caps(),
        ["a" * 40],
        deepen_since=0,
    )
    assert b"deepen-since 0\n" in body


@pytest.mark.parametrize(
    "revision",
    ["", "refs/heads/has space", "refs/heads/x\nwant deadbeef", "refs/heads/x\tbad"],
)
def test_deepen_not_rejects_unsafe_record_framing(revision):
    with pytest.raises(ValueError, match="deepen-not revision"):
        build_shallow_cutoff_fetch_request(
            _caps(),
            ["a" * 40],
            deepen_not=[revision],
        )


def test_deepen_not_allows_remote_revision_syntax_and_utf8_refnames():
    body = build_shallow_cutoff_fetch_request(
        _caps(),
        ["a" * 40],
        deepen_not=["HEAD", "old", "refs/heads/測試"],
    )
    assert b"deepen-not HEAD\n" in body
    assert b"deepen-not old\n" in body
    assert "deepen-not refs/heads/測試\n".encode() in body


def test_shallow_cutoff_requires_shallow_capability_and_one_cutoff():
    with pytest.raises(RuntimeError, match="does not advertise shallow"):
        build_shallow_cutoff_fetch_request(
            _caps(shallow=False),
            ["a" * 40],
            deepen_since=0,
        )

    with pytest.raises(ValueError, match="requires deepen-since and/or deepen-not"):
        build_shallow_cutoff_fetch_request(_caps(), ["a" * 40])


def test_request_aware_unshallow_must_have_been_declared_shallow():
    requested = "a" * 40
    other = "b" * 40

    validate_shallow_response_for_request(
        _response(unshallow=[requested]),
        requested_shallow=[requested],
    )

    with pytest.raises(ValueError, match="not declared shallow"):
        validate_shallow_response_for_request(
            _response(unshallow=[other]),
            requested_shallow=[requested],
        )


def test_smart_http_client_threads_cutoffs_through_existing_fetch_state_machine(monkeypatch):
    want = "c" * 40
    existing_shallow = "d" * 40
    pack = build_pack([])
    advertisement = Advertisement({"refs/heads/main": want}, set(), {})
    client = SmartHttpV2ShallowCutoffClient("https://example.test/repo.git")
    captured = []

    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())

    def fake_post(body):
        captured.append(body)
        return _response(unshallow=[existing_shallow], pack=pack)

    monkeypatch.setattr(client, "_post_fetch", fake_post)

    result = client.fetch_shallow(
        deepen_since=0,
        deepen_not=["old"],
        advertisement=advertisement,
        shallow=[existing_shallow],
    )

    assert result is not None
    assert result.objects == {}
    assert result.unshallow == (existing_shallow,)
    assert len(captured) == 1
    assert b"deepen-since 0\n" in captured[0]
    assert b"deepen-not old\n" in captured[0]
    assert f"want {want}\n".encode() in captured[0]


def test_native_git_deepen_since_and_deepen_not_round_trip(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run([git, "init", "-b", "main", str(repo)], check=True, stdout=subprocess.PIPE)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.email", "test@example.com"], check=True)

    for day, message in ((1, "one"), (2, "two"), (3, "three")):
        (repo / "f").write_text(message + "\n", encoding="utf-8")
        subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
        env = dict(os.environ)
        stamp = f"2024-01-0{day}T00:00:00Z"
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
        subprocess.run(
            [git, "-C", str(repo), "commit", "-m", message],
            check=True,
            env=env,
            stdout=subprocess.PIPE,
        )

    subprocess.run([git, "-C", str(repo), "branch", "old", "HEAD~1"], check=True)
    head = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    old = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "old"], text=True
    ).strip()

    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"

    since_response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=build_shallow_cutoff_fetch_request(
            _caps(),
            [head],
            deepen_since=1704153600,
        ),
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed_since = parse_fetch_response(since_response)
    _validate_fetch_response_for_request(parsed_since, done=True)
    validate_shallow_response_for_request(parsed_since)
    assert old in parsed_since.shallow
    assert parsed_since.pack is not None

    combined_response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=build_shallow_cutoff_fetch_request(
            _caps(),
            [head],
            deepen_since=1704067200,
            deepen_not=["refs/heads/old"],
        ),
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed_combined = parse_fetch_response(combined_response)
    _validate_fetch_response_for_request(parsed_combined, done=True)
    validate_shallow_response_for_request(parsed_combined)
    assert head in parsed_combined.shallow
    assert parsed_combined.pack is not None
