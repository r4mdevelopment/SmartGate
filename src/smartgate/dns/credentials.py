"""Resolve adapter credentials without retaining or exposing the environment."""

from collections.abc import Mapping

from smartgate.errors import ConfigError
from smartgate.models import DnsAuthConfig, DnsCredentials


def required_value(name: str | None, env: Mapping[str, str]) -> str:
    value = env.get(name, "") if name else ""
    if (
        not value
        or value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or value.lower() in {"changeme", "your_api_key", "your_password", "your_token"}
    ):
        raise ConfigError("Required environment variable missing or invalid: " + (name or "unset"))
    return value


def resolve_nextdns_value(new: str, old: str, env: Mapping[str, str]) -> str:
    if new in env and old in env and env[new] != env[old]:
        raise ConfigError("Conflicting DNS environment variables: " + new + " / " + old)
    return required_value(new if new in env else old, env)


def resolve_credentials(auth: DnsAuthConfig, env: Mapping[str, str]) -> DnsCredentials:
    if auth.kind == "basic":
        if auth.api_key_env is not None or not auth.username_env or not auth.password_env:
            raise ConfigError("Basic DNS auth requires only username and password")
        return DnsCredentials(
            username=required_value(auth.username_env, env),
            password=required_value(auth.password_env, env),
        )
    if auth.kind in {"api_key", "bearer"}:
        if not auth.api_key_env or auth.username_env is not None or auth.password_env is not None:
            raise ConfigError("Token DNS auth requires only an API key variable")
        return DnsCredentials(api_key=required_value(auth.api_key_env, env))
    raise ConfigError("Unsupported DNS authentication")
