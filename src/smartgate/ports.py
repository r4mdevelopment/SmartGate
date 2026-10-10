"""Interfaces implemented by external adapters; the core imports only these ports."""

from datetime import datetime
from typing import Protocol

from smartgate.models import (
    DesiredRewrite,
    DnsRecord,
    ProbePolicy,
    ProbeResult,
    ProviderConfig,
    RelayCandidate,
    RelaySnapshot,
    StateDocument,
)


class RelayProvider(Protocol):
    def fetch(self, config: ProviderConfig, now: datetime) -> RelaySnapshot: ...


class HealthChecker(Protocol):
    def probe(self, candidate: RelayCandidate, policy: ProbePolicy) -> ProbeResult: ...


class DnsProvider(Protocol):
    def list_records(self) -> tuple[DnsRecord, ...]: ...
    def create(self, record: DesiredRewrite) -> DnsRecord: ...
    def delete(self, record: DnsRecord) -> None: ...
    def replace(self, before: DnsRecord, after: DesiredRewrite) -> DnsRecord: ...


class StateStore(Protocol):
    def load(self, profile_id: str) -> StateDocument | None: ...
    def save(self, state: StateDocument, expected_revision: str | None) -> str: ...
