"""Bounded, fixed-origin HTTPS API transport for self-hosted DNS adapters."""

import json
import re
import time
from collections.abc import Callable
from typing import Literal

import httpx

from smartgate.dns.validation import validate_endpoint
from smartgate.errors import ConfigError, DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.models import DnsCredentials


class ApiClient:
    def __init__(
        self,
        origin: str,
        kind: Literal["basic", "bearer"],
        credentials: DnsCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_calls: int = 400,
        duration_s: float = 1200,
    ) -> None:
        self._origin = validate_endpoint(origin, origin_only=True).rstrip("/")
        if type(max_calls) is not int or not 1 <= max_calls <= 400 or not 0 < duration_s <= 1200:
            raise SafetyBlocked("Invalid DNS API budget")
        self._clock, self._deadline = clock, clock() + duration_s
        self._calls, self._max_calls = 0, max_calls
        headers = {}
        auth = None
        if (
            kind == "basic"
            and credentials.username
            and credentials.password
            and credentials.api_key is None
        ):
            if ":" in credentials.username:
                raise ConfigError("Invalid basic authentication username")
            auth = httpx.BasicAuth(credentials.username, credentials.password)
        elif (
            kind == "bearer"
            and credentials.api_key
            and credentials.username is None
            and credentials.password is None
        ):
            headers["Authorization"] = "Bearer " + credentials.api_key
        else:
            raise ConfigError("Invalid DNS API credentials")
        if any(
            any(ord(c) < 32 or ord(c) == 127 for c in value)
            for value in (credentials.api_key, credentials.username, credentials.password)
            if value
        ):
            raise ConfigError("Invalid DNS API credential characters")
        self._client = httpx.Client(
            auth=auth,
            headers=headers,
            trust_env=False,
            follow_redirects=False,
            timeout=10,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        payload: object = None,
        *,
        params: dict[str, str] | None = None,
    ) -> object:
        if (
            method not in {"GET", "POST", "PUT", "DELETE"}
            or re.fullmatch(r"/[A-Za-z0-9/_-]+", path) is None
            or path.startswith("//")
        ):
            raise SafetyBlocked("DNS API request must stay within the configured origin")
        if self._calls >= self._max_calls or self._clock() >= self._deadline:
            raise SafetyBlocked("DNS API request or duration budget exhausted")
        request_deadline = min(self._deadline, self._clock() + 10)
        mutation = method != "GET"
        self._calls += 1
        try:
            with self._client.stream(
                method,
                self._origin + path,
                json=payload,
                params=params,
                timeout=min(10, self._deadline - self._clock()),
            ) as response:
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        raise DnsApiError("DNS API response too large")
                if self._clock() > request_deadline:
                    raise DnsApiError("DNS API response exceeded duration budget")
                if response.status_code != 200:
                    if mutation and response.status_code >= 500:
                        raise RecoveryRequired("DNS mutation result unknown; recovery required")
                    raise DnsApiError("DNS API response rejected")
                if not data:
                    return None
                return json.loads(bytes(data))
        except RecoveryRequired:
            raise
        except (httpx.HTTPError, ValueError, DnsApiError):
            if mutation:
                raise RecoveryRequired(
                    "DNS mutation response unconfirmed; recovery required"
                ) from None
            raise DnsApiError("DNS API request failed") from None
