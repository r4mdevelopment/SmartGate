from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from smartgate.app import AppDependencies, run
from smartgate.catalog import ServiceCatalog
from smartgate.config import load_config
from smartgate.errors import DnsApiError, SourceError
from smartgate.models import ProbeResult, RelaySnapshot, RunReport
from smartgate.report import exit_code, render_report
from tests.reconcile_fakes import NOW, FakeDns, MemoryStore, target, verify_ok


class Relay:
    def __init__(self, fail=False):
        self.fail = fail

    def fetch(self, config, now):
        if self.fail:
            raise SourceError("unavailable")
        candidate = replace(
            target("chatgpt.com", "8.8.8.8", config.id).candidate, source_id="fixture"
        )
        return RelaySnapshot(config.id, now, "digest", (candidate,), ("source warning",))


class Checker:
    def __init__(self, status="tls_ok"):
        self.status = status

    def probe(self, candidate, policy):
        return ProbeResult(candidate, self.status, NOW, "local", 1.0, "fixture probe")


def setup(enabled=True, bootstrap=True):
    config = load_config(Path("config/smartgate.example.toml"), {})
    config = replace(config, sync=replace(config.sync, enabled=enabled))
    dns, store = FakeDns(), MemoryStore()
    deps = AppDependencies(
        {p.id: Relay() for p in config.providers},
        Checker(),
        dns,
        store,
        verify_ok,
        lambda: NOW,
        "abcdef",
        bootstrap=bootstrap,
        subtree_capabilities=True,
    )
    return config, deps


def test_preview_apply_repeat_rollback():
    config, deps = setup()
    preview = run(config, "preview", False, deps)
    assert preview.status == "preview" and len(preview.plan.operations) == 1
    assert deps.dns.writes == deps.store.saves == 0
    applied = run(config, "sync", True, deps)
    assert applied.status == "applied"
    assert deps.store.state.last_sources
    assert run(config, "sync", True, replace(deps, bootstrap=False)).status == "no_change"
    journal_id = deps.store.state.journal.run_id
    rolled = run(config, "rollback", True, replace(deps, rollback_run_id=journal_id))
    assert rolled.status == "applied" and not deps.dns.records


@pytest.mark.parametrize("mode", ["preview", "sync", "doctor"])
def test_reads_never_save_or_mutate(mode):
    config, deps = setup()
    run(config, mode, False, deps)
    assert deps.dns.writes == deps.store.saves == 0


def test_disabled_apply_is_blocked_before_reads():
    config, deps = setup(enabled=False)
    deps.dns.list_records = lambda: pytest.fail("must block before DNS")
    assert exit_code(run(config, "sync", True, deps)) == 4
    assert deps.store.saves == 0


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_required_failure_is_degraded_and_no_bootstrap_write(status):
    config, deps = setup()
    deps = replace(deps, checker=Checker(status))
    report = run(config, "sync", True, deps)
    assert exit_code(report) == 3
    assert deps.dns.writes == 0


def test_external_error_safe_exit5():
    config, deps = setup()

    def fail():
        raise DnsApiError("secret-body-token")

    deps.dns.list_records = fail
    report = run(config, "preview", False, deps)
    assert exit_code(report) == 5
    assert "secret-body-token" not in str(render_report(report))


def test_pending_preview_readonly_and_sync_recovers_before_fetch():
    config, deps = setup()
    deps.dns.mode = "timeout_create"
    assert exit_code(run(config, "sync", True, deps)) == 6
    count = deps.store.saves
    assert exit_code(run(config, "preview", False, deps)) == 6
    assert deps.store.saves == count
    deps = replace(
        deps,
        clock=lambda: NOW + timedelta(minutes=21),
        verifier=lambda profile, desired: tuple(
            replace(c, checked_at=NOW + timedelta(minutes=21)) for c in verify_ok(profile, desired)
        ),
        relays={},
    )
    assert run(config, "sync", True, deps).status == "no_change"
    assert deps.dns.writes == 1


def test_active_lease_blocks_new_recovery():
    config, deps = setup()
    deps.dns.mode = "timeout_create"
    run(config, "sync", True, deps)
    assert exit_code(run(config, "recover", True, deps)) == 4
    assert deps.dns.writes == 1


