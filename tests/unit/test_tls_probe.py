import ssl
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from smartgate.health.tls import TlsHealthChecker
from smartgate.models import ProbePolicy, RelayCandidate

NOW = datetime(2026, 10, 7, tzinfo=UTC)
CANDIDATE = RelayCandidate("gh", "chatgpt.com", "8.8.8.8", 443, "tls_sni_tcp", 0, "fixture", "RU")


class Connection:
    def __init__(self, version="TLSv1.3"):
        self.timeouts = []
        self.closed = False
        self._version = version

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def version(self):
        return self._version


class Context:
    verify_mode = ssl.CERT_REQUIRED
    check_hostname = True

    def __init__(self, error=None, secure=None):
        self.error = error
        self.secure = secure or Connection()
        self.names = []

    def wrap_socket(self, raw, *, server_hostname):
        self.names.append(server_hostname)
        if self.error:
            raise self.error
        return self.secure


def test_probe_dials_candidate_ip_and_verifies_domain_certificate():
    calls = []
    raw = Connection()
    context = Context()
    elapsed = [0.0]

    def dial(address, *, timeout):
        calls.append((address, timeout))
        elapsed[0] += 1.0
        return raw

    checker = TlsHealthChecker(
        dialer=dial,
        context_factory=lambda: context,
        clock=lambda: elapsed[0],
        now=lambda: NOW,
        vantage="local",
    )
    result = checker.probe(CANDIDATE, ProbePolicy())
    assert calls == [(("8.8.8.8", 443), 4.0)]
    assert raw.timeouts == [3.0]
    assert context.names == ["chatgpt.com"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert result.status == "tls_ok" and result.vantage == "local"
    assert result.latency_ms == 1000.0 and result.checked_at == NOW
    assert raw.closed and context.secure.closed


@pytest.mark.parametrize("message", ["wrong hostname", "expired certificate"])
def test_wrong_or_expired_certificate_is_failed(message):
    context = Context(error=ssl.SSLCertVerificationError(message))
    checker = TlsHealthChecker(
        dialer=lambda *a, **k: Connection(), context_factory=lambda: context, now=lambda: NOW
    )
    result = checker.probe(CANDIDATE, ProbePolicy())
    assert result.status == "failed" and result.latency_ms is None
    assert result.reason == "certificate validation failed"
    assert len(context.names) == 2


def test_two_timeouts_never_exceed_two_attempt_budgets():
    elapsed = [0.0]
    calls = []

    def dial(address, *, timeout):
        calls.append(timeout)
        elapsed[0] += timeout
        raise TimeoutError("private diagnostic")

    checker = TlsHealthChecker(dialer=dial, clock=lambda: elapsed[0], now=lambda: NOW)
    result = checker.probe(CANDIDATE, ProbePolicy())
    assert result.status == "failed" and result.reason == "connection timed out"
    assert calls == [4.0, 4.0] and elapsed[0] == 8.0
    assert "private diagnostic" not in result.reason


def test_handshake_uses_only_remaining_connect_budget():
    elapsed = [0.0]
    raw = Connection()

    def dial(address, *, timeout):
        elapsed[0] += 4.0
        return raw

    context = Context()
    checker = TlsHealthChecker(
        dialer=dial, context_factory=lambda: context, clock=lambda: elapsed[0], now=lambda: NOW
    )
    assert checker.probe(CANDIDATE, ProbePolicy()).status == "failed"
    assert context.names == [] and raw.closed


def test_unexpected_failure_is_unknown_and_not_blindly_retried():
    calls = []

    def dial(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("secret internal detail")

    result = TlsHealthChecker(dialer=dial).probe(CANDIDATE, ProbePolicy())
    assert result.status == "unknown" and len(calls) == 1
    assert "secret" not in result.reason


def test_tcp_success_without_a_tls_session_is_not_healthy():
    context = Context(secure=Connection(version=None))
    checker = TlsHealthChecker(dialer=lambda *a, **k: Connection(), context_factory=lambda: context)
    assert checker.probe(CANDIDATE, ProbePolicy()).status == "failed"


def test_insecure_context_is_rejected_before_connecting():
    context = Context()
    context.check_hostname = False
    checker = TlsHealthChecker(
        dialer=lambda *a, **k: pytest.fail("must not connect"), context_factory=lambda: context
    )
    assert checker.probe(CANDIDATE, ProbePolicy()).status == "unknown"


@pytest.mark.parametrize(
    "change",
    [
        {"ip": "chatgpt.com"},
        {"ip": "127.0.0.1"},
        {"port": 8443},
        {"transport": "socks"},
        {"domain": "https://chatgpt.com"},
    ],
)
def test_invalid_target_is_rejected_without_network(change):
    checker = TlsHealthChecker(dialer=lambda *a, **k: pytest.fail("must not connect"))
    assert checker.probe(replace(CANDIDATE, **change), ProbePolicy()).status == "unknown"
