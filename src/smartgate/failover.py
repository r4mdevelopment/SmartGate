"""Bounded replacement of repeatedly failed owned routes through the normal journal."""

from dataclasses import replace
from datetime import timedelta
from time import sleep
from typing import TYPE_CHECKING
from uuid import uuid4

from smartgate.candidates import candidate_pools
from smartgate.config import AppConfig
from smartgate.dns.types import target_fingerprint
from smartgate.dns.validation import validate_desired
from smartgate.errors import SafetyBlocked
from smartgate.health.batch import probe_candidates
from smartgate.models import (
    ApplyContext,
    DesiredRewrite,
    DnsRecord,
    PlanContext,
    ProbeResult,
    RelayCandidate,
    RunReport,
    Selection,
)
from smartgate.providers.hosts import validate_snapshot
from smartgate.reconcile.apply import apply_plan
from smartgate.reconcile.common import semantic_checks, snapshot_equal
from smartgate.reconcile.plan import build_plan
from smartgate.routing import routing_policy_digest

if TYPE_CHECKING:
    from smartgate.app import AppDependencies

MIN_FAILED = 5
MAX_REPLACEMENTS = 25
NETWORK_FAILURES = frozenset({"connection timed out", "TLS connection failed"})


def mass_failure(failed: int, total: int) -> bool:
    return failed >= MIN_FAILED and failed * 2 >= total


