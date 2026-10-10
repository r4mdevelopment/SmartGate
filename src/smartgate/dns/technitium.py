"""Technitium v15+ reference API for an explicitly existing authoritative zone."""

import hashlib
import json

from smartgate.catalog import normalize_domain, suffix_matches
from smartgate.dns.transport import ApiClient
from smartgate.dns.validation import validate_desired
from smartgate.errors import ConfigError, DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.models import DesiredRewrite, DnsRecord


class TechnitiumProvider:
    def __init__(self, http: ApiClient, zone: str, *, allow_reference_writes: bool = False) -> None:
        if normalize_domain(zone) != zone:
            raise ConfigError("Technitium requires a canonical existing zone")
        self._http, self._zone, self._allow = http, zone, allow_reference_writes
        self._entries: dict[str, dict[str, object]] = {}

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, action: str, params: dict[str, str]) -> dict[str, object]:
        data = self._http.request(
            method, "/api/zones/records/" + action, params={"zone": self._zone, **params}
        )
        if not isinstance(data, dict) or data.get("status") != "ok":
            if method != "GET":
                raise RecoveryRequired("Technitium mutation response unconfirmed")
            raise DnsApiError("Technitium API response rejected")
        return data

    def list_records(self) -> tuple[DnsRecord, ...]:
        data = self._request("GET", "get", {"domain": self._zone, "listZone": "true"})
        response = data.get("response")
        if not isinstance(response, dict):
            raise DnsApiError("Invalid Technitium records response")
        zone = response.get("zone")
        if (
            not isinstance(zone, dict)
            or zone.get("name") != self._zone
            or zone.get("type") != "Primary"
            or zone.get("disabled") is not False
            or zone.get("internal") is not False
        ):
            raise DnsApiError("Technitium zone unavailable or outside approved scope")
        rows = response.get("records")
        if not isinstance(rows, list) or len(rows) > 10000:
            raise DnsApiError("Invalid Technitium record list")
        records = []
        entries = {}
        value_keys = set()
        for row in rows:
            if not isinstance(row, dict):
                raise DnsApiError("Invalid Technitium record")
            name, kind, rdata = row.get("name"), row.get("type"), row.get("rData")
            if (
                not isinstance(name, str)
                or not isinstance(kind, str)
                or not isinstance(rdata, dict)
                or row.get("disabled") is not False
            ):
                raise DnsApiError("Disabled or invalid Technitium record requires inspection")
            try:
                if normalize_domain(name) != name or not suffix_matches(name, self._zone):
                    raise ConfigError("Record outside zone")
            except ConfigError:
                raise DnsApiError("Unsupported Technitium owner name") from None
            content = (
                rdata.get("ipAddress")
                if kind in {"A", "AAAA"}
                else json.dumps(
                    {"type": kind, "rData": rdata}, sort_keys=True, separators=(",", ":")
                )
            )
            if not isinstance(content, str) or not content:
                raise DnsApiError("Invalid Technitium record content")
            value_key = (name, kind, content)
            if value_key in value_keys:
                raise DnsApiError("Ambiguous Technitium record value")
            value_keys.add(value_key)
            identity = {
                "name": name,
                "type": kind,
                "rData": rdata,
                "ttl": row.get("ttl"),
                "comments": row.get("comments", ""),
                "expiryTtl": row.get("expiryTtl", 0),
            }
            key = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            entries[key] = row
            records.append(DnsRecord(key, name, content))
        self._entries = entries
        return tuple(sorted(records, key=lambda r: (r.domain, r.record_id)))

    def _guard(self, domain: str) -> None:
        if not self._allow:
            raise SafetyBlocked(
                "Technitium reference adapter is read-only; lifecycle ownership unverified"
            )
        if not suffix_matches(domain, self._zone):
            raise SafetyBlocked("Record outside explicitly configured zone")

    def create(self, record: DesiredRewrite) -> DnsRecord:
        self._guard(record.domain)
        validate_desired(record)
        if any(r.domain == record.domain for r in self.list_records()):
            raise SafetyBlocked("Technitium owner already has records")
        self._request(
            "POST",
            "add",
            {
                "domain": record.domain,
                "type": "A",
                "ipAddress": record.ipv4,
                "ttl": "60",
                "overwrite": "false",
                "ptr": "false",
            },
        )
        return self._confirmed(record)

    def _confirmed(self, record: DesiredRewrite) -> DnsRecord:
        try:
            matches = [r for r in self.list_records() if r.domain == record.domain]
            if len(matches) != 1 or matches[0].content != record.ipv4:
                raise DnsApiError("Technitium read-back differs")
            return matches[0]
        except (DnsApiError, SafetyBlocked):
            raise RecoveryRequired("Technitium mutation read-back unconfirmed") from None

    def _before(self, record: DnsRecord) -> dict[str, object]:
        if record not in self.list_records():
            raise SafetyBlocked("Technitium record precondition changed")
        row = self._entries[record.record_id]
        if row["type"] != "A":
            raise SafetyBlocked("Reference mutation only supports A records")
        return row

    def delete(self, record: DnsRecord) -> None:
        self._guard(record.domain)
        self._before(record)
        self._request(
            "POST", "delete", {"domain": record.domain, "type": "A", "ipAddress": record.content}
        )
        try:
            if record in self.list_records():
                raise DnsApiError("Technitium deletion not confirmed")
        except (DnsApiError, SafetyBlocked):
            raise RecoveryRequired("Technitium delete read-back unconfirmed") from None

    def replace(self, before: DnsRecord, after: DesiredRewrite) -> DnsRecord:
        self._guard(after.domain)
        validate_desired(after)
        if before.domain != after.domain:
            raise SafetyBlocked("Technitium replace must preserve owner")
        row = self._before(before)
        self._request(
            "POST",
            "update",
            {
                "domain": after.domain,
                "type": "A",
                "ipAddress": before.content,
                "newIpAddress": after.ipv4,
                "ttl": str(row.get("ttl", 60)),
                "comments": str(row.get("comments", "")),
                "expiryTtl": str(row.get("expiryTtl", 0)),
                "ptr": "false",
                "createPtrZone": "false",
            },
        )
        return self._confirmed(after)
