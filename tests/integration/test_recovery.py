import pytest

from smartgate.models import DesiredRewrite, DnsRecord, StateDocument
from smartgate.reconcile.apply import apply_plan
from smartgate.reconcile.plan import build_plan
from smartgate.reconcile.recovery import recover_pending
from tests.reconcile_fakes import FakeDns, MemoryStore, context, owned, policy


def test_committed_create_timeout_is_adopted_without_duplicate():
    desired = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    dns.mode = "timeout_create"
    plan = build_plan(desired, (), state, frozenset(), policy(bootstrap=True))
    assert apply_plan(plan, dns, store, state, context(desired)).status == "partial"
    writes = dns.writes
    report = recover_pending(dns, store, store.state, context(desired))
    assert report.status in {"no_change", "applied"} and dns.writes == writes
    assert (
        len(dns.records) == 1
        and store.state.managed_records[0].record_id == dns.records[0].record_id
    )


def test_kill_between_delete_create_restores_before_and_updates_new_id():
    before, state = owned()
    after = (DesiredRewrite(before.domain, "1.1.1.1"),)
    dns, store = FakeDns((before,)), MemoryStore(state)
    plan = build_plan(after, (before,), state, frozenset(), policy(max_change_ratio=1))
    dns.mode = "kill_after_delete"
    with pytest.raises(KeyboardInterrupt):
        apply_plan(plan, dns, store, state, context(after))
    assert dns.records == [] and store.state.journal.phase == "applying"
    report = recover_pending(dns, store, store.state, context(after))
    assert report.status == "applied"
    restored = dns.records[0]
    assert restored.content == before.content and restored.record_id != before.record_id
    assert store.state.managed_records[0].record_id == restored.record_id
    assert store.state.journal.phase == "rolled_back"


def test_ambiguous_after_or_manual_drift_preserves_every_record():
    desired = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    dns.mode = "timeout_create"
    apply_plan(
        build_plan(desired, (), state, frozenset(), policy(bootstrap=True)),
        dns,
        store,
        state,
        context(desired),
    )
    dns.records.append(DnsRecord("foreign", desired[0].domain, desired[0].ipv4))
    before = list(dns.records)
    writes = dns.writes
    assert recover_pending(dns, store, store.state, context(desired)).status == "blocked"
    assert dns.writes == writes and dns.records == before


def test_recovery_retries_dns_semantics_without_repeating_mutation():
    desired = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    apply_plan(
        build_plan(desired, (), state, frozenset(), policy(bootstrap=True)),
        dns,
        store,
        state,
        context(desired, verify=lambda p, d: ()),
    )
    writes = dns.writes
    assert recover_pending(dns, store, store.state, context(desired)).status == "no_change"
    assert dns.writes == writes and store.state.journal.phase == "verified"


def test_final_state_save_failure_recovers_confirmed_result_without_duplicate():
    desired = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    store.fail_at = 4
    report = apply_plan(
        build_plan(desired, (), state, frozenset(), policy(bootstrap=True)),
        dns,
        store,
        state,
        context(desired),
    )
    assert report.status == "partial"
    writes = dns.writes
    assert recover_pending(dns, store, store.state, context(desired)).status == "no_change"
    assert dns.writes == writes


def test_unrelated_foreign_record_is_preserved_during_recovery():
    before, state = owned()
    foreign = DnsRecord("manual", "manual.example", "9.9.9.9")
    after = (DesiredRewrite(before.domain, "1.1.1.1"),)
    dns, store = FakeDns((before, foreign)), MemoryStore(state)
    dns.mode = "kill_after_delete"
    with pytest.raises(KeyboardInterrupt):
        apply_plan(
            build_plan(after, dns.list_records(), state, frozenset(), policy(max_change_ratio=1)),
            dns,
            store,
            state,
            context(after),
        )
    assert recover_pending(dns, store, store.state, context(after)).status == "applied"
    assert foreign in dns.records


