"""Real Git adapter checkpoints under an installation-token HTTP simulation."""

import hashlib
import json
from dataclasses import replace

import httpx
import pytest

from smartgate.app import AppDependencies, run
from smartgate.models import ProbeCursor, RelaySnapshot
from smartgate.state.encrypted import PlainStateCodec
from smartgate.state.git_branch import GitBranchStateStore, GitHubClient
from tests.integration.test_app import Checker, setup
from tests.reconcile_fakes import NOW, FakeDns, target, verify_ok


class GitBackend:
    def __init__(self):
        self.head = None
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}

        def object_sha(value):
            return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()

        if path == "/user":
            return httpx.Response(403)
        if path == "/repos/owner/repo":
            return httpx.Response(
                200, json={"owner": {"login": "owner", "id": 123, "type": "User"}}
            )
        if "/git/ref/heads/" in path:
            return (
                httpx.Response(200, json={"object": {"sha": self.head}})
                if self.head
                else httpx.Response(404)
            )
        if "/contents/" in path:
            revision = request.url.params["ref"]
            data = self.blobs[self.trees[self.commits[revision]["tree"]]]
            return httpx.Response(200, json={"encoding": "base64", "content": data})
        if path.endswith("/git/blobs"):
            sha = object_sha(body)
            self.blobs[sha] = body["content"]
            return httpx.Response(201, json={"sha": sha})
        if path.endswith("/git/trees"):
            sha = object_sha(body)
            self.trees[sha] = body["tree"][0]["sha"]
            return httpx.Response(201, json={"sha": sha})
        if "/git/commits/" in path:
            return httpx.Response(
                200, json={"tree": {"sha": self.commits[path.rsplit("/", 1)[1]]["tree"]}}
            )
        if path.endswith("/git/commits"):
            sha = object_sha(body)
            self.commits[sha] = body
            return httpx.Response(201, json={"sha": sha})
        if path.endswith("/git/refs"):
            assert self.head is None
            self.head = body["sha"]
            return httpx.Response(201, json={"object": {"sha": self.head}})
        if "/git/refs/heads/" in path:
            assert body["force"] is False
            if self.commits[body["sha"]]["parents"] != [self.head]:
                return httpx.Response(422)
            self.head = body["sha"]
            return httpx.Response(200, json={"object": {"sha": self.head}})
        raise AssertionError(path)


def scenario(count):
    config, _ = setup()
    service = replace(
        config.catalog.services[0],
        include_suffixes=("example.com",),
        required_domains=("d0.example.com",),
    )
    config = replace(
        config,
        catalog=replace(config.catalog, services=(service,)),
        sync=replace(config.sync, max_mutations=100),
    )

    class Relay:
        def fetch(self, provider, now):
            candidates = tuple(
                target(f"d{i}.example.com", "8.8.8.8", provider.id).candidate for i in range(count)
            )
            return RelaySnapshot(provider.id, now, "digest", candidates, ())

    backend = GitBackend()
    client = GitHubClient("test-installation-token", transport=httpx.MockTransport(backend.handle))
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "test-installation-token", client, codec=PlainStateCodec()
    )
    deps = AppDependencies(
        {p.id: Relay() for p in config.providers},
        Checker(),
        FakeDns(),
        store,
        verify_ok,
        lambda: NOW,
        "abcdef",
        bootstrap=True,
        subtree_capabilities=True,
        bootstrap_state=store.bootstrap,
    )
    return config, deps, backend, client


@pytest.mark.parametrize("count", [20, 27])
def test_installation_token_bootstrap_finishes_within_request_cap(count):
    config, deps, backend, client = scenario(count)
    try:
        deps = replace(deps, preflight_writes=deps.store.check_write_budget)
        result = run(config, "sync", True, deps)
        assert result.status == "applied"
        assert deps.dns.writes == count
        assert len(backend.requests) < 400
        # Reload with a new client/store: no reliance on the first runner cache.
        client2 = GitHubClient(
            "test-installation-token", transport=httpx.MockTransport(backend.handle)
        )
        try:
            fresh = GitBranchStateStore(
                "owner/repo",
                "smartgate-state",
                "test-installation-token",
                client2,
                codec=PlainStateCodec(),
            )
            state = fresh.load("abcdef")
            assert state.journal.phase == "verified" and len(state.managed_records) == count
            assert state.last_sources
        finally:
            client2.close()
    finally:
        client.close()


def test_oversized_git_transaction_is_blocked_before_bootstrap_or_dns():
    config, deps, backend, client = scenario(50)
    try:
        deps = replace(deps, preflight_writes=deps.store.check_write_budget)
        result = run(config, "sync", True, deps)
        assert result.status == "blocked"
        assert deps.dns.writes == 0 and backend.head is None
    finally:
        client.close()


def test_cached_state_does_not_hide_another_writer_or_allow_stale_save():
    from smartgate.errors import StateConflict
    from smartgate.state.journal import initial_state

    _, deps, backend, client = scenario(1)
    client2 = GitHubClient("test-token", transport=httpx.MockTransport(backend.handle))
    try:
        deps.store.bootstrap(initial_state("abcdef"))
        original = deps.store.load("abcdef")
        writer = GitBranchStateStore(
            "owner/repo", "smartgate-state", "test-token", client2, codec=PlainStateCodec()
        )
        external = writer.load("abcdef")
        new_revision = writer.save(
            replace(external, probe_cursors=(ProbeCursor("chatgpt.com", 1),)), external.revision
        )
        with pytest.raises(StateConflict):
            deps.store.save(original, original.revision)
        reloaded = deps.store.load("abcdef")
        assert reloaded.revision == new_revision
        assert reloaded.probe_cursors == (ProbeCursor("chatgpt.com", 1),)
    finally:
        client2.close()
        client.close()


def test_delayed_confirmed_checkpoint_reads_do_not_interrupt_serial_apply(monkeypatch):
    monkeypatch.setattr("smartgate.state.git_branch.time.sleep", lambda delay: None)
    config, deps, backend, client = scenario(1)
    original = backend.handle
    pending_parent = None
    stale_reads = 0

    def handle(request):
        nonlocal pending_parent, stale_reads
        parent = backend.head
        response = original(request)
        if request.method == "PATCH" and "/git/refs/heads/" in request.url.path:
            pending_parent = parent
        elif "/git/ref/heads/" in request.url.path and pending_parent is not None:
            old = pending_parent
            pending_parent = None
            stale_reads += 1
            return httpx.Response(200, json={"object": {"sha": old}})
        return response

    client._client._transport.handler = handle
    try:
        deps = replace(deps, preflight_writes=deps.store.check_write_budget)
        result = run(config, "sync", True, deps)
        assert result.status == "applied" and result.journal_phase == "verified"
        assert deps.dns.writes == 1 and stale_reads > 0
        assert len(backend.requests) < 400
    finally:
        client.close()
