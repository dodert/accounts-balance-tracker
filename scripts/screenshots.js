// Takes the README screenshots (docs/*.png) from the demo mode, with made-up data.
// Start the app on http://localhost:8012 (e.g. uvicorn app.main:create_app --factory --port 8012),
// then: node scripts/screenshots.js docs   (needs Playwright with Chromium)
const { chromium } = require('playwright');
const out = process.argv[2];
const hideBanner = '#demo-banner { display: none !important; }';
async function page(b, opts, lang = 'en') {
  const ctx = await b.newContext(opts);
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('pageerror', e.message));
  await p.addInitScript((l) => { localStorage.setItem('abt-lang', l); }, lang);
  await p.request.post('http://localhost:8012/api/demo?lang=' + lang, { headers: { Origin: 'http://localhost:8012' } });
  await p.goto('http://localhost:8012/');
  await p.addStyleTag({ content: hideBanner });
  await p.waitForTimeout(1500);
  return p;
}
(async () => {
  const b = await chromium.launch();
  // 1. Overview, desktop light: totals, total chart with small charts, accounts
  let p = await page(b, { viewport: { width: 1280, height: 900 }, colorScheme: 'light' });
  await p.selectOption('#chart-range', '12'); await p.waitForTimeout(800);
  await p.screenshot({ path: `${out}/screenshot.png`, fullPage: true });
  // 2. All in EUR, by institution, dark
  await p.close();
  p = await page(b, { viewport: { width: 1280, height: 900 }, colorScheme: 'dark' });
  await p.selectOption('#chart-currency', '='); await p.selectOption('#chart-group', 'institution'); await p.waitForTimeout(1000);
  // hover a milestone line on the big chart
  const line = p.locator('#chart .milestone-line').first();
  if (await line.count()) {
    const box = await line.boundingBox();
    await p.mouse.move(box.x + box.width / 2 + 1, box.y + box.height * 0.6); await p.waitForTimeout(200);
  }
  await p.locator('#chart-panel').screenshot({ path: `${out}/screenshot-dark.png` });
  await p.close();
  // 3. Phone
  p = await page(b, { ...require('playwright').devices['iPhone 13'], colorScheme: 'light' });
  await p.locator('#chart-panel').evaluate((e) => e.scrollIntoView()); await p.waitForTimeout(300);
  await p.screenshot({ path: `${out}/screenshot-mobile.png` });
  await p.close();
  // 4. Update balances dialog, grouped by institution
  p = await page(b, { viewport: { width: 1280, height: 900 }, colorScheme: 'light' });
  await p.click('#update-btn'); await p.waitForTimeout(400);
  await p.locator('#update-dlg').screenshot({ path: `${out}/screenshot-update.png` });
  await p.close();
  await b.close();
})();
