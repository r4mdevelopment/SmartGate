from dataclasses import replace
from datetime import UTC, datetime

import pytest

from smartgate.errors import SafetyBlocked
from smartgate.models import ChangePlan, DesiredRewrite, DnsRecord, Operation
from smartgate.state.journal import (
    complete_journal,
    initial_state,
    prepare_journal,
    record_operation,
)
from smartgate.state.local import LocalStateStore

NOW = datetime(2026, 10, 7, tzinfo=UTC)


def plan():
    return ChangePlan(
        "plan",
        "config",
        "dns",
        None,
        NOW,
        (Operation("create", "chatgpt.com", None, DesiredRewrite("chatgpt.com", "8.8.8.8")),),
        (),
    )


def test_intent_survives_roundtrip_and_cannot_verify_pending(tmp_path):
    state = prepare_journal(plan(), initial_state("abcdef"), "run")
    store = LocalStateStore(tmp_path / "state.json")
    revision = store.save(state, None)
    loaded = store.load("abcdef")
    assert loaded.journal.operation_statuses == ("pending",)
    assert loaded.journal.plan == plan()
    with pytest.raises(SafetyBlocked):
        complete_journal(loaded, "verified")
    with pytest.raises(SafetyBlocked):
        prepare_journal(replace(plan(), state_revision=revision), loaded, "next")
    result = DnsRecord("new-id", "chatgpt.com", "8.8.8.8")
    applied = record_operation(loaded, 0, "confirmed", result)
    done = complete_journal(applied, "verified")
    assert done.journal.operation_results == (result,)
    assert done.journal.phase == "verified"


def test_before_snapshot_preserved_and_unknown_retained():
    before = DnsRecord("old-id", "chatgpt.com", "1.1.1.1")
    change = replace(
        plan(),
        operations=(
            Operation("replace", before.domain, before, DesiredRewrite(before.domain, "8.8.8.8")),
        ),
    )
    state = prepare_journal(change, initial_state("abcdef"), "run")
    assert state.journal.before_snapshot == (before,)
    partial = record_operation(state, 0, "unknown", None)
    assert complete_journal(partial, "partial").journal.operation_statuses == ("unknown",)
    with pytest.raises(SafetyBlocked):
        complete_journal(partial, "verified")


@pytest.mark.parametrize("index,status", [(-1, "confirmed"), (1, "confirmed"), (0, "nonsense")])
def test_invalid_operation_update_blocks(index, status):
    state = prepare_journal(plan(), initial_state("abcdef"), "run")
    with pytest.raises(SafetyBlocked):
        record_operation(state, index, status, None)


def test_blocked_or_stale_plan_cannot_prepare():
    state = initial_state("abcdef")
    with pytest.raises(SafetyBlocked):
        prepare_journal(replace(plan(), blocked_reasons=("conflict",)), state, "run")
    with pytest.raises(SafetyBlocked):
        prepare_journal(replace(plan(), state_revision="other"), state, "run")


def test_confirmation_requires_matching_result():
    state = prepare_journal(plan(), initial_state("abcdef"), "run")
    with pytest.raises(SafetyBlocked):
        record_operation(state, 0, "confirmed", None)
    with pytest.raises(SafetyBlocked):
        record_operation(state, 0, "confirmed", DnsRecord("id", "other.example", "8.8.8.8"))


def test_forged_verified_without_result_is_rejected_on_load():
    import json

    from smartgate.state.codec import decode_state, encode_state

    state = prepare_journal(plan(), initial_state("abcdef"), "run")
    data = json.loads(encode_state(state))
    data["journal"]["phase"] = "verified"
    data["journal"]["operation_statuses"] = ["confirmed"]
    with pytest.raises(SafetyBlocked):
        decode_state(json.dumps(data).encode(), "abcdef", "revision")


def test_forged_rolled_back_with_pending_is_rejected_on_load():
    import json

    from smartgate.state.codec import decode_state, encode_state

    state = prepare_journal(plan(), initial_state("abcdef"), "run")
    data = json.loads(encode_state(state))
    data["journal"]["phase"] = "rolled_back"
    with pytest.raises(SafetyBlocked):
        decode_state(json.dumps(data).encode(), "abcdef", "revision")


def test_complete_before_snapshot_includes_foreign_records_for_recovery():
    before = DnsRecord("old-id", "chatgpt.com", "1.1.1.1")
    foreign = DnsRecord("manual-id", "manual.example", "9.9.9.9")
    change = replace(
        plan(),
        operations=(
            Operation("replace", before.domain, before, DesiredRewrite(before.domain, "8.8.8.8")),
        ),
    )
    state = prepare_journal(
        change, initial_state("abcdef"), "run", before_snapshot=(before, foreign)
    )
    assert state.journal.before_snapshot == (before, foreign)
    with pytest.raises(SafetyBlocked):
        prepare_journal(change, initial_state("abcdef"), "run", before_snapshot=(foreign,))
