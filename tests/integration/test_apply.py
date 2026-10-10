from dataclasses import replace
from datetime import timedelta

from smartgate.models import DesiredRewrite, StateDocument
from smartgate.reconcile.apply import apply_plan
from smartgate.reconcile.plan import build_plan
from tests.reconcile_fakes import NOW, FakeDns, MemoryStore, context, policy, verify_ok


def setup():
    desired = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    state = StateDocument(1, "abcdef", None)
    events = []
    dns = FakeDns(events=events)
    store = MemoryStore(events=events)
    plan = build_plan(desired, (), state, frozenset(), policy(bootstrap=True))
    return desired, state, events, dns, store, plan


def test_durable_intent_before_write_and_second_sync_noop():
    desired, state, events, dns, store, plan = setup()
    result = apply_plan(plan, dns, store, state, context(desired))
    assert result.status == "applied"
    assert events.index("store:prepared") < events.index("dns:create")
    assert store.state.managed_records[0].record_id == dns.records[0].record_id
    second = build_plan(desired, dns.list_records(), store.state, frozenset(), policy())
    writes = dns.writes
    assert apply_plan(second, dns, store, store.state, context(desired)).status == "no_change"
    assert dns.writes == writes


def test_failed_prepared_save_means_zero_writes():
    desired, state, events, dns, store, plan = setup()
    store.fail_at = 1
    assert apply_plan(plan, dns, store, state, context(desired)).status == "blocked"
    assert dns.writes == 0


def test_stale_config_dns_preview_and_deadline_block():
    desired, state, events, dns, store, plan = setup()
    assert (
        apply_plan(plan, dns, store, state, context(desired, config_digest="changed")).status
        == "blocked"
    )
    old = replace(plan, created_at=NOW - timedelta(minutes=21))
    assert apply_plan(old, dns, store, state, context(desired)).status == "blocked"
    dns.create(DesiredRewrite("manual.example", "1.1.1.1"))
    assert apply_plan(plan, dns, store, state, context(desired)).status == "blocked"


def test_pending_journal_and_final_save_failure_require_recovery():
    desired, state, events, dns, store, plan = setup()
    store.fail_at = 4
    assert apply_plan(plan, dns, store, state, context(desired)).status == "partial"
    assert store.state.journal.phase != "verified"
    newer = build_plan(desired, dns.list_records(), store.state, frozenset(), policy())
    assert apply_plan(newer, dns, store, store.state, context(desired)).status == "blocked"


def test_dns_verification_failure_is_not_applied_success():
    desired, state, events, dns, store, plan = setup()

    def failed(profile, records):
        return tuple(replace(c, ok=False) for c in verify_ok(profile, records))

    assert apply_plan(plan, dns, store, state, context(desired, verify=failed)).status == "degraded"
    assert store.state.journal.phase == "partial"


def test_real_local_journal_survives_process_interruption(tmp_path):
    import pytest

    from smartgate.reconcile.recovery import recover_pending
    from smartgate.state.local import LocalStateStore
    from tests.reconcile_fakes import owned

    before, state = owned()
    store = LocalStateStore(tmp_path / "state.json")
    store.save(state, None)
    state = store.load("abcdef")
    after = (DesiredRewrite(before.domain, "1.1.1.1"),)
    dns = FakeDns((before,))
    dns.mode = "kill_after_delete"
    plan = build_plan(after, (before,), state, frozenset(), policy(max_change_ratio=1))
    with pytest.raises(KeyboardInterrupt):
        apply_plan(plan, dns, store, state, context(after))
    loaded = store.load("abcdef")
    assert loaded.journal.before_managed == state.managed_records
    assert loaded.journal.before_selections == state.selections
    assert loaded.journal.target_selections == context(after).selections
    assert recover_pending(dns, store, loaded, context(after)).status == "applied"
    restored = store.load("abcdef")
    assert restored.journal.phase == "rolled_back"
    assert restored.managed_records[0].record_id == dns.records[0].record_id


def test_tampered_plan_or_target_metadata_is_blocked():
    desired, state, events, dns, store, plan = setup()
    changed = replace(plan.operations[0], after=DesiredRewrite("chatgpt.com", "1.1.1.1"))
    assert (
        apply_plan(replace(plan, operations=(changed,)), dns, store, state, context(desired)).status
        == "blocked"
    )
    assert apply_plan(plan, dns, store, state, context(())).status == "blocked"
    assert dns.writes == 0


