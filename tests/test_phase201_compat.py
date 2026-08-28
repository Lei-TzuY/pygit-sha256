from __future__ import annotations

from pygit.fetch_cli_dry_run import run_fetch
from pygit.fetch_negotiation import negotiation_transport
from pygit.fetch_protocol_v2 import negotiate_only, protocol_v2_transport
from pygit.protocol_v2 import SmartHttpV2QueryClient
from pygit.protocol_v2_fetch import SmartHttpV2FetchClient
from pygit.remote import Advertisement, FetchResult, SmartHttpClient
from pygit.repo import Repository


def test_negotiate_only_uses_standard_default_fetch_remote(monkeypatch):
    repo = object()
    seen = {}

    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: repo)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._default_fetch_remote",
        lambda repo_arg: "backup" if repo_arg is repo else "wrong",
    )

    def fake_negotiate(repo_arg, *, source, restrict, include=()):
        seen.update(
            repo=repo_arg,
            source=source,
            restrict=list(restrict),
            include=list(include),
        )
        return []

    monkeypatch.setattr("pygit.fetch_cli_dry_run.negotiate_only", fake_negotiate)

    assert run_fetch(["--negotiate-only", "--negotiation-tip=main"]) == 0
    assert seen == {
        "repo": repo,
        "source": "backup",
        "restrict": ["main"],
        "include": [],
    }


def test_negotiate_only_uses_remote_negotiation_include_fallback(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    restricted_native = "a" * 40
    included_native = "b" * 40
    local_sha = "c" * 64
    seen = {}

    monkeypatch.setattr(
        "pygit.fetch_protocol_v2._negotiation_have_map",
        lambda repo_arg, expressions: {restricted_native: local_sha},
    )

    def configured(repo_arg, remote):
        assert repo_arg is repo
        assert remote == "origin"
        return ["refs/heads/release"]

    monkeypatch.setattr(
        "pygit.fetch_protocol_v2.configured_negotiation_includes",
        configured,
    )

    def included(repo_arg, expressions):
        assert repo_arg is repo
        assert list(expressions) == ["refs/heads/release"]
        return {included_native}

    monkeypatch.setattr("pygit.fetch_protocol_v2.plan_included_haves", included)
    monkeypatch.setattr(
        SmartHttpV2FetchClient,
        "discover_refs",
        lambda self: Advertisement({"refs/heads/main": "d" * 40}, set(), {}),
    )

    def fake_negotiate(self, *, haves, advertisement=None):
        seen["haves"] = set(haves)
        return (restricted_native, included_native)

    monkeypatch.setattr(SmartHttpV2FetchClient, "negotiate", fake_negotiate)

    assert negotiate_only(repo, source="origin", restrict=["main"]) == [local_sha]
    assert seen["haves"] == {restricted_native, included_native}


def test_negotiate_only_explicit_include_overrides_remote_config(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    restricted_native = "a" * 40
    included_native = "e" * 40
    local_sha = "f" * 64
    seen = {}

    monkeypatch.setattr(
        "pygit.fetch_protocol_v2._negotiation_have_map",
        lambda repo_arg, expressions: {restricted_native: local_sha},
    )
    monkeypatch.setattr(
        "pygit.fetch_protocol_v2.configured_negotiation_includes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("explicit include must suppress remote config fallback")
        ),
    )

    def included(repo_arg, expressions):
        assert list(expressions) == ["topic"]
        return {included_native}

    monkeypatch.setattr("pygit.fetch_protocol_v2.plan_included_haves", included)
    monkeypatch.setattr(
        SmartHttpV2FetchClient,
        "discover_refs",
        lambda self: Advertisement({"refs/heads/main": "d" * 40}, set(), {}),
    )

    def fake_negotiate(self, *, haves, advertisement=None):
        seen["haves"] = set(haves)
        return (restricted_native,)

    monkeypatch.setattr(SmartHttpV2FetchClient, "negotiate", fake_negotiate)

    assert negotiate_only(
        repo,
        source="origin",
        restrict=["main"],
        include=["topic"],
    ) == [local_sha]
    assert seen["haves"] == {restricted_native, included_native}


def test_protocol_v2_scope_composes_with_negotiation_have_planner(monkeypatch):
    restricted = "b" * 40
    included = "c" * 40
    baseline = "d" * 40
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    expected = FetchResult(advertisement, {})
    seen = []

    monkeypatch.setattr(
        "pygit.fetch_negotiation.plan_restricted_haves",
        lambda repo, expressions: {restricted},
    )
    monkeypatch.setattr(
        "pygit.fetch_negotiation.plan_included_haves",
        lambda repo, expressions: {included},
    )

    def fake_v2_fetch(self, haves=None, advertisement=None):
        seen.append(set(haves or []))
        return expected

    monkeypatch.setattr(SmartHttpV2FetchClient, "fetch", fake_v2_fetch)

    with protocol_v2_transport():
        with negotiation_transport(
            object(),
            restrict=["main"],
            include=["topic"],
        ):
            actual = SmartHttpClient("https://example.test/repo.git").fetch(
                haves={baseline},
                advertisement=advertisement,
            )

    assert actual is expected
    assert seen == [{restricted, included}]


def test_protocol_v2_discovery_fallback_is_sticky_for_fetch(monkeypatch):
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    expected = FetchResult(advertisement, {})
    calls = []

    def v0_discover(self):
        calls.append("v0-discover")
        return advertisement

    def v0_fetch(self, haves=None, advertisement=None):
        calls.append(("v0-fetch", set(haves or [])))
        return expected

    def v2_discover(self):
        calls.append("v2-discover")
        return None

    def unexpected_v2_fetch(self, haves=None, advertisement=None):
        raise AssertionError("sticky v0 fallback must bypass a second v2 attempt")

    monkeypatch.setattr(SmartHttpClient, "discover", v0_discover)
    monkeypatch.setattr(SmartHttpClient, "fetch", v0_fetch)
    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_refs", v2_discover)
    monkeypatch.setattr(SmartHttpV2FetchClient, "fetch", unexpected_v2_fetch)

    with protocol_v2_transport():
        client = SmartHttpClient("https://example.test/repo.git")
        assert client.discover() is advertisement
        assert client.fetch(haves={"b" * 40}, advertisement=advertisement) is expected

    assert calls == ["v2-discover", "v0-discover", ("v0-fetch", {"b" * 40})]