def test_rollback_requires_exact_latest_run():
    config, deps = setup()
    run(config, "sync", True, deps)
    writes = deps.dns.writes
    assert exit_code(run(config, "rollback", True, replace(deps, rollback_run_id="wrong"))) == 4
    assert deps.dns.writes == writes


def test_source_failure_retains_but_does_not_return_success():
    config, deps = setup()
    run(config, "sync", True, deps)
    deps = replace(deps, relays={p.id: Relay(True) for p in config.providers}, bootstrap=False)
    report = run(config, "sync", True, deps)
    assert report.status == "degraded" and deps.dns.writes == 1
    assert any("stale:" in warning for s in report.sources for warning in s.warnings)


def test_provider_switch_preserves_dns_port():
    config, deps = setup()
    run(config, "sync", True, deps)
    service = config.catalog.services[0]
    own = replace(config.providers[0], id="own", type="selfhosted", region=None)
    config = replace(
        config,
        providers=(own,),
        catalog=ServiceCatalog((replace(service, provider_order=("own",)),)),
    )
    deps = replace(deps, relays={"own": Relay()}, bootstrap=False)
    assert run(config, "sync", True, deps).status == "no_change"
    assert deps.store.state.managed_records[0].provider_id == "own"
    assert deps.dns.writes == 1


@pytest.mark.parametrize(
    "status,expected",
    [
        ("preview", 0),
        ("no_change", 0),
        ("applied", 0),
        ("degraded", 3),
        ("blocked", 4),
        ("partial", 6),
    ],
)
def test_exit_status(status, expected):
    assert exit_code(RunReport("test", status, (), (), (), ())) == expected


def test_reports_are_reviewable_without_profile_or_record_ids():
    config, deps = setup()
    report = run(config, "preview", False, deps)
    data, markdown = render_report(report)
    assert "abcdef" not in data + markdown
    assert "record_id" not in data
    assert "chatgpt.com" in data and "source warning" in data
    assert "github_runner" not in data
    assert "create" in markdown and "8.8.8.8" in markdown
    assert "fetched_at" in data and "vantage" in data


def test_explicit_bootstrap_persists_probe_cursor_without_dns_when_all_fail():
    config, deps = setup()
    candidate = target("chatgpt.com", "8.8.8.8", config.providers[0].id).candidate

    class ManyRelay:
        def fetch(self, provider, now):
            return RelaySnapshot(
                provider.id,
                now,
                "digest",
                tuple(
                    replace(candidate, provider_id=provider.id, ip=ip)
                    for ip in ("8.8.8.8", "1.1.1.1", "8.8.4.4")
                ),
                (),
            )

    deps = replace(
        deps, checker=Checker("failed"), relays={p.id: ManyRelay() for p in config.providers}
    )
    assert run(config, "sync", True, deps).status == "degraded"
    assert deps.dns.writes == 0
    assert deps.store.state.probe_cursors[0].index > 0


def test_last_state_read_failure_after_apply_requires_recovery_exit6():
    config, deps = setup()
    original = deps.store.load

    def load(profile):
        if deps.dns.writes and deps.store.saves >= 4:
            raise RuntimeError("unavailable")
        return original(profile)

    deps.store.load = load
    assert exit_code(run(config, "sync", True, deps)) == 6


def test_redaction_preserves_json_with_quoted_secret():
    secret = 'a"private\nvalue'
    report = RunReport("run", "blocked", (), (), (), (secret,))
    data, markdown = render_report(report, (secret,))
    import json

    assert json.loads(data)["errors"] == ["[redacted]"]
    assert secret not in markdown


def test_missing_git_state_requires_explicit_bootstrap_and_empty_dns():
    config, deps = setup(bootstrap=False)
    calls = []
    deps = replace(deps, bootstrap_state=lambda state: calls.append(state) or "revision")
    assert exit_code(run(config, "sync", True, deps)) == 4
    assert not calls and deps.dns.writes == 0


def test_git_bootstrap_precedes_intent_and_dns_write():
    config, deps = setup()
    events = deps.dns.events

    def bootstrap(state):
        events.append("bootstrap")
        return deps.store.save(state, None)

    deps = replace(deps, bootstrap_state=bootstrap)
    assert run(config, "sync", True, deps).status == "applied"
    assert events.index("bootstrap") < events.index("dns:create")
    assert deps.store.state.journal.before_snapshot == ()


