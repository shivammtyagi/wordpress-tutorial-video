#!/usr/bin/env node
// Does the recording browser survive an idle minute? Some machines kill
// Playwright's bundled headless shell ~30s after launch (silently, even on
// about:blank); an installed channel (Google Chrome) does not. Run:
//   node scripts/browser_lifetime_check.mjs            # bundled Chromium
//   node scripts/browser_lifetime_check.mjs chrome     # channel
import { chromium } from 'playwright';
const channel = process.argv[2];
const secs = Number(process.argv[3] || 45);
const t0 = Date.now();
const b = await chromium.launch(channel ? { channel } : {});
let died = false;
b.on('disconnected', () => { died = true; });
const page = await b.newPage();
await page.goto('about:blank');
for (let t = 5; t <= secs && !died; t += 5) {
  await new Promise((r) => setTimeout(r, 5000));
  if (!(await page.evaluate(() => 1).catch(() => 0))) died = true;
}
const label = channel ? `channel "${channel}"` : 'bundled Chromium';
if (died) {
  console.log(`browser_lifetime_check: ${label} DIED after ${((Date.now() - t0) / 1000).toFixed(0)}s — set "browser_channel": "chrome" in config.json`);
  process.exit(1);
}
console.log(`browser_lifetime_check: ${label} survived ${secs}s`);
await b.close();
