"""Prepare reviewed changelog notes; this tool never creates tags or releases."""

import argparse
import re
import tomllib
from pathlib import Path

VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
HEADER = re.compile(r"^## \[([^]]+)\](?: - [0-9]{4}-[0-9]{2}-[0-9]{2})?\s*$", re.MULTILINE)


def notes(root: Path, version: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError("Use a stable version in MAJOR.MINOR.PATCH format")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != version:
        raise ValueError("Release version must match pyproject.toml")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headers = list(HEADER.finditer(changelog))
    matching = [index for index, header in enumerate(headers) if header[1] == version]
    if len(matching) != 1:
        raise ValueError("Changelog must contain exactly one entry for this version")
    index = matching[0]
    end = headers[index + 1].start() if index + 1 < len(headers) else len(changelog)
    body = changelog[headers[index].end() : end].strip()
    if not body:
        raise ValueError("Release notes cannot be empty")
    return "# SmartGate v" + version + "\n\n" + body + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        text = notes(args.root, args.version)
        args.output.write_text(text, encoding="utf-8")
    except (ValueError, KeyError, OSError) as error:
        print("Release preparation failed: " + str(error))
        return 1
    print("Release notes prepared for v" + args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