def test_expired_source_does_not_select_or_drop_ownership():
    config, deps = setup()
    run(config, "sync", True, deps)
    later = NOW + timedelta(hours=25)
    deps = replace(
        deps,
        clock=lambda: later,
        verifier=lambda p, d: tuple(replace(c, checked_at=later) for c in verify_ok(p, d)),
        relays={p.id: Relay(True) for p in config.providers},
        bootstrap=False,
    )
    result = run(config, "sync", True, deps)
    assert result.status == "degraded" and deps.dns.writes == 1
    assert deps.store.state.managed_records
    assert not result.sources


def test_failed_batch_advances_in_existing_ledger():
    config, deps = setup()
    run(config, "sync", True, deps)
    deps = replace(deps, checker=Checker("failed"), bootstrap=False)
    result = run(config, "sync", True, deps)
    assert result.status == "degraded" and deps.dns.writes == 1
    assert deps.store.state.probe_cursors


def test_expired_last_good_snapshot_preserved_for_quality_baseline():
    config, deps = setup()
    run(config, "sync", True, deps)
    last_good = deps.store.state.last_sources
    later = NOW + timedelta(hours=25)
    deps = replace(
        deps,
        clock=lambda: later,
        verifier=lambda p, d: tuple(replace(c, checked_at=later) for c in verify_ok(p, d)),
        relays={p.id: Relay(True) for p in config.providers},
        bootstrap=False,
    )
    assert run(config, "sync", True, deps).status == "degraded"
    assert deps.store.state.last_sources == last_good


def test_app_roundtrip_with_durable_local_store(tmp_path):
    from smartgate.state.local import LocalStateStore

    config, deps = setup()
    path = tmp_path / "state.json"
    deps = replace(deps, store=LocalStateStore(path))
    assert run(config, "sync", True, deps).status == "applied"
    # A new store instance proves journal/cache are decoded from disk, not memory.
    deps = replace(deps, store=LocalStateStore(path), bootstrap=False)
    assert run(config, "sync", True, deps).status == "no_change"
    state = deps.store.load("abcdef")
    assert state.last_sources and state.selections and state.journal.phase == "verified"
    assert (
        run(config, "rollback", True, replace(deps, rollback_run_id=state.journal.run_id)).status
        == "applied"
    )
    assert not deps.store.load("abcdef").managed_records
    assert not deps.dns.records


def test_short_credential_cannot_corrupt_report_json_types():
    import json

    report = RunReport("run", "blocked", (("mutations", 2),), (), (), ("true 2",))
    encoded, markdown = render_report(report, ("true", "2"))
    data = json.loads(encoded)
    assert data["offline"] is False and data["counts"]["mutations"] == 2
    assert data["errors"] == ["[redacted] [redacted]"]


def test_first_apply_cannot_initialize_observations_without_bootstrap():
    config, deps = setup(bootstrap=False)
    config = replace(
        config,
        catalog=replace(
            config.catalog, services=(replace(config.catalog.services[0], required_domains=()),)
        ),
    )
    deps = replace(deps, checker=Checker("failed"))
    from smartgate.models import DnsRecord

    manual = DnsRecord("manual", "unrelated.example", "9.9.9.9")
    deps.dns.records = [manual]
    result = run(config, "sync", True, deps)
    assert result.status == "blocked"
    assert deps.store.saves == deps.dns.writes == 0
    assert deps.store.state is None and deps.dns.records == [manual]


def test_unverified_dns_adapter_cannot_apply_even_with_explicit_flag():
    config, deps = setup()
    report = run(config, "sync", True, replace(deps, production_apply=False))
    assert report.status == "blocked"
    assert deps.dns.writes == deps.store.saves == 0


