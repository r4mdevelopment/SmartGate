from pathlib import Path

import pytest

from smartgate.config import load_config
from smartgate.errors import ConfigError

BASE = """
schema_version = 1
[dns]
provider = "nextdns"
[state]
provider = "local"
[[relay.providers]]
id = "geohide-ru"
type = "geohide"
region = "RU"
[services.chatgpt]
include_suffixes = ["chatgpt.com"]
required_domains = ["chatgpt.com"]
provider_order = ["geohide-ru"]
allow_subtree_rewrites = true
"""


def config(tmp_path, text=BASE):
    path = tmp_path / "smartgate.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path, {})


def test_offline_config_needs_no_credentials_and_apply_defaults_off(tmp_path):
    value = config(tmp_path)
    assert not value.sync.enabled
    assert not value.sync.allow_prune
    assert value.sync.max_records == 100
    assert value.dns.api_key_env == "NEXTDNS_API_KEY"
    assert value.catalog.select("chatgpt.com").id == "chatgpt"
    assert value.probe.max_pairs == 200


@pytest.mark.parametrize(
    "text",
    [
        BASE.replace("schema_version = 1", "schema_version = 3"),
        BASE.replace("schema_version = 1", "schema_version = 1\nunknown = true"),
        BASE.replace('provider = "nextdns"', 'provider = "nextdns"\nunknown = 1'),
        BASE.replace('region = "RU"', 'region = "RU"\nunknown = "ignored"'),
        BASE.replace('region = "RU"', 'region = "OTHER"'),
        BASE.replace('type = "geohide"', 'type = "vpn"'),
        BASE.replace('provider_order = ["geohide-ru"]', 'provider_order = ["missing"]'),
        BASE.replace('provider = "nextdns"', 'provider = "nextdns"\napi_key_env = "bad-name"'),
        BASE + '\n[sync]\nenabled = "true"\n',
        BASE + "\n[sync]\nmax_records = true\n",
        BASE + "\n[sync]\nmax_change_ratio = nan\n",
        BASE + "\n[probe]\nmax_pairs = 201\n",
    ],
)
def test_invalid_config_is_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        config(tmp_path, text)


def test_duplicate_provider_ids_are_rejected(tmp_path):
    with pytest.raises(ConfigError):
        config(
            tmp_path, BASE + '\n[[relay.providers]]\nid="geohide-ru"\ntype="geohide"\nregion="EU"\n'
        )


def test_external_catalog_is_resolved_relative_to_config(tmp_path):
    service = BASE[BASE.index("[services.chatgpt]") :]
    (tmp_path / "services.toml").write_text(service, encoding="utf-8")
    text = BASE[: BASE.index("[services.chatgpt]")]
    text = text.replace("schema_version = 1", 'schema_version = 1\ncatalog_path="services.toml"')
    value = config(tmp_path, text)
    assert value.catalog_path == tmp_path / "services.toml"
    assert value.catalog.select("chatgpt.com").id == "chatgpt"


def test_example_configuration_is_valid():
    root = Path(__file__).parents[2]
    value = load_config(root / "config" / "smartgate.example.toml", {})
    assert not value.sync.enabled
    assert [provider.id for provider in value.providers] == ["geohide-ru", "geohide-eu"]


def test_sensitive_values_are_not_copied_into_config(tmp_path):
    path = tmp_path / "smartgate.toml"
    path.write_text(BASE, encoding="utf-8")
    value = load_config(path, {"NEXTDNS_API_KEY": "private-token-value"})
    assert "private-token-value" not in repr(value)


@pytest.mark.parametrize(
    "text",
    [
        BASE.replace('provider = "local"', "provider = []"),
        BASE + "\n[sync]\nmax_change_ratio = " + "1" + "0" * 400 + "\n",
    ],
)
def test_malformed_scalar_inputs_raise_config_error(tmp_path, text):
    with pytest.raises(ConfigError):
        config(tmp_path, text)


V2 = BASE.replace("schema_version = 1", "schema_version = 2").replace(
    '[dns]\nprovider = "nextdns"',
    """[dns]
provider = "adguardhome"
instance_id = "home"
[dns.connection]
api_url_env = "SMARTGATE_DNS_API_URL"
namespace_env = "SMARTGATE_DNS_NAMESPACE"
[dns.auth]
kind = "basic"
username_env = "SMARTGATE_DNS_USERNAME"
password_env = "SMARTGATE_DNS_PASSWORD"
[dns.verify]
doh_url_env = "SMARTGATE_DNS_DOH_URL"
""",
)


def test_schema_v2_basic_connection_requires_no_api_key(tmp_path):
    value = config(tmp_path, V2)
    assert value.dns.provider == "adguardhome"
    assert value.dns.instance_id == "home"
    assert value.dns.auth.kind == "basic"
    assert value.dns.auth.api_key_env is None


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace('kind = "basic"', 'kind = "bearer"'),
        lambda s: s.replace('kind = "basic"', 'kind = "basic"\napi_key_env = "UNUSED"'),
        lambda s: s.replace('instance_id = "home"', 'instance_id = "../home"'),
        lambda s: s.replace('password_env = "SMARTGATE_DNS_PASSWORD"', ""),
        lambda s: s.replace('api_url_env = "SMARTGATE_DNS_API_URL"', ""),
        lambda s: s.replace('provider = "adguardhome"', 'provider = "unknown"'),
    ],
)
def test_v2_invalid_provider_configuration_is_rejected(tmp_path, change):
    with pytest.raises(ConfigError):
        config(tmp_path, change(V2))
