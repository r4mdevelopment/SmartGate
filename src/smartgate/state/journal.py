"""Immutable recovery intent transitions; callers must persist each transition."""

from dataclasses import replace
from typing import cast

from smartgate.errors import SafetyBlocked
from smartgate.models import ChangePlan, DnsRecord, JournalPhase, RunJournal, StateDocument
from smartgate.state.codec import validate_profile


def initial_state(profile_id: str) -> StateDocument:
    validate_profile(profile_id)
    return StateDocument(1, profile_id, None)


def prepare_journal(
    plan: ChangePlan,
    state: StateDocument,
    run_id: str,
    *,
    before_snapshot: tuple[DnsRecord, ...] | None = None,
) -> StateDocument:
    if plan.blocked_reasons or plan.state_revision != state.revision or not run_id:
        raise SafetyBlocked("Cannot prepare blocked or stale plan")
    if state.journal is not None and state.journal.phase not in {"verified", "rolled_back"}:
        raise SafetyBlocked("Existing journal requires recovery")
    affected = tuple(op.before for op in plan.operations if op.before is not None)
    before = affected if before_snapshot is None else before_snapshot
    if any(record not in before for record in affected) or len(
        {record.record_id for record in before}
    ) != len(before):
        raise SafetyBlocked("Before snapshot does not match plan")
    journal = RunJournal(
        run_id,
        plan,
        "prepared",
        ("pending",) * len(plan.operations),
        before,
        (None,) * len(plan.operations),
        target_id=state.target_id,
        target_fingerprint=state.target_fingerprint,
    )
    return replace(state, journal=journal)


def record_operation(
    state: StateDocument, index: int, status: str, result: DnsRecord | None
) -> StateDocument:
    journal = state.journal
    if journal is None or journal.phase in {"verified", "rolled_back"}:
        raise SafetyBlocked("No pending journal")
    if type(index) is not int or not 0 <= index < len(journal.plan.operations):
        raise SafetyBlocked("Invalid operation index")
    if status not in {"pending", "confirmed", "unknown", "failed", "rolled_back"}:
        raise SafetyBlocked("Invalid operation status")
    operation = journal.plan.operations[index]
    if status == "confirmed":
        if operation.kind == "delete":
            if result is not None:
                raise SafetyBlocked("Delete confirmation cannot contain a record")
        elif (
            result is None
            or not result.record_id
            or operation.after is None
            or result.domain != operation.after.domain
            or result.content != operation.after.ipv4
        ):
            raise SafetyBlocked("Confirmation does not match operation")
    statuses = list(journal.operation_statuses)
    results = list(journal.operation_results)
    statuses[index], results[index] = status, result
    return replace(
        state,
        journal=replace(
            journal,
            phase="applying",
            operation_statuses=tuple(statuses),
            operation_results=tuple(results),
        ),
    )


def complete_journal(state: StateDocument, phase: str) -> StateDocument:
    journal = state.journal
    if journal is None or phase not in {"verified", "partial", "rolled_back"}:
        raise SafetyBlocked("Invalid journal completion")
    expected = "confirmed" if phase == "verified" else "rolled_back"
    if phase != "partial" and any(s != expected for s in journal.operation_statuses):
        raise SafetyBlocked("Cannot complete unconfirmed journal")
    return replace(state, journal=replace(journal, phase=cast(JournalPhase, phase)))
