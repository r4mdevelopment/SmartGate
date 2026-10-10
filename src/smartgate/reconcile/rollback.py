"""Durable inverse intents; rollback never deletes drifted or foreign after IDs."""

from dataclasses import replace

from smartgate.errors import SafetyBlocked
from smartgate.models import (
    ApplyContext,
    DesiredRewrite,
    DnsRecord,
    RunJournal,
    RunReport,
    StateDocument,
)
from smartgate.ports import DnsProvider, StateStore
from smartgate.reconcile.common import (
    checkpoint,
    claim_execution,
    current,
    guard,
    journal_update,
    pending_checkpoint,
    report,
    restore_ownership,
    semantic_checks,
    snapshot_equal,
    validate_journal,
)
from smartgate.reconcile.plan import related


def reverse_actions(
    state: StateDocument, actual: tuple[DnsRecord, ...]
) -> tuple[tuple[str, DnsRecord | None], ...]:
    journal = validate_journal(state)
    domains = {op.domain for op in journal.plan.operations}
    allowed = {r.record_id for r in journal.before_managed}
    original_ids = {r.record_id for r in journal.before_snapshot}
    if len({r.record_id for r in actual}) != len(actual):
        raise SafetyBlocked("Duplicate actual ID")
    for record in state.managed_records:
        if (
            record.domain not in domains
            and DnsRecord(record.record_id, record.domain, record.content) not in actual
        ):
            raise SafetyBlocked("Unrelated owned drift")
    actions = []
    for index, operation in enumerate(journal.plan.operations):
        before, after = operation.before, operation.after
        records = [r for r in actual if r.domain == operation.domain]
        if len(records) > 1:
            raise SafetyBlocked("Ambiguous inverse domain")
        found = records[0] if records else None
        saved = journal.operation_results[index]
        status = journal.operation_statuses[index]
        if status in {"pending", "failed"} and (
            (before is None and found is not None) or (before is not None and found != before)
        ):
            raise SafetyBlocked("Unstarted operation has manual drift")
        if status == "confirmed" and after is not None and found != saved:
            raise SafetyBlocked("Confirmed after ID missing or drifted")
        if found and before and found.record_id == before.record_id and found != before:
            raise SafetyBlocked("Original ID drift")
        if status == "rolled_back":
            if (before is None and found is not None) or (
                before is not None
                and (found != saved or found is None or found.content != before.content)
            ):
                raise SafetyBlocked("Restored ID drift")
            action = "none"
        elif before is not None and found == before:
            action = "none"
        elif before is not None and found and found.content == before.content:
            if (
                not journal.rollback_started
                or status != "rollback_pending"
                or found.record_id in original_ids
                or (saved and found.record_id == saved.record_id)
            ):
                raise SafetyBlocked("Unattributed restoration")
            action = "none"
        elif found is None:
            action = "create" if before else "none"
        elif after is not None and found.content == after.ipv4:
            if status not in {"unknown", "confirmed", "rollback_pending"}:
                raise SafetyBlocked("After record has no started mutation intent")
            if found.record_id in original_ids or (saved is not None and found != saved):
                raise SafetyBlocked("After ID drift or pre-existing record")
            if status == "confirmed" and saved is None:
                raise SafetyBlocked("Confirmed result missing")
            action = "replace" if before else "delete"
        else:
            raise SafetyBlocked("Manual drift blocks inverse")
        if found:
            allowed.add(found.record_id)
        actions.append((action, found))
    if any(related(r.domain, d) for r in actual if r.record_id not in allowed for d in domains):
        raise SafetyBlocked("Foreign subtree blocks rollback")
    return tuple(actions)


def rollback(
    journal: RunJournal,
    dns: DnsProvider,
    store: StateStore,
    state: StateDocument,
    context: ApplyContext,
) -> RunReport:
    writes = 0
    try:
        guard(context, state)
        current(store, state)
        if state.journal != journal:
            raise SafetyBlocked("Rollback journal changed")
        validate_journal(state)
        actual = dns.list_records()
        actions = reverse_actions(state, actual)
        state = claim_execution(store, state, context)
        assert state.journal is not None
        journal = state.journal
        state = checkpoint(
            store, replace(state, journal=replace(journal, phase="applying", rollback_started=True))
        )
    except SafetyBlocked as error:
        return report(context, "blocked", str(error))
    except Exception:
        return report(context, "blocked", "Rollback attribution or durable intent unavailable")
    expected = actual
    for index in reversed(range(len(journal.plan.operations))):
        operation = journal.plan.operations[index]
        action, found = actions[index]
        if state.journal is not None and state.journal.operation_statuses[index] == "rolled_back":
            continue
        try:
            guard(context, state)
            if not snapshot_equal(dns.list_records(), expected):
                raise SafetyBlocked("DNS changed before inverse")
            assert state.journal is not None
            saved = state.journal.operation_results[index]
            state = checkpoint(
                store,
                journal_update(
                    state,
                    index,
                    "rollback_pending",
                    found if action in {"replace", "delete"} else saved,
                ),
            )
            current(store, state)
            if action == "delete":
                assert found is not None
                writes += 1
                dns.delete(found)
                result = None
            elif action == "replace":
                assert found is not None and operation.before is not None
                writes += 1
                result = dns.replace(
                    found, DesiredRewrite(operation.domain, operation.before.content)
                )
            elif action == "create":
                assert operation.before is not None
                writes += 1
                result = dns.create(DesiredRewrite(operation.domain, operation.before.content))
            else:
                result = found if operation.before else None
            if operation.before is not None and (
                result is None
                or result.domain != operation.domain
                or result.content != operation.before.content
                or not result.record_id
            ):
                raise SafetyBlocked("Inverse returned unexpected record")
            expected = tuple(r for r in expected if r != found)
            if result:
                expected += (result,)
            if not snapshot_equal(dns.list_records(), expected):
                raise SafetyBlocked("Inverse read-back failed")
            state = journal_update(state, index, "rolled_back", result)
            state = restore_ownership(state, operation, result, context)
            state = checkpoint(store, state)
        except Exception:
            pending_checkpoint(store, state)
            return report(
                context,
                "partial",
                "Inverse or checkpoint unconfirmed; recover rollback first",
                writes,
            )
    checks, ok = semantic_checks(state, context)
    assert state.journal is not None
    try:
        guard(context, state)
        if not snapshot_equal(dns.list_records(), expected):
            raise SafetyBlocked("API drift after DNS verification")
    except Exception:
        pending_checkpoint(store, state)
        return report(
            context, "partial", "Final rollback API state or deadline changed", writes, checks
        )
    try:
        checkpoint(
            store,
            replace(
                state, journal=replace(state.journal, phase="rolled_back" if ok else "partial")
            ),
        )
    except Exception:
        pending_checkpoint(store, state)
        return report(context, "partial", "Final rollback save failed", writes, checks)
    return report(
        context,
        "applied" if ok else "degraded",
        "" if ok else "Rollback API confirmed; profile DNS unconfirmed",
        writes,
        checks,
    )
