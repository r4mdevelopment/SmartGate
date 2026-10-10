import base64
import json
from dataclasses import replace

import httpx
import pytest

from smartgate.errors import SafetyBlocked, StateConflict
from smartgate.state.codec import encode_state
from smartgate.state.encrypted import PlainStateCodec
from smartgate.state.git_branch import GitBranchStateStore, GitHubClient
from smartgate.state.journal import initial_state

PARENT = "a" * 40
NEW = "b" * 40


def backend(exists=True, conflict=False, forbidden=False):
    requests = []

    def handle(request):
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer temporary-test-token"
        requests.append(request)
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "owner", "id": 123})
        if "/git/ref/" in path:
            return (
                httpx.Response(200, json={"object": {"sha": PARENT}})
                if exists
                else httpx.Response(404)
            )
        if "/contents/" in path:
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(encode_state(initial_state("abcdef"))).decode(),
                },
            )
        if path.endswith("/git/commits/" + PARENT):
            return httpx.Response(200, json={"tree": {"sha": "c" * 40}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "d" * 40})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "e" * 40})
        if path.endswith("/git/commits"):
            body = json.loads(request.content)
            assert body["parents"] == [PARENT]
            assert (
                body["author"]
                == body["committer"]
                == {"name": "owner", "email": "123+owner@users.noreply.github.com"}
            )
            return httpx.Response(201, json={"sha": NEW})
        if "/git/refs/" in path:
            assert json.loads(request.content) == {"sha": NEW, "force": False}
            return httpx.Response(
                403 if forbidden else 422 if conflict else 200, json={"object": {"sha": NEW}}
            )
        raise AssertionError(path)

    return GitHubClient("temporary-test-token", transport=httpx.MockTransport(handle)), requests


def test_load_and_nonforce_cas_save():
    client, requests = backend()
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    state = store.load("abcdef")
    assert state.revision == PARENT
    assert store.save(state, PARENT) == NEW
    assert requests[-1].method == "PATCH"
    assert all("temporary-test-token" not in r.content.decode() for r in requests)
    assert any("ref=" + PARENT in str(r.url) for r in requests)


def test_absent_branch_preview_never_creates():
    client, requests = backend(exists=False)
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    assert store.load("abcdef") is None
    assert all(r.method == "GET" for r in requests)
    with pytest.raises(SafetyBlocked):
        store.save(initial_state("abcdef"), None)


@pytest.mark.parametrize(
    "conflict,forbidden,error", [(True, False, StateConflict), (False, True, SafetyBlocked)]
)
def test_remote_save_rejection_is_not_success(conflict, forbidden, error):
    client, requests = backend(conflict=conflict, forbidden=forbidden)
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    with pytest.raises(error):
        store.save(replace(initial_state("abcdef"), revision=PARENT), PARENT)


def test_changed_parent_blocks_before_git_writes():
    client, requests = backend()
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    with pytest.raises(StateConflict):
        store.save(replace(initial_state("abcdef"), revision=NEW), NEW)
    assert all(r.method == "GET" for r in requests)


def test_missing_file_existing_branch_initializes_without_replacing_other_files():
    requests = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if "/git/ref/" in path:
            return httpx.Response(200, json={"object": {"sha": PARENT}})
        if "/contents/" in path:
            return httpx.Response(404)
        if path == "/user":
            return httpx.Response(200, json={"login": "owner", "id": 123})
        if path.endswith("/git/commits/" + PARENT):
            return httpx.Response(200, json={"tree": {"sha": "c" * 40}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "d" * 40})
        if path.endswith("/git/trees"):
            assert json.loads(request.content)["base_tree"] == "c" * 40
            return httpx.Response(201, json={"sha": "e" * 40})
        if path.endswith("/git/commits"):
            assert json.loads(request.content)["parents"] == [PARENT]
            return httpx.Response(201, json={"sha": NEW})
        if "/git/refs/" in path:
            return httpx.Response(200, json={"object": {"sha": NEW}})
        raise AssertionError(path)

    client = GitHubClient("test-token", transport=httpx.MockTransport(handle))
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "test-token", client, codec=PlainStateCodec()
    )
    assert store.save(initial_state("abcdef"), None) == NEW


def test_explicit_bootstrap_creates_orphan_without_source_tree():
    requests = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if "/git/ref/" in path:
            return httpx.Response(404)
        if path == "/user":
            return httpx.Response(200, json={"login": "owner", "id": 123})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "d" * 40})
        if path.endswith("/git/trees"):
            assert "base_tree" not in json.loads(request.content)
            return httpx.Response(201, json={"sha": "e" * 40})
        if path.endswith("/git/commits"):
            assert json.loads(request.content)["parents"] == []
            return httpx.Response(201, json={"sha": NEW})
        if path.endswith("/git/refs"):
            assert json.loads(request.content) == {"ref": "refs/heads/smartgate-state", "sha": NEW}
            return httpx.Response(201, json={"object": {"sha": NEW}})
        raise AssertionError(path)

    client = GitHubClient("test-token", transport=httpx.MockTransport(handle))
    assert (
        GitBranchStateStore(
            "owner/repo", "smartgate-state", "test-token", client, codec=PlainStateCodec()
        ).bootstrap(initial_state("abcdef"))
        == NEW
    )


