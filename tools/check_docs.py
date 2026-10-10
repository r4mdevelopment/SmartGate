"""Check repository-local Markdown links and anchors without network requests."""

import argparse
import html
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINKS = re.compile(r"!?\[[^\]\n]*\]\(([^\s)]+)(?:\s+[^)]*)?\)")
HTML_LINKS = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']")
ANCHORS = re.compile(r"<a\s+[^>]*(?:id|name)=[\"']([^\"']+)[\"']", re.IGNORECASE)


def without_fences(text: str) -> str:
    lines = []
    fence = ""
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match[1][0]
            if not fence:
                fence = marker
            elif marker == fence:
                fence = ""
            continue
        if not fence:
            lines.append(line)
    return "\n".join(lines)


def slug(heading: str) -> str:
    heading = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", heading)
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = html.unescape(heading).lower()
    return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")


def anchors(text: str) -> set[str]:
    text = without_fences(text)
    result = set(ANCHORS.findall(text))
    seen: dict[str, int] = {}
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        key = slug(match[1])
        count = seen.get(key, 0)
        result.add(key if count == 0 else key + "-" + str(count))
        seen[key] = count + 1
    return result


def documents(root: Path) -> list[Path]:
    paths = list(root.glob("README*.md"))
    paths += [root / name for name in ("CHANGELOG.md", "docs/README.md") if (root / name).is_file()]
    for language in ("en", "ru"):
        paths += list((root / "docs" / language).rglob("*.md"))
    return sorted(set(paths))


def check(root: Path) -> list[str]:
    root = root.resolve()
    failures = []
    cache: dict[Path, set[str]] = {}
    for document in documents(root):
        text = without_fences(document.read_text(encoding="utf-8"))
        for raw in LINKS.findall(text) + HTML_LINKS.findall(text):
            raw = html.unescape(raw).strip("<>")
            url = urlsplit(raw)
            if url.scheme or url.netloc:
                continue
            target = (document.parent / unquote(url.path)).resolve() if url.path else document
            location = document.relative_to(root).as_posix() + ": " + raw
            if target != root and root not in target.parents:
                failures.append(location + " (outside repository)")
            elif not target.exists():
                failures.append(location + " (missing file)")
            elif url.fragment and target.suffix == ".md":
                if target not in cache:
                    cache[target] = anchors(target.read_text(encoding="utf-8"))
                if unquote(url.fragment) not in cache[target]:
                    failures.append(location + " (missing anchor)")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    root = parser.parse_args().root.resolve()
    failures = check(root)
    for failure in failures:
        print(failure)
    print(
        str(len(documents(root)))
        + " Markdown files checked; "
        + str(len(failures))
        + " broken links"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
