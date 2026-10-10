"""Errors safe to present without exposing credentials or HTTP response bodies."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smartgate.models import DesiredRewrite, DnsRecord


class SmartGateError(Exception):
    """Base error for the control-plane."""


class ConfigError(SmartGateError):
    """Invalid configuration."""


class SourceError(SmartGateError):
    """Unavailable or invalid relay source."""


class DnsApiError(SmartGateError):
    """DNS API returned an error."""


class StateConflict(SmartGateError):
    """State revision changed or another writer holds the lock."""


class SafetyBlocked(SmartGateError):
    """Safety precondition is not satisfied."""


class RecoveryRequired(SmartGateError):
    """A mutation has an unknown result and must be reconciled."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        before: "DnsRecord | None" = None,
        after: "DesiredRewrite | None" = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.before = before
        self.after = after
