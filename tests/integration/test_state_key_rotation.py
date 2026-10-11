from dataclasses import replace

import pytest

from smartgate.errors import RecoveryRequired, SafetyBlocked, StateConflict
from smartgate.security.envelope import EnvelopeCipher
from tests.contract.test_encrypted_git_state import KEY, GitResponse, codec, store
from tests.integration.test_state_encryption_migration import fixture, migrate
from tests.integration.test_state_upgrade import target

NEW_KEY = bytes(reversed(range(32)))


def prepared():
    backend, dns = fixture()
    migrate(backend, dns, True)
    original = backend.request

    def request(method, path, payload=None):
        if method == "GET" and "/git/trees/" in path:
            backend.calls.append((method, path, payload))
            return GitResponse(
                200,
                {
                    "truncated": False,
                    "tree": [{"path": name, "type": "blob"} for name in backend.files],
                },
            )
        return original(method, path, payload)

    backend.request = request
    backend.calls.clear()
    return backend, dns


def rotate(backend, dns, backup, apply=True, key=NEW_KEY):
    from smartgate.state.key_rotation import rotate_state_key

    return rotate_state_key(store(backend), target(), dns, EnvelopeCipher(key), backup, apply=apply)


def new_store(backend):
    from smartgate.state.git_branch import GitBranchStateStore

    return GitBranchStateStore(
        "owner/repo",
        "smartgate-state",
        "fixture-token",
        backend,
        codec=codec().with_cipher(EnvelopeCipher(NEW_KEY)),
    )


def test_preview_is_read_only_and_apply_preserves_state_dns_and_backup(tmp_path):
    backend, dns = prepared()
    before = backend.files["state/primary.json"]
    state = store(backend).load("primary")
    records, writes = tuple(dns.records), dns.writes
    backup = tmp_path / "before.enc.json"
    rotate(backend, dns, backup, False)
    assert not backup.exists()
    assert all(method == "GET" for method, _, _ in backend.calls)
    rotate(backend, dns, backup)
    assert backup.read_bytes() == before
    assert codec().decode(backup.read_bytes(), "primary", state.revision) == state
    current = new_store(backend).load("primary")
    assert replace(current, revision=state.revision) == state
    with pytest.raises(SafetyBlocked):
        store(backend).load("primary")
    assert tuple(dns.records) == records and dns.writes == writes
    assert b"chatgpt.com" not in backend.files["state/primary.json"]


@pytest.mark.parametrize(
    "reason", ["pending", "drift", "same_key", "backup_exists", "extra_state", "target"]
)
def test_rotation_guards_do_not_write_remote_state_or_dns(tmp_path, reason):
    backend, dns = prepared()
    state = store(backend).load("primary")
    backup = tmp_path / "before.enc.json"
    if reason == "pending":
        changed = replace(state, journal=replace(state.journal, phase="applying"))
        store(backend).save(changed, state.revision)
    if reason == "drift":
        dns.records[0] = replace(dns.records[0], content="1.1.1.1")
    if reason == "backup_exists":
        backup.write_bytes(b"do not overwrite")
    if reason == "extra_state":
        backend.files["state/other.json"] = b"other"
    before = dict(backend.files)
    backend.calls.clear()
    with pytest.raises((SafetyBlocked, StateConflict)):
        if reason == "target":
            from smartgate.state.key_rotation import rotate_state_key

            rotate_state_key(
                store(backend),
                replace(target(), namespace="other"),
                dns,
                EnvelopeCipher(NEW_KEY),
                backup,
                apply=True,
            )
        else:
            rotate(backend, dns, backup, key=KEY if reason == "same_key" else NEW_KEY)
    assert backend.files == before
    assert all(method == "GET" for method, _, _ in backend.calls)


def test_rotation_lost_ack_is_read_back_once_without_mutation_retry(tmp_path):
    backend, dns = prepared()
    backend.lose_ack = True
    rotate(backend, dns, tmp_path / "before.enc.json")
    assert new_store(backend).load("primary").journal.phase == "verified"
    assert sum(method == "PATCH" for method, _, _ in backend.calls) == 1


def test_rotation_conflict_preserves_old_state_and_backup(tmp_path):
    backend, dns = prepared()
    before = backend.files["state/primary.json"]
    backend.conflict = True
    backup = tmp_path / "before.enc.json"
    with pytest.raises(StateConflict):
        rotate(backend, dns, backup)
    assert backend.files["state/primary.json"] == backup.read_bytes() == before


