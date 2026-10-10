from dataclasses import replace
from datetime import UTC, datetime, timedelta

from smartgate.errors import RecoveryRequired, StateConflict
from smartgate.models import (
    ApplyContext,
    DnsCheck,
    DnsRecord,
    ManagedRecord,
    PlanContext,
    RelayCandidate,
    Selection,
    ServicePolicy,
    StateDocument,
)

NOW = datetime(2026, 10, 8, tzinfo=UTC)


class FakeDns:
    def __init__(self, records=(), events=None):
        self.records = list(records)
        self.events = events if events is not None else []
        self.writes = 0
        self.sequence = 0
        self.mode = None

    def list_records(self):
        return tuple(self.records)

    def create(self, desired):
        self.events.append("dns:create")
        self.writes += 1
        self.sequence += 1
        result = DnsRecord("new-" + str(self.sequence), desired.domain, desired.ipv4)
        self.records.append(result)
        if self.mode == "timeout_create":
            self.mode = None
            raise RecoveryRequired("unknown")
        return result

    def delete(self, before):
        self.events.append("dns:delete")
        self.writes += 1
        assert before in self.records
        self.records.remove(before)

    def replace(self, before, after):
        self.delete(before)
        if self.mode == "kill_after_delete":
            self.mode = None
            raise KeyboardInterrupt()
        return self.create(after)


class MemoryStore:
    def __init__(self, state=None, events=None):
        self.state = state
        self.events = events if events is not None else []
        self.revision = 0
        self.fail_at = None
        self.saves = 0

    def load(self, profile_id):
        return self.state

    def save(self, state, expected_revision):
        self.events.append("store:" + (state.journal.phase if state.journal else "metadata"))
        self.saves += 1
        if self.saves == self.fail_at:
            raise StateConflict("simulated unavailable store")
        current = self.state.revision if self.state else None
        if current != expected_revision:
            raise StateConflict("CAS")
        self.revision += 1
        revision = str(self.revision)
        self.state = replace(state, revision=revision)
        return revision


def owned(domain="chatgpt.com", ip="8.8.8.8"):
    record = DnsRecord("owned-id", domain, ip)
    managed = ManagedRecord(record.record_id, domain, ip, "relay", NOW)
    selection = target(domain, ip)
    return record, StateDocument(1, "abcdef", None, (managed,), selections=(selection,))


def target(domain, ip, provider="relay"):
    return Selection(
        domain,
        RelayCandidate(provider, domain, ip, 443, "tls_sni_tcp", 0, "fixture", None),
        "policy",
    )


def policy(**values):
    domains = ("chatgpt.com", "new.example", "a.example", "b.example") + tuple(
        f"d{i}.example" for i in range(101)
    )
    services = (ServicePolicy("fixture", True, domains, (), (), ("relay", "own"), True),)
    return replace(
        PlanContext("config", NOW, subtree_capabilities=True, services=services), **values
    )


def verify_ok(profile, desired):
    return tuple(
        DnsCheck(
            r.domain,
            qtype,
            (r.ipv4,) if qtype == "A" else (),
            (r.ipv4,) if qtype == "A" else (),
            True,
            "fixture",
            NOW,
        )
        for r in desired
        for qtype in ("A", "AAAA", "HTTPS", "SVCB")
    )


def context(desired=(), verify=verify_ok, **values):
    return replace(
        ApplyContext(
            lambda: NOW,
            NOW + timedelta(minutes=20),
            "run",
            "abcdef",
            selections=tuple(target(r.domain, r.ipv4) for r in desired),
            verify=verify,
            config_digest="config",
        ),
        **values,
    )
