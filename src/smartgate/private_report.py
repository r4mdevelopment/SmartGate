"""Public aggregate projection and authenticated detailed report artifacts."""

import json
import re
from collections.abc import Mapping, Sequence

from smartgate.availability import MIN_ROUTE_AVAILABILITY_PERCENT, meets_availability_threshold
from smartgate.diagnostics import public_issues
from smartgate.errors import SafetyBlocked
from smartgate.models import RunReport
from smartgate.report import exit_code, render_report, workflow_exit_code
from smartgate.security.envelope import MAX_PLAINTEXT, EnvelopeBinding, EnvelopeCipher

PHASES = {
    "prepared",
    "applying",
    "verifying",
    "verified",
    "rolling_back",
    "rolled_back",
    "blocked",
    "partial",
}
COUNT_NAMES = {
    "mutations",
    "created",
    "replaced",
    "deleted",
    "unchanged",
    "candidates",
    "probes",
    "selected",
    "operations",
    "failover_owned",
    "failover_failed_initial",
    "failover_failed_confirmed",
}


def report_binding(repository: str, run_id: str, attempt: str) -> EnvelopeBinding:
    if not re.fullmatch(r"[1-9][0-9]{0,29}", run_id) or not re.fullmatch(
        r"[1-9][0-9]{0,9}", attempt
    ):
        raise SafetyBlocked("Private report requires valid run identity")
    return EnvelopeBinding("report", repository, "actions", run_id + ":" + attempt)


def binding_from_env(env: Mapping[str, str]) -> EnvelopeBinding:
    return report_binding(
        env.get("GITHUB_REPOSITORY", ""),
        env.get("GITHUB_RUN_ID", ""),
        env.get("GITHUB_RUN_ATTEMPT", ""),
    )


def public_report(report: RunReport) -> tuple[str, str]:
    """Do not forward arbitrary strings from adapters, config or exception messages."""
    configuration_valid = report.status == "preview" and report.errors == ("Configuration valid",)
    has_errors = bool(report.errors) and not configuration_valid
    issues = public_issues(report)
    counts = {
        name: value
        for name, value in report.counts
        if name in COUNT_NAMES and type(value) is int and value >= 0
    }
    data = {
        "run_id": report.run_id if re.fullmatch(r"[0-9a-f]{32}", report.run_id) else None,
        "status": report.status,
        "offline": report.offline,
        "exit_code": exit_code(report),
        "workflow_exit_code": workflow_exit_code(report),
        "counts": counts,
        "operations": len(report.plan.operations) if report.plan else 0,
        "checks": {
            "total": len(report.checks),
            "successful": sum(check.ok for check in report.checks),
        },
        "services": len(report.availability),
        "availability_threshold_percent": MIN_ROUTE_AVAILABILITY_PERCENT,
        "minimum_availability_percent": min(
            (100 * a.available / a.total if a.total else 0 for a in report.availability),
            default=None,
        ),
        "required_routes_ok": bool(report.availability)
        and all(a.required_ok for a in report.availability),
        "availability_threshold_met": meets_availability_threshold(report.availability),
        "journal_phase": report.journal_phase if report.journal_phase in PHASES else None,
        "journal_run_id": report.journal_run_id
        if report.journal_run_id and re.fullmatch(r"[0-9a-f]{32}", report.journal_run_id)
        else None,
        "optional_route_warnings": report.optional_route_warnings,
        "errors": ["controller_error"] if has_errors else [],
        "issues": issues,
    }
    encoded = json.dumps(data, ensure_ascii=True, indent=2) + "\n"
    lines = [
        "# SmartGate report",
        "",
        "Status: " + report.status,
        "",
        "DNS operations: " + str(data["operations"]),
        "DNS checks: "
        + str(sum(check.ok for check in report.checks))
        + " / "
        + str(len(report.checks)),
        "",
        "Detailed operational data is available only in the encrypted artifact.",
        "",
    ]
    if "failover_owned" in counts:
        lines += ["Emergency-only inspection: normal publication changes are excluded.", ""]
        for name, label in (
            ("failover_owned", "Published owned routes"),
            ("failover_failed_initial", "Initially failed TLS routes"),
            ("failover_failed_confirmed", "Confirmed failed TLS routes"),
        ):
            if name in counts:
                lines.append(label + ": " + str(counts[name]))
        lines.append("")
    if configuration_valid:
        lines += ["Configuration valid", ""]
    if issues:
        lines += ["## What happened", "", "| Code | Reason | Next step |", "| --- | --- | --- |"]
        for issue in issues:
            lines.append(
                "| `"
                + issue["code"]
                + "` | "
                + issue["message"]
                + " | "
                + issue["next_step"]
                + " |"
            )
        lines.append("")
    if report.optional_route_warnings:
        lines += [
            "> [!WARNING]",
            "> Optional route availability warnings; see aggregate status and encrypted details.",
            "",
        ]
    return encoded, "\n".join(lines)


def encrypt_report(
    report: RunReport, redactions: Sequence[str], cipher: EnvelopeCipher, binding: EnvelopeBinding
) -> bytes:
    if binding.purpose != "report":
        raise SafetyBlocked("Invalid report binding")
    detailed, _ = render_report(report, redactions)
    return cipher.seal(detailed.encode("utf-8"), binding, max_plaintext=MAX_PLAINTEXT)


def decrypt_report(envelope: bytes, cipher: EnvelopeCipher, binding: EnvelopeBinding) -> bytes:
    if binding.purpose != "report":
        raise SafetyBlocked("Invalid report binding")
    return cipher.open(envelope, binding, max_plaintext=MAX_PLAINTEXT)
