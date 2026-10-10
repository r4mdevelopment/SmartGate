import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_docs.py"


def run_check(root):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_checker_rejects_missing_files_and_sections(tmp_path):
    (tmp_path / "README.md").write_text(
        "[Missing](docs/missing.md) [Section](docs/ru/page.md#absent)", encoding="utf-8"
    )
    target = tmp_path / "docs/ru/page.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Existing heading\n", encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "missing.md" in result.stdout and "absent" in result.stdout


def test_checker_accepts_bilingual_links_assets_and_duplicate_headings(tmp_path):
    target = tmp_path / "docs/ru/page.md"
    target.parent.mkdir(parents=True)
    target.write_text('# Раздел\n## Same\n## Same\n<a name="explicit"></a>\n', encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[RU](docs/ru/page.md#%D1%80%D0%B0%D0%B7%D0%B4%D0%B5%D0%BB) "
        "[Duplicate](docs/ru/page.md#same-1) [Anchor](docs/ru/page.md#explicit)\n"
        '<img src="docs/banner.svg"> [External](https://example.com/#ignored)\n'
        "```md\n[Example](does-not-exist.md)\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/banner.svg").write_text("<svg/>", encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_rejects_paths_outside_repository(tmp_path):
    (tmp_path / "README.md").write_text("[Outside](../other.md)", encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "outside repository" in result.stdout
