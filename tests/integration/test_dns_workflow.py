import pytest

from smartgate.errors import SafetyBlocked
from smartgate.workflow import controller_arguments


def env(**values):
    return {
        "SMARTGATE_COMMAND": "sync",
        "SMARTGATE_EVENT": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "SMARTGATE_DEFAULT_BRANCH": "main",
        **values,
    }


def test_manual_apply_is_explicit_and_independent_of_schedule_switch():
    assert "--apply" not in controller_arguments(env())
    assert "--apply" in controller_arguments(
        env(SMARTGATE_REQUEST_APPLY="true", SMARTGATE_APPLY_ENABLED="false")
    )


def test_scheduled_apply_requires_enabled_variable():
    assert "--apply" not in controller_arguments(env(SMARTGATE_EVENT="schedule"))
    assert "--apply" in controller_arguments(
        env(SMARTGATE_EVENT="schedule", SMARTGATE_APPLY_ENABLED="true")
    )


@pytest.mark.parametrize(
    "values",
    [
        {
            "SMARTGATE_COMMAND": "state-upgrade",
            "SMARTGATE_REQUEST_APPLY": "true",
            "SMARTGATE_BOOTSTRAP": "true",
        },
        {"SMARTGATE_COMMAND": "state-upgrade", "SMARTGATE_EVENT": "schedule"},
        {
            "SMARTGATE_COMMAND": "state-upgrade",
            "SMARTGATE_REQUEST_APPLY": "true",
            "GITHUB_REF": "refs/heads/topic",
        },
        {"SMARTGATE_COMMAND": "unknown"},
        {"SMARTGATE_REQUEST_APPLY": "yes"},
    ],
)
def test_invalid_dispatch_is_rejected_before_controller(values):
    with pytest.raises(SafetyBlocked):
        controller_arguments(env(**values))


def test_state_upgrade_apply_requires_default_branch_and_no_bootstrap():
    args = controller_arguments(
        env(SMARTGATE_COMMAND="state-upgrade", SMARTGATE_REQUEST_APPLY="true")
    )
    assert args[0] == "state-upgrade"
    assert "--apply" in args and "--bootstrap" not in args
    assert "--apply" not in controller_arguments(env(SMARTGATE_COMMAND="state-upgrade"))


def test_readonly_preview_cannot_receive_apply_flag():
    with pytest.raises(SafetyBlocked):
        controller_arguments(env(SMARTGATE_COMMAND="preview", SMARTGATE_REQUEST_APPLY="true"))


@pytest.mark.parametrize(
    "new,old,expected",
    [
        ("neutral-value", "", 0),
        ("legacy-value", "legacy-value", 0),
        ("same-value", "same-value", 0),
        ("new-value", "old-value", 4),
    ],
)
def test_materialized_actions_environment_preserves_real_conflicts(monkeypatch, new, old, expected):
    import os

    import smartgate.workflow as workflow
    from smartgate.dns.credentials import resolve_nextdns_value
    from smartgate.errors import ConfigError

    monkeypatch.setenv("SMARTGATE_DNS_API_KEY", new)
    monkeypatch.setenv("NEXTDNS_API_KEY", old)
    monkeypatch.setenv("SMARTGATE_DNS_PROFILE_ID", "abcdef")
    monkeypatch.setenv("NEXTDNS_PROFILE_ID", "" if not old else "abcdef")
    for key, value in env().items():
        monkeypatch.setenv(key, value)

    def controller(args):
        try:
            assert (
                resolve_nextdns_value("SMARTGATE_DNS_API_KEY", "NEXTDNS_API_KEY", os.environ) == new
            )
            assert (
                resolve_nextdns_value("SMARTGATE_DNS_PROFILE_ID", "NEXTDNS_PROFILE_ID", os.environ)
                == "abcdef"
            )
            return 0
        except ConfigError:
            return 4

    monkeypatch.setattr(workflow, "controller_main", controller)
    assert workflow.main() == expected


def test_actions_accepts_fresh_explicit_warning_only_result(monkeypatch, tmp_path):
    import json

    import smartgate.workflow as workflow

    monkeypatch.chdir(tmp_path)
    for key, value in env(SMARTGATE_REQUEST_APPLY="true").items():
        monkeypatch.setenv(key, value)
    path = tmp_path / ".smartgate/reports/report.json"

    def controller(args):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"status": "degraded", "exit_code": 3, "workflow_exit_code": 0}),
            encoding="utf-8",
        )
        return 3

    monkeypatch.setattr(workflow, "controller_main", controller)
    assert workflow.main() == 0


