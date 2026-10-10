"""Shared serial checkpoints and journal attribution checks; no adapter imports."""

from dataclasses import replace
from datetime import timedelta

from smartgate.errors import SafetyBlocked
from smartgate.models import (
    ApplyContext,
    DesiredRewrite,
    DnsCheck,
    DnsRecord,
    ManagedRecord,
    Operation,
    ReportStatus,
    RunJournal,
    RunReport,
    Selection,
    StateDocument,
)
from smartgate.ports import StateStore
from smartgate.reconcile.plan import build_plan, public_ipv4, related, validate_policy


def snapshot_equal(actual: tuple[DnsRecord, ...], expected: tuple[DnsRecord, ...]) -> bool:
    return len({r.record_id for r in actual}) == len(actual) == len(expected) and set(
        actual
    ) == set(expected)


def report(
    context: ApplyContext,
    status: ReportStatus,
    reason: str = "",
    writes: int = 0,
    checks: tuple[DnsCheck, ...] = (),
) -> RunReport:
    return RunReport(
        context.run_id, status, (("mutations", writes),), (), checks, (reason,) if reason else ()
    )


def guard(context: ApplyContext, state: StateDocument) -> None:
    now = context.now()
    if (
        context.profile_id != state.profile_id
        or context.target_id != state.target_id
        or context.target_fingerprint != state.target_fingerprint
        or now.tzinfo is None
        or context.deadline.tzinfo is None
        or not now < context.deadline <= now + timedelta(minutes=20)
        or not context.run_id
    ):
        raise SafetyBlocked("Profile or execution deadline invalid")


def current(store: StateStore, state: StateDocument) -> StateDocument | None:
    loaded = store.load(state.profile_id)
    if loaded is None:
        if state.revision is not None or state.managed_records or state.journal:
            raise SafetyBlocked("Ownership state disappeared")
    elif loaded != state:
        raise SafetyBlocked("State changed; reload before continuing")
    return loaded


def checkpoint(store: StateStore, state: StateDocument) -> StateDocument:
    revision = store.save(state, state.revision)
    return replace(state, revision=revision)


def claim_execution(
    store: StateStore, state: StateDocument, context: ApplyContext
) -> StateDocument:
    journal = validate_journal(state)
    guard(context, state)
    closed = journal.phase in {"verified", "rolled_back"}
    if not closed and journal.execution_run_id != context.run_id:
        if not context.allow_recovery_takeover and (
            journal.execution_deadline is None or context.now() <= journal.execution_deadline
        ):
            raise SafetyBlocked("Previous run may still be active; wait or confirm it stopped")
    return checkpoint(
        store,
        replace(
            state,
            journal=replace(
                journal,
                execution_run_id=context.run_id,
                execution_deadline=context.deadline,
            ),
        ),
    )


def journal_update(
    state: StateDocument, index: int, status: str, result: DnsRecord | None
) -> StateDocument:
    assert state.journal is not None
    statuses = list(state.journal.operation_statuses)
    results = list(state.journal.operation_results)
    statuses[index], results[index] = status, result
    return replace(
        state,
        journal=replace(
            state.journal,
            phase="applying",
            operation_statuses=tuple(statuses),
            operation_results=tuple(results),
        ),
    )


def pending_checkpoint(store: StateStore, state: StateDocument) -> None:
    if state.journal is not None:
        try:
            checkpoint(store, replace(state, journal=replace(state.journal, phase="partial")))
        except Exception:
            # Existing durable intent remains authoritative if this checkpoint fails.
            pass


def targets(context: ApplyContext) -> dict[str, Selection]:
    result = {s.domain: s for s in context.selections}
    if len(result) != len(context.selections):
        raise SafetyBlocked("Duplicate target selection")
    for domain, selection in result.items():
        candidate = selection.candidate
        if (
            candidate.domain != domain
            or not candidate.provider_id
            or not public_ipv4(candidate.ip)
            or candidate.port != 443
            or candidate.transport != "tls_sni_tcp"
            or not selection.routing_policy_digest
        ):
            raise SafetyBlocked("Invalid target selection")
    return result


def apply_ownership(
    state: StateDocument, operation: Operation, result: DnsRecord | None, context: ApplyContext
) -> StateDocument:
    assert state.journal is not None
    desired = {s.domain: s for s in state.journal.target_selections}
    managed = {r.domain: r for r in state.managed_records}
    selections = {s.domain: s for s in state.selections}
    if operation.kind == "delete":
        managed.pop(operation.domain, None)
        selections.pop(operation.domain, None)
    else:
        selection = desired.get(operation.domain)
        if result is None or selection is None or selection.candidate.ip != result.content:
            raise SafetyBlocked("Confirmed operation has no routing ownership metadata")
        managed[operation.domain] = ManagedRecord(
            result.record_id,
            result.domain,
            result.content,
            selection.candidate.provider_id,
            context.now(),
        )
        selections[operation.domain] = selection
    return replace(
        state,
        managed_records=tuple(managed[d] for d in sorted(managed)),
        selections=tuple(selections[d] for d in sorted(selections)),
    )


def restore_ownership(
    state: StateDocument, operation: Operation, result: DnsRecord | None, context: ApplyContext
) -> StateDocument:
    assert state.journal is not None
    managed = {r.domain: r for r in state.managed_records}
    selections = {s.domain: s for s in state.selections}
    originals = {r.domain: r for r in state.journal.before_managed}
    old_selections = {s.domain: s for s in state.journal.before_selections}
    if operation.before is None:
        managed.pop(operation.domain, None)
        selections.pop(operation.domain, None)
    else:
        original = originals.get(operation.domain)
        if original is None or result is None:
            raise SafetyBlocked("Restore lacks original ownership")
        managed[operation.domain] = replace(
            original, record_id=result.record_id, last_verified_at=context.now()
        )
        if operation.domain in old_selections:
            selections[operation.domain] = old_selections[operation.domain]
        else:
            selections.pop(operation.domain, None)
    return replace(
        state,
        managed_records=tuple(managed[d] for d in sorted(managed)),
        selections=tuple(selections[d] for d in sorted(selections)),
    )


