from dataclasses import replace

from smartgate.models import DesiredRewrite, DnsRecord
from smartgate.reconcile.apply import apply_plan
from smartgate.reconcile.plan import build_plan
from smartgate.reconcile.rollback import rollback
from tests.reconcile_fakes import FakeDns, MemoryStore, context, owned, policy, target


def applied():
    before, state = owned()
    after = (DesiredRewrite(before.domain, "1.1.1.1"),)
    dns, store = FakeDns((before,)), MemoryStore(state)
    changed = replace(context(after), selections=(target(before.domain, "1.1.1.1", "own"),))
    plan = build_plan(after, (before,), state, frozenset(), policy(max_change_ratio=1))
    assert apply_plan(plan, dns, store, state, changed).status == "applied"
    return before, after, dns, store


def test_inverse_replace_restores_before_provider_and_new_record_id():
    before, after, dns, store = applied()
    report = rollback(store.state.journal, dns, store, store.state, context(after))
    assert report.status == "applied"
    assert dns.records[0].content == before.content
    assert store.state.managed_records[0].record_id == dns.records[0].record_id
    assert store.state.managed_records[0].provider_id == "relay"
    assert store.state.selections[0].candidate.ip == before.content
    assert store.state.journal.phase == "rolled_back"


def test_manual_changed_after_or_foreign_same_value_is_not_touched():
    for same_value in (True, False):
        before, after, dns, store = applied()
        current = dns.records[0]
        dns.records[0] = DnsRecord(
            "foreign" if same_value else current.record_id,
            current.domain,
            current.content if same_value else "9.9.9.9",
        )
        writes = dns.writes
        assert (
            rollback(store.state.journal, dns, store, store.state, context(after)).status
            == "blocked"
        )
        assert dns.writes == writes


def test_rollback_create_and_delete_only_owned_ids():
    from smartgate.models import StateDocument

    state = StateDocument(1, "abcdef", None)
    desired = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    dns, store = FakeDns(), MemoryStore()
    plan = build_plan(desired, (), state, frozenset(), policy(bootstrap=True))
    apply_plan(plan, dns, store, state, context(desired))
    assert (
        rollback(store.state.journal, dns, store, store.state, context(desired)).status == "applied"
    )
    assert dns.records == [] and store.state.managed_records == ()

    before, state = owned()
    dns, store = FakeDns((before,)), MemoryStore(state)
    plan = build_plan(
        (),
        (before,),
        state,
        frozenset({before.domain}),
        policy(allow_prune=True, max_change_ratio=1),
    )
    apply_plan(plan, dns, store, state, context())
    assert rollback(store.state.journal, dns, store, store.state, context()).status == "applied"
    assert dns.records[0].content == before.content


def test_interrupted_inverse_replace_resumes_its_own_intent():
    import pytest

    from smartgate.reconcile.recovery import recover_pending

    before, after, dns, store = applied()
    dns.mode = "kill_after_delete"
    with pytest.raises(KeyboardInterrupt):
        rollback(store.state.journal, dns, store, store.state, context(after))
    assert store.state.journal.rollback_started
    assert store.state.journal.operation_statuses == ("rollback_pending",)
    assert dns.records == []
    assert recover_pending(dns, store, store.state, context(after)).status == "applied"
    assert dns.records[0].content == before.content
    assert store.state.journal.phase == "rolled_back"


def test_committed_restore_timeout_is_attributed_without_duplicate():
    from smartgate.reconcile.recovery import recover_pending

    before, after, dns, store = applied()
    dns.mode = "timeout_create"
    assert (
        rollback(store.state.journal, dns, store, store.state, context(after)).status == "partial"
    )
    writes = dns.writes
    assert recover_pending(dns, store, store.state, context(after)).status == "applied"
    assert dns.writes == writes and len(dns.records) == 1
    assert store.state.managed_records[0].record_id == dns.records[0].record_id


def test_stale_rollback_journal_and_prepared_save_failure_stop_writes():
    before, after, dns, store = applied()
    writes = dns.writes
    assert (
        rollback(
            replace(store.state.journal, run_id="other"), dns, store, store.state, context(after)
        ).status
        == "blocked"
    )
    store.fail_at = store.saves + 1
    assert (
        rollback(store.state.journal, dns, store, store.state, context(after)).status == "blocked"
    )
    assert dns.writes == writes