def test_readback_failure_cannot_be_reported_as_applied():
    desired, state, events, dns, store, plan = setup()
    original = dns.list_records
    calls = [0]

    def read():
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("private-response")
        return original()

    dns.list_records = read
    report = apply_plan(plan, dns, store, state, context(desired))
    assert report.status == "partial"
    assert "private-response" not in str(report)
    assert store.state.journal.phase == "partial"


def test_profile_state_revision_mismatch_blocks_before_write():
    desired, state, events, dns, store, plan = setup()
    assert (
        apply_plan(plan, dns, store, state, context(desired, profile_id="123456")).status
        == "blocked"
    )
    store.state = replace(state, revision="foreign-writer")
    assert apply_plan(plan, dns, store, state, context(desired)).status == "blocked"
    assert dns.writes == 0


def test_removal_without_prune_keeps_disabled_owned_domain():
    from tests.reconcile_fakes import owned

    before, state = owned()
    dns, store = FakeDns((before,)), MemoryStore(state)
    ctx = policy(services=())
    plan = build_plan((), (before,), state, frozenset({before.domain}), ctx)
    assert not plan.blocked_reasons and not plan.operations
    assert apply_plan(plan, dns, store, state, context()).status == "no_change"
    assert dns.writes == 0 and dns.records == [before]


def test_noop_policy_change_updates_metadata_without_dns_mutation():
    from tests.reconcile_fakes import owned, target

    before, state = owned()
    desired = (DesiredRewrite(before.domain, before.content),)
    dns, store = FakeDns((before,)), MemoryStore(state)
    plan = build_plan(desired, (before,), state, frozenset(), policy())
    ctx = context(desired, selections=(target(before.domain, before.content, "own"),))
    assert apply_plan(plan, dns, store, state, ctx).status == "no_change"
    assert store.state.managed_records[0].provider_id == "own" and dns.writes == 0


def test_dns_drift_during_semantic_verification_blocks_final_success():
    desired, state, events, dns, store, plan = setup()

    def drift(profile, records):
        current = dns.records[0]
        dns.records[0] = replace(current, content="9.9.9.9")
        return verify_ok(profile, records)

    result = apply_plan(plan, dns, store, state, context(desired, verify=drift))
    assert result.status == "partial"
    assert store.state.journal.phase != "verified"


def test_duplicate_post_mutation_records_are_not_a_confirmed_snapshot():
    desired, state, events, dns, store, plan = setup()
    original = dns.list_records

    def duplicate():
        actual = original()
        return actual + actual if dns.writes else actual

    dns.list_records = duplicate
    assert apply_plan(plan, dns, store, state, context(desired)).status == "partial"
    assert store.state.journal.phase != "verified"


def test_noop_cannot_hide_api_drift_behind_cached_dns_answers():
    from tests.reconcile_fakes import owned

    before, state = owned()
    desired = (DesiredRewrite(before.domain, before.content),)
    dns, store = FakeDns((before,)), MemoryStore(state)
    plan = build_plan(desired, (before,), state, frozenset(), policy())

    def drift(profile, records):
        dns.records[0] = replace(before, content="9.9.9.9")
        return verify_ok(profile, records)

    assert apply_plan(plan, dns, store, state, context(desired, verify=drift)).status == "blocked"
    assert dns.writes == 0


def test_failed_per_operation_intent_cas_means_zero_dns_writes():
    desired, state, events, dns, store, plan = setup()
    store.fail_at = 2
    assert apply_plan(plan, dns, store, state, context(desired)).status == "partial"
    assert dns.writes == 0


def test_stale_dns_check_timestamps_do_not_confirm_application():
    desired, state, events, dns, store, plan = setup()

    def stale(profile, records):
        return tuple(
            replace(c, checked_at=NOW - timedelta(days=1)) for c in verify_ok(profile, records)
        )

    assert apply_plan(plan, dns, store, state, context(desired, verify=stale)).status == "degraded"
    assert store.state.journal.phase == "partial"
