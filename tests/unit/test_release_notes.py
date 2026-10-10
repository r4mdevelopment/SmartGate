import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools/release_notes.py"


def prepare(root, version="0.1.0"):
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(SCRIPT),
            "--root",
            str(root),
            "--version",
            version,
            "--output",
            str(root / "notes.md"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def fixture(
    root,
    changelog=(
        "## [0.1.0] - 2026-10-10\n\nFirst release.\n\n## [0.0.1] - 2026-10-01\n\nOld notes.\n"
    ),
):
    (root / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


def test_release_contains_only_requested_version_notes(tmp_path):
    fixture(tmp_path)
    result = prepare(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "First release." in (tmp_path / "notes.md").read_text()
    assert "Old notes." not in (tmp_path / "notes.md").read_text()
    assert "v0.1.0" in result.stdout


@pytest.mark.parametrize(
    "version", ["0.2.0", "0.1.0;echo secret", "01.1.0", "v0.1.0", "../../secret"]
)
def test_version_mismatch_or_injection_cannot_prepare_release(tmp_path, version):
    fixture(tmp_path)
    result = prepare(tmp_path, version)
    assert result.returncode == 1
    assert not (tmp_path / "notes.md").exists()


@pytest.mark.parametrize(
    "changelog",
    [
        "# Changelog\n",
        "## [0.1.0] - 2026-10-10\n\n",
        "## [0.1.0] - 2026-10-10\nOne\n## [0.1.0] - 2026-10-11\nTwo\n",
    ],
)
def test_missing_empty_or_duplicate_notes_block_release(tmp_path, changelog):
    fixture(tmp_path, changelog)
    result = prepare(tmp_path)
    assert result.returncode == 1
    assert not (tmp_path / "notes.md").exists()
