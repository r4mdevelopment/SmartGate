import json
import os
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from smartgate.errors import SafetyBlocked, StateConflict
from smartgate.models import ManagedRecord, ProbeCursor
from smartgate.state.journal import initial_state
from smartgate.state.local import LocalStateStore


def test_roundtrip_and_compare_and_swap(tmp_path):
    store = LocalStateStore(tmp_path / "state.json")
    assert store.load("abcdef") is None
    state = replace(
        initial_state("abcdef"),
        managed_records=(
            ManagedRecord("id", "chatgpt.com", "8.8.8.8", "relay", datetime.now(UTC)),
        ),
        probe_cursors=(ProbeCursor("chatgpt.com", 2),),
    )
    revision = store.save(state, None)
    loaded = store.load("abcdef")
    assert loaded == replace(state, revision=revision)
    assert store.save(loaded, revision) == revision
    with pytest.raises(StateConflict):
        store.save(state, None)


@pytest.mark.parametrize(
    "payload", ["{", '{"schema_version":2}', '{"schema_version":true}', '{"token":"secret"}']
)
def test_malformed_state_blocks(tmp_path, payload):
    path = tmp_path / "state.json"
    path.write_text(payload)
    with pytest.raises(SafetyBlocked):
        LocalStateStore(path).load("abcdef")


def test_profile_mismatch_and_lock(tmp_path):
    store = LocalStateStore(tmp_path / "state.json")
    store.save(initial_state("abcdef"), None)
    with pytest.raises(SafetyBlocked):
        store.load("123456")
    with store.lock():
        with pytest.raises(StateConflict):
            store.save(initial_state("abcdef"), None)
        with pytest.raises(StateConflict):
            store.load("abcdef")


def test_failed_atomic_replace_keeps_old_state(tmp_path, monkeypatch):
    store = LocalStateStore(tmp_path / "state.json")
    revision = store.save(initial_state("abcdef"), None)
    old = store.load("abcdef")

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(SafetyBlocked):
        store.save(replace(old, probe_cursors=(ProbeCursor("chatgpt.com", 1),)), revision)
    assert store.load("abcdef") == old
    assert list(tmp_path.glob("*.tmp")) == []


def test_extra_field_and_duplicate_json_key_block(tmp_path):
    store = LocalStateStore(tmp_path / "state.json")
    store.save(initial_state("abcdef"), None)
    path = tmp_path / "state.json"
    data = json.loads(path.read_text())
    data["api_key"] = "secret"
    path.write_text(json.dumps(data))
    with pytest.raises(SafetyBlocked):
        store.load("abcdef")
    path.write_text('{"schema_version":1,"schema_version":2}')
    with pytest.raises(SafetyBlocked):
        store.load("abcdef")


@pytest.mark.parametrize(
    "change", ["wrong_version", "negative_cursor", "duplicate_id", "naive_time", "oversize"]
)
def test_full_state_corruption_is_rejected(tmp_path, change):
    from smartgate.state.codec import MAX_STATE_BYTES

    store = LocalStateStore(tmp_path / "state.json")
    state = replace(
        initial_state("abcdef"),
        managed_records=(
            ManagedRecord("id", "chatgpt.com", "8.8.8.8", "relay", datetime.now(UTC)),
        ),
        probe_cursors=(ProbeCursor("chatgpt.com", 2),),
    )
    store.save(state, None)
    data = json.loads(store.path.read_text())
    if change == "wrong_version":
        data["schema_version"] = 2
    elif change == "negative_cursor":
        data["probe_cursors"][0]["index"] = -1
    elif change == "duplicate_id":
        duplicate = dict(data["managed_records"][0], domain="openai.com")
        data["managed_records"].append(duplicate)
    elif change == "naive_time":
        data["managed_records"][0]["last_verified_at"] = "2026-10-07T00:00:00"
    elif change == "oversize":
        store.path.write_bytes(b" " * (MAX_STATE_BYTES + 1))
        with pytest.raises(SafetyBlocked):
            store.load("abcdef")
        return
    store.path.write_text(json.dumps(data))
    with pytest.raises(SafetyBlocked):
        store.load("abcdef")


def test_synthetic_recovery_fixture_roundtrip():
    from pathlib import Path

    from smartgate.state.codec import decode_state, encode_state

    fixture = Path("tests/fixtures/state/recovery.json").read_bytes()
    state = decode_state(fixture, "abcdef", "revision")
    assert state.journal.phase == "partial"
    assert state.journal.operation_statuses == ("unknown",)
    assert state.last_sources[0].candidates[0] == state.selections[0].candidate
    assert decode_state(encode_state(state), "abcdef", "revision") == state
