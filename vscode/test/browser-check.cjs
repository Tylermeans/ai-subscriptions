/* Real-browser rendering checks with synthetic data only; invoked by CI. */
'use strict';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {renderTemplate} = require('../status');

(async () => {
  const now = Date.now() / 1000;
  const base = {automatic: {enabled: true, threshold: 90}, default: false, capacity: 'Fresh usage needed for automatic selection'};
  const data = {version: 1, generated_at: now, profiles: [
    {...base, profile: 'codex/work', provider: 'codex', name: 'work', email: 'work@example.com', default: true,
     freshness: 'fresh', capacity: 'Both short-window and weekly data are required for automatic selection',
     sample: {observed: now, windows: [{label: 'codex/primary', minutes: 10080, used: 4, reset: now + 604800}]}},
    {...base, profile: 'claude/personal', provider: 'claude', name: 'personal', email: 'personal@example.com',
     freshness: 'unknown', sample: null},
    {...base, profile: 'claude/work', provider: 'claude', name: 'work', email: 'work@example.com', freshness: 'stale',
     sample: {observed: now - 1800, windows: [{label: 'five_hour', minutes: 300, used: 95, reset: now + 7200},
       {label: 'seven_day', minutes: 10080, used: 60, reset: now + 604800}]}}
  ]};
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-sub-browser-'));
  const file = path.join(temp, 'chart.html');
  const template = fs.readFileSync(path.join(__dirname, '../media/dashboard.html'), 'utf8');
  fs.writeFileSync(file, renderTemplate(template, data, 'synthetic-test-nonce'));
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 1000}});
    const errors = [], remoteRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (/^https?:/.test(request.url())) remoteRequests.push(request.url()); });
    await page.addInitScript(() => { window.messages = []; window.acquireVsCodeApi = () => ({postMessage(m) { window.messages.push(m); }}); });
    await page.goto(pathToFileURL(file).href);
    assert.equal(await page.locator('.card').count(), 3);
    assert.equal(await page.locator('progress').count(), 3);
    assert.equal(await page.locator('.unknown').count(), 3);
    assert.match(await page.locator('.card').first().textContent(), /4%used/);
    assert.equal(await page.locator('.card').nth(1).locator('progress').count(), 0);
    assert.equal(await page.locator('.card').nth(2).locator('.old').count(), 2);
    const output = process.env.AI_SUB_SCREENSHOTS || path.join(temp, 'screenshots');
    fs.mkdirSync(output, {recursive: true});
    await page.screenshot({path: path.join(output, 'usage-desktop.png'), fullPage: true});
    await page.getByRole('button', {name: 'Refresh display'}).click();
    assert.equal(await page.evaluate(() => window.messages.some(m => m.type === 'refresh')), true);
    await page.getByRole('button', {name: 'Fetch Codex usage'}).click();
    assert.equal(await page.locator('#fetch').isDisabled(), true);
    await page.evaluate(() => window.postMessage({type: 'fetchDone'}, '*'));
    await page.waitForFunction(() => !document.getElementById('fetch').disabled);
    data.profiles[0].sample.windows[0].used = 8;
    data.profiles[0].email = '<script>window.injected = true</script>';
    await page.evaluate(data => window.postMessage({type: 'snapshot', data}, '*'), data);
    await page.waitForFunction(() => document.querySelector('progress').value === 8);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(output, 'usage-mobile.png'), fullPage: true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    assert.deepEqual(remoteRequests, []);
    console.log('Browser checks passed: chart rendering, empty and stale data, refresh messages, injection escaping, narrow layout, no remote requests.');
  } finally { await browser.close(); fs.rmSync(temp, {recursive: true, force: true}); }
})().catch(error => { console.error(error); process.exitCode = 1; });
