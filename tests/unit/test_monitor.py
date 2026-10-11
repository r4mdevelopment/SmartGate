import json
from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module

import pytest

from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.errors import SafetyBlocked
from smartgate.health.http import HttpObservation
from smartgate.models import DnsRecord, ManagedRecord, StateDocument
from smartgate.private_report import decrypt_report, report_binding
from smartgate.security.envelope import EnvelopeCipher

NOW = datetime(2026, 10, 11, tzinfo=UTC)
TARGET = DnsTarget(
    "primary", "nextdns", "https://api.nextdns.io", "abcdef", "https://dns.nextdns.io/abcdef"
)
RECORD = ManagedRecord("owned", "example.com", "8.8.8.8", "relay", NOW)
STATE = StateDocument(
    2,
    "primary",
    "revision",
    (RECORD,),
    target_id="primary",
    target_fingerprint=target_fingerprint(TARGET),
)


def module():
    return import_module("smartgate.monitor")


class Store:
    def __init__(self, state=STATE, after=None):
        self.state, self.after, self.loads = state, after, 0

    def load(self, key):
        assert key == "primary"
        self.loads += 1
        return self.after if self.loads > 1 and self.after else self.state

    def save(self, *args):
        pytest.fail("Monitoring must never save state")


class Dns:
    def __init__(self, records=None):
        self.records = (
            records if records is not None else (DnsRecord("owned", "example.com", "8.8.8.8"),)
        )
        self.loads = 0

    def list_records(self):
        self.loads += 1
        return self.records

    def create(self, *args):
        pytest.fail("Monitoring must never create DNS")

    delete = replace = create


class Checker:
    def __init__(self, classification="response", tls=True, status=200):
        self.calls = []
        self.classification, self.tls, self.status = classification, tls, status

    def probe(self, domain, ip, *, timeout_s=4):
        self.calls.append((domain, ip))
        return HttpObservation(NOW.isoformat(), self.tls, self.status, self.classification, 12)


def observe(store=None, dns=None, checker=None, query=None, **kwargs):
    return module().observe_routes(
        store or Store(),
        dns or Dns(),
        TARGET,
        query or (lambda domain, timeout: ("8.8.8.8",)),
        checker or Checker(),
        **kwargs,
    )


def test_monitor_reads_owned_routes_only_and_never_writes():
    store, dns, checker = Store(), Dns(), Checker()
    dns.records += (DnsRecord("foreign", "foreign.example.com", "1.1.1.1"),)
    r = observe(store, dns, checker)
    assert checker.calls == [("example.com", "8.8.8.8")]
    assert store.loads == dns.loads == 2
    assert not r.issues and r.routes[0].dns_ok and r.routes[0].http.tls_ok


@pytest.mark.parametrize(
    "state",
    [
        None,
        replace(STATE, schema_version=1),
        replace(STATE, target_fingerprint="f" * 64),
        replace(STATE, managed_records=()),
        replace(STATE, managed_records=(RECORD, RECORD)),
    ],
)
def test_invalid_state_is_blocked_before_endpoint_probes(state):
    c = Checker()
    with pytest.raises(SafetyBlocked):
        observe(Store(state), checker=c)
    assert c.calls == []


def test_owned_dns_drift_does_not_probe_a_substituted_ip():
    c = Checker()
    with pytest.raises(SafetyBlocked):
        observe(dns=Dns((DnsRecord("owned", "example.com", "1.1.1.1"),)), checker=c)
    assert c.calls == []


def test_state_change_during_monitoring_is_reported():
    r = observe(Store(after=replace(STATE, revision="changed")))
    assert r.issues == ("snapshot_changed",)


def test_dns_mismatch_and_challenge_are_observations_not_mutations():
    r = observe(checker=Checker("challenge", True, 403), query=lambda *args: ("1.1.1.1",))
    assert not r.routes[0].dns_ok
    assert r.routes[0].dns_answers == ("1.1.1.1",)
    assert r.routes[0].dns_elapsed_ms >= 0
    summary = module().public_summary(r)
    assert summary["status"] == "observed_with_warnings"
    assert summary["http"]["challenge"] == 1 and summary["dns_successful"] == 0
    assert summary["dns_operations"] == 0


def test_dns_exception_does_not_leak_private_details():
    def query(*args):
        raise RuntimeError("private-api-key")

    r = observe(query=query)
    assert r.routes[0].dns_ok is None and r.routes[0].dns_classification == "dns_error"
    assert "private-api-key" not in repr(r)


def test_expired_batch_budget_does_not_start_network_checks():
    c = Checker()
    times = iter([0, 31, 31, 31, 31])
    r = observe(checker=c, clock=lambda: next(times))
    assert c.calls == [] and r.routes[0].http.classification == "not_checked"
    assert module().public_summary(r)["status"] == "incomplete"


def test_report_contains_details_only_in_authenticated_encrypted_artifact(tmp_path):
    r = observe(checker=Checker("challenge", True, 403))
    cipher, binding = EnvelopeCipher(b"x" * 32), report_binding("owner/repo", "123", "1")
    module().write_reports(r, tmp_path, cipher, binding)
    for name in ("monitor.json", "monitor.md"):
        text = (tmp_path / name).read_text()
        assert "example.com" not in text and "8.8.8.8" not in text and "abcdef" not in text
    decrypted = decrypt_report((tmp_path / "monitor.enc.json").read_bytes(), cipher, binding)
    data = json.loads(decrypted)
    assert data["routes"][0]["domain"] == "example.com"
    assert data["routes"][0]["ip"] == "8.8.8.8"
    assert data["routes"][0]["http"]["classification"] == "challenge"
    with pytest.raises(SafetyBlocked):
        decrypt_report(
            (tmp_path / "monitor.enc.json").read_bytes(),
            cipher,
            report_binding("owner/repo", "124", "1"),
        )


def test_topic_branch_is_rejected_before_secret_clients(monkeypatch, tmp_path):
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF": "refs/heads/topic",
        "SMARTGATE_DEFAULT_BRANCH": "main",
    }
    assert module().main(["--report-dir", str(tmp_path)], env=env) == 4
    assert not (tmp_path / "monitor.enc.json").exists()
