"""Recover a pending forward intent or resume its durable inverse."""

from dataclasses import replace

from smartgate.errors import SafetyBlocked
from smartgate.models import ApplyContext, RunReport, StateDocument
from smartgate.ports import DnsProvider, StateStore
from smartgate.reconcile.common import (
    apply_ownership,
    checkpoint,
    claim_execution,
    classify,
    current,
    guard,
    journal_update,
    pending_checkpoint,
    report,
    semantic_checks,
    snapshot_equal,
    validate_journal,
)
from smartgate.reconcile.rollback import rollback


def recover_pending(
    dns: DnsProvider, store: StateStore, state: StateDocument, context: ApplyContext
) -> RunReport:
    try:
        guard(context, state)
        current(store, state)
        journal = validate_journal(state)
        if journal.phase in {"verified", "rolled_back"}:
            return report(context, "no_change")
        state = claim_execution(store, state, context)
        assert state.journal is not None
        journal = state.journal
        if journal.rollback_started:
            return rollback(journal, dns, store, state, context)
        actual = dns.list_records()
        outcomes = classify(state, actual)
        if any(outcome != "after" for outcome in outcomes):
            return rollback(journal, dns, store, state, context)
        # Every effect already exists. Adopt only IDs attributed to the saved intent.
        for index, operation in enumerate(journal.plan.operations):
            guard(context, state)
            if not snapshot_equal(dns.list_records(), actual):
                raise SafetyBlocked("API drift during recovery")
            result = next(
                (
                    r
                    for r in actual
                    if operation.after
                    and r.domain == operation.domain
                    and r.content == operation.after.ipv4
                ),
                None,
            )
            state = journal_update(state, index, "confirmed", result)
            state = apply_ownership(state, operation, result, context)
            state = checkpoint(store, state)
        checks, ok = semantic_checks(state, context)
        guard(context, state)
        if not snapshot_equal(dns.list_records(), actual):
            raise SafetyBlocked("API drift after recovered DNS verification")
        assert state.journal is not None
        state = checkpoint(
            store,
            replace(state, journal=replace(state.journal, phase="verified" if ok else "partial")),
        )
        return report(
            context,
            "no_change" if ok else "degraded",
            "" if ok else "Recovered API state; profile DNS still unconfirmed",
            checks=checks,
        )
    except SafetyBlocked as error:
        pending_checkpoint(store, state)
        return report(context, "blocked", str(error))
    except Exception:
        pending_checkpoint(store, state)
        return report(
            context, "blocked", "Recovery ambiguous, drifted or durable state unavailable"
        )