def semantic_checks(
    state: StateDocument, context: ApplyContext
) -> tuple[tuple[DnsCheck, ...], bool]:
    desired = tuple(DesiredRewrite(r.domain, r.content) for r in state.managed_records)
    if not desired:
        return (), True
    if context.verify is None:
        return (), False
    try:
        guard(context, state)
        started = context.now()
        checks = context.verify(state.profile_id, desired)
        finished = context.now()
        expected = {
            (r.domain, q): (r.ipv4,) if q == "A" else ()
            for r in desired
            for q in ("A", "AAAA", "HTTPS", "SVCB")
        }
        keys = [(c.domain, c.qtype) for c in checks]
        valid = (
            len(keys) == len(expected)
            and set(keys) == set(expected)
            and all(
                c.ok
                and c.expected == expected[(c.domain, c.qtype)]
                and c.answers == c.expected
                and c.checked_at.tzinfo is not None
                and started <= c.checked_at <= finished
                for c in checks
            )
        )
        return checks, valid
    except Exception:
        return (), False


def validate_journal(state: StateDocument) -> RunJournal:
    if state.journal and (
        state.journal.target_id != state.target_id
        or state.journal.target_fingerprint != state.target_fingerprint
        or state.journal.plan.target_id != state.target_id
        or state.journal.plan.target_fingerprint != state.target_fingerprint
    ):
        raise SafetyBlocked("Saved journal DNS binding differs from state")
    journal = state.journal
    if journal is None or journal.plan.policy is None or journal.plan.blocked_reasons:
        raise SafetyBlocked("Missing executable recovery policy")
    validate_policy(journal.plan.policy)
    if len(journal.operation_statuses) != len(journal.plan.operations) or len(
        journal.operation_results
    ) != len(journal.plan.operations):
        raise SafetyBlocked("Invalid journal lengths")
    original_state = replace(
        state,
        revision=journal.plan.state_revision,
        managed_records=journal.before_managed,
        selections=journal.before_selections,
        journal=None,
    )
    rebuilt = build_plan(
        journal.plan.desired,
        journal.before_snapshot,
        original_state,
        frozenset(journal.plan.removals),
        journal.plan.policy,
    )
    if rebuilt != journal.plan:
        raise SafetyBlocked("Journal plan failed original safety validation")
    originals = {
        r.record_id: DnsRecord(r.record_id, r.domain, r.content) for r in journal.before_managed
    }
    for operation in journal.plan.operations:
        if operation.before is not None and (
            originals.get(operation.before.record_id) != operation.before
            or operation.before not in journal.before_snapshot
        ):
            raise SafetyBlocked("Journal before record is not owned")
        if operation.after is not None:
            selections = {s.domain: s for s in journal.target_selections}
            selection = selections.get(operation.domain)
            if selection is None or selection.candidate.ip != operation.after.ipv4:
                raise SafetyBlocked("Journal after has no routing metadata")
    return journal


def classify(state: StateDocument, actual: tuple[DnsRecord, ...]) -> tuple[str, ...]:
    """Attribute after records only to unique durable intents, never arbitrary IDs."""
    journal = validate_journal(state)
    if len({r.record_id for r in actual}) != len(actual):
        raise SafetyBlocked("Duplicate actual ID")
    domains = {op.domain for op in journal.plan.operations}
    for record in state.managed_records:
        if (
            record.domain not in domains
            and DnsRecord(record.record_id, record.domain, record.content) not in actual
        ):
            raise SafetyBlocked("Unrelated owned record drift")
    before_ids = {r.record_id for r in journal.before_snapshot}
    allowed = {r.record_id for r in journal.before_managed}
    outcomes = []
    for index, operation in enumerate(journal.plan.operations):
        before = operation.before
        same_id = next((r for r in actual if before and r.record_id == before.record_id), None)
        if same_id is not None and same_id != before:
            raise SafetyBlocked("Original owned ID drift")
        domain_records = [r for r in actual if r.domain == operation.domain]
        after = operation.after
        status = journal.operation_statuses[index]
        result = journal.operation_results[index]
        if status in {"pending", "failed"}:
            if (before is None and domain_records) or (before is not None and same_id != before):
                raise SafetyBlocked("Unstarted operation has manual drift")
        if status == "confirmed" and after is not None and result not in domain_records:
            raise SafetyBlocked("Confirmed after ID missing or drifted")
        matches = [r for r in domain_records if after and r.content == after.ipv4]
        if after is not None and matches:
            if status not in {"unknown", "confirmed"}:
                raise SafetyBlocked("After record has no started mutation intent")
            if len(matches) != 1 or matches[0].record_id in before_ids:
                raise SafetyBlocked("Ambiguous or pre-existing after record")
            if status == "confirmed" and result != matches[0]:
                raise SafetyBlocked("Confirmed after ID drift")
            if same_id is not None:
                raise SafetyBlocked("Before and after both present")
            allowed.add(matches[0].record_id)
            outcomes.append("after")
        elif before is not None and same_id == before:
            allowed.add(before.record_id)
            outcomes.append("before")
        elif not domain_records:
            outcomes.append("after" if operation.kind == "delete" else "gap")
        else:
            raise SafetyBlocked("Manual domain drift")
    if any(related(r.domain, d) for r in actual if r.record_id not in allowed for d in domains):
        raise SafetyBlocked("Foreign subtree conflict during recovery")
    return tuple(outcomes)
