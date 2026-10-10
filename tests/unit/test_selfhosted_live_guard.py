import pytest

from smartgate.compatibility import selfhosted_live_target
from smartgate.dns.types import target_fingerprint
from smartgate.errors import SafetyBlocked


def env(**values):
    return {
        "SMARTGATE_LIVE": "1",
        "SMARTGATE_DNS_TEST_ACK": "isolated-target-only",
        "SMARTGATE_DNS_TEST_PROVIDER": "adguardhome",
        "SMARTGATE_DNS_TEST_API_URL": "https://test.example",
        "SMARTGATE_DNS_TEST_NAMESPACE": "isolated.example",
        "SMARTGATE_DNS_TEST_DOH_URL": "https://test-dns.example/query",
        "SMARTGATE_DNS_PROTECTED_FINGERPRINT": "a" * 64,
        "SMARTGATE_DNS_PROTECTED_PROVIDER": "adguardhome",
        "SMARTGATE_DNS_PROTECTED_API_URL": "https://protected.example",
        "SMARTGATE_DNS_PROTECTED_NAMESPACE": "protected.example",
        **values,
    }


def test_isolated_target_requires_explicit_opt_in_before_clients():
    for name in ("SMARTGATE_LIVE", "SMARTGATE_DNS_TEST_ACK", "SMARTGATE_DNS_PROTECTED_FINGERPRINT"):
        value = env()
        value.pop(name)
        with pytest.raises(SafetyBlocked):
            selfhosted_live_target(value)


def test_live_harness_cannot_target_protected_binding():
    target = selfhosted_live_target(env())
    with pytest.raises(SafetyBlocked):
        selfhosted_live_target(env(SMARTGATE_DNS_PROTECTED_FINGERPRINT=target_fingerprint(target)))


@pytest.mark.parametrize(
    "values",
    [
        {"SMARTGATE_DNS_TEST_PROVIDER": "unknown"},
        {"SMARTGATE_DNS_TEST_NAMESPACE": "../zone"},
        {"SMARTGATE_DNS_TEST_API_URL": "http://test.example"},
        {"SMARTGATE_DNS_PROTECTED_FINGERPRINT": ""},
    ],
)
def test_invalid_live_binding_fails_closed(values):
    with pytest.raises(SafetyBlocked):
        selfhosted_live_target(env(**values))


@pytest.mark.parametrize("provider", ["adguardhome", "technitium"])
def test_same_mutation_destination_is_blocked_despite_different_doh(provider):
    with pytest.raises(SafetyBlocked):
        selfhosted_live_target(
            env(
                SMARTGATE_DNS_TEST_PROVIDER=provider,
                SMARTGATE_DNS_PROTECTED_PROVIDER=provider,
                SMARTGATE_DNS_PROTECTED_API_URL="https://TEST.example:443/",
                SMARTGATE_DNS_PROTECTED_NAMESPACE="isolated.example",
            )
        )


def test_adguard_namespace_does_not_isolate_global_rewrite_api():
    with pytest.raises(SafetyBlocked):
        selfhosted_live_target(
            env(
                SMARTGATE_DNS_PROTECTED_API_URL="https://test.example",
                SMARTGATE_DNS_PROTECTED_NAMESPACE="another.example",
            )
        )


def test_missing_protected_mutation_destination_blocks_before_clients():
    values = env()
    values.pop("SMARTGATE_DNS_PROTECTED_API_URL")
    with pytest.raises(SafetyBlocked):
        selfhosted_live_target(values)


def test_technitium_distinct_existing_zones_are_distinct_mutation_destinations():
    assert (
        selfhosted_live_target(
            env(
                SMARTGATE_DNS_TEST_PROVIDER="technitium",
                SMARTGATE_DNS_PROTECTED_PROVIDER="technitium",
                SMARTGATE_DNS_PROTECTED_API_URL="https://test.example",
            )
        ).namespace
        == "isolated.example"
    )
