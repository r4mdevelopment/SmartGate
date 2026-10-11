"""Storage codecs keep encryption outside reconciliation values."""

from typing import Protocol

from smartgate.models import StateDocument
from smartgate.security.envelope import EnvelopeBinding, EnvelopeCipher
from smartgate.state.codec import MAX_STATE_BYTES, decode_state, encode_state, validate_state_key


class GitStateCodec(Protocol):
    def encode(self, state: StateDocument) -> bytes: ...
    def decode(self, data: bytes, state_key: str, revision: str) -> StateDocument: ...


class PlainStateCodec:
    """Explicit fixture codec; never selected by live Git runtime."""

    def encode(self, state: StateDocument) -> bytes:
        return encode_state(state)

    def decode(self, data: bytes, state_key: str, revision: str) -> StateDocument:
        return decode_state(data, state_key, revision)


class EncryptedStateCodec:
    def __init__(self, cipher: EnvelopeCipher, repository: str, branch: str) -> None:
        self._cipher, self._repository, self._branch = cipher, repository, branch

    def with_cipher(self, cipher: EnvelopeCipher) -> "EncryptedStateCodec":
        return EncryptedStateCodec(cipher, self._repository, self._branch)

    def _binding(self, state_key: str) -> EnvelopeBinding:
        validate_state_key(state_key)
        return EnvelopeBinding("state", self._repository, self._branch, state_key)

    def encode(self, state: StateDocument) -> bytes:
        return self._cipher.seal(
            encode_state(state), self._binding(state.profile_id), max_plaintext=MAX_STATE_BYTES
        )

    def decode(self, data: bytes, state_key: str, revision: str) -> StateDocument:
        plaintext = self._cipher.open(data, self._binding(state_key), max_plaintext=MAX_STATE_BYTES)
        return decode_state(plaintext, state_key, revision)
