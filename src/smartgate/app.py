"""Compose provider-independent control-plane stages with explicit write permission."""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from time import sleep
from uuid import uuid4

from smartgate.availability import measure_availability
from smartgate.candidates import endpoint_key
from smartgate.config import AppConfig
from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.errors import (
    ConfigError,
    DnsApiError,
    RecoveryRequired,
    SafetyBlocked,
    StateConflict,
)
from smartgate.health.batch import (
    advance_probe_cursors,
    probe_candidates,
    select_confirmation_batch,
    select_probe_batch,
)
from smartgate.models import (
    ApplyContext,
    DesiredRewrite,
    DnsCheck,
    PlanContext,
    RelaySnapshot,
    RunReport,
    Selection,
    StateDocument,
)
from smartgate.ports import DnsProvider, HealthChecker, RelayProvider, StateStore
from smartgate.providers.hosts import validate_snapshot
from smartgate.reconcile.apply import apply_plan
from smartgate.reconcile.plan import build_plan, dns_digest
from smartgate.reconcile.recovery import recover_pending
from smartgate.reconcile.rollback import rollback
from smartgate.routing import routing_policy_digest, select_routes, to_desired
from smartgate.state.journal import initial_state


@dataclass(frozen=True)
class AppDependencies:
    relays: Mapping[str, RelayProvider]
    checker: HealthChecker
    dns: DnsProvider
    store: StateStore
    verifier: Callable[[str, tuple[DesiredRewrite, ...]], tuple[DnsCheck, ...]]
    clock: Callable[[], datetime]
    profile_id: str
    bootstrap: bool = False
    subtree_capabilities: bool = False
    vantage: str = "local"
    offline: bool = False
    rollback_run_id: str | None = None
    allow_recovery_takeover: bool = False
    bootstrap_state: Callable[[StateDocument], str] | None = None
    preflight_writes: Callable[[int], None] | None = None
    dns_target: DnsTarget | None = None
    production_apply: bool = True
    upgrade_state: Callable[[bool], None] | None = None
    load_legacy_state: Callable[[], StateDocument | None] | None = None
    legacy_state_key: str | None = None
    require_no_change: bool = False
    encrypt_state: Callable[[bool], None] | None = None
    rotate_state: Callable[[bool], None] | None = None


