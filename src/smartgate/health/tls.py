"""Connect to a numeric relay IP, with TLS certificate verification for its domain."""

import ipaddress
import math
import socket
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime

from smartgate.catalog import normalize_domain
from smartgate.errors import ConfigError, SafetyBlocked
from smartgate.models import ProbePolicy, ProbeResult, ProbeStatus, RelayCandidate


def validate_probe_policy(policy: ProbePolicy) -> None:
    limits = (
        (policy.attempts, 2),
        (policy.concurrency, 8),
        (policy.max_candidates_per_domain, 2),
        (policy.max_pairs, 200),
    )
    if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in limits):
        raise SafetyBlocked("Probe policy exceeds the supported budget")
    if isinstance(policy.timeout_s, bool) or not isinstance(policy.timeout_s, int | float):
        raise SafetyBlocked("Probe timeout must be finite")
    if not 0 < policy.timeout_s <= 4 or not math.isfinite(policy.timeout_s):
        raise SafetyBlocked("Probe timeout exceeds the supported budget")


def dial_ip(address: tuple[str, int], *, timeout: float) -> socket.socket:
    # Numeric IPv4 only: no application hostname resolution can substitute the target.
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
        connection.connect(address)
        return connection
    except BaseException:
        connection.close()
        raise


class TlsHealthChecker:
    def __init__(
        self,
        *,
        dialer: Callable[..., socket.socket] = dial_ip,
        context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        vantage: str = "local",
    ) -> None:
        if vantage not in {"local", "github_runner"}:
            raise SafetyBlocked("Probe vantage must identify the actual controller")
        self._dialer = dialer
        self._context_factory = context_factory
        self._clock = clock
        self._now = now
        self._vantage = vantage

    def probe(self, candidate: RelayCandidate, policy: ProbePolicy) -> ProbeResult:
        validate_probe_policy(policy)

        def outcome(
            status: ProbeStatus,
            reason: str,
            latency: float | None = None,
        ) -> ProbeResult:
            return ProbeResult(candidate, status, self._now(), self._vantage, latency, reason)

        try:
            address = ipaddress.ip_address(candidate.ip)
            domain = normalize_domain(candidate.domain)
            if (
                address.version != 4
                or not address.is_global
                or address.is_multicast
                or address.is_reserved
                or candidate.port != 443
                or candidate.transport != "tls_sni_tcp"
            ):
                return outcome("unknown", "unsupported probe target")
            context = self._context_factory()
            if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                return outcome("unknown", "insecure TLS context rejected")
        except (ValueError, ConfigError):
            return outcome("unknown", "invalid probe target")
        except Exception:
            return outcome("unknown", "TLS context initialization failed")

        reason = "TLS connection failed"
        for _ in range(policy.attempts):
            started = self._clock()
            deadline = started + policy.timeout_s
            try:
                with self._dialer((str(address), 443), timeout=policy.timeout_s) as raw:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise TimeoutError()
                    raw.settimeout(remaining)
                    with context.wrap_socket(raw, server_hostname=domain) as secure:
                        if secure.version() is None:
                            raise ssl.SSLError("TLS session missing")
                        elapsed = self._clock() - started
                        if elapsed > policy.timeout_s:
                            raise TimeoutError()
                        return outcome("tls_ok", "verified TLS handshake", max(0.0, elapsed) * 1000)
            except ssl.SSLCertVerificationError:
                reason = "certificate validation failed"
            except TimeoutError:
                reason = "connection timed out"
            except (ssl.SSLError, OSError):
                reason = "TLS connection failed"
            except Exception:
                return outcome("unknown", "unexpected probe failure")
        return outcome("failed", reason)
