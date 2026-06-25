// Node test: record_scene.mjs against the static fixture page (no WordPress).
// Run: node tests/test_record_scene.mjs
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, existsSync, statSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const fixtureUrl = pathToFileURL(join(__dirname, 'fixtures', 'page.html')).href;

const runDir = mkdtempSync(join(tmpdir(), 'recordtest-'));
mkdirSync(join(runDir, 'audio'), { recursive: true });

const script = {
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Open the menu and enable the sitemap.', intent: 'Enable sitemap',
    actions: [
      { type: 'click', target: 'Sitemaps link', selector: '#sitemaps-link', highlight: true },
      { type: 'click', target: 'Enable toggle', selector: '#enable-toggle', highlight: true },
    ],
    focus_selector: '#panel', hold_after_ms: 200, verify: { expect_on_screen: 'sitemap settings' },
  }],
};
writeFileSync(join(runDir, 'script.discovered.json'), JSON.stringify(script));
writeFileSync(join(runDir, 'audio', 'durations.json'), JSON.stringify({ '01': 1.5 }));

execFileSync('node', [
  join(ROOT, 'scripts', 'record_scene.mjs'),
  '--run-dir', runDir, '--scene-id', '01', '--base-url', fixtureUrl,
], { stdio: 'inherit' });

const clip = join(runDir, 'clips', '01.raw.webm');
const focus = join(runDir, 'clips', '01.focus.json');
if (!existsSync(clip) || statSync(clip).size === 0) {
  console.error('FAIL: clip not produced'); process.exit(1);
}
if (!existsSync(focus)) { console.error('FAIL: focus sidecar missing'); process.exit(1); }
console.log(`PASS: recorded ${statSync(clip).size} bytes + focus sidecar`);