def run_failover(config: AppConfig, apply: bool, deps: "AppDependencies") -> RunReport:
    from smartgate.app import config_digest

    result = RunReport(uuid4().hex, "blocked", (), (), (), (), dns_provider=config.dns.provider)
    count = 0
    first = 0
    confirmed = 0
    written = False
    try:
        if (
            deps.offline
            or deps.bootstrap
            or deps.require_no_change
            or deps.rollback_run_id
            or deps.allow_recovery_takeover
        ):
            raise SafetyBlocked(
                "Emergency failover cannot initialize, recover or bypass acceptance"
            )
        if apply and (not config.sync.enabled or not deps.production_apply):
            raise SafetyBlocked("Emergency apply is disabled for this configuration")
        state = deps.store.load(deps.profile_id)
        binding = deps.dns_target
        if (
            binding is None
            or state is None
            or state.schema_version != 2
            or state.profile_id != binding.instance_id
            or state.target_id != binding.instance_id
            or state.target_fingerprint != target_fingerprint(binding)
        ):
            raise SafetyBlocked("Emergency failover requires bound ownership")
        if state.journal is None or state.journal.phase not in {"verified", "rolled_back"}:
            raise SafetyBlocked(
                "Emergency failover requires a closed journal; use existing recovery"
            )
        owned = tuple(sorted(state.managed_records, key=lambda r: r.domain))
        count = len(owned)
        if (
            not 1 <= count <= 100
            or len({r.domain for r in owned}) != count
            or len({r.record_id for r in owned}) != count
        ):
            raise SafetyBlocked("Invalid emergency ownership inventory")
        actual = deps.dns.list_records()
        if len({r.record_id for r in actual}) != len(actual) or any(
            DnsRecord(r.record_id, r.domain, r.content) not in actual for r in owned
        ):
            raise SafetyBlocked("Owned DNS changed; emergency failover stopped")
        selections = {s.domain: s for s in state.selections}
        currents = []
        for r in owned:
            validate_desired(DesiredRewrite(r.domain, r.content))
            s = selections.get(r.domain)
            service = config.catalog.select(r.domain)
            if (
                s is None
                or service is None
                or s.candidate.ip != r.content
                or s.candidate.domain != r.domain
                or s.candidate.provider_id != r.provider_id
                or s.routing_policy_digest != routing_policy_digest(service)
            ):
                raise SafetyBlocked("Owned route policy changed; emergency failover stopped")
            currents.append(s.candidate)
        started = deps.clock()
        digest = config_digest(config)
        context = ApplyContext(
            deps.clock,
            started + timedelta(minutes=20),
            result.run_id,
            deps.profile_id,
            verify=deps.verifier,
            config_digest=digest,
            target_id=state.target_id,
            target_fingerprint=state.target_fingerprint,
        )
        checks, ok = semantic_checks(state, context)
        if not ok:
            raise SafetyBlocked(
                "Current profile DNS could not be verified; emergency failover stopped"
            )

        def fresh_probes(candidates: tuple[RelayCandidate, ...]) -> tuple[ProbeResult, ...]:
            batch_start = deps.clock()
            observations = probe_candidates(candidates, deps.checker, config.probe, deps.vantage)
            batch_end = deps.clock()
            if any(
                p.checked_at.tzinfo is None
                or p.checked_at.utcoffset() is None
                or not batch_start <= p.checked_at <= batch_end
                for p in observations
            ):
                raise SafetyBlocked("Emergency probes require fresh current-batch observations")
            return observations

        results = fresh_probes(tuple(currents))
        used = len(results)
        failed = tuple(
            r.candidate for r in results if r.status == "failed" and r.reason in NETWORK_FAILURES
        )
        first = len(failed)
        result = replace(result, probes=results, checks=checks, journal_phase=state.journal.phase)
        if not mass_failure(first, count):
            return replace(
                result,
                status="no_change",
                counts=(
                    ("failover_owned", count),
                    ("failover_failed_initial", first),
                    ("mutations", 0),
                ),
            )
        if any(r.status == "unknown" for r in results):
            raise SafetyBlocked("Incomplete current-route observations; emergency failover stopped")
        if used + len(failed) > config.probe.max_pairs:
            raise SafetyBlocked("Emergency confirmation exceeds probe budget")
        sleep(5.0)
        repeated = fresh_probes(failed)
        used += len(repeated)
        failed = tuple(
            r.candidate for r in repeated if r.status == "failed" and r.reason in NETWORK_FAILURES
        )
        confirmed = len(failed)
        result = replace(result, probes=result.probes + repeated)
        if not mass_failure(confirmed, count):
            return replace(
                result,
                status="no_change",
                counts=(
                    ("failover_owned", count),
                    ("failover_failed_initial", first),
                    ("failover_failed_confirmed", confirmed),
                    ("mutations", 0),
                ),
            )
        if confirmed > MAX_REPLACEMENTS or any(r.status == "unknown" for r in repeated):
            raise SafetyBlocked("Emergency failure count or observation exceeds supported limits")
        if deps.store.load(deps.profile_id) != state or not snapshot_equal(
            deps.dns.list_records(), actual
        ):
            raise SafetyBlocked("State or DNS changed during confirmation")
        snapshots = []
        previous = {s.provider_id: s for s in state.last_sources}
        for provider in config.providers:
            try:
                fetched_at = deps.clock()
                fetched = deps.relays[provider.id].fetch(provider, fetched_at)
                snapshot = validate_snapshot(
                    fetched, previous.get(provider.id), config.catalog, fetched_at
                )
                if any(w.startswith("stale:") for w in snapshot.warnings):
                    continue
                snapshots.append(snapshot)
            except Exception:
                continue
        pools = candidate_pools(tuple(snapshots), config.catalog)
        options = tuple(c for old in failed for c in pools.get(old.domain, ()) if c.ip != old.ip)
        # Take at most two fresh backup candidates for each failed domain.
        options = tuple(
            c
            for old in failed
            for c in tuple(c for c in options if c.domain == old.domain)[
                : config.probe.max_candidates_per_domain
            ]
        )
        if used + len(options) > config.probe.max_pairs:
            raise SafetyBlocked("Emergency backup search exceeds probe budget")
        backups = fresh_probes(options)
        used += len(backups)
        chosen: dict[str, RelayCandidate] = {}
        for probe in backups:
            if probe.status == "tls_ok":
                chosen.setdefault(probe.candidate.domain, probe.candidate)
        result = replace(result, probes=result.probes + backups, sources=tuple(snapshots))
        if set(chosen) != {c.domain for c in failed}:
            raise SafetyBlocked("Every confirmed failed route requires a fresh verified backup")
        if used + len(chosen) > config.probe.max_pairs:
            raise SafetyBlocked("Emergency backup confirmation exceeds probe budget")
        repeated_backups = fresh_probes(tuple(chosen[d] for d in sorted(chosen)))
        result = replace(result, probes=result.probes + repeated_backups)
        if not all(r.status == "tls_ok" for r in repeated_backups):
            raise SafetyBlocked("Backup confirmation failed; emergency failover stopped")
        desired = tuple(
            DesiredRewrite(r.domain, chosen[r.domain].ip if r.domain in chosen else r.content)
            for r in owned
        )
        policy = PlanContext(
            digest,
            deps.clock(),
            config.sync.max_records,
            MAX_REPLACEMENTS,
            1.0,
            False,
            False,
            deps.subtree_capabilities,
            config.catalog.services,
        )
        plan = build_plan(desired, actual, state, frozenset(), policy)
        if (
            plan.blocked_reasons
            or len(plan.operations) != confirmed
            or any(op.kind != "replace" or op.domain not in chosen for op in plan.operations)
        ):
            raise SafetyBlocked("Emergency plan exceeded its exact replacement scope")
        result = replace(
            result,
            plan=plan,
            counts=(
                ("failover_owned", count),
                ("failover_failed_initial", first),
                ("failover_failed_confirmed", confirmed),
                ("operations", confirmed),
            ),
        )
        if not apply:
            return replace(result, status="preview")
        if deps.preflight_writes is not None:
            deps.preflight_writes(confirmed)
        target_selections = tuple(
            Selection(r.domain, chosen[r.domain], selections[r.domain].routing_policy_digest)
            if r.domain in chosen
            else selections[r.domain]
            for r in owned
        )
        written = True
        applied = apply_plan(
            plan, deps.dns, deps.store, state, replace(context, selections=target_selections)
        )
        latest = deps.store.load(deps.profile_id)
        return replace(
            applied,
            plan=plan,
            probes=result.probes,
            sources=result.sources,
            counts=applied.counts + result.counts,
            journal_phase=latest.journal.phase if latest and latest.journal else None,
            journal_run_id=latest.journal.run_id if latest and latest.journal else None,
            dns_provider=config.dns.provider,
            dns_readiness="production-ready",
        )
    except SafetyBlocked as error:
        return replace(
            result,
            status="partial" if written else "blocked",
            errors=(str(error),),
            counts=result.counts
            or (
                ("failover_owned", count),
                ("failover_failed_initial", first),
                ("failover_failed_confirmed", confirmed),
                ("mutations", 0),
            ),
            issue_codes=("recovery_required" if written else "safety_blocked",),
        )
    except Exception:
        return replace(
            result,
            status="partial" if written else "blocked",
            errors=("Emergency inspection or execution failed; inspect the encrypted journal",),
            issue_codes=("recovery_required" if written else "controller_error",),
        )
