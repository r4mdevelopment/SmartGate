"""Bounded anonymous HTTP observations over verified, numeric-IP TLS connections."""

import ipaddress
import math
import re
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from smartgate.catalog import normalize_domain
from smartgate.errors import ConfigError
from smartgate.health.tls import dial_ip

HEADER_LIMIT = 16384


@dataclass(frozen=True)
class HttpObservation:
    checked_at: str
    tls_ok: bool
    http_status: int | None
    classification: str
    elapsed_ms: float


def classify_headers(data: bytes) -> tuple[int, str]:
    lines = data.split(b"\r\n")
    match = re.fullmatch(rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [\x20-\x7e]*)?", lines[0])
    if match is None:
        raise ValueError
    status = int(match[1])
    challenge = False
    seen_challenge = False
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        if (
            not separator
            or re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None
            or any(c < 32 and c != 9 or c == 127 for c in value)
        ):
            raise ValueError
        if name.lower() == b"cf-mitigated":
            if seen_challenge:
                raise ValueError
            seen_challenge = True
            challenge = value.strip().lower() == b"challenge"
    if status < 200:
        return status, "informational"
    if challenge:
        return status, "challenge"
    if status < 400:
        return status, "response"
    if status == 401:
        return status, "authorization_required"
    if status == 429:
        return status, "rate_limited"
    return status, "server_error" if status >= 500 else "http_rejected"


class HttpMonitor:
    def __init__(
        self,
        *,
        dialer: Callable[..., socket.socket] = dial_ip,
        context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dialer, self._context_factory, self._clock = dialer, context_factory, clock

    def probe(self, domain: str, ip: str, *, timeout_s: float = 4.0) -> HttpObservation:
        started = self._clock()
        deadline = started + timeout_s
        tls_ok = False

        def outcome(classification: str, status: int | None = None) -> HttpObservation:
            return HttpObservation(
                datetime.now(UTC).isoformat(),
                tls_ok,
                status,
                classification,
                round(max(0.0, self._clock() - started) * 1000, 2),
            )

        def remaining() -> float:
            left = deadline - self._clock()
            if left <= 0:
                raise TimeoutError
            return left

        try:
            address = ipaddress.ip_address(ip)
            if (
                normalize_domain(domain) != domain
                or address.version != 4
                or not address.is_global
                or address.is_multicast
                or address.is_reserved
                or isinstance(timeout_s, bool)
                or not math.isfinite(timeout_s)
                or not 0 < timeout_s <= 4
            ):
                return outcome("invalid_target")
            context = self._context_factory()
            if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                return outcome("invalid_target")
            with self._dialer((str(address), 443), timeout=remaining()) as raw:
                raw.settimeout(remaining())
                with context.wrap_socket(raw, server_hostname=domain) as secure:
                    if secure.version() is None:
                        raise ssl.SSLError
                    remaining()
                    tls_ok = True
                    secure.settimeout(remaining())
                    secure.sendall(
                        (
                            f"GET / HTTP/1.1\r\nHost: {domain}\r\n"
                            "User-Agent: SmartGate-Monitor/0.1\r\nAccept: */*\r\n"
                            "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
                        ).encode("ascii")
                    )
                    received = bytearray()
                    header_bytes = 0
                    interim = 0
                    while True:
                        end = received.find(b"\r\n\r\n")
                        if end >= 0:
                            header_bytes += end + 4
                            if header_bytes > HEADER_LIMIT:
                                return outcome("invalid_response")
                            status, classification = classify_headers(bytes(received[:end]))
                            if classification == "informational":
                                interim += 1
                                if status == 101 or interim > 4:
                                    return outcome("invalid_response")
                                del received[: end + 4]
                                continue
                            return outcome(classification, status)
                        if header_bytes + len(received) >= HEADER_LIMIT:
                            return outcome("invalid_response")
                        secure.settimeout(remaining())
                        chunk = secure.recv(
                            min(2048, HEADER_LIMIT + 1 - header_bytes - len(received))
                        )
                        remaining()
                        if not chunk:
                            return outcome("invalid_response")
                        received.extend(chunk)
            return outcome("invalid_response")
        except ssl.SSLCertVerificationError:
            return outcome("certificate_error")
        except (ConfigError, ValueError):
            return outcome("invalid_response" if tls_ok else "invalid_target")
        except TimeoutError:
            return outcome("timeout")
        except (ssl.SSLError, OSError):
            return outcome("connection_error")
        except Exception:
            return outcome("observation_error")