def test_rotation_unknown_write_result_is_not_retried(tmp_path):
    backend, dns = prepared()
    original = backend.request
    attempts = []

    def request(method, path, payload=None):
        if method == "PATCH":
            attempts.append(path)
            raise SafetyBlocked("lost before acknowledgement")
        return original(method, path, payload)

    backend.request = request
    with pytest.raises(RecoveryRequired):
        rotate(backend, dns, tmp_path / "before.enc.json")
    assert len(attempts) == 1


def test_cli_rotation_delegates_without_routing_or_dns_writes(monkeypatch, tmp_path):
    import smartgate.cli as cli
    from tests.integration.test_app import setup

    config, deps = setup(enabled=False, bootstrap=False)
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(
        cli,
        "live_dependencies",
        lambda *a, **kw: replace(deps, rotate_state=lambda apply: calls.append(apply)),
    )
    assert (
        cli.main(["state-rotate", "--config", "unused", "--apply", "--report-dir", str(tmp_path)])
        == 0
    )
    assert calls == [True] and deps.dns.writes == 0


def test_rotation_requires_manual_dispatch_and_paused_auto_apply():
    from smartgate.workflow import controller_arguments
    from tests.integration.test_dns_workflow import env

    values = {
        "SMARTGATE_COMMAND": "state-rotate",
        "SMARTGATE_REQUEST_APPLY": "true",
        "SMARTGATE_APPLY_ENABLED": "false",
    }
    assert controller_arguments(env(**values))[0] == "state-rotate"
    for bad in (
        {"SMARTGATE_EVENT": "schedule"},
        {"SMARTGATE_APPLY_ENABLED": "true"},
        {"SMARTGATE_BOOTSTRAP": "true"},
    ):
        with pytest.raises(SafetyBlocked):
            controller_arguments(env(**(values | bad)))


@pytest.mark.parametrize(
    "extra",
    [
        ["--offline"],
        ["--bootstrap"],
        ["--run-id", "x"],
        ["--previous-run-stopped"],
        ["--require-no-change"],
    ],
)
def test_cli_rotation_invalid_flags_reject_before_clients(monkeypatch, extra):
    import smartgate.cli as cli
    from tests.integration.test_app import setup

    config, _ = setup()
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(
        cli, "live_dependencies", lambda *a, **kw: pytest.fail("clients constructed")
    )
    assert cli.main(["state-rotate", "--config", "unused", "--apply", *extra]) == 4


def test_corrupted_source_is_never_treated_as_absent_or_bootstrapped(tmp_path):
    backend, dns = prepared()
    backend.files["state/primary.json"] = b"corrupt"
    backend.commits[backend.head][1]["state/primary.json"] = b"corrupt"
    with pytest.raises(SafetyBlocked):
        rotate(backend, dns, tmp_path / "before.enc.json")
    assert all(method == "GET" for method, _, _ in backend.calls)


def test_key_rotation_continues_through_normal_sync_and_verified_backup_restore(tmp_path):
    from smartgate.app import run
    from tests.integration.test_app import setup

    backend, dns = prepared()
    original = store(backend).load("primary")
    records, writes = tuple(dns.records), dns.writes
    backup = tmp_path / "before.enc.json"
    rotate(backend, dns, backup)
    config, deps = setup(bootstrap=False)
    upgraded = replace(
        deps,
        dns=dns,
        store=new_store(backend),
        profile_id="primary",
        dns_target=target(),
        require_no_change=True,
    )
    assert run(config, "sync", True, upgraded).status == "no_change"
    assert tuple(dns.records) == records and dns.writes == writes
    # Restore the verified backup document through CAS under the new key;
    # never reset a Git ref or discard newer state without inspecting it.
    saved = codec().decode(backup.read_bytes(), "primary", original.revision)
    storage = new_store(backend)
    current = storage.load("primary")
    restored = replace(saved, revision=current.revision)
    storage.save(restored, current.revision)
    actual = new_store(backend).load("primary")
    assert replace(actual, revision=original.revision) == original
    assert tuple(dns.records) == records and dns.writes == writes


def test_workflow_rotation_key_and_backup_are_explicitly_allowlisted():
    from pathlib import Path

    text = Path(".github/workflows/sync.yml").read_text()
    assert "inputs.command == 'state-rotate' && secrets.SMARTGATE_STATE_NEW_ENCRYPTION_KEY" in text
    assert ".smartgate/rotation/before.enc.json" in text
