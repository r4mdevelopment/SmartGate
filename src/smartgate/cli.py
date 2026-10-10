"""Explicit CLI modes; reports are allowed, DNS/state writes require --apply."""

import argparse
import contextlib
import io
import os
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from smartgate.app import run
from smartgate.config import load_config
from smartgate.errors import ConfigError, SafetyBlocked
from smartgate.models import RunReport
from smartgate.private_report import (
    binding_from_env,
    decrypt_report,
    encrypt_report,
    public_report,
    report_binding,
)
from smartgate.report import exit_code, render_report
from smartgate.runtime import live_dependencies, offline_dependencies
from smartgate.security.envelope import (
    MAX_PLAINTEXT,
    EnvelopeCipher,
    canonical_aad,
    envelope_limit,
    parse_encryption_key,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="smartgate")
    commands = root.add_subparsers(dest="mode", required=True)
    for mode in (
        "validate",
        "preview",
        "sync",
        "recover",
        "rollback",
        "doctor",
        "state-upgrade",
        "state-encrypt",
    ):
        command = commands.add_parser(mode)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--apply", action="store_true")
        command.add_argument("--private-report", action="store_true")
        command.add_argument(
            "--require-no-change",
            action="store_true",
            help="Verify live sync only if its fresh plan has no DNS operations; refuse recovery",
        )
        command.add_argument("--offline", action="store_true")
        command.add_argument("--fixtures", type=Path)
        command.add_argument("--report-dir", type=Path)
        command.add_argument("--bootstrap", action="store_true")
        command.add_argument("--state-provider", choices=("local", "git_branch"))
        command.add_argument("--run-id", dest="rollback_run_id")
        command.add_argument(
            "--previous-run-stopped",
            action="store_true",
            help="Attest previous executor is stopped before early recovery takeover",
        )
    decrypt = commands.add_parser("report-decrypt")
    decrypt.add_argument("--input", type=Path, required=True)
    decrypt.add_argument("--output", type=Path, required=True)
    decrypt.add_argument("--repository", required=True)
    decrypt.add_argument("--run-id", required=True)
    decrypt.add_argument("--run-attempt", required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    private = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or "--private-report" in raw
        or "git_branch" in raw
    )
    try:
        with contextlib.redirect_stderr(io.StringIO()) if private else contextlib.nullcontext():
            args = parser().parse_args(raw)
    except SystemExit as error:
        return int(error.code or 0)
    env = dict(os.environ)
    if args.mode == "report-decrypt":
        try:
            decrypt_binding = report_binding(args.repository, args.run_id, args.run_attempt)
            decrypt_cipher = EnvelopeCipher(
                parse_encryption_key(env.get("SMARTGATE_STATE_ENCRYPTION_KEY", ""))
            )
            with args.input.open("rb") as source:
                envelope = source.read(envelope_limit(MAX_PLAINTEXT) + 1)
            plaintext = decrypt_report(envelope, decrypt_cipher, decrypt_binding)
            with args.output.open("xb") as destination:
                destination.write(plaintext)
        except (SafetyBlocked, OSError, UnicodeError):
            print("Report decryption failed; verify key, binding and exclusive output path")
            return 4
        print("Report decrypted to the requested local file")
        return 0
    cipher = None
    binding = None
    report = RunReport(uuid4().hex, "blocked", (), (), (), (), offline=args.offline)
    redactions = [value for name, value in env.items() if name.startswith("SMARTGATE_DNS_")] + [
        env.get("NEXTDNS_PROFILE_ID", ""),
        env.get("NEXTDNS_API_KEY", ""),
        env.get("GITHUB_TOKEN", ""),
        env.get("SMARTGATE_STATE_ENCRYPTION_KEY", ""),
    ]
    try:
        config = load_config(args.config, env)
        private = private or (
            not args.offline and (args.state_provider or config.state.provider) == "git_branch"
        )
        if private and not args.offline and args.mode != "validate":
            report = replace(report, issue_codes=("encryption_setup_invalid",))
            cipher = EnvelopeCipher(
                parse_encryption_key(env.get("SMARTGATE_STATE_ENCRYPTION_KEY", ""))
            )
            binding = binding_from_env(env)
            canonical_aad(binding)
            report = replace(report, issue_codes=())
        redactions += [
            env.get(name, "")
            for name in (
                config.dns.profile_id_env,
                config.dns.api_key_env,
                config.dns.auth.api_key_env,
                config.dns.auth.username_env,
                config.dns.auth.password_env,
                config.dns.connection.api_url_env,
                config.dns.connection.namespace_env,
                config.dns.verify.doh_url_env,
            )
            if name
        ]
        if args.require_no_change and (
            args.mode != "sync" or not args.apply or args.offline or args.bootstrap
        ):
            raise SafetyBlocked("No-change acceptance requires live sync --apply without bootstrap")
        if args.apply and (
            args.offline
            or args.mode not in {"sync", "recover", "rollback", "state-upgrade", "state-encrypt"}
        ):
            raise SafetyBlocked("Apply requires live sync, recover or rollback")
        if (
            args.apply
            and args.mode not in {"state-upgrade", "state-encrypt"}
            and not config.sync.enabled
        ):
            raise SafetyBlocked("sync.enabled=false blocks apply")
        if args.previous_run_stopped and (
            not args.apply or args.mode not in {"recover", "rollback"}
        ):
            raise SafetyBlocked("Early takeover requires an explicit recovery or rollback apply")
        if args.bootstrap and args.mode not in {"preview", "sync"}:
            raise SafetyBlocked("Bootstrap is only for preview or sync")
        if args.mode == "state-encrypt" and (args.offline or args.rollback_run_id):
            raise SafetyBlocked(
                "State encryption requires live metadata migration without rollback ID"
            )
        if args.mode == "validate":
            report = replace(report, status="preview", errors=("Configuration valid",))
        else:
            if args.state_provider:
                config = replace(config, state=replace(config.state, provider=args.state_provider))
            with ExitStack() as stack:
                if args.offline:
                    if args.fixtures is None:
                        raise ConfigError("Offline mode requires --fixtures")
                    deps = offline_dependencies(config, args.fixtures, args.bootstrap)
                else:
                    deps = live_dependencies(
                        config,
                        env,
                        stack,
                        bootstrap=args.bootstrap,
                        rollback_run_id=args.rollback_run_id,
                        takeover=args.previous_run_stopped,
                    )
                deps = replace(deps, require_no_change=args.require_no_change)
                report = run(config, args.mode, args.apply, deps)
    except KeyboardInterrupt:
        report = replace(
            report,
            status="partial" if args.apply else "blocked",
            errors=("Command interrupted; inspect durable journal before retry",),
            exit_code_override=6 if args.apply else 5,
            issue_codes=("recovery_required" if args.apply else "interrupted",),
        )
    except ConfigError as error:
        report = replace(
            report, errors=(str(error),), exit_code_override=2, issue_codes=("config_invalid",)
        )
    except SafetyBlocked as error:
        report = replace(
            report, errors=(str(error),), issue_codes=report.issue_codes or ("safety_blocked",)
        )
    except Exception:
        report = replace(report, errors=("Controller initialization failed",), exit_code_override=5)
    encrypted = None
    if private:
        if cipher is not None and binding is not None:
            try:
                encrypted = encrypt_report(report, redactions, cipher, binding)
            except SafetyBlocked:
                report = replace(
                    report,
                    status="partial" if args.apply else "blocked",
                    errors=("Private report encryption failed",),
                    issue_codes=("encryption_report_failed",),
                    exit_code_override=6 if args.apply else 5,
                )
        json_report, markdown = public_report(report)
    else:
        json_report, markdown = render_report(report, redactions)
    try:
        if args.report_dir:
            args.report_dir.mkdir(parents=True, exist_ok=True)
            (args.report_dir / "report.enc.json").unlink(missing_ok=True)
            if encrypted is not None:
                (args.report_dir / "report.enc.json").write_bytes(encrypted)
            (args.report_dir / "report.json").write_text(json_report, encoding="utf-8")
            (args.report_dir / "report.md").write_text(markdown, encoding="utf-8")
        print(markdown)
    except (OSError, UnicodeError):
        print("SmartGate report output unavailable; inspect durable journal before retry")
        return 6 if args.apply else 5
    return exit_code(report)
