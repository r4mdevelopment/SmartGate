from dataclasses import replace

import pytest

from smartgate.errors import SafetyBlocked, StateConflict
from smartgate.state.codec import encode_state
from smartgate.state.upgrade import upgrade_legacy_state
from tests.contract.test_encrypted_git_state import GitBackend, store
from tests.integration.test_app import setup
from tests.integration.test_state_upgrade import target


def fixture(phase="verified"):
    from smartgate.app import run

    config, deps = setup()
    assert run(config, "sync", True, deps).status == "applied"
    if phase == "rolled_back":
        assert (
            run(
                config,
                "rollback",
                True,
                replace(deps, rollback_run_id=deps.store.state.journal.run_id),
            ).status
            == "applied"
        )
    state = upgrade_legacy_state(deps.store.state, target())
    if phase == "pending":
        state = replace(state, journal=replace(state.journal, phase="applying"))
    backend = GitBackend()
    backend.head = "a" * 40
    backend.files = {"state/primary.json": encode_state(state)}
    return backend, deps.dns


def migrate(backend, dns, apply):
    from smartgate.state.encryption_migration import migrate_state_encryption

    return migrate_state_encryption(store(backend), target(), dns, apply=apply)


@pytest.mark.parametrize("phase", ["verified", "rolled_back"])
def test_migration_preview_and_apply_preserve_entire_state_and_dns(phase):
    backend, dns = fixture(phase)
    before = backend.files["state/primary.json"]
    records = tuple(dns.records)
    writes = dns.writes
    migrate(backend, dns, False)
    assert backend.files["state/primary.json"] == before
    assert all(method == "GET" for method, _, _ in backend.calls)
    old = store(backend).inspect_plaintext_for_encryption("primary")
    migrate(backend, dns, True)
    new = store(backend).load("primary")
    assert replace(new, revision=old.revision) == old
    assert tuple(dns.records) == records and dns.writes == writes
    assert b"chatgpt.com" not in backend.files["state/primary.json"]
    assert new.journal.plan.plan_id == old.journal.plan.plan_id


@pytest.mark.parametrize("reason", ["pending", "ownership", "target", "conflict"])
def test_invalid_migration_stops_before_ref_or_dns_writes(reason):
    backend, dns = fixture("pending" if reason == "pending" else "verified")
    before = backend.files["state/primary.json"]
    writes = dns.writes
    if reason == "ownership":
        dns.records[0] = replace(dns.records[0], content="1.1.1.1")
    if reason == "conflict":
        backend.conflict = True
    with pytest.raises((SafetyBlocked, StateConflict)):
        if reason == "target":
            from smartgate.state.encryption_migration import migrate_state_encryption

            migrate_state_encryption(
                store(backend), replace(target(), namespace="other"), dns, apply=True
            )
        else:
            migrate(backend, dns, True)
    assert backend.files["state/primary.json"] == before and dns.writes == writes


def test_lost_migration_ack_is_confirmed_by_exact_fresh_readback():
    backend, dns = fixture()
    backend.lose_ack = True
    writes = dns.writes
    migrate(backend, dns, True)
    assert store(backend).load("primary").journal.phase == "verified"
    assert dns.writes == writes
    assert len([1 for method, path, _ in backend.calls if method == "PATCH"]) == 1


def test_unknown_migration_result_is_partial_and_not_retried(monkeypatch):
    from smartgate.errors import RecoveryRequired

    backend, dns = fixture()
    original = backend.request

    def request(method, path, payload=None):
        if method == "PATCH":
            raise SafetyBlocked("lost before acknowledgement")
        return original(method, path, payload)

    monkeypatch.setattr(backend, "request", request)
    with pytest.raises(RecoveryRequired):
        migrate(backend, dns, True)
    assert b"schema_version" in backend.files["state/primary.json"]


def test_cli_migration_is_entire_command_without_routing(monkeypatch, tmp_path):
    import smartgate.cli as cli

    config, deps = setup()
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(
        cli,
        "live_dependencies",
        lambda *a, **kw: replace(
            deps, bootstrap=False, encrypt_state=lambda apply: calls.append(apply)
        ),
    )
    assert (
        cli.main(["state-encrypt", "--config", "unused", "--apply", "--report-dir", str(tmp_path)])
        == 0
    )
    assert calls == [True] and deps.dns.writes == 0 and deps.store.saves == 0


@pytest.mark.parametrize(
    "extra",
    [
        ["--offline"],
        ["--bootstrap"],
        ["--run-id", "marker"],
        ["--previous-run-stopped"],
        ["--require-no-change"],
    ],
)
def test_cli_invalid_migration_flags_stop_before_clients(monkeypatch, extra):
    import smartgate.cli as cli

    config, _ = setup()
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(
        cli, "live_dependencies", lambda *a, **kw: pytest.fail("clients constructed")
    )
    assert cli.main(["state-encrypt", "--config", "unused", "--apply", *extra]) == 4
