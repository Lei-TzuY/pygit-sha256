from pathlib import Path

import pytest

from pygit import Repository
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.protocol_v2_packfile_uri_transaction import PackfileUriFetchTransactionResult
from pygit.protocol_v2_packfile_uris import (
    PackfileUriDescriptor,
    SmartHttpV2PackfileUriClient,
    V2PackfileUriFetchResult,
)
from pygit.refs import ZERO_SHA
from pygit.remote import Advertisement
import pygit.protocol_v2_packfile_uri_repository as repository_fetch


NATIVE = "a" * 40
OTHER = "b" * 40
LOCAL = "c" * 64
DESCRIPTOR = PackfileUriDescriptor(
    "d" * 40,
    b"https://cdn.example.test/objects.pack",
)


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _advertisement(oid: str = NATIVE) -> Advertisement:
    return Advertisement(
        refs={
            "HEAD": oid,
            "refs/heads/main": oid,
            "refs/tags/v1": oid,
            "refs/tags/v1^{}": OTHER,
        },
        capabilities=set(),
        symrefs={"HEAD": "refs/heads/main"},
    )


def _transport(oid: str = NATIVE) -> V2PackfileUriFetchResult:
    return V2PackfileUriFetchResult(
        _advertisement(oid),
        {"inline-native": "inline-object"},  # type: ignore[dict-item]
        ("1" * 40,),
        ("2" * 40,),
        (DESCRIPTOR,),
    )


def _transaction() -> PackfileUriFetchTransactionResult:
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    certificate = PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})
    return PackfileUriFetchTransactionResult(
        batch,
        staged,
        certificate,
        {"refs/heads/fetched": LOCAL},
    )


def _plan():
    return (
        {NATIVE: b"commit"},
        {"refs/heads/fetched": PackfileUriRefPublication(NATIVE, ZERO_SHA)},
    )


def test_connects_phase318_result_to_phase326_transaction_without_identity_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git", timeout=19)
    transport = _transport()
    transaction = _transaction()
    expected_roots, publications = _plan()
    fetch_calls = []
    transaction_calls = []

    def fake_fetch(protocols, **kwargs):
        fetch_calls.append((protocols, kwargs))
        return transport

    def fake_transaction(received_repo, descriptors, inline_objects, roots, refs, **kwargs):
        transaction_calls.append(
            (received_repo, tuple(descriptors), inline_objects, roots, refs, kwargs)
        )
        return transaction

    monkeypatch.setattr(client, "fetch_with_packfile_uris", fake_fetch)
    monkeypatch.setattr(
        repository_fetch,
        "execute_packfile_uri_fetch_transaction",
        fake_transaction,
    )

    result = repository_fetch.fetch_packfile_uris_into_repository(
        repo,
        client,
        ["HTTPS"],
        expected_roots,
        publications,
        haves=["e" * 40],
        advertisement=_advertisement(),
        shallow=["f" * 40],
        deepen=2,
        deepen_relative=True,
        message="fetch: phase327",
        max_pack_bytes=101,
        max_total_bytes=202,
        max_packs=3,
        opener="external-opener",
    )

    assert result is not None
    assert result.transport is transport
    assert result.transaction is transaction
    assert fetch_calls == [
        (
            ("https",),
            {
                "haves": ["e" * 40],
                "advertisement": _advertisement(),
                "shallow": ["f" * 40],
                "deepen": 2,
                "deepen_relative": True,
            },
        )
    ]
    assert len(transaction_calls) == 1
    call = transaction_calls[0]
    assert call[0] is repo
    assert call[1] == (DESCRIPTOR,)
    assert call[2] is transport.objects
    assert call[3] is expected_roots
    assert call[4] is publications
    assert call[5] == {
        "message": "fetch: phase327",
        "timeout": 19,
        "max_pack_bytes": 101,
        "max_total_bytes": 202,
        "max_packs": 3,
        "opener": "external-opener",
    }
    assert NATIVE in call[3]
    assert len(NATIVE) == 40
    assert len(result.transaction.published_refs["refs/heads/fetched"]) == 64


def test_explicit_external_timeout_overrides_transport_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git", timeout=17)
    expected_roots, publications = _plan()
    seen = {}

    monkeypatch.setattr(client, "fetch_with_packfile_uris", lambda *a, **k: _transport())

    def fake_transaction(*args, **kwargs):
        seen.update(kwargs)
        return _transaction()

    monkeypatch.setattr(
        repository_fetch,
        "execute_packfile_uri_fetch_transaction",
        fake_transaction,
    )

    repository_fetch.fetch_packfile_uris_into_repository(
        repo,
        client,
        ["https"],
        expected_roots,
        publications,
        external_timeout=7,
    )
    assert seen["timeout"] == 7


