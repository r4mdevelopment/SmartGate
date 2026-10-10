import pytest

from smartgate.compatibility import ensure_empty_profile, live_settings
from smartgate.errors import SafetyBlocked

ENV = {
    "SMARTGATE_LIVE": "1",
    "NEXTDNS_TEST_PROFILE_ID": "abcdef",
    "NEXTDNS_ACTIVE_PROFILE_ID": "123456",
    "NEXTDNS_API_KEY": "unit-test-key",
    "NEXTDNS_TEST_ACK": "isolated-profile-only",
}


def test_active_profile_can_never_be_selected_for_live_mutations():
    with pytest.raises(SafetyBlocked):
        live_settings(dict(ENV, NEXTDNS_TEST_PROFILE_ID="123456"))


@pytest.mark.parametrize("missing", list(ENV))
def test_missing_live_safety_setting_blocks_before_any_request(missing):
    env = dict(ENV)
    del env[missing]
    with pytest.raises(SafetyBlocked):
        live_settings(env)


def test_live_settings_reject_malformed_profile_id():
    with pytest.raises(SafetyBlocked):
        live_settings(dict(ENV, NEXTDNS_TEST_PROFILE_ID="../profiles/123456"))


def test_live_settings_do_not_expose_key_in_repr():
    assert "unit-test-key" not in repr(live_settings(ENV))


def test_nonempty_test_profile_is_never_modified():
    with pytest.raises(SafetyBlocked):
        ensure_empty_profile([{"id": "existing", "name": "chatgpt.com", "content": "8.8.8.8"}])
    ensure_empty_profile([])


def test_explicit_external_dns_allows_an_isolated_nextdns_test_profile():
    settings = live_settings(dict(ENV, NEXTDNS_ACTIVE_PROFILE_ID="not-nextdns"))
    assert settings.test_profile_id == "abcdef"
    assert settings.active_profile_id == "not-nextdns"


@pytest.mark.parametrize("active_id", ["none", "unknown", "", "xbox-dns.ru"])
def test_external_dns_requires_the_exact_explicit_attestation(active_id):
    with pytest.raises(SafetyBlocked):
        live_settings(dict(ENV, NEXTDNS_ACTIVE_PROFILE_ID=active_id))
