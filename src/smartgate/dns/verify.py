"""Bounded endpoint-specific DoH observations; never changes client DNS."""

import time
from collections.abc import Callable
from datetime import UTC, datetime

import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import httpx

from smartgate.dns.validation import validate_desired, validate_endpoint
from smartgate.errors import DnsApiError, SafetyBlocked
from smartgate.models import DesiredRewrite, DnsCheck

QTYPES = ("A", "AAAA", "HTTPS", "SVCB")


class DnsVerifier:
    def __init__(
        self,
        doh_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        observation_s: float = 60,
        max_calls: int = 800,
    ) -> None:
        if not 0 <= observation_s <= 60 or type(max_calls) is not int or not 1 <= max_calls <= 800:
            raise SafetyBlocked("Invalid DNS observation budget")
        self._doh_url = validate_endpoint(doh_url)
        self._clock, self._sleep, self._now = clock, sleep, now
        self._observation_s, self._max_calls, self._calls = observation_s, max_calls, 0
        self._deadline = clock() + 1200
        self._client = httpx.Client(
            transport=transport, timeout=10, follow_redirects=False, trust_env=False
        )

    def close(self) -> None:
        self._client.close()

    def query(self, domain: str, qtype: str, *, timeout_s: float = 10) -> tuple[str, ...]:
        if qtype not in QTYPES:
            raise SafetyBlocked("Invalid DNS query")
        from smartgate.catalog import normalize_domain

        if normalize_domain(domain) != domain or not 0 < timeout_s <= 10:
            raise SafetyBlocked("Invalid DNS question")
        if self._calls >= self._max_calls or self._clock() >= self._deadline:
            raise SafetyBlocked("DNS verification request or duration budget exhausted")
        self._calls += 1
        request_deadline = min(self._deadline, self._clock() + timeout_s)
        question = dns.message.make_query(domain, qtype)
        try:
            with self._client.stream(
                "POST",
                self._doh_url,
                content=question.to_wire(),
                headers={
                    "Content-Type": "application/dns-message",
                    "Accept": "application/dns-message",
                },
                timeout=min(timeout_s, self._deadline - self._clock()),
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    != "application/dns-message"
                ):
                    raise DnsApiError("DoH response rejected")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 65535:
                        raise DnsApiError("DoH response exceeds DNS wire size")
                answer = dns.message.from_wire(bytes(content))
            if self._clock() > request_deadline:
                raise DnsApiError("DoH response exceeded its deadline")
            if (
                answer.id != question.id
                or not question.is_response(answer)
                or answer.rcode() != dns.rcode.NOERROR
                or answer.flags & dns.flags.TC
            ):
                raise DnsApiError("DoH response does not match a complete successful question")
            result: set[str] = set()
            for rrset in answer.answer:
                if rrset.rdtype == dns.rdatatype.RRSIG:
                    continue
                if (
                    rrset.name != question.question[0].name
                    or rrset.rdtype != question.question[0].rdtype
                ):
                    raise DnsApiError("DoH returned an unexpected answer owner or type")
                result.update(item.to_text() for item in rrset)
            return tuple(sorted(result))
        except (httpx.HTTPError, dns.exception.DNSException):
            raise DnsApiError("DNS request failed") from None

    def verify(self, desired: tuple[DesiredRewrite, ...]) -> tuple[DnsCheck, ...]:
        if len(desired) > 100 or len({r.domain for r in desired}) != len(desired):
            raise SafetyBlocked("DNS verification requires at most 100 unique domains")
        for record in desired:
            validate_desired(record)
        deadline = min(self._deadline, self._clock() + self._observation_s)
        pending = [(r, qtype) for r in sorted(desired, key=lambda r: r.domain) for qtype in QTYPES]
        checks: dict[tuple[str, str], DnsCheck] = {}
        first = True
        while pending and (first or self._clock() < deadline):
            first = False
            failed: list[tuple[DesiredRewrite, str]] = []
            for record, qtype in pending:
                expected = (record.ipv4,) if qtype == "A" else ()
                try:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise SafetyBlocked("DNS observation deadline exhausted")
                    answers = self.query(record.domain, qtype, timeout_s=min(10, remaining))
                    ok = answers == expected
                    reason = "DNS confirmed" if ok else "DNS answers differ from rewrite policy"
                except (DnsApiError, SafetyBlocked):
                    answers, ok, reason = (), False, "DNS observation failed"
                checks[(record.domain, qtype)] = DnsCheck(
                    record.domain, qtype, answers, expected, ok, reason, self._now()
                )
                if not ok:
                    failed.append((record, qtype))
            pending = failed
            if not pending or self._clock() >= deadline:
                break
            self._sleep(min(5, deadline - self._clock()))
        return tuple(
            checks[key] for key in sorted(checks, key=lambda k: (k[0], QTYPES.index(k[1])))
        )
