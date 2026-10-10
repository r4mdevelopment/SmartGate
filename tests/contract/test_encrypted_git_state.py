import base64
import json
from dataclasses import replace

import httpx
import pytest

from smartgate.errors import SafetyBlocked, StateConflict
from smartgate.security.envelope import EnvelopeBinding, EnvelopeCipher
from smartgate.state.codec import MAX_STATE_BYTES, encode_state
from smartgate.state.git_branch import GitBranchStateStore, GitHubClient, GitResponse
from smartgate.state.journal import initial_state

KEY = bytes(range(32))


class GitBackend:
    def __init__(self, data=None):
        self.head = "a" * 40 if data is not None else None
        self.files = {} if data is None else {"state/abcdef.json": data}
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.calls = []
        self.remaining_calls = 400
        self.counter = 10
        self.conflict = False
        self.lose_ack = False

    def sha(self):
        self.counter += 1
        return f"{self.counter:040x}"

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        self.remaining_calls -= 1
        path = path.removeprefix("/repos/owner/repo")
        if path == "/user":
            return GitResponse(200, {"login": "owner", "id": 123})
        if path.startswith("/git/ref/heads/"):
            return (
                GitResponse(404, {})
                if self.head is None
                else GitResponse(200, {"object": {"sha": self.head}})
            )
        if path.startswith("/contents/"):
            key = path.split("?")[0].removeprefix("/contents/")
            revision = path.split("ref=")[-1]
            files = self.commits[revision][1] if revision in self.commits else self.files
            data = files.get(key)
            return (
                GitResponse(404, {})
                if data is None
                else GitResponse(
                    200, {"encoding": "base64", "content": base64.b64encode(data).decode()}
                )
            )
        if method == "GET" and path.startswith("/git/commits/"):
            sha = self.sha()
            self.trees[sha] = dict(self.files)
            return GitResponse(200, {"tree": {"sha": sha}})
        if path == "/git/blobs":
            sha = self.sha()
            self.blobs[sha] = base64.b64decode(payload["content"])
            return GitResponse(201, {"sha": sha})
        if path == "/git/trees":
            files = dict(self.trees.get(payload.get("base_tree"), {}))
            for item in payload["tree"]:
                if item["sha"] is None:
                    files.pop(item["path"], None)
                else:
                    files[item["path"]] = self.blobs[item["sha"]]
            sha = self.sha()
            self.trees[sha] = files
            return GitResponse(201, {"sha": sha})
        if path == "/git/commits":
            sha = self.sha()
            self.commits[sha] = (payload, self.trees[payload["tree"]])
            return GitResponse(201, {"sha": sha})
        if path == "/git/refs" or path.startswith("/git/refs/heads/"):
            if self.conflict:
                return GitResponse(422, {})
            assert payload.get("force", False) is False
            self.head = payload["sha"]
            self.files = dict(self.commits[self.head][1])
            if self.lose_ack:
                self.lose_ack = False
                raise SafetyBlocked("GitHub state request failed; read state before retrying")
            return GitResponse(201, {"object": {"sha": self.head}})
        raise AssertionError((method, path))


def codec():
    from smartgate.state.encrypted import EncryptedStateCodec

    return EncryptedStateCodec(EnvelopeCipher(KEY), "owner/repo", "smartgate-state")


def store(backend):
    return GitBranchStateStore(
        "owner/repo", "smartgate-state", "fixture-token", backend, codec=codec()
    )


def test_encrypted_bootstrap_save_fresh_load_and_other_file_preserved():
    backend = GitBackend()
    s = store(backend)
    state = initial_state("abcdef")
    revision = s.bootstrap(state)
    assert b"profile_id" not in backend.files["state/abcdef.json"]
    assert store(backend).load("abcdef") == replace(state, revision=revision)
    backend.files["other.txt"] = b"preserve"
    s._tree_cache = None
    loaded = s.load("abcdef")
    revision = s.save(loaded, loaded.revision)
    assert store(backend).load("abcdef") == replace(state, revision=revision)
    assert backend.files["other.txt"] == b"preserve"


