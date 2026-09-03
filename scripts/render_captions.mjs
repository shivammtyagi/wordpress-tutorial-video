// render_captions.mjs — render caption "pills" to transparent PNGs via Chromium.
// Each cue in the cues JSON becomes one tightly-cropped PNG (NN.png by index)
// that a compositor overlays at the cue's time window. Fonts are embedded as
// data URIs so rendering is fully self-contained.
//
//   node render_captions.mjs --cues <cues.json> --out-dir <dir> \
//        --font-dir <dir with ProximaNova-{Regular,Semibold,Bold}.woff2>
import { chromium } from 'playwright';
import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const cuesPath = arg('cues');
const outDir = arg('out-dir');
const fontDir = arg('font-dir');
if (!cuesPath || !outDir || !fontDir) {
  console.error('render_captions: --cues, --out-dir, --font-dir are required');
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });
const cues = JSON.parse(readFileSync(cuesPath, 'utf8'));
const b64 = (p) => readFileSync(p).toString('base64');
const semibold = b64(join(fontDir, 'ProximaNova-Semibold.woff2'));

const html = `<!doctype html><html><head><meta charset="utf-8"><style>
  @font-face { font-family:'Proxima Nova'; font-weight:600;
    src:url(data:font/woff2;base64,${semibold}) format('woff2'); }
  html, body { margin:0; background:transparent; }
  #pill {
    display:inline-block;
    font-family:'Proxima Nova', -apple-system, 'Segoe UI', sans-serif;
    font-weight:600; font-size:38px; line-height:1.25; color:#ffffff;
    letter-spacing:.015em; white-space:nowrap;
    background:rgba(10,16,31,.78);
    border:1px solid rgba(255,255,255,.08);
    border-radius:16px; padding:13px 28px;
    box-shadow:0 8px 28px rgba(2,8,23,.35);
    text-shadow:0 1px 2px rgba(2,8,23,.45);
  }
</style></head><body><span id="pill"></span></body></html>`;

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1920, height: 220 }, deviceScaleFactor: 1,
});
await page.setContent(html, { waitUntil: 'networkidle' });
for (let i = 0; i < cues.length; i++) {
  await page.locator('#pill').evaluate((el, text) => { el.textContent = text; }, cues[i].text);
  await page.locator('#pill').screenshot({
    path: join(outDir, `${String(i).padStart(2, '0')}.png`),
    omitBackground: true,
  });
}
await browser.close();
console.log(`render_captions: wrote ${cues.length} pills to ${outDir}`);
