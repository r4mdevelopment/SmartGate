"""Read-only route monitoring with private details and bounded anonymous HTTP checks."""

import argparse
import json
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from smartgate.config import load_config
from smartgate.dns.registry import create_dns_bundle
from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.dns.validation import validate_desired
from smartgate.dns.verify import DnsVerifier
from smartgate.errors import SafetyBlocked
from smartgate.health.http import HttpMonitor, HttpObservation
from smartgate.models import DesiredRewrite, DnsRecord, ManagedRecord
from smartgate.ports import DnsProvider, StateStore
from smartgate.private_report import binding_from_env
from smartgate.security.envelope import (
    MAX_PLAINTEXT,
    EnvelopeBinding,
    EnvelopeCipher,
    parse_encryption_key,
)
from smartgate.state.encrypted import EncryptedStateCodec
from smartgate.state.git_branch import GitBranchStateStore, GitHubClient

HTTP_CLASSES = frozenset(
    {
        "response",
        "challenge",
        "authorization_required",
        "http_rejected",
        "rate_limited",
        "server_error",
        "invalid_target",
        "certificate_error",
        "timeout",
        "connection_error",
        "invalid_response",
        "observation_error",
        "not_checked",
    }
)


@dataclass(frozen=True)
class RouteObservation:
    domain: str
    ip: str
    dns_ok: bool | None
    dns_classification: str
    http: HttpObservation
    dns_answers: tuple[str, ...]
    dns_elapsed_ms: float


@dataclass(frozen=True)
class MonitorReport:
    checked_at: str
    vantage: str
    routes: tuple[RouteObservation, ...] = ()
    issues: tuple[str, ...] = ()