def test_different_api_binding_blocks_before_any_dns_or_state_write():
    from smartgate.dns.types import DnsTarget
    from smartgate.state.journal import initial_state
    from smartgate.state.upgrade import upgrade_legacy_state

    config, deps = setup()
    original = DnsTarget(
        "primary", "nextdns", "https://api.nextdns.io", "abcdef", "https://dns.nextdns.io/abcdef"
    )
    deps.store.state = upgrade_legacy_state(initial_state("abcdef"), original)
    changed = replace(original, namespace="123456", doh_url="https://dns.nextdns.io/123456")
    report = run(config, "sync", True, replace(deps, profile_id="primary", dns_target=changed))
    assert report.status == "blocked"
    assert deps.dns.writes == deps.store.saves == 0


def test_public_readiness_report_omits_destination_and_credentials():
    import json

    from smartgate.dns.types import DnsTarget

    config, deps = setup()
    destination = DnsTarget(
        "home",
        "adguardhome",
        "https://private-api.example",
        "private-scope",
        "https://private-dns.example/query",
    )
    result = run(
        config,
        "preview",
        False,
        replace(deps, profile_id="home", dns_target=destination, production_apply=False),
    )
    encoded, markdown = render_report(result)
    data = json.loads(encoded)
    assert data["dns_provider"] == "adguardhome"
    assert data["dns_readiness"] == "reference-read-only"
    assert "private-api" not in encoded + markdown
    assert "private-scope" not in encoded + markdown
    assert "private-dns" not in encoded + markdown


def optional_route_setup(*, source_failed=False, dns_failed=False, healthy_optional=6):
    config, deps = setup()
    service = config.catalog.services[0]
    config = replace(
        config,
        catalog=ServiceCatalog(
            (replace(service, include_suffixes=service.include_suffixes + ("optional.example",)),)
        ),
    )

    class MixedRelay(Relay):
        def fetch(self, provider, now):
            snapshot = super().fetch(provider, now)
            optional = replace(snapshot.candidates[0], domain="optional.example")
            good = tuple(
                replace(optional, domain=f"good{i}.optional.example")
                for i in range(healthy_optional)
            )
            return replace(snapshot, candidates=snapshot.candidates + good + (optional,))

    class MixedChecker(Checker):
        def probe(self, candidate, policy):
            result = super().probe(candidate, policy)
            return (
                replace(result, status="failed")
                if candidate.domain == "optional.example"
                else result
            )

    deps = replace(
        deps, relays={p.id: MixedRelay() for p in config.providers}, checker=MixedChecker()
    )
    if source_failed:
        deps = replace(deps, relays={p.id: Relay(True) for p in config.providers})
    if dns_failed:
        deps = replace(
            deps, verifier=lambda p, d: tuple(replace(c, ok=False) for c in verify_ok(p, d))
        )
    return config, deps


def optional_route_report(*, apply=True, **values):
    config, deps = optional_route_setup(**values)
    return run(config, "sync", apply, deps)


def test_optional_route_warning_is_green_only_after_verified_sync():
    import json

    from smartgate.report import workflow_exit_code

    report = optional_route_report()
    assert report.status == "degraded" and exit_code(report) == 3
    assert workflow_exit_code(report) == 0
    data, markdown = render_report(report)
    assert json.loads(data)["workflow_exit_code"] == 0
    assert "verified with optional route warnings" in markdown


@pytest.mark.parametrize(
    "values", [{"apply": False}, {"source_failed": True}, {"dns_failed": True}]
)
def test_unverified_or_failed_execution_remains_red(values):
    from smartgate.report import workflow_exit_code

    assert workflow_exit_code(optional_route_report(**values)) != 0


def test_missing_required_route_remains_red():
    from smartgate.report import workflow_exit_code

    config, deps = setup()
    result = run(config, "sync", True, replace(deps, checker=Checker("failed")))
    assert workflow_exit_code(result) == 3


def test_verified_sync_below_85_percent_keeps_warning_failure():
    import json

    from smartgate.report import workflow_exit_code

    report = optional_route_report(healthy_optional=0)
    assert report.status == "degraded" and report.journal_phase == "verified"
    assert all(c.ok for c in report.checks)
    assert workflow_exit_code(report) == 3
    data, markdown = render_report(report)
    assert json.loads(data)["availability"][0]["available"] == 1
    assert json.loads(data)["availability"][0]["total"] == 2
    assert "50.0%" in markdown and "85%" in markdown


