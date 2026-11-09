"""End-to-end tests: the real page in a browser (Chromium, with Playwright) against the app on a local port.

They need `pip install playwright` and `python -m playwright install chromium`. Without them they are
skipped, unless E2E=1 (as in the CI), which turns that into a failure. PLAYWRIGHT_CHROMIUM can point to a
Chromium binary to use instead of Playwright's own.

After each test, passed or failed, the whole page is saved as test-screenshots/<test>.png (the CI keeps the
folder as an artifact of the run).
"""
import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.demo import SAMPLES
from app.main import create_app

SCREENSHOTS = Path(__file__).resolve().parent.parent / "test-screenshots"

if os.environ.get("E2E"):
    from playwright import sync_api as playwright_api
else:
    playwright_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """The app (single user, no login) served by uvicorn on a free local port."""
    import uvicorn
    port = free_port()
    config = uvicorn.Config(create_app(Settings(data_dir=tmp_path, default_currency="EUR")), host="127.0.0.1",
                            port=port, log_level="warning")
    app_server = uvicorn.Server(config)
    thread = threading.Thread(target=app_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not app_server.started:
        assert time.time() < deadline, "the app did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    app_server.should_exit = True
    thread.join(5)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as pw:
        try:
            chromium = pw.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM") or None)
        except Exception as error:  # noqa: BLE001
            if os.environ.get("E2E"):
                raise
            pytest.skip(f"Chromium can't start: {error}")
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser, server, request):
    """A fresh browser page on the app, in English, failing the test on any JavaScript error. Its screenshot is
    saved when the test ends, also when it failed."""
    context = browser.new_context(base_url=server, viewport={"width": 1280, "height": 900})
    context.add_init_script("localStorage.setItem('abt-lang', 'en')")
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda error: errors.append(str(error)))
    tab.on("dialog", lambda dialog: dialog.accept())  # confirm() questions: yes
    yield tab
    SCREENSHOTS.mkdir(exist_ok=True)
    tab.screenshot(path=SCREENSHOTS / f"{re.sub(r'[^\w.-]', '_', request.node.name)}.png", full_page=True)
    context.close()
    assert errors == [], errors


def open_app(page):
    page.goto("/")
    page.wait_for_selector("#version:not(:empty)")


def test_the_page_loads_with_no_data(page):
    """The page loads its modules, shows the version, the time zone and the 'no accounts yet' message."""
    open_app(page)
    assert page.locator("#version").text_content().startswith("v")
    assert page.locator("#empty").is_visible()
    assert page.locator("#empty .hint").text_content().startswith("No accounts yet")
    zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    assert page.locator("#tz-badge").text_content() == f"🕒 {zone.replace('_', ' ')}"  # the browser's time zone


def test_add_an_account_and_a_balance(page):
    """An account made in its dialog, then a balance added to it, show in the list and the totals."""
    open_app(page)
    page.click("#add-account-btn")
    page.fill("#account-form input[name=name]", "Savings")
    page.fill("#account-form input[name=institution]", "Bank")
    page.click("#account-form button[type=submit]")
    row = page.locator("#accounts-table tbody tr", has_text="Savings")
    row.wait_for()
    row.click()
    page.fill("#nb-amount", "1234,50")
    page.click("#nb-add")
    page.locator("#balances-table tbody td.num", has_text="1,234.50").first.wait_for()
    page.keyboard.press("Escape")
    page.locator("#totals .tile-value", has_text="1,234.50").wait_for()
    assert "1,234.50" in row.text_content()


def test_update_balances_of_every_account_at_once(page):
    """The 'Update balances' dialog saves a balance for each account on one date."""
    open_app(page)
    for name in ("One", "Two"):
        page.click("#add-account-btn")
        page.fill("#account-form input[name=name]", name)
        page.click("#account-form button[type=submit]")
        page.locator("#accounts-table tbody tr", has_text=name).wait_for()
    page.click("#update-btn")
    inputs = page.locator("#update-rows input")
    inputs.nth(0).fill("100")
    inputs.nth(1).fill("50,25")
    page.click("#update-form button[type=submit]")
    page.locator("#totals .tile-value", has_text="150.25").wait_for()


def test_import_a_csv(page):
    """A CSV imported from its dialog says what it added and the accounts appear."""
    open_app(page)
    page.click("#data-menu summary")
    page.click("#csv-import-btn")
    csv = b"account,institution,date,amount\nCurrent,Bank,2026-01-31,100\nCurrent,Bank,2026-02-28,120\n"
    page.set_input_files("#csv-form input[name=file]", files=[{"name": "b.csv", "mimeType": "text/csv", "buffer": csv}])
    page.click("#csv-form button[type=submit]")
    result = page.locator("#csv-result")
    result.wait_for()
    assert "1" in result.text_content() and "2" in result.text_content()
    page.keyboard.press("Escape")
    page.locator("#accounts-table tbody tr", has_text="Current").wait_for()


def test_demo_mode_with_charts(page):
    """The demo shows its sample accounts and charts (amounts, percent, maximized), and leaving it ends it."""
    open_app(page)
    page.click("#demo-try")
    page.wait_for_selector("#demo-banner:not([hidden])")
    page.wait_for_selector("#chart svg")
    assert page.locator("#accounts-table tbody tr").count() == sum(1 for s in SAMPLES if s.closed_month is None)
    assert page.locator("#multiples .mini").count() > 0
    page.click("#chart-panel .views button[data-mode=percent]")
    page.wait_for_selector("#chart svg")
    page.click("#chart-panel .views button[data-mode=amounts]")
    page.locator("#multiples .mini-max").first.click()
    page.wait_for_selector("#chart-dlg[open] #chart-dlg-chart svg")
    page.keyboard.press("Escape")
    page.click("#demo-exit")
    page.wait_for_selector("#demo-banner[hidden]", state="attached")
    page.locator("#empty").wait_for()


def test_change_the_language(page):
    """Choosing Spanish in the settings translates the page."""
    open_app(page)
    page.click("#settings-btn")
    page.locator("#lang-select option[value=es]").wait_for(state="attached")
    page.select_option("#lang-select", "es")
    page.click("#settings-form button[type=submit]")
    page.locator("#update-btn", has_text="Actualizar").wait_for()


def test_the_page_sends_its_local_date(page):
    """Each API call carries the browser's own date, which the server uses as "today"."""
    with page.expect_request(lambda r: "/api/" in r.url) as first:
        open_app(page)
    local = page.evaluate("(() => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1)"
                          ".padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); })()")
    assert first.value.headers.get("x-local-date") == local


def test_the_dates_follow_the_time_zone_chosen_in_the_settings(page):
    """With another time zone chosen in the settings, the page sends the date of that zone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    open_app(page)
    page.click("#settings-btn")
    page.locator("#tz-select option[value='Pacific/Kiritimati']").wait_for(state="attached")
    page.select_option("#tz-select", "Pacific/Kiritimati")  # UTC+14
    with page.expect_request(lambda r: "/api/" in r.url) as sent:
        page.click("#settings-form button[type=submit]")
    assert sent.value.headers.get("x-local-date") == datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()
    page.locator("#tz-badge", has_text="Pacific/Kiritimati").wait_for()  # shown next to the title