def observe_routes(
    store: StateStore,
    dns: DnsProvider,
    target: DnsTarget,
    query: Callable[[str, float], tuple[str, ...]],
    checker: HttpMonitor,
    *,
    clock: Callable[[], float] = time.monotonic,
    vantage: str = "local",
) -> MonitorReport:
    if vantage not in {"local", "github_runner"}:
        raise SafetyBlocked("Invalid monitor vantage")
    checked_at = datetime.now(UTC).isoformat()
    state = store.load(target.instance_id)
    if (
        state is None
        or state.schema_version != 2
        or state.profile_id != target.instance_id
        or state.target_id != target.instance_id
        or state.target_fingerprint != target_fingerprint(target)
        or not 1 <= len(state.managed_records) <= 100
        or state.journal is not None
        and state.journal.phase not in {"verified", "rolled_back"}
    ):
        raise SafetyBlocked("Monitor requires bound ownership and a closed journal")
    owned = tuple(sorted(state.managed_records, key=lambda r: r.domain))
    if len({r.domain for r in owned}) != len(owned) or len({r.record_id for r in owned}) != len(
        owned
    ):
        raise SafetyBlocked("Duplicate monitoring target")
    for record in owned:
        validate_desired(DesiredRewrite(record.domain, record.content))
    before = dns.list_records()
    if len({r.record_id for r in before}) != len(before) or any(
        DnsRecord(r.record_id, r.domain, r.content) not in before for r in owned
    ):
        raise SafetyBlocked("Owned DNS differs from saved state")
    deadline = clock() + 30

    def observe(record: ManagedRecord) -> RouteObservation:
        dns_started = clock()
        answers: tuple[str, ...] = ()
        dns_ok: bool | None = None
        dns_classification = "not_checked"
        left = deadline - clock()
        if left > 0:
            try:
                answers = query(record.domain, min(4.0, left))
                dns_ok = answers == (record.content,)
                dns_classification = "confirmed" if dns_ok else "mismatch"
            except Exception:
                dns_classification = "dns_error"
        dns_elapsed_ms = round(max(0.0, clock() - dns_started) * 1000, 2)
        left = deadline - clock()
        http = (
            checker.probe(record.domain, record.content, timeout_s=min(4.0, left))
            if left > 0
            else HttpObservation(datetime.now(UTC).isoformat(), False, None, "not_checked", 0)
        )
        return RouteObservation(
            record.domain, record.content, dns_ok, dns_classification, http, answers, dns_elapsed_ms
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        routes = tuple(executor.map(observe, owned))
    after = dns.list_records()
    unchanged = sorted(before, key=lambda r: r.record_id) == sorted(
        after, key=lambda r: r.record_id
    )
    issues = () if unchanged and store.load(target.instance_id) == state else ("snapshot_changed",)
    return MonitorReport(checked_at, vantage, routes, issues)


def public_summary(report: MonitorReport) -> dict[str, object]:
    counts = Counter(
        r.http.classification if r.http.classification in HTTP_CLASSES else "observation_error"
        for r in report.routes
    )
    incomplete = bool(report.issues) or not report.routes or counts["not_checked"] > 0
    warning = any(
        r.dns_ok is not True or r.http.classification != "response" for r in report.routes
    )
    return {
        "kind": "route_monitor",
        "checked_at": report.checked_at,
        "vantage": report.vantage,
        "status": "incomplete"
        if incomplete
        else "observed_with_warnings"
        if warning
        else "observed",
        "routes": len(report.routes),
        "dns_successful": sum(r.dns_ok is True for r in report.routes),
        "tls_successful": sum(r.http.tls_ok for r in report.routes),
        "http": dict(sorted(counts.items())),
        "dns_operations": 0,
        "state_writes": 0,
        "issues": [
            code for code in report.issues if code in {"snapshot_changed", "collection_failed"}
        ],
    }


def failover_needed(report: MonitorReport) -> bool:
    from smartgate.failover import mass_failure

    if report.issues or not report.routes or any(r.dns_ok is not True for r in report.routes):
        return False
    failed = sum(
        not r.http.tls_ok and r.http.classification in {"timeout", "connection_error"}
        for r in report.routes
    )
    return mass_failure(failed, len(report.routes))


def write_reports(
    report: MonitorReport, directory: Path, cipher: EnvelopeCipher, binding: EnvelopeBinding
) -> None:
    if binding.purpose != "report":
        raise SafetyBlocked("Invalid monitoring report binding")
    detailed = json.dumps(asdict(report), ensure_ascii=True, indent=2).encode("utf-8")
    encrypted = cipher.seal(detailed, binding, max_plaintext=MAX_PLAINTEXT)
    public = public_summary(report)
    http_counts = public["http"]
    assert isinstance(http_counts, dict)
    lines = [
        "# SmartGate route monitor",
        "",
        "Status: " + str(public["status"]),
        "",
        "Observation point: " + report.vantage,
        "",
        f"DNS answers confirmed: {public['dns_successful']} / {public['routes']}",
        f"TLS connections verified: {public['tls_successful']} / {public['routes']}",
        "",
        "DNS operations: 0. State writes: 0.",
        "",
        "| HTTP observation | Count |",
        "| --- | --- |",
        *(f"| {name} | {count} |" for name, count in http_counts.items()),
        "",
        "Anonymous probes do not verify browser login or account access.",
        "Challenges and HTTP rejections are observations; they do not trigger address changes.",
        "Detailed domains and addresses are available only in monitor.enc.json.",
        "",
    ]
    if public["status"] != "observed":
        lines += [
            "> [!WARNING]",
            "> Review observations; this is not proof of service availability.",
            "",
        ]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "monitor.enc.json").write_bytes(encrypted)
    (directory / "monitor.json").write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    (directory / "monitor.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only route monitoring")
    parser.add_argument("--config", type=Path, default=Path("config/smartgate.actions.toml"))
    parser.add_argument("--report-dir", type=Path, default=Path(".smartgate/monitor"))
    args = parser.parse_args(argv)
    values = dict(os.environ if env is None else env)
    if values.get("GITHUB_ACTIONS") == "true" and (
        not values.get("SMARTGATE_DEFAULT_BRANCH")
        or values.get("GITHUB_REF") != "refs/heads/" + values["SMARTGATE_DEFAULT_BRANCH"]
    ):
        print("Monitor must run on the default branch")
        return 4
    for name in ("NEXTDNS_API_KEY", "NEXTDNS_PROFILE_ID"):
        if values.get(name) == "":
            values.pop(name)
    try:
        cipher = EnvelopeCipher(
            parse_encryption_key(values.get("SMARTGATE_STATE_ENCRYPTION_KEY", ""))
        )
        binding = binding_from_env(values)
    except Exception:
        print("Monitor encryption context is unavailable; no detailed report was written")
        return 4
    code = 0
    try:
        config = load_config(args.config, values)
        if config.state.provider != "git_branch" or config.dns.legacy:
            raise SafetyBlocked("Monitor requires bound encrypted Git state")
        with ExitStack() as stack:
            bundle = create_dns_bundle(config.dns, values, stack)
            github = GitHubClient(values.get("GITHUB_TOKEN", ""))
            stack.callback(github.close)
            store = GitBranchStateStore(
                values.get("GITHUB_REPOSITORY", ""),
                config.state.branch,
                values.get("GITHUB_TOKEN", ""),
                github,
                codec=EncryptedStateCodec(
                    cipher, values.get("GITHUB_REPOSITORY", ""), config.state.branch
                ),
            )
            verifier = DnsVerifier(bundle.target.doh_url)
            stack.callback(verifier.close)
            report = observe_routes(
                store,
                bundle.provider,
                bundle.target,
                lambda domain, timeout: verifier.query(domain, "A", timeout_s=timeout),
                HttpMonitor(),
                vantage="github_runner" if values.get("GITHUB_ACTIONS") == "true" else "local",
            )
    except Exception:
        report = MonitorReport(
            datetime.now(UTC).isoformat(),
            "github_runner" if values.get("GITHUB_ACTIONS") == "true" else "local",
            issues=("collection_failed",),
        )
        code = 4
    try:
        write_reports(report, args.report_dir, cipher, binding)
        if values.get("GITHUB_OUTPUT"):
            with Path(values["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(
                    "failover_needed=" + str(not code and failover_needed(report)).lower() + "\n"
                )
    except Exception:
        print("Monitor report could not be safely written")
        return 5
    print("Monitoring report saved; DNS operations: 0; state writes: 0")
    if not code and public_summary(report)["status"] != "observed":
        print("::warning::Monitoring collected warnings; see aggregate and encrypted observations.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
