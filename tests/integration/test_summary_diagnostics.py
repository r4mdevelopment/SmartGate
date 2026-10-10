import json
from dataclasses import replace

import pytest

from smartgate.app import run
from smartgate.errors import DnsApiError, StateConflict
from smartgate.private_report import public_report
from smartgate.report import exit_code, workflow_exit_code
from tests.integration.test_app import Checker, Relay, setup


def codes(report):
    return {issue["code"] for issue in json.loads(public_report(report)[0]).get("issues", [])}


@pytest.mark.parametrize(
    "failure, expected",
    [
        ("source", "source_unavailable"),
        ("route", "required_route_unavailable"),
        ("api", "dns_api_unavailable"),
        ("state", "state_conflict"),
        ("disabled", "safety_blocked"),
    ],
)
def test_public_summary_explains_failures_without_exposing_external_data(failure, expected):
    config, deps = setup()
    secret = "private.example 8.8.8.8 api-key-secret"
    if failure == "source":
        deps = replace(deps, relays={p.id: Relay(True) for p in config.providers})
    elif failure == "route":
        deps = replace(deps, checker=Checker("failed"))
    elif failure == "api":

        def fail_api():
            raise DnsApiError(secret)

        deps.dns.list_records = fail_api
    elif failure == "state":

        def fail_state(profile):
            raise StateConflict(secret)

        deps.store.load = fail_state
    else:
        config = replace(config, sync=replace(config.sync, enabled=False))
    report = run(
        config, "sync" if failure == "disabled" else "preview", failure == "disabled", deps
    )
    encoded, markdown = public_report(report)
    assert expected in codes(report)
    assert "Next step" in markdown
    for marker in secret.split():
        assert marker not in encoded + markdown
    assert deps.dns.writes == deps.store.saves == 0
    assert json.loads(encoded)["exit_code"] == exit_code(report)
    assert json.loads(encoded)["workflow_exit_code"] == workflow_exit_code(report)


def test_plan_limits_have_specific_safe_actions():
    config, deps = setup()
    preview = run(config, "preview", False, deps)
    blocked = replace(
        preview,
        status="blocked",
        errors=("private.example",),
        plan=replace(
            preview.plan, blocked_reasons=("mutation limit exceeded", "change ratio exceeded")
        ),
    )
    encoded, markdown = public_report(blocked)
    assert {"mutation_limit", "change_ratio_limit"} <= codes(blocked)
    assert "max_mutations" in markdown and "max_change_ratio" in markdown
    assert "private.example" not in encoded + markdown


def test_unknown_failure_is_explained_without_guessing_category():
    from smartgate.models import RunReport

    report = RunReport("a" * 32, "blocked", (), (), (), ("API key private.example",))
    encoded, markdown = public_report(report)
    assert codes(report) == {"controller_error"}
    assert "Next step" in markdown
    assert "private.example" not in encoded + markdown


def test_failed_dns_checks_are_explained_without_exposing_answers():
    from tests.integration.test_app import optional_route_report

    value = optional_route_report(dns_failed=True)
    assert "dns_verification_failed" in codes(value)
    assert "chatgpt.com" not in str(public_report(value))


def test_pending_journal_explains_recovery_instead_of_new_apply():
    config, deps = setup()
    deps.dns.mode = "timeout_create"
    run(config, "sync", True, deps)
    value = run(config, "preview", False, deps)
    assert "recovery_required" in codes(value)
    assert "recovery guide" in public_report(value)[1]


def test_unknown_diagnostic_codes_and_external_markup_never_reach_summary():
    from smartgate.models import RunReport

    value = RunReport(
        "a" * 32,
        "blocked",
        (),
        (),
        (),
        ("private.example",),
        issue_codes=("<script>private.example</script>", "mutation_limit private.example"),
    )
    assert codes(value) == {"controller_error"}
    encoded, markdown = public_report(value)
    assert "private.example" not in encoded + markdown
    assert "<script>" not in encoded + markdown


@pytest.mark.parametrize(
    "failure, expected", [("config", "config_invalid"), ("encryption", "encryption_setup_invalid")]
)
def test_private_cli_early_failure_has_safe_action(
    monkeypatch, tmp_path, capsys, failure, expected
):
    import smartgate.cli as cli
    from smartgate.errors import ConfigError
    from tests.integration.test_private_cli import context

    context(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    if failure == "config":

        def bad_config(*args):
            raise ConfigError("private.example api-key-secret")

        monkeypatch.setattr(cli, "load_config", bad_config)
    else:
        config, deps = setup()
        monkeypatch.setattr(cli, "load_config", lambda *args: config)
        monkeypatch.setenv("SMARTGATE_STATE_ENCRYPTION_KEY", "private.example")
    assert cli.main(["preview", "--config", "unused", "--report-dir", str(tmp_path)]) != 0
    data = json.loads((tmp_path / "report.json").read_text())
    assert expected in {issue["code"] for issue in data["issues"]}
    assert "private.example" not in capsys.readouterr().out
    assert not (tmp_path / "report.enc.json").exists()


def test_fatal_preflight_failure_remains_visible_after_source_warning():
    config, deps = setup()
    providers = {p.id: Relay(index == 0) for index, p in enumerate(config.providers)}

    def fail_preflight(count):
        raise RuntimeError("private.example api-key-secret")

    deps = replace(deps, relays=providers, preflight_writes=fail_preflight)
    value = run(config, "sync", True, deps)
    assert exit_code(value) == 5
    assert {"controller_error", "source_unavailable"} <= codes(value)
    assert deps.dns.writes == deps.store.saves == 0
    assert "private.example" not in str(public_report(value))
