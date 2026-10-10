"""Bounded NextDNS HTTPS transport with safe errors and explicit mutation uncertainty."""

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import cast
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from smartgate.errors import DnsApiError, RecoveryRequired, SafetyBlocked


class HttpClient:
    """Never follows redirects or sends the API key outside api.nextdns.io."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        max_calls: int = 400,
        duration_s: float = 1200,
    ) -> None:
        if not api_key or not 1 <= max_calls <= 400 or not 0 < duration_s <= 1200:
            raise SafetyBlocked("Invalid HTTP transport configuration")
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._deadline = clock() + duration_s
        self._next_call = clock()
        self._calls = 0
        self._max_calls = max_calls
        self._client = httpx.Client(
            base_url="https://api.nextdns.io",
            headers={"X-Api-Key": api_key},
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=10.0,
        )

    def close(self) -> None:
        self._client.close()

    def _reserve_call(self) -> float:
        now = self._clock()
        delay = max(0.0, self._next_call - now)
        if self._calls >= self._max_calls or now + delay >= self._deadline:
            raise SafetyBlocked("NextDNS request or duration budget exhausted")
        if delay:
            self._sleep(delay)
        self._calls += 1
        now = self._clock()
        self._next_call = now + 2.0
        return min(10.0, self._deadline - now)

    def _pause(self, delay: float) -> None:
        if delay > 60 or self._clock() + delay >= self._deadline:
            raise SafetyBlocked("Retry delay exceeds the run policy")
        self._sleep(max(0.0, delay))

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                return max(0.0, float(int(retry_after)))
            except ValueError:
                try:
                    timestamp = parsedate_to_datetime(retry_after)
                    return max(0.0, (timestamp - datetime.now(UTC)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    raise DnsApiError("Invalid API retry policy") from None
        return float(2**attempt) + max(0.0, min(self._jitter(), 1.0))

    def request(self, method: str, path: str, json: object | None = None) -> dict[str, object]:
        method = method.upper()
        parsed = urlsplit(path)
        if (
            method not in {"GET", "POST", "DELETE", "PATCH"}
            or not path.startswith("/")
            or path.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or ".." in parsed.path.split("/")
        ):
            raise SafetyBlocked("Unsafe API method or path")
        mutation = method != "GET"
        for attempt in range(3):
            timeout = self._reserve_call()
            try:
                response = self._client.request(method, path, json=json, timeout=timeout)
            except httpx.RequestError:
                if mutation:
                    raise RecoveryRequired(
                        "DNS mutation result is unknown; read-back required"
                    ) from None
                if attempt == 2:
                    raise DnsApiError("NextDNS read failed after bounded retries") from None
                self._pause(self._retry_delay(None, attempt))
                continue
            if response.status_code == 429:
                if attempt == 2:
                    raise DnsApiError("NextDNS rate limit persists")
                self._pause(self._retry_delay(response, attempt))
                continue
            if response.status_code >= 500:
                if mutation:
                    raise RecoveryRequired("DNS mutation result is unknown; read-back required")
                if attempt == 2:
                    raise DnsApiError("NextDNS server error after bounded retries")
                self._pause(self._retry_delay(response, attempt))
                continue
            if not 200 <= response.status_code < 300:
                raise DnsApiError(f"NextDNS API rejected request (HTTP {response.status_code})")
            if method == "DELETE" and response.status_code == 204 and not response.content:
                return {}
            try:
                payload = response.json()
            except ValueError:
                if mutation:
                    raise RecoveryRequired(
                        "DNS mutation returned invalid JSON; read-back required"
                    ) from None
                raise DnsApiError("Invalid NextDNS JSON response") from None
            if not isinstance(payload, dict):
                if mutation:
                    raise RecoveryRequired("DNS mutation response is malformed; read-back required")
                raise DnsApiError("Invalid NextDNS response envelope")
            if "errors" in payload:
                raise DnsApiError("NextDNS API returned an errors envelope")
            return cast(dict[str, object], payload)
        raise DnsApiError("NextDNS request failed")

    def list_pages(self, path: str) -> list[dict[str, object]]:
        parsed = urlsplit(path)
        query = list(parse_qsl(parsed.query))
        if any(key == "cursor" for key, _ in query):
            raise SafetyBlocked("Caller must not provide a pagination cursor")
        records: list[dict[str, object]] = []
        seen: set[str] = set()
        cursor: str | None = None
        while True:
            params = query + ([("cursor", cursor)] if cursor is not None else [])
            page_path = parsed.path + ("?" + urlencode(params) if params else "")
            payload = self.request("GET", page_path)
            data = payload.get("data")
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise DnsApiError("Invalid paginated API data")
            records.extend(cast(list[dict[str, object]], data))
            meta = payload.get("meta", {})
            if not isinstance(meta, dict):
                raise DnsApiError("Invalid pagination metadata")
            pagination = meta.get("pagination", {})
            if not isinstance(pagination, dict):
                raise DnsApiError("Invalid pagination metadata")
            next_cursor = pagination.get("cursor")
            if next_cursor is None:
                return records
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                raise DnsApiError("Invalid or repeated pagination cursor")
            seen.add(next_cursor)
            cursor = next_cursor
