"""Composition boundary for interchangeable relay implementations."""

from smartgate.errors import SourceError
from smartgate.models import ProviderConfig
from smartgate.ports import RelayProvider
from smartgate.providers.geohide import GeoHideProvider, SourceClient
from smartgate.providers.manifest import ManifestRelayProvider


def create_relay_provider(
    config: ProviderConfig, http: SourceClient | None = None
) -> RelayProvider:
    if config.type == "geohide":
        if http is None:
            raise SourceError("GeoHide provider requires a source HTTP client")
        return GeoHideProvider(http)
    if config.type == "selfhosted":
        return ManifestRelayProvider()
    raise SourceError("Unsupported relay provider")
