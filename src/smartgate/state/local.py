"""Atomic local files with an exclusive lock and content revision CAS."""

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from smartgate.errors import SafetyBlocked, StateConflict
from smartgate.models import StateDocument
from smartgate.state.codec import MAX_STATE_BYTES, decode_state, encode_state, validate_state_key


class LocalStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(self.path.name + ".lock")
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise StateConflict("Local state is locked") from None
        except OSError:
            raise SafetyBlocked("Local state lock unavailable") from None
        try:
            yield
        finally:
            os.close(descriptor)
            lock_path.unlink()

    def _load(self, profile_id: str) -> StateDocument | None:
        validate_state_key(profile_id)
        try:
            with self.path.open("rb") as file:
                data = file.read(MAX_STATE_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError:
            raise SafetyBlocked("Local state cannot be read") from None
        return decode_state(data, profile_id, hashlib.sha256(data).hexdigest())

    def load(self, profile_id: str) -> StateDocument | None:
        with self.lock():
            return self._load(profile_id)

    def save(self, state: StateDocument, expected_revision: str | None) -> str:
        data = encode_state(state)
        with self.lock():
            current = self._load(state.profile_id)
            revision = current.revision if current else None
            if revision != expected_revision or state.revision != expected_revision:
                raise StateConflict("Local state revision changed")
            temp_path: Path | None = None
            try:
                descriptor, name = tempfile.mkstemp(suffix=".tmp", dir=self.path.parent)
                temp_path = Path(name)
                with os.fdopen(descriptor, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp_path, self.path)
                if os.name != "nt":
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            except OSError:
                raise SafetyBlocked("Durable local state save failed") from None
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
        return hashlib.sha256(data).hexdigest()

    def upgrade(self, old: StateDocument, new: StateDocument) -> str:
        from smartgate.state.codec import validate_state
        from smartgate.state.upgrade import validate_upgrade_pair

        validate_upgrade_pair(old, new)
        # The pure upgrader has already checked the adapter binding; this store checks CAS.
        if old.schema_version != 1 or new.schema_version != 2 or old.revision != new.revision:
            raise StateConflict("Invalid state upgrade precondition")
        validate_state(new, new.profile_id)
        data = encode_state(new)
        with self.lock():
            current = self._load(old.profile_id)
            if current != old:
                raise StateConflict("State changed before format upgrade")
            backup = self.path.with_name(
                self.path.name + "." + (old.revision or "initial") + ".backup"
            )
            original = self.path.read_bytes()
            try:
                with backup.open("xb") as file:
                    file.write(original)
                    file.flush()
                    os.fsync(file.fileno())
            except FileExistsError:
                if backup.read_bytes() != original:
                    raise StateConflict("State backup already differs") from None
            descriptor, name = tempfile.mkstemp(suffix=".tmp", dir=self.path.parent)
            temp = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp, self.path)
                if os.name != "nt":
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                temp.unlink(missing_ok=True)
            revision = hashlib.sha256(data).hexdigest()
            if self._load(new.profile_id) != decode_state(data, new.profile_id, revision):
                raise SafetyBlocked("State upgrade read-back differs")
            return revision
