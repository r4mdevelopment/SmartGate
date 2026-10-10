from dataclasses import replace

import httpx
import pytest

from smartgate.errors import SafetyBlocked
from smartgate.models import ProbeCursor
from smartgate.reconcile.common import current
from smartgate.state.git_branch import GitHubClient, GitResponse
from smartgate.state.journal import initial_state
from tests.contract.test_encrypted_git_state import GitBackend, store


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr("smartgate.state.git_branch.time.sleep", lambda delay: None)


def saved_state():
    backend = GitBackend()
    s = store(backend)
    parent = s.bootstrap(initial_state("abcdef"))
    original = s.load("abcdef")
    changed = replace(original, probe_cursors=(ProbeCursor("chatgpt.com", 1),))
    revision = s.save(changed, parent)
    return backend, s, parent, replace(changed, revision=revision)


def ref_responses(backend, responses):
    original = backend.request
    heads = []

    def request(method, path, payload=None):
        result = original(method, path, payload)
        if "/git/ref/heads/" in path:
            heads.append(path)
            answer = responses(len(heads))
            return (
                GitResponse(404, {})
                if answer is None
                else GitResponse(200, {"object": {"sha": answer}})
            )
        return result

    backend.request = request
    return heads


def test_delayed_parent_read_after_acknowledged_save_reaches_exact_new_state():
    backend, s, parent, expected = saved_state()
    heads = ref_responses(backend, lambda n: parent if n < 3 else expected.revision)
    before = len(backend.calls)
    assert current(s, expected) == expected
    assert len(heads) == 3
    assert all(method == "GET" for method, _, _ in backend.calls[before:])


def test_parent_that_never_advances_blocks_instead_of_trusting_cached_state():
    backend, s, parent, expected = saved_state()
    heads = ref_responses(backend, lambda n: parent)
    before = len(backend.calls)
    with pytest.raises(SafetyBlocked):
        current(s, expected)
    assert len(heads) == 4
    assert all(method == "GET" for method, _, _ in backend.calls[before:])


@pytest.mark.parametrize("foreign", ["f" * 40, None, "invalid-sha"])
def test_other_revision_or_missing_ref_is_not_retried_or_hidden(foreign):
    backend, s, parent, expected = saved_state()
    heads = ref_responses(backend, lambda n: parent if n == 1 else foreign)
    with pytest.raises(SafetyBlocked):
        current(s, expected)
    assert len(heads) == 2


def test_fresh_store_has_no_permission_to_retry_an_old_ref():
    backend, _, parent, _ = saved_state()
    s = store(backend)
    heads = ref_responses(backend, lambda n: parent)
    assert s.load("abcdef").revision == parent
    assert len(heads) == 1


def test_total_readback_retry_budget_is_bounded_across_reads():
    backend, s, parent, expected = saved_state()
    heads = ref_responses(backend, lambda n: parent if n % 2 else expected.revision)
    for _ in range(12):
        assert current(s, expected) == expected
    with pytest.raises(SafetyBlocked):
        current(s, expected)
    assert len(heads) == 25


def test_write_preflight_reserves_twelve_extra_readback_requests():
    backend = GitBackend()
    s = store(backend)
    backend.remaining_calls = 398
    s.check_write_budget(27)
    backend.remaining_calls = 397
    with pytest.raises(SafetyBlocked):
        s.check_write_budget(27)


def test_ref_gets_bypass_cache_with_unique_queries():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"object": {"sha": "a" * 40}})

    with_client = GitHubClient("test-token", transport=httpx.MockTransport(handle))
    try:
        for _ in range(2):
            with_client.request("GET", "/repos/owner/repo/git/ref/heads/smartgate-state")
        assert all(r.headers.get("cache-control") == "no-cache" for r in seen)
        assert seen[0].url.query and seen[0].url.query != seen[1].url.query
    finally:
        with_client.close()


@pytest.mark.parametrize("status", [401, 403, 500])
def test_api_errors_after_parent_delay_stop_without_another_retry(status):
    backend, s, parent, expected = saved_state()
    original = backend.request
    heads = []

    def request(method, path, payload=None):
        result = original(method, path, payload)
        if "/git/ref/heads/" in path:
            heads.append(path)
            return (
                GitResponse(200, {"object": {"sha": parent}})
                if len(heads) == 1
                else GitResponse(status, {})
            )
        return result

    backend.request = request
    with pytest.raises(SafetyBlocked):
        current(s, expected)
    assert len(heads) == 2


def test_unacknowledged_save_does_not_grant_parent_retry_permission():
    backend = GitBackend()
    s = store(backend)
    parent = s.bootstrap(initial_state("abcdef"))
    state = s.load("abcdef")
    backend.lose_ack = True
    with pytest.raises(SafetyBlocked):
        s.save(replace(state, probe_cursors=(ProbeCursor("chatgpt.com", 1),)), parent)
    heads = ref_responses(backend, lambda n: parent)
    assert s.load("abcdef").revision == parent
    assert len(heads) == 1
    assert (
        len(
            [
                method
                for method, path, _ in backend.calls
                if method == "PATCH" and "/git/refs/heads/" in path
            ]
        )
        == 1
    )