def test_stale_success_artifact_cannot_hide_new_controller_failure(monkeypatch, tmp_path):
    import json

    import smartgate.workflow as workflow

    monkeypatch.chdir(tmp_path)
    for key, value in env().items():
        monkeypatch.setenv(key, value)
    path = tmp_path / ".smartgate/reports/report.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "degraded", "exit_code": 3, "workflow_exit_code": 0}))
    monkeypatch.setattr(workflow, "controller_main", lambda args: 3)
    assert workflow.main() == 3
    assert not path.exists()


@pytest.mark.parametrize(
    "values",
    [
        {"SMARTGATE_COMMAND": "preview"},
        {"SMARTGATE_COMMAND": "recover"},
        {"SMARTGATE_COMMAND": "rollback"},
        {"SMARTGATE_COMMAND": "state-upgrade"},
        {"SMARTGATE_REQUEST_APPLY": "false"},
        {"SMARTGATE_BOOTSTRAP": "true"},
        {"SMARTGATE_EVENT": "schedule"},
        {"SMARTGATE_REQUIRE_NO_CHANGE": "TRUE"},
    ],
)
def test_workflow_no_change_guard_rejects_invalid_inputs(values):
    with pytest.raises(SafetyBlocked):
        controller_arguments(
            env(
                **(
                    {"SMARTGATE_REQUEST_APPLY": "true", "SMARTGATE_REQUIRE_NO_CHANGE": "true"}
                    | values
                )
            )
        )


def test_workflow_no_change_guard_reaches_controller_only_when_requested():
    assert "--require-no-change" in controller_arguments(
        env(SMARTGATE_REQUEST_APPLY="true", SMARTGATE_REQUIRE_NO_CHANGE="true")
    )
    assert "--require-no-change" not in controller_arguments(env(SMARTGATE_REQUEST_APPLY="true"))


def test_state_encryption_dispatch_is_explicit_manual_only():
    args = controller_arguments(
        env(SMARTGATE_COMMAND="state-encrypt", SMARTGATE_REQUEST_APPLY="true")
    )
    assert args[0] == "state-encrypt" and "--apply" in args
    for values in (
        {"SMARTGATE_EVENT": "schedule"},
        {"SMARTGATE_BOOTSTRAP": "true"},
        {"SMARTGATE_REQUIRE_NO_CHANGE": "true"},
    ):
        with pytest.raises(SafetyBlocked):
            controller_arguments(
                env(
                    **(
                        {"SMARTGATE_COMMAND": "state-encrypt", "SMARTGATE_REQUEST_APPLY": "true"}
                        | values
                    )
                )
            )


def test_workflow_always_requests_private_report():
    assert "--private-report" in controller_arguments(env())


def test_workflow_exports_key_and_uploads_only_allowlisted_reports():
    from pathlib import Path

    workflow = Path(".github/workflows/sync.yml").read_text()
    assert (
        "SMARTGATE_STATE_ENCRYPTION_KEY: ${{ secrets.SMARTGATE_STATE_ENCRYPTION_KEY }}" in workflow
    )
    assert "state-encrypt]" in workflow
    assert "path: .smartgate/reports/" not in workflow
    for name in ("report.json", "report.md", "report.enc.json"):
        assert ".smartgate/reports/" + name in workflow


def test_sensitive_dns_metadata_prefers_masked_secrets():
    from pathlib import Path

    workflow = Path(".github/workflows/sync.yml").read_text()
    for name in (
        "SMARTGATE_DNS_PROFILE_ID",
        "SMARTGATE_DNS_API_URL",
        "SMARTGATE_DNS_NAMESPACE",
        "SMARTGATE_DNS_DOH_URL",
    ):
        assert name + ": ${{ secrets." + name + " || vars." + name in workflow


@pytest.mark.parametrize("automatic,command", [("false", "doctor"), ("true", "sync")])
def test_scheduled_command_checks_dns_when_automatic_apply_is_disabled(automatic, command):
    args = controller_arguments(env(SMARTGATE_EVENT="schedule", SMARTGATE_APPLY_ENABLED=automatic))
    assert args[0] == command
    assert ("--apply" in args) == (automatic == "true")


@pytest.mark.parametrize("command", ["sync", "preview", "doctor"])
def test_manual_readonly_commands_keep_their_original_meaning(command):
    args = controller_arguments(env(SMARTGATE_COMMAND=command))
    assert args[0] == command and "--apply" not in args
