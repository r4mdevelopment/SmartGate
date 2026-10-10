import json
from dataclasses import replace

import pytest

from smartgate.errors import SafetyBlocked
from smartgate.models import RunReport
from smartgate.report import exit_code, workflow_exit_code
from smartgate.security.envelope import EnvelopeBinding, EnvelopeCipher


def report():
    return RunReport(
        "a" * 32,
        "blocked",
        (("mutations", 0), ("private-key.example", 9)),
        (),
        (),
        ("secret.example 8.8.8.8 private-profile provider-id service-id",),
        dns_provider="provider-id",
        journal_phase="secret.example",
        journal_run_id="private-profile",
    )


def test_public_projection_never_forwards_arbitrary_strings():
    from smartgate.private_report import public_report

    text, markdown = public_report(report())
    for marker in (
        "secret.example",
        "8.8.8.8",
        "private-profile",
        "provider-id",
        "service-id",
        "private-key.example",
    ):
        assert marker not in text + markdown
    data = json.loads(text)
    assert data["counts"] == {"mutations": 0}
    assert data["errors"] == ["controller_error"]
    assert data["journal_phase"] is None


@pytest.mark.parametrize(
    "status", ["preview", "no_change", "applied", "degraded", "blocked", "partial"]
)
def test_public_status_comes_from_authoritative_report(status):
    from smartgate.private_report import public_report

    value = replace(report(), status=status)
    data = json.loads(public_report(value)[0])
    assert data["status"] == status
    assert data["exit_code"] == exit_code(value)
    assert data["workflow_exit_code"] == workflow_exit_code(value)


def test_detailed_artifact_is_authenticated_and_bound_to_run():
    from smartgate.private_report import decrypt_report, encrypt_report

    cipher = EnvelopeCipher(bytes(range(32)))
    binding = EnvelopeBinding("report", "owner/repo", "actions", "123:1")
    encrypted = encrypt_report(report(), (), cipher, binding)
    assert b"secret.example" not in encrypted
    assert b"secret.example" in decrypt_report(encrypted, cipher, binding)
    with pytest.raises(SafetyBlocked):
        decrypt_report(encrypted, cipher, replace(binding, identity="123:2"))
    with pytest.raises(SafetyBlocked):
        decrypt_report(encrypted, cipher, replace(binding, purpose="state"))


@pytest.mark.parametrize(
    "changes",
    [{}, {"dns_failed": True}, {"source_failed": True}, {"apply": False}, {"healthy_optional": 0}],
)
def test_private_projection_preserves_verified_warning_and_failure_status(changes):
    from smartgate.private_report import public_report
    from tests.integration.test_app import optional_route_report

    value = optional_route_report(**changes)
    data = json.loads(public_report(value)[0])
    assert data["workflow_exit_code"] == workflow_exit_code(value)
    assert "chatgpt.com" not in public_report(value)[0]


@pytest.mark.parametrize(
    "available,required_ok,expected", [(17, True, True), (16, True, False), (19, False, False)]
)
def test_private_threshold_is_exact_and_requires_required_domains(available, required_ok, expected):
    from smartgate.models import ServiceAvailability
    from smartgate.private_report import public_report

    value = replace(
        report(), availability=(ServiceAvailability("private-service", available, 20, required_ok),)
    )
    assert json.loads(public_report(value)[0])["availability_threshold_met"] is expected


def test_configuration_success_is_not_reported_as_error():
    from smartgate.private_report import public_report

    value = replace(report(), status="preview", errors=("Configuration valid",))
    text, markdown = public_report(value)
    assert json.loads(text)["errors"] == []
    assert "Configuration valid" in markdown