def test_restore_save_failure_and_manual_drift_stop_recovery():
    before, state = owned()
    after = (DesiredRewrite(before.domain, "1.1.1.1"),)
    dns, store = FakeDns((before,)), MemoryStore(state)
    dns.mode = "kill_after_delete"
    with pytest.raises(KeyboardInterrupt):
        apply_plan(
            build_plan(after, (before,), state, frozenset(), policy(max_change_ratio=1)),
            dns,
            store,
            state,
            context(after),
        )
    store.fail_at = store.saves + 1
    writes = dns.writes
    assert recover_pending(dns, store, store.state, context(after)).status == "blocked"
    assert dns.writes == writes
    dns.records.append(DnsRecord("manual", before.domain, "9.9.9.9"))
    assert recover_pending(dns, store, store.state, context(after)).status == "blocked"
    assert dns.writes == writes


def test_multi_operation_interruption_rolls_back_only_attributed_records():
    wanted = (DesiredRewrite("a.example", "8.8.8.8"), DesiredRewrite("b.example", "1.1.1.1"))
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    original = dns.create

    def interrupted(desired):
        if desired.domain == "b.example":
            raise KeyboardInterrupt()
        return original(desired)

    dns.create = interrupted
    with pytest.raises(KeyboardInterrupt):
        apply_plan(
            build_plan(wanted, (), state, frozenset(), policy(bootstrap=True)),
            dns,
            store,
            state,
            context(wanted),
        )
    assert len(dns.records) == 1
    report = recover_pending(dns, store, store.state, context(wanted))
    assert report.status == "applied"
    assert dns.records == [] and store.state.managed_records == ()
    assert store.state.journal.phase == "rolled_back"


def test_modified_durable_plan_is_not_executable_recovery():
    from dataclasses import replace

    wanted = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    dns.mode = "timeout_create"
    apply_plan(
        build_plan(wanted, (), state, frozenset(), policy(bootstrap=True)),
        dns,
        store,
        state,
        context(wanted),
    )
    journal = store.state.journal
    changed = replace(journal.plan.operations[0], after=DesiredRewrite("chatgpt.com", "1.1.1.1"))
    store.state = replace(
        store.state, journal=replace(journal, plan=replace(journal.plan, operations=(changed,)))
    )
    writes = dns.writes
    assert recover_pending(dns, store, store.state, context(wanted)).status == "blocked"
    assert dns.writes == writes


def test_another_runner_cannot_recover_an_active_run():
    from dataclasses import replace

    wanted = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    dns.mode = "timeout_create"
    apply_plan(
        build_plan(wanted, (), state, frozenset(), policy(bootstrap=True)),
        dns,
        store,
        state,
        context(wanted),
    )
    writes = dns.writes
    assert (
        recover_pending(
            dns, store, store.state, replace(context(wanted), run_id="other-runner")
        ).status
        == "blocked"
    )
    assert dns.writes == writes


def test_expired_execution_lease_can_be_recovered_by_new_runner():
    from dataclasses import replace
    from datetime import timedelta

    from tests.reconcile_fakes import NOW, verify_ok

    wanted = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    dns.mode = "timeout_create"
    apply_plan(
        build_plan(wanted, (), state, frozenset(), policy(bootstrap=True)),
        dns,
        store,
        state,
        context(wanted),
    )
    future = NOW + timedelta(minutes=21)
    ctx = replace(
        context(wanted),
        run_id="next-runner",
        now=lambda: future,
        deadline=future + timedelta(minutes=20),
        verify=lambda p, d: tuple(replace(c, checked_at=future) for c in verify_ok(p, d)),
    )
    writes = dns.writes
    assert recover_pending(dns, store, store.state, ctx).status == "no_change"
    assert dns.writes == writes and store.state.journal.execution_run_id == "next-runner"


