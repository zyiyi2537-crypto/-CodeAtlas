const { chromium } = require('playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 2,
  });
  const mockupUrl = pathToFileURL(path.join(__dirname, 'redesign-mockup.html')).href;
  await page.goto(mockupUrl, {
    waitUntil: 'networkidle',
    timeout: 45000,
  });
  await page.waitForTimeout(1200);
  const iconsRendered = await page.evaluate(
    () => document.querySelectorAll('svg.lucide').length,
  );
  console.log('lucide icons rendered:', iconsRendered);
  await page.screenshot({ path: 'redesign-search.png', fullPage: true });
  await browser.close();
  console.log('SCREENSHOT_DONE');
})();
