import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 }, deviceScaleFactor: 1.5 });

await page.goto('http://127.0.0.1:3002/login', { waitUntil: 'networkidle' });
await page.fill('input[type="password"]', process.env.DASHBOARD_PASSWORD || '');
await page.click('button[type="submit"]');
await page.waitForURL('**/', { timeout: 15000 }).catch(() => {});
await page.goto('http://127.0.0.1:3002/', { waitUntil: 'networkidle' });
await page.screenshot({ path: 'docs/screenshots/dashboard-full.png', fullPage: true });

await browser.close();
