import pytest

from smartgate.dns.credentials import resolve_credentials, resolve_nextdns_value
from smartgate.errors import ConfigError
from smartgate.models import DnsAuthConfig


def test_basic_credentials_do_not_need_an_api_key_or_expose_password():
    result = resolve_credentials(
        DnsAuthConfig("basic", username_env="USER", password_env="PASS"),
        {"USER": "admin", "PASS": "sensitive-value"},
    )
    assert result.username == "admin" and result.password == "sensitive-value"
    assert result.api_key is None
    assert "sensitive-value" not in repr(result)


def test_bearer_auth_does_not_need_username_or_password():
    result = resolve_credentials(
        DnsAuthConfig("bearer", api_key_env="TOKEN"), {"TOKEN": "sensitive-value"}
    )
    assert result.api_key == "sensitive-value" and result.password is None
    assert "sensitive-value" not in repr(result)


@pytest.mark.parametrize(
    "value", ["", " leading", "trailing ", "changeme", "YOUR_API_KEY", "secret\nheader"]
)
def test_invalid_or_placeholder_credentials_are_rejected(value):
    with pytest.raises(ConfigError) as error:
        resolve_credentials(DnsAuthConfig("api_key", api_key_env="KEY"), {"KEY": value})
    assert "secret\nheader" not in str(error.value)


def test_conflicting_old_and_new_values_fail_without_disclosing_either():
    with pytest.raises(ConfigError) as error:
        resolve_nextdns_value(
            "SMARTGATE_DNS_API_KEY",
            "NEXTDNS_API_KEY",
            {"SMARTGATE_DNS_API_KEY": "new-sensitive", "NEXTDNS_API_KEY": "old-sensitive"},
        )
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"OLD": "same"}, "same"),
        ({"NEW": "same", "OLD": "same"}, "same"),
        ({"NEW": "same"}, "same"),
    ],
)
def test_legacy_transition_preserves_the_selected_value(env, expected):
    assert resolve_nextdns_value("NEW", "OLD", env) == expected
