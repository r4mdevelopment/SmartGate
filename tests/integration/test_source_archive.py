import subprocess
import sys
import tarfile
from pathlib import Path


def test_source_archive_contains_working_public_documentation(tmp_path):
    root = Path(__file__).resolve().parents[2]
    build = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert build.returncode == 0, build.stdout + build.stderr
    with tarfile.open(next(tmp_path.glob("*.tar.gz"))) as archive:
        archive.extractall(tmp_path / "source", filter="data")
    source = next((tmp_path / "source").iterdir())
    assert (source / "docs/README.md").is_file()
    check = subprocess.run(
        [sys.executable, "-X", "utf8", str(source / "tools/check_docs.py"), "--root", str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert check.returncode == 0, check.stdout + check.stderr
    for private in (
        "AGENTS.md",
        "REPO_RULES.md",
        ".smartgate",
        "docs/progress.md",
        "docs/superpowers",
        "config/smartgate.toml",
    ):
        assert not (source / private).exists()
