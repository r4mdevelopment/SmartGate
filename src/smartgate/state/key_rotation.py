"""Explicit encrypted-state key rotation, with no DNS mutation calls."""

from pathlib import Path

from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.errors import RecoveryRequired, SafetyBlocked
from smartgate.models import DnsRecord
from smartgate.ports import DnsProvider
from smartgate.reconcile.common import validate_journal
from smartgate.security.envelope import EnvelopeCipher
from smartgate.state.git_branch import GitBranchStateStore


def rotate_state_key(
    store: GitBranchStateStore,
    target: DnsTarget,
    dns: DnsProvider,
    cipher: EnvelopeCipher,
    backup: Path,
    *,
    apply: bool,
) -> None:
    old = store.load(target.instance_id)
    if (
        old is None
        or old.schema_version != 2
        or old.target_id != target.instance_id
        or (old.target_fingerprint != target_fingerprint(target))
    ):
        raise SafetyBlocked("Key rotation requires matching bound state")
    if old.journal:
        if old.journal.phase not in {"verified", "rolled_back"}:
            raise SafetyBlocked("Recover unfinished journal before key rotation")
        validate_journal(old)
    before = dns.list_records()
    if any(DnsRecord(r.record_id, r.domain, r.content) not in before for r in old.managed_records):
        raise SafetyBlocked("Owned DNS records changed; rotation blocked")
    if apply and dns.list_records() != before:
        raise SafetyBlocked("DNS snapshot changed before rotation")
    store.rotate_encryption(old, cipher, backup, apply=apply)
    if apply:
        try:
            if dns.list_records() != before:
                raise SafetyBlocked("DNS snapshot changed during rotation")
        except Exception:
            raise RecoveryRequired(
                "Key rotation committed; DNS read-back requires inspection"
            ) from None
