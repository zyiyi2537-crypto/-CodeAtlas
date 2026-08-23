const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    proxy: { server: 'http://127.0.0.1:7897' },
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 2,
  });
  await page.goto('file:///D:/agent%20project/CodeAtlas/design/redesign-mockup.html', {
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
