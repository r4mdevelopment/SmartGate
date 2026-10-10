"""Fixed GeoHide source registry and bounded credential-free HTTPS downloads."""

import time
from collections.abc import Callable
from datetime import datetime
from types import MappingProxyType

import httpx

from smartgate.errors import SourceError
from smartgate.models import ProviderConfig, RelaySnapshot
from smartgate.providers.hosts import MAX_SOURCE_BYTES, parse_hosts

_BASE = "https://dns.geohide.ru:8443/static/formats/"
GEOHIDE_SOURCES = MappingProxyType(
    {region: _BASE + region.lower() + "/hosts.txt" for region in ("RU", "EU", "US")}
)


class SourceClient:
    """Separate from the authenticated DNS API clients; never accepts credentials."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport, timeout=10, follow_redirects=False, trust_env=False
        )

    def close(self) -> None:
        self._client.close()

    def get_text(self, url: str) -> str:
        if url not in GEOHIDE_SOURCES.values():
            raise SourceError("Unregistered relay source URL")
        for attempt in range(3):
            try:
                with self._client.stream("GET", url) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt == 2:
                            raise SourceError("Relay source unavailable after bounded retries")
                    else:
                        if response.status_code != 200:
                            raise SourceError("Relay source rejected request or redirected")
                        if "text/html" in response.headers.get("Content-Type", "").lower():
                            raise SourceError("HTML cannot be used as a relay source")
                        length = response.headers.get("Content-Length")
                        if length is not None:
                            try:
                                declared = int(length)
                            except ValueError:
                                raise SourceError("Invalid source Content-Length") from None
                            if declared < 0 or declared > MAX_SOURCE_BYTES:
                                raise SourceError("Relay source exceeds 5 MiB")
                        body = bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if len(body) + len(chunk) > MAX_SOURCE_BYTES:
                                raise SourceError("Relay source exceeds 5 MiB")
                            body.extend(chunk)
                        try:
                            return body.decode("utf-8-sig")
                        except UnicodeError:
                            raise SourceError("Relay source is not UTF-8") from None
            except httpx.RequestError:
                if attempt == 2:
                    raise SourceError("Relay source network request failed") from None
            self._sleep(float(2**attempt))
        raise SourceError("Relay source unavailable")


class GeoHideProvider:
    def __init__(self, http: SourceClient) -> None:
        self._http = http

    def fetch(self, config: ProviderConfig, now: datetime) -> RelaySnapshot:
        if (
            config.type != "geohide"
            or config.region is None
            or config.region not in GEOHIDE_SOURCES
        ):
            raise SourceError("Unsupported GeoHide source region")
        url = GEOHIDE_SOURCES[config.region]
        return parse_hosts(self._http.get_text(url), config, url, now)
