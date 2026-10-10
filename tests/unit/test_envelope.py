import base64
import json
from dataclasses import replace

import pytest

from smartgate.errors import SafetyBlocked

KEY = bytes(range(32))


def api():
    from smartgate.security.envelope import EnvelopeBinding, EnvelopeCipher, parse_encryption_key

    return EnvelopeBinding, EnvelopeCipher, parse_encryption_key


def test_roundtrip_private_and_fresh_nonce():
    Binding, Cipher, parse = api()
    key = parse(base64.b64encode(KEY).decode())
    cipher = Cipher(key)
    binding = Binding("state", "Owner/Repo", "smartgate-state", "primary")
    payload = b"private.example 8.8.8.8 profile-id journal"
    first = cipher.seal(payload, binding, max_plaintext=1024)
    second = cipher.seal(payload, binding, max_plaintext=1024)
    assert first != second and payload not in first
    assert len(base64.b64decode(json.loads(first)["nonce"])) == 12
    assert cipher.open(first, binding, max_plaintext=1024) == payload
    assert (
        cipher.open(first, replace(binding, repository="owner/repo"), max_plaintext=1024) == payload
    )


@pytest.mark.parametrize(
    "change", ["ciphertext", "nonce", "key", "repository", "scope", "identity", "purpose"]
)
def test_altered_ciphertext_and_binding_fail_closed(change):
    Binding, Cipher, _ = api()
    cipher = Cipher(KEY)
    binding = Binding("state", "owner/repo", "smartgate-state", "primary")
    envelope = cipher.seal(b"private.example", binding, max_plaintext=1024)
    if change in {"ciphertext", "nonce"}:
        data = json.loads(envelope)
        value = bytearray(base64.b64decode(data[change]))
        value[0] ^= 1
        data[change] = base64.b64encode(value).decode()
        envelope = json.dumps(data).encode()
    elif change == "key":
        cipher = Cipher(b"x" * 32)
    else:
        binding = replace(
            binding,
            **{
                change: "report"
                if change == "purpose"
                else "different/repo"
                if change == "repository"
                else "different"
            },
        )
    with pytest.raises(SafetyBlocked) as error:
        cipher.open(envelope, binding, max_plaintext=1024)
    assert "private.example" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "bad",
        base64.b64encode(KEY[:16]).decode(),
        base64.b64encode(KEY).decode() + "\n",
        base64.b64encode(KEY).decode().rstrip("="),
    ],
)
def test_invalid_key_never_reflects_input(value):
    _, _, parse = api()
    with pytest.raises(SafetyBlocked) as error:
        parse(value)
    assert str(error.value) == "State encryption key is missing or invalid"


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "duplicate",
        "version",
        "algorithm",
        "base64",
        "short_nonce",
        "bool_version",
        "oversize",
    ],
)
def test_invalid_envelopes_are_bounded_and_strict(change):
    Binding, Cipher, _ = api()
    binding = Binding("state", "owner/repo", "smartgate-state", "primary")
    cipher = Cipher(KEY)
    envelope = cipher.seal(b"x", binding, max_plaintext=1024)
    data = json.loads(envelope)
    if change == "extra":
        data["extra"] = "no"
    elif change == "duplicate":
        envelope = envelope[:-1] + b',"version":1}'
    elif change == "version":
        data["version"] = 2
    elif change == "bool_version":
        data["version"] = True
    elif change == "algorithm":
        data["algorithm"] = "other"
    elif change == "base64":
        data["ciphertext"] += "\n"
    elif change == "short_nonce":
        data["nonce"] = base64.b64encode(b"x").decode()
    elif change == "oversize":
        envelope = b"x" * 10000
    if change not in {"duplicate", "oversize"}:
        envelope = json.dumps(data).encode()
    with pytest.raises(SafetyBlocked):
        cipher.open(envelope, binding, max_plaintext=1024)


def test_plaintext_limit_at_both_boundaries():
    Binding, Cipher, _ = api()
    cipher = Cipher(KEY)
    binding = Binding("state", "owner/repo", "smartgate-state", "primary")
    envelope = cipher.seal(b"x" * 1024, binding, max_plaintext=1024)
    assert cipher.open(envelope, binding, max_plaintext=1024) == b"x" * 1024
    with pytest.raises(SafetyBlocked):
        cipher.seal(b"x" * 1025, binding, max_plaintext=1024)
    with pytest.raises(SafetyBlocked):
        cipher.open(envelope, binding, max_plaintext=512)


@pytest.mark.parametrize(
    "field,value", [("repository", "owner/.."), ("scope", "a/../b"), ("identity", "../primary")]
)
def test_ambiguous_binding_paths_are_rejected(field, value):
    Binding, Cipher, _ = api()
    binding = replace(
        Binding("state", "owner/repo", "smartgate-state", "primary"), **{field: value}
    )
    with pytest.raises(SafetyBlocked):
        Cipher(KEY).seal(b"x", binding, max_plaintext=1024)
