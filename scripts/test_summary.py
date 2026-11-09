"""The CI's test report: reads pytest's JUnit XML and writes a Markdown summary for the workflow run page.

    python scripts/test_summary.py test-results.xml >> "$GITHUB_STEP_SUMMARY"
"""
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ICON = {"passed": "✅", "failed": "❌", "skipped": "⏭️"}


def outcome(case: ET.Element) -> tuple[str, str]:
    for tag, name in (("failure", "failed"), ("error", "failed"), ("skipped", "skipped")):
        found = case.find(tag)
        if found is not None:
            return name, (found.get("message") or "") + "\n" + (found.text or "")
    return "passed", ""


def summary(path: str) -> str:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    files: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0, "time": 0.0, "tests": []})
    failures = []
    for case in cases:
        file = case.get("classname", "").rsplit(".", 1)[-1] + ".py" if case.get("classname") else "?"
        status, detail = outcome(case)
        time = float(case.get("time") or 0)
        group = files[file]
        group[status] += 1
        group["time"] += time
        group["tests"].append((status, case.get("name", ""), time))
        if status == "failed":
            failures.append((file, case.get("name", ""), detail.strip()))
    total = {s: sum(g[s] for g in files.values()) for s in ICON}
    seconds = sum(g["time"] for g in files.values())
    head = "❌ Some tests failed" if total["failed"] else "✅ All tests passed"
    out = [f"## {head}", "",
           f"**{len(cases)}** tests: {total['passed']} passed, {total['failed']} failed, "
           f"{total['skipped']} skipped, in {seconds:.1f} s", ""]
    if failures:
        out += ["### Failed", ""]
        for file, name, detail in failures:
            out += [f"<details open><summary>❌ <code>{file}::{name}</code></summary>", "", "```",
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
                f"({len(g['tests'])})</summary>", ""]
        out += [f"- {ICON[status]} `{name}` ({time:.2f} s)" for status, name, time in g["tests"]]
        out += ["", "</details>", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(summary(sys.argv[1]))
