from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.errors import SafetyBlocked
from smartgate.models import ApplyContext, StateDocument
from smartgate.reconcile.common import guard
from smartgate.state.codec import decode_state, encode_state

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def target():
    return DnsTarget(
        "primary", "nextdns", "https://api.nextdns.io", "abcdef", "https://dns.nextdns.io/abcdef"
    )


def state():
    return StateDocument(
        2, "primary", None, target_id="primary", target_fingerprint=target_fingerprint(target())
    )


def test_v2_codec_roundtrip_accepts_generic_instance_identity():
    value = state()
    assert decode_state(encode_state(value), "primary", "revision") == replace(
        value, revision="revision"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "adguardhome"),
        ("namespace", "123456"),
        ("api_origin", "https://different.example"),
        ("doh_url", "https://different.example/query"),
    ],
)
def test_different_destination_is_blocked_even_with_same_instance(field, value):
    altered = replace(target(), **{field: value})
    context = ApplyContext(
        lambda: NOW,
        NOW + timedelta(minutes=20),
        "run",
        "primary",
        target_id="primary",
        target_fingerprint=target_fingerprint(altered),
    )
    with pytest.raises(SafetyBlocked):
        guard(context, state())


def test_v2_state_without_fingerprint_is_not_serializable():
    with pytest.raises(SafetyBlocked):
        encode_state(replace(state(), target_fingerprint=""))