def test_cached_optional_domains_below_threshold_cannot_be_hidden_by_fresh_source():
    from smartgate.report import workflow_exit_code

    config, deps = setup()
    service = config.catalog.services[0]
    config = replace(
        config,
        catalog=ServiceCatalog(
            (replace(service, include_suffixes=service.include_suffixes + ("missing.example",)),)
        ),
    )
    source = deps.relays[config.providers[0].id].fetch(config.providers[0], NOW)
    cached = replace(
        source,
        candidates=source.candidates + (replace(source.candidates[0], domain="missing.example"),),
    )
    from smartgate.state.journal import initial_state

    deps.store.state = replace(initial_state("abcdef"), last_sources=(cached,))
    result = run(config, "sync", True, deps)
    assert result.status == "degraded" and result.journal_phase == "verified"
    assert result.availability[0].available == 1 and result.availability[0].total == 2
    assert workflow_exit_code(result) == 3


def test_enabled_service_without_known_domains_cannot_be_green():
    from smartgate.report import workflow_exit_code

    config, deps = setup()
    unknown = replace(
        config.catalog.services[0],
        id="unknown",
        include_suffixes=("unknown.example",),
        required_domains=(),
    )
    config = replace(config, catalog=ServiceCatalog(config.catalog.services + (unknown,)))
    result = run(config, "sync", True, deps)
    assert result.availability[-1].total == 0
    assert workflow_exit_code(result) == 3


def test_verified_noop_after_rollback_can_be_green_with_optional_warnings():
    from smartgate.report import workflow_exit_code

    report = optional_route_report()
    report = replace(
        report,
        journal_phase="rolled_back",
        counts=(("mutations", 0),),
        plan=replace(report.plan, operations=()),
    )
    assert exit_code(report) == 3
    assert workflow_exit_code(report) == 0


@pytest.mark.parametrize(
    "condition",
    [
        "mutations",
        "missing_count",
        "operations",
        "dns_failure",
        "low_coverage",
        "required_failure",
        "pending",
        "partial",
        "no_journal",
    ],
)
def test_rollback_noop_status_never_hides_unverified_or_changed_execution(condition):
    from smartgate.report import workflow_exit_code

    original = optional_route_report()
    report = replace(
        original,
        journal_phase="rolled_back",
        counts=(("mutations", 0),),
        plan=replace(original.plan, operations=()),
    )
    if condition == "mutations":
        report = replace(report, counts=(("mutations", 1),))
    elif condition == "missing_count":
        report = replace(report, counts=())
    elif condition == "operations":
        report = replace(report, plan=original.plan)
    elif condition == "dns_failure":
        report = replace(report, checks=(replace(report.checks[0], ok=False),) + report.checks[1:])
    elif condition == "low_coverage":
        report = replace(report, availability=(replace(report.availability[0], available=1),))
    elif condition == "required_failure":
        report = replace(report, availability=(replace(report.availability[0], required_ok=False),))
    elif condition in {"pending", "partial"}:
        report = replace(report, journal_phase=condition)
    else:
        report = replace(report, journal_phase=None)
    assert workflow_exit_code(report) == 3


@pytest.mark.parametrize("phase", ["verified", "rolled_back"])
def test_readonly_doctor_verifies_optional_warnings_without_any_writes(phase):
    from smartgate.report import workflow_exit_code

    config, deps = optional_route_setup()
    assert run(config, "sync", True, deps).journal_phase == "verified"
    deps.store.state = replace(
        deps.store.state, journal=replace(deps.store.state.journal, phase=phase)
    )
    before = (deps.store.state, tuple(deps.dns.records), deps.store.saves, deps.dns.writes)
    report = run(config, "doctor", False, replace(deps, bootstrap=False))
    assert report.status == "degraded" and workflow_exit_code(report) == 0
    assert report.journal_phase == phase and dict(report.counts)["mutations"] == 0
    assert len(report.checks) == 28 and all(c.ok for c in report.checks)
    assert before == (deps.store.state, tuple(deps.dns.records), deps.store.saves, deps.dns.writes)


