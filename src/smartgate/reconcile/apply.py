"""Serial mutation executor with durable intent before DNS writes."""

from dataclasses import replace
from datetime import timedelta

from smartgate.errors import SafetyBlocked
from smartgate.models import ApplyContext, ChangePlan, RunReport, StateDocument
from smartgate.ports import DnsProvider, StateStore
from smartgate.reconcile.common import (
    apply_ownership,
    checkpoint,
    current,
    guard,
    journal_update,
    pending_checkpoint,
    report,
    semantic_checks,
    snapshot_equal,
    targets,
)
from smartgate.reconcile.plan import build_plan, dns_digest
from smartgate.state.journal import prepare_journal


def apply_plan(
    plan: ChangePlan,
    dns: DnsProvider,
    store: StateStore,
    state: StateDocument,
    context: ApplyContext,
) -> RunReport:
    writes = 0
    try:
        guard(context, state)
        if plan.target_id != state.target_id or plan.target_fingerprint != state.target_fingerprint:
            raise SafetyBlocked("Plan DNS binding changed")
        loaded = current(store, state)
        if state.journal and state.journal.phase not in {"verified", "rolled_back"}:
            raise SafetyBlocked("Pending journal requires recovery")
        policy = plan.policy
        if policy is None or context.config_digest != plan.config_digest or plan.blocked_reasons:
            raise SafetyBlocked("Executable plan policy or current config missing")
        if not context.now() - timedelta(minutes=20) <= plan.created_at <= context.now():
            raise SafetyBlocked("Plan expired; rebuild preview")
        actual = dns.list_records()
        if dns_digest(actual) != plan.dns_digest or state.revision != plan.state_revision:
            raise SafetyBlocked("Plan input changed; rebuild preview")
        refreshed = build_plan(plan.desired, actual, state, frozenset(plan.removals), policy)
        if refreshed != plan:
            raise SafetyBlocked("Plan failed fresh safety validation")
        selections = targets(context)
        for operation in plan.operations:
            if operation.after is not None:
                selection = selections.get(operation.domain)
                if selection is None or selection.candidate.ip != operation.after.ipv4:
                    raise SafetyBlocked("Target route metadata missing")
        if not plan.operations:
            managed = tuple(
                replace(r, provider_id=selections[r.domain].candidate.provider_id)
                if r.domain in selections and selections[r.domain].candidate.ip == r.content
                else r
                for r in state.managed_records
            )
            cache = {s.domain: s for s in state.selections}
            for r in managed:
                if r.domain in selections:
                    if selections[r.domain].candidate.ip != r.content:
                        raise SafetyBlocked("No-op route metadata disagrees")
                    cache[r.domain] = selections[r.domain]
            updated = replace(
                state, managed_records=managed, selections=tuple(cache[d] for d in sorted(cache))
            )
            if updated != state:
                state = checkpoint(store, updated)
            checks, ok = semantic_checks(state, context)
            guard(context, state)
            if not snapshot_equal(dns.list_records(), actual):
                raise SafetyBlocked("API drift after no-op DNS verification")
            return report(
                context,
                "no_change" if ok else "degraded",
                "" if ok else "Profile DNS verification not confirmed",
                checks=checks,
            )
        if loaded is None and (not policy.bootstrap or actual):
            raise SafetyBlocked("Initial ownership requires explicit empty-profile bootstrap")
        state = prepare_journal(plan, state, context.run_id, before_snapshot=actual)
        assert state.journal is not None
        state = replace(
            state,
            journal=replace(
                state.journal,
                before_managed=state.managed_records,
                before_selections=state.selections,
                target_selections=context.selections,
                execution_run_id=context.run_id,
                execution_deadline=context.deadline,
            ),
        )
        state = checkpoint(store, state)
    except SafetyBlocked as error:
        return report(context, "blocked", str(error))
    except Exception:
        return report(context, "blocked", "Fresh plan or durable intent unavailable")
    expected = actual
    for index, operation in enumerate(plan.operations):
        try:
            guard(context, state)
            if not snapshot_equal(dns.list_records(), expected):
                raise SafetyBlocked("DNS changed before mutation")
        except Exception:
            pending_checkpoint(store, state)
            return report(
                context, "partial", "DNS precondition or execution deadline changed", writes
            )
        try:
            state = checkpoint(store, journal_update(state, index, "unknown", None))
            current(store, state)
            writes += 1
            if operation.kind == "create":
                assert operation.after is not None
                result = dns.create(operation.after)
            elif operation.kind == "replace":
                assert operation.before is not None and operation.after is not None
                result = dns.replace(operation.before, operation.after)
            else:
                assert operation.before is not None
                dns.delete(operation.before)
                result = None
            if operation.after and (
                result is None
                or result.domain != operation.after.domain
                or result.content != operation.after.ipv4
                or not result.record_id
                or result.record_id in {r.record_id for r in actual}
            ):
                raise SafetyBlocked("Mutation returned an unconfirmed record")
            expected = tuple(r for r in expected if r != operation.before)
            if result is not None:
                expected += (result,)
            if not snapshot_equal(dns.list_records(), expected):
                raise SafetyBlocked("Mutation read-back failed")
            state = journal_update(state, index, "confirmed", result)
            state = apply_ownership(state, operation, result, context)
            state = checkpoint(store, state)
        except Exception:
            state = journal_update(state, index, "unknown", None)
            pending_checkpoint(store, state)
            return report(
                context,
                "partial",
                "Mutation result or state checkpoint unconfirmed; recover first",
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
            context, "partial", "Final API state or deadline changed; recover first", writes, checks
        )
    try:
        state = checkpoint(
            store,
            replace(state, journal=replace(state.journal, phase="verified" if ok else "partial")),
        )
    except Exception:
        pending_checkpoint(store, state)
        return report(
            context,
            "partial",
            "Final journal save failed; recover before another apply",
            writes,
            checks,
        )
    return report(
        context,
        "applied" if ok else "degraded",
        "" if ok else "API confirmed; profile DNS verification not confirmed",
        writes,
        checks,
    )
