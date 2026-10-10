from dataclasses import replace
from datetime import UTC, datetime

import pytest

from smartgate.dns.types import DnsTarget
from smartgate.errors import SafetyBlocked
from smartgate.models import ChangePlan, ManagedRecord, RunJournal
from smartgate.state.journal import initial_state
from smartgate.state.upgrade import upgrade_legacy_state


def target():
    return DnsTarget(
        "primary", "nextdns", "https://api.nextdns.io", "abcdef", "https://dns.nextdns.io/abcdef"
    )


def test_clean_upgrade_preserves_owned_ids_without_dns_operations():
    old = replace(
        initial_state("abcdef"),
        revision="revision",
        managed_records=(
            ManagedRecord(
                "owned-id", "example.com", "8.8.8.8", "relay", datetime(2026, 10, 8, tzinfo=UTC)
            ),
        ),
    )
    new = upgrade_legacy_state(old, target())
    assert new.schema_version == 2 and new.target_id == "primary"
    assert new.managed_records == old.managed_records
    assert new.revision == old.revision


@pytest.mark.parametrize("phase", ["prepared", "applying", "partial"])
def test_pending_legacy_journal_requires_recovery_before_upgrade(phase):
    plan = ChangePlan("plan", "config", "dns", None, datetime(2026, 10, 8, tzinfo=UTC), (), ())
    old = replace(initial_state("abcdef"), journal=RunJournal("run", plan, phase, ()))
    with pytest.raises(SafetyBlocked):
        upgrade_legacy_state(old, target())


def test_wrong_profile_or_provider_cannot_claim_legacy_ownership():
    for altered in (
        replace(target(), namespace="123456"),
        replace(target(), provider="adguardhome"),
    ):
        with pytest.raises(SafetyBlocked):
            upgrade_legacy_state(initial_state("abcdef"), altered)


def test_local_upgrade_is_durable_and_preserves_original_backup(tmp_path):
    from smartgate.state.codec import decode_state
    from smartgate.state.local import LocalStateStore

    store = LocalStateStore(tmp_path / "state.json")
    old = initial_state("abcdef")
    revision = store.save(old, None)
    old = store.load("abcdef")
    upgraded = upgrade_legacy_state(old, target())
    new_revision = store.upgrade(old, upgraded)
    assert store.load("primary").target_fingerprint == upgraded.target_fingerprint
    assert new_revision != revision
    backup = next(tmp_path.glob("*.backup"))
    assert decode_state(backup.read_bytes(), "abcdef", revision) == old


def test_local_upgrade_conflict_cannot_overwrite_changed_state(tmp_path):
    from smartgate.errors import StateConflict
    from smartgate.state.local import LocalStateStore

    store = LocalStateStore(tmp_path / "state.json")
    store.save(initial_state("abcdef"), None)
    old = store.load("abcdef")
    store.save(replace(old, probe_cursors=()), old.revision)
    # Simulate a different writer without pretending a content-identical save changed revision.
    store.path.write_bytes(store.path.read_bytes() + b" ")
    before = store.path.read_bytes()
    with pytest.raises(StateConflict):
        store.upgrade(old, upgrade_legacy_state(old, target()))
    assert store.path.read_bytes() == before


def test_runtime_upgrade_reads_legacy_local_file_before_new_key(tmp_path, monkeypatch):
    from contextlib import ExitStack
    from pathlib import Path

    from smartgate.app import run
    from smartgate.config import load_config
    from smartgate.dns.types import DnsBundle, DnsCapabilities
    from smartgate.runtime import live_dependencies
    from smartgate.state.local import LocalStateStore
    from tests.reconcile_fakes import FakeDns

    config = load_config(Path("config/smartgate.example.toml"), {})
    config = replace(config, state=replace(config.state, path=tmp_path / "state.json"))
    store = LocalStateStore(config.state.path)
    store.save(initial_state("abcdef"), None)
    api = FakeDns()
    bundle = DnsBundle(
        api,
        target(),
        DnsCapabilities("verified", "immutable", "delete_create", True),
        lambda desired: (),
        legacy_state_key="abcdef",
    )
    monkeypatch.setattr("smartgate.runtime.create_dns_bundle", lambda *args: bundle)
    with ExitStack() as stack:
        deps = live_dependencies(
            config, {}, stack, bootstrap=False, rollback_run_id=None, takeover=False
        )
        result = run(config, "state-upgrade", True, deps)
    assert result.status == "no_change"
    assert store.load("primary").schema_version == 2
    assert api.writes == 0


def legacy_verified_fixture():
    from pathlib import Path

    from smartgate.state.codec import decode_state

    return decode_state(
        Path("tests/fixtures/state/legacy-verified.json").read_bytes(), "abcdef", "4"
    )


def test_prebranch_pending_journal_recovers_without_repeating_dns_mutations():
    from smartgate.reconcile.recovery import recover_pending
    from tests.reconcile_fakes import FakeDns, MemoryStore, context

    old = legacy_verified_fixture()
    pending = replace(old, journal=replace(old.journal, phase="partial"))
    dns = FakeDns(tuple(r for r in old.journal.operation_results if r is not None))
    store = MemoryStore(pending)
    result = recover_pending(dns, store, pending, context(old.journal.plan.desired))
    assert result.status == "no_change"
    assert dns.writes == 0 and store.state.journal.phase == "verified"


def test_prebranch_verified_journal_survives_upgrade_and_can_rollback():
    from smartgate.dns.types import target_fingerprint
    from smartgate.reconcile.common import validate_journal
    from smartgate.reconcile.rollback import rollback
    from smartgate.state.upgrade import validate_upgrade_pair
    from tests.reconcile_fakes import FakeDns, MemoryStore, context

    old = legacy_verified_fixture()
    validate_journal(old)
    new = upgrade_legacy_state(old, target())
    validate_upgrade_pair(old, new)
    validate_journal(new)
    dns = FakeDns(tuple(r for r in old.journal.operation_results if r is not None))
    store = MemoryStore(new)
    bound = replace(
        context(old.journal.plan.desired),
        profile_id="primary",
        target_id="primary",
        target_fingerprint=target_fingerprint(target()),
    )
    result = rollback(new.journal, dns, store, new, bound)
    assert result.status == "applied"
    assert dns.records[0].content == "8.8.8.8"
    assert store.state.journal.phase == "rolled_back"


def test_upgraded_plan_tampering_is_rejected_before_store_write():
    from smartgate.state.upgrade import validate_upgrade_pair

    old = legacy_verified_fixture()
    new = upgrade_legacy_state(old, target())
    changed = replace(
        new, journal=replace(new.journal, plan=replace(new.journal.plan, plan_id="f" * 64))
    )
    with pytest.raises(SafetyBlocked):
        validate_upgrade_pair(old, changed)