def test_plaintext_and_corruption_never_become_absent_state():
    backend = GitBackend(encode_state(initial_state("abcdef")))
    with pytest.raises(SafetyBlocked):
        store(backend).load("abcdef")
    backend.files["state/abcdef.json"] = b"bad ciphertext"
    with pytest.raises(SafetyBlocked):
        store(backend).load("abcdef")
    assert all(method == "GET" for method, _, _ in backend.calls)


def test_encrypted_cas_conflict_does_not_update_head():
    backend = GitBackend()
    s = store(backend)
    rev = s.bootstrap(initial_state("abcdef"))
    state = s.load("abcdef")
    backend.conflict = True
    with pytest.raises(StateConflict):
        s.save(state, rev)
    assert backend.head == rev


def test_maximum_ciphertext_fits_full_bounded_api_response():
    payload = b"x" * MAX_STATE_BYTES
    envelope = EnvelopeCipher(KEY).seal(
        payload,
        EnvelopeBinding("state", "owner/repo", "smartgate-state", "abcdef"),
        max_plaintext=MAX_STATE_BYTES,
    )
    body = {
        "encoding": "base64",
        "content": base64.b64encode(envelope).decode(),
        "metadata": "m" * 10000,
    }
    assert len(json.dumps(body).encode()) < 2 * 1024 * 1024
    client = GitHubClient(
        "test-token", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    try:
        assert (
            client.request("GET", "/repos/owner/repo/contents/state/abcdef.json").data["content"]
            == body["content"]
        )
    finally:
        client.close()


def test_encryption_key_missing_stops_runtime_before_dns_clients(monkeypatch):
    from contextlib import ExitStack
    from pathlib import Path

    import smartgate.runtime as runtime
    from smartgate.config import load_config

    config = load_config(Path("config/smartgate.actions.toml"), {})
    monkeypatch.setattr(
        runtime,
        "create_dns_bundle",
        lambda *a: pytest.fail("key validation must precede DNS client"),
    )
    with ExitStack() as stack, pytest.raises(SafetyBlocked):
        runtime.live_dependencies(
            config,
            {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_TOKEN": "fixture"},
            stack,
            bootstrap=False,
            rollback_run_id=None,
            takeover=False,
        )


def test_large_state_reads_immutable_blob_from_contents_object():
    from smartgate.state.encrypted import EncryptedStateCodec

    envelope = EnvelopeCipher(KEY).seal(
        b"x" * MAX_STATE_BYTES,
        EnvelopeBinding("state", "owner/repo", "smartgate-state", "abcdef"),
        max_plaintext=MAX_STATE_BYTES,
    )
    blob_sha = "b" * 40
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "/contents/" in request.url.path:
            assert request.headers["Accept"] == "application/vnd.github.object+json"
            assert request.url.params["ref"] == "a" * 40
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "none",
                    "content": "",
                    "sha": blob_sha,
                    "size": len(envelope),
                },
            )
        assert request.url.path.endswith("/git/blobs/" + blob_sha)
        return httpx.Response(
            200,
            json={
                "sha": blob_sha,
                "size": len(envelope),
                "encoding": "base64",
                "content": base64.b64encode(envelope).decode(),
            },
        )

    client = GitHubClient("test", transport=httpx.MockTransport(handler))
    try:
        storage = GitBranchStateStore(
            "owner/repo",
            "smartgate-state",
            "test",
            client,
            codec=EncryptedStateCodec(EnvelopeCipher(KEY), "owner/repo", "smartgate-state"),
        )
        assert storage._read_data_at("abcdef", "a" * 40) == envelope
        assert len(calls) == 2
    finally:
        client.close()


@pytest.mark.parametrize(
    "change",
    [
        {"sha": "../outside"},
        {"size": 3 * MAX_STATE_BYTES},
        {"content": "unexpected"},
        {"type": "symlink"},
    ],
)
def test_invalid_large_contents_metadata_stops_before_blob_fetch(change):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "none",
                "content": "",
                "sha": "b" * 40,
                "size": MAX_STATE_BYTES + 1,
                **change,
            },
        )

    client = GitHubClient("test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SafetyBlocked):
            GitBranchStateStore(
                "owner/repo", "smartgate-state", "test", client, codec=codec()
            )._read_data_at("abcdef", "a" * 40)
        assert len(calls) == 1
    finally:
        client.close()
