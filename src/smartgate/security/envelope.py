"""Versioned AES-GCM envelopes with trusted context binding and fixed errors."""

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from smartgate.errors import SafetyBlocked

FORMAT = "smartgate-encrypted"
VERSION = 1
ALGORITHM = "AES-256-GCM"
MAX_PLAINTEXT = 2 * 1024 * 1024


@dataclass(frozen=True)
class EnvelopeBinding:
    purpose: Literal["state", "report"]
    repository: str
    scope: str
    identity: str


def canonical_aad(binding: EnvelopeBinding) -> bytes:
    if (
        binding.purpose not in {"state", "report"}
        or any(
            part in {"", ".", ".."}
            for value in (binding.repository, binding.scope, binding.identity)
            for part in value.split("/")
        )
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", binding.repository) is None
        or any(
            not value
            or len(value) > 200
            or not value.isascii()
            or re.fullmatch(r"[A-Za-z0-9_:/.-]+", value) is None
            for value in (binding.scope, binding.identity)
        )
    ):
        raise SafetyBlocked("Invalid encrypted data binding")
    return json.dumps(
        {
            "format": FORMAT,
            "version": VERSION,
            "algorithm": ALGORITHM,
            "purpose": binding.purpose,
            "repository": binding.repository.lower(),
            "scope": binding.scope,
            "identity": binding.identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError
    return decoded


def parse_encryption_key(value: str) -> bytes:
    try:
        if len(value) != 44:
            raise ValueError
        key = _base64(value)
        if len(key) != 32:
            raise ValueError
        return key
    except (ValueError, TypeError, binascii.Error):
        raise SafetyBlocked("State encryption key is missing or invalid") from None


def envelope_limit(max_plaintext: int) -> int:
    if type(max_plaintext) is not int or not 0 < max_plaintext <= MAX_PLAINTEXT:
        raise SafetyBlocked("Invalid encrypted data size limit")
    return 4 * ((max_plaintext + 16 + 2) // 3) + 512


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class EnvelopeCipher:
    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise SafetyBlocked("State encryption key is missing or invalid")
        self._cipher = AESGCM(key)

    def seal(self, plaintext: bytes, binding: EnvelopeBinding, *, max_plaintext: int) -> bytes:
        limit = envelope_limit(max_plaintext)
        if len(plaintext) > max_plaintext:
            raise SafetyBlocked("Encrypted data exceeds size limit")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, canonical_aad(binding))
        result = json.dumps(
            {
                "format": FORMAT,
                "version": VERSION,
                "algorithm": ALGORITHM,
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("ascii")
        if len(result) > limit:
            raise SafetyBlocked("Encrypted data exceeds size limit")
        return result

    def open(self, envelope: bytes, binding: EnvelopeBinding, *, max_plaintext: int) -> bytes:
        limit = envelope_limit(max_plaintext)
        if len(envelope) > limit:
            raise SafetyBlocked("Encrypted data exceeds size limit")
        aad = canonical_aad(binding)
        try:
            data = json.loads(envelope, object_pairs_hook=_object)
            if (
                not isinstance(data, dict)
                or set(data) != {"format", "version", "algorithm", "nonce", "ciphertext"}
                or data["format"] != FORMAT
                or type(data["version"]) is not int
                or data["version"] != VERSION
                or data["algorithm"] != ALGORITHM
            ):
                raise ValueError
            nonce, ciphertext = _base64(data["nonce"]), _base64(data["ciphertext"])
            if len(nonce) != 12 or not 16 <= len(ciphertext) <= max_plaintext + 16:
                raise ValueError
            plaintext = self._cipher.decrypt(nonce, ciphertext, aad)
            if len(plaintext) > max_plaintext:
                raise ValueError
            return plaintext
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, InvalidTag):
            raise SafetyBlocked("Encrypted data cannot be authenticated or decoded") from None
