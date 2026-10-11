import ssl
from importlib import import_module

import pytest


def module():
    return import_module("smartgate.health.http")


class Connection:
    def __init__(self, response=b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"):
        self.response = response
        self.sent = b""
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, value):
        assert 0 < value <= 4

    def version(self):
        return "TLSv1.3"

    def sendall(self, value):
        self.sent += value

    def recv(self, size):
        part, self.response = self.response[:size], self.response[size:]
        return part


class Context:
    verify_mode = ssl.CERT_REQUIRED
    check_hostname = True

    def __init__(self, secure, error=None):
        self.secure = secure
        self.error = error
        self.names = []

    def wrap_socket(self, raw, *, server_hostname):
        self.names.append(server_hostname)
        if self.error:
            raise self.error
        return self.secure


def checker(response=b"HTTP/1.1 200 OK\r\n\r\n", error=None):
    raw, secure = Connection(), Connection(response)
    context = Context(secure, error)
    calls = []

    def dial(address, *, timeout):
        calls.append((address, timeout))
        return raw

    return module().HttpMonitor(dialer=dial, context_factory=lambda: context), context, calls, raw


def test_http_dials_numeric_ip_with_verified_sni_and_no_credentials():
    h, context, calls, raw = checker()
    r = h.probe("example.com", "8.8.8.8")
    assert calls == [(("8.8.8.8", 443), 4.0)]
    assert context.names == ["example.com"]
    assert context.secure.sent.startswith(b"GET / HTTP/1.1\r\nHost: example.com\r\n")
    assert b"Authorization:" not in context.secure.sent and b"Cookie:" not in context.secure.sent
    assert r.tls_ok and r.http_status == 200 and r.classification == "response"
    assert raw.closed and context.secure.closed


@pytest.mark.parametrize(
    "status,classification",
    [
        (302, "response"),
        (401, "authorization_required"),
        (403, "http_rejected"),
        (429, "rate_limited"),
        (500, "server_error"),
        (503, "server_error"),
    ],
)
def test_http_status_is_observation_without_following_redirects(status, classification):
    h, context, calls, _ = checker(
        f"HTTP/1.1 {status} Response\r\nLocation: http://127.0.0.1/private\r\n\r\n".encode()
    )
    r = h.probe("example.com", "8.8.8.8")
    assert r.http_status == status and r.classification == classification and r.tls_ok
    assert len(calls) == 1 and context.secure.sent.count(b"GET ") == 1


def test_cloudflare_challenge_is_distinct_from_transport_failure():
    h, _, _, _ = checker(b"HTTP/1.1 403 Forbidden\r\nCF-Mitigated: challenge\r\n\r\n")
    r = h.probe("example.com", "8.8.8.8")
    assert r.classification == "challenge" and r.tls_ok and r.http_status == 403


@pytest.mark.parametrize(
    "domain,ip",
    [
        ("example.com\r\nCookie: secret", "8.8.8.8"),
        ("example.com", "127.0.0.1"),
        ("example.com", "10.0.0.1"),
        ("example.com", "relay.example.com"),
    ],
)
def test_invalid_targets_make_no_network_calls(domain, ip):
    h, _, calls, _ = checker()
    assert h.probe(domain, ip).classification == "invalid_target"
    assert calls == []


def test_insecure_context_never_dials():
    h, context, calls, _ = checker()
    context.check_hostname = False
    assert h.probe("example.com", "8.8.8.8").classification == "invalid_target"
    assert calls == []


def test_certificate_failure_has_no_private_exception_text():
    h, _, _, raw = checker(error=ssl.SSLCertVerificationError("private-token"))
    r = h.probe("example.com", "8.8.8.8")
    assert r.classification == "certificate_error" and not r.tls_ok
    assert "private-token" not in repr(r) and raw.closed


def test_slow_connection_uses_one_total_deadline_and_no_retry():
    elapsed = [0.0]
    raw = Connection()
    calls = []

    def dial(address, *, timeout):
        calls.append(address)
        elapsed[0] += 4
        return raw

    h = module().HttpMonitor(dialer=dial, clock=lambda: elapsed[0])
    assert h.probe("example.com", "8.8.8.8").classification == "timeout"
    assert len(calls) == 1 and raw.closed


@pytest.mark.parametrize(
    "response",
    [
        b"HTTP/1.1 200 OK\r\nX: " + b"a" * 17000,
        b"garbage\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nBad header\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nCF-Mitigated: challenge\r\nCF-Mitigated: other\r\n\r\n",
    ],
)
def test_malformed_or_oversized_headers_are_not_success(response):
    h, _, _, _ = checker(response)
    r = h.probe("example.com", "8.8.8.8")
    assert r.classification == "invalid_response" and r.tls_ok


def test_only_headers_are_retained_not_cookies_or_body():
    h, _, _, _ = checker(b"HTTP/1.1 200 OK\r\nSet-Cookie: private-secret\r\n\r\nprivate-body")
    r = h.probe("example.com", "8.8.8.8")
    assert "private-secret" not in repr(r) and "private-body" not in repr(r)
    assert r.classification == "response"


def test_bounded_early_hints_are_skipped_without_new_requests():
    h, context, calls, _ = checker(
        b"HTTP/1.1 103 Early Hints\r\nLink: </style.css>\r\n\r\nHTTP/1.1 200 OK\r\n\r\n"
    )
    r = h.probe("example.com", "8.8.8.8")
    assert r.http_status == 200 and r.classification == "response"
    assert len(calls) == 1 and context.secure.sent.count(b"GET ") == 1


def test_unbounded_informational_responses_are_rejected():
    h, _, _, _ = checker(b"HTTP/1.1 103 Early Hints\r\n\r\n" * 5)
    assert h.probe("example.com", "8.8.8.8").classification == "invalid_response"
