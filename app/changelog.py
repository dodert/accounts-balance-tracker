"""The changelog shown in the app (#205): CHANGELOG.md, kept by release-please (#196) and copied into the image, read
into releases → sections → items. Nothing is fetched from outside: the file is next to the app."""
import re
from functools import cache
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

# "## [0.49.11](https://.../compare/v0.49.10...v0.49.11) (2026-11-22)" or "## 0.1.0 (2026-11-22)"
RELEASE = re.compile(r"^##\s+\[?(?P<version>\d+\.\d+\.\d+)\]?(?:\([^)]*\))?\s*(?:\((?P<date>\d{4}-\d{2}-\d{2})\))?")
SECTION = re.compile(r"^###\s+(?P<title>.+?)\s*$")
# "* **deps:** bump ... ([280470e](https://.../commit/...)), closes [#176](https://.../issues/176)"
ITEM = re.compile(r"^[*-]\s+(?:\*\*(?P<scope>[^*]+?):\*\*\s+)?(?P<text>.+?)\s*$")
COMMIT_LINK = re.compile(r"\s*\(\[[0-9a-f]{7,40}\]\([^)]*\)\)")
ISSUE = re.compile(r"\[#(\d+)\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean(text: str) -> tuple[str, list[int]]:
    """An item's text without its commit links and "closes #N" tail, and the issues it names."""
    issues = [int(n) for n in ISSUE.findall(text)]
    text = COMMIT_LINK.sub("", text)
    text = re.sub(r",?\s*(?:closes|fixes|resolves)(?:[,\s]+\[#\d+\]\([^)]*\))+\s*$", "", text,
                  flags=re.IGNORECASE)
    text = LINK.sub(r"\1", text).replace("**", "").replace("`", "")
    return text.strip(), sorted(set(issues))


def parse(markdown: str) -> list[dict]:
    """Releases newest first, as in the file. release-please repeats an entry when a PR's commit and its merge
    both describe it: an item already in the same section is kept once, with the issues of both."""
    releases: list[dict] = []
    section = None
    for line in markdown.splitlines():
        if m := RELEASE.match(line):
            releases.append({"version": m["version"], "date": m["date"], "sections": []})
            section = None
        elif not releases:
            continue
        elif m := SECTION.match(line):
            section = {"title": m["title"], "items": []}
            releases[-1]["sections"].append(section)
        elif section is not None and (m := ITEM.match(line)):
            text, issues = clean(m["text"])
            if not text:
                continue
            scope = m["scope"]
            same = next((i for i in section["items"] if i["text"] == text and i["scope"] == scope), None)
            if same:
                same["issues"] = sorted(set(same["issues"]) | set(issues))
            else:
                section["items"].append({"scope": scope, "text": text, "issues": issues})
    return releases


@cache
def releases(path: Path = CHANGELOG) -> tuple[dict, ...]:
    """The changelog's releases, read once (the file doesn't change while the app runs); none without the file."""
    try:
        return tuple(parse(path.read_text(encoding="utf-8")))
    except OSError:
        return ()
