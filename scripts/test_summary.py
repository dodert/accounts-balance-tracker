"""The CI's test report: reads pytest's JUnit XML and writes a Markdown summary for the workflow run page.

    python scripts/test_summary.py test-results.xml >> "$GITHUB_STEP_SUMMARY"

Each test shows its description (the first line of its docstring, put in the report by tests/conftest.py) and,
from git history, when it was added and last changed: date, commit and PR. That needs the whole history
(actions/checkout with fetch-depth: 0); without it, those columns are left empty. Commits listed in
.git-blame-ignore-revs (e.g. adding the docstrings to every test) don't count as a change.
"""
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from functools import cache

ICON = {"passed": "✅", "failed": "❌", "skipped": "⏭️"}
REPO_URL = (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
            if os.environ.get("GITHUB_REPOSITORY") else "")
# A pull request's own run: its commits are not merged yet (refs/pull/<n>/merge)
THIS_PR = (re.findall(r"refs/pull/(\d+)/", os.environ.get("GITHUB_REF", "")) or [""])[0]


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


@cache
def pull_requests() -> dict[str, str]:
    """Commit -> number of the PR that brought it into the branch: the commits of each 'Merge pull request #N'."""
    found = {}
    for line in git("log", "--merges", "--first-parent", "--format=%H %P%x09%s").splitlines():
        hashes, subject = line.split("\t", 1)
        merge, *parents = hashes.split()
        number = re.match(r"Merge pull request #(\d+)", subject)
        number = number.group(1) if number else THIS_PR if merge == git("rev-parse", "HEAD").strip() else ""
        if number and len(parents) == 2:
            for commit in git("rev-list", f"{parents[0]}..{parents[1]}").split():
                found.setdefault(commit, number)
    return found


def pr_of(commit: str, subject: str) -> str:
    number = pull_requests().get(commit)
    if not number:  # a squashed PR: "Title (#N)"
        squashed = re.search(r"\(#(\d+)\)$", subject)
        number = squashed.group(1) if squashed else ""
    return number


@cache
def ignored() -> set[str]:
    try:
        with open(".git-blame-ignore-revs", encoding="utf-8") as revs:
            return {line.split("#")[0].strip() for line in revs if line.split("#")[0].strip()}
    except OSError:
        return set()


@cache
def history(file: str, function: str) -> tuple[tuple, tuple] | None:
    """(added, last changed) of a test function, each (date, commit, PR)."""
    log = git("log", "-L", rf":^\(async \)\?def {function}\b:{file}", "-s", "--format=%H%x09%cs%x09%s")
    changes = [line.split("\t", 2) for line in log.splitlines() if line.count("\t") == 2]
    if not changes:
        return None
    first, first_day, first_subject = changes[-1]
    changes = [c for c in changes if c[0] not in ignored()] or [changes[-1]]
    last, last_day, last_subject = changes[0]
    return ((first_day, first, pr_of(first, first_subject)), (last_day, last, pr_of(last, last_subject)))


def link(change: tuple) -> str:
    day, commit, pr = change
    if not REPO_URL:
        return f"{day} · {commit[:7]}" + (f" · #{pr}" if pr else "")
    text = f"{day} · [{commit[:7]}]({REPO_URL}/commit/{commit})"
    return text + (f" · [#{pr}]({REPO_URL}/pull/{pr})" if pr else "")


def outcome(case: ET.Element) -> tuple[str, str]:
    for tag, name in (("failure", "failed"), ("error", "failed"), ("skipped", "skipped")):
        found = case.find(tag)
        if found is not None:
            return name, (found.get("message") or "") + "\n" + (found.text or "")
    return "passed", ""


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def summary(path: str) -> str:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    files: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0, "time": 0.0, "tests": []})
    failures = []
    for case in cases:
        module = case.get("classname", "")
        file = module.replace(".", "/") + ".py" if module else "?"
        name = case.get("name", "")
        status, detail = outcome(case)
        time = float(case.get("time") or 0)
        description = next((p.get("value", "") for p in case.iter("property") if p.get("name") == "description"), "")
        group = files[file]
        group[status] += 1
        group["time"] += time
        group["tests"].append((status, name, description, history(file, name.split("[")[0]), time))
        if status == "failed":
            failures.append((file, name, description, detail.strip()))
    total = {s: sum(g[s] for g in files.values()) for s in ICON}
    seconds = sum(g["time"] for g in files.values())
    head = "❌ Some tests failed" if total["failed"] else "✅ All tests passed"
    out = [f"## {head}", "",
           f"**{len(cases)}** tests: {total['passed']} passed, {total['failed']} failed, "
           f"{total['skipped']} skipped, in {seconds:.1f} s", ""]
    if failures:
        out += ["### Failed", ""]
        for file, name, description, detail in failures:
            out += [f"<details open><summary>❌ <code>{file}::{name}</code> {description}</summary>", "", "```",
                    detail[-3000:], "```", "", "</details>", ""]
    out += ["### By file", "", "| | File | Passed | Failed | Skipped | Time |", "|---|---|---:|---:|---:|---:|"]
    for file in sorted(files):
        g = files[file]
        icon = ICON["failed"] if g["failed"] else ICON["passed"]
        out.append(f"| {icon} | `{file}` | {g['passed']} | {g['failed']} | {g['skipped']} | {g['time']:.2f} s |")
    out += ["", "### Every test", ""]
    for file in sorted(files):
        g = files[file]
        out += [f"<details><summary>{ICON['failed'] if g['failed'] else ICON['passed']} <code>{file}</code> "
                f"({len(g['tests'])})</summary>", "",
                "| | Test | What it checks | Added | Last changed | Time |", "|---|---|---|---|---|---:|"]
        for status, name, description, changes, time in g["tests"]:
            added, changed = (link(changes[0]), link(changes[1]) if changes[1] != changes[0] else "—") \
                if changes else ("", "")
            out.append(f"| {ICON[status]} | `{cell(name)}` | {cell(description)} | {added} | {changed} | {time:.2f} s |")
        out += ["", "</details>", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(summary(sys.argv[1]))
