from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from smartgate.models import RelayCandidate, RelaySnapshot


def test_candidates_are_immutable_inputs_to_routing():
    candidate = RelayCandidate(
        "geohide-ru", "chatgpt.com", "8.8.8.8", 443, "tls_sni_tcp", 0, "test-feed", "RU"
    )
    with pytest.raises(FrozenInstanceError):
        candidate.ip = "1.1.1.1"


def test_snapshot_preserves_domain_ip_relationship():
    a = RelayCandidate("relay", "chatgpt.com", "8.8.8.8", 443, "tls_sni_tcp", 0, "feed", "RU")
    b = RelayCandidate("relay", "auth.openai.com", "1.1.1.1", 443, "tls_sni_tcp", 0, "feed", "RU")
    snapshot = RelaySnapshot("relay", datetime(2026, 10, 7, tzinfo=UTC), "digest", (a, b), ())
    assert [(c.domain, c.ip) for c in snapshot.candidates] == [
        ("chatgpt.com", "8.8.8.8"),
        ("auth.openai.com", "1.1.1.1"),
    ]
