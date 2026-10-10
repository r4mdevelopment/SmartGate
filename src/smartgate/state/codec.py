"""Strict credential-free state JSON serialization."""

import ipaddress
import json
import re
import types
from dataclasses import fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints

from smartgate.catalog import normalize_domain
from smartgate.errors import SafetyBlocked
from smartgate.models import ChangePlan, PlanContext, RunJournal, StateDocument

MAX_STATE_BYTES = 1024 * 1024


def validate_profile(profile_id: str) -> None:
    if re.fullmatch(r"[0-9a-f]{6}", profile_id) is None:
        raise SafetyBlocked("Invalid state profile")


def _encode(value: Any) -> Any:
    if is_dataclass(value):
        return {
            field.name: _encode(getattr(value, field.name))
            for field in fields(value)
            if field.name not in {"target_id", "target_fingerprint"} or getattr(value, field.name)
        }
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise SafetyBlocked("State timestamps must include a timezone")
        return value.isoformat()
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _decode(value: Any, annotation: Any) -> Any:
    origin, arguments = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        for option in arguments:
            try:
                return _decode(value, option)
            except SafetyBlocked:
                pass
        raise SafetyBlocked("Invalid state union value")
    if origin is Literal:
        if value not in arguments or isinstance(value, bool):
            raise SafetyBlocked("Invalid state enum")
        return value
    if origin is tuple:
        if not isinstance(value, list) or len(value) > 10000:
            raise SafetyBlocked("Invalid state sequence")
        return tuple(_decode(item, arguments[0]) for item in value)
    if annotation is datetime:
        try:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None:
                raise ValueError
            return timestamp
        except (ValueError, TypeError):
            raise SafetyBlocked("Invalid state timestamp") from None
    if is_dataclass(annotation):
        expected = {field.name for field in fields(annotation)}
        optional = (
            {"policy", "desired", "removals"}
            if annotation is ChangePlan
            else {"services"}
            if annotation is PlanContext
            else {
                "before_managed",
                "before_selections",
                "target_selections",
                "rollback_started",
                "execution_run_id",
                "execution_deadline",
            }
            if annotation is RunJournal
            else set()
        )
        optional |= {"target_id", "target_fingerprint"} & expected
        if not isinstance(value, dict) or set(value) - expected or expected - set(value) - optional:
            raise SafetyBlocked("Invalid state fields")
        hints = get_type_hints(annotation)
        return cast(Any, annotation)(
            **{key: _decode(item, hints[key]) for key, item in value.items()}
        )
    if annotation is type(None):
        if value is not None:
            raise SafetyBlocked("Invalid null value")
    elif annotation is float:
        if type(value) not in (float, int):
            raise SafetyBlocked("Invalid numeric state value")
    elif type(value) is not annotation:
        raise SafetyBlocked("Invalid state value type")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SafetyBlocked("Duplicate state JSON key")
        result[key] = value
    return result


