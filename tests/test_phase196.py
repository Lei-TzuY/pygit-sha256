from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from pygit.fetch_cli_dry_run import run_fetch
from pygit.fetch_refetch import _force_fetch_import_sources, refetch_transport
from pygit.remote import Advertisement


def test_refetch_forces_transfer_with_empty_have_set_even_when_tip_is_known(monkeypatch):
    native = "1" * 40
    pygit_sha = "a" * 64
    calls = []

    class Client:
        def fetch(self, *, haves, advertisement):
            calls.append((list(haves), advertisement))
            return SimpleNamespace(objects={native: object()})

    class Importer:
        def __init__(self, store, objects, known):
            assert objects == {native: object()} or set(objects) == {native}
            assert known[native] == pygit_sha
            self.converted = {native: pygit_sha}

        def import_oid(self, oid):
            assert oid == native
            return pygit_sha

    monkeypatch.setattr("pygit.fetch_refetch.NativeImporter", Importer)
    repo = SimpleNamespace(store=object())
    advertisement = Advertisement(
        {"refs/heads/main": native}, {"side-band-64k"}, {"HEAD": "refs/heads/main"}
    )
    native_map = {pygit_sha: native}
    known = {native: pygit_sha}

    imported, count = _force_fetch_import_sources(
        repo,
        Client(),
        advertisement,
        {"refs/heads/main": native},
        native_map,
        known,
    )

    assert imported == {"refs/heads/main": pygit_sha}
    assert count == 1
    assert calls[0][0] == []
    assert calls[0][1].refs == {"refs/heads/main": native}
    assert native_map[pygit_sha] == native


def test_refetch_scope_patches_all_fetch_import_seams_and_restores_them():
    from pygit import fetch_configured, fetch_direct, fetch_porcelain

    modules = (fetch_configured, fetch_porcelain, fetch_direct)
    originals = [module._fetch_import_sources for module in modules]

    with refetch_transport():
        assert all(module._fetch_import_sources is _force_fetch_import_sources for module in modules)

    assert [module._fetch_import_sources for module in modules] == originals


def test_refetch_scope_restores_seams_after_exception():
    from pygit import fetch_configured

    original = fetch_configured._fetch_import_sources
    try:
        with refetch_transport():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert fetch_configured._fetch_import_sources is original


def test_cli_strips_refetch_before_legacy_fetch_parser(monkeypatch):
    forwarded = []
    entered = []

    @contextmanager
    def scope():
        entered.append(True)
        yield

    monkeypatch.setattr("pygit.fetch_cli_dry_run.refetch_transport", scope)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: forwarded.append(list(argv)) or 0,
    )

    assert run_fetch(["--refetch", "origin"]) == 0
    assert entered == [True]
    assert forwarded == [["origin"]]


def test_refetch_after_option_terminator_remains_literal_refspec(monkeypatch):
    forwarded = []

    def forbidden_scope():
        raise AssertionError("literal --refetch must not activate transport policy")

    monkeypatch.setattr("pygit.fetch_cli_dry_run.refetch_transport", forbidden_scope)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: forwarded.append(list(argv)) or 0,
    )

    assert run_fetch(["origin", "--", "--refetch"]) == 0
    assert forwarded == [["origin", "--", "--refetch"]]


def test_refetch_composes_with_dry_run(monkeypatch):
    events = []

    @contextmanager
    def refetch_scope():
        events.append("refetch-enter")
        yield
        events.append("refetch-exit")

    @contextmanager
    def dry_scope(repo):
        events.append("dry-enter")
        yield
        events.append("dry-exit")

    monkeypatch.setattr("pygit.fetch_cli_dry_run.refetch_transport", refetch_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.dry_run_repository", dry_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: object())
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: events.append(tuple(argv)) or 0,
    )

    assert run_fetch(["--dry-run", "--refetch", "origin"]) == 0
    assert events[0:2] == ["refetch-enter", "dry-enter"]
    assert "--refetch" not in events[2]
    assert "--no-write-fetch-head" in events[2]
    assert events[-2:] == ["dry-exit", "refetch-exit"]
