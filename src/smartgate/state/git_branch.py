"""GitHub state branch storage with fixed-origin transport and non-force CAS."""

import base64
import binascii
import json
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from smartgate.errors import RecoveryRequired, SafetyBlocked, StateConflict
from smartgate.models import StateDocument
from smartgate.security.envelope import EnvelopeCipher, envelope_limit
from smartgate.state.codec import MAX_STATE_BYTES, decode_state, encode_state, validate_state_key
from smartgate.state.encrypted import EncryptedStateCodec, GitStateCodec


@dataclass(frozen=True)
class GitResponse:
    status: int
    data: dict[str, Any]


class GitHubClient:
    """Keep the GitHub credential separate from NextDNS and relay transports."""

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not token or token != token.strip():
            raise SafetyBlocked("GitHub token is required")
        self._calls = 0
        self._deadline = time.monotonic() + 1200
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            trust_env=False,
            follow_redirects=False,
            timeout=10,
            transport=transport,
        )

    @property
    def remaining_calls(self) -> int:
        return max(0, 400 - self._calls)

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> GitResponse:
        if method not in {"GET", "POST", "PATCH"} or not path.startswith("/"):
            raise SafetyBlocked("Invalid GitHub request")
        url = self._client.base_url.join(path)
        if url.scheme != "https" or url.host != "api.github.com" or url.port not in {None, 443}:
            raise SafetyBlocked("GitHub request must stay on the API origin")
        if self._calls >= 400 or time.monotonic() >= self._deadline:
            raise SafetyBlocked("GitHub state request budget exhausted")
        self._calls += 1
        try:
            headers = (
                {"Accept": "application/vnd.github.object+json"} if "/contents/" in url.path else {}
            )
            if method == "GET" and "/git/ref/heads/" in url.path:
                url = url.copy_add_param("_smartgate_read", uuid4().hex)
                headers["Cache-Control"] = "no-cache"
            with self._client.stream(method, url, json=payload, headers=headers) as response:
                parts: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise SafetyBlocked("GitHub state response too large")
                    parts.append(chunk)
                body = b"".join(parts)
                if response.status_code >= 300:
                    return GitResponse(response.status_code, {})
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise SafetyBlocked("Invalid GitHub response")
                return GitResponse(response.status_code, data)
        except (httpx.HTTPError, ValueError):
            # No mutation retries: an interrupted ref update may already have succeeded.
            raise SafetyBlocked("GitHub state request failed; read state before retrying") from None


