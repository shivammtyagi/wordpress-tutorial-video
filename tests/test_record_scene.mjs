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

// ---- scenario 2: setup/recorded phases + narration cue + focus scale ----------
import { readFileSync } from 'node:fs';

const run2 = mkdtempSync(join(tmpdir(), 'recordtest2-'));
mkdirSync(join(run2, 'audio'), { recursive: true });
mkdirSync(join(run2, 'verify', 'scenes'), { recursive: true });

writeFileSync(join(run2, 'script.discovered.json'), JSON.stringify({
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Click the enable button now.', intent: 'Phase test',
    actions: [
      { type: 'click', target: 'Sitemaps link', selector: '#sitemaps-link',
        highlight: false, phase: 'setup' },
      { type: 'click', target: 'Enable toggle', selector: '#enable-toggle',
        highlight: true, cue: 'button' },
    ],
    focus_selector: '#panel', hold_after_ms: 200,
    verify: { expect_on_screen: 'sitemap settings' },
  }],
}));
writeFileSync(join(run2, 'config.json'), JSON.stringify({
  capture_scale: 1, action_timeout_ms: 3000,
}));
writeFileSync(join(run2, 'verify', 'scenes', '01.json'), JSON.stringify({
  words: [
    { word: 'Click', start: 0.0, end: 0.2 }, { word: 'the', start: 0.2, end: 0.3 },
    { word: 'enable', start: 0.3, end: 0.5 }, { word: 'button', start: 0.5, end: 0.8 },
    { word: 'now.', start: 0.8, end: 1.0 }],
  wer: 0, ok: true,
}));
writeFileSync(join(run2, 'audio', 'durations.json'), JSON.stringify({ '01': 1.2 }));

execFileSync('node', [
  join(ROOT, 'scripts', 'record_scene.mjs'),
  '--run-dir', run2, '--scene-id', '01', '--base-url', fixtureUrl,
], { stdio: 'inherit' });

const clip2 = join(run2, 'clips', '01.raw.webm');
if (!existsSync(clip2) || statSync(clip2).size === 0) {
  console.error('FAIL: phase/cue clip not produced'); process.exit(1);
}
const focus2 = JSON.parse(readFileSync(join(run2, 'clips', '01.focus.json'), 'utf8'));
if (focus2.scale !== 1 || focus2.viewport.width !== 640) {
  console.error('FAIL: focus sidecar missing scale/viewport'); process.exit(1);
}
console.log('PASS: phase/cue scenario recorded with focus scale sidecar');

// ---- scenario 3: destructive guard blocks risky recorded clicks ---------------
const run3 = mkdtempSync(join(tmpdir(), 'recordtest3-'));
mkdirSync(join(run3, 'audio'), { recursive: true });
writeFileSync(join(run3, 'script.discovered.json'), JSON.stringify({
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Delete everything.', intent: 'Danger',
    actions: [
      { type: 'click', target: 'Delete all posts button', selector: '#enable-toggle',
        highlight: false },
    ],
    focus_selector: '#panel', hold_after_ms: 100,
    verify: { expect_on_screen: 'x' },
  }],
}));
writeFileSync(join(run3, 'audio', 'durations.json'), JSON.stringify({ '01': 1.0 }));

let guardCode = 0;
try {
  execFileSync('node', [
    join(ROOT, 'scripts', 'record_scene.mjs'),
    '--run-dir', run3, '--scene-id', '01', '--base-url', fixtureUrl,
  ], { stdio: 'pipe' });
} catch (e) {
  guardCode = e.status;
}
if (guardCode !== 5) {
  console.error(`FAIL: destructive guard expected exit 5, got ${guardCode}`); process.exit(1);
}
console.log('PASS: destructive guard blocks risky scene (exit 5)');