def validate_state(state: StateDocument, profile_id: str) -> None:
    if state.schema_version == 1:
        validate_profile(profile_id)
        if state.target_id or state.target_fingerprint:
            raise SafetyBlocked("Legacy state cannot carry target binding")
    elif state.schema_version == 2:
        validate_instance(profile_id)
        if (
            state.target_id != profile_id
            or re.fullmatch(r"[0-9a-f]{64}", state.target_fingerprint) is None
        ):
            raise SafetyBlocked("Invalid DNS state binding")
        if state.journal and (
            state.journal.target_id != state.target_id
            or state.journal.target_fingerprint != state.target_fingerprint
            or state.journal.plan.target_id != state.target_id
            or state.journal.plan.target_fingerprint != state.target_fingerprint
        ):
            raise SafetyBlocked("Journal binding differs from state")
    else:
        raise SafetyBlocked("Unsupported state version")
    if state.profile_id != profile_id:
        raise SafetyBlocked("State destination mismatch")
    for values, attribute in (
        (state.managed_records, "domain"),
        (state.managed_records, "record_id"),
        (state.selections, "domain"),
        (state.probe_cursors, "domain"),
        (state.last_sources, "provider_id"),
    ):
        keys = [getattr(value, attribute) for value in values]
        if len(keys) != len(set(keys)):
            raise SafetyBlocked("Duplicate state identity")
    for record in state.managed_records:
        if not record.record_id or not record.provider_id:
            raise SafetyBlocked("Invalid ownership identity")
        if normalize_domain(record.domain) != record.domain:
            raise SafetyBlocked("Noncanonical state domain")
        address = ipaddress.ip_address(record.content)
        if (
            address.version != 4
            or not address.is_global
            or address.is_multicast
            or address.is_reserved
        ):
            raise SafetyBlocked("Invalid owned IPv4")
    for cursor in state.probe_cursors:
        if cursor.index < 0 or normalize_domain(cursor.domain) != cursor.domain:
            raise SafetyBlocked("Invalid probe cursor")
    journal = state.journal
    if journal is not None:
        count = len(journal.plan.operations)
        if len(journal.operation_statuses) != count or len(journal.operation_results) != count:
            raise SafetyBlocked("Journal operation lengths disagree")
        if not set(journal.operation_statuses) <= {
            "pending",
            "confirmed",
            "unknown",
            "failed",
            "rolled_back",
            "rollback_pending",
        }:
            raise SafetyBlocked("Invalid operation status")
        for operation, status, result in zip(
            journal.plan.operations,
            journal.operation_statuses,
            journal.operation_results,
            strict=True,
        ):
            if normalize_domain(operation.domain) != operation.domain:
                raise SafetyBlocked("Invalid operation domain")
            if operation.kind == "create":
                valid = operation.before is None and operation.after is not None
            elif operation.kind == "replace":
                valid = operation.before is not None and operation.after is not None
            else:
                valid = operation.before is not None and operation.after is None
            if not valid:
                raise SafetyBlocked("Operation shape is invalid")
            for intent_record in (operation.before, operation.after):
                if intent_record is not None and intent_record.domain != operation.domain:
                    raise SafetyBlocked("Operation domain disagrees")
            if status == "confirmed":
                if operation.kind == "delete":
                    valid_result = result is None
                else:
                    valid_result = (
                        result is not None
                        and bool(result.record_id)
                        and operation.after is not None
                        and result.domain == operation.after.domain
                        and result.content == operation.after.ipv4
                    )
                if not valid_result:
                    raise SafetyBlocked("Confirmed result does not match intent")
        if journal.phase == "verified" and any(
            s != "confirmed" for s in journal.operation_statuses
        ):
            raise SafetyBlocked("Verified journal contains unconfirmed operations")
        if journal.phase == "rolled_back" and any(
            s != "rolled_back" for s in journal.operation_statuses
        ):
            raise SafetyBlocked("Rolled back journal contains pending operations")


def encode_state(state: StateDocument) -> bytes:
    try:
        value = _encode(replace(state, revision=None))
        checked = _decode(value, StateDocument)
        validate_state(checked, state.profile_id)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
        if len(encoded) > MAX_STATE_BYTES:
            raise SafetyBlocked("State size limit exceeded")
        return encoded
    except (ValueError, TypeError, RecursionError):
        raise SafetyBlocked("State cannot be serialized") from None


def decode_state(data: bytes, profile_id: str, revision: str) -> StateDocument:
    try:
        if len(data) > MAX_STATE_BYTES:
            raise SafetyBlocked("State size limit exceeded")
        value = json.loads(
            data,
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        state: StateDocument = _decode(value, StateDocument)
        validate_state(state, profile_id)
        return replace(state, revision=revision)
    except (ValueError, TypeError, RecursionError):
        raise SafetyBlocked("State is malformed") from None


def validate_instance(value: str) -> None:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value) is None:
        raise SafetyBlocked("Invalid state instance")


def validate_state_key(value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{6}", value) is None:
        validate_instance(value)
