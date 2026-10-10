"""CLI adapter composition; offline fixtures never construct network transports."""

from collections.abc import Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from smartgate.app import AppDependencies
from smartgate.config import AppConfig
from smartgate.dns.registry import create_dns_bundle
from smartgate.errors import ConfigError, SafetyBlocked
from smartgate.health.tls import TlsHealthChecker
from smartgate.models import (
    DesiredRewrite,
    DnsCheck,
    DnsRecord,
    ProbePolicy,
    ProbeResult,
    ProviderConfig,
    RelayCandidate,
    RelaySnapshot,
    StateDocument,
)
from smartgate.ports import StateStore
from smartgate.providers.geohide import SourceClient
from smartgate.providers.hosts import parse_hosts
from smartgate.providers.registry import create_relay_provider
from smartgate.security.envelope import EnvelopeCipher, parse_encryption_key
from smartgate.state.encrypted import EncryptedStateCodec
from smartgate.state.encryption_migration import migrate_state_encryption
from smartgate.state.git_branch import GitBranchStateStore, GitHubClient
from smartgate.state.local import LocalStateStore
from smartgate.state.upgrade import upgrade_legacy_state


class FixtureRelay:
    def __init__(self, text: str) -> None:
        self.text = text

    def fetch(self, config: ProviderConfig, now: datetime) -> RelaySnapshot:
        return parse_hosts(self.text, config, "offline-fixture", now)


class FixtureChecker:
    def probe(self, candidate: RelayCandidate, policy: ProbePolicy) -> ProbeResult:
        return ProbeResult(
            candidate,
            "tls_ok",
            datetime.now(UTC),
            "local",
            None,
            "simulated fixture result; no live TLS observation",
        )


class EmptyDns:
    def list_records(self) -> tuple[DnsRecord, ...]:
        return ()

    def create(self, record: DesiredRewrite) -> DnsRecord:
        raise SafetyBlocked("Offline DNS is read-only")

    def delete(self, record: DnsRecord) -> None:
        raise SafetyBlocked("Offline DNS is read-only")

    def replace(self, before: DnsRecord, after: DesiredRewrite) -> DnsRecord:
        raise SafetyBlocked("Offline DNS is read-only")


class EmptyStore:
    def load(self, profile_id: str) -> StateDocument | None:
        return None

    def save(self, state: StateDocument, expected_revision: str | None) -> str:
        raise SafetyBlocked("Offline state is read-only")


def offline_dependencies(config: AppConfig, fixtures: Path, bootstrap: bool) -> AppDependencies:
    try:
        text = (fixtures / "hosts" / "geohide-chatgpt.hosts").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ConfigError("Offline hosts fixture is missing or unreadable") from None

    def verify(profile: str, desired: tuple[DesiredRewrite, ...]) -> tuple[DnsCheck, ...]:
        return ()

    return AppDependencies(
        {p.id: FixtureRelay(text) for p in config.providers},
        FixtureChecker(),
        EmptyDns(),
        EmptyStore(),
        verify,
        lambda: datetime.now(UTC),
        "abcdef",
        bootstrap=bootstrap,
        subtree_capabilities=True,
        offline=True,
    )


def live_dependencies(
    config: AppConfig,
    env: Mapping[str, str],
    stack: ExitStack,
    *,
    bootstrap: bool,
    rollback_run_id: str | None,
    takeover: bool,
) -> AppDependencies:
    def required(name: str) -> str:
        value = env.get(name, "")
        if not value or value != value.strip():
            raise ConfigError("Required environment variable missing or invalid: " + name)
        return value

    # Validate all needed credentials before constructing clients.
    repo = required("GITHUB_REPOSITORY") if config.state.provider == "git_branch" else ""
    token = required("GITHUB_TOKEN") if config.state.provider == "git_branch" else ""
    cipher = (
        EnvelopeCipher(parse_encryption_key(env.get("SMARTGATE_STATE_ENCRYPTION_KEY", "")))
        if config.state.provider == "git_branch"
        else None
    )
    bundle = create_dns_bundle(config.dns, env, stack)
    profile = bundle.target.namespace if config.dns.legacy else bundle.target.instance_id
    source = SourceClient()
    stack.callback(source.close)
    state_bootstrap = None
    preflight_writes = None
    store: StateStore
    if config.state.provider == "git_branch":
        github = GitHubClient(token)
        stack.callback(github.close)
        assert cipher is not None
        git_store = GitBranchStateStore(
            repo,
            config.state.branch,
            token,
            github,
            codec=EncryptedStateCodec(cipher, repo, config.state.branch),
        )
        store = git_store
        state_bootstrap = git_store.bootstrap
        preflight_writes = git_store.check_write_budget
    else:
        store = LocalStateStore(config.state.path)

    def upgrade_state(apply: bool) -> None:
        if config.dns.legacy or bundle.target.provider != "nextdns":
            raise SafetyBlocked("State upgrade requires the new NextDNS binding configuration")
        old = store.load(bundle.target.namespace)
        if not isinstance(store, LocalStateStore):
            existing = store.load(bundle.target.instance_id)
            if existing is not None:
                raise SafetyBlocked("Upgrade destination already exists; inspect current state")
        if old is None:
            raise SafetyBlocked("Legacy ownership state is absent")
        new = upgrade_legacy_state(old, bundle.target)
        before = bundle.provider.list_records()
        if any(
            DnsRecord(record.record_id, record.domain, record.content) not in before
            for record in old.managed_records
        ):
            raise SafetyBlocked("Legacy ownership read-back changed; upgrade blocked")
        if apply:
            if isinstance(store, (LocalStateStore, GitBranchStateStore)):
                store.upgrade(old, new)
            else:
                raise SafetyBlocked("Store does not support explicit upgrade")
            if bundle.provider.list_records() != before:
                raise SafetyBlocked("DNS changed during state upgrade; inspect writer activity")

    def encrypt_state(apply: bool) -> None:
        if not isinstance(store, GitBranchStateStore) or config.dns.legacy:
            raise SafetyBlocked("Encryption migration requires bound Git state")
        migrate_state_encryption(store, bundle.target, bundle.provider, apply=apply)

    # Subtree semantics were verified in the public NextDNS compatibility evidence;
    # every apply still checks actual profile answers and exact API read-back.
    return AppDependencies(
        {p.id: create_relay_provider(p, source) for p in config.providers},
        TlsHealthChecker(),
        bundle.provider,
        store,
        lambda profile, desired: bundle.verify(desired),
        lambda: datetime.now(UTC),
        profile,
        bootstrap=bootstrap,
        subtree_capabilities=bundle.capabilities.subtree == "verified",
        vantage="github_runner" if env.get("GITHUB_ACTIONS") == "true" else "local",
        rollback_run_id=rollback_run_id,
        allow_recovery_takeover=takeover,
        bootstrap_state=state_bootstrap,
        preflight_writes=preflight_writes,
        dns_target=None if config.dns.legacy else bundle.target,
        production_apply=bundle.capabilities.production_apply,
        upgrade_state=upgrade_state,
        encrypt_state=encrypt_state,
        legacy_state_key=bundle.legacy_state_key,
        load_legacy_state=(lambda: store.load(bundle.target.namespace))
        if isinstance(store, LocalStateStore)
        and bundle.legacy_state_key is not None
        and not config.dns.legacy
        else None,
    )