def config_digest(config: AppConfig) -> str:
    return hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def run(config: AppConfig, mode: str, apply: bool, dependencies: AppDependencies) -> RunReport:
    deps = dependencies
    run_id = uuid4().hex
    report = RunReport(
        run_id,
        "blocked",
        (),
        (),
        (),
        (),
        offline=deps.offline,
        dns_provider=deps.dns_target.provider if deps.dns_target else config.dns.provider,
        dns_readiness="offline"
        if deps.offline
        else "production-ready"
        if deps.production_apply
        else "reference-read-only",
    )
    wrote_state = False
    try:
        if deps.require_no_change and (
            mode != "sync" or not apply or deps.offline or deps.bootstrap
        ):
            raise SafetyBlocked("No-change acceptance requires live sync --apply without bootstrap")
        if mode not in {
            "preview",
            "sync",
            "doctor",
            "recover",
            "rollback",
            "state-upgrade",
            "state-encrypt",
            "state-rotate",
        }:
            raise ConfigError("Unsupported command")
        if apply and (
            mode
            not in {"sync", "recover", "rollback", "state-upgrade", "state-encrypt", "state-rotate"}
            or deps.offline
        ):
            raise SafetyBlocked("Apply requires a live sync, recover or rollback command")
        if apply and not deps.production_apply:
            raise SafetyBlocked("DNS adapter is not verified for production apply")
        if mode in {"state-encrypt", "state-rotate"}:
            state_command = deps.rotate_state if mode == "state-rotate" else deps.encrypt_state
            if (
                state_command is None
                or deps.bootstrap
                or deps.allow_recovery_takeover
                or deps.rollback_run_id
            ):
                raise SafetyBlocked(
                    "Explicit state encryption is unavailable for this configuration"
                )
            state_command(apply)
            return replace(
                report, status="no_change" if apply else "preview", counts=(("mutations", 0),)
            )
        if mode == "state-upgrade":
            if deps.upgrade_state is None:
                raise SafetyBlocked("State upgrade is unavailable for this configuration")
            deps.upgrade_state(apply)
            return replace(
                report,
                status="no_change" if apply else "preview",
                errors=("State format upgrade checked; no DNS mutations",),
            )
        if apply and not config.sync.enabled:
            raise SafetyBlocked("sync.enabled=false blocks apply")
        started = deps.clock()
        if started.tzinfo is None:
            raise SafetyBlocked("Controller requires an aware clock")
        try:
            loaded = deps.store.load(deps.profile_id)
        except SafetyBlocked:
            if deps.load_legacy_state is None:
                raise
            loaded = deps.load_legacy_state()
            if loaded is None or loaded.schema_version != 1:
                raise SafetyBlocked("Legacy inspection did not find valid old state") from None
        binding = deps.dns_target
        if binding is not None:
            fingerprint = target_fingerprint(binding)
            if loaded is None and deps.legacy_state_key is not None:
                loaded = deps.store.load(deps.legacy_state_key)
            if loaded is not None and loaded.schema_version == 1:
                if deps.legacy_state_key is None or loaded.profile_id != deps.legacy_state_key:
                    raise SafetyBlocked("Legacy state belongs to another DNS destination")
                if apply:
                    raise SafetyBlocked("Explicit state-upgrade is required before DNS apply")
                # Read-only legacy inspection cannot inherit an executable v2 binding.
                state = loaded
            elif loaded is not None:
                if (
                    loaded.target_id != binding.instance_id
                    or loaded.target_fingerprint != fingerprint
                ):
                    raise SafetyBlocked("DNS destination differs from ownership state")
                state = loaded
            else:
                state = StateDocument(
                    2,
                    binding.instance_id,
                    None,
                    target_id=binding.instance_id,
                    target_fingerprint=fingerprint,
                )
        else:
            state = loaded or initial_state(deps.profile_id)
        digest = config_digest(config)
        context = ApplyContext(
            deps.clock,
            started + timedelta(minutes=20),
            run_id,
            deps.profile_id,
            verify=deps.verifier,
            config_digest=digest,
            allow_recovery_takeover=deps.allow_recovery_takeover,
            target_id=state.target_id,
            target_fingerprint=state.target_fingerprint,
        )
        pending = state.journal and state.journal.phase not in {"verified", "rolled_back"}
        if deps.require_no_change and pending:
            raise SafetyBlocked("No-change acceptance refuses pending journal recovery")
        if mode in {"recover", "rollback"} or pending:
            report = replace(
                report,
                journal_phase=state.journal.phase if state.journal else None,
                journal_run_id=state.journal.run_id if state.journal else None,
                plan=state.journal.plan if state.journal else None,
            )
            if not state.journal:
                raise SafetyBlocked("No saved journal to recover or roll back")
            if mode == "rollback" and deps.rollback_run_id != state.journal.run_id:
                raise SafetyBlocked("Rollback requires the exact latest journal run ID")
            if not apply:
                return replace(
                    report,
                    status="partial" if pending else "preview",
                    errors=(
                        "Pending journal requires explicit recovery"
                        if pending
                        else "Journal inspected; no writes authorized",
                    ),
                )
            if deps.preflight_writes is not None:
                deps.preflight_writes(len(state.journal.plan.operations))
            # Recovery is the entire command; never start a new plan in the same run.
            result = (
                rollback(state.journal, deps.dns, deps.store, state, context)
                if mode == "rollback"
                else recover_pending(deps.dns, deps.store, state, context)
            )
            wrote_state = result.status != "blocked"
            recovered = deps.store.load(deps.profile_id) or state
            return replace(
                result,
                plan=state.journal.plan,
                journal_phase=recovered.journal.phase if recovered.journal else None,
                journal_run_id=state.journal.run_id,
                offline=deps.offline,
                dns_provider=report.dns_provider,
                dns_readiness=report.dns_readiness,
                availability=report.availability,
            )
        if apply and loaded is None and not deps.bootstrap:
            raise SafetyBlocked("Initial state requires explicit empty-profile bootstrap")
        actual = deps.dns.list_records()
        previous = {s.provider_id: s for s in state.last_sources}
        snapshots: list[RelaySnapshot] = []
        warnings: list[str] = []
        issue_codes: list[str] = []
        for provider in config.providers:
            old = previous.get(provider.id)
            try:
                fetched = deps.relays[provider.id].fetch(provider, started)
                snapshot = validate_snapshot(fetched, old, config.catalog, started)
            except Exception:
                warnings.append("Relay source unavailable or rejected: " + provider.id)
                issue_codes.append("source_unavailable")
                if old is None:
                    continue
                try:
                    # A failed fetch is retain-only even if a test clock has not advanced.
                    fallback = replace(
                        old, warnings=old.warnings + ("stale: source fetch failed; retain only",)
                    )
                    snapshot = validate_snapshot(fallback, None, config.catalog, started)
                except Exception:
                    continue
            snapshots.append(snapshot)
        sources = tuple(snapshots)
        cached = {p.id: previous[p.id] for p in config.providers if p.id in previous}
        for snapshot in sources:
            if snapshot.fetched_at == started and not any(
                w.startswith("stale:") for w in snapshot.warnings
            ):
                cached[snapshot.provider_id] = snapshot
        cache_sources = tuple(cached[p.id] for p in config.providers if p.id in cached)
        batch = select_probe_batch(sources, state, config.catalog, config.probe)
        probes = probe_candidates(batch, deps.checker, config.probe, deps.vantage)
        decisions = select_routes(config.catalog, sources, probes, state, started)
        confirmations = (
            select_confirmation_batch(probes, state, decisions, config.probe)
            if not deps.offline
            else ()
        )
        if confirmations:
            # No state/DNS writes or source refetches: only confirm failed current endpoints.
            sleep(5.0)
            retried = probe_candidates(confirmations, deps.checker, config.probe, deps.vantage)
            confirmed_results = {endpoint_key(r.candidate): r for r in retried}
            # Routing must see one current result per endpoint, not contradictory rounds.
            probes = tuple(confirmed_results.get(endpoint_key(r.candidate), r) for r in probes)
            decisions = select_routes(config.catalog, sources, probes, state, started)
        coverage = measure_availability(config.catalog, decisions, state)
        desired = to_desired(decisions)
        removals = frozenset(d.domain for d in decisions if d.action == "remove")
        policy = PlanContext(
            digest,
            started,
            config.sync.max_records,
            config.sync.max_mutations,
            config.sync.max_change_ratio,
            config.sync.allow_prune,
            deps.bootstrap,
            deps.subtree_capabilities,
            config.catalog.services,
        )
        plan = build_plan(desired, actual, state, removals, policy)
        report = replace(
            report,
            status="preview",
            decisions=decisions,
            plan=plan,
            sources=sources,
            probes=probes,
            counts=(("operations", len(plan.operations)),),
            availability=coverage,
            errors=tuple(warnings),
            issue_codes=tuple(sorted(set(issue_codes))),
        )
        if deps.require_no_change and plan.operations:
            raise SafetyBlocked("No-change acceptance blocked: fresh plan contains DNS operations")
        if plan.blocked_reasons:
            return replace(report, status="blocked", errors=tuple(warnings) + plan.blocked_reasons)
        if apply and deps.preflight_writes is not None:
            deps.preflight_writes(len(plan.operations))
        degraded = (
            bool(warnings)
            or any(d.reason.startswith("degraded:") for d in decisions)
            or any(value.total == 0 or value.available < value.total for value in coverage)
        )
        required = {
            domain
            for service in config.catalog.services
            if service.enabled
            for domain in service.required_domains
        }
        available = {d.domain for d in decisions if d.action in {"select", "retain"}}
        missing = required - available
        if missing:
            # Explicit initialization may save observations without publishing DNS.
            # Existing ledgers must advance failed batches to reach later candidates.
            if apply and (state.revision is not None or (deps.bootstrap and not actual)):
                observed = replace(
                    state,
                    last_sources=cache_sources,
                    probe_cursors=advance_probe_cursors(sources, state, config.catalog, probes),
                )
                wrote_state = True
                if state.revision is None and deps.bootstrap_state is not None:
                    deps.bootstrap_state(observed)
                else:
                    deps.store.save(observed, state.revision)
            return replace(
                report,
                status="degraded",
                errors=report.errors + ("Required domain has no usable route",),
            )
        degraded_decisions = tuple(d for d in decisions if d.reason.startswith("degraded:"))
        optional_only = (
            degraded
            and not warnings
            and all(
                d.action == "unavailable"
                and d.reason == "degraded: optional domain has no fresh verified route"
                for d in degraded_decisions
            )
        )
        if not apply:
            checks = (
                deps.verifier(
                    deps.profile_id,
                    tuple(DesiredRewrite(r.domain, r.content) for r in state.managed_records),
                )
                if mode == "doctor"
                else ()
            )
            if mode == "doctor":
                if deps.store.load(deps.profile_id) != loaded:
                    raise SafetyBlocked("State changed during read-only DNS verification")
                if dns_digest(deps.dns.list_records()) != dns_digest(actual):
                    raise SafetyBlocked("DNS records changed during read-only verification")
            confirmed = (
                mode == "doctor"
                and not deps.offline
                and loaded is not None
                and state.journal is not None
                and state.journal.phase in {"verified", "rolled_back"}
                and not plan.operations
                and bool(checks)
                and all(c.ok for c in checks)
            )
            return replace(
                report,
                status="degraded"
                if degraded or any(not c.ok for c in checks) or (mode == "doctor" and not confirmed)
                else "preview",
                checks=checks,
                counts=report.counts + (("mutations", 0),),
                journal_phase=state.journal.phase if mode == "doctor" and state.journal else None,
                journal_run_id=state.journal.run_id if mode == "doctor" and state.journal else None,
                optional_route_warnings=optional_only and confirmed,
                issue_codes=report.issue_codes
                + (("verification_incomplete",) if mode == "doctor" and not confirmed else ()),
            )
        if state.revision is None and deps.bootstrap_state is not None:
            if not deps.bootstrap or actual or state.managed_records:
                raise SafetyBlocked(
                    "State branch creation requires explicit empty-profile bootstrap"
                )
            revision = deps.bootstrap_state(state)
            wrote_state = True
            state = replace(state, revision=revision)
            plan = build_plan(desired, actual, state, removals, policy)
            report = replace(report, plan=plan)
        selections = tuple(
            Selection(d.domain, d.candidate, routing_policy_digest(service))
            for d in decisions
            if d.candidate is not None
            and d.action in {"select", "retain"}
            and (service := config.catalog.select(d.domain)) is not None
        )
        result = apply_plan(
            plan, deps.dns, deps.store, state, replace(context, selections=selections)
        )
        wrote_state = (
            wrote_state
            or result.status in {"applied", "partial"}
            or dict(result.counts).get("mutations", 0) > 0
        )
        if result.status in {"applied", "no_change", "degraded"}:
            latest = deps.store.load(deps.profile_id) or state
            updated = replace(
                latest,
                last_sources=cache_sources,
                probe_cursors=advance_probe_cursors(sources, state, config.catalog, probes),
            )
            if updated != latest:
                wrote_state = True
                deps.store.save(updated, latest.revision)
        if result.status in {"applied", "no_change"} and degraded:
            result = replace(result, status="degraded", optional_route_warnings=optional_only)
        final_state = deps.store.load(deps.profile_id)
        return replace(
            result,
            decisions=decisions,
            plan=plan,
            sources=sources,
            probes=probes,
            errors=result.errors + tuple(warnings),
            issue_codes=result.issue_codes + report.issue_codes,
            journal_phase=final_state.journal.phase
            if final_state and final_state.journal
            else None,
            journal_run_id=final_state.journal.run_id
            if final_state and final_state.journal
            else None,
            offline=deps.offline,
            dns_provider=report.dns_provider,
            dns_readiness=report.dns_readiness,
            availability=report.availability,
        )
    except RecoveryRequired as error:
        return replace(
            report,
            status="partial",
            errors=(str(error),),
            exit_code_override=6,
            issue_codes=report.issue_codes + ("recovery_required",),
        )
    except ConfigError:
        return replace(
            report,
            errors=("Invalid controller configuration",),
            exit_code_override=2,
            issue_codes=report.issue_codes + ("config_invalid",),
        )
    except (SafetyBlocked, StateConflict) as error:
        return replace(
            report,
            status="partial" if wrote_state else "blocked",
            errors=(str(error),),
            issue_codes=report.issue_codes
            + ("state_conflict" if isinstance(error, StateConflict) else "safety_blocked",),
        )
    except DnsApiError:
        return replace(
            report,
            status="partial" if wrote_state else "blocked",
            errors=("External service or state unavailable; inspect durable journal before retry",),
            exit_code_override=6 if wrote_state else 5,
            issue_codes=report.issue_codes + ("dns_api_unavailable",),
        )
    except Exception:
        return replace(
            report,
            status="partial" if wrote_state else "blocked",
            errors=("External service or state unavailable; inspect durable journal before retry",),
            exit_code_override=6 if wrote_state else 5,
            issue_codes=report.issue_codes + ("controller_error",),
        )