def test_v0_fallback_does_not_enter_repository_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git")
    expected_roots, publications = _plan()
    monkeypatch.setattr(client, "fetch_with_packfile_uris", lambda *a, **k: None)

    def should_not_run(*args, **kwargs):
        raise AssertionError("v0 fallback must not enter repository transaction")

    monkeypatch.setattr(
        repository_fetch,
        "execute_packfile_uri_fetch_transaction",
        should_not_run,
    )

    assert (
        repository_fetch.fetch_packfile_uris_into_repository(
            repo, client, ["https"], expected_roots, publications
        )
        is None
    )


def test_rejects_expected_root_not_advertised_by_this_fetch_before_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git")
    monkeypatch.setattr(client, "fetch_with_packfile_uris", lambda *a, **k: _transport())

    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unbound roots must not enter repository transaction")

    monkeypatch.setattr(
        repository_fetch,
        "execute_packfile_uri_fetch_transaction",
        should_not_run,
    )

    with pytest.raises(ValueError, match="not advertised by this transport fetch"):
        repository_fetch.fetch_packfile_uris_into_repository(
            repo,
            client,
            ["https"],
            {OTHER: b"commit"},
            {"refs/heads/fetched": PackfileUriRefPublication(OTHER, ZERO_SHA)},
        )

    assert called is False


def test_peeled_tag_oid_is_not_treated_as_a_requested_transport_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git")
    monkeypatch.setattr(client, "fetch_with_packfile_uris", lambda *a, **k: _transport())

    with pytest.raises(ValueError, match="not advertised by this transport fetch"):
        repository_fetch.fetch_packfile_uris_into_repository(
            repo,
            client,
            ["https"],
            {OTHER: b"commit"},
            {"refs/heads/fetched": PackfileUriRefPublication(OTHER, ZERO_SHA)},
        )


def test_publication_root_must_be_declared_in_expected_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git")
    advertisement = Advertisement(
        refs={"refs/heads/main": NATIVE, "refs/heads/other": OTHER},
        capabilities=set(),
        symrefs={},
    )
    result = V2PackfileUriFetchResult(advertisement, {}, (), (), ())
    monkeypatch.setattr(client, "fetch_with_packfile_uris", lambda *a, **k: result)

    with pytest.raises(ValueError, match="not declared in expected_roots"):
        repository_fetch.fetch_packfile_uris_into_repository(
            repo,
            client,
            ["https"],
            {NATIVE: b"commit"},
            {"refs/heads/fetched": PackfileUriRefPublication(OTHER, ZERO_SHA)},
        )


def test_case_aliases_cannot_duplicate_expected_native_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git")
    monkeypatch.setattr(client, "fetch_with_packfile_uris", lambda *a, **k: _transport())

    with pytest.raises(ValueError, match="duplicate native identities"):
        repository_fetch.fetch_packfile_uris_into_repository(
            repo,
            client,
            ["https"],
            {NATIVE: b"commit", NATIVE.upper(): b"commit"},
            {"refs/heads/fetched": PackfileUriRefPublication(NATIVE, ZERO_SHA)},
        )


def test_invalid_uri_protocol_is_rejected_before_transport_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git")
    called = False

    def should_not_fetch(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid protocol must fail before transport I/O")

    monkeypatch.setattr(client, "fetch_with_packfile_uris", should_not_fetch)
    expected_roots, publications = _plan()

    with pytest.raises(ValueError, match="unsupported protocol-v2 packfile URI protocol"):
        repository_fetch.fetch_packfile_uris_into_repository(
            repo, client, ["ssh"], expected_roots, publications
        )
    assert called is False


def test_requires_concrete_repository_and_packfile_uri_client(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    client = SmartHttpV2PackfileUriClient("https://example.test/repo.git")
    expected_roots, publications = _plan()

    with pytest.raises(TypeError, match="requires a Repository"):
        repository_fetch.fetch_packfile_uris_into_repository(  # type: ignore[arg-type]
            object(), client, ["https"], expected_roots, publications
        )
    with pytest.raises(TypeError, match="SmartHttpV2PackfileUriClient"):
        repository_fetch.fetch_packfile_uris_into_repository(  # type: ignore[arg-type]
            repo, object(), ["https"], expected_roots, publications
        )
