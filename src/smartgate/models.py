"""Immutable values shared by the core and adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

ProbeStatus = Literal["tls_ok", "failed", "unknown"]
RouteAction = Literal["select", "retain", "unavailable", "remove"]
OperationKind = Literal["create", "replace", "delete"]
JournalPhase = Literal["prepared", "applying", "verified", "partial", "rolled_back"]
ReportStatus = Literal["preview", "no_change", "applied", "degraded", "blocked", "partial"]


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    type: Literal["geohide", "selfhosted"]
    region: str | None = None
    manifest_path: Path | None = None
    priority: int = 0


@dataclass(frozen=True)
class ProbePolicy:
    attempts: int = 2
    timeout_s: float = 4.0
    concurrency: int = 8
    max_candidates_per_domain: int = 2
    max_pairs: int = 200


@dataclass(frozen=True)
class ServicePolicy:
    id: str
    enabled: bool
    include_suffixes: tuple[str, ...]
    required_domains: tuple[str, ...]
    exclude_suffixes: tuple[str, ...]
    provider_order: tuple[str, ...]
    allow_subtree_rewrites: bool = False


@dataclass(frozen=True)
class DnsConnectionConfig:
    api_url_env: str | None = None
    namespace_env: str | None = None


@dataclass(frozen=True)
class DnsVerifyConfig:
    doh_url_env: str | None = None


@dataclass(frozen=True)
class DnsAuthConfig:
    kind: Literal["api_key", "basic", "bearer"]
    api_key_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None


@dataclass(frozen=True)
class DnsCredentials:
    api_key: str | None = field(default=None, repr=False)
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DnsConfig:
    provider: str = "nextdns"
    profile_id_env: str = "SMARTGATE_DNS_PROFILE_ID"
    api_key_env: str = "SMARTGATE_DNS_API_KEY"
    instance_id: str = "primary"
    connection: DnsConnectionConfig = DnsConnectionConfig()
    auth: DnsAuthConfig = DnsAuthConfig("api_key", api_key_env="SMARTGATE_DNS_API_KEY")
    verify: DnsVerifyConfig = DnsVerifyConfig()
    legacy: bool = True


@dataclass(frozen=True)
class StateConfig:
    provider: Literal["local", "git_branch"] = "local"
    branch: str = "smartgate-state"
    path: Path = Path(".smartgate/state.json")


@dataclass(frozen=True)
class SyncPolicy:
    enabled: bool = False
    allow_prune: bool = False
    max_records: int = 100
    max_mutations: int = 20
    max_change_ratio: float = 0.25


@dataclass(frozen=True)
class RelayCandidate:
    provider_id: str
    domain: str
    ip: str
    port: int
    transport: str
    priority: int
    source_id: str
    source_region: str | None


@dataclass(frozen=True)
class RelaySnapshot:
    provider_id: str
    fetched_at: datetime
    source_digest: str
    candidates: tuple[RelayCandidate, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ProbeResult:
    candidate: RelayCandidate
    status: ProbeStatus
    checked_at: datetime
    vantage: str
    latency_ms: float | None
    reason: str


@dataclass(frozen=True)
class RouteDecision:
    domain: str
    candidate: RelayCandidate | None
    action: RouteAction
    reason: str


@dataclass(frozen=True)
class DesiredRewrite:
    domain: str
    ipv4: str


@dataclass(frozen=True)
class DnsRecord:
    record_id: str
    domain: str
    content: str


@dataclass(frozen=True)
class ManagedRecord:
    record_id: str
    domain: str
    content: str
    provider_id: str
    last_verified_at: datetime


@dataclass(frozen=True)
class Selection:
    domain: str
    candidate: RelayCandidate
    routing_policy_digest: str


@dataclass(frozen=True)
class ProbeCursor:
    domain: str
    index: int


@dataclass(frozen=True)
class Operation:
    kind: OperationKind
    domain: str
    before: DnsRecord | None
    after: DesiredRewrite | None


@dataclass(frozen=True)
class ChangePlan:
    plan_id: str
    config_digest: str
    dns_digest: str
    state_revision: str | None
    created_at: datetime
    operations: tuple[Operation, ...]
    blocked_reasons: tuple[str, ...]
    policy: PlanContext | None = None
    desired: tuple[DesiredRewrite, ...] = ()
    removals: tuple[str, ...] = ()
    target_id: str = ""
    target_fingerprint: str = ""


@dataclass(frozen=True)
class RunJournal:
    run_id: str
    plan: ChangePlan
    phase: JournalPhase
    operation_statuses: tuple[str, ...]
    before_snapshot: tuple[DnsRecord, ...] = ()
    operation_results: tuple[DnsRecord | None, ...] = ()
    before_managed: tuple[ManagedRecord, ...] = ()
    before_selections: tuple[Selection, ...] = ()
    target_selections: tuple[Selection, ...] = ()
    rollback_started: bool = False
    execution_run_id: str = ""
    execution_deadline: datetime | None = None
    target_id: str = ""
    target_fingerprint: str = ""


@dataclass(frozen=True)
class StateDocument:
    schema_version: int
    profile_id: str
    revision: str | None
    managed_records: tuple[ManagedRecord, ...] = ()
    last_sources: tuple[RelaySnapshot, ...] = ()
    selections: tuple[Selection, ...] = ()
    journal: RunJournal | None = None
    probe_cursors: tuple[ProbeCursor, ...] = ()
    target_id: str = ""
    target_fingerprint: str = ""


@dataclass(frozen=True)
class DnsCheck:
    domain: str
    qtype: str
    answers: tuple[str, ...]
    expected: tuple[str, ...]
    ok: bool
    reason: str
    checked_at: datetime


@dataclass(frozen=True)
class ServiceAvailability:
    service_id: str
    available: int
    total: int
    required_ok: bool


@dataclass(frozen=True)
class RunReport:
    run_id: str
    status: ReportStatus
    counts: tuple[tuple[str, int], ...]
    decisions: tuple[RouteDecision, ...]
    checks: tuple[DnsCheck, ...]
    errors: tuple[str, ...]
    plan: ChangePlan | None = None
    sources: tuple[RelaySnapshot, ...] = ()
    probes: tuple[ProbeResult, ...] = ()
    journal_phase: str | None = None
    journal_run_id: str | None = None
    offline: bool = False
    exit_code_override: int | None = None
    dns_provider: str | None = None
    dns_readiness: str = "unknown"
    optional_route_warnings: bool = False
    availability: tuple[ServiceAvailability, ...] = ()
    issue_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanContext:
    config_digest: str
    created_at: datetime
    max_records: int = 100
    max_mutations: int = 20
    max_change_ratio: float = 0.25
    allow_prune: bool = False
    bootstrap: bool = False
    subtree_capabilities: bool = False
    services: tuple[ServicePolicy, ...] = ()


@dataclass(frozen=True)
class ApplyContext:
    now: Callable[[], datetime]
    deadline: datetime
    run_id: str
    profile_id: str
    selections: tuple[Selection, ...] = ()
    verify: Callable[[str, tuple[DesiredRewrite, ...]], tuple[DnsCheck, ...]] | None = None
    config_digest: str | None = None
    allow_recovery_takeover: bool = False
    target_id: str = ""
    target_fingerprint: str = ""
