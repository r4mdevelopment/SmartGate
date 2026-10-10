import base64

import pytest

import smartgate.cli as cli
from smartgate.security.envelope import EnvelopeBinding, EnvelopeCipher
from tests.integration.test_app import setup

KEY = bytes(range(32))


def context(monkeypatch):
    monkeypatch.setenv("SMARTGATE_STATE_ENCRYPTION_KEY", base64.b64encode(KEY).decode())
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")


def test_actions_report_contains_no_plaintext_routes(monkeypatch, tmp_path, capsys):
    from smartgate.private_report import decrypt_report

    config, deps = setup()
    context(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(cli, "load_config", lambda *a: config)
    monkeypatch.setattr(cli, "live_dependencies", lambda *a, **kw: deps)
    assert cli.main(["preview", "--config", "unused", "--report-dir", str(tmp_path)]) == 0
    public = (
        capsys.readouterr().out
        + (tmp_path / "report.json").read_text()
        + (tmp_path / "report.md").read_text()
    )
    assert "chatgpt.com" not in public and "8.8.8.8" not in public
    artifact = (tmp_path / "report.enc.json").read_bytes()
    full = decrypt_report(
        artifact, EnvelopeCipher(KEY), EnvelopeBinding("report", "owner/repo", "actions", "123:1")
    )
    assert b"chatgpt.com" in full
    assert {p.name for p in tmp_path.iterdir()} == {"report.json", "report.md", "report.enc.json"}


@pytest.mark.parametrize("private_source", ["actions", "flag", "git"])
def test_early_errors_are_private_without_key(monkeypatch, tmp_path, capsys, private_source):
    context(monkeypatch)
    monkeypatch.delenv("SMARTGATE_STATE_ENCRYPTION_KEY")
    if private_source == "actions":
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    extra = (
        ["--private-report"]
        if private_source == "flag"
        else ["--state-provider", "git_branch"]
        if private_source == "git"
        else []
    )
    from smartgate.errors import ConfigError

    monkeypatch.setattr(
        cli, "load_config", lambda *a: (_ for _ in ()).throw(ConfigError("private.example 8.8.8.8"))
    )
    (tmp_path / "report.enc.json").write_text("old artifact")
    assert cli.main(["preview", "--config", "unused", "--report-dir", str(tmp_path), *extra]) != 0
    output = (
        capsys.readouterr().out
        + (tmp_path / "report.json").read_text()
        + (tmp_path / "report.md").read_text()
    )
    assert "private.example" not in output and "8.8.8.8" not in output
    assert not (tmp_path / "report.enc.json").exists()


def test_decrypt_is_local_exclusive_and_never_prints_payload(monkeypatch, tmp_path, capsys):
    from smartgate.models import RunReport
    from smartgate.private_report import encrypt_report

    context(monkeypatch)
    artifact = tmp_path / "report.enc.json"
    output = tmp_path / "decrypted.json"
    report = RunReport("a" * 32, "blocked", (), (), (), ("private.example",))
    artifact.write_bytes(
        encrypt_report(
            report,
            (),
            EnvelopeCipher(KEY),
            EnvelopeBinding("report", "owner/repo", "actions", "123:1"),
        )
    )
    monkeypatch.setattr(
        cli, "live_dependencies", lambda *a, **kw: pytest.fail("network composition")
    )
    args = [
        "report-decrypt",
        "--input",
        str(artifact),
        "--output",
        str(output),
        "--repository",
        "owner/repo",
        "--run-id",
        "123",
        "--run-attempt",
        "1",
    ]
    assert cli.main(args) == 0
    assert "private.example" in output.read_text()
    assert "private.example" not in capsys.readouterr().out
    assert cli.main(args) != 0
    assert "private.example" in output.read_text()


def test_offline_actions_remains_key_free(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("SMARTGATE_STATE_ENCRYPTION_KEY", raising=False)
    assert (
        cli.main(
            [
                "preview",
                "--config",
                "config/smartgate.example.toml",
                "--offline",
                "--fixtures",
                "tests/fixtures",
                "--report-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "chatgpt.com" not in (tmp_path / "report.json").read_text()
    assert not (tmp_path / "report.enc.json").exists()


def test_private_parser_error_does_not_echo_arguments(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cli.main(["private.example"]) == 2
    assert "private.example" not in str(capsys.readouterr())
