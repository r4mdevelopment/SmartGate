"""Validate Actions dispatch inputs before invoking the controller."""

import json
import os
from collections.abc import Mapping
from pathlib import Path

from smartgate.cli import main as controller_main
from smartgate.errors import SafetyBlocked


def _flag(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name, "false")
    if value not in {"true", "false"}:
        raise SafetyBlocked("Invalid workflow boolean: " + name)
    return value == "true"


def controller_arguments(env: Mapping[str, str]) -> list[str]:
    event = env.get("SMARTGATE_EVENT", "")
    command = env.get("SMARTGATE_COMMAND", "sync")
    if event not in {"workflow_dispatch", "schedule"} or command not in {
        "preview",
        "sync",
        "doctor",
        "recover",
        "rollback",
        "state-upgrade",
        "state-encrypt",
        "state-rotate",
    }:
        raise SafetyBlocked("Unsupported workflow dispatch")
    emergency = _flag(env, "SMARTGATE_EMERGENCY_ONLY")
    if emergency and (
        command not in {"preview", "sync"}
        or _flag(env, "SMARTGATE_BOOTSTRAP")
        or _flag(env, "SMARTGATE_REQUIRE_NO_CHANGE")
        or env.get("SMARTGATE_ROLLBACK_RUN_ID")
    ):
        raise SafetyBlocked("Emergency workflow requires isolated sync or preview")
    if emergency and event == "schedule" and not _flag(env, "SMARTGATE_FAILOVER_ENABLED"):
        raise SafetyBlocked("Automatic emergency failover is not enabled")
    requested = _flag(env, "SMARTGATE_REQUEST_APPLY")
    automatic = _flag(env, "SMARTGATE_APPLY_ENABLED")
    if emergency and requested and not automatic:
        raise SafetyBlocked("Emergency apply requires the automatic apply safety switch")
    bootstrap = _flag(env, "SMARTGATE_BOOTSTRAP")
    no_change = _flag(env, "SMARTGATE_REQUIRE_NO_CHANGE")
    if no_change and (
        event != "workflow_dispatch" or command != "sync" or not requested or bootstrap
    ):
        raise SafetyBlocked("No-change acceptance requires manual sync apply without bootstrap")
    if event == "schedule" and (command != "sync" or bootstrap or requested):
        raise SafetyBlocked("Scheduled runs only support ordinary sync")
    apply = automatic if event == "schedule" else requested
    if apply and command not in {
        "sync",
        "recover",
        "rollback",
        "state-upgrade",
        "state-encrypt",
        "state-rotate",
    }:
        raise SafetyBlocked("Read-only commands cannot apply")
    if apply:
        branch = env.get("SMARTGATE_DEFAULT_BRANCH", "")
        if not branch or env.get("GITHUB_REF") != "refs/heads/" + branch:
            raise SafetyBlocked("Workflow apply requires the default branch")
    if bootstrap and command not in {"preview", "sync"}:
        raise SafetyBlocked("Bootstrap is only valid for preview or sync")
    if command == "state-rotate" and automatic:
        raise SafetyBlocked("Pause automatic apply before key rotation")
    if command in {"state-encrypt", "state-rotate"} and env.get("SMARTGATE_ROLLBACK_RUN_ID", ""):
        raise SafetyBlocked("State encryption cannot accept a rollback run ID")
    if event == "schedule" and not apply and not emergency:
        command = "doctor"
    args = [
        command,
        "--config",
        "config/smartgate.actions.toml",
        "--state-provider",
        "git_branch",
        "--report-dir",
        ".smartgate/reports",
        "--private-report",
    ]
    if emergency:
        args.append("--emergency-only")
    if apply:
        args.append("--apply")
    if bootstrap:
        args.append("--bootstrap")
    if no_change:
        args.append("--require-no-change")
    if command == "rollback":
        run_id = env.get("SMARTGATE_ROLLBACK_RUN_ID", "")
        if not run_id:
            raise SafetyBlocked("Rollback requires the exact journal run ID")
        args.extend(["--run-id", run_id])
    return args


def main() -> int:
    try:
        args = controller_arguments(os.environ)
    except SafetyBlocked as error:
        print(str(error))
        return 4
    # GitHub materializes missing optional Secrets/Variables as empty strings.
    # Two configured values still reach the resolver and must agree.
    for name in ("NEXTDNS_API_KEY", "NEXTDNS_PROFILE_ID"):
        if os.environ.get(name) == "":
            os.environ.pop(name)
    report_path = Path(".smartgate/reports/report.json")
    try:
        for name in ("report.json", "report.md", "report.enc.json"):
            report_path.with_name(name).unlink(missing_ok=True)
    except OSError:
        print("Unable to clear previous workflow report")
        return 5
    code = controller_main(args)
    if code != 3:
        return code
    try:
        if report_path.stat().st_size > 2 * 1024 * 1024:
            return code
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            isinstance(report, dict)
            and report.get("status") == "degraded"
            and report.get("exit_code") == 3
            and type(report.get("workflow_exit_code")) is int
            and report["workflow_exit_code"] == 0
        ):
            print("::warning::DNS verification passed; optional relay routes unavailable.")
            return 0
    except (OSError, ValueError, UnicodeError):
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
