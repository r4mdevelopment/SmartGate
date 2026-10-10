"""Public allowlisted reports: never serialize state, profile IDs or HTTP bodies."""

import json
from collections.abc import Sequence
from dataclasses import asdict

from smartgate.availability import MIN_ROUTE_AVAILABILITY_PERCENT, meets_availability_threshold
from smartgate.diagnostics import public_issues
from smartgate.models import RunReport


def exit_code(report: RunReport) -> int:
    if report.exit_code_override is not None:
        return report.exit_code_override
    return {"preview": 0, "no_change": 0, "applied": 0, "degraded": 3, "blocked": 4, "partial": 6}[
        report.status
    ]


def workflow_exit_code(report: RunReport) -> int:
    code = exit_code(report)
    if (
        code == 3
        and report.status == "degraded"
        and report.optional_route_warnings
        and meets_availability_threshold(report.availability)
        and not report.offline
        and not report.errors
        and report.plan is not None
        and not report.plan.blocked_reasons
        and (
            report.journal_phase == "verified"
            or (
                report.journal_phase == "rolled_back"
                and not report.plan.operations
                and dict(report.counts).get("mutations") == 0
            )
        )
        and bool(report.checks)
        and all(check.ok for check in report.checks)
    ):
        return 0
    return code


def render_report(report: RunReport, redactions: Sequence[str] = ()) -> tuple[str, str]:
    data = {
        "run_id": report.run_id,
        "status": report.status,
        "offline": report.offline,
        "dns_provider": report.dns_provider,
        "dns_readiness": report.dns_readiness,
        "exit_code": exit_code(report),
        "workflow_exit_code": workflow_exit_code(report),
        "counts": dict(report.counts),
        "availability_threshold_percent": MIN_ROUTE_AVAILABILITY_PERCENT,
        "availability": [asdict(v) for v in report.availability],
        "journal_phase": report.journal_phase,
        "journal_run_id": report.journal_run_id,
        "errors": report.errors,
        "issues": public_issues(report),
        "sources": [
            {
                "provider_id": s.provider_id,
                "fetched_at": s.fetched_at.isoformat(),
                "source_digest": s.source_digest,
                "warnings": s.warnings,
                "candidate_count": len(s.candidates),
            }
            for s in report.sources
        ],
        "decisions": [
            {
                "domain": d.domain,
                "action": d.action,
                "reason": d.reason,
                "provider": d.candidate.provider_id if d.candidate else None,
                "ip": d.candidate.ip if d.candidate else None,
            }
            for d in report.decisions
        ],
        "probes": [
            {
                "domain": p.candidate.domain,
                "ip": p.candidate.ip,
                "provider": p.candidate.provider_id,
                "status": p.status,
                "vantage": p.vantage,
                "checked_at": p.checked_at.isoformat(),
                "latency_ms": p.latency_ms,
                "reason": p.reason,
            }
            for p in report.probes
        ],
        "checks": [asdict(c) | {"checked_at": c.checked_at.isoformat()} for c in report.checks],
        "plan": {
            "plan_id": report.plan.plan_id,
            "created_at": report.plan.created_at.isoformat(),
            "blocked_reasons": report.plan.blocked_reasons,
            "operations": [
                {
                    "kind": op.kind,
                    "domain": op.domain,
                    "before": op.before.content if op.before else None,
                    "after": op.after.ipv4 if op.after else None,
                }
                for op in report.plan.operations
            ],
        }
        if report.plan
        else None,
    }
    # Scrub only string values before encoding, preserving JSON keys and scalar types.
    secrets = sorted({v for v in redactions if v}, key=len, reverse=True)

    def scrub(value: object) -> object:
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [scrub(item) for item in value]
        return value

    encoded = json.dumps(scrub(data), ensure_ascii=False, indent=2) + "\n"
    safe = json.loads(encoded)

    def cell(value: object) -> str:
        return (
            str(value)
            .replace("|", r"\|")
            .replace("\n", " ")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    lines = [
        "# SmartGate report",
        "",
        "Status: " + safe["status"],
        "Run: " + safe["run_id"],
        "Offline: " + str(safe["offline"]).lower(),
        "",
    ]
    if safe["availability"]:
        lines += [
            "## Service availability",
            "",
            "Minimum: " + str(safe["availability_threshold_percent"]) + "% per service",
            "",
            "| Service | Working routes | Availability | Required domains |",
            "| --- | --- | --- | --- |",
        ]
        for value in safe["availability"]:
            percent = 100 * value["available"] / value["total"] if value["total"] else 0
            lines.append(
                "| "
                + cell(value["service_id"])
                + " | "
                + str(value["available"])
                + " / "
                + str(value["total"])
                + " | "
                + f"{percent:.1f}%"
                + " | "
                + ("ok" if value["required_ok"] else "failed")
                + " |"
            )
        lines.append("")
    if safe["status"] == "degraded" and safe["workflow_exit_code"] == 0:
        lines += ["> [!WARNING]", "> DNS sync verified with optional route warnings.", ""]
    if safe["journal_phase"]:
        lines += [
            "Journal: " + safe["journal_phase"],
            "Journal run: " + str(safe["journal_run_id"] or ""),
            "",
        ]
    lines += [
        "## Routes",
        "",
        "| Domain | Action | Relay | IP | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for d in safe["decisions"]:
        lines.append(
            "| "
            + " | ".join(cell(d[k] or "") for k in ("domain", "action", "provider", "ip", "reason"))
            + " |"
        )
    if safe["plan"]:
        lines += [
            "",
            "## Changes",
            "",
            "| Operation | Domain | Before | After |",
            "| --- | --- | --- | --- |",
        ]
        for op in safe["plan"]["operations"]:
            lines.append(
                "| "
                + " | ".join(cell(op[k] or "") for k in ("kind", "domain", "before", "after"))
                + " |"
            )
    lines += ["", "## Sources and observations", ""]
    for source in safe["sources"]:
        lines += ["- " + cell(source["provider_id"]) + ": " + cell(source["fetched_at"])]
        lines += ["  - " + cell(w) for w in source["warnings"]]
    for probe in safe["probes"]:
        lines += [
            "- "
            + cell(probe["domain"])
            + ": "
            + cell(probe["status"])
            + " ("
            + cell(probe["vantage"])
            + "): "
            + cell(probe["reason"])
        ]
    for check in safe["checks"]:
        lines += [
            "- "
            + cell(check["domain"])
            + " "
            + cell(check["qtype"])
            + ": "
            + ("ok" if check["ok"] else "failed")
            + " — "
            + cell(check["reason"])
        ]
    lines += ["", "## Errors", ""] + ["- " + cell(e) for e in safe["errors"]]
    if safe["plan"]:
        lines += ["- " + cell(e) for e in safe["plan"]["blocked_reasons"]]
    return encoded, "\n".join(lines) + "\n"
