"""NextDNS rewrite adapter based on verified create/delete API contracts."""

import re
from urllib.parse import quote

from smartgate.catalog import normalize_domain
from smartgate.dns.nextdns_transport import HttpClient
from smartgate.dns.validation import validate_desired
from smartgate.errors import ConfigError, DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.models import DesiredRewrite, DnsRecord


def _record(data: object) -> DnsRecord:
    if not isinstance(data, dict):
        raise DnsApiError("Invalid rewrite data")
    identifier, name, content = data.get("id"), data.get("name"), data.get("content")
    if (
        not isinstance(identifier, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", identifier) is None
        or not isinstance(name, str)
        or not isinstance(content, str)
        or not content
        or content != content.strip()
    ):
        raise DnsApiError("Invalid rewrite identity or content")
    try:
        domain = normalize_domain(name)
    except ConfigError:
        raise DnsApiError("Invalid rewrite hostname") from None
    return DnsRecord(identifier, domain, content)


class NextDnsProvider:
    def __init__(self, profile_id: str, api_key: str, http: HttpClient | None = None) -> None:
        if re.fullmatch(r"[0-9a-f]{6}", profile_id) is None:
            raise SafetyBlocked("Invalid NextDNS profile")
        if not api_key or api_key != api_key.strip():
            raise SafetyBlocked("NextDNS key is required")
        self._http = http if http is not None else HttpClient(api_key)
        self._path = "/profiles/" + profile_id + "/rewrites"

    def close(self) -> None:
        self._http.close()

    def list_records(self) -> tuple[DnsRecord, ...]:
        records = tuple(_record(item) for item in self._http.list_pages(self._path))
        if len({r.record_id for r in records}) != len(records):
            raise DnsApiError("Duplicate NextDNS record ID")
        return tuple(sorted(records, key=lambda r: (r.domain, r.record_id)))

    def create(self, record: DesiredRewrite) -> DnsRecord:
        validate_desired(record)
        try:
            payload = self._http.request(
                "POST",
                self._path,
                {"name": record.domain, "content": record.ipv4},
            )
        except RecoveryRequired:
            raise RecoveryRequired(
                "Create result unknown; recovery required", operation="create", after=record
            ) from None
        try:
            data = payload.get("data")
            result = _record(data)
            if result.domain != record.domain or result.content != record.ipv4:
                raise DnsApiError("Create result does not match intent")
            if not isinstance(data, dict) or data.get("type") != "A":
                raise DnsApiError("Create returned an unsupported rewrite type")
            if result not in self.list_records():
                raise DnsApiError("Created record is not confirmed by read-back")
            return result
        except (DnsApiError, SafetyBlocked):
            raise RecoveryRequired(
                "Create read-back unconfirmed; recovery required", operation="create", after=record
            ) from None

    def delete(self, record: DnsRecord) -> None:
        # Validate the ID before it can become a URL segment.
        _record({"id": record.record_id, "name": record.domain, "content": record.content})
        if record not in self.list_records():
            raise SafetyBlocked("Delete precondition changed or record is absent")
        try:
            self._http.request("DELETE", self._path + "/" + quote(record.record_id, safe=""))
            if any(r.record_id == record.record_id for r in self.list_records()):
                raise DnsApiError("Deleted ID is still present")
        except (RecoveryRequired, DnsApiError, SafetyBlocked):
            raise RecoveryRequired(
                "Delete result unconfirmed; recovery required", operation="delete", before=record
            ) from None

    def replace(self, before: DnsRecord, after: DesiredRewrite) -> DnsRecord:
        validate_desired(after)
        if before.domain != after.domain:
            raise SafetyBlocked("Replace must preserve the domain")
        try:
            self.delete(before)
        except RecoveryRequired:
            raise RecoveryRequired(
                "Replace delete unconfirmed; recovery required",
                operation="replace",
                before=before,
                after=after,
            ) from None
        try:
            return self.create(after)
        except (DnsApiError, RecoveryRequired, SafetyBlocked):
            raise RecoveryRequired(
                "Replace incomplete after delete; recovery required",
                operation="replace",
                before=before,
                after=after,
            ) from None