def test_race_before_ref_update_blocks_without_patching():
    original, requests = backend()
    original_request = original.request
    calls = 0

    def changed(method, path, payload=None):
        nonlocal calls
        if "/git/ref/" in path:
            calls += 1
            if calls == 2:
                from smartgate.state.git_branch import GitResponse

                return GitResponse(200, {"object": {"sha": "f" * 40}})
        return original_request(method, path, payload)

    original.request = changed
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", original, codec=PlainStateCodec()
    )
    with pytest.raises(StateConflict):
        store.save(replace(initial_state("abcdef"), revision=PARENT), PARENT)
    assert all(r.method != "PATCH" for r in requests)


@pytest.mark.parametrize("path", ["https://evil.example", "//evil.example/path"])
def test_token_cannot_leave_github(path):
    client = GitHubClient(
        "test-token", transport=httpx.MockTransport(lambda r: pytest.fail("must not send"))
    )
    with pytest.raises(SafetyBlocked):
        client.request("GET", path)


def test_uncertain_ref_update_is_not_retried_or_reported_saved():
    client, requests = backend()
    original = client._client._transport.handler

    def handle(request):
        if "/git/refs/" in request.url.path:
            requests.append(request)
            raise httpx.ReadTimeout("lost response")
        return original(request)

    client._client._transport = httpx.MockTransport(handle)
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    with pytest.raises(SafetyBlocked):
        store.save(replace(initial_state("abcdef"), revision=PARENT), PARENT)
    assert sum(r.method == "PATCH" for r in requests) == 1


def test_actions_installation_token_uses_repository_owner_identity():
    client, requests = backend()
    original = client.request

    def installation(method, path, payload=None):
        from smartgate.state.git_branch import GitResponse

        if path == "/user":
            return GitResponse(403, {})
        if path == "/repos/owner/repo":
            return GitResponse(200, {"owner": {"login": "owner", "id": 123, "type": "User"}})
        return original(method, path, payload)

    client.request = installation
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    assert store.save(replace(initial_state("abcdef"), revision=PARENT), PARENT) == NEW


def test_concurrent_identical_saves_produce_distinct_commit_intents():
    client, requests = backend()
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    state = replace(initial_state("abcdef"), revision=PARENT)
    store.save(state, PARENT)
    # Independent contenders start from the same parent; one store cannot ignore its own ack.
    contender = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    contender.save(state, PARENT)
    messages = [
        json.loads(r.content)["message"]
        for r in requests
        if r.method == "POST" and r.url.path.endswith("/git/commits")
    ]
    assert len(set(messages)) == 2


@pytest.mark.parametrize("case", ["success", "conflict", "lost_response"])
def test_git_upgrade_moves_paths_in_one_commit_and_checks_readback(case):
    from smartgate.dns.types import DnsTarget
    from smartgate.state.upgrade import upgrade_legacy_state

    head = [PARENT]
    original = encode_state(initial_state("abcdef"))
    files = {"state/abcdef.json": original}
    blob = [None]
    tree = [None]
    patches = []

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if "/git/ref/" in path:
            return httpx.Response(200, json={"object": {"sha": head[0]}})
        if "/contents/" in path:
            key = path.split("/contents/", 1)[1]
            if key not in files:
                return httpx.Response(404)
            return httpx.Response(
                200, json={"encoding": "base64", "content": base64.b64encode(files[key]).decode()}
            )
        if path == "/user":
            return httpx.Response(200, json={"login": "owner", "id": 123})
        if path.endswith("/git/commits/" + PARENT):
            return httpx.Response(200, json={"tree": {"sha": "c" * 40}})
        if path.endswith("/git/blobs"):
            blob[0] = base64.b64decode(body["content"])
            return httpx.Response(201, json={"sha": "d" * 40})
        if path.endswith("/git/trees"):
            tree[0] = body["tree"]
            return httpx.Response(201, json={"sha": "e" * 40})
        if path.endswith("/git/commits"):
            assert body["parents"] == [PARENT]
            return httpx.Response(201, json={"sha": NEW})
        if "/git/refs/" in path:
            patches.append(request)
            if case == "conflict":
                return httpx.Response(422)
            assert body == {"sha": NEW, "force": False}
            assert {entry["path"] for entry in tree[0]} == {
                "state/abcdef.json",
                "state/primary.json",
            }
            for entry in tree[0]:
                if entry["sha"] is None:
                    files.pop(entry["path"])
                else:
                    files[entry["path"]] = blob[0]
            head[0] = NEW
            if case == "lost_response":
                raise httpx.ReadError("response lost")
            return httpx.Response(200, json={"object": {"sha": NEW}})
        raise AssertionError(path)

    client = GitHubClient("temporary-test-token", transport=httpx.MockTransport(handle))
    store = GitBranchStateStore(
        "owner/repo", "smartgate-state", "temporary-test-token", client, codec=PlainStateCodec()
    )
    old = store.load("abcdef")
    new = upgrade_legacy_state(
        old,
        DnsTarget(
            "primary",
            "nextdns",
            "https://api.nextdns.io",
            "abcdef",
            "https://dns.nextdns.io/abcdef",
        ),
    )
    if case == "conflict":
        with pytest.raises(StateConflict):
            store.upgrade(old, new)
        assert files == {"state/abcdef.json": original}
    else:
        assert store.upgrade(old, new) == NEW
        assert store.load("abcdef") is None
        assert store.load("primary").target_fingerprint == new.target_fingerprint
    assert len(patches) == 1
