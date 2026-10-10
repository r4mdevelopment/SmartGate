"""Finite, non-sensitive explanations; never publish exception or adapter text."""

from smartgate.availability import meets_availability_threshold
from smartgate.models import RunReport

# Messages and actions are repository-owned literals, independent of external strings.
MESSAGES = {
    "source_unavailable": (
        "A relay address source could not be downloaded or validated.",
        "Run preview again. If it repeats, inspect the encrypted source details before "
        "changing providers.",
    ),
    "required_route_unavailable": (
        "At least one required site has no verified usable route.",
        "Run doctor again and check the service on your device. Do not raise publication "
        "limits to bypass failed routes.",
    ),
    "availability_below_threshold": (
        "Verified route availability is below the minimum of 85% for a service.",
        "Run doctor and inspect the encrypted route details before applying changes.",
    ),
    "dns_api_unavailable": (
        "The DNS provider API request could not be completed.",
        "Check the provider status and DNS credentials. Inspect any pending journal before "
        "another apply.",
    ),
    "dns_verification_failed": (
        "DNS answers did not pass the expected checks.",
        "Run doctor after a short wait. If the journal is pending, follow the recovery guide "
        "before another apply.",
    ),
    "verification_incomplete": (
        "The read-only check could not confirm the complete saved configuration.",
        "Inspect preview and the saved journal, then follow the first-run or recovery guide "
        "as appropriate.",
    ),
    "state_conflict": (
        "Saved state changed or another run holds the lock.",
        "Wait for the other run to finish and retry doctor. Never delete the state branch to "
        "resolve a conflict.",
    ),
    "config_invalid": (
        "The controller configuration is invalid or incomplete.",
        "Run validate and check the configuration and required secrets against the setup guide.",
    ),
    "mutation_limit": (
        "The plan exceeds the maximum number of DNS changes per run.",
        "Inspect preview and max_mutations. Increase the limit only after reviewing the exact "
        "planned changes.",
    ),
    "change_ratio_limit": (
        "The plan would replace or delete too large a share of managed records.",
        "Inspect preview and max_change_ratio. Confirm a genuine address change before "
        "adjusting this protection.",
    ),
    "record_limit": (
        "The plan exceeds the maximum number of managed DNS records.",
        "Reduce the enabled domain sets and review max_records. The hard maximum is 100.",
    ),
    "plan_conflict": (
        "The plan conflicts with existing records or ownership safeguards.",
        "Inspect the encrypted plan. Resolve the conflict without deleting ownership state or "
        "unrelated DNS records.",
    ),
    "recovery_required": (
        "A previous update has an unfinished or unconfirmed result.",
        "Inspect the encrypted journal and follow the recovery guide before starting another "
        "update.",
    ),
    "safety_blocked": (
        "A safety precondition prevented this command from continuing.",
        "Check the command options and inspect the encrypted details. Do not bypass "
        "safeguards or reset state.",
    ),
    "encryption_setup_invalid": (
        "Private reporting could not establish valid encryption settings.",
        "Check the encryption secret and Actions run identity. Keep the original state key "
        "and its backup.",
    ),
    "encryption_report_failed": (
        "The detailed report could not be encrypted safely.",
        "Check encryption settings and inspect the durable journal before another apply. No "
        "plaintext report is published.",
    ),
    "interrupted": (
        "The command was interrupted before completion.",
        "Retry a read-only check. If writes were authorized, inspect the journal before "
        "another apply.",
    ),
    "controller_error": (
        "The controller could not complete the command; the cause is not safely classified.",
        "Decrypt the detailed artifact on a trusted device and follow the troubleshooting guide.",
    ),
}

PLAN_REASONS = {
    "mutation limit exceeded": "mutation_limit",
    "change ratio exceeded": "change_ratio_limit",
    "managed record limit exceeded": "record_limit",
    "pending journal requires recovery": "recovery_required",
}
ERROR_REASONS = {
    "Pending journal requires explicit recovery": "recovery_required",
    "Profile DNS verification not confirmed": "dns_verification_failed",
    "API confirmed; profile DNS verification not confirmed": "dns_verification_failed",
    "Recovered API state; profile DNS still unconfirmed": "dns_verification_failed",
}


def public_issues(report: RunReport) -> list[dict[str, str]]:
    codes = {code for code in report.issue_codes if code in MESSAGES}
    if report.plan:
        codes.update(
            PLAN_REASONS.get(reason, "plan_conflict") for reason in report.plan.blocked_reasons
        )
    # Exact literals only: no keyword matching of external exception strings.
    codes.update(ERROR_REASONS[e] for e in report.errors if e in ERROR_REASONS)
    if any(not check.ok for check in report.checks):
        codes.add("dns_verification_failed")
    if report.availability:
        if any(not value.required_ok for value in report.availability):
            codes.add("required_route_unavailable")
        if not meets_availability_threshold(report.availability):
            codes.add("availability_below_threshold")
    configuration_valid = report.status == "preview" and report.errors == ("Configuration valid",)
    if not codes and report.errors and not configuration_valid:
        codes.add("controller_error")
    return [
        {"code": code, "message": MESSAGES[code][0], "next_step": MESSAGES[code][1]}
        for code in sorted(codes)
    ]
