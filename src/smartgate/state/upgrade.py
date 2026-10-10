"""Explicit ownership-format upgrade; never performs DNS mutations."""

from dataclasses import replace

from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.errors import SafetyBlocked
from smartgate.models import StateDocument
from smartgate.reconcile.common import validate_journal
from smartgate.reconcile.plan import build_plan
from smartgate.state.codec import encode_state


def upgrade_legacy_state(state: StateDocument, target: DnsTarget) -> StateDocument:
    if (
        state.schema_version != 1
        or target.provider != "nextdns"
        or state.profile_id != target.namespace
        or target.api_origin != "https://api.nextdns.io"
        or target.doh_url != "https://dns.nextdns.io/" + target.namespace
    ):
        raise SafetyBlocked("Legacy state does not match the approved DNS target")
    if state.journal and state.journal.phase not in {"verified", "rolled_back"}:
        raise SafetyBlocked("Recover unfinished legacy journal before state upgrade")
    fingerprint = target_fingerprint(target)
    journal = state.journal
    if journal:
        validate_journal(state)
        assert journal.plan.policy is not None
        original = replace(
            state,
            revision=journal.plan.state_revision,
            managed_records=journal.before_managed,
            selections=journal.before_selections,
            journal=None,
            schema_version=2,
            profile_id=target.instance_id,
            target_id=target.instance_id,
            target_fingerprint=fingerprint,
        )
        bound_plan = build_plan(
            journal.plan.desired,
            journal.before_snapshot,
            original,
            frozenset(journal.plan.removals),
            journal.plan.policy,
        )
        journal = replace(
            journal,
            target_id=target.instance_id,
            target_fingerprint=fingerprint,
            plan=bound_plan,
        )
    result = replace(
        state,
        schema_version=2,
        profile_id=target.instance_id,
        target_id=target.instance_id,
        target_fingerprint=fingerprint,
        journal=journal,
    )
    encode_state(result)
    if result.journal:
        validate_journal(result)
    return result


def validate_upgrade_pair(old: StateDocument, new: StateDocument) -> None:
    if old.schema_version != 1 or new.schema_version != 2 or old.revision != new.revision:
        raise SafetyBlocked("Invalid state upgrade precondition")
    if old.journal and old.journal.phase not in {"verified", "rolled_back"}:
        raise SafetyBlocked("Pending state cannot be upgraded")
    journal = new.journal
    if journal:
        if old.journal is None:
            raise SafetyBlocked("State upgrade cannot introduce a journal")
        validate_journal(old)
        validate_journal(new)
        journal = replace(
            journal,
            target_id="",
            target_fingerprint="",
            plan=replace(
                journal.plan, plan_id=old.journal.plan.plan_id, target_id="", target_fingerprint=""
            ),
        )
    restored = replace(
        new,
        schema_version=1,
        profile_id=old.profile_id,
        target_id="",
        target_fingerprint="",
        journal=journal,
    )
    if restored != old:
        raise SafetyBlocked("State upgrade must preserve ownership and journal")
    encode_state(new)
