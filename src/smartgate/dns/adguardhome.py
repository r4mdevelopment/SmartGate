"""AdGuard Home reference adapter; value keys never prove lifecycle ownership."""

import hashlib
import json

from smartgate.catalog import normalize_domain
from smartgate.dns.transport import ApiClient
from smartgate.dns.validation import validate_desired
from smartgate.errors import ConfigError, DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.models import DesiredRewrite, DnsRecord


class AdGuardHomeProvider:
    def __init__(self, http: ApiClient, *, allow_reference_writes: bool = False) -> None:
        self._http, self._allow = http, allow_reference_writes
        self._entries: dict[str, dict[str, object]] = {}

    def close(self) -> None:
        self._http.close()

    def list_records(self) -> tuple[DnsRecord, ...]:
        data = self._http.request("GET", "/control/rewrite/list")
        if not isinstance(data, list) or len(data) > 10000:
            raise DnsApiError("Invalid AdGuard rewrite list")
        records = []
        entries = {}
        for item in data:
            if not isinstance(item, dict):
                raise DnsApiError("Invalid AdGuard rewrite")
            domain, answer = item.get("domain"), item.get("answer")
            enabled = item.get("enabled", True)
            if (
                not isinstance(domain, str)
                or not isinstance(answer, str)
                or not answer
                or answer != answer.strip()
                or enabled is not True
            ):
                raise DnsApiError("Disabled or invalid rewrite requires manual inspection")
            try:
                if normalize_domain(domain) != domain:
                    raise ConfigError("Noncanonical rewrite")
            except ConfigError:
                raise DnsApiError("Unsupported AdGuard rewrite domain") from None
            key = hashlib.sha256(
                json.dumps([domain, answer], separators=(",", ":")).encode()
            ).hexdigest()
            if key in entries:
                raise DnsApiError("Ambiguous AdGuard rewrite value")
            entries[key] = {"domain": domain, "answer": answer, "enabled": True}
            records.append(DnsRecord(key, domain, answer))
        self._entries = entries
        return tuple(sorted(records, key=lambda r: (r.domain, r.record_id)))

    def _write_guard(self) -> None:
        if not self._allow:
            raise SafetyBlocked(
                "AdGuard reference adapter is read-only; value IDs cannot establish ownership"
            )

    def create(self, record: DesiredRewrite) -> DnsRecord:
        self._write_guard()
        validate_desired(record)
        if any(r.domain == record.domain for r in self.list_records()):
            raise SafetyBlocked("Rewrite domain already exists")
        self._http.request(
            "POST",
            "/control/rewrite/add",
            {"domain": record.domain, "answer": record.ipv4, "enabled": True},
        )
        try:
            matches = [
                r
                for r in self.list_records()
                if r.domain == record.domain and r.content == record.ipv4
            ]
            if len(matches) != 1:
                raise DnsApiError("Rewrite creation not confirmed")
            return matches[0]
        except (DnsApiError, SafetyBlocked):
            raise RecoveryRequired("AdGuard create read-back unconfirmed") from None

    def delete(self, record: DnsRecord) -> None:
        self._write_guard()
        if record not in self.list_records():
            raise SafetyBlocked("AdGuard delete precondition changed")
        self._http.request("POST", "/control/rewrite/delete", self._entries[record.record_id])
        try:
            if record in self.list_records():
                raise DnsApiError("Rewrite deletion not confirmed")
        except (DnsApiError, SafetyBlocked):
            raise RecoveryRequired("AdGuard delete read-back unconfirmed") from None

    def replace(self, before: DnsRecord, after: DesiredRewrite) -> DnsRecord:
        self._write_guard()
        validate_desired(after)
        if before.domain != after.domain or before not in self.list_records():
            raise SafetyBlocked("AdGuard replace precondition changed")
        self._http.request(
            "PUT",
            "/control/rewrite/update",
            {
                "target": self._entries[before.record_id],
                "update": {"domain": after.domain, "answer": after.ipv4, "enabled": True},
            },
        )
        try:
            matches = [r for r in self.list_records() if r.domain == after.domain]
            if len(matches) != 1 or matches[0].content != after.ipv4:
                raise DnsApiError("Rewrite replacement not confirmed")
            return matches[0]
        except (DnsApiError, SafetyBlocked):
            raise RecoveryRequired("AdGuard replace read-back unconfirmed") from None