def test_explicit_takeover_requires_stopped_process_attestation_flag():
    from dataclasses import replace

    wanted = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    dns, store = FakeDns(), MemoryStore()
    dns.mode = "timeout_create"
    apply_plan(
        build_plan(wanted, (), state, frozenset(), policy(bootstrap=True)),
        dns,
        store,
        state,
        context(wanted),
    )
    ctx = replace(context(wanted), run_id="manual-recovery", allow_recovery_takeover=True)
    writes = dns.writes
    assert recover_pending(dns, store, store.state, ctx).status == "no_change"
    assert dns.writes == writes


@pytest.mark.parametrize("foreign_count", [1, 2])
@pytest.mark.parametrize("mode", ["recover", "rollback"])
def test_unstarted_create_intents_never_adopt_or_delete_manual_records(foreign_count, mode):
    from dataclasses import replace

    from smartgate.reconcile.rollback import rollback
    from smartgate.state.journal import prepare_journal

    desired = (DesiredRewrite("a.example", "8.8.8.8"), DesiredRewrite("b.example", "1.1.1.1"))
    state = StateDocument(1, "abcdef", None)
    plan = build_plan(desired, (), state, frozenset(), policy(bootstrap=True))
    prepared = prepare_journal(plan, state, "run", before_snapshot=())
    prepared = replace(
        prepared,
        journal=replace(
            prepared.journal,
            target_selections=context(desired).selections,
            execution_run_id="run",
            execution_deadline=context(desired).deadline,
        ),
    )
    manual = tuple(
        DnsRecord("manual-" + str(i), r.domain, r.ipv4)
        for i, r in enumerate(desired[:foreign_count])
    )
    dns, store = FakeDns(manual), MemoryStore(prepared)
    result = (
        recover_pending(dns, store, prepared, context(desired))
        if mode == "recover"
        else rollback(prepared.journal, dns, store, prepared, context(desired))
    )
    assert result.status == "blocked"
    assert dns.writes == 0 and tuple(dns.records) == manual
    assert not store.state.managed_records


@pytest.mark.parametrize("kind", ["replace", "delete"])
def test_unstarted_owned_operation_missing_before_is_manual_drift(kind):
    from dataclasses import replace

    from smartgate.state.journal import prepare_journal

    before, state = owned()
    desired = (DesiredRewrite(before.domain, "1.1.1.1"),) if kind == "replace" else ()
    removals = frozenset({before.domain}) if kind == "delete" else frozenset()
    plan = build_plan(
        desired, (before,), state, removals, policy(allow_prune=True, max_change_ratio=1)
    )
    prepared = prepare_journal(plan, state, "run", before_snapshot=(before,))
    prepared = replace(
        prepared,
        journal=replace(
            prepared.journal,
            before_managed=state.managed_records,
            before_selections=state.selections,
            target_selections=context(desired).selections,
            execution_run_id="run",
            execution_deadline=context(desired).deadline,
        ),
    )
    dns, store = FakeDns(), MemoryStore(prepared)
    assert recover_pending(dns, store, prepared, context(desired)).status == "blocked"
    assert dns.writes == 0 and not dns.records


@pytest.mark.parametrize("kind", ["create", "replace"])
@pytest.mark.parametrize("mode", ["recover", "rollback"])
def test_missing_confirmed_after_blocks_inverse_before_it_started(kind, mode):
    from dataclasses import replace

    from smartgate.reconcile.rollback import rollback

    before, state = owned() if kind == "replace" else (None, StateDocument(1, "abcdef", None))
    actual = (before,) if before else ()
    desired = (DesiredRewrite("chatgpt.com", "1.1.1.1"),)
    dns, store = FakeDns(actual), MemoryStore(state)
    plan = build_plan(
        desired, actual, state, frozenset(), policy(bootstrap=kind == "create", max_change_ratio=1)
    )
    assert apply_plan(plan, dns, store, state, context(desired)).status == "applied"
    dns.records = []
    writes = dns.writes
    if mode == "recover":
        store.state = replace(store.state, journal=replace(store.state.journal, phase="partial"))
        result = recover_pending(dns, store, store.state, context(desired))
    else:
        result = rollback(store.state.journal, dns, store, store.state, context(desired))
    assert result.status == "blocked" and dns.writes == writes and not dns.records
