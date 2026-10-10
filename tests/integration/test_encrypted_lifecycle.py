from dataclasses import replace
from datetime import timedelta

from smartgate.app import run
from smartgate.private_report import public_report
from smartgate.report import exit_code
from tests.contract.test_encrypted_git_state import GitBackend, store
from tests.integration.test_app import setup
from tests.integration.test_state_encryption_migration import fixture, migrate
from tests.integration.test_state_upgrade import target
from tests.reconcile_fakes import NOW, verify_ok


def dependencies(backend):
    config, deps = setup()
    storage = store(backend)
    return config, replace(deps, store=storage, bootstrap_state=storage.bootstrap)


def test_encrypted_bootstrap_apply_guard_recovery_and_rollback():
    backend = GitBackend()
    config, deps = dependencies(backend)
    deps.dns.mode = "timeout_create"
    interrupted = run(config, "sync", True, deps)
    assert exit_code(interrupted) == 6
    pending = store(backend).load("abcdef")
    assert pending.journal.phase != "verified"
    writes = deps.dns.writes
    guarded = replace(deps, bootstrap=False, require_no_change=True)
    assert run(config, "sync", True, guarded).status == "blocked"
    assert deps.dns.writes == writes

    class LaterChecker:
        def probe(self, candidate, policy):
            return replace(
                deps.checker.probe(candidate, policy), checked_at=NOW + timedelta(minutes=21)
            )

    recovered = replace(
        deps,
        bootstrap=False,
        checker=LaterChecker(),
        clock=lambda: NOW + timedelta(minutes=21),
        verifier=lambda p, d: tuple(
            replace(c, checked_at=NOW + timedelta(minutes=21)) for c in verify_ok(p, d)
        ),
    )
    assert run(config, "recover", True, recovered).status == "no_change"
    assert deps.dns.writes == writes
    noop = run(config, "sync", True, replace(recovered, require_no_change=True))
    assert noop.status == "no_change"
    state = store(backend).load("abcdef")
    rolled = run(config, "rollback", True, replace(recovered, rollback_run_id=state.journal.run_id))
    assert rolled.status == "applied" and not deps.dns.records
    assert store(backend).load("abcdef").journal.phase == "rolled_back"
    for blob in backend.blobs.values():
        assert (
            b"chatgpt.com" not in blob and b"8.8.8.8" not in blob and b"managed_records" not in blob
        )
    assert "chatgpt.com" not in str(public_report(noop))


def test_migrated_verified_journal_can_rollback_with_original_config():
    backend, dns = fixture()
    config, deps = setup()
    old = store(backend).inspect_plaintext_for_encryption("primary")
    migrate(backend, dns, True)
    state = store(backend).load("primary")
    assert state.journal.plan.config_digest == old.journal.plan.config_digest
    result = run(
        config,
        "rollback",
        True,
        replace(
            deps,
            bootstrap=False,
            dns=dns,
            store=store(backend),
            profile_id="primary",
            dns_target=target(),
            rollback_run_id=state.journal.run_id,
        ),
    )
    assert result.status == "applied" and not dns.records
    assert store(backend).load("primary").journal.phase == "rolled_back"
