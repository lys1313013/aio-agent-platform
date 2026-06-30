import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  try {
    console.log('Opening app...');
    await page.goto('http://localhost:5174/', { waitUntil: 'networkidle', timeout: 10000 });

    console.log('Checking page...');
    const title = await page.title();
    console.log('Page title:', title);

    // Take screenshot of initial page
    await page.screenshot({ path: '/tmp/app-initial.png', fullPage: true });
    console.log('Screenshot saved: /tmp/app-initial.png');

  } catch (error) {
    console.error('Error:', error.message);
  } finally {
    await browser.close();
  }
})();