def _sha(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise SafetyBlocked("Invalid Git object identity")
    return value


MAX_READBACK_RETRIES = 12
MAX_REF_READ_ATTEMPTS = 4


class GitBranchStateStore:
    def __init__(
        self,
        repo: str,
        branch: str,
        token: str,
        http: GitHubClient | None = None,
        *,
        codec: GitStateCodec,
    ) -> None:
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is None:
            raise SafetyBlocked("Invalid GitHub repository")
        if (
            re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_/-]*", branch) is None
            or branch.endswith("/")
            or "//" in branch
            or branch in {"main", "master"}
        ):
            raise SafetyBlocked("Invalid state branch")
        self._codec = codec
        self._base = "/repos/" + repo
        self._branch = branch
        self._http = http if http is not None else GitHubClient(token)
        self._author_cache: dict[str, str] | None = None
        self._tree_cache: tuple[str, str] | None = None
        self._state_cache: StateDocument | None = None
        self._confirmed_save: tuple[str, str] | None = None
        self._readback_retries_left = MAX_READBACK_RETRIES

    def check_write_budget(self, operation_count: int) -> None:
        # Two six-request checkpoints plus one fresh ref read per operation.
        # Reserve bootstrap, author/tree lookup, final save, observations and read-back retries.
        if type(operation_count) is not int or operation_count < 0:
            raise SafetyBlocked("Invalid state operation budget")
        if operation_count * 13 + 35 + self._readback_retries_left > self._http.remaining_calls:
            raise SafetyBlocked(
                "GitHub state request budget insufficient; reduce the reviewed batch"
            )

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = self._http.request(method, self._base + path, payload)
        if response.status in {409, 422}:
            raise StateConflict("GitHub state changed or update conflicted")
        if response.status not in {200, 201}:
            raise SafetyBlocked("GitHub state storage unavailable")
        return response.data

    def _head(self) -> str | None:
        confirmed = self._confirmed_save
        for attempt in range(MAX_REF_READ_ATTEMPTS):
            revision = self._read_head_once()
            if confirmed is None or revision == confirmed[0]:
                return revision
            if revision != confirmed[1]:
                # Another revision or an absent ref is not an acknowledged parent delay.
                self._confirmed_save = None
                return revision
            if attempt == MAX_REF_READ_ATTEMPTS - 1 or not self._readback_retries_left:
                raise SafetyBlocked("GitHub state read-back still returns the previous revision")
            self._readback_retries_left -= 1
            time.sleep(0.1 * 2**attempt)
        raise SafetyBlocked("GitHub state read-back unavailable")

    def _read_head_once(self) -> str | None:
        response = self._http.request("GET", self._base + "/git/ref/heads/" + self._branch)
        if response.status == 404:
            return None
        if response.status != 200:
            raise SafetyBlocked("GitHub state reference cannot be read")
        try:
            return _sha(response.data["object"]["sha"])
        except (KeyError, TypeError):
            raise SafetyBlocked("Invalid GitHub reference") from None

    def _load_at(self, profile_id: str, revision: str) -> StateDocument | None:
        if self._state_cache is not None and (
            self._state_cache.revision == revision and self._state_cache.profile_id == profile_id
        ):
            return self._state_cache
        data = self._read_data_at(profile_id, revision)
        if data is None:
            return None
        state = self._codec.decode(data, profile_id, revision)
        self._state_cache = state
        return state

    def _read_data_at(self, profile_id: str, revision: str) -> bytes | None:
        response = self._http.request(
            "GET",
            self._base + "/contents/state/" + profile_id + ".json?ref=" + quote(revision),
        )
        if response.status == 404:
            return None
        if response.status != 200:
            raise SafetyBlocked("GitHub state file cannot be read")
        try:
            if response.data["encoding"] == "none":
                # Contents objects omit large payloads; fetch only the immutable blob on our origin.
                size = response.data["size"]
                blob = _sha(response.data["sha"])
                if (
                    response.data.get("type") != "file"
                    or response.data.get("content") != ""
                    or type(size) is not int
                    or not 0 < size <= envelope_limit(MAX_STATE_BYTES)
                ):
                    raise SafetyBlocked("Invalid large state metadata")
                response = self._http.request("GET", self._base + "/git/blobs/" + blob)
                if (
                    response.status != 200
                    or response.data.get("sha") != blob
                    or response.data.get("size") != size
                ):
                    raise SafetyBlocked("GitHub state blob read-back differs")
            if response.data["encoding"] != "base64":
                raise SafetyBlocked("Unsupported GitHub state encoding")
            content = response.data["content"]
            if not isinstance(content, str):
                raise SafetyBlocked("Invalid GitHub state content")
            data = base64.b64decode("".join(content.split()), validate=True)
        except (KeyError, TypeError, binascii.Error):
            raise SafetyBlocked("Invalid GitHub state content") from None
        if len(data) > envelope_limit(MAX_STATE_BYTES):
            raise SafetyBlocked("GitHub state file exceeds storage limit")
        return data

    def inspect_plaintext_for_encryption(self, state_key: str) -> StateDocument:
        """Explicit migration read; never populates the normal encrypted cache."""
        if not isinstance(self._codec, EncryptedStateCodec):
            raise SafetyBlocked("Migration requires encrypted destination storage")
        validate_state_key(state_key)
        revision = self._head()
        if revision is None:
            raise SafetyBlocked("Migration requires existing ownership state")
        data = self._read_data_at(state_key, revision)
        if data is None:
            raise SafetyBlocked("Migration ownership state is absent")
        return decode_state(data, state_key, revision)

    def encrypt_existing_state(self, old: StateDocument) -> str:
        if old.revision is None or self.inspect_plaintext_for_encryption(old.profile_id) != old:
            raise StateConflict("State changed before encryption")
        if old.journal and old.journal.phase not in {"verified", "rolled_back"}:
            raise SafetyBlocked("Unfinished journal cannot be encrypted by migration")
        revision = self._commit(old, old.revision)
        if self._head() != old.revision:
            raise StateConflict("State changed before encryption ref update")
        try:
            response = self._request(
                "PATCH", "/git/refs/heads/" + self._branch, {"sha": revision, "force": False}
            )
            if _sha(response["object"]["sha"]) != revision:
                raise SafetyBlocked("Encryption reference confirmation disagrees")
        except StateConflict:
            raise
        except (SafetyBlocked, KeyError, TypeError):
            # Do not repeat an ambiguous write; verify the exact proposed commit.
            pass
        self._state_cache = None
        try:
            if self._head() != revision:
                raise SafetyBlocked("Encryption commit is not the current head")
            actual = self._load_at(old.profile_id, revision)
            if actual != replace(old, revision=revision) or self._head() != revision:
                raise SafetyBlocked("Encryption read-back differs")
        except (SafetyBlocked, StateConflict):
            raise RecoveryRequired(
                "State encryption result unknown; inspect before retry"
            ) from None
        return revision

    def rotate_encryption(
        self, old: StateDocument, cipher: EnvelopeCipher, backup: Path, *, apply: bool
    ) -> str | None:
        """Rotate one bound state with an exclusive encrypted backup and one CAS write."""
        if not isinstance(self._codec, EncryptedStateCodec) or old.revision is None:
            raise SafetyBlocked("Rotation requires existing encrypted Git state")
        parent = old.revision
        if self._head() != parent:
            raise StateConflict("State changed before key rotation")
        raw = self._read_data_at(old.profile_id, parent)
        if raw is None or self._codec.decode(raw, old.profile_id, parent) != old:
            raise StateConflict("Rotation source differs from saved state")
        commit = self._request("GET", "/git/commits/" + parent)
        tree = _sha(commit["tree"]["sha"])
        inventory = self._request("GET", "/git/trees/" + tree + "?recursive=1")
        entries = inventory.get("tree")
        if inventory.get("truncated") is not False or not isinstance(entries, list):
            raise SafetyBlocked("Rotation requires a complete state tree")
        state_files = [
            entry.get("path")
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and isinstance(entry.get("path"), str)
            and entry["path"].startswith("state/")
        ]
        if state_files != ["state/" + old.profile_id + ".json"]:
            raise SafetyBlocked("Rotation requires a single state document on this branch")
        updated_codec = self._codec.with_cipher(cipher)
        candidate = updated_codec.encode(old)
        if updated_codec.decode(candidate, old.profile_id, parent) != old:
            raise SafetyBlocked("New key round-trip failed")
        try:
            self._codec.decode(candidate, old.profile_id, parent)
        except SafetyBlocked:
            pass
        else:
            raise SafetyBlocked("New encryption key must differ from the current key")
        if not apply:
            return None
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open("xb") as destination:
                destination.write(raw)
                destination.flush()
                os.fsync(destination.fileno())
            if backup.read_bytes() != raw:
                raise SafetyBlocked("Rotation backup verification failed")
        except OSError:
            raise SafetyBlocked("Rotation requires a new writable backup path") from None
        if self._head() != parent:
            raise StateConflict("State changed after rotation backup")
        previous_codec = self._codec
        self._codec = updated_codec
        self._state_cache = None
        try:
            revision = self._commit(old, parent)
            if self._head() != parent:
                raise StateConflict("State changed before rotation reference update")
        except Exception:
            self._codec = previous_codec
            raise
        try:
            response = self._request(
                "PATCH", "/git/refs/heads/" + self._branch, {"sha": revision, "force": False}
            )
            if _sha(response["object"]["sha"]) != revision:
                raise SafetyBlocked("Rotation reference confirmation disagrees")
            self._confirmed_save = (revision, parent)
        except StateConflict:
            self._codec = previous_codec
            raise
        except (SafetyBlocked, KeyError, TypeError):
            # An ambiguous write is never sent again; inspect its exact proposed commit.
            pass
        try:
            if self._head() != revision:
                raise SafetyBlocked("Rotation commit is not the current head")
            data = self._read_data_at(old.profile_id, revision)
            if (
                data is None
                or updated_codec.decode(data, old.profile_id, revision)
                != replace(old, revision=revision)
                or self._head() != revision
            ):
                raise SafetyBlocked("Rotation read-back differs")
        except (SafetyBlocked, StateConflict):
            raise RecoveryRequired("Key rotation result unknown; inspect before retry") from None
        return revision

    def load(self, profile_id: str) -> StateDocument | None:
        validate_state_key(profile_id)
        revision = self._head()
        return None if revision is None else self._load_at(profile_id, revision)

    def _author(self) -> dict[str, str]:
        if self._author_cache is not None:
            return dict(self._author_cache)
        response = self._http.request("GET", "/user")
        identity = response.data
        if response.status == 403:
            repository = self._http.request("GET", self._base)
            identity = repository.data.get("owner", {})
            if (
                repository.status != 200
                or not isinstance(identity, dict)
                or identity.get("type") != "User"
            ):
                raise SafetyBlocked("State commit requires a personal repository owner")
        elif response.status != 200:
            raise SafetyBlocked("Authenticated GitHub identity unavailable")
        login, identifier = identity.get("login"), identity.get("id")
        if (
            not isinstance(login, str)
            or re.fullmatch(r"[A-Za-z0-9-]+", login) is None
            or type(identifier) is not int
            or identifier <= 0
        ):
            raise SafetyBlocked("Authenticated GitHub identity unavailable")
        self._author_cache = {
            "name": login,
            "email": f"{identifier}+{login}@users.noreply.github.com",
        }
        return dict(self._author_cache)

    def _commit(
        self, state: StateDocument, parent: str | None, remove_key: str | None = None
    ) -> str:
        data = self._codec.encode(state)
        try:
            blob = _sha(
                self._request(
                    "POST",
                    "/git/blobs",
                    {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
                )["sha"]
            )
            tree_payload: dict[str, Any] = {
                "tree": [
                    {
                        "path": "state/" + state.profile_id + ".json",
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob,
                    }
                ]
            }
            if remove_key is not None:
                validate_state_key(remove_key)
                tree_payload["tree"].append(
                    {
                        "path": "state/" + remove_key + ".json",
                        "mode": "100644",
                        "type": "blob",
                        "sha": None,
                    }
                )
            if parent is not None:
                if self._tree_cache is not None and self._tree_cache[0] == parent:
                    tree_payload["base_tree"] = self._tree_cache[1]
                else:
                    commit = self._request("GET", "/git/commits/" + parent)
                    tree_payload["base_tree"] = _sha(commit["tree"]["sha"])
            tree = _sha(self._request("POST", "/git/trees", tree_payload)["sha"])
            author = self._author()
            revision = _sha(
                self._request(
                    "POST",
                    "/git/commits",
                    {
                        "message": "state: persist ownership and journal\n\nUpdate: " + uuid4().hex,
                        "tree": tree,
                        "parents": [] if parent is None else [parent],
                        "author": author,
                        "committer": author,
                    },
                )["sha"]
            )
            self._tree_cache = (revision, tree)
            return revision
        except (KeyError, TypeError):
            raise SafetyBlocked("Invalid GitHub object response") from None

    def bootstrap(self, state: StateDocument) -> str:
        """Explicit apply-only creation of an orphan state branch; never called by load."""
        if state.revision is not None:
            raise StateConflict("Bootstrap requires initial state")
        encode_state(state)
        if self._head() is not None:
            raise StateConflict("State branch already exists")
        revision = self._commit(state, None)
        response = self._request(
            "POST", "/git/refs", {"ref": "refs/heads/" + self._branch, "sha": revision}
        )
        try:
            if _sha(response["object"]["sha"]) != revision:
                raise SafetyBlocked("State reference confirmation disagrees")
        except (KeyError, TypeError):
            raise SafetyBlocked("Invalid state reference confirmation") from None
        self._state_cache = decode_state(encode_state(state), state.profile_id, revision)
        return revision

    def save(self, state: StateDocument, expected_revision: str | None) -> str:
        encode_state(state)
        parent = self._head()
        if parent is None:
            raise SafetyBlocked("State branch requires explicit apply bootstrap")
        if state.revision != expected_revision:
            raise StateConflict("State revision precondition disagrees")
        if expected_revision is None:
            if self._load_at(state.profile_id, parent) is not None:
                raise StateConflict("State file already exists")
        elif parent != expected_revision:
            raise StateConflict("State branch revision changed")
        revision = self._commit(state, parent)
        if self._head() != parent:
            raise StateConflict("State branch changed before ref update")
        response = self._request(
            "PATCH", "/git/refs/heads/" + self._branch, {"sha": revision, "force": False}
        )
        try:
            if _sha(response["object"]["sha"]) != revision:
                raise SafetyBlocked("State reference confirmation disagrees")
        except (KeyError, TypeError):
            raise SafetyBlocked("Invalid state reference confirmation") from None
        self._state_cache = decode_state(encode_state(state), state.profile_id, revision)
        self._confirmed_save = (revision, parent)
        return revision

    def upgrade(self, old: StateDocument, new: StateDocument) -> str:
        from smartgate.state.upgrade import validate_upgrade_pair

        validate_upgrade_pair(old, new)
        parent = self._head()
        if parent is None or parent != old.revision or self._load_at(old.profile_id, parent) != old:
            raise StateConflict("Legacy state changed before upgrade")
        if old.profile_id == new.profile_id or self._load_at(new.profile_id, parent) is not None:
            raise StateConflict("Upgrade destination already exists")
        revision = self._commit(new, parent, remove_key=old.profile_id)
        if self._head() != parent:
            raise StateConflict("State changed before upgrade ref update")
        try:
            response = self._request(
                "PATCH", "/git/refs/heads/" + self._branch, {"sha": revision, "force": False}
            )
            if _sha(response["object"]["sha"]) != revision:
                raise SafetyBlocked("Upgrade reference confirmation disagrees")
        except StateConflict:
            raise
        except (SafetyBlocked, KeyError, TypeError):
            # A lost response is accepted only if the exact proposed commit is visible.
            if self._head() != revision:
                raise SafetyBlocked(
                    "State upgrade result unknown; inspect both paths before retry"
                ) from None
        self._state_cache = None
        actual_head = self._head()
        if (
            actual_head != revision
            or self._load_at(old.profile_id, revision) is not None
            or self._load_at(new.profile_id, revision) != replace(new, revision=revision)
        ):
            raise SafetyBlocked("State upgrade read-back differs")
        return revision
