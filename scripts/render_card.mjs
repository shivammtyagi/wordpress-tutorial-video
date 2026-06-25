// render_card.mjs — render a title/outro card to PNG using Chromium.
// Portable alternative to ffmpeg drawtext (Homebrew ffmpeg ships without
// libfreetype). Playwright is already a required dependency of this skill.
//
//   node render_card.mjs --title "..." --subtitle "..." --out card.png \
//        --width 1920 --height 1080 [--template path/to/card.html]
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const width = parseInt(arg('width', '1920'), 10);
const height = parseInt(arg('height', '1080'), 10);
const title = arg('title', 'Tutorial');
const subtitle = arg('subtitle', '');
const out = arg('out', 'card.png');
const template = arg('template', join(__dirname, '..', 'assets', 'card.html'));

let html = readFileSync(template, 'utf8')
  .replaceAll('{{TITLE}}', title.replace(/</g, '&lt;'))
  .replaceAll('{{SUBTITLE}}', subtitle.replace(/</g, '&lt;'));

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
await page.setContent(html, { waitUntil: 'networkidle' });
await page.screenshot({ path: out });
await browser.close();
console.log(`render_card: wrote ${out}`);