@pytest.mark.parametrize("drift", ["dns", "state"])
def test_readonly_doctor_blocks_snapshot_changes_during_verification(drift):
    from smartgate.report import workflow_exit_code

    config, deps = optional_route_setup()
    run(config, "sync", True, deps)

    def verify(profile, desired):
        checks = verify_ok(profile, desired)
        if drift == "dns":
            deps.dns.records[0] = replace(deps.dns.records[0], content="9.9.9.9")
        else:
            deps.store.state = replace(deps.store.state, revision="concurrent")
        return checks

    report = run(config, "doctor", False, replace(deps, verifier=verify, bootstrap=False))
    assert report.status == "blocked" and workflow_exit_code(report) != 0


@pytest.mark.parametrize(
    "condition",
    [
        "dns",
        "empty_checks",
        "offline",
        "low_coverage",
        "source",
        "pending",
        "no_journal",
        "planned_change",
        "required",
    ],
)
def test_readonly_doctor_never_hides_unverified_conditions(condition):
    from smartgate.report import workflow_exit_code

    config, deps = optional_route_setup(healthy_optional=0 if condition == "low_coverage" else 6)
    run(config, "sync", True, deps)
    deps = replace(deps, bootstrap=False)
    if condition == "dns":
        deps = replace(
            deps, verifier=lambda p, d: tuple(replace(c, ok=False) for c in verify_ok(p, d))
        )
    elif condition == "empty_checks":
        deps = replace(deps, verifier=lambda p, d: ())
    elif condition == "offline":
        deps = replace(deps, offline=True)
    elif condition == "source":
        deps = replace(deps, relays={p.id: Relay(True) for p in config.providers})
    elif condition == "pending":
        deps.store.state = replace(
            deps.store.state, journal=replace(deps.store.state.journal, phase="prepared")
        )
    elif condition == "no_journal":
        deps.store.state = replace(deps.store.state, journal=None)
    elif condition == "planned_change":
        deps.dns.records[0] = replace(deps.dns.records[0], content="9.9.9.9")
    elif condition == "required":
        deps = replace(deps, checker=Checker("failed"))
    before = (deps.store.saves, deps.dns.writes)
    report = run(config, "doctor", False, deps)
    assert workflow_exit_code(report) != 0
    assert before == (deps.store.saves, deps.dns.writes)


@pytest.mark.parametrize("condition", ["empty_checks", "no_journal", "planned_change"])
def test_healthy_routes_do_not_hide_unconfirmed_readonly_dns(condition):
    from smartgate.report import workflow_exit_code

    config, deps = setup()
    run(config, "sync", True, deps)
    if condition == "empty_checks":
        deps = replace(deps, verifier=lambda p, d: ())
    elif condition == "no_journal":
        deps.store.state = replace(deps.store.state, journal=None)
    else:
        deps.dns.records[0] = replace(deps.dns.records[0], content="9.9.9.9")
    assert workflow_exit_code(run(config, "doctor", False, replace(deps, bootstrap=False))) != 0


def test_readonly_doctor_keeps_valid_pending_create_red_without_writing():
    from smartgate.report import workflow_exit_code

    config, deps = optional_route_setup()
    run(config, "sync", True, deps)
    before = (deps.store.state, tuple(deps.dns.records), deps.store.saves, deps.dns.writes)
    report = run(config, "doctor", False, replace(deps, checker=Checker(), bootstrap=False))
    assert report.plan and not report.plan.blocked_reasons
    assert len(report.plan.operations) == 1 and report.plan.operations[0].kind == "create"
    assert report.availability[0].available == report.availability[0].total
    assert report.checks and all(c.ok for c in report.checks)
    assert workflow_exit_code(report) == 3
    assert before == (deps.store.state, tuple(deps.dns.records), deps.store.saves, deps.dns.writes)


def test_healthy_readonly_doctor_confirms_current_dns_without_writing():
    from smartgate.report import workflow_exit_code

    config, deps = setup()
    run(config, "sync", True, deps)
    before = (deps.store.state, tuple(deps.dns.records), deps.store.saves, deps.dns.writes)
    report = run(config, "doctor", False, replace(deps, bootstrap=False))
    assert report.checks and all(c.ok for c in report.checks)
    assert workflow_exit_code(report) == 0
    assert before == (deps.store.state, tuple(deps.dns.records), deps.store.saves, deps.dns.writes)
