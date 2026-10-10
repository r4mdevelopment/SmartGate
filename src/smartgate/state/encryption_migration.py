"""Explicit conversion of bound ownership metadata without DNS mutations."""

from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.errors import RecoveryRequired, SafetyBlocked
from smartgate.models import DnsRecord
from smartgate.ports import DnsProvider
from smartgate.reconcile.common import validate_journal
from smartgate.state.git_branch import GitBranchStateStore


def migrate_state_encryption(
    store: GitBranchStateStore, target: DnsTarget, dns: DnsProvider, *, apply: bool
) -> None:
    old = store.inspect_plaintext_for_encryption(target.instance_id)
    if (
        old.schema_version != 2
        or old.target_id != target.instance_id
        or old.target_fingerprint != target_fingerprint(target)
    ):
        raise SafetyBlocked("Migration DNS destination differs from ownership state")
    if old.journal:
        if old.journal.phase not in {"verified", "rolled_back"}:
            raise SafetyBlocked("Recover unfinished journal before encryption migration")
        validate_journal(old)
    before = dns.list_records()
    if any(
        DnsRecord(record.record_id, record.domain, record.content) not in before
        for record in old.managed_records
    ):
        raise SafetyBlocked("Owned DNS records changed; encryption migration blocked")
    if apply:
        if dns.list_records() != before:
            raise SafetyBlocked("DNS snapshot changed before encryption migration")
        store.encrypt_existing_state(old)
        try:
            if dns.list_records() != before:
                raise SafetyBlocked("DNS snapshot changed during encryption migration")
        except Exception:
            raise RecoveryRequired(
                "Encryption committed; DNS read-back requires inspection"
            ) from None
