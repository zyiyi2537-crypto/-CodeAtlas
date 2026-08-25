const { chromium } = require('playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const mockupUrl = pathToFileURL(path.join(__dirname, 'redesign-mockup.html')).href;
  await page.goto(mockupUrl, {
    waitUntil: 'networkidle',
    timeout: 45000,
  });
  await page.waitForTimeout(1000);

  const audit = await page.evaluate(() => {
    const issues = [];
    const de = document.documentElement;
    if (de.scrollWidth > de.clientWidth) {
      issues.push(`horizontal overflow: scrollWidth ${de.scrollWidth} > ${de.clientWidth}`);
    }
    // fonts
    const serifLoaded = document.fonts.check('700 30px "Noto Serif SC"');
    const monoLoaded = document.fonts.check('400 12px "IBM Plex Mono"');
    // overlap check: actions vs title inside each result head
    document.querySelectorAll('.result-head').forEach((head, i) => {
      const title = head.querySelector('.result-title').getBoundingClientRect();
      const actions = head.querySelector('.result-actions').getBoundingClientRect();
      if (title.right > actions.left + 1) issues.push(`result ${i}: title overlaps actions`);
    });
    // h3 ellipsis present (no clipping beyond container)
    document.querySelectorAll('.result-title h3').forEach((h3) => {
      if (h3.scrollWidth > h3.clientWidth + 2 && getComputedStyle(h3).textOverflow !== 'ellipsis') {
        issues.push(`h3 clipped without ellipsis: ${h3.textContent.slice(0, 40)}`);
      }
    });
    // snippet code fits horizontally (has its own scroll, acceptable; flag only body overflow)
    // rail sticky within viewport
    const rail = document.querySelector('.rail');
    if (!rail) issues.push('rail missing');
    // icon render check
    const icons = document.querySelectorAll('svg.lucide').length;
    return { issues, serifLoaded, monoLoaded, icons };
  });

  console.log(JSON.stringify(audit, null, 2));
  await browser.close();
})();
