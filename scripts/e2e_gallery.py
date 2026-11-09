"""One HTML page with every end-to-end test's screenshot, for the CI run's artifacts.

    python scripts/e2e_gallery.py test-results.xml test-screenshots e2e-screenshots.html

The images go inside the page (base64), so the single file shows everything. Each one is under its test's name,
description and result, read from pytest's JUnit report.
"""
import base64
import html
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

STYLE = """body{font:15px system-ui,sans-serif;margin:24px;background:#f4f5f7;color:#1d2330}
figure{background:#fff;border:1px solid #d9dde3;border-radius:8px;margin:0 0 24px;padding:16px}
figcaption{margin-bottom:12px}code{font-size:14px}img{max-width:100%;border:1px solid #d9dde3}
.failed{color:#b42318}.passed{color:#067647}"""


def gallery(report: str, folder: str) -> str:
    cases = {case.get("name"): case for case in ET.parse(report).getroot().iter("testcase")
             if case.get("classname", "").endswith("test_e2e")}
    parts = [f"<!doctype html><meta charset=utf-8><title>End-to-end screenshots</title><style>{STYLE}</style>",
             "<h1>End-to-end tests: screenshots</h1>"]
    for image in sorted(Path(folder).glob("*.png")):
        case = cases.get(image.stem)
        description = next((p.get("value", "") for p in case.iter("property") if p.get("name") == "description"),
                           "") if case is not None else ""
        failed = case is not None and (case.find("failure") is not None or case.find("error") is not None)
        status = "failed" if failed else "passed"
        data = base64.b64encode(image.read_bytes()).decode()
        parts.append(f'<figure><figcaption><span class="{status}">{"❌" if failed else "✅"} {status}</span> · '
                     f"<code>{html.escape(image.stem)}</code><br>{html.escape(description)}</figcaption>"
                     f'<img alt="{html.escape(image.stem)}" src="data:image/png;base64,{data}"></figure>')
    return "\n".join(parts)


if __name__ == "__main__":
    Path(sys.argv[3]).write_text(gallery(sys.argv[1], sys.argv[2]), encoding="utf-8")
