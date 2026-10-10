import json
from pathlib import Path

import pytest

from smartgate.cli import main

CONFIG = "config/smartgate.example.toml"


def test_validate_without_secrets(capsys):
    assert main(["validate", "--config", CONFIG]) == 0
    assert "valid" in capsys.readouterr().out.lower()


def test_invalid_config_safe_exit2(tmp_path, capsys):
    path = tmp_path / "broken.toml"
    path.write_text("not-toml-token", encoding="utf-8")
    assert main(["validate", "--config", str(path)]) == 2
    assert "not-toml-token" not in capsys.readouterr().out


def test_offline_preview_no_secrets_reports(tmp_path, capsys):
    report = tmp_path / "report"
    assert (
        main(
            [
                "preview",
                "--config",
                CONFIG,
                "--offline",
                "--fixtures",
                "tests/fixtures",
                "--report-dir",
                str(report),
            ]
        )
        == 0
    )
    data = json.loads((report / "report.json").read_text(encoding="utf-8"))
    assert data["offline"] is True and data["status"] == "preview"
    assert data["plan"]["operations"]
    assert not Path(".smartgate/state.json").exists()


def test_live_requires_env_but_no_secret_output(monkeypatch, capsys):
    monkeypatch.delenv("NEXTDNS_PROFILE_ID", raising=False)
    assert main(["preview", "--config", CONFIG]) == 2
    assert "NEXTDNS_PROFILE_ID" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["sync", "recover", "rollback"])
def test_offline_apply_forbidden(mode, capsys):
    assert (
        main([mode, "--config", CONFIG, "--offline", "--fixtures", "tests/fixtures", "--apply"])
        == 4
    )


def test_missing_offline_fixture_safe_failure(tmp_path, capsys):
    assert main(["preview", "--config", CONFIG, "--offline", "--fixtures", str(tmp_path)]) == 2


def test_parser_errors_return2(capsys):
    assert main(["unknown-command"]) == 2


def test_disabled_apply_without_credentials_blocks(capsys):
    assert main(["sync", "--config", CONFIG, "--apply"]) == 4


@pytest.mark.parametrize("mode", ["preview", "sync", "doctor"])
def test_live_cli_reads_only_with_injected_ports(mode, monkeypatch, tmp_path):
    import smartgate.cli as cli
    from tests.integration.test_app import setup

    config, deps = setup()
    # Live composition is replaced by controlled ports; sockets remain prohibited.
    monkeypatch.setattr(cli, "live_dependencies", lambda *a, **kw: deps)
    # An empty profile has no DNS answers to verify: doctor must not report success.
    expected = 3 if mode == "doctor" else 0
    assert cli.main([mode, "--config", CONFIG, "--report-dir", str(tmp_path)]) == expected
    assert deps.dns.writes == deps.store.saves == 0


def test_cli_applies_with_enabled_config_and_reports_safely(monkeypatch, tmp_path):
    import smartgate.cli as cli
    from tests.integration.test_app import setup

    config, deps = setup()
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(cli, "live_dependencies", lambda *a, **kw: deps)
    monkeypatch.setenv("NEXTDNS_PROFILE_ID", "abcdef")
    monkeypatch.setenv("NEXTDNS_API_KEY", "private-key-test")
    assert (
        cli.main(
            ["sync", "--config", CONFIG, "--apply", "--bootstrap", "--report-dir", str(tmp_path)]
        )
        == 0
    )
    data = (tmp_path / "report.json").read_text(encoding="utf-8")
    assert "abcdef" not in data and "private-key-test" not in data
    assert json.loads(data)["journal_run_id"] == deps.store.state.journal.run_id


def test_interrupt_writes_safe_partial_report(monkeypatch, tmp_path):
    import smartgate.cli as cli
    from tests.integration.test_app import setup

    config, deps = setup()
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(cli, "live_dependencies", lambda *a, **kw: deps)
    monkeypatch.setattr(cli, "run", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.main(["sync", "--config", CONFIG, "--apply", "--report-dir", str(tmp_path)]) == 6
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["status"] == "partial"


def test_cli_no_change_guard_reaches_app_and_blocks_changed_plan(monkeypatch, tmp_path):
    from dataclasses import replace

    import smartgate.cli as cli
    from tests.integration.test_app import Relay
    from tests.integration.test_no_change_guard import initialized

    config, deps = initialized()

    class ChangedRelay(Relay):
        def fetch(self, provider, now):
            snapshot = super().fetch(provider, now)
            return replace(snapshot, candidates=(replace(snapshot.candidates[0], ip="1.1.1.1"),))

    deps = replace(deps, relays={p.id: ChangedRelay() for p in config.providers})
    writes, saves = deps.dns.writes, deps.store.saves
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(cli, "live_dependencies", lambda *a, **kw: deps)
    assert (
        cli.main(
            [
                "sync",
                "--config",
                CONFIG,
                "--apply",
                "--require-no-change",
                "--report-dir",
                str(tmp_path),
            ]
        )
        == 4
    )
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert "no-change" in data["errors"][0].lower()
    assert data["plan"]["operations"]
    assert (deps.dns.writes, deps.store.saves) == (writes, saves)


@pytest.mark.parametrize(
    "mode,args",
    [
        ("preview", []),
        ("sync", []),
        ("recover", ["--apply"]),
        ("rollback", ["--apply"]),
        ("state-upgrade", ["--apply"]),
        ("sync", ["--apply", "--bootstrap"]),
        ("sync", ["--apply", "--offline"]),
    ],
)
def test_cli_invalid_no_change_guard_stops_before_live_dependencies(mode, args, monkeypatch):
    import smartgate.cli as cli
    from tests.integration.test_app import setup

    config, _ = setup()
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(
        cli,
        "live_dependencies",
        lambda *a, **kw: pytest.fail("invalid guard must not compose live dependencies"),
    )
    assert cli.main([mode, "--config", CONFIG, "--require-no-change", *args]) == 4
