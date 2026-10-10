from dataclasses import replace
from datetime import timedelta

import pytest

from smartgate.app import run
from smartgate.report import exit_code
from tests.integration.test_app import Relay, setup
from tests.reconcile_fakes import NOW


def initialized():
    config, deps = setup()
    assert run(config, "sync", True, deps).status == "applied"
    config = replace(config, sync=replace(config.sync, max_change_ratio=1.0))
    return config, replace(deps, bootstrap=False)


def guarded(deps):
    return replace(deps, require_no_change=True)


def test_no_change_guard_blocks_new_plan_after_zero_preview():
    config, deps = initialized()
    assert not run(config, "preview", False, deps).plan.operations

    class ChangedRelay(Relay):
        def fetch(self, provider, now):
            snapshot = super().fetch(provider, now)
            candidate = replace(snapshot.candidates[0], ip="1.1.1.1")
            return replace(snapshot, candidates=(candidate,))

    deps = guarded(replace(deps, relays={p.id: ChangedRelay() for p in config.providers}))
    writes, saves = deps.dns.writes, deps.store.saves
    preflights = []
    deps = replace(deps, preflight_writes=preflights.append)
    report = run(config, "sync", True, deps)
    assert report.status == "blocked"
    assert report.plan.operations
    assert "no-change" in report.errors[0].lower()
    assert (deps.dns.writes, deps.store.saves) == (writes, saves)
    assert not preflights


def test_no_change_guard_never_recovers_pending_journal():
    config, deps = setup()
    deps.dns.mode = "timeout_create"
    assert run(config, "sync", True, deps).status == "partial"
    writes, saves = deps.dns.writes, deps.store.saves
    deps = guarded(
        replace(deps, bootstrap=False, clock=lambda: NOW + timedelta(minutes=21), relays={})
    )
    report = run(config, "sync", True, deps)
    assert report.status == "blocked"
    assert "journal" in report.errors[0].lower()
    assert (deps.dns.writes, deps.store.saves) == (writes, saves)


def test_no_change_guard_verifies_zero_plan_without_dns_writes():
    config, deps = initialized()
    writes = deps.dns.writes
    report = run(config, "sync", True, guarded(deps))
    assert report.status == "no_change"
    assert not report.plan.operations
    assert report.checks and all(c.ok for c in report.checks)
    assert deps.dns.writes == writes


@pytest.mark.parametrize(
    "mode,apply,bootstrap",
    [
        ("preview", False, False),
        ("rollback", True, False),
        ("recover", True, False),
        ("state-upgrade", True, False),
        ("sync", False, False),
        ("sync", True, True),
    ],
)
def test_no_change_guard_rejects_incompatible_modes_before_any_write(mode, apply, bootstrap):
    config, deps = initialized()
    deps = guarded(replace(deps, bootstrap=bootstrap))
    writes, saves = deps.dns.writes, deps.store.saves
    report = run(config, mode, apply, deps)
    assert exit_code(report) == 4
    assert "no-change" in report.errors[0].lower()
    assert (deps.dns.writes, deps.store.saves) == (writes, saves)


@pytest.mark.parametrize("kind", ["create", "delete"])
def test_no_change_guard_blocks_other_operation_kinds(kind):
    from smartgate.catalog import ServiceCatalog

    config, deps = initialized()
    if kind == "create":

        class ExtraRelay(Relay):
            def fetch(self, provider, now):
                snapshot = super().fetch(provider, now)
                candidate = replace(snapshot.candidates[0], domain="extra.chatgpt.com")
                return replace(snapshot, candidates=snapshot.candidates + (candidate,))

        deps = replace(deps, relays={p.id: ExtraRelay() for p in config.providers})
    else:
        config = replace(
            config,
            catalog=ServiceCatalog(
                tuple(replace(s, enabled=False) for s in config.catalog.services)
            ),
            sync=replace(config.sync, allow_prune=True),
        )
    writes, saves = deps.dns.writes, deps.store.saves
    report = run(config, "sync", True, guarded(deps))
    assert report.status == "blocked"
    assert any(op.kind == kind for op in report.plan.operations)
    assert "no-change" in report.errors[0].lower()
    assert (deps.dns.writes, deps.store.saves) == (writes, saves)


def test_no_change_guard_keeps_failed_dns_verification_unsuccessful():
    config, deps = initialized()
    verifier = deps.verifier
    deps = replace(
        deps, verifier=lambda *args: tuple(replace(c, ok=False) for c in verifier(*args))
    )
    writes = deps.dns.writes
    report = run(config, "sync", True, guarded(deps))
    assert report.status == "degraded"
    assert report.checks and not any(c.ok for c in report.checks)
    assert deps.dns.writes == writes
